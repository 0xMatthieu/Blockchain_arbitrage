import requests, pandas as pd, time

# --- Configuration ---
ADDR = "0xc0634090f2fe6c6d75e61be2b949464abb498973"   # KTA on Base
MIN_LIQUIDITY_USD = 1000 # Minimum liquidity in USD to consider a pool

# --- Data Fetching ---
print(f"Searching for pools for token: {ADDR}...")
try:
    response = requests.get(f"https://api.dexscreener.com/latest/dex/tokens/{ADDR}")
    response.raise_for_status() # Raise an exception for bad status codes (4xx or 5xx)
    j = response.json()
except requests.exceptions.RequestException as e:
    print(f"Error calling DexScreener API: {e}")
    exit()

if not j or not j.get('pairs'):
    print("No pairs found for this token.")
    exit()

# --- Data Processing ---
pairs = []
for p in j['pairs']:
    # Ensure necessary data exists, especially liquidity and price
    if p.get('priceUsd') and p.get('liquidity') and p.get('liquidity').get('usd'):
        pairs.append({
            'dex': p['dexId'],
            'chain' : p['chainId'],
            'pair': f"{p['baseToken']['symbol']}/{p['quoteToken']['symbol']}",
            'price': float(p['priceUsd']),
            'liq_usd': float(p['liquidity']['usd']),
            'pairAddress': p['pairAddress']
        })

if not pairs:
    print("No valid pair data could be extracted.")
    exit()

df = pd.DataFrame(pairs)

# --- Arbitrage Analysis ---
print(f"Analyzing arbitrage opportunities with a minimum liquidity of ${MIN_LIQUIDITY_USD:,.2f}...")

# Filter for pools with sufficient liquidity
liquid_pools = df[df['liq_usd'] >= MIN_LIQUIDITY_USD].copy()

# Sort by price to easily find the lowest and highest
liquid_pools.sort_values('price', inplace=True)

if len(liquid_pools) < 2:
    print("Not enough pools with sufficient liquidity to find an arbitrage opportunity.")
else:
    # The pool with the lowest price (best for buying)
    buy_pool = liquid_pools.iloc[0]
    # The pool with the highest price (best for selling)
    sell_pool = liquid_pools.iloc[-1]

    # Calculate the potential profit margin
    spread = ((sell_pool['price'] - buy_pool['price']) / buy_pool['price']) * 100

    print("\n--- Potential Arbitrage Opportunity Found ---")
    print(f"Buy on: {buy_pool['dex']} ({buy_pool['chain']})")
    print(f"  Pair         : {buy_pool['pair']}")
    print(f"  Price        : ${buy_pool['price']:.6f}")
    print(f"  Liquidity    : ${buy_pool['liq_usd']:,.2f}")
    print(f"  Pair Address : {buy_pool['pairAddress']}")
    
    print("\n" + "-"*20 + "\n")

    print(f"Sell on : {sell_pool['dex']} ({sell_pool['chain']})")
    print(f"  Pair         : {sell_pool['pair']}")
    print(f"  Price        : ${sell_pool['price']:.6f}")
    print(f"  Liquidity    : ${sell_pool['liq_usd']:,.2f}")
    print(f"  Pair Address : {sell_pool['pairAddress']}")

    print("\n" + "="*45)
    print(f"Potential price spread: {spread:.2f}%")
    print("="*45)
    print("\nNote: This is not financial advice. Transaction fees, slippage, and other factors can affect profitability.")
