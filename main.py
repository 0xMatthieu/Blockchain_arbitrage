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
from dex_utils import check_and_approve_token
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

    def _router_fee_bps(self, pool):
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

        buy_dex_name = buy_pool['dex']
        sell_dex_name = sell_pool['dex']
        banner = (f"{buy_pool['pair']:<20} | "
                  f"Route: {buy_dex_name.upper()} (buy, fee {buy_fee*100:.2f}%) -> "
                  f"{sell_dex_name.upper()} (sell, fee {sell_fee*100:.2f}%) | "
                  f"Spread: {spread:6.2f}%")
        self.latest_spread_info[token_address] = banner

        if spread >= MIN_SPREAD_PERCENT:
            token_info = self.TOKEN_INFO.get(token_address, {})
            execute_trade(buy_pool, sell_pool, spread, token_address, token_info)
            self.last_trade_attempt_ts = time.time()

    def run(self):
        check_dex = True
        if not all([TOKEN_ADDRESSES, BASE_CURRENCY_ADDRESS, account]):
            logging.error("Error: Core configuration is missing.")
            return

        if account: logging.info(f"Bot wallet address: {account.address}")
        
        logging.info("--- Discovering pools and fetching initial token info via DexScreener ---")
        for token_address in TOKEN_ADDRESSES:
            try:
                api_url = f"https://api.dexscreener.com/latest/dex/tokens/{token_address}"
                logging.info(f"Querying for token: {token_address}")
                response = requests.get(api_url)
                response.raise_for_status()
                j = response.json()

                for p in j['pairs']:
                    liquidity_usd = p.get('liquidity', {}).get('usd', 0)
                    volume_h24 = p.get('volume', {}).get('h24', 0)
                    logging.info(
                        f"  - DEX: {p['dexId']:<15} | Pool: {p['pairAddress']} | Liq: ${liquidity_usd:12,.2f} | Vol: ${volume_h24:12,.2f}")
                
                pairs = j.get('pairs', [])
                if not pairs:
                    logging.warning(f"  -> No pairs found for {token_address}")
                    time.sleep(1)
                    continue
                
                # Use the first pair to get token info
                first_pair = pairs[0]
                self.TOKEN_INFO[token_address] = {
                    'name': first_pair['baseToken']['name'],
                    'symbol': first_pair['baseToken']['symbol']
                }
                logging.info(f"  -> Found token: {self.TOKEN_INFO[token_address]['name']} ({self.TOKEN_INFO[token_address]['symbol']})")
                time.sleep(1)

            except requests.exceptions.HTTPError as http_err:
                if hasattr(http_err, 'response') and http_err.response.status_code == 429:
                    logging.warning(f"  -> Rate limited for token {token_address}. Skipping for now and sleeping.")
                    time.sleep(5)
                else:
                    logging.error(f"  -> HTTP error for token {token_address}: {http_err}")
            except Exception as e:
                logging.error(f"\nCould not fetch initial data for {token_address}: {e}")
        
        if not self.TOKEN_INFO:
            logging.error("Could not discover any tokens. Stopping bot.")
            return

        logging.info("-" * 50)
        
        if check_dex:
            logging.info("--- Running Initial Approval Checks ---")
            base_token_contract = w3.eth.contract(address=BASE_CURRENCY_ADDRESS, abi=ERC20_ABI)
            base_decimals = base_token_contract.functions.decimals().call()
            amount_to_approve_wei = int(TRADE_AMOUNT_BASE_TOKEN * (10**base_decimals))
            unlimited_allowance = 2**256 - 1
            for dex, info in DEX_ROUTERS.items():
                logging.info(f"\nChecking approvals for {dex.upper()} router ({info['address']})...")
                check_and_approve_token(BASE_CURRENCY_ADDRESS, info['address'], amount_to_approve_wei)
                for token_address in self.TOKEN_INFO: # Only approve tokens we are actually watching
                    token_name = self.TOKEN_INFO[token_address]['name']
                    logging.info(f"  - Approving target token: {token_name} ({token_address})")
                    check_and_approve_token(token_address, info['address'], unlimited_allowance)
                time.sleep(1)
            logging.info("--- Initial Approval Checks Complete ---\n")

        logging.info("Starting DexScreener polling for arbitrage analysis...")
        logging.info(f"Polling every {POLL_INTERVAL:.2f} seconds.")
        logging.info("-" * 50)
        last_summary_print_time = time.time()
        
        while self.running:
            if time.time() - last_summary_print_time >= 60:
                logging.info("--- Best Current Spread Summary (1 min) ---")
                if self.latest_spread_info:
                    for token_addr in self.TOKEN_INFO:
                        logging.info(self.latest_spread_info.get(token_addr, f"Waiting for data on {self.TOKEN_INFO[token_addr]['symbol']}..."))
                else: logging.info("No spread data yet. Waiting for polls...")
                logging.info("-" * 50)
                last_summary_print_time = time.time()

            # --- Main Polling Logic ---
            # Loop through each token and poll its data individually to get all pairs.
            for token_address in list(self.TOKEN_INFO.keys()):
                # Check if bot was stopped during the loop
                if not self.running:
                    break
                
                token_symbol = self.TOKEN_INFO[token_address]['symbol']
                try:
                    api_url = f"https://api.dexscreener.com/latest/dex/tokens/{token_address}"
                    response = requests.get(api_url)
                    response.raise_for_status()
                    j = response.json()
                    
                    current_pairs_raw = j.get('pairs', [])
                    if not current_pairs_raw:
                        logging.info(f"[{token_symbol}] No pairs found on DexScreener.")
                        # Clear old data from UI if no pairs are found
                        self.analyze_and_trade([], token_address)
                        time.sleep(POLL_INTERVAL)
                        continue

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
                            base_currency_price_usd = price_usd / price_native if price_native > 1e-18 else 0

                            valid_pairs.append({
                                'dex': p['dexId'], 'chain' : p['chainId'],
                                'pair': f"{p['baseToken']['symbol']}/{p['quoteToken']['symbol']}",
                                'price': price_native, 'liq_usd': liquidity_usd,
                                'pairAddress': p['pairAddress'], 'feeBps': p.get('feeBps', 0),
                                'base_currency_price_usd': base_currency_price_usd,
                                'dexId': p['dexId']
                            })
                    
                    if valid_pairs:
                        self.analyze_and_trade(valid_pairs, token_address)
                    else:
                        # This handles the case where pairs are returned, but none meet the liquidity/volume criteria.
                        logging.info(f"[{token_symbol}] No pairs found meeting liquidity/volume criteria.")
                        self.analyze_and_trade([], token_address)

                except requests.exceptions.RequestException as e:
                    logging.warning(f"API Error fetching {token_symbol}: {e}")
                    # Clear data for this token on error to avoid staleness
                    self.analyze_and_trade([], token_address)
                    time.sleep(POLL_INTERVAL_ERROR) # Longer sleep on API error
                except Exception as e:
                    logging.error(f"App Error processing {token_symbol}: {e}", exc_info=True)
                    self.analyze_and_trade([], token_address)
                    time.sleep(POLL_INTERVAL_ERROR)
                
                # Sleep between each token poll to respect rate limits
                time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    setup_logging()
    bot = ArbitrageBot(shared_spread_info_dict={})
    try:
        bot.run()
    except KeyboardInterrupt:
        logging.info("\nProgram stopped by user.")
