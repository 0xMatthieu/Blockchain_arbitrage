import requests
import sys
import json
import time
import logging
from config import (
    w3, account, TOKEN_ADDRESSES, BASE_CURRENCY_ADDRESS, DEX_ROUTERS,
    MIN_LIQUIDITY_USD, MIN_VOLUME_USD, MIN_SPREAD_PERCENT, POLL_INTERVAL, POLL_INTERVAL_ERROR, TRADE_COOLDOWN_SECONDS,
    TRADE_AMOUNT_BASE_TOKEN, V2_FEE_BPS, V3_FEE_MAP, RPC_MAX_RETRIES, RPC_BACKOFF_FACTOR
)
from abi import ERC20_ABI, SOLIDLY_PAIR_ABI, UNISWAP_V3_POOL_ABI, PANCAKE_V3_POOL_ABI
from dex_utils import check_and_approve_token, find_router_info
from trading import execute_trade
from logging_config import setup_logging
from web3.exceptions import ContractLogicError
from eth_abi import decode
from hexbytes import HexBytes


class ArbitrageBot:
    def __init__(self, shared_spread_info_dict):
        self.last_trade_attempt_ts = 0
        self.TOKEN_INFO = {}
        self.latest_spread_info = shared_spread_info_dict
        self.running = True
        self.watched_pools = {}

    def stop(self):
        self.running = False

    def _resilient_rpc_call(self, callable_func):
        _QUOTER_V2_RET_TYPES = ["uint256", "uint160", "uint32", "uint256"]
        last_exception = None
        for i in range(RPC_MAX_RETRIES):
            try:
                return callable_func()
            except ContractLogicError as err:
                last_exception = err
                payload = err.args[0].get("data") if err.args and isinstance(err.args[0], dict) else None
                if payload and len(payload) > 4:
                    data_bytes = HexBytes(payload)[4:] if len(payload) % 32 else HexBytes(payload)
                    decoded = decode(_QUOTER_V2_RET_TYPES, data_bytes.ljust(32 * 4, b"\0"))
                    return decoded[0]
            except Exception as err:
                last_exception = err
            if i < RPC_MAX_RETRIES - 1:
                wait = RPC_BACKOFF_FACTOR * (2 ** i)
                logging.warning(f"\n  - [RPC] Call failed: {last_exception}. Retrying in {wait:.2f}s ({i + 1}/{RPC_MAX_RETRIES})")
                time.sleep(wait)
            else:
                logging.error(f"  - [RPC] Call failed after {RPC_MAX_RETRIES} retries.")
        raise Exception(f"RPC call failed after {RPC_MAX_RETRIES} retries.") from last_exception

    def _get_v3_pool_abi(self, dex_name):
        if 'pancake' in dex_name.lower():
            return PANCAKE_V3_POOL_ABI
        return UNISWAP_V3_POOL_ABI

    def _get_onchain_price(self, pool_details):
        try:
            pair_address = w3.to_checksum_address(pool_details['pairAddress'])
            router_info = find_router_info(pool_details['dexId'], DEX_ROUTERS)
            if not router_info:
                # This can happen if the dexId is a raw address
                 router_info = find_router_info(self._get_dex_name_from_id(pool_details['dexId']), DEX_ROUTERS)
            
            if not router_info:
                logging.warning(f"Could not find router info for dexId {pool_details['dexId']}")
                return None

            version = router_info.get('version', 2)
            base_t = w3.to_checksum_address(pool_details['baseToken']['address'])
            quote_t = w3.to_checksum_address(pool_details['quoteToken']['address'])

            if version == 3:
                pool_contract = w3.eth.contract(address=pair_address, abi=self._get_v3_pool_abi(pool_details['dexId']))
                sqrt_price_x96, *_ = self._resilient_rpc_call(lambda: pool_contract.functions.slot0().call())
                if sqrt_price_x96 == 0: return None
                
                (token0, _) = (base_t, quote_t) if int(base_t, 16) < int(quote_t, 16) else (quote_t, base_t)
                price_token1_div_token0 = (sqrt_price_x96 / 2**96)**2
                
                return price_token1_div_token0 if base_t != token0 else (1 / price_token1_div_token0 if price_token1_div_token0 else 0)

            elif version == 2:
                pool_contract = w3.eth.contract(address=pair_address, abi=SOLIDLY_PAIR_ABI)
                reserves = self._resilient_rpc_call(lambda: pool_contract.functions.getReserves().call())
                reserve0, reserve1 = reserves[0], reserves[1]
                if reserve0 == 0 or reserve1 == 0: return None

                (token0, _) = (base_t, quote_t) if int(base_t, 16) < int(quote_t, 16) else (quote_t, base_t)
                return (reserve0 / reserve1) if base_t != token0 else (reserve1 / reserve0)
            else:
                return None
        except Exception as e:
            logging.error(f"Failed to get on-chain price for pool {pool_details.get('pairAddress')}: {e}")
            return None

    def _get_dex_name_from_id(self, dex_id):
        if dex_id in DEX_ROUTERS: return dex_id
        if not hasattr(self, '_dex_reverse_map'):
            self._dex_reverse_map = { v['address'].lower(): k for k, v in DEX_ROUTERS.items() if 'address' in v }
        return self._dex_reverse_map.get(dex_id.lower(), dex_id)

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

        buy_dex_name = self._get_dex_name_from_id(buy_pool['dex'])
        sell_dex_name = self._get_dex_name_from_id(sell_pool['dex'])
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
        
        logging.info("--- Discovering pools and fetching initial data via DexScreener ---")
        try:
            for token_address in TOKEN_ADDRESSES:
                api_url = f"https://api.dexscreener.com/latest/dex/tokens/{token_address}"
                logging.info(f"Querying for token: {token_address}")
                response = requests.get(api_url)
                response.raise_for_status()
                j = response.json()
                
                all_discovered_pairs = j.get('pairs', [])
                if not all_discovered_pairs:
                    logging.warning(f"  -> No pairs found for {token_address}")
                    time.sleep(1) # a small sleep to avoid hammering the API
                    continue
                
                # Process the discovered pairs for the current token
                for p in all_discovered_pairs:
                    base_token_addr = w3.to_checksum_address(p['baseToken']['address'])
                    if base_token_addr != token_address: continue
                    
                    liquidity_usd = p.get('liquidity', {}).get('usd', 0)
                    volume_h24 = p.get('volume', {}).get('h24', 0)
                    if not (p.get('priceNative') and p.get('quoteToken') and p.get('quoteToken').get('address') and
                            w3.to_checksum_address(p['quoteToken']['address']) == BASE_CURRENCY_ADDRESS and
                            liquidity_usd >= MIN_LIQUIDITY_USD and volume_h24 >= MIN_VOLUME_USD):
                        continue

                    if base_token_addr not in self.TOKEN_INFO:
                        self.TOKEN_INFO[base_token_addr] = {'name': p['baseToken']['name'], 'symbol': p['baseToken']['symbol']}
                    
                    if base_token_addr not in self.watched_pools:
                        self.watched_pools[base_token_addr] = []

                    price_native = float(p['priceNative'])
                    price_usd = float(p['priceUsd'])
                    base_currency_price_usd = price_usd / price_native if price_native > 1e-18 else 0

                    pool_details = {
                        'dexId': p['dexId'], 'pairAddress': p['pairAddress'], 'feeBps': p.get('feeBps', 0),
                        'pair': f"{p['baseToken']['symbol']}/{p['quoteToken']['symbol']}",
                        'baseToken': p['baseToken'], 'quoteToken': p['quoteToken'],
                        'liq_usd': liquidity_usd, 'base_currency_price_usd': base_currency_price_usd,
                        'dex': p['dexId']
                    }
                    self.watched_pools[base_token_addr].append(pool_details)
                
                # Give feedback on what was discovered for the token
                token_info = self.TOKEN_INFO.get(token_address)
                if token_info:
                    logging.info(f"--- Watching: {token_info['name']} ({token_info['symbol']}) ---")
                    if not self.watched_pools.get(token_address):
                        logging.info("  -> No valid pools found meeting criteria.")
                    else:
                        for pool in self.watched_pools.get(token_address, []):
                            dex_name = self._get_dex_name_from_id(pool['dexId'])
                            logging.info(f"  - Discovered: {dex_name:<15} | Pool: {pool['pairAddress']} | Liq: ${pool['liq_usd']:12,.2f}")
                
                time.sleep(1) # a small sleep to avoid hammering the API

        except Exception as e:
            logging.error(f"\nCould not fetch initial token/pool data: {e}", exc_info=True)
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

        logging.info("Starting on-chain polling for arbitrage analysis...")
        logging.info(f"Polling every {POLL_INTERVAL:.2f} seconds.")
        logging.info("-" * 50)
        last_summary_print_time = time.time()
        
        while self.running:
            if time.time() - last_summary_print_time >= 60:
                logging.info("--- Best Current Spread Summary (1 min) ---")
                if self.latest_spread_info:
                    for token_addr in self.watched_pools:
                        logging.info(self.latest_spread_info.get(token_addr, f"Waiting for data on {self.TOKEN_INFO[token_addr]['symbol']}..."))
                else: logging.info("No spread data yet. Waiting for polls...")
                logging.info("-" * 50)
                last_summary_print_time = time.time()

            for token_address, pools in self.watched_pools.items():
                token_symbol = self.TOKEN_INFO[token_address]['symbol']
                valid_pairs_for_token = []
                for pool_details in pools:
                    price = self._get_onchain_price(pool_details)
                    if price is not None:
                        pair_for_analysis = pool_details.copy()
                        pair_for_analysis['price'] = price
                        valid_pairs_for_token.append(pair_for_analysis)
                
                if valid_pairs_for_token:
                    self.analyze_and_trade(valid_pairs_for_token, token_address)
                else:
                    logging.info(f"[{token_symbol}] Could not fetch any on-chain prices for discovered pools this cycle.")
                
            # Distribute polling over the interval to avoid bursting RPC calls
            if len(self.watched_pools) > 0:
                time.sleep(POLL_INTERVAL / len(self.watched_pools))
            else:
                time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    setup_logging()
    bot = ArbitrageBot(shared_spread_info_dict={})
    try:
        bot.run()
    except KeyboardInterrupt:
        logging.info("\nProgram stopped by user.")
