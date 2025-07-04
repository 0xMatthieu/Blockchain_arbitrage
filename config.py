import os
import json
from dotenv import load_dotenv
from web3 import Web3

# --- Load Configuration from .env file ---
load_dotenv()

# --- API and Bot Configuration ---
TOKEN_ADDRESSES_RAW = os.getenv("TOKEN_ADDRESSES", "")
MIN_LIQUIDITY_USD = float(os.getenv("MIN_LIQUIDITY_USD", 1000))
MIN_SPREAD_PERCENT = float(os.getenv("MIN_SPREAD_PERCENT", 1.0))
API_CALLS_PER_MINUTE = 280
POLL_INTERVAL = 60.0 / API_CALLS_PER_MINUTE
TRADE_COOLDOWN_SECONDS = 60

V2_FEE_BPS = 20          # Aerodrome, BaseSwap, etc. (0.20 %)
V3_FEE_MAP = {100: 1,    # Uniswap 0.01 %
              500: 5,    # 0.05 %
              2500: 25,  # Pancake 0.25 %
              3000: 30,  # Uniswap 0.30 %
              10000: 100 # 1 %
             }

# --- Blockchain Configuration ---
BASE_RPC_URL = os.getenv("BASE_RPC_URL")
PRIVATE_KEY = os.getenv("PRIVATE_KEY")
BOT_WALLET = os.getenv("BOT_WALLET")
# Retry mechanism for RPC calls
RPC_MAX_RETRIES = int(os.getenv("RPC_MAX_RETRIES", 5))
RPC_BACKOFF_FACTOR = float(os.getenv("RPC_BACKOFF_FACTOR", 0.5))

# --- Trading Configuration ---
BASE_CURRENCY_ADDRESS_RAW = os.getenv("BASE_CURRENCY_ADDRESS")
TRADE_AMOUNT_BASE_TOKEN = float(os.getenv("TRADE_AMOUNT_BASE_TOKEN", 0.0))
SLIPPAGE_TOLERANCE_PERCENT = float(os.getenv("SLIPPAGE_TOLERANCE_PERCENT", 1.0))
MAX_GAS_LIMIT = int(os.getenv("MAX_GAS_LIMIT", 500000))

# --- DEX Router Configuration Loading with Debugging ---
dex_routers_env_string = os.getenv("DEX_ROUTERS")
print(f"DEBUG: Raw DEX_ROUTERS string from .env: {dex_routers_env_string}")

try:
    # Use json.loads for safer and more standard parsing of the DEX_ROUTERS string
    DEX_ROUTERS_RAW = json.loads(dex_routers_env_string or '{}')
except json.JSONDecodeError as e:
    print(f"CRITICAL: Could not parse DEX_ROUTERS from .env file. Please ensure it is valid JSON. Error: {e}")
    DEX_ROUTERS_RAW = {}

# --- Web3 Setup ---
# Determine the correct Web3 provider based on the BASE_RPC_URL scheme.
if not BASE_RPC_URL:
    raise ValueError(
        "BASE_RPC_URL is not set. Please provide a websocket or http(s) RPC URL in your .env file."
    )

if BASE_RPC_URL.startswith(("ws://", "wss://")):
    # Use WebsocketProvider for websocket endpoints
    w3 = Web3(Web3.LegacyWebSocketProvider(BASE_RPC_URL))
elif BASE_RPC_URL.startswith(("http://", "https://")):
    # Use HTTPProvider when provided with an http(s) endpoint
    w3 = Web3(Web3.HTTPProvider(BASE_RPC_URL))
else:
    raise ValueError(
        "Invalid BASE_RPC_URL. Must start with ws://, wss://, http:// or https://"
    )

# --- Address Checksumming ---
TOKEN_ADDRESSES = [w3.to_checksum_address(addr.strip()) for addr in TOKEN_ADDRESSES_RAW.split(',') if addr.strip()] if TOKEN_ADDRESSES_RAW else []
BASE_CURRENCY_ADDRESS = w3.to_checksum_address(BASE_CURRENCY_ADDRESS_RAW) if BASE_CURRENCY_ADDRESS_RAW else None

DEX_ROUTERS = {}
for dex, info in DEX_ROUTERS_RAW.items():
    router_data = {
        'address': w3.to_checksum_address(info['address']),
        'version': info['version']
    }
    # Include optional fields if they exist
    if 'type' in info:
        router_data['type'] = info['type']
    if 'factory' in info:
        router_data['factory'] = w3.to_checksum_address(info['factory'])
    if 'quoter' in info:
        router_data['quoter'] = w3.to_checksum_address(info['quoter'])
    DEX_ROUTERS[dex] = router_data

# --- Account Setup ---
account = w3.eth.account.from_key(PRIVATE_KEY) if PRIVATE_KEY and PRIVATE_KEY != "0xyour_private_key_here" else None
