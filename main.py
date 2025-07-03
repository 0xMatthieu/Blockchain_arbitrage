import requests
import json
import time
from config import (
    w3, account, TOKEN_ADDRESS, BASE_CURRENCY_ADDRESS, DEX_ROUTERS,
    MIN_LIQUIDITY_USD, MIN_SPREAD_PERCENT, POLL_INTERVAL, TRADE_COOLDOWN_SECONDS,
    TRADE_AMOUNT_BASE_TOKEN, V2_FEE_BPS, V3_FEE_MAP
)
from abi import ERC20_ABI
from dex_utils import check_and_approve_token
from trading import execute_trade

# --- Global State ---
last_trade_attempt_ts = 0

def _router_fee_bps(pool):
    """Return total fee bps for price quoted by DexScreener item."""
    if pool['dex'] in ('uniswap', 'pancakeswap'):
        return V3_FEE_MAP.get(pool['feeBps'], 30)   # default 0.30 %
    else:                                           # solidly-style v2
        return V2_FEE_BPS

def analyze_and_trade(pairs):
    global last_trade_attempt_ts

    if time.time() - last_trade_attempt_ts < TRADE_COOLDOWN_SECONDS:
        return

    # keep only liquid pools
    liquid_pools = [p for p in pairs if p['liq_usd'] >= MIN_LIQUIDITY_USD]
    if len(liquid_pools) < 2:
        print("\rNot enough liquid pools to analyze. Waiting...", end="")
        return

    # sort by quoted token price
    liquid_pools.sort(key=lambda x: x['price'])
    buy_pool  = liquid_pools[0]
    sell_pool = liquid_pools[-1]

    # ---- fee-adjusted spread ------------------------------------
    buy_fee  = _router_fee_bps(buy_pool)   / 10_000
    sell_fee = _router_fee_bps(sell_pool)  / 10_000

    effective_buy  = buy_pool['price']  * (1 + buy_fee)   # we pay
    effective_sell = sell_pool['price'] * (1 - sell_fee)  # we receive
    spread = (effective_sell - effective_buy) / effective_buy * 100
    # -------------------------------------------------------------

    # ---- pretty banner (route line) -----------------------------
    banner = (
        f"Route:  {buy_pool['dex'].upper()} (buy, fee {buy_fee*100:.2f} %) "
        "➜  "
        f"{sell_pool['dex'].upper()} (sell, fee {sell_fee*100:.2f} %) | "
        f"Adj. spread: {spread:.2f} %   "
    )
    print("\r" + banner, end="", flush=True)
    # -------------------------------------------------------------

    if spread >= MIN_SPREAD_PERCENT:
        execute_trade(buy_pool, sell_pool, spread)
        last_trade_attempt_ts = time.time()

def main():
    # This flag should be True to ensure all routers are approved to spend tokens.
    check_dex = True

    if not all([TOKEN_ADDRESS, BASE_CURRENCY_ADDRESS, account]):
        print("Error: Core configuration (TOKEN_ADDRESS, BASE_CURRENCY_ADDRESS, PRIVATE_KEY) is missing.")
        return

    if account:
        print(f"Bot wallet address: {account.address}")

    if check_dex:
        print("--- Running Initial Approval Checks ---")
        base_token_contract = w3.eth.contract(address=BASE_CURRENCY_ADDRESS, abi=ERC20_ABI)
        base_decimals = base_token_contract.functions.decimals().call()
        amount_to_approve_wei = int(TRADE_AMOUNT_BASE_TOKEN * (10**base_decimals))

        # We set a very high (practically unlimited) approval for the target token
        # to avoid needing to re-approve it later.
        unlimited_allowance = 2**256 - 1

        for dex, info in DEX_ROUTERS.items():
            print(f"\nChecking {dex.upper()} router ({info['address']})...")
            # Approve the router to spend the base currency for the buy part of the trade
            check_and_approve_token(BASE_CURRENCY_ADDRESS, info['address'], amount_to_approve_wei)
            # Approve the router to spend the target token for the sell part of the trade
            check_and_approve_token(TOKEN_ADDRESS, info['address'], unlimited_allowance)
            time.sleep(1)
        print("--- Initial Approval Checks Complete ---\n")

    api_url = f"https://api.dexscreener.com/latest/dex/tokens/{TOKEN_ADDRESS}"
    print(f"Starting arbitrage analysis for token: {TOKEN_ADDRESS}")
    print(f"Polling API every {POLL_INTERVAL:.2f} seconds.")
    print("-" * 50)

    while True:
        try:
            response = requests.get(api_url)
            response.raise_for_status()
            j = response.json()
            if not j or not j.get('pairs'):
                print("\rNo pairs found in API response. Waiting...", end="")
                time.sleep(POLL_INTERVAL)
                continue

            current_pairs = []
            for p in j['pairs']:
                if (p.get('priceNative') and p.get('quoteToken') and p.get('quoteToken').get('address') and
                    w3.to_checksum_address(p['quoteToken']['address']) == BASE_CURRENCY_ADDRESS and
                    p.get('liquidity') and p.get('liquidity').get('usd')):
                    
                    price_native = float(p['priceNative'])
                    price_usd = float(p['priceUsd'])
                    base_currency_price_usd = 0
                    if price_native > 1e-18: # Avoid division by zero
                        base_currency_price_usd = price_usd / price_native

                    current_pairs.append({
                        'dex': p['dexId'],
                        'chain' : p['chainId'],
                        'pair': f"{p['baseToken']['symbol']}/{p['quoteToken']['symbol']}",
                        'price': price_native,
                        'liq_usd': float(p['liquidity']['usd']),
                        'pairAddress': p['pairAddress'],
                        'feeBps': p.get('feeBps', 0), # Get fee for V3, default to 0
                        'base_currency_price_usd': base_currency_price_usd
                    })
            
            if current_pairs:
                analyze_and_trade(current_pairs)

        except requests.exceptions.RequestException as e:
            print(f"\nAn error occurred while fetching data: {e}")
        except Exception as e:
            print(f"\nAn unexpected error occurred: {e}")
        
        time.sleep(POLL_INTERVAL)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nProgram stopped by user.")
