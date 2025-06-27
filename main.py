import requests, pandas as pd, time
ADDR = "0xc0634090f2fe6c6d75e61be2b949464abb498973"   # KTA on Base
j = requests.get(f"https://api.dexscreener.com/latest/dex/tokens/{ADDR}").json()
pairs = [{
    'dex': p['dexId'],
    'chain' : p['chainId'],
    'pair': f"{p['baseToken']['symbol']}/{p['quoteToken']['symbol']}",
    'price': float(p['priceUsd']),
    'liq_usd': p['liquidity']['usd'],
    'liq_base': p['liquidity']['base'],
    'liq_quote': p['liquidity']['quote'],
    'vol1h': p['volume']['h1'],
    'vol24h': p['volume']['h24'],
    'pairAddress': p['pairAddress']
} for p in j['pairs']]
df = pd.DataFrame(pairs).sort_values('price')
print(df.head())
