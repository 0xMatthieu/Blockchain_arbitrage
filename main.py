import os
import requests
import json
import time
from dotenv import load_dotenv
from web3 import Web3

# --- Load Configuration from .env file ---
load_dotenv()

TOKEN_ADDRESS = os.getenv("TOKEN_ADDRESS")
MIN_LIQUIDITY_USD = float(os.getenv("MIN_LIQUIDITY_USD", 1000))
MIN_SPREAD_PERCENT = float(os.getenv("MIN_SPREAD_PERCENT", 1.0))
BASE_RPC_URL = os.getenv("BASE_RPC_URL")
PRIVATE_KEY = os.getenv("PRIVATE_KEY")

# --- API Polling Configuration ---
# DexScreener limit is ~300 requests/min. We'll stay safely below that.
API_CALLS_PER_MINUTE = 280
POLL_INTERVAL = 60.0 / API_CALLS_PER_MINUTE

# --- Global State ---
last_trade_attempt_ts = 0 # Timestamp of the last trade attempt
TRADE_COOLDOWN_SECONDS = 60 # Cooldown period to prevent rapid-fire trades

# --- Web3 Setup (for trading) ---
# Note: This setup is basic. A real implementation would need more configuration.
w3 = Web3(Web3.HTTPProvider(BASE_RPC_URL))
account = w3.eth.account.from_key(PRIVATE_KEY) if PRIVATE_KEY and PRIVATE_KEY != "0xyour_private_key_here" else None

# --- DANGER: TRADING FUNCTION ---
# This is a placeholder function. It does NOT execute real trades.
# PROCEED WITH EXTREME CAUTION AND TEST ON A TESTNET FIRST.
def execute_trade(buy_pool, sell_pool, spread):
    global last_trade_attempt_ts
    
    print("\n" + "!"*60)
    print("!!! TRADE EXECUTION TRIGGERED (SIMULATION) !!!")
    print(f"!!! Spread of {spread:.2f}% detected, which is >= {MIN_SPREAD_PERCENT}%")
    print("!"*60)

    if not account:
        print("!!! TRADING SKIPPED: PRIVATE_KEY not configured in .env file.")
        return

    print(f"SIMULATION: Buy on {buy_pool['dex']} and Sell on {sell_pool['dex']}.")
    print(f"  - Buy Price : ${buy_pool['price']:.6f} (Liq: ${buy_pool['liq_usd']:,.2f})")
    print(f"  - Sell Price: ${sell_pool['price']:.6f} (Liq: ${sell_pool['liq_usd']:,.2f})")
    
    # --- REAL IMPLEMENTATION WOULD GO HERE ---
    # See previous comments for details on what a real implementation requires.
    
    print("!!! SIMULATION COMPLETE. NO REAL FUNDS WERE USED. !!!")
    
    # Update timestamp to enforce cooldown
    last_trade_attempt_ts = time.time()


def analyze_and_trade(pairs):
    global last_trade_attempt_ts

    # Check if we are in a cooldown period before any analysis
    if time.time() - last_trade_attempt_ts < TRADE_COOLDOWN_SECONDS:
        return

    # Filter for pools with sufficient liquidity
    liquid_pools = [p for p in pairs if p.get('liq_usd') >= MIN_LIQUIDITY_USD]

    if len(liquid_pools) < 2:
        print("\rNot enough liquid pools to analyze. Waiting...", end="")
        return

    # Sort by price to find the best buy and sell opportunities
    liquid_pools.sort(key=lambda x: x['price'])
    
    buy_pool = liquid_pools[0]
    sell_pool = liquid_pools[-1]

    # Calculate the potential profit margin
    spread = ((sell_pool['price'] - buy_pool['price']) / buy_pool['price']) * 100

    # Print current best prices for monitoring
    print(f"\rBest Buy: ${buy_pool['price']:.6f} ({buy_pool['dex']}) | Best Sell: ${sell_pool['price']:.6f} ({sell_pool['dex']}) | Spread: {spread:.2f}%   ", end="")

    if spread >= MIN_SPREAD_PERCENT:
        # An opportunity is found, trigger the trade function
        execute_trade(buy_pool, sell_pool, spread)


def main():
    if not TOKEN_ADDRESS:
        print("Error: TOKEN_ADDRESS is not defined in your .env file.")
        return

    api_url = f"https://api.dexscreener.com/latest/dex/tokens/{TOKEN_ADDRESS}"
    print(f"Starting arbitrage analysis for token: {TOKEN_ADDRESS}")
    print(f"Polling API every {POLL_INTERVAL:.2f} seconds.")
    print("-" * 50)

    while True:
        try:
            # Fetch the latest data from the API
            response = requests.get(api_url)
            response.raise_for_status() # Raise an exception for bad status codes
            
            j = response.json()
            if not j or not j.get('pairs'):
                print("\rNo pairs found in API response. Waiting...", end="")
                time.sleep(POLL_INTERVAL)
                continue

            # Process data
            current_pairs = []
            for p in j['pairs']:
                if p.get('priceUsd') and p.get('liquidity') and p.get('liquidity').get('usd'):
                    current_pairs.append({
                        'dex': p['dexId'],
                        'chain' : p['chainId'],
                        'pair': f"{p['baseToken']['symbol']}/{p['quoteToken']['symbol']}",
                        'price': float(p['priceUsd']),
                        'liq_usd': float(p['liquidity']['usd']),
                        'pairAddress': p['pairAddress']
                    })
            
            if current_pairs:
                analyze_and_trade(current_pairs)

        except requests.exceptions.RequestException as e:
            print(f"\nAn error occurred while fetching data: {e}")
        except Exception as e:
            print(f"\nAn unexpected error occurred: {e}")
        
        # Wait for the next poll
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nProgram stopped by user.")
