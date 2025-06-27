import os
import asyncio
import json
import time
from dotenv import load_dotenv
import pandas as pd
from web3 import Web3

# --- Load Configuration from .env file ---
load_dotenv()

TOKEN_ADDRESS = os.getenv("TOKEN_ADDRESS")
MIN_LIQUIDITY_USD = float(os.getenv("MIN_LIQUIDITY_USD", 1000))
MIN_SPREAD_PERCENT = float(os.getenv("MIN_SPREAD_PERCENT", 1.0))
BASE_RPC_URL = os.getenv("BASE_RPC_URL")
PRIVATE_KEY = os.getenv("PRIVATE_KEY")

# --- Global State ---
pools_data = {} # Dictionary to store real-time data for each pair address
last_trade_attempt_ts = 0 # Timestamp of the last trade attempt
TRADE_COOLDOWN_SECONDS = 60 # Cooldown period to prevent rapid-fire trades

# --- Web3 Setup (for trading) ---
# Note: This setup is basic. A real implementation would need more configuration.
w3 = Web3(Web3.HTTPProvider(BASE_RPC_URL))
account = w3.eth.account.from_key(PRIVATE_KEY) if PRIVATE_KEY and PRIVATE_KEY != "0xyour_private_key_here" else None

# --- DANGER: TRADING FUNCTION ---
# This is a placeholder function. It does NOT execute real trades.
# Building a reliable trading function is complex and requires handling:
# - DEX Router ABIs (e.g., Uniswap V2/V3)
# - Calculating optimal amounts for swapping
# - Gas price estimation
# - Slippage tolerance
# - Transaction nonce management
# - Error handling (e.g., transaction reverted)
# PROCEED WITH EXTREME CAUTION AND TEST ON A TESTNET FIRST.
async def execute_trade(buy_pool, sell_pool, spread):
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
    # 1. Connect to DEX routers for both pools using their contract addresses and ABIs.
    # 2. Determine the amount to trade based on liquidity and desired profit.
    # 3. Build the transaction for the 'buy' operation.
    #    - e.g., swapExactETHForTokens or swapExactTokensForTokens
    # 4. Sign the transaction with your private key.
    # 5. Send the transaction and wait for the receipt.
    # 6. If the buy was successful, build, sign, and send the 'sell' transaction.
    #
    # This is often done via a custom smart contract for atomic execution (flash swaps).
    # A simple buy-then-sell sequence is vulnerable to front-running and price changes.
    
    print("!!! SIMULATION COMPLETE. NO REAL FUNDS WERE USED. !!!")
    
    # Update timestamp to enforce cooldown
    last_trade_attempt_ts = time.time()


def analyze_and_trade():
    global last_trade_attempt_ts

    # Check if we are in a cooldown period
    if time.time() - last_trade_attempt_ts < TRADE_COOLDOWN_SECONDS:
        return

    valid_pools = [pool for pool in pools_data.values() if pool.get('price') and pool.get('liq_usd') >= MIN_LIQUIDITY_USD]

    if len(valid_pools) < 2:
        return # Not enough liquid pools to compare

    # Sort by price to find the best buy and sell opportunities
    sorted_pools = sorted(valid_pools, key=lambda x: x['price'])
    
    buy_pool = sorted_pools[0]
    sell_pool = sorted_pools[-1]

    # Calculate the potential profit margin
    spread = ((sell_pool['price'] - buy_pool['price']) / buy_pool['price']) * 100

    # Print current best prices for monitoring
    print(f"\rBest Buy: ${buy_pool['price']:.6f} ({buy_pool['dex']}) | Best Sell: ${sell_pool['price']:.6f} ({sell_pool['dex']}) | Spread: {spread:.2f}%", end="")

    if spread >= MIN_SPREAD_PERCENT:
        # An opportunity is found, trigger the trade function
        asyncio.create_task(execute_trade(buy_pool, sell_pool, spread))


async def main():
    if not TOKEN_ADDRESS:
        print("Error: TOKEN_ADDRESS is not defined in your .env file.")
        return

    uri = "wss://io.dexscreener.com/dex/screener/v2/streaming/pairs/sub"
    
    print("Connecting to DexScreener WebSocket...")
    async with websockets.connect(uri) as websocket:
        print("Connection successful.")
        
        # Subscribe to the token's pairs
        subscription_message = {
            "method": "subscribe",
            "id": 1,
            "params": {
                "channel": "tokens",
                "token": TOKEN_ADDRESS,
                "chain": "base" # Assuming 'base' chain, can be made dynamic
            }
        }
        await websocket.send(json.dumps(subscription_message))

        print(f"Subscribed to updates for token: {TOKEN_ADDRESS}")
        print("Listening for price updates...")
        print("-" * 50)

        async for message in websocket:
            try:
                data = json.loads(message)
                if data.get("method") == "pair" and data.get("params"):
                    for pair_update in data["params"]:
                        # Update our global state with the new data
                        pools_data[pair_update['pairAddress']] = {
                            'dex': pair_update['dexId'],
                            'chain': pair_update['chainId'],
                            'pair': f"{pair_update['baseToken']['symbol']}/{pair_update['quoteToken']['symbol']}",
                            'price': float(pair_update['priceUsd']),
                            'liq_usd': float(pair_update['liquidity']['usd']),
                            'pairAddress': pair_update['pairAddress']
                        }
                    
                    # After every update, re-analyze for arbitrage
                    analyze_and_trade()

            except Exception as e:
                print(f"\nAn error occurred while processing a message: {e}")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nProgram stopped by user.")
