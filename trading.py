import time
import logging
from web3.logs import DISCARD
from config import (
    w3, account, PRIVATE_KEY, MAX_GAS_LIMIT, DEX_ROUTERS,
    BASE_CURRENCY_ADDRESS, TRADE_AMOUNT_BASE_TOKEN
)
from abi import (
    ERC20_ABI, UNISWAP_V2_ROUTER_ABI, UNISWAP_V3_ROUTER_ABI, SOLIDLY_ROUTER_ABI,
    SOLIDLY_FACTORY_ABI, UNISWAP_V3_POOL_ABI, UNISWAP_V3_FACTORY_ABI, SOLIDLY_PAIR_ABI,
    UNISWAP_V3_QUOTER_ABI, ONEINCH_V6_ROUTER_ABI,
    ALIENBASE_V2_ROUTER_ABI, BALANCER_V2_ROUTER_ABI, BALANCER_POOL_ABI,
    PANCAKE_V3_FACTORY_ABI, PANCAKE_V3_POOL_ABI, SWAAP_ROUTER_ABI, SWAAP_POOL_ABI
)
from dex_utils import find_router_info

def _prepare_1inch_swap(router_info: dict, amount_in_wei: int, token_in: str, token_out: str):
    """
    Prepares a swap transaction for the 1inch Aggregation router using on-chain calls.
    Sets minReturn to 0 for maximum speed, skipping quotes.
    """
    logging.info("  - Preparing 1inch on-chain swap (fast mode)...")
    router = w3.eth.contract(address=router_info['address'], abi=ONEINCH_V6_ROUTER_ABI)

    # 1inch default executor for Base network.
    EXECUTOR_ADDR = "0x1111111111111111111111111111111111111111"
    
    # Empty data, so the executor tries to find the best route.
    executor_data = b""

    amount_out_min_wei = 0
    logging.info(f"  - 1inch Min Amount Out: 0 (fast mode)")

    final_desc = (
        token_in,           # srcToken
        token_out,          # dstToken
        account.address,    # srcReceiver (will be ignored by router, but needs to be valid addr)
        account.address,    # dstReceiver
        amount_in_wei,      # amount
        amount_out_min_wei, # minReturn
        0,                  # flags (0 = simple)
        b""                 # permit (none)
    )

    swap_function = router.functions.swap(EXECUTOR_ADDR, final_desc, executor_data)

    return swap_function, amount_out_min_wei

def _prepare_solidly_swap(
    dex_name: str,
    router_info: dict,
    amount_in_wei: int,
    token_in: str,
    token_out: str,
    pair_address: str = None,
    *,
    safety_slippage_bps: int = 300  # extra 3 % head-room on top of your global setting
):
    """
    Build a Solidly-style swap call.
    This version skips quotes and assumes a volatile pool for speed.
    """
    factory = router_info.get("factory")
    if not factory:
        raise ValueError(f"{dex_name}: no factory address in config")

    logging.info(f"  - Solidly router detected ({dex_name}) (fast mode).")
    
    if not pair_address or not w3.eth.get_code(pair_address):
        raise ValueError(f"{dex_name}: No valid pair_address provided for solidly swap.")
    
    logging.info(f"  - Using provided pool address: {pair_address}")
    
    # For speed, we assume the pool is volatile. This is the most common case.
    # The transaction may fail if the pool is stable.
    final_is_stable = False
    min_out = 0

    router = w3.eth.contract(router_info["address"], abi=SOLIDLY_ROUTER_ABI)
    
    def _build_swap_fn(out_min: int):
        final_routes = [(token_in, token_out, final_is_stable, factory)]
        return router.functions.swapExactTokensForTokens(
            amount_in_wei,
            out_min,
            final_routes,
            account.address,
            int(time.time()) + 300,
        )

    swap_fn = _build_swap_fn(min_out)
    logging.info(f"  - MinOut = {min_out} (fast mode)")
    return swap_fn, min_out

def _prepare_alien_base_swap(
        dex_name: str,
        router_info: dict,
        amount_in_wei: int,
        token_in: str,
        token_out: str,
        pair_address: str = None,
        fee_bps_hint: int = None
    ):
    """
    Prepares an Alien Base (Uniswap-V2 style) swap.
    It does not use a quoter and sets amountOutMinimum to 0 for speed.
    """
    if pair_address:
        logging.info(f"  - Alien Base V2 Using provided pool address: {pair_address}")

    path = [token_in, token_out]
    router_contract = w3.eth.contract(address=router_info['address'], abi=ALIENBASE_V2_ROUTER_ABI)
    
    logging.info(f"  - Alien Base V2 Path: {path} (fast mode)")
    amount_out_min_wei = 0
    logging.info(f"  - Alien Base V2 Min Amount Out (wei): {amount_out_min_wei} (fast mode)")
    
    swap_function = router_contract.functions.swapExactTokensForTokens(
        amount_in_wei, amount_out_min_wei, path, account.address, int(time.time()) + 300
    )
    return swap_function, amount_out_min_wei

def _prepare_balancer_v2_swap(
        router_info: dict,
        amount_in_wei: int,
        token_in: str,
        token_out: str,
        pair_address: str
    ):
    """Prepares a swap for a Balancer V2-style DEX (e.g., Swaap)."""
    if not pair_address:
        raise ValueError("Balancer V2 swaps require a pair_address (pool address).")

    logging.info(f"  - Balancer V2 router detected. Using pool: {pair_address}")

    # Get the poolId from the pool contract
    pool_contract = w3.eth.contract(address=pair_address, abi=BALANCER_POOL_ABI)
    pool_id = pool_contract.functions.getPoolId().call()

    router_contract = w3.eth.contract(address=router_info['address'], abi=BALANCER_V2_ROUTER_ABI)
    
    # For a GIVEN_IN swap, we specify the exact input amount.
    swap_kind = 0  # 0 for GIVEN_IN

    single_swap = (
        pool_id,
        swap_kind,
        token_in,
        token_out,
        amount_in_wei,
        b''  # userData
    )

    # Funds are managed by the wallet, not the Vault's internal balance.
    funds = (
        account.address,  # sender
        False,            # fromInternalBalance
        account.address,  # recipient
        False,            # toInternalBalance
    )

    amount_out_min_wei = 0  # For fast swaps, we don't check for a minimum output.
    deadline = int(time.time()) + 300
    
    logging.info(f"  - Balancer V2 Min Amount Out (wei): {amount_out_min_wei} (fast mode)")
    
    swap_function = router_contract.functions.swap(
        single_swap, funds, amount_out_min_wei, deadline
    )
    
    return swap_function, amount_out_min_wei


def _prepare_swaap_swap(
        router_info: dict,
        amount_in_wei: int,
        token_in: str,
        token_out: str,
        pair_address: str
    ):
    """Prepares a swap for a Swaap DEX, which uses the Balancer V2 router."""
    if not pair_address:
        raise ValueError("Swaap swaps require a pair_address (pool address).")

    logging.info(f"  - Swaap router detected. Using pool: {pair_address}")

    # Get the poolId from the Swaap pool contract
    pool_contract = w3.eth.contract(address=pair_address, abi=SWAAP_POOL_ABI)
    pool_id = pool_contract.functions.getPoolId().call()

    # Swaap uses the Balancer V2 router.
    router_contract = w3.eth.contract(address=router_info['address'], abi=BALANCER_V2_ROUTER_ABI)
    
    # For a GIVEN_IN swap, we specify the exact input amount.
    swap_kind = 0  # 0 for GIVEN_IN

    single_swap = (
        pool_id,
        swap_kind,
        token_in,
        token_out,
        amount_in_wei,
        b''  # userData
    )

    # Funds are managed by the wallet, not the Vault's internal balance.
    funds = (
        account.address,  # sender
        False,            # fromInternalBalance
        account.address,  # recipient
        False,            # toInternalBalance
    )

    amount_out_min_wei = 0  # For fast swaps, we don't check for a minimum output.
    deadline = int(time.time()) + 300
    
    logging.info(f"  - Swaap Min Amount Out (wei): {amount_out_min_wei} (fast mode)")
    
    swap_function = router_contract.functions.swap(
        single_swap, funds, amount_out_min_wei, deadline
    )
    
    return swap_function, amount_out_min_wei


def _prepare_uniswap_v2_swap(router_info, amount_in_wei, path, pair_address: str = None):
    """Prepares a swap transaction for a Uniswap V2-style DEX, skipping quotes for speed."""
    if pair_address:
        logging.info(f"  - V2 Using provided pool address: {pair_address}")
    router_contract = w3.eth.contract(address=router_info['address'], abi=UNISWAP_V2_ROUTER_ABI)
    logging.info(f"  - V2 Path: {path} (fast mode)")
    amount_out_min_wei = 0
    logging.info(f"  - V2 Min Amount Out (wei): {amount_out_min_wei} (fast mode)")
    swap_function = router_contract.functions.swapExactTokensForTokens(
        amount_in_wei, amount_out_min_wei, path, account.address, int(time.time()) + 300
    )
    return swap_function, amount_out_min_wei

def _prepare_uniswap_v3_swap(
        dex_name: str,
        router_info: dict,
        amount_in_wei: int,
        token_in: str,
        token_out: str,
        pair_address: str = None,
        fee_bps_hint: int = None
    ):
    """
    Prepares a Uniswap-V3 style swap and **never** dies on
    `quoteExactInputSingle` “execution reverted, no data”.
    """
    router_type = router_info.get('type')
    if router_type == 'pancakeswap_v3':
        factory_abi = PANCAKE_V3_FACTORY_ABI
        pool_abi = PANCAKE_V3_POOL_ABI
        logging.info(f"  - Using PancakeSwap V3 ABIs for {dex_name}.")
    else: # Default to Uniswap V3
        factory_abi = UNISWAP_V3_FACTORY_ABI
        pool_abi = UNISWAP_V3_POOL_ABI
        if router_type not in ['uniswap_v3', None]:
            logging.info(f"  - Using default Uniswap V3 ABIs for '{dex_name}' (type: {router_type}).")

    factory_address = router_info.get("factory")
    if not factory_address:
        raise ValueError(f"V3 DEX '{dex_name}' requires a 'factory' address")

    logging.info(f"  - V3 DEX detected. Querying factory {factory_address} …")
    factory = w3.eth.contract(factory_address, abi=factory_abi)

    # ------------------------------------------------------------------ #
    # ① find a pool that actually exists (code size > 0)
    # ------------------------------------------------------------------ #
    chosen_fee, pool_address = None, None
    
    if pair_address and w3.eth.get_code(pair_address):
        logging.info(f"  - Using provided pool address: {pair_address}")
        pool_contract = w3.eth.contract(address=pair_address, abi=pool_abi)
        try:
            pool_fee = pool_contract.functions.fee().call()
            if fee_bps_hint and fee_bps_hint != pool_fee:
                logging.warning(f"  - Fee mismatch! DexScreener: {fee_bps_hint}, On-chain: {pool_fee}. Trusting on-chain fee.")
            chosen_fee = pool_fee
            pool_address = pair_address
            logging.info(f"  - Successfully confirmed pool at fee tier {chosen_fee} bps.")
        except Exception as e:
            logging.warning(f"  - Could not confirm fee for provided pool {pair_address}. Falling back to factory search. Error: {e}")

    if not pool_address:
        logging.info(f"  - No valid pool provided. Querying factory {factory_address} for a liquid pool...")
        FEE_TIERS = [500, 3000, 10000, 2500, 100]
        if fee_bps_hint and fee_bps_hint in FEE_TIERS:
            FEE_TIERS.insert(0, FEE_TIERS.pop(FEE_TIERS.index(fee_bps_hint)))
            logging.info(f"  - Prioritizing fee tier {fee_bps_hint} from DexScreener hint.")
        zero = "0x" + "00" * 20
        for fee in FEE_TIERS:
            addr = factory.functions.getPool(token_in, token_out, fee).call()
            if addr and addr != zero and w3.eth.get_code(addr):
                # Found a potential pool, now check its liquidity before selecting it.
                temp_pool = w3.eth.contract(address=addr, abi=pool_abi)
                try:
                    liquidity = temp_pool.functions.liquidity().call()
                    if liquidity > 0:
                        chosen_fee, pool_address = fee, addr
                        logging.info(f"  - Pool {addr} at {fee} bps with liquidity {liquidity} selected")
                        break  # Found a valid, liquid pool. Exit the loop.
                    else:
                        logging.info(f"  - Pool {addr} at {fee} bps found but has zero liquidity. Skipping.")
                except Exception as e:
                    logging.warning(f"  - Could not check liquidity for pool {addr}: {e}. Skipping.")

    if not pool_address:
        raise ValueError(f"No live, liquid V3 pool for pair on {dex_name}")

    # ------------------------------------------------------------------ #
    # ② sanity-check pool status (slot0 & liquidity)
    # ------------------------------------------------------------------ #
    pool = w3.eth.contract(pool_address, abi=pool_abi)
    try:
        sqrt_price_x96, *_ = pool.functions.slot0().call()
    except Exception as e:
        # Some V3 forks (like PancakeSwap) might have a slightly different slot0 return signature.
        # If we can't decode it, we can't proceed with this pool.
        logging.warning(f"  - Could not decode slot0 for pool {pool_address} on {dex_name}. It might be an incompatible V3 fork. Error: {e}")
        raise ValueError(f"Could not decode slot0 for pool {pool_address}")

    if sqrt_price_x96 == 0:
        raise ValueError("Pool exists but never initialised (sqrtPriceX96 == 0)")

    try:
        liquidity = pool.functions.liquidity().call()
    except Exception as e:
        logging.warning(f"  - Could not read liquidity for pool {pool_address} on {dex_name}. It might be an incompatible V3 fork. Error: {e}")
        raise ValueError(f"Could not read liquidity for pool {pool_address}")

    if liquidity == 0:
        raise ValueError("Pool initialised but has zero active liquidity")

    logging.info(f"  - Pool initialised with {liquidity} liquidity")

    amount_out_min = 0

    router = w3.eth.contract(router_info["address"], abi=UNISWAP_V3_ROUTER_ABI)

    def _build_v3_swap_fn(min_out):
        swap_params = {
            "tokenIn": token_in,
            "tokenOut": token_out,
            "fee": chosen_fee,
            "recipient": account.address,
            "amountIn": amount_in_wei,
            "amountOutMinimum": min_out,
            "sqrtPriceLimitX96": 0,
        }
        return router.functions.exactInputSingle(swap_params)

    logging.info(f"  - Prepare swap")
    swap_fn = _build_v3_swap_fn(amount_out_min)

    # Gas estimation checks removed by user request.
    return swap_fn, amount_out_min


def _wait_for_balance_change(token_contract, owner_address, initial_balance, retries=5, delay=1.0):
    """
    Waits for a token balance to change after a transaction.
    Polls the balance with a delay to account for RPC node sync time.
    """
    for i in range(retries):
        new_balance = token_contract.functions.balanceOf(owner_address).call()
        if new_balance != initial_balance:
            logging.info(f"  - Balance change detected on attempt {i+1}.")
            return new_balance
        if i < retries - 1:
            logging.info(f"  - Balance unchanged, retrying in {delay}s... ({i+1}/{retries})")
            time.sleep(delay)
    logging.warning(f"  - Balance did not change after {retries} retries.")
    return token_contract.functions.balanceOf(owner_address).call() # return last known balance


def execute_trade(buy_pool, sell_pool, spread, token_address, token_info):
    token_name = token_info.get('name', token_address)
    logging.info("\n" + "!"*60)
    logging.warning(f"!!! REAL TRADE TRIGGERED on {token_name} - Spread: {spread:.2f}% !!!")
    logging.info("!"*60)

    if not all([account, BASE_CURRENCY_ADDRESS, TRADE_AMOUNT_BASE_TOKEN > 0]):
        logging.warning("!!! TRADING SKIPPED: Wallet or trading parameters not configured correctly.")
        return

    buy_dex_name = buy_pool['dex']
    sell_dex_name = sell_pool['dex']
    buy_router_info = find_router_info(buy_dex_name, DEX_ROUTERS)
    sell_router_info = find_router_info(sell_dex_name, DEX_ROUTERS)

    if not buy_router_info or not sell_router_info:
        logging.warning(f"!!! TRADING SKIPPED: Router info for '{buy_dex_name}' or '{sell_dex_name}' not found in .env")
        logging.warning(f"!!! TRADING SKIPPED: Debug: Router pair address is '{buy_pool['pairAddress']}' and '{sell_pool['pairAddress']}'")
        return

    try:
        # --- Pre-flight checks ---
        logging.info("  - Performing pre-flight checks...")
        base_token_contract = w3.eth.contract(address=BASE_CURRENCY_ADDRESS, abi=ERC20_ABI)
        base_decimals = base_token_contract.functions.decimals().call()
        amount_in_wei = int(TRADE_AMOUNT_BASE_TOKEN * (10**base_decimals))
        wallet_balance_wei = base_token_contract.functions.balanceOf(account.address).call()

        if wallet_balance_wei < amount_in_wei:
            logging.warning(f"!!! TRADE SKIPPED: Insufficient balance. Have {wallet_balance_wei / (10**base_decimals):.6f}, need {TRADE_AMOUNT_BASE_TOKEN}.")
            return
        logging.info(f"  - Wallet balance check passed. Have {wallet_balance_wei / (10**base_decimals):.6f}, need {TRADE_AMOUNT_BASE_TOKEN}.")

        trade_amount_usd = TRADE_AMOUNT_BASE_TOKEN * buy_pool.get('base_currency_price_usd', 0)
        if trade_amount_usd == 0:
            logging.warning("!!! TRADE SKIPPED: Could not determine USD value of trade amount.")
            return

        LIQUIDITY_IMPACT_THRESHOLD = 0.1 
        if trade_amount_usd > buy_pool['liq_usd'] * LIQUIDITY_IMPACT_THRESHOLD or \
           trade_amount_usd > sell_pool['liq_usd'] * LIQUIDITY_IMPACT_THRESHOLD:
            logging.warning(f"!!! TRADE SKIPPED: Trade size (${trade_amount_usd:,.2f}) is too large for pool liquidity.")
            return
        logging.info(f"  - Liquidity check passed. Trade size ${trade_amount_usd:,.2f} is reasonable for both pools.")

        # --- 1. BUY TRANSACTION ---
        logging.info(f"Step 1: Buying {token_name} ({token_address}) on {buy_dex_name} (v{buy_router_info['version']})...")
        target_token_contract = w3.eth.contract(address=token_address, abi=ERC20_ABI)
        target_decimals = target_token_contract.functions.decimals().call()
        initial_target_token_balance = target_token_contract.functions.balanceOf(account.address).call()
        logging.info(f"  - Initial balance of {token_name}: {initial_target_token_balance / (10**target_decimals):.6f}")

        # --- Transaction Preparation ---
        chain_id = w3.eth.chain_id
        router_type = buy_router_info.get('type', 'uniswap_v2')
        buy_txn = None

        max_priority_fee = w3.eth.max_priority_fee
        latest_block = w3.eth.get_block('latest')
        base_fee = latest_block['baseFeePerGas']
        max_fee_per_gas = base_fee * 2 + max_priority_fee
        nonce = w3.eth.get_transaction_count(account.address)

        swap_function = None
        if router_type == '1inch':
            swap_function, _ = _prepare_1inch_swap(buy_router_info, amount_in_wei, BASE_CURRENCY_ADDRESS, token_address)
        elif router_type == 'alienbase':
            swap_function, _ = _prepare_alien_base_swap(buy_dex_name, buy_router_info, amount_in_wei, BASE_CURRENCY_ADDRESS, token_address, pair_address=buy_pool['pairAddress'], fee_bps_hint=buy_pool.get('feeBps'))
        elif buy_router_info['version'] == 2:
            if router_type == 'solidly':
                swap_function, _ = _prepare_solidly_swap(buy_dex_name, buy_router_info, amount_in_wei, BASE_CURRENCY_ADDRESS, token_address, pair_address=buy_pool['pairAddress'])
            elif router_type == 'balancer_v2':
                swap_function, _ = _prepare_balancer_v2_swap(buy_router_info, amount_in_wei, BASE_CURRENCY_ADDRESS, token_address, pair_address=buy_pool['pairAddress'])
            elif router_type == 'swaap_v2':
                swap_function, _ = _prepare_swaap_swap(buy_router_info, amount_in_wei, BASE_CURRENCY_ADDRESS, token_address, pair_address=buy_pool['pairAddress'])
            else: # Default to uniswapv2
                swap_function, _ = _prepare_uniswap_v2_swap(buy_router_info, amount_in_wei, [BASE_CURRENCY_ADDRESS, token_address], pair_address=buy_pool['pairAddress'])
        elif buy_router_info['version'] == 3:
            swap_function, _ = _prepare_uniswap_v3_swap(buy_dex_name, buy_router_info, amount_in_wei, BASE_CURRENCY_ADDRESS, token_address, pair_address=buy_pool['pairAddress'], fee_bps_hint=buy_pool.get('feeBps'))
        else:
            raise NotImplementedError(f"DEX version {buy_router_info['version']} or type '{router_type}' is not supported for buys.")
        
        logging.info("  - Building buy transaction...")
        buy_payload = {
            'from': account.address, 'nonce': nonce,
            'maxFeePerGas': max_fee_per_gas, 'maxPriorityFeePerGas': max_priority_fee,
            'chainId': chain_id
        }
        # Gas estimation removed by user request. Using MAX_GAS_LIMIT.
        buy_payload['gas'] = MAX_GAS_LIMIT
        buy_txn = swap_function.build_transaction(buy_payload)

        if not buy_txn:
            raise Exception("Failed to build buy transaction.")

        signed_buy_txn = w3.eth.account.sign_transaction(buy_txn, PRIVATE_KEY)
        buy_tx_hash = w3.eth.send_raw_transaction(signed_buy_txn.raw_transaction)
        logging.info(f"  - Buy Tx sent: {buy_tx_hash.hex()}. Waiting for receipt...")
        buy_receipt = w3.eth.wait_for_transaction_receipt(buy_tx_hash, timeout=120)

        if buy_receipt['status'] == 0:
            logging.error("  - BUY TRANSACTION FAILED (reverted). Aborting arbitrage.")
            return

        logging.info("  - Buy transaction successful! Checking for balance change...")
        new_target_token_balance = _wait_for_balance_change(
            target_token_contract, account.address, initial_target_token_balance
        )
        amount_received_wei = new_target_token_balance - initial_target_token_balance
        logging.info(f"  - New balance of {token_name}: {new_target_token_balance / (10**target_decimals):.6f}")
        logging.info(f"  - Amount received: {amount_received_wei / (10**target_decimals):.6f}")

        if amount_received_wei <= 0:
            logging.error("  - CRITICAL: No tokens received from buy transaction. Aborting sell.")
            return
        
        # --- 2. SELL TRANSACTION ---
        logging.info(f"Step 2: Selling {amount_received_wei / (10**target_decimals)} of {token_name} ({token_address}) on {sell_dex_name} (v{sell_router_info['version']})...")
        
        initial_base_token_balance = base_token_contract.functions.balanceOf(account.address).call()
        logging.info(f"  - Initial balance of base token: {initial_base_token_balance / (10**base_decimals):.6f}")

        # --- Sell Transaction Preparation ---
        router_type_sell = sell_router_info.get('type', 'uniswapv2')
        sell_txn = None
        sell_nonce = w3.eth.get_transaction_count(account.address)

        sell_swap_function = None
        if router_type_sell == '1inch':
            sell_swap_function, _ = _prepare_1inch_swap(sell_router_info, amount_received_wei, token_address, BASE_CURRENCY_ADDRESS)
        elif router_type_sell == 'alienbase':
            sell_swap_function, _ = _prepare_alien_base_swap(sell_dex_name, sell_router_info, amount_received_wei, token_address, BASE_CURRENCY_ADDRESS, pair_address=sell_pool['pairAddress'], fee_bps_hint=sell_pool.get('feeBps'))
        elif sell_router_info['version'] == 2:
            if router_type_sell == 'solidly':
                sell_swap_function, _ = _prepare_solidly_swap(sell_dex_name, sell_router_info, amount_received_wei, token_address, BASE_CURRENCY_ADDRESS, pair_address=sell_pool['pairAddress'])
            elif router_type_sell == 'balancer_v2':
                sell_swap_function, _ = _prepare_balancer_v2_swap(sell_router_info, amount_received_wei, token_address, BASE_CURRENCY_ADDRESS, pair_address=sell_pool['pairAddress'])
            elif router_type_sell == 'swaap':
                sell_swap_function, _ = _prepare_swaap_swap(sell_router_info, amount_received_wei, token_address, BASE_CURRENCY_ADDRESS, pair_address=sell_pool['pairAddress'])
            else: # Default to uniswapv2
                sell_swap_function, _ = _prepare_uniswap_v2_swap(sell_router_info, amount_received_wei, [token_address, BASE_CURRENCY_ADDRESS], pair_address=sell_pool['pairAddress'])
        elif sell_router_info['version'] == 3:
            sell_swap_function, _ = _prepare_uniswap_v3_swap(sell_dex_name, sell_router_info, amount_received_wei, token_address, BASE_CURRENCY_ADDRESS, pair_address=sell_pool['pairAddress'], fee_bps_hint=sell_pool.get('feeBps'))
        else:
            raise NotImplementedError(f"DEX version {sell_router_info['version']} or type '{router_type_sell}' is not supported for sells.")

        logging.info("  - Building sell transaction...")
        sell_payload = {
            'from': account.address, 'nonce': sell_nonce,
            'maxFeePerGas': max_fee_per_gas, 'maxPriorityFeePerGas': max_priority_fee,
            'chainId': chain_id
        }
        # Gas estimation removed by user request. Using MAX_GAS_LIMIT.
        sell_payload['gas'] = MAX_GAS_LIMIT
        sell_txn = sell_swap_function.build_transaction(sell_payload)

        if not sell_txn:
            raise Exception("Failed to build sell transaction.")
            
        signed_sell_txn = w3.eth.account.sign_transaction(sell_txn, PRIVATE_KEY)
        sell_tx_hash = w3.eth.send_raw_transaction(signed_sell_txn.raw_transaction)
        logging.info(f"  - Sell Tx sent: {sell_tx_hash.hex()}. Waiting for receipt...")
        sell_receipt = w3.eth.wait_for_transaction_receipt(sell_tx_hash, timeout=120)

        if sell_receipt['status'] == 0:
            logging.error("  - SELL TRANSACTION FAILED. You are now holding the bought tokens.")
        else:
            logging.info("  - Sell transaction successful! Checking for balance change...")
            new_base_token_balance = _wait_for_balance_change(
                base_token_contract, account.address, initial_base_token_balance
            )
            # final_amount_out_wei is the net change in balance from the sell transaction.
            final_amount_out_wei = new_base_token_balance - initial_base_token_balance
            logging.info(f"  - New balance of base token: {new_base_token_balance / (10**base_decimals):.6f}")
            logging.info(f"  - Net base tokens received from sell: {final_amount_out_wei / (10**base_decimals):.6f}")

            # Profit is the final amount received from the sell, minus the initial amount spent on the buy.
            # This now implicitly includes the gas cost of the sell transaction.
            profit_wei = final_amount_out_wei
            profit_base_token = (final_amount_out_wei - amount_in_wei) / (10**base_decimals)

            if (final_amount_out_wei - amount_in_wei) > 0:
                logging.info(f"  - SUCCESS! Arbitrage profitable. Profit: {profit_base_token:.6f} base tokens.")
            else:
                logging.warning(f"  - LOSS. Arbitrage resulted in a loss of: {abs(profit_base_token):.6f} base tokens.")

    except Exception as e:
        logging.error(f"An unexpected error occurred during trade execution: {e}", exc_info=True)
