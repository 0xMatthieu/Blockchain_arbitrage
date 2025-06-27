import os
import requests
import json
import time
from dotenv import load_dotenv
from web3 import Web3
from abi import ERC20_ABI, UNISWAP_V2_ROUTER_ABI, UNISWAP_V3_ROUTER_ABI

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
MAX_GAS_LIMIT = int(os.getenv("MAX_GAS_LIMIT", 500000))
try:
    # Use eval to correctly parse the more complex dictionary structure
    DEX_ROUTERS = eval(os.getenv("DEX_ROUTERS", '{}'))
except Exception:
    DEX_ROUTERS = {}

# --- API Polling Configuration ---
API_CALLS_PER_MINUTE = 280
POLL_INTERVAL = 60.0 / API_CALLS_PER_MINUTE

# --- Global State ---
last_trade_attempt_ts = 0
TRADE_COOLDOWN_SECONDS = 60

# --- Web3 Setup ---
w3 = Web3(Web3.HTTPProvider(BASE_RPC_URL))

# --- Convert addresses to checksum format for web3.py compatibility ---
if TOKEN_ADDRESS:
    TOKEN_ADDRESS = w3.to_checksum_address(TOKEN_ADDRESS)
if BASE_CURRENCY_ADDRESS:
    BASE_CURRENCY_ADDRESS = w3.to_checksum_address(BASE_CURRENCY_ADDRESS)
for dex in DEX_ROUTERS:
    DEX_ROUTERS[dex]['address'] = w3.to_checksum_address(DEX_ROUTERS[dex]['address'])

account = w3.eth.account.from_key(PRIVATE_KEY) if PRIVATE_KEY and PRIVATE_KEY != "0xyour_private_key_here" else None
if account:
    print(f"Bot wallet address: {account.address}")

def find_router_info(dex_id, routers):
    """Finds a router's info (address and version) with flexible matching."""
    for key, info in routers.items():
        if dex_id in key or key in dex_id:
            return info
    return None

def check_and_approve_token(token_address, spender_address, amount_to_approve_wei):
    if not account or not token_address or not spender_address:
        return
    
    token_contract = w3.eth.contract(address=token_address, abi=ERC20_ABI)
    
    print(f"Checking allowance for {spender_address} to spend {token_address}...")
    allowance = token_contract.functions.allowance(account.address, spender_address).call()
    
    if allowance < amount_to_approve_wei:
        print(f"Allowance is {allowance}. Need {amount_to_approve_wei}. Approving...")
        
        try:
            max_priority_fee = w3.eth.max_priority_fee
            base_fee = w3.eth.get_block('latest')['baseFeePerGas']
            max_fee_per_gas = base_fee * 2 + max_priority_fee

            approve_payload = {
                'from': account.address,
                'nonce': w3.eth.get_transaction_count(account.address),
                'maxFeePerGas': max_fee_per_gas,
                'maxPriorityFeePerGas': max_priority_fee,
                'chainId': w3.eth.chain_id
            }
            gas_estimate = token_contract.functions.approve(
                spender_address, amount_to_approve_wei
            ).estimate_gas(approve_payload)
            
            buffered_gas = int(gas_estimate * 1.2)
            approve_payload['gas'] = min(buffered_gas, MAX_GAS_LIMIT)

            approve_txn = token_contract.functions.approve(
                spender_address, amount_to_approve_wei
            ).build_transaction(approve_payload)
            
            signed_txn = w3.eth.account.sign_transaction(approve_txn, PRIVATE_KEY)
            tx_hash = w3.eth.send_raw_transaction(signed_txn.raw_transaction)
            
            print(f"Approval transaction sent. Hash: {tx_hash.hex()}. Waiting for confirmation...")
            w3.eth.wait_for_transaction_receipt(tx_hash)
            print(f"Token {token_address} approved for spender {spender_address}.")
        except Exception as e:
            print(f"  - Could not send approval transaction: {e}")
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
    buy_router_info = find_router_info(buy_dex_name, DEX_ROUTERS)
    sell_router_info = find_router_info(sell_dex_name, DEX_ROUTERS)

    if not buy_router_info or not sell_router_info:
        print(f"!!! TRADING SKIPPED: Router info for '{buy_dex_name}' or '{sell_dex_name}' not found in .env")
        return

    last_trade_attempt_ts = time.time()

    # --- DYNAMIC TRADE LOGIC BASED ON DEX VERSION ---
    try:
        # --- 1. BUY TRANSACTION ---
        print(f"Step 1: Buying {TOKEN_ADDRESS} on {buy_dex_name} (v{buy_router_info['version']})...")
        
        base_token_contract = w3.eth.contract(address=BASE_CURRENCY_ADDRESS, abi=ERC20_ABI)
        base_decimals = base_token_contract.functions.decimals().call()
        amount_in_wei = int(TRADE_AMOUNT_BASE_TOKEN * (10**base_decimals))

        # --- V2 BUY LOGIC ---
        if buy_router_info['version'] == 2:
            buy_router_contract = w3.eth.contract(address=buy_router_info['address'], abi=UNISWAP_V2_ROUTER_ABI)
            path_buy = [BASE_CURRENCY_ADDRESS, TOKEN_ADDRESS]
            amounts_out = buy_router_contract.functions.getAmountsOut(amount_in_wei, path_buy).call()
            amount_out_min_wei = int(amounts_out[1] * (1 - SLIPPAGE_TOLERANCE_PERCENT / 100.0))
            swap_function = buy_router_contract.functions.swapExactTokensForTokens(
                amount_in_wei, amount_out_min_wei, path_buy, account.address, int(time.time()) + 300
            )
        # --- V3 BUY LOGIC ---
        elif buy_router_info['version'] == 3:
            buy_router_contract = w3.eth.contract(address=buy_router_info['address'], abi=UNISWAP_V3_ROUTER_ABI)
            fee = int(buy_pool['feeBps'] * 100) # e.g., 0.05% fee is 500
            
            # V3 quote is not on-chain, so we calculate min amount from API price
            target_token_contract = w3.eth.contract(address=TOKEN_ADDRESS, abi=ERC20_ABI)
            target_decimals = target_token_contract.functions.decimals().call()
            expected_amount_out_float = TRADE_AMOUNT_BASE_TOKEN / buy_pool['price']
            min_amount_out_float = expected_amount_out_float * (1 - SLIPPAGE_TOLERANCE_PERCENT / 100.0)
            amount_out_min_wei = int(min_amount_out_float * (10**target_decimals))

            swap_params = {
                'tokenIn': BASE_CURRENCY_ADDRESS, 'tokenOut': TOKEN_ADDRESS, 'fee': fee,
                'recipient': account.address, 'deadline': int(time.time()) + 300,
                'amountIn': amount_in_wei, 'amountOutMinimum': amount_out_min_wei,
                'sqrtPriceLimitX96': 0
            }
            swap_function = buy_router_contract.functions.exactInputSingle(swap_params)
        else:
            raise NotImplementedError(f"DEX version {buy_router_info['version']} is not supported.")

        # --- Build and Send Buy Transaction ---
        max_priority_fee = w3.eth.max_priority_fee
        base_fee = w3.eth.get_block('latest')['baseFeePerGas']
        max_fee_per_gas = base_fee * 2 + max_priority_fee
        buy_payload = {
            'from': account.address, 'nonce': w3.eth.get_transaction_count(account.address),
            'maxFeePerGas': max_fee_per_gas, 'maxPriorityFeePerGas': max_priority_fee,
            'chainId': w3.eth.chain_id
        }
        gas_estimate = swap_function.estimate_gas(buy_payload)
        buy_payload['gas'] = min(int(gas_estimate * 1.2), MAX_GAS_LIMIT)
        buy_txn = swap_function.build_transaction(buy_payload)
        signed_buy_txn = w3.eth.account.sign_transaction(buy_txn, PRIVATE_KEY)
        buy_tx_hash = w3.eth.send_raw_transaction(signed_buy_txn.raw_transaction)
        print(f"  - Buy Tx sent: {buy_tx_hash.hex()}")
        buy_receipt = w3.eth.wait_for_transaction_receipt(buy_tx_hash, timeout=120)

        if buy_receipt['status'] == 0:
            print("  - BUY TRANSACTION FAILED (reverted). Aborting arbitrage.")
            return

        # --- Parse receipt for actual amount received ---
        print("  - Buy transaction successful! Parsing receipt...")
        amount_received_wei = 0
        TRANSFER_EVENT_SIG = w3.keccak(text="Transfer(address,address,uint256)").hex()
        for log in buy_receipt.logs:
            if str(log.address) == TOKEN_ADDRESS and str(log.topics[0].hex()) == TRANSFER_EVENT_SIG and w3.to_checksum_address("0x" + log.topics[2].hex()[-40:]) == account.address:
                amount_received_wei = int(log.data.hex(), 16)
                break
        if amount_received_wei == 0:
            print("  - CRITICAL: Could not determine received token amount. Aborting sell.")
            return
        
        # --- 2. SELL TRANSACTION ---
        print(f"Step 2: Selling {amount_received_wei / (10**target_decimals)} of {TOKEN_ADDRESS} on {sell_dex_name} (v{sell_router_info['version']})...")
        
        # --- V2 SELL LOGIC ---
        if sell_router_info['version'] == 2:
            sell_router_contract = w3.eth.contract(address=sell_router_info['address'], abi=UNISWAP_V2_ROUTER_ABI)
            path_sell = [TOKEN_ADDRESS, BASE_CURRENCY_ADDRESS]
            amounts_out_sell = sell_router_contract.functions.getAmountsOut(amount_received_wei, path_sell).call()
            final_amount_out_min_wei = int(amounts_out_sell[1] * (1 - SLIPPAGE_TOLERANCE_PERCENT / 100.0))
            sell_swap_function = sell_router_contract.functions.swapExactTokensForTokens(
                amount_received_wei, final_amount_out_min_wei, path_sell, account.address, int(time.time()) + 300
            )
        # --- V3 SELL LOGIC ---
        elif sell_router_info['version'] == 3:
            sell_router_contract = w3.eth.contract(address=sell_router_info['address'], abi=UNISWAP_V3_ROUTER_ABI)
            fee = int(sell_pool['feeBps'] * 100)
            expected_sell_return_float = (amount_received_wei / (10**target_decimals)) * sell_pool['price']
            min_sell_return_float = expected_sell_return_float * (1 - SLIPPAGE_TOLERANCE_PERCENT / 100.0)
            final_amount_out_min_wei = int(min_sell_return_float * (10**base_decimals))
            sell_swap_params = {
                'tokenIn': TOKEN_ADDRESS, 'tokenOut': BASE_CURRENCY_ADDRESS, 'fee': fee,
                'recipient': account.address, 'deadline': int(time.time()) + 300,
                'amountIn': amount_received_wei, 'amountOutMinimum': final_amount_out_min_wei,
                'sqrtPriceLimitX96': 0
            }
            sell_swap_function = sell_router_contract.functions.exactInputSingle(sell_swap_params)
        else:
            raise NotImplementedError(f"DEX version {sell_router_info['version']} is not supported.")

        # --- Build and Send Sell Transaction ---
        sell_payload = {
            'from': account.address, 'nonce': w3.eth.get_transaction_count(account.address),
            'maxFeePerGas': max_fee_per_gas, 'maxPriorityFeePerGas': max_priority_fee,
            'chainId': w3.eth.chain_id
        }
        gas_estimate_sell = sell_swap_function.estimate_gas(sell_payload)
        sell_payload['gas'] = min(int(gas_estimate_sell * 1.2), MAX_GAS_LIMIT)
        sell_txn = sell_swap_function.build_transaction(sell_payload)
        signed_sell_txn = w3.eth.account.sign_transaction(sell_txn, PRIVATE_KEY)
        sell_tx_hash = w3.eth.send_raw_transaction(signed_sell_txn.raw_transaction)
        print(f"  - Sell Tx sent: {sell_tx_hash.hex()}")
        sell_receipt = w3.eth.wait_for_transaction_receipt(sell_tx_hash, timeout=120)

        if sell_receipt['status'] == 0:
            print("  - SELL TRANSACTION FAILED. You are now holding the bought tokens.")
        else:
            print("  - Sell transaction successful! Arbitrage attempt complete.")

    except Exception as e:
        print(f"An unexpected error occurred during trade execution: {e}")

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

    print("--- Running Initial Approval Checks ---")
    base_token_contract = w3.eth.contract(address=BASE_CURRENCY_ADDRESS, abi=ERC20_ABI)
    base_decimals = base_token_contract.functions.decimals().call()
    amount_to_approve_wei = int(TRADE_AMOUNT_BASE_TOKEN * (10**base_decimals))

    for dex, info in DEX_ROUTERS.items():
        print(f"\nChecking {dex.upper()} router ({info['address']})...")
        check_and_approve_token(BASE_CURRENCY_ADDRESS, info['address'], amount_to_approve_wei)
        check_and_approve_token(TOKEN_ADDRESS, info['address'], w3.to_wei(2**64 - 1, 'ether'))
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
                    current_pairs.append({
                        'dex': p['dexId'],
                        'chain' : p['chainId'],
                        'pair': f"{p['baseToken']['symbol']}/{p['quoteToken']['symbol']}",
                        'price': float(p['priceNative']),
                        'liq_usd': float(p['liquidity']['usd']),
                        'pairAddress': p['pairAddress'],
                        'feeBps': p.get('feeBps', 0) # Get fee for V3, default to 0
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
