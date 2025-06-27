import requests, pandas as pd, time

# --- Configuration ---
ADDR = "0xc0634090f2fe6c6d75e61be2b949464abb498973"   # KTA on Base
MIN_LIQUIDITY_USD = 1000 # Liquidité minimale en USD pour considérer un pool

# --- Récupération des données ---
print(f"Recherche des pools pour le token : {ADDR}...")
try:
    response = requests.get(f"https://api.dexscreener.com/latest/dex/tokens/{ADDR}")
    response.raise_for_status() # Lève une exception pour les mauvais codes de statut (4xx ou 5xx)
    j = response.json()
except requests.exceptions.RequestException as e:
    print(f"Erreur lors de l'appel à l'API DexScreener : {e}")
    exit()

if not j or not j.get('pairs'):
    print("Aucune paire trouvée pour ce token.")
    exit()

# --- Traitement des données ---
pairs = []
for p in j['pairs']:
    # S'assurer que les données nécessaires existent, en particulier la liquidité et le prix
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
    print("Aucune donnée de paire valide n'a pu être extraite.")
    exit()

df = pd.DataFrame(pairs)

# --- Analyse pour l'arbitrage ---
print(f"Analyse des opportunités d'arbitrage avec une liquidité minimale de ${MIN_LIQUIDITY_USD:,.2f}...")

# Filtrer pour les pools avec une liquidité suffisante
liquid_pools = df[df['liq_usd'] >= MIN_LIQUIDITY_USD].copy()

# Trier par prix pour trouver facilement le plus bas et le plus haut
liquid_pools.sort_values('price', inplace=True)

if len(liquid_pools) < 2:
    print("Pas assez de pools avec une liquidité suffisante pour trouver une opportunité d'arbitrage.")
else:
    # Le pool avec le prix le plus bas (meilleur pour acheter)
    buy_pool = liquid_pools.iloc[0]
    # Le pool avec le prix le plus haut (meilleur pour vendre)
    sell_pool = liquid_pools.iloc[-1]

    # Calculer la marge de profit potentielle
    spread = ((sell_pool['price'] - buy_pool['price']) / buy_pool['price']) * 100

    print("\n--- Opportunité d'Arbitrage Potentielle Trouvée ---")
    print(f"Acheter sur : {buy_pool['dex']} ({buy_pool['chain']})")
    print(f"  Paire        : {buy_pool['pair']}")
    print(f"  Prix         : ${buy_pool['price']:.6f}")
    print(f"  Liquidité    : ${buy_pool['liq_usd']:,.2f}")
    print(f"  Adresse Paire: {buy_pool['pairAddress']}")
    
    print("\n" + "-"*20 + "\n")

    print(f"Vendre sur  : {sell_pool['dex']} ({sell_pool['chain']})")
    print(f"  Paire        : {sell_pool['pair']}")
    print(f"  Prix         : ${sell_pool['price']:.6f}")
    print(f"  Liquidité    : ${sell_pool['liq_usd']:,.2f}")
    print(f"  Adresse Paire: {sell_pool['pairAddress']}")

    print("\n" + "="*45)
    print(f"Écart de prix (Spread) potentiel : {spread:.2f}%")
    print("="*45)
    print("\nNote: Ceci n'est pas un conseil financier. Les frais de transaction, le slippage et d'autres facteurs peuvent affecter la rentabilité.")
