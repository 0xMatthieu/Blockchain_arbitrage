import os
import requests
import json
import time
from dotenv import load_dotenv
from web3 import Web3
from abi import ERC20_ABI, UNISWAP_V2_ROUTER_ABI

# --- Load Configuration from .env file ---
load_dotenv()

TOKEN_ADDRESS = os.getenv("TOKEN_ADDRESS")
MIN_LIQUIDITY_USD = float(os.getenv("MIN_LIQUIDITY_USD", 1000))
MIN_SPREAD_PERCENT = float(os.getenv("MIN_SPREAD_PERCENT", 1.0))
BASE_RPC_URL = os.getenv("BASE_RPC_URL")
PRIVATE_KEY = os.getenv("PRIVATE_KEY")

# --- Trading Configuration ---
BASE_CURRENCY_ADDRESS = os.getenv("BASE_CURRENCY_ADDRESS")
TRADE_AMOUNT_BASE_TOKEN = float(os.getenv("TRADE_AMOUNT_BASE_TOKEN", 0.0))
SLIPPAGE_TOLERANCE_PERCENT = float(os.getenv("SLIPPAGE_TOLERANCE_PERCENT", 1.0))
try:
    DEX_ROUTERS = json.loads(os.getenv("DEX_ROUTERS", '{}'))
except json.JSONDecodeError:
    DEX_ROUTERS = {}

# --- API Polling Configuration ---
API_CALLS_PER_MINUTE = 280
POLL_INTERVAL = 60.0 / API_CALLS_PER_MINUTE

# --- Global State ---
last_trade_attempt_ts = 0
TRADE_COOLDOWN_SECONDS = 60

# --- Web3 Setup ---
w3 = Web3(Web3.HTTPProvider(BASE_RPC_URL))
account = w3.eth.account.from_key(PRIVATE_KEY) if PRIVATE_KEY and PRIVATE_KEY != "0xyour_private_key_here" else None
if account:
    print(f"Bot wallet address: {account.address}")

def check_and_approve_token(token_address, spender_address, amount_to_approve_wei):
    if not account or not token_address or not spender_address:
        return
    
    token_contract = w3.eth.contract(address=token_address, abi=ERC20_ABI)
    
    print(f"Checking allowance for {spender_address} to spend {token_address}...")
    allowance = token_contract.functions.allowance(account.address, spender_address).call()
    
    if allowance < amount_to_approve_wei:
        print(f"Allowance is {allowance}. Need {amount_to_approve_wei}. Approving...")
        
        approve_txn = token_contract.functions.approve(
            spender_address,
            amount_to_approve_wei
        ).build_transaction({
            'from': account.address,
            'nonce': w3.eth.get_transaction_count(account.address),
            'gasPrice': w3.eth.gas_price,
        })
        
        signed_txn = w3.eth.account.sign_transaction(approve_txn, PRIVATE_KEY)
        tx_hash = w3.eth.send_raw_transaction(signed_txn.raw_transaction)
        
        print(f"Approval transaction sent. Hash: {tx_hash.hex()}. Waiting for confirmation...")
        w3.eth.wait_for_transaction_receipt(tx_hash)
        print(f"Token {token_address} approved for spender {spender_address}.")
    else:
        print("Sufficient allowance already set.")

def execute_trade(buy_pool, sell_pool, spread):
    global last_trade_attempt_ts
    
    print("\n" + "!"*60)
    print(f"!!! REAL TRADE TRIGGERED - Spread: {spread:.2f}% >= {MIN_SPREAD_PERCENT}% !!!")
    print("!"*60)

    if not all([account, BASE_CURRENCY_ADDRESS, TRADE_AMOUNT_BASE_TOKEN > 0]):
        print("!!! TRADING SKIPPED: Wallet or trading parameters not configured correctly.")
        return

    buy_dex_name = buy_pool['dex']
    sell_dex_name = sell_pool['dex']
    buy_router_address = DEX_ROUTERS.get(buy_dex_name)
    sell_router_address = DEX_ROUTERS.get(sell_dex_name)

    if not buy_router_address or not sell_router_address:
        print(f"!!! TRADING SKIPPED: Router address for {buy_dex_name} or {sell_dex_name} not in .env")
        return

    last_trade_attempt_ts = time.time() # Set cooldown immediately

    try:
        # --- 1. BUY TRANSACTION ---
        print(f"Step 1: Buying {TOKEN_ADDRESS} on {buy_dex_name}...")
        buy_router_contract = w3.eth.contract(address=buy_router_address, abi=UNISWAP_V2_ROUTER_ABI)
        
        base_token_contract = w3.eth.contract(address=BASE_CURRENCY_ADDRESS, abi=ERC20_ABI)
        base_decimals = base_token_contract.functions.decimals().call()
        amount_in_wei = int(TRADE_AMOUNT_BASE_TOKEN * (10**base_decimals))

        path_buy = [BASE_CURRENCY_ADDRESS, TOKEN_ADDRESS]
        amount_out_min_wei = int((amount_in_wei / buy_pool['price']) * (1 - SLIPPAGE_TOLERANCE_PERCENT / 100.0))

        buy_txn = buy_router_contract.functions.swapExactTokensForTokens(
            amount_in_wei,
            amount_out_min_wei,
            path_buy,
            account.address,
            int(time.time()) + 60 * 5 # 5 minute deadline
        ).build_transaction({
            'from': account.address,
            'gasPrice': w3.eth.gas_price,
            'nonce': w3.eth.get_transaction_count(account.address)
        })

        signed_buy_txn = w3.eth.account.sign_transaction(buy_txn, PRIVATE_KEY)
        buy_tx_hash = w3.eth.send_raw_transaction(signed_buy_txn.raw_transaction)
        print(f"  - Buy Tx sent: {buy_tx_hash.hex()}")
        buy_receipt = w3.eth.wait_for_transaction_receipt(buy_tx_hash, timeout=120)

        if buy_receipt['status'] == 0:
            print("  - BUY TRANSACTION FAILED (reverted). Aborting arbitrage.")
            return
        
        print("  - Buy transaction successful!")
        # To get the exact amount out, you'd parse the receipt logs. For simplicity, we'll use the trade amount.
        # A robust implementation MUST parse logs to get the true amount received.
        amount_to_sell_wei = amount_out_min_wei # Simplified for this example

        # --- 2. SELL TRANSACTION ---
        print(f"Step 2: Selling {TOKEN_ADDRESS} on {sell_dex_name}...")
        sell_router_contract = w3.eth.contract(address=sell_router_address, abi=UNISWAP_V2_ROUTER_ABI)
        path_sell = [TOKEN_ADDRESS, BASE_CURRENCY_ADDRESS]
        
        # For the sell, amountOutMin is the original investment minus slippage
        final_amount_out_min_wei = int(amount_in_wei * (1 - SLIPPAGE_TOLERANCE_PERCENT / 100.0))

        sell_txn = sell_router_contract.functions.swapExactTokensForTokens(
            amount_to_sell_wei,
            final_amount_out_min_wei,
            path_sell,
            account.address,
            int(time.time()) + 60 * 5
        ).build_transaction({
            'from': account.address,
            'gasPrice': w3.eth.gas_price,
            'nonce': w3.eth.get_transaction_count(account.address)
        })

        signed_sell_txn = w3.eth.account.sign_transaction(sell_txn, PRIVATE_KEY)
        sell_tx_hash = w3.eth.send_raw_transaction(signed_sell_txn.raw_transaction)
        print(f"  - Sell Tx sent: {sell_tx_hash.hex()}")
        sell_receipt = w3.eth.wait_for_transaction_receipt(sell_tx_hash, timeout=120)

        if sell_receipt['status'] == 0:
            print("  - SELL TRANSACTION FAILED. You are now holding the bought tokens.")
        else:
            print("  - Sell transaction successful! Arbitrage attempt complete.")

    except Exception as e:
        print(f"An error occurred during trade execution: {e}")


def analyze_and_trade(pairs):
    if time.time() - last_trade_attempt_ts < TRADE_COOLDOWN_SECONDS:
        return

    liquid_pools = [p for p in pairs if p.get('liq_usd') >= MIN_LIQUIDITY_USD]
    if len(liquid_pools) < 2:
        print("\rNot enough liquid pools to analyze. Waiting...", end="")
        return

    liquid_pools.sort(key=lambda x: x['price'])
    buy_pool = liquid_pools[0]
    sell_pool = liquid_pools[-1]
    spread = ((sell_pool['price'] - buy_pool['price']) / buy_pool['price']) * 100

    print(f"\rBest Buy: ${buy_pool['price']:.6f} ({buy_pool['dex']}) | Best Sell: ${sell_pool['price']:.6f} ({sell_pool['dex']}) | Spread: {spread:.2f}%   ", end="")

    if spread >= MIN_SPREAD_PERCENT:
        execute_trade(buy_pool, sell_pool, spread)


def main():
    if not all([TOKEN_ADDRESS, BASE_CURRENCY_ADDRESS, account]):
        print("Error: Core configuration (TOKEN_ADDRESS, BASE_CURRENCY_ADDRESS, PRIVATE_KEY) is missing.")
        return

    # --- Initial Approval Checks ---
    print("--- Running Initial Approval Checks ---")
    base_token_contract = w3.eth.contract(address=BASE_CURRENCY_ADDRESS, abi=ERC20_ABI)
    base_decimals = base_token_contract.functions.decimals().call()
    amount_to_approve_wei = int(TRADE_AMOUNT_BASE_TOKEN * (10**base_decimals))

    for dex, router_address in DEX_ROUTERS.items():
        print(f"\nChecking {dex.upper()} router ({router_address})...")
        # Approve the router to spend the BASE currency for the BUY
        check_and_approve_token(BASE_CURRENCY_ADDRESS, router_address, amount_to_approve_wei)
        # Approve the router to spend the TARGET currency for the SELL
        # We approve a very large number for the target token to avoid re-approving.
        check_and_approve_token(TOKEN_ADDRESS, router_address, w3.to_wei(2**64 - 1, 'ether'))
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
        
        time.sleep(POLL_INTERVAL)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nProgram stopped by user.")
