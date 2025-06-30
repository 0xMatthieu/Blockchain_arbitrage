import os
import json
from dotenv import load_dotenv
from web3 import Web3

# --- Load Configuration from .env file ---
load_dotenv()

# --- API and Bot Configuration ---
TOKEN_ADDRESS_RAW = os.getenv("TOKEN_ADDRESS")
MIN_LIQUIDITY_USD = float(os.getenv("MIN_LIQUIDITY_USD", 1000))
MIN_SPREAD_PERCENT = float(os.getenv("MIN_SPREAD_PERCENT", 1.0))
API_CALLS_PER_MINUTE = 280
POLL_INTERVAL = 60.0 / API_CALLS_PER_MINUTE
TRADE_COOLDOWN_SECONDS = 60

# --- Blockchain Configuration ---
BASE_RPC_URL = os.getenv("BASE_RPC_URL")
PRIVATE_KEY = os.getenv("PRIVATE_KEY")

# --- Trading Configuration ---
BASE_CURRENCY_ADDRESS_RAW = os.getenv("BASE_CURRENCY_ADDRESS")
TRADE_AMOUNT_BASE_TOKEN = float(os.getenv("TRADE_AMOUNT_BASE_TOKEN", 0.0))
SLIPPAGE_TOLERANCE_PERCENT = float(os.getenv("SLIPPAGE_TOLERANCE_PERCENT", 1.0))
MAX_GAS_LIMIT = int(os.getenv("MAX_GAS_LIMIT", 500000))
try:
    DEX_ROUTERS_RAW = eval(os.getenv("DEX_ROUTERS", '{}'))
except Exception:
    DEX_ROUTERS_RAW = {}

# --- Web3 Setup ---
w3 = Web3(Web3.HTTPProvider(BASE_RPC_URL))

# --- Address Checksumming ---
TOKEN_ADDRESS = w3.to_checksum_address(TOKEN_ADDRESS_RAW) if TOKEN_ADDRESS_RAW else None
BASE_CURRENCY_ADDRESS = w3.to_checksum_address(BASE_CURRENCY_ADDRESS_RAW) if BASE_CURRENCY_ADDRESS_RAW else None

DEX_ROUTERS = {}
for dex, info in DEX_ROUTERS_RAW.items():
    router_data = {
        'address': w3.to_checksum_address(info['address']),
        'version': info['version']
    }
    # Include the 'type' field if it exists (for Solidly forks, etc.)
    if 'type' in info:
        router_data['type'] = info['type']
    DEX_ROUTERS[dex] = router_data

# --- Account Setup ---
account = w3.eth.account.from_key(PRIVATE_KEY) if PRIVATE_KEY and PRIVATE_KEY != "0xyour_private_key_here" else None
