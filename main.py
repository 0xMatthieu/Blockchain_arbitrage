import requests
import sys
import json
import time
from config import (
    w3, account, TOKEN_ADDRESSES, BASE_CURRENCY_ADDRESS, DEX_ROUTERS,
    MIN_LIQUIDITY_USD, MIN_VOLUME_USD, MIN_SPREAD_PERCENT, POLL_INTERVAL, POLL_INTERVAL_ERROR, TRADE_COOLDOWN_SECONDS,
    TRADE_AMOUNT_BASE_TOKEN, V2_FEE_BPS, V3_FEE_MAP
)
from abi import ERC20_ABI
from dex_utils import check_and_approve_token
from trading import execute_trade

# --- Global State ---
last_trade_attempt_ts = 0
LAST_BANNERS_LOG = {}
printed_lines = 0          # <- module-level mutable

def _router_fee_bps(pool):
    """Return total fee bps for price quoted by DexScreener item."""
    if pool['dex'] in ('uniswap', 'pancakeswap'):
        return V3_FEE_MAP.get(pool['feeBps'], 30)   # default 0.30 %
    else:                                           # solidly-style v2
        return V2_FEE_BPS

def analyze_and_trade(pairs, token_address):
    global last_trade_attempt_ts, LAST_BANNERS_LOG

    if time.time() - last_trade_attempt_ts < TRADE_COOLDOWN_SECONDS:
        return

    # The pairs list is now pre-filtered for liquidity and volume in main()
    if len(pairs) < 2:
        # This message will be displayed if there are fewer than 2 valid pools
        # after filtering in main(). `pairs` will have 0 or 1 item.
        pair_symbol = pairs[0]['pair'] if pairs else f"[{token_address[-6:]}]"
        LAST_BANNERS_LOG[token_address] = f"{pair_symbol:<20} | Not enough valid pools to analyze. Waiting..."
        return

    # sort by quoted token price
    pairs.sort(key=lambda x: x['price'])
    buy_pool  = pairs[0]
    sell_pool = pairs[-1]

    # ---- fee-adjusted spread ------------------------------------
    buy_fee  = _router_fee_bps(buy_pool)   / 10_000
    sell_fee = _router_fee_bps(sell_pool)  / 10_000

    effective_buy  = buy_pool['price']  * (1 + buy_fee)   # we pay
    effective_sell = sell_pool['price'] * (1 - sell_fee)  # we receive
    spread = (effective_sell - effective_buy) / effective_buy * 100
    # -------------------------------------------------------------

    # ---- pretty banner (route line) -----------------------------
    banner = (
        f"{buy_pool['pair']:<20} | "
        f"Route: {buy_pool['dex'].upper()} (buy, fee {buy_fee*100:.2f}%) "
        "-> "
        f"{sell_pool['dex'].upper()} (sell, fee {sell_fee*100:.2f}%) | "
        f"Spread: {spread:6.2f}%"
    )
    LAST_BANNERS_LOG[token_address] = banner
    # -------------------------------------------------------------

    if spread >= MIN_SPREAD_PERCENT:
        execute_trade(buy_pool, sell_pool, spread, token_address)
        last_trade_attempt_ts = time.time()

def main():
    # This flag should be True to ensure all routers are approved to spend tokens.
    check_dex = True

    if not all([TOKEN_ADDRESSES, BASE_CURRENCY_ADDRESS, account]):
        print("Error: Core configuration (TOKEN_ADDRESSES, BASE_CURRENCY_ADDRESS, PRIVATE_KEY) is missing.")
        return

    if account:
        print(f"Bot wallet address: {account.address}")

    if check_dex:
        print("--- Running Initial Approval Checks ---")
        base_token_contract = w3.eth.contract(address=BASE_CURRENCY_ADDRESS, abi=ERC20_ABI)
        base_decimals = base_token_contract.functions.decimals().call()
        amount_to_approve_wei = int(TRADE_AMOUNT_BASE_TOKEN * (10**base_decimals))
        unlimited_allowance = 2**256 - 1

        for dex, info in DEX_ROUTERS.items():
            print(f"\nChecking approvals for {dex.upper()} router ({info['address']})...")
            check_and_approve_token(BASE_CURRENCY_ADDRESS, info['address'], amount_to_approve_wei)
            for token_address in TOKEN_ADDRESSES:
                print(f"  - Approving target token: {token_address}")
                check_and_approve_token(token_address, info['address'], unlimited_allowance)
            time.sleep(1)
        print("--- Initial Approval Checks Complete ---\n")

    print("--- Initial Pool Liquidity & Volume Check ---")
    for token_address in TOKEN_ADDRESSES:
        try:
            api_url = f"https://api.dexscreener.com/latest/dex/tokens/{token_address}"
            response = requests.get(api_url)
            response.raise_for_status()
            j = response.json()

            if not j or not j.get('pairs'):
                print(f"\nToken: {token_address} - No pairs found.")
                continue

            print(f"\n--- Token: {token_address} ---")
            for p in j['pairs']:
                liquidity_usd = p.get('liquidity', {}).get('usd', 0)
                volume_h24 = p.get('volume', {}).get('h24', 0)
                print(f"  - DEX: {p['dexId']:<15} | Pool: {p['pairAddress']} | Liq: ${liquidity_usd:12,.2f} | Vol: ${volume_h24:12,.2f}")
        except Exception as e:
            print(f"\nCould not fetch initial pool data for {token_address}: {e}")
    print("-" * 50)

    print(f"Starting arbitrage analysis for tokens: {TOKEN_ADDRESSES}")
    print(f"Polling API every {POLL_INTERVAL:.2f} seconds for each token.")
    print("-" * 50)

    # Initialize banner log and reserve space on screen
    for token_address in TOKEN_ADDRESSES:
        LAST_BANNERS_LOG[token_address] = f"[{token_address[-6:]}] Waiting for initial data..."
        print("")

    while True:
        for token_address in TOKEN_ADDRESSES:
            try:
                api_url = f"https://api.dexscreener.com/latest/dex/tokens/{token_address}"
                response = requests.get(api_url)
                response.raise_for_status()
                j = response.json()
                
                if not j or not j.get('pairs'):
                    LAST_BANNERS_LOG[token_address] = f"[{token_address[-6:]}] No pairs found in API response."
                    current_pairs = []
                else:
                    current_pairs = []
                    for p in j['pairs']:
                        # --- Pre-filter pools based on liquidity and volume ---
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

                            current_pairs.append({
                                'dex': p['dexId'], 'chain' : p['chainId'],
                                'pair': f"{p['baseToken']['symbol']}/{p['quoteToken']['symbol']}",
                                'price': price_native, 'liq_usd': liquidity_usd,
                                'pairAddress': p['pairAddress'], 'feeBps': p.get('feeBps', 0),
                                'base_currency_price_usd': base_currency_price_usd
                            })
                
                if current_pairs:
                    analyze_and_trade(current_pairs, token_address)
                elif j and j.get('pairs'):
                    # case where pairs exist but none are valid (e.g. not against base currency or no liquidity)
                    pair_symbol = f"{j['pairs'][0]['baseToken']['symbol']}/{j['pairs'][0]['quoteToken']['symbol']}"
                    LAST_BANNERS_LOG[token_address] = f"{pair_symbol:<20} | No valid/liquid pools found."

            except requests.exceptions.RequestException as e:
                LAST_BANNERS_LOG[token_address] = f"[{token_address[-6:]}] API Error: {str(e)[:80]}"
                time.sleep(POLL_INTERVAL_ERROR)
            except Exception as e:
                LAST_BANNERS_LOG[token_address] = f"[{token_address[-6:]}] App Error: {str(e)[:80]}"
                time.sleep(POLL_INTERVAL_ERROR)
            
            # --- Display Banners ---
            """
            Overwrite the previous banner block in-place.

            `LAST_BANNERS_LOG` must already hold one banner string per token.
            The display order is the order in which the dict was first populated
            (Python 3.7+ preserves insertion order).
            """
            """
            banners = list(LAST_BANNERS_LOG.values())
            n = len(banners)

            if n == 0:
                return  # nothing to draw yet

            # move cursor UP n lines AND to column-0  (ESC[{n}F)
            sys.stdout.write(f"\033[{n}F")

            # rewrite each line;  \r ensures column-0, \033[K clears leftovers
            for line in banners:
                sys.stdout.write(f"\r{line}\033[K\n")

            # flush so the terminal executes the codes immediately
            sys.stdout.flush()
            """

            time.sleep(POLL_INTERVAL)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nProgram stopped by user.")
