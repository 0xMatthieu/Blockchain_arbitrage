import time
from web3.logs import DISCARD
from config import (
    w3, account, PRIVATE_KEY, MAX_GAS_LIMIT, DEX_ROUTERS,
    TOKEN_ADDRESS, BASE_CURRENCY_ADDRESS, TRADE_AMOUNT_BASE_TOKEN, SLIPPAGE_TOLERANCE_PERCENT
)
from abi import ERC20_ABI, UNISWAP_V2_ROUTER_ABI, UNISWAP_V3_ROUTER_ABI, SOLIDLY_ROUTER_ABI
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
        # --- Pre-flight checks ---
        print("  - Performing pre-flight checks...")
        
        # 1. Check wallet balance for the base currency
        base_token_contract = w3.eth.contract(address=BASE_CURRENCY_ADDRESS, abi=ERC20_ABI)
        base_decimals = base_token_contract.functions.decimals().call()
        amount_in_wei = int(TRADE_AMOUNT_BASE_TOKEN * (10**base_decimals))
        wallet_balance_wei = base_token_contract.functions.balanceOf(account.address).call()

        if wallet_balance_wei < amount_in_wei:
            print(f"!!! TRADE SKIPPED: Insufficient balance. Have {wallet_balance_wei / (10**base_decimals):.6f}, need {TRADE_AMOUNT_BASE_TOKEN}.")
            return
        print(f"  - Wallet balance check passed. Have {wallet_balance_wei / (10**base_decimals):.6f}, need {TRADE_AMOUNT_BASE_TOKEN}.")

        # 2. Check if trade size is a reasonable fraction of pool liquidity
        trade_amount_usd = TRADE_AMOUNT_BASE_TOKEN * buy_pool.get('base_currency_price_usd', 0)
        if trade_amount_usd == 0:
            print("!!! TRADE SKIPPED: Could not determine USD value of trade amount.")
            return

        buy_liq_usd = buy_pool['liq_usd']
        sell_liq_usd = sell_pool['liq_usd']
        
        # Trade shouldn't be more than 10% of liquidity to avoid high price impact
        LIQUIDITY_IMPACT_THRESHOLD = 0.1 

        if trade_amount_usd > buy_liq_usd * LIQUIDITY_IMPACT_THRESHOLD:
            print(f"!!! TRADE SKIPPED: Trade size (${trade_amount_usd:,.2f}) is too large for buy pool liquidity (${buy_liq_usd:,.2f}).")
            return

        if trade_amount_usd > sell_liq_usd * LIQUIDITY_IMPACT_THRESHOLD:
            print(f"!!! TRADE SKIPPED: Trade size (${trade_amount_usd:,.2f}) is too large for sell pool liquidity (${sell_liq_usd:,.2f}).")
            return
            
        print(f"  - Liquidity check passed. Trade size ${trade_amount_usd:,.2f} is reasonable for both pools.")

        # --- 1. BUY TRANSACTION ---
        print(f"Step 1: Buying {TOKEN_ADDRESS} on {buy_dex_name} (v{buy_router_info['version']})...")
        print(f"  - Buy Router Info: {buy_router_info}")
        print(f"  - Amount In (wei): {amount_in_wei}")
        
        target_token_contract = w3.eth.contract(address=TOKEN_ADDRESS, abi=ERC20_ABI)
        target_decimals = target_token_contract.functions.decimals().call()

        # --- V2 BUY LOGIC ---
        if buy_router_info['version'] == 2:
            buy_router_type = buy_router_info.get('type', 'uniswapv2')
            if buy_router_type == 'solidly':
                buy_router_contract = w3.eth.contract(address=buy_router_info['address'], abi=SOLIDLY_ROUTER_ABI)
                is_stable_pool = buy_pool.get('stable', False)
                routes_buy = [(BASE_CURRENCY_ADDRESS, TOKEN_ADDRESS, is_stable_pool)]
                print(f"  - Solidly Route: {routes_buy}")
                amounts_out = buy_router_contract.functions.getAmountsOut(amount_in_wei, routes_buy).call()
                print(f"  - Solidly getAmountsOut result: {amounts_out}")
                amount_out_min_wei = int(amounts_out[-1] * (1 - SLIPPAGE_TOLERANCE_PERCENT / 100.0))
                print(f"  - Solidly Min Amount Out (wei): {amount_out_min_wei}")
                swap_function = buy_router_contract.functions.swapExactTokensForTokens(
                    amount_in_wei, amount_out_min_wei, routes_buy, account.address, int(time.time()) + 300
                )
            else: # Default to uniswapv2
                buy_router_contract = w3.eth.contract(address=buy_router_info['address'], abi=UNISWAP_V2_ROUTER_ABI)
                path_buy = [BASE_CURRENCY_ADDRESS, TOKEN_ADDRESS]
                print(f"  - V2 Path: {path_buy}")
                amounts_out = buy_router_contract.functions.getAmountsOut(amount_in_wei, path_buy).call()
                print(f"  - V2 getAmountsOut result: {amounts_out}")
                amount_out_min_wei = int(amounts_out[-1] * (1 - SLIPPAGE_TOLERANCE_PERCENT / 100.0))
                print(f"  - V2 Min Amount Out (wei): {amount_out_min_wei}")
                swap_function = buy_router_contract.functions.swapExactTokensForTokens(
                    amount_in_wei, amount_out_min_wei, path_buy, account.address, int(time.time()) + 300
                )
        # --- V3 BUY LOGIC ---
        elif buy_router_info['version'] == 3:
            buy_router_contract = w3.eth.contract(address=buy_router_info['address'], abi=UNISWAP_V3_ROUTER_ABI)
            fee = int(buy_pool['feeBps'] * 100)
            print(f"  - V3 Fee: {fee}")
            
            expected_amount_out_float = TRADE_AMOUNT_BASE_TOKEN / buy_pool['price']
            min_amount_out_float = expected_amount_out_float * (1 - SLIPPAGE_TOLERANCE_PERCENT / 100.0)
            amount_out_min_wei = int(min_amount_out_float * (10**target_decimals))
            print(f"  - V3 Min Amount Out (wei): {amount_out_min_wei}")

            swap_params = {
                'tokenIn': BASE_CURRENCY_ADDRESS, 'tokenOut': TOKEN_ADDRESS, 'fee': fee,
                'recipient': account.address, 'deadline': int(time.time()) + 300,
                'amountIn': amount_in_wei, 'amountOutMinimum': amount_out_min_wei,
                'sqrtPriceLimitX96': 0
            }
            print(f"  - V3 Swap Params: {swap_params}")
            swap_function = buy_router_contract.functions.exactInputSingle(swap_params)
        else:
            raise NotImplementedError(f"DEX version {buy_router_info['version']} is not supported.")

        # --- Build and Send Buy Transaction ---
        print("  - Building buy transaction...")
        max_priority_fee = w3.eth.max_priority_fee
        base_fee = w3.eth.get_block('latest')['baseFeePerGas']
        max_fee_per_gas = base_fee * 2 + max_priority_fee
        buy_payload = {
            'from': account.address, 'nonce': w3.eth.get_transaction_count(account.address),
            'maxFeePerGas': max_fee_per_gas, 'maxPriorityFeePerGas': max_priority_fee,
            'chainId': w3.eth.chain_id
        }
        print(f"  - Estimating gas with payload: {buy_payload}")
        gas_estimate = swap_function.estimate_gas(buy_payload)
        print(f"  - Gas estimate: {gas_estimate}")
        buy_payload['gas'] = min(int(gas_estimate * 1.2), MAX_GAS_LIMIT)
        print(f"  - Final buy payload: {buy_payload}")
        buy_txn = swap_function.build_transaction(buy_payload)
        
        print("  - Signing buy transaction...")
        signed_buy_txn = w3.eth.account.sign_transaction(buy_txn, PRIVATE_KEY)
        
        print("  - Sending buy transaction...")
        buy_tx_hash = w3.eth.send_raw_transaction(signed_buy_txn.raw_transaction)
        print(f"  - Buy Tx sent: {buy_tx_hash.hex()}. Waiting for receipt...")
        buy_receipt = w3.eth.wait_for_transaction_receipt(buy_tx_hash, timeout=120)

        if buy_receipt['status'] == 0:
            print("  - BUY TRANSACTION FAILED (reverted). Aborting arbitrage.")
            return

        # --- Parse receipt for actual amount received ---
        print("  - Buy transaction successful! Parsing receipt...")
        amount_received_wei = 0
        
        # Use web3.py's event processing to find the Transfer event to our address.
        # This is more robust than manually parsing logs.
        try:
            transfer_events = target_token_contract.events.Transfer().process_receipt(buy_receipt, errors=DISCARD)
            for event in transfer_events:
                if event.args.to == account.address:
                    amount_received_wei = event.args.value
                    print(f"  - Found transfer of {amount_received_wei / (10**target_decimals):.4f} tokens to wallet.")
                    break # Stop after finding the first relevant transfer
        except Exception as e:
            # This might happen with non-standard contracts or ABI mismatches.
            print(f"  - Error parsing transaction receipt for Transfer events: {e}")

        if amount_received_wei == 0:
            print("  - CRITICAL: Could not determine received token amount from receipt. Aborting sell.")
            return
        
        # --- 2. SELL TRANSACTION ---
        print(f"Step 2: Selling {amount_received_wei / (10**target_decimals)} of {TOKEN_ADDRESS} on {sell_dex_name} (v{sell_router_info['version']})...")
        print(f"  - Sell Router Info: {sell_router_info}")
        
        # --- V2 SELL LOGIC ---
        if sell_router_info['version'] == 2:
            sell_router_type = sell_router_info.get('type', 'uniswapv2')
            if sell_router_type == 'solidly':
                sell_router_contract = w3.eth.contract(address=sell_router_info['address'], abi=SOLIDLY_ROUTER_ABI)
                is_stable_pool = sell_pool.get('stable', False)
                routes_sell = [(TOKEN_ADDRESS, BASE_CURRENCY_ADDRESS, is_stable_pool)]
                print(f"  - Solidly Route: {routes_sell}")
                amounts_out_sell = sell_router_contract.functions.getAmountsOut(amount_received_wei, routes_sell).call()
                print(f"  - Solidly getAmountsOut result: {amounts_out_sell}")
                final_amount_out_min_wei = int(amounts_out_sell[-1] * (1 - SLIPPAGE_TOLERANCE_PERCENT / 100.0))
                print(f"  - Solidly Min Amount Out (wei): {final_amount_out_min_wei}")
                sell_swap_function = sell_router_contract.functions.swapExactTokensForTokens(
                    amount_received_wei, final_amount_out_min_wei, routes_sell, account.address, int(time.time()) + 300
                )
            else: # Default to uniswapv2
                sell_router_contract = w3.eth.contract(address=sell_router_info['address'], abi=UNISWAP_V2_ROUTER_ABI)
                path_sell = [TOKEN_ADDRESS, BASE_CURRENCY_ADDRESS]
                print(f"  - V2 Path: {path_sell}")
                amounts_out_sell = sell_router_contract.functions.getAmountsOut(amount_received_wei, path_sell).call()
                print(f"  - V2 getAmountsOut result: {amounts_out_sell}")
                final_amount_out_min_wei = int(amounts_out_sell[-1] * (1 - SLIPPAGE_TOLERANCE_PERCENT / 100.0))
                print(f"  - V2 Min Amount Out (wei): {final_amount_out_min_wei}")
                sell_swap_function = sell_router_contract.functions.swapExactTokensForTokens(
                    amount_received_wei, final_amount_out_min_wei, path_sell, account.address, int(time.time()) + 300
                )
        # --- V3 SELL LOGIC ---
        elif sell_router_info['version'] == 3:
            sell_router_contract = w3.eth.contract(address=sell_router_info['address'], abi=UNISWAP_V3_ROUTER_ABI)
            fee = int(sell_pool['feeBps'] * 100)
            print(f"  - V3 Fee: {fee}")
            expected_sell_return_float = (amount_received_wei / (10**target_decimals)) * sell_pool['price']
            min_sell_return_float = expected_sell_return_float * (1 - SLIPPAGE_TOLERANCE_PERCENT / 100.0)
            final_amount_out_min_wei = int(min_sell_return_float * (10**base_decimals))
            print(f"  - V3 Min Amount Out (wei): {final_amount_out_min_wei}")
            sell_swap_params = {
                'tokenIn': TOKEN_ADDRESS, 'tokenOut': BASE_CURRENCY_ADDRESS, 'fee': fee,
                'recipient': account.address, 'deadline': int(time.time()) + 300,
                'amountIn': amount_received_wei, 'amountOutMinimum': final_amount_out_min_wei,
                'sqrtPriceLimitX96': 0
            }
            print(f"  - V3 Swap Params: {sell_swap_params}")
            sell_swap_function = sell_router_contract.functions.exactInputSingle(sell_swap_params)
        else:
            raise NotImplementedError(f"DEX version {sell_router_info['version']} is not supported.")

        # --- Build and Send Sell Transaction ---
        print("  - Building sell transaction...")
        sell_payload = {
            'from': account.address, 'nonce': w3.eth.get_transaction_count(account.address),
            'maxFeePerGas': max_fee_per_gas, 'maxPriorityFeePerGas': max_priority_fee,
            'chainId': w3.eth.chain_id
        }
        print(f"  - Estimating gas with payload: {sell_payload}")
        gas_estimate_sell = sell_swap_function.estimate_gas(sell_payload)
        print(f"  - Gas estimate: {gas_estimate_sell}")
        sell_payload['gas'] = min(int(gas_estimate_sell * 1.2), MAX_GAS_LIMIT)
        print(f"  - Final sell payload: {sell_payload}")
        sell_txn = sell_swap_function.build_transaction(sell_payload)

        print("  - Signing sell transaction...")
        signed_sell_txn = w3.eth.account.sign_transaction(sell_txn, PRIVATE_KEY)

        print("  - Sending sell transaction...")
        sell_tx_hash = w3.eth.send_raw_transaction(signed_sell_txn.raw_transaction)
        print(f"  - Sell Tx sent: {sell_tx_hash.hex()}. Waiting for receipt...")
        sell_receipt = w3.eth.wait_for_transaction_receipt(sell_tx_hash, timeout=120)

        if sell_receipt['status'] == 0:
            print("  - SELL TRANSACTION FAILED. You are now holding the bought tokens.")
        else:
            print("  - Sell transaction successful! Arbitrage attempt complete.")

    except Exception as e:
        print(f"An unexpected error occurred during trade execution: {e}")
