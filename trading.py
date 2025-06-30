import time
from web3.logs import DISCARD
from web3.exceptions import ContractLogicError
from config import (
    w3, account, PRIVATE_KEY, MAX_GAS_LIMIT, DEX_ROUTERS,
    TOKEN_ADDRESS, BASE_CURRENCY_ADDRESS, TRADE_AMOUNT_BASE_TOKEN, SLIPPAGE_TOLERANCE_PERCENT,
    RPC_MAX_RETRIES, RPC_BACKOFF_FACTOR
)
from abi import (
    ERC20_ABI, UNISWAP_V2_ROUTER_ABI, UNISWAP_V3_ROUTER_ABI, SOLIDLY_ROUTER_ABI, 
    SOLIDLY_FACTORY_ABI, UNISWAP_V3_POOL_ABI
)
from dex_utils import find_router_info

def resilient_rpc_call(callable_func):
    """
    Tries to execute a callable that makes an RPC call, with exponential backoff.
    A 'callable' is a function that takes no arguments, e.g., lambda: my_func(arg1, arg2)
    """
    for i in range(RPC_MAX_RETRIES):
        try:
            return callable_func()
        except Exception as e:
            # If it's a contract logic error (revert), don't retry, it will fail again.
            if "execution reverted" in str(e) or isinstance(e, ContractLogicError):
                print(f"  - Contract logic error (revert): {e}")
                raise e
            
            wait_time = RPC_BACKOFF_FACTOR * (2 ** i)
            print(f"\n  - RPC call failed with error: {e}. Retrying in {wait_time:.2f}s... ({i+1}/{RPC_MAX_RETRIES})")
            time.sleep(wait_time)
    raise Exception(f"RPC call failed after {RPC_MAX_RETRIES} retries.")


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
        base_decimals = resilient_rpc_call(lambda: base_token_contract.functions.decimals().call())
        amount_in_wei = int(TRADE_AMOUNT_BASE_TOKEN * (10**base_decimals))
        wallet_balance_wei = resilient_rpc_call(lambda: base_token_contract.functions.balanceOf(account.address).call())

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
        target_decimals = resilient_rpc_call(lambda: target_token_contract.functions.decimals().call())

        # --- V2 BUY LOGIC ---
        if buy_router_info['version'] == 2:
            buy_router_type = buy_router_info.get('type', 'uniswapv2')
            if buy_router_type == 'solidly':
                is_stable_pool = False  # Default value
                factory_address = buy_router_info.get('factory')

                if factory_address:
                    print(f"  - Solidly DEX detected. Determining pool type from factory {factory_address}...")
                    factory_contract = w3.eth.contract(address=factory_address, abi=SOLIDLY_FACTORY_ABI)
                    zero_address = "0x0000000000000000000000000000000000000000"
                    
                    pool_address = zero_address
                    # Try to find a volatile pool first, then a stable one.
                    try:
                        pool_address = resilient_rpc_call(lambda: factory_contract.functions.getPool(BASE_CURRENCY_ADDRESS, TOKEN_ADDRESS, False).call())
                        if pool_address != zero_address:
                            is_stable_pool = False
                            print(f"  - Found volatile pool at address: {pool_address}")
                    except ContractLogicError:
                         print(f"  - INFO: Volatile pool not found or call reverted.")
                         pool_address = zero_address # Ensure it's zero if reverted

                    if pool_address == zero_address:
                        try:
                            pool_address = resilient_rpc_call(lambda: factory_contract.functions.getPool(BASE_CURRENCY_ADDRESS, TOKEN_ADDRESS, True).call())
                            if pool_address != zero_address:
                                is_stable_pool = True
                                print(f"  - Found stable pool at address: {pool_address}")
                        except ContractLogicError:
                            print(f"  - INFO: Stable pool not found or call reverted.")
                    
                    if pool_address == zero_address:
                        print("  - WARNING: Could not find a valid pool for this token pair in the factory. Defaulting to stable=False.")
                else:
                    print("  - WARNING: 'factory' address not found in config for Solidly DEX. Defaulting to stable=False.")
                
                buy_router_contract = w3.eth.contract(address=buy_router_info['address'], abi=SOLIDLY_ROUTER_ABI)
                routes_buy = [(BASE_CURRENCY_ADDRESS, TOKEN_ADDRESS, is_stable_pool)]
                print(f"  - Solidly Route: {routes_buy}")
                amounts_out = resilient_rpc_call(lambda: buy_router_contract.functions.getAmountsOut(amount_in_wei, routes_buy).call({'from': account.address}))
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
                amounts_out = resilient_rpc_call(lambda: buy_router_contract.functions.getAmountsOut(amount_in_wei, path_buy).call())
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
        max_priority_fee = resilient_rpc_call(lambda: w3.eth.max_priority_fee)
        latest_block = resilient_rpc_call(lambda: w3.eth.get_block('latest'))
        base_fee = latest_block['baseFeePerGas']
        max_fee_per_gas = base_fee * 2 + max_priority_fee
        
        buy_payload = {
            'from': account.address, 
            'nonce': resilient_rpc_call(lambda: w3.eth.get_transaction_count(account.address)),
            'maxFeePerGas': max_fee_per_gas, 
            'maxPriorityFeePerGas': max_priority_fee,
            'chainId': resilient_rpc_call(lambda: w3.eth.chain_id)
        }
        print(f"  - Estimating gas with payload: {buy_payload}")
        gas_estimate = resilient_rpc_call(lambda: swap_function.estimate_gas(buy_payload))
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
        TRANSFER_EVENT_TOPIC = w3.keccak(text="Transfer(address,address,uint256)")

        # For V3, parse the Swap event to be more robust. Fallback to Transfer event if it fails.
        if buy_router_info['version'] == 3:
            try:
                # 1. Find the pool address from the base currency transfer log
                pool_address = None
                for log in buy_receipt.logs:
                    if log.address == BASE_CURRENCY_ADDRESS and \
                       len(log.topics) == 3 and \
                       log.topics[0] == TRANSFER_EVENT_TOPIC and \
                       w3.to_checksum_address('0x' + log.topics[1].hex()[-40:]) == account.address:
                        pool_address = w3.to_checksum_address('0x' + log.topics[2].hex()[-40:])
                        print(f"  - Inferred V3 pool address: {pool_address}")
                        break
                
                if pool_address:
                    # 2. Find the Swap event from that pool and parse amount out
                    pool_contract = w3.eth.contract(address=pool_address, abi=UNISWAP_V3_POOL_ABI)
                    swap_events = pool_contract.events.Swap().process_receipt(buy_receipt, errors=DISCARD)
                    for event in swap_events:
                        if event['args']['recipient'] == account.address:
                            amount0, amount1 = event['args']['amount0'], event['args']['amount1']
                            # Amount received is the negative value (token leaving the pool)
                            amount_received = abs(min(amount0, amount1))
                            if amount_received > 0:
                                amount_received_wei = amount_received
                                print(f"  - Parsed amount from Swap event: {amount_received_wei / (10**target_decimals):.4f} tokens.")
                                break
            except Exception as e:
                 print(f"  - Error parsing V3 Swap event from receipt: {e}. Falling back to simple Transfer parsing.")

        # Fallback for V3 or standard logic for V2/other
        if amount_received_wei == 0:
            if buy_router_info['version'] == 3:
                print("  - V3 Swap event parsing failed or found no amount. Trying generic Transfer event parsing...")
            try:
                for log in buy_receipt.logs:
                    if len(log.topics) == 3 and log.topics[0] == TRANSFER_EVENT_TOPIC and log.address == TOKEN_ADDRESS:
                        recipient_address = w3.to_checksum_address('0x' + log.topics[2].hex()[-40:])
                        if recipient_address == account.address:
                            amount_received_wei = w3.codec.decode(['uint256'], log.data)[0]
                            print(f"  - Found transfer of {amount_received_wei / (10**target_decimals):.4f} tokens to wallet.")
                            break
            except Exception as e:
                print(f"  - Error manually parsing transaction receipt for Transfer events: {e}")

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
                is_stable_pool = False
                factory_address = sell_router_info.get('factory')

                if factory_address:
                    print(f"  - Solidly DEX detected. Determining pool type from factory {factory_address}...")
                    factory_contract = w3.eth.contract(address=factory_address, abi=SOLIDLY_FACTORY_ABI)
                    zero_address = "0x0000000000000000000000000000000000000000"
                    
                    pool_address = zero_address
                    try:
                        pool_address = resilient_rpc_call(lambda: factory_contract.functions.getPool(TOKEN_ADDRESS, BASE_CURRENCY_ADDRESS, False).call())
                        if pool_address != zero_address:
                            is_stable_pool = False
                            print(f"  - Found volatile pool at address: {pool_address}")
                    except ContractLogicError:
                        print(f"  - INFO: Volatile pool not found or call reverted.")
                        pool_address = zero_address

                    if pool_address == zero_address:
                        try:
                            pool_address = resilient_rpc_call(lambda: factory_contract.functions.getPool(TOKEN_ADDRESS, BASE_CURRENCY_ADDRESS, True).call())
                            if pool_address != zero_address:
                                is_stable_pool = True
                                print(f"  - Found stable pool at address: {pool_address}")
                        except ContractLogicError:
                            print(f"  - INFO: Stable pool not found or call reverted.")

                    if pool_address == zero_address:
                        print("  - WARNING: Could not find a valid pool for this token pair in the factory. Defaulting to stable=False.")
                else:
                    print("  - WARNING: 'factory' address not found in config for Solidly DEX. Defaulting to stable=False.")

                sell_router_contract = w3.eth.contract(address=sell_router_info['address'], abi=SOLIDLY_ROUTER_ABI)
                routes_sell = [(TOKEN_ADDRESS, BASE_CURRENCY_ADDRESS, is_stable_pool)]
                print(f"  - Solidly Route: {routes_sell}")
                amounts_out_sell = resilient_rpc_call(lambda: sell_router_contract.functions.getAmountsOut(amount_received_wei, routes_sell).call({'from': account.address}))
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
                amounts_out_sell = resilient_rpc_call(lambda: sell_router_contract.functions.getAmountsOut(amount_received_wei, path_sell).call())
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
            'from': account.address, 
            'nonce': resilient_rpc_call(lambda: w3.eth.get_transaction_count(account.address)),
            'maxFeePerGas': max_fee_per_gas, 
            'maxPriorityFeePerGas': max_priority_fee,
            'chainId': resilient_rpc_call(lambda: w3.eth.chain_id)
        }
        print(f"  - Estimating gas with payload: {sell_payload}")
        gas_estimate_sell = resilient_rpc_call(lambda: sell_swap_function.estimate_gas(sell_payload))
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
