import time
import logging
from config import (
    w3, account, TOKEN_ADDRESSES, BASE_CHAIN_ID, BASE_CURRENCY_ADDRESS, DEX_ROUTERS,
    MIN_SPREAD_PERCENT, TRADE_COOLDOWN_SECONDS,
    TRADE_AMOUNT_BASE_TOKEN, V2_FEE_BPS, V3_FEE_MAP, ON_CHAIN_POLL_INTERVAL
)
from abi import ERC20_ABI
from dex_utils import get_lp_price, check_and_approve_token, get_decimals, discover_pools, get_token_info
from trading import execute_trade
from logging_config import setup_logging


class ArbitrageBot:
    def __init__(self, shared_spread_info_dict):
        self.last_trade_attempt_ts = 0
        self.TOKEN_INFO = {}
        self.watched_pools = {}  # token_address → list of pool dicts
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
        if not pairs or len(pairs) < 2:
            token_symbol = self.TOKEN_INFO.get(token_address, {}).get('symbol', f"[{token_address[-6:]}]")
            pair_symbol = pairs[0]['pair'] if pairs else token_symbol
            self.latest_spread_info[token_address] = f"{pair_symbol:<20} | Not enough priceable pools to analyze."
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
                  f"Route: {buy_dex_name.upper()} (buy @ {buy_pool['price']:.8f}, fee {buy_fee*100:.2f}%) -> "
                  f"{sell_dex_name.upper()} (sell @ {sell_pool['price']:.8f}, fee {sell_fee*100:.2f}%) | "
                  f"Spread: {spread:6.2f}%")
        self.latest_spread_info[token_address] = banner

        if spread >= MIN_SPREAD_PERCENT:
            token_info = self.TOKEN_INFO.get(token_address, {})
            logging.info(
                f"  -> Trade opportunity: {token_info.get('name', token_address)}. Spread {spread:.2f}%. "
                f"Buy {buy_pool['price']:.8f} / {effective_buy:.8f}. Sell {sell_pool['price']:.8f} / {effective_sell:.8f}.")
            execute_trade(buy_pool, sell_pool, spread, token_address, token_info)
            self.last_trade_attempt_ts = time.time()

    def discover_all_pools(self):
        """Discover pools for all configured tokens by querying factory contracts on-chain."""
        for token_name, token_address in TOKEN_ADDRESSES.items():
            logging.info(f"Discovering pools for {token_name} ({token_address})...")

            # Get token info from on-chain ERC20 contract
            token_info = get_token_info(token_address)
            self.TOKEN_INFO[token_address] = token_info
            logging.info(f"  -> Token: {token_info['name']} ({token_info['symbol']})")

            # Discover pools across all DEXes
            pools = discover_pools(token_address)
            if pools:
                self.watched_pools[token_address] = pools
            else:
                logging.warning(f"  -> No pools found for {token_name}")

    def poll_on_chain(self):
        """Poll on-chain prices for all watched pools and analyze spreads."""
        for token_address, pools in self.watched_pools.items():
            priced_pools = []
            for pool in pools:
                try:
                    price = get_lp_price(pool, token_address)
                    if price is not None and price > 0:
                        priced_pools.append({**pool, 'price': price})
                except Exception as e:
                    logging.debug(f"Price fetch failed for {pool['dex']} {pool['pairAddress']}: {e}")

            self.analyze_and_trade(priced_pools, token_address)

    def run(self):
        if not all([TOKEN_ADDRESSES, BASE_CURRENCY_ADDRESS, account]):
            logging.error("Error: Core configuration is missing.")
            return

        if account:
            logging.info(f"Bot wallet address: {account.address}")

        # --- Phase 1: On-chain pool discovery ---
        logging.info("--- Discovering pools via on-chain factory queries ---")
        self.discover_all_pools()

        if not self.watched_pools:
            logging.error("Could not discover any pools. Stopping bot.")
            return

        total_pools = sum(len(p) for p in self.watched_pools.values())
        logging.info(f"Discovered {total_pools} pools across {len(self.watched_pools)} tokens.")
        logging.info("-" * 50)

        # --- Phase 2: Approvals ---
        logging.info("--- Running Initial Approval Checks ---")
        base_decimals = get_decimals(BASE_CURRENCY_ADDRESS)
        amount_to_approve_wei = int(TRADE_AMOUNT_BASE_TOKEN * (10**base_decimals))
        unlimited_allowance = 2**256 - 1
        for dex, info in DEX_ROUTERS.items():
            logging.info(f"\nChecking approvals for {dex.upper()} router ({info['address']})...")
            check_and_approve_token(BASE_CURRENCY_ADDRESS, info['address'], amount_to_approve_wei)
            for token_address in self.TOKEN_INFO:
                token_name = self.TOKEN_INFO[token_address]['name']
                logging.info(f"  - Approving target token: {token_name} ({token_address})")
                check_and_approve_token(token_address, info['address'], unlimited_allowance)
            time.sleep(1)
        logging.info("--- Initial Approval Checks Complete ---\n")

        # --- Phase 3: On-chain price polling ---
        logging.info("Starting on-chain price polling for arbitrage analysis...")
        logging.info(f"Polling every {ON_CHAIN_POLL_INTERVAL:.2f} seconds.")
        logging.info("-" * 50)
        last_summary_print_time = time.time()

        while self.running:
            if time.time() - last_summary_print_time >= 60:
                logging.info("--- Best Current Spread Summary (1 min) ---")
                if self.latest_spread_info:
                    for token_addr in self.TOKEN_INFO:
                        logging.info(self.latest_spread_info.get(token_addr, f"Waiting for data on {self.TOKEN_INFO[token_addr]['symbol']}..."))
                else:
                    logging.info("No spread data yet. Waiting for polls...")
                logging.info("-" * 50)
                last_summary_print_time = time.time()

            try:
                self.poll_on_chain()
            except Exception as e:
                logging.error(f"Error during on-chain polling: {e}", exc_info=True)
                time.sleep(5)

            time.sleep(ON_CHAIN_POLL_INTERVAL)


if __name__ == "__main__":
    setup_logging()
    bot = ArbitrageBot(shared_spread_info_dict={})
    try:
        bot.run()
    except KeyboardInterrupt:
        logging.info("\nProgram stopped by user.")
