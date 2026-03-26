import requests
import sys
import json
import time
import logging
from config import (
    w3, account, TOKEN_ADDRESSES, BASE_CHAIN_ID, BASE_CURRENCY_ADDRESS, DEX_ROUTERS,
    MIN_LIQUIDITY_USD, MIN_VOLUME_USD, MIN_SPREAD_PERCENT, POLL_INTERVAL, POLL_INTERVAL_ERROR, TRADE_COOLDOWN_SECONDS,
    TRADE_AMOUNT_BASE_TOKEN, V2_FEE_BPS, V3_FEE_MAP
)
from abi import ERC20_ABI
from dex_utils import get_lp_price, check_and_approve_token, get_decimals
from trading import execute_trade
from logging_config import setup_logging


class ArbitrageBot:
    def __init__(self, shared_spread_info_dict):
        self.last_trade_attempt_ts = 0
        self.TOKEN_INFO = {}
        self.latest_spread_info = shared_spread_info_dict
        self.running = True
        self.valid_pairs = []

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
        if not pairs or len(pairs) < 2:
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

        # get price directly in LP
        price_buy_lp = get_lp_price(buy_pool, token_address)
        price_sell_lp = get_lp_price(sell_pool, token_address)

        if price_buy_lp is not None:
            logging.info(f" buy price from api for {buy_pool['pair']} on {buy_pool['dex']} is {buy_pool['price']} while from LP {price_buy_lp}")
        if price_sell_lp is not None:
            logging.info(f" sell price from api  for {sell_pool['pair']} on {sell_pool['dex']}  is {sell_pool['price']} while from LP {price_sell_lp}")

        banner = (f"{buy_pool['pair']:<20} | "
                  f"Route: {buy_dex_name.upper()} (buy, fee {buy_fee*100:.2f}%) -> "
                  f"{sell_dex_name.upper()} (sell, fee {sell_fee*100:.2f}%) | "
                  f"Spread: {spread:6.2f}%")
        self.latest_spread_info[token_address] = banner

        if spread >= MIN_SPREAD_PERCENT:
            token_info = self.TOKEN_INFO.get(token_address, {})
            logging.info(
                f"  -> Trade can be done on : {token_info.get('name', token_address)}. Spread is {spread}. Buy price dex/fee are {buy_pool['price']} / {effective_buy}. Sell price dex/fee are {sell_pool['price']} / {effective_sell}.")
            execute_trade(buy_pool, sell_pool, spread, token_address, token_info)
            self.last_trade_attempt_ts = time.time()

    def poll_dexscreener_api(self, discover=False):

        if discover:
            data = TOKEN_ADDRESSES.values()
        else:
            data = list(self.TOKEN_INFO.keys())

        for token_address in data:
            try:
                api_url = f"https://api.dexscreener.com/latest/dex/tokens/{token_address}"
                if discover:
                    logging.info(f"Querying for token: {token_address}")
                response = requests.get(api_url)
                response.raise_for_status()
                j = response.json()

                if discover:
                    for p in j['pairs']:
                        liquidity_usd = p.get('liquidity', {}).get('usd', 0)
                        volume_h24 = p.get('volume', {}).get('h24', 0)
                        if discover:
                            logging.info(
                                f"  - Chain: {p['chainId']} | DEX: {p['dexId']:<15} | Pool: {p['pairAddress']} | Pair: {p['baseToken']['symbol']}/{p['quoteToken']['symbol']} | Liq: ${liquidity_usd:12,.2f} | Vol: ${volume_h24:12,.2f}")

                    pairs = j.get('pairs', [])
                    if not pairs:
                        logging.warning(f"  -> No pairs found for {token_address}")
                        time.sleep(POLL_INTERVAL_ERROR)
                        continue

                    # Use the first pair to get token info
                    first_pair = pairs[0]
                    self.TOKEN_INFO[token_address] = {
                        'name': first_pair['baseToken']['name'],
                        'symbol': first_pair['baseToken']['symbol']
                    }
                    logging.info(
                        f"  -> Found token: {self.TOKEN_INFO[token_address]['name']} ({self.TOKEN_INFO[token_address]['symbol']})")

                else:
                    token_symbol = self.TOKEN_INFO[token_address]['symbol']
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
                                volume_h24 >= MIN_VOLUME_USD and p['chainId'] == BASE_CHAIN_ID):
                            price_native = float(p['priceNative'])
                            price_usd = float(p['priceUsd'])
                            base_currency_price_usd = price_usd / price_native if price_native > 1e-18 else 0

                            valid_pairs.append({
                                'dex': p['dexId'], 'chain': p['chainId'],
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
                        self.latest_spread_info[token_address] = f"[{token_symbol}] No pairs found meeting liquidity/volume criteria."


            except requests.exceptions.HTTPError as http_err:
                if hasattr(http_err, 'response') and http_err.response.status_code == 429:
                    logging.warning(f"  -> Rate limited for token {token_address}. Skipping for now and sleeping.")
                    time.sleep(5)
                else:
                    logging.error(f"  -> HTTP error for token {token_address}: {http_err}")
            except requests.exceptions.RequestException as e:
                logging.warning(f"API Error fetching {token_address}: {e}")
                time.sleep(POLL_INTERVAL_ERROR)  # Longer sleep on API error
            except Exception as e:
                logging.error(f"\nCould not fetch initial data for {token_address}: {e}", exc_info=True)
                time.sleep(POLL_INTERVAL_ERROR)

            time.sleep(POLL_INTERVAL)

    def run(self):
        check_dex = True
        if not all([TOKEN_ADDRESSES, BASE_CURRENCY_ADDRESS, account]):
            logging.error("Error: Core configuration is missing.")
            return

        if account: logging.info(f"Bot wallet address: {account.address}")

        logging.info("--- Discovering pools and fetching initial token info via DexScreener ---")
        self.poll_dexscreener_api(discover=True)
        
        if not self.TOKEN_INFO:
            logging.error("Could not discover any tokens. Stopping bot.")
            return

        logging.info("-" * 50)
        
        if check_dex:
            logging.info("--- Running Initial Approval Checks ---")
            base_decimals = get_decimals(BASE_CURRENCY_ADDRESS)
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
            self.poll_dexscreener_api()


if __name__ == "__main__":
    setup_logging()
    bot = ArbitrageBot(shared_spread_info_dict={})
    try:
        bot.run()
    except KeyboardInterrupt:
        logging.info("\nProgram stopped by user.")
