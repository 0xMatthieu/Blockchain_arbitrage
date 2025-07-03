import time
from web3.logs import DISCARD
from web3.exceptions import ContractLogicError
from config import (
    w3, account, PRIVATE_KEY, MAX_GAS_LIMIT, DEX_ROUTERS,
    TOKEN_ADDRESS, BASE_CURRENCY_ADDRESS, TRADE_AMOUNT_BASE_TOKEN, SLIPPAGE_TOLERANCE_PERCENT,
    RPC_MAX_RETRIES, RPC_BACKOFF_FACTOR, BOT_WALLET
)
from abi import (
    ERC20_ABI, UNISWAP_V2_ROUTER_ABI, UNISWAP_V3_ROUTER_ABI, SOLIDLY_ROUTER_ABI,
    SOLIDLY_FACTORY_ABI, UNISWAP_V3_POOL_ABI, UNISWAP_V3_FACTORY_ABI, SOLIDLY_PAIR_ABI,
    UNISWAP_V3_QUOTER_ABI, PANCAKE_V3_POOL_ABI
)
from dex_utils import find_router_info

from eth_abi import decode        # already inside web3’s deps
from hexbytes import HexBytes
from web3.exceptions import ContractLogicError

def resilient_rpc_call(callable_func):
    """
    Execute `callable_func` (usually a web3 call) with exponential back-off.

    * If it succeeds → return result immediately.
    * If it reverts **with** payload (Quoter V2 style) → decode & return the
      first uint256 (amountOut).
    * Any other error → retry up to RPC_MAX_RETRIES, doubling the delay.
    """
    # expect the Quoter V2 output layout once decoded
    _QUOTER_V2_RET_TYPES = ["uint256", "uint160", "uint32", "uint256"]

    for i in range(RPC_MAX_RETRIES):
        try:
            return callable_func()

        except ContractLogicError as err:
            # ↪ web3-py puts the tx receipt dict as err.args[0]
            payload = None
            if err.args and isinstance(err.args[0], dict):
                payload = err.args[0].get("data", b"")

            # payload comes back as HexBytes; length > 4 → has real data
            if payload and len(payload) > 4:
                # strip first 4B selector if present
                data_bytes = HexBytes(payload)[4:] if len(payload) % 32 else HexBytes(payload)
                # decode up to the 4 items Quoter V2 returns
                decoded = decode(_QUOTER_V2_RET_TYPES, data_bytes.ljust(32 * 4, b"\0"))
                return decoded[0]                     # amountOut (uint256)

            # truly empty revert → raise without retry
            print(f"  - Contract logic error (revert, no data): {err}")
            raise err

        except Exception as err:
            wait = RPC_BACKOFF_FACTOR * (2 ** i)
            print(f"\n  - RPC call failed: {err}. Retrying in {wait:.2f}s "
                  f"({i + 1}/{RPC_MAX_RETRIES})")
            time.sleep(wait)

    raise Exception(f"RPC call failed after {RPC_MAX_RETRIES} retries.")

def _get_v3_pool_abi(dex_name):
    """Selects the correct V3 pool ABI based on the DEX name."""
    if 'pancake' in dex_name.lower():
        print("  - Using PancakeSwap V3 Pool ABI.")
        return PANCAKE_V3_POOL_ABI
    print("  - Using Uniswap V3 Pool ABI.")
    return UNISWAP_V3_POOL_ABI


def _prepare_solidly_swap(dex_name, router_info, amount_in_wei, token_in, token_out):
    """Prepares a swap transaction for a Solidly-style DEX."""
    factory_address = router_info.get('factory')
    if not factory_address:
        raise ValueError(f"Solidly DEX '{dex_name}' requires a 'factory' address.")

    print(f"  - Solidly DEX detected. Determining pool type and reserves from factory {factory_address}...")
    factory_contract = w3.eth.contract(address=factory_address, abi=SOLIDLY_FACTORY_ABI)
    zero_address = "0x0000000000000000000000000000000000000000"
    
    is_stable_pool = False
    pool_address = resilient_rpc_call(lambda: factory_contract.functions.getPool(token_in, token_out, is_stable_pool).call())
    if pool_address == zero_address:
        is_stable_pool = True
        pool_address = resilient_rpc_call(lambda: factory_contract.functions.getPool(token_in, token_out, is_stable_pool).call())

    if pool_address == zero_address:
        raise ValueError(f"Could not find any valid pool for this pair on Solidly DEX {dex_name}.")
    
    print(f"  - Found pool: {'Stable' if is_stable_pool else 'Volatile'} at {pool_address}")

    pair_contract = w3.eth.contract(address=pool_address, abi=SOLIDLY_PAIR_ABI)
    reserves = resilient_rpc_call(lambda: pair_contract.functions.getReserves().call())
    if reserves[0] == 0 or reserves[1] == 0:
        raise ValueError(f"Pool {pool_address} on {dex_name} has zero reserves. Skipping trade.")
    print(f"  - Pool reserves are non-zero. Reserve0: {reserves[0]}, Reserve1: {reserves[1]}")

    router_contract = w3.eth.contract(address=router_info['address'], abi=SOLIDLY_ROUTER_ABI)
    routes = [(token_in, token_out, is_stable_pool, factory_address)]
    print(f"  - Solidly Route: {routes}")
    
    amounts_out = resilient_rpc_call(lambda: router_contract.functions.getAmountsOut(amount_in_wei, routes).call({'from': account.address}))
    amount_out_min_wei = int(amounts_out[-1] * (1 - SLIPPAGE_TOLERANCE_PERCENT / 100.0))

    print(f"  - Solidly Min Amount Out (from quote): {amount_out_min_wei}")
    swap_function = router_contract.functions.swapExactTokensForTokens(
        amount_in_wei, amount_out_min_wei, routes, account.address, int(time.time()) + 300
    )
    return swap_function, amount_out_min_wei


def _prepare_uniswap_v2_swap(router_info, amount_in_wei, path):
    """Prepares a swap transaction for a Uniswap V2-style DEX."""
    router_contract = w3.eth.contract(address=router_info['address'], abi=UNISWAP_V2_ROUTER_ABI)
    print(f"  - V2 Path: {path}")
    amounts_out = resilient_rpc_call(lambda: router_contract.functions.getAmountsOut(amount_in_wei, path).call())
    print(f"  - V2 getAmountsOut result: {amounts_out}")
    amount_out_min_wei = int(amounts_out[-1] * (1 - SLIPPAGE_TOLERANCE_PERCENT / 100.0))
    print(f"  - V2 Min Amount Out (wei): {amount_out_min_wei}")
    swap_function = router_contract.functions.swapExactTokensForTokens(
        amount_in_wei, amount_out_min_wei, path, account.address, int(time.time()) + 300
    )
    return swap_function, amount_out_min_wei

def _prepare_uniswap_v3_swap(dex_name, router_info, amount_in_wei, token_in, token_out):
    """Prepares a swap transaction for a Uniswap V3-style DEX."""
    factory_address = router_info.get("factory")
    if not factory_address:
        raise ValueError(f"V3 DEX '{dex_name}' requires a 'factory' address in config.")

    print(f"  - V3 DEX detected. Querying factory {factory_address} for a valid pool...")
    factory_contract = w3.eth.contract(address=factory_address, abi=UNISWAP_V3_FACTORY_ABI)

    FEE_TIERS = [100, 500, 2500, 3000, 10000]
    chosen_fee, pool_address = None, None
    for fee in FEE_TIERS:
        pool_addr_candidate = resilient_rpc_call(
            lambda: factory_contract.functions.getPool(token_in, token_out, fee).call()
        )
        if int(pool_addr_candidate, 16):
            chosen_fee, pool_address = fee, pool_addr_candidate
            print(f"  - Found valid pool with fee tier {fee} bps at address {pool_address}")
            break
    if chosen_fee is None:
        raise ValueError(f"No V3 pool found for pair on {dex_name}")

    # ---------- pool-init check ----------
    v3_pool_abi = _get_v3_pool_abi(dex_name)
    pool_contract = w3.eth.contract(pool_address, abi=v3_pool_abi)
    sqrt_price_x96, *_ = resilient_rpc_call(lambda: pool_contract.functions.slot0().call())
    if sqrt_price_x96 == 0:
        raise ValueError("Pool exists but has zero liquidity")

    # ---------- quote via Quoter V2 ----------
    quoter_address = router_info.get("quoter")
    if not quoter_address:
        raise ValueError(f"V3 DEX '{dex_name}' requires a 'quoter' address in config.")
    print(f"  - Using Quoter at {quoter_address} to get quote…")

    quoter_contract = w3.eth.contract(quoter_address, abi=UNISWAP_V3_QUOTER_ABI)
    params_tuple = (token_in, token_out, chosen_fee, amount_in_wei, 0)
    quote_tuple = resilient_rpc_call(
        lambda: quoter_contract.functions.quoteExactInputSingle(params_tuple).call(
            {"from": account.address, "gas": 500_000}
        )
    )
    quoted_amount_out_wei = int(quote_tuple[0])
    amount_out_min_wei = int(quoted_amount_out_wei * (1 - SLIPPAGE_TOLERANCE_PERCENT / 100.0))
    print(f"  - V3 Min Amount Out (from quote): {amount_out_min_wei}")

    # ---------- build swap ----------
    router_contract = w3.eth.contract(router_info["address"], abi=UNISWAP_V3_ROUTER_ABI)
    swap_params = {
        "tokenIn": token_in,
        "tokenOut": token_out,
        "fee": chosen_fee,
        "recipient": account.address,
        "deadline": int(time.time()) + 300,
        "amountIn": amount_in_wei,
        "amountOutMinimum": amount_out_min_wei,
        "sqrtPriceLimitX96": 0,
    }
    print(f"  - V3 Swap Params: {swap_params}")
    swap_function = router_contract.functions.exactInputSingle(swap_params)
    return swap_function, amount_out_min_wei

def _parse_receipt_for_amount_out(receipt, router_info, dex_name, target_token_address, target_decimals):
    """Parses a transaction receipt to find the amount of tokens received."""
    amount_received_wei = 0
    TRANSFER_EVENT_TOPIC = w3.keccak(text="Transfer(address,address,uint256)")

    # V3: Try parsing the Swap event first for accuracy
    if router_info['version'] == 3:
        try:
            pool_address = None
            for log in receipt.logs:
                if log.address == BASE_CURRENCY_ADDRESS and \
                   len(log.topics) == 3 and \
                   log.topics[0] == TRANSFER_EVENT_TOPIC and \
                   w3.to_checksum_address('0x' + log.topics[1].hex()[-40:]) == account.address:
                    pool_address = w3.to_checksum_address('0x' + log.topics[2].hex()[-40:])
                    print(f"  - Inferred V3 pool address: {pool_address}")
                    break
            
            if pool_address:
                v3_pool_abi = _get_v3_pool_abi(dex_name)
                pool_contract = w3.eth.contract(address=pool_address, abi=v3_pool_abi)
                swap_events = pool_contract.events.Swap().process_receipt(receipt, errors=DISCARD)
                for event in swap_events:
                    if event['args']['recipient'] == account.address:
                        amount0, amount1 = event['args']['amount0'], event['args']['amount1']
                        amount_received = abs(min(amount0, amount1))
                        if amount_received > 0:
                            amount_received_wei = amount_received
                            print(f"  - Parsed amount from Swap event: {amount_received_wei / (10**target_decimals):.4f} tokens.")
                            break
        except Exception as e:
             print(f"  - Error parsing V3 Swap event from receipt: {e}. Falling back to simple Transfer parsing.")

    # Fallback for V3 or standard logic for V2/other
    if amount_received_wei == 0:
        if router_info['version'] == 3:
            print("  - V3 Swap event parsing failed or found no amount. Trying generic Transfer event parsing...")
        try:
            for log in receipt.logs:
                if len(log.topics) == 3 and log.topics[0] == TRANSFER_EVENT_TOPIC and log.address == target_token_address:
                    recipient_address = w3.to_checksum_address('0x' + log.topics[2].hex()[-40:])
                    if recipient_address == account.address:
                        amount_received_wei = w3.codec.decode(['uint256'], log.data)[0]
                        print(f"  - Found transfer of {amount_received_wei / (10**target_decimals):.4f} tokens to wallet.")
                        break
        except Exception as e:
            print(f"  - Error manually parsing transaction receipt for Transfer events: {e}")

    return amount_received_wei


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
        base_token_contract = w3.eth.contract(address=BASE_CURRENCY_ADDRESS, abi=ERC20_ABI)
        base_decimals = resilient_rpc_call(lambda: base_token_contract.functions.decimals().call())
        amount_in_wei = int(TRADE_AMOUNT_BASE_TOKEN * (10**base_decimals))
        wallet_balance_wei = resilient_rpc_call(lambda: base_token_contract.functions.balanceOf(account.address).call())

        if wallet_balance_wei < amount_in_wei:
            print(f"!!! TRADE SKIPPED: Insufficient balance. Have {wallet_balance_wei / (10**base_decimals):.6f}, need {TRADE_AMOUNT_BASE_TOKEN}.")
            return
        print(f"  - Wallet balance check passed. Have {wallet_balance_wei / (10**base_decimals):.6f}, need {TRADE_AMOUNT_BASE_TOKEN}.")

        trade_amount_usd = TRADE_AMOUNT_BASE_TOKEN * buy_pool.get('base_currency_price_usd', 0)
        if trade_amount_usd == 0:
            print("!!! TRADE SKIPPED: Could not determine USD value of trade amount.")
            return

        LIQUIDITY_IMPACT_THRESHOLD = 0.1 
        if trade_amount_usd > buy_pool['liq_usd'] * LIQUIDITY_IMPACT_THRESHOLD or \
           trade_amount_usd > sell_pool['liq_usd'] * LIQUIDITY_IMPACT_THRESHOLD:
            print(f"!!! TRADE SKIPPED: Trade size (${trade_amount_usd:,.2f}) is too large for pool liquidity.")
            return
        print(f"  - Liquidity check passed. Trade size ${trade_amount_usd:,.2f} is reasonable for both pools.")

        # --- 1. BUY TRANSACTION ---
        print(f"Step 1: Buying {TOKEN_ADDRESS} on {buy_dex_name} (v{buy_router_info['version']})...")
        target_token_contract = w3.eth.contract(address=TOKEN_ADDRESS, abi=ERC20_ABI)
        target_decimals = resilient_rpc_call(lambda: target_token_contract.functions.decimals().call())

        if buy_router_info['version'] == 2:
            router_type = buy_router_info.get('type', 'uniswapv2')
            if router_type == 'solidly':
                swap_function, _ = _prepare_solidly_swap(buy_dex_name, buy_router_info, amount_in_wei, BASE_CURRENCY_ADDRESS, TOKEN_ADDRESS)
            else: # Default to uniswapv2
                swap_function, _ = _prepare_uniswap_v2_swap(buy_router_info, amount_in_wei, [BASE_CURRENCY_ADDRESS, TOKEN_ADDRESS])
        elif buy_router_info['version'] == 3:
            swap_function, _ = _prepare_uniswap_v3_swap(buy_dex_name, buy_router_info, amount_in_wei, BASE_CURRENCY_ADDRESS, TOKEN_ADDRESS)
        else:
            raise NotImplementedError(f"DEX version {buy_router_info['version']} is not supported for buys.")

        print("  - Building buy transaction...")
        max_priority_fee = resilient_rpc_call(lambda: w3.eth.max_priority_fee)
        latest_block = resilient_rpc_call(lambda: w3.eth.get_block('latest'))
        base_fee = latest_block['baseFeePerGas']
        max_fee_per_gas = base_fee * 2 + max_priority_fee
        
        buy_payload = {
            'from': account.address, 
            'nonce': resilient_rpc_call(lambda: w3.eth.get_transaction_count(account.address)),
            'maxFeePerGas': max_fee_per_gas, 'maxPriorityFeePerGas': max_priority_fee,
            'chainId': resilient_rpc_call(lambda: w3.eth.chain_id)
        }
        gas_estimate = resilient_rpc_call(lambda: swap_function.estimate_gas(buy_payload))
        buy_payload['gas'] = min(int(gas_estimate * 1.2), MAX_GAS_LIMIT)
        buy_txn = swap_function.build_transaction(buy_payload)
        
        signed_buy_txn = w3.eth.account.sign_transaction(buy_txn, PRIVATE_KEY)
        buy_tx_hash = w3.eth.send_raw_transaction(signed_buy_txn.raw_transaction)
        print(f"  - Buy Tx sent: {buy_tx_hash.hex()}. Waiting for receipt...")
        buy_receipt = w3.eth.wait_for_transaction_receipt(buy_tx_hash, timeout=120)

        if buy_receipt['status'] == 0:
            print("  - BUY TRANSACTION FAILED (reverted). Aborting arbitrage.")
            return

        print("  - Buy transaction successful! Parsing receipt...")
        amount_received_wei = _parse_receipt_for_amount_out(buy_receipt, buy_router_info, buy_dex_name, TOKEN_ADDRESS, target_decimals)

        if amount_received_wei == 0:
            print("  - CRITICAL: Could not determine received token amount from receipt. Aborting sell.")
            return
        
        # --- 2. SELL TRANSACTION ---
        print(f"Step 2: Selling {amount_received_wei / (10**target_decimals)} of {TOKEN_ADDRESS} on {sell_dex_name} (v{sell_router_info['version']})...")
        
        if sell_router_info['version'] == 2:
            router_type = sell_router_info.get('type', 'uniswapv2')
            if router_type == 'solidly':
                sell_swap_function, _ = _prepare_solidly_swap(sell_dex_name, sell_router_info, amount_received_wei, TOKEN_ADDRESS, BASE_CURRENCY_ADDRESS)
            else: # Default to uniswapv2
                sell_swap_function, _ = _prepare_uniswap_v2_swap(sell_router_info, amount_received_wei, [TOKEN_ADDRESS, BASE_CURRENCY_ADDRESS])
        elif sell_router_info['version'] == 3:
            sell_swap_function, _ = _prepare_uniswap_v3_swap(sell_dex_name, sell_router_info, amount_received_wei, TOKEN_ADDRESS, BASE_CURRENCY_ADDRESS)
        else:
            raise NotImplementedError(f"DEX version {sell_router_info['version']} is not supported for sells.")

        print("  - Building sell transaction...")
        sell_payload = {
            'from': account.address, 
            'nonce': resilient_rpc_call(lambda: w3.eth.get_transaction_count(account.address)),
            'maxFeePerGas': max_fee_per_gas, 'maxPriorityFeePerGas': max_priority_fee,
            'chainId': resilient_rpc_call(lambda: w3.eth.chain_id)
        }
        gas_estimate_sell = resilient_rpc_call(lambda: sell_swap_function.estimate_gas(sell_payload))
        sell_payload['gas'] = min(int(gas_estimate_sell * 1.2), MAX_GAS_LIMIT)
        sell_txn = sell_swap_function.build_transaction(sell_payload)

        signed_sell_txn = w3.eth.account.sign_transaction(sell_txn, PRIVATE_KEY)
        sell_tx_hash = w3.eth.send_raw_transaction(signed_sell_txn.raw_transaction)
        print(f"  - Sell Tx sent: {sell_tx_hash.hex()}. Waiting for receipt...")
        sell_receipt = w3.eth.wait_for_transaction_receipt(sell_tx_hash, timeout=120)

        if sell_receipt['status'] == 0:
            print("  - SELL TRANSACTION FAILED. You are now holding the bought tokens.")
        else:
            print("  - Sell transaction successful! Arbitrage attempt complete.")

    except Exception as e:
        print(f"An unexpected error occurred during trade execution: {e}")
