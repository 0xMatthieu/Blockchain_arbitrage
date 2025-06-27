import time
from config import (
    w3, account, PRIVATE_KEY, MAX_GAS_LIMIT, DEX_ROUTERS,
    TOKEN_ADDRESS, BASE_CURRENCY_ADDRESS, TRADE_AMOUNT_BASE_TOKEN, SLIPPAGE_TOLERANCE_PERCENT
)
from abi import ERC20_ABI, UNISWAP_V2_ROUTER_ABI, UNISWAP_V3_ROUTER_ABI
from dex_utils import find_router_info

def execute_trade(buy_pool, sell_pool, spread):
    print("\n" + "!"*60)
    print(f"!!! REAL TRADE TRIGGERED - Spread: {spread:.2f}% !!!")
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

    try:
        # --- 1. BUY TRANSACTION ---
        print(f"Step 1: Buying {TOKEN_ADDRESS} on {buy_dex_name} (v{buy_router_info['version']})...")
        
        base_token_contract = w3.eth.contract(address=BASE_CURRENCY_ADDRESS, abi=ERC20_ABI)
        base_decimals = base_token_contract.functions.decimals().call()
        amount_in_wei = int(TRADE_AMOUNT_BASE_TOKEN * (10**base_decimals))
        
        target_token_contract = w3.eth.contract(address=TOKEN_ADDRESS, abi=ERC20_ABI)
        target_decimals = target_token_contract.functions.decimals().call()

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
            fee = int(buy_pool['feeBps'] * 100)
            
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
