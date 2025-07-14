import requests
import sys
import json
import time
import logging
from config import (
    w3, account, TOKEN_ADDRESSES, BASE_CURRENCY_ADDRESS, DEX_ROUTERS,
    MIN_LIQUIDITY_USD, MIN_VOLUME_USD, MIN_SPREAD_PERCENT, POLL_INTERVAL, POLL_INTERVAL_ERROR, TRADE_COOLDOWN_SECONDS,
    TRADE_AMOUNT_BASE_TOKEN, V2_FEE_BPS, V3_FEE_MAP
)
from abi import ERC20_ABI
from dex_utils import check_and_approve_token, get_token_info
from trading import execute_trade
from logging_config import setup_logging


class ArbitrageBot:
    def __init__(self, shared_spread_info_dict):
        self.last_trade_attempt_ts = 0
        self.TOKEN_INFO = {}
        self.latest_spread_info = shared_spread_info_dict
        self.running = True

    def stop(self):
        self.running = False

    def _get_dex_name_from_id(self, dex_id):
        """Resolves a DEX ID from DexScreener to a configured name."""
        # First, check if the dex_id itself is a key (e.g., "uniswap_v3")
        if dex_id in DEX_ROUTERS:
            return dex_id

        # If not, build a reverse map (if not cached) and check by address
        if not hasattr(self, '_dex_reverse_map'):
            self._dex_reverse_map = {
                v['address'].lower(): k for k, v in DEX_ROUTERS.items() if 'address' in v
            }
        
        return self._dex_reverse_map.get(dex_id.lower(), dex_id)

    def _router_fee_bps(self, pool):
        """Return total fee bps for price quoted by DexScreener item."""
        if pool['dex'] in ('uniswap', 'pancakeswap'):
            return V3_FEE_MAP.get(pool['feeBps'], 30)
        else:
            return V2_FEE_BPS

    def analyze_and_trade(self, pairs, token_address):
        if time.time() - self.last_trade_attempt_ts < TRADE_COOLDOWN_SECONDS:
            return

        if len(pairs) < 2:
            token_symbol = self.TOKEN_INFO.get(token_address, {}).get('symbol', f"[{token_address[-6:]}]")
            pair_symbol = pairs[0]['pair'] if pairs else token_symbol
            self.latest_spread_info[token_address] = f"{pair_symbol:<20} | Not enough valid pools to analyze."
            return

        pairs.sort(key=lambda x: x['price'])
        buy_pool, sell_pool = pairs[0], pairs[-1]

        buy_fee = self._router_fee_bps(buy_pool) / 10_000
        sell_fee = self._router_fee_bps(sell_pool) / 10_000
        effective_buy = buy_pool['price'] * (1 + buy_fee)
        effective_sell = sell_pool['price'] * (1 - sell_fee)
        spread = (effective_sell - effective_buy) / effective_buy * 100

        buy_dex_name = self._get_dex_name_from_id(buy_pool['dex'])
        sell_dex_name = self._get_dex_name_from_id(sell_pool['dex'])
        banner = (
            f"{buy_pool['pair']:<20} | "
            f"Route: {buy_dex_name.upper()} (buy, fee {buy_fee*100:.2f}%) -> "
            f"{sell_dex_name.upper()} (sell, fee {sell_fee*100:.2f}%) | "
            f"Spread: {spread:6.2f}%"
        )
        self.latest_spread_info[token_address] = banner

        if spread >= MIN_SPREAD_PERCENT:
            token_info = self.TOKEN_INFO.get(token_address, {})
            execute_trade(buy_pool, sell_pool, spread, token_address, token_info)
            self.last_trade_attempt_ts = time.time()

    def run(self):
        check_dex = True
        if not all([TOKEN_ADDRESSES, BASE_CURRENCY_ADDRESS, account]):
            logging.error("Error: Core configuration (TOKEN_ADDRESSES, BASE_CURRENCY_ADDRESS, PRIVATE_KEY) is missing.")
            return

        if account:
            logging.info(f"Bot wallet address: {account.address}")

        logging.info("\n--- Fetching Watched Token Information ---")
        for addr in TOKEN_ADDRESSES:
            info = get_token_info(addr)
            self.TOKEN_INFO[addr] = info
            logging.info(f"  - Watching: {info['name']} ({info['symbol']})")
        logging.info("----------------------------------------\n")

        if check_dex:
            logging.info("--- Running Initial Approval Checks ---")
            base_token_contract = w3.eth.contract(address=BASE_CURRENCY_ADDRESS, abi=ERC20_ABI)
            base_decimals = base_token_contract.functions.decimals().call()
            amount_to_approve_wei = int(TRADE_AMOUNT_BASE_TOKEN * (10**base_decimals))
            unlimited_allowance = 2**256 - 1

            for dex, info in DEX_ROUTERS.items():
                logging.info(f"\nChecking approvals for {dex.upper()} router ({info['address']})...")
                check_and_approve_token(BASE_CURRENCY_ADDRESS, info['address'], amount_to_approve_wei)
                for token_address in TOKEN_ADDRESSES:
                    token_name = self.TOKEN_INFO.get(token_address, {}).get('name', token_address)
                    logging.info(f"  - Approving target token: {token_name} ({token_address})")
                    check_and_approve_token(token_address, info['address'], unlimited_allowance)
                time.sleep(1)
            logging.info("--- Initial Approval Checks Complete ---\n")

        logging.info("--- Initial Pool Liquidity & Volume Check ---")
        for token_address in TOKEN_ADDRESSES:
            try:
                api_url = f"https://api.dexscreener.com/latest/dex/tokens/{token_address}"
                response = requests.get(api_url)
                response.raise_for_status()
                j = response.json()

                if not j or not j.get('pairs'):
                    token_name = self.TOKEN_INFO.get(token_address, {}).get('name', token_address)
                    logging.info(f"\nToken: {token_name} ({token_address}) - No pairs found.")
                    continue

                token_name = self.TOKEN_INFO.get(token_address, {}).get('name', token_address)
                logging.info(f"\n--- Token: {token_name} ({token_address}) ---")
                for p in j['pairs']:
                    liquidity_usd = p.get('liquidity', {}).get('usd', 0)
                    volume_h24 = p.get('volume', {}).get('h24', 0)
                    logging.info(f"  - DEX: {p['dexId']:<15} | Pool: {p['pairAddress']} | Liq: ${liquidity_usd:12,.2f} | Vol: ${volume_h24:12,.2f}")
            except Exception as e:
                logging.error(f"\nCould not fetch initial pool data for {token_address}: {e}")
        logging.info("-" * 50)

        logging.info(f"Starting arbitrage analysis for tokens: {TOKEN_ADDRESSES}")
        logging.info(f"Polling API every {POLL_INTERVAL:.2f} seconds for each token.")
        logging.info("-" * 50)

        last_summary_print_time = time.time()
        while self.running:
            # --- Periodic Summary ---
            if time.time() - last_summary_print_time >= 60:
                logging.info("--- Best Current Spread Summary (1 min) ---")
                if self.latest_spread_info:
                    for token_addr in TOKEN_ADDRESSES:
                        info_line = self.latest_spread_info.get(token_addr, "Waiting for data...")
                        logging.info(info_line)
                else:
                    logging.info("No spread data yet. Waiting for polls...")
                logging.info("-" * 50)
                last_summary_print_time = time.time()

            # --- Main Polling Logic ---
            try:
                # Batch API call for all tokens at once
                token_list_str = ",".join(TOKEN_ADDRESSES)
                api_url = f"https://api.dexscreener.com/latest/dex/tokens/{token_list_str}"
                response = requests.get(api_url)
                response.raise_for_status()
                j = response.json()
                
                if not j or not j.get('pairs'):
                    logging.info("No pairs found in batched API response.")
                    all_pairs = []
                else:
                    all_pairs = j['pairs']
                
                # Group pairs by their base token address
                pairs_by_token = {addr: [] for addr in TOKEN_ADDRESSES}
                for p in all_pairs:
                    base_token_addr = w3.to_checksum_address(p['baseToken']['address'])
                    if base_token_addr in pairs_by_token:
                        pairs_by_token[base_token_addr].append(p)

                # Analyze each token with its filtered list of pairs
                for token_address in TOKEN_ADDRESSES:
                    token_symbol = self.TOKEN_INFO.get(token_address, {}).get('symbol', f"[{token_address[-6:]}]")
                    
                    current_pairs_raw = pairs_by_token.get(token_address, [])
                    if not current_pairs_raw:
                        logging.info(f"[{token_symbol}] No pairs found for this token in batched response.")
                        continue
                        
                    # Pre-filter pools based on liquidity and volume
                    valid_pairs = []
                    for p in current_pairs_raw:
                        liquidity_usd = p.get('liquidity', {}).get('usd', 0)
                        volume_h24 = p.get('volume', {}).get('h24', 0)

                        if (p.get('priceNative') and p.get('quoteToken') and p.get('quoteToken').get('address') and
                            w3.to_checksum_address(p['quoteToken']['address']) == BASE_CURRENCY_ADDRESS and
                            liquidity_usd >= MIN_LIQUIDITY_USD and
                            volume_h24 >= MIN_VOLUME_USD):
                            
                            price_native = float(p['priceNative'])
                            price_usd = float(p['priceUsd'])
                            base_currency_price_usd = 0
                            if price_native > 1e-18:
                                base_currency_price_usd = price_usd / price_native

                            valid_pairs.append({
                                'dex': p['dexId'], 'chain' : p['chainId'],
                                'pair': f"{p['baseToken']['symbol']}/{p['quoteToken']['symbol']}",
                                'price': price_native, 'liq_usd': liquidity_usd,
                                'pairAddress': p['pairAddress'], 'feeBps': p.get('feeBps', 0),
                                'base_currency_price_usd': base_currency_price_usd
                            })
                    
                    if valid_pairs:
                        self.analyze_and_trade(valid_pairs, token_address)
                    else:
                        pair_symbol = f"{current_pairs_raw[0]['baseToken']['symbol']}/{current_pairs_raw[0]['quoteToken']['symbol']}"
                        logging.info(f"[{token_symbol}] {pair_symbol:<20} | No valid/liquid pools found.")

            except requests.exceptions.RequestException as e:
                logging.warning(f"API Error during batch poll: {str(e)[:100]}")
                time.sleep(POLL_INTERVAL_ERROR)
            except Exception as e:
                logging.error(f"App Error during batch poll: {str(e)[:100]}", exc_info=True)
                time.sleep(POLL_INTERVAL_ERROR)
            
            # Wait for the next polling cycle
            time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    setup_logging()
    bot = ArbitrageBot(shared_spread_info_dict={})
    try:
        bot.run()
    except KeyboardInterrupt:
        logging.info("\nProgram stopped by user.")
