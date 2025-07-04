import time
from web3.logs import DISCARD
from web3.exceptions import ContractLogicError
from config import (
    w3, account, PRIVATE_KEY, MAX_GAS_LIMIT, DEX_ROUTERS,
    BASE_CURRENCY_ADDRESS, TRADE_AMOUNT_BASE_TOKEN, SLIPPAGE_TOLERANCE_PERCENT,
    RPC_MAX_RETRIES, RPC_BACKOFF_FACTOR, BOT_WALLET
)
from abi import (
    ERC20_ABI, UNISWAP_V2_ROUTER_ABI, UNISWAP_V3_ROUTER_ABI, SOLIDLY_ROUTER_ABI,
    SOLIDLY_FACTORY_ABI, UNISWAP_V3_POOL_ABI, UNISWAP_V3_FACTORY_ABI, SOLIDLY_PAIR_ABI,
    UNISWAP_V3_QUOTER_ABI, PANCAKE_V3_POOL_ABI, ONEINCH_V6_ROUTER_ABI
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
            result = callable_func()
            return result

        except ContractLogicError as err:
            # ↪ web3-py puts the tx receipt dict as err.args[0]
            payload = None
            print(f"  - [RPC] ContractLogicError {err}")
            if err.args and isinstance(err.args[0], dict):
                payload = err.args[0].get("data", b"")
                print(f"  - [RPC] payload: {payload}")

            # payload comes back as HexBytes; length > 4 → has real data
            if payload and len(payload) > 4:
                print("  - [RPC] Call reverted with data. Attempting to decode as Quoter V2 response.")
                # strip first 4B selector if present
                data_bytes = HexBytes(payload)[4:] if len(payload) % 32 else HexBytes(payload)
                # decode up to the 4 items Quoter V2 returns
                decoded = decode(_QUOTER_V2_RET_TYPES, data_bytes.ljust(32 * 4, b"\0"))
                print(f"  - [RPC] Decoded amountOut: {decoded[0]}")
                return decoded[0]                     # amountOut (uint256)

            # truly empty revert → raise without retry
            print(f"  - Contract logic error (revert, no data): {err}")
            raise err

        except Exception as err:
            wait = RPC_BACKOFF_FACTOR * (2 ** i)
            print(f"\n  - [RPC] Call failed with unhandled exception: {err}. Retrying in {wait:.2f}s "
                  f"({i + 1}/{RPC_MAX_RETRIES})")
            time.sleep(wait)

    print(f"  - [RPC] Call failed after {RPC_MAX_RETRIES} retries. Raising exception.")
    raise Exception(f"RPC call failed after {RPC_MAX_RETRIES} retries.")

def _get_v3_pool_abi(dex_name):
    """Selects the correct V3 pool ABI based on the DEX name."""
    if 'pancake' in dex_name.lower():
        print("  - Using PancakeSwap V3 Pool ABI.")
        return PANCAKE_V3_POOL_ABI
    print("  - Using Uniswap V3 Pool ABI.")
    return UNISWAP_V3_POOL_ABI

def _prepare_1inch_swap(router_info: dict, amount_in_wei: int, token_in: str, token_out: str):
    """
    Prepares a swap transaction for the 1inch Aggregation router using on-chain calls.
    This function simulates the swap to get a quote, then builds the real transaction.
    """
    print("  - Preparing 1inch on-chain swap...")
    router = w3.eth.contract(address=router_info['address'], abi=ONEINCH_V6_ROUTER_ABI)

    # 1inch default executor for Base network.
    EXECUTOR_ADDR = "0x1111111111111111111111111111111111111111"
    
    # Empty data, so the executor tries to find the best route.
    executor_data = b""

    # --- Step 1: Simulate the swap to get a quote ---
    # We need a quote to calculate minReturn, but the router has no getAmountsOut.
    # So, we build a "probe" transaction with minReturn=1 and simulate it.
    print("  - Simulating swap to get on-chain quote...")
    probe_desc = (
        token_in,           # srcToken
        token_out,          # dstToken
        account.address,    # srcReceiver (will be ignored by router, but needs to be valid addr)
        account.address,    # dstReceiver
        amount_in_wei,      # amount
        1,                  # minReturn (probe value)
        0,                  # flags (0 = simple)
        b""                 # permit (none)
    )
    
    probe_swap_fn = router.functions.swap(EXECUTOR_ADDR, probe_desc, executor_data)
    
    try:
        # .call() simulates the transaction. No value is sent as we use WETH, not native ETH.
        call_params = {'from': account.address}
        quoted_amounts = resilient_rpc_call(lambda: probe_swap_fn.call(call_params))
        quoted_amount_out_wei = quoted_amounts[0] # returnAmount
    except Exception as e:
        print(f"  - 1inch quote simulation failed: {e}")
        raise ValueError("Could not get on-chain quote from 1inch router.") from e

    if quoted_amount_out_wei == 0:
        raise ValueError("1inch on-chain quote returned 0 amount out.")

    # --- Step 2: Build the real swap function with proper slippage ---
    amount_out_min_wei = int(quoted_amount_out_wei * (1 - SLIPPAGE_TOLERANCE_PERCENT / 100.0))
    print(f"  - 1inch On-chain Quote: {quoted_amount_out_wei}, Min Amount Out: {amount_out_min_wei}")

    final_desc = (
        token_in,
        token_out,
        account.address,
        account.address,
        amount_in_wei,
        amount_out_min_wei, # Use the properly calculated minReturn
        0,
        b""
    )

    swap_function = router.functions.swap(EXECUTOR_ADDR, final_desc, executor_data)

    return swap_function, amount_out_min_wei

def _prepare_solidly_swap(
    dex_name: str,
    router_info: dict,
    amount_in_wei: int,
    token_in: str,
    token_out: str,
    *,
    safety_slippage_bps: int = 300  # extra 3 % head-room on top of your global setting
):
    """
    Build a Solidly-style swap call that will *not* revert on:
        • transfer-tax / fee-on-transfer tokens
        • tiny quote drift between `getAmountsOut` and the actual swap
    """
    factory = router_info.get("factory")
    if not factory:
        raise ValueError(f"{dex_name}: no factory address in config")

    print(f"  - Solidly router detected ({dex_name}). Looking up pool …")
    factory_contract = w3.eth.contract(factory, abi=SOLIDLY_FACTORY_ABI)
    zero = "0x" + "0"*40

    # ---------- 1️⃣  find the pool (volatile first, then stable) ----------
    for attempted_stable in (False, True):
        pool = resilient_rpc_call(
            lambda: factory_contract.functions.getPool(
                token_in, token_out, attempted_stable
            ).call()
        )
        if pool != zero:
            is_stable = attempted_stable
            break
    else:
        raise ValueError(f"{dex_name}: no pool for {token_in} → {token_out}")

    print(f"  - Pool found: {pool} ({'stable' if is_stable else 'volatile'})")

    # ---------- 2️⃣  reserves sanity-check ----------
    pair = w3.eth.contract(pool, abi=SOLIDLY_PAIR_ABI)
    r0, r1, _ = resilient_rpc_call(lambda: pair.functions.getReserves().call())
    print(f"  - On-chain reserves: r0={r0}, r1={r1}")
    if r0 == 0 or r1 == 0:
        raise ValueError(f"{dex_name}: pool has zero reserves")

    # ---------- 3️⃣  quote ----------
    router = w3.eth.contract(router_info["address"], abi=SOLIDLY_ROUTER_ABI)
    routes = [(token_in, token_out, is_stable, factory)]
    amounts = resilient_rpc_call(
        lambda: router.functions.getAmountsOut(amount_in_wei, routes).call(
            {"from": account.address}
        )
    )
    min_out = int(amounts[-1] * (1 - SLIPPAGE_TOLERANCE_PERCENT / 100.0))

    # ---------- 4️⃣  helper to build either classic or FOT-safe swap ----------
    def _build_swap_fn(out_min: int):
        if "swapExactTokensForTokensSupportingFeeOnTransferTokens" in router.functions:
            return router.functions.swapExactTokensForTokensSupportingFeeOnTransferTokens(
                amount_in_wei,
                out_min,
                routes,
                account.address,
                int(time.time()) + 300,
            )
        # classic path
        return router.functions.swapExactTokensForTokens(
            amount_in_wei,
            out_min,
            routes,
            account.address,
            int(time.time()) + 300,
        )

    swap_fn = _build_swap_fn(min_out)

    # ---------- 5️⃣  try a cheap gas-estimation; fall back on failure ----------
    try:
        swap_fn.estimate_gas({"from": account.address})
        final_min_out = min_out
        print(f"  - MinOut = {min_out}")
    except ContractLogicError:
        print("  - ↪ first gas check reverted; falling back to minOut = 0 "
              "(fee-on-transfer or quote slippage)")
        # tighten safety: add extra bps off the original quote
        min_out_safe = int(amounts[-1] * (1 - safety_slippage_bps / 10_000))
        swap_fn = _build_swap_fn(min_out_safe if min_out_safe > 0 else 0)
        final_min_out = min_out_safe
        
        # Re-check gas estimate with the new zero-min-out swap function.
        # If this also fails, the pool is truly broken/illiquid.
        try:
            swap_fn.estimate_gas({"from": account.address})
            print("  - Gas estimation with minOut=0 succeeded. Proceeding with caution.")
        except ContractLogicError as e:
            print("  - ↪ Gas estimation FAILED even with minOut=0. Pool is unusable.")
            raise ValueError(f"{dex_name}: Pool is illiquid or broken, swap would fail.") from e

    return swap_fn, final_min_out

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

def _prepare_uniswap_v3_swap(
        dex_name: str,
        router_info: dict,
        amount_in_wei: int,
        token_in: str,
        token_out: str
    ):
    """
    Prepares a Uniswap-V3 style swap and **never** dies on
    `quoteExactInputSingle` “execution reverted, no data”.
    """

    factory_address = router_info.get("factory")
    if not factory_address:
        raise ValueError(f"V3 DEX '{dex_name}' requires a 'factory' address")

    print(f"  - V3 DEX detected. Querying factory {factory_address} …")
    factory = w3.eth.contract(factory_address, abi=UNISWAP_V3_FACTORY_ABI)

    # ------------------------------------------------------------------ #
    # ① find a pool that actually exists (code size > 0)
    # ------------------------------------------------------------------ #
    FEE_TIERS = [500, 3000, 10000, 2500, 100]
    chosen_fee, pool_address = None, None
    zero = "0x" + "00" * 20

    for fee in FEE_TIERS:
        addr = resilient_rpc_call(
            lambda: factory.functions.getPool(token_in, token_out, fee).call()
        )
        if addr and addr != zero and w3.eth.get_code(addr):
            chosen_fee, pool_address = fee, addr
            print(f"  - Pool {addr} at {fee} bps selected")
            break

    if not pool_address:
        raise ValueError(f"No live V3 pool for pair on {dex_name}")

    # ------------------------------------------------------------------ #
    # ② sanity-check pool status (slot0 & liquidity)
    # ------------------------------------------------------------------ #
    pool = w3.eth.contract(pool_address, abi=_get_v3_pool_abi(dex_name))
    sqrt_price_x96, *_ = resilient_rpc_call(lambda: pool.functions.slot0().call())
    if sqrt_price_x96 == 0:
        raise ValueError("Pool exists but never initialised (sqrtPriceX96 == 0)")

    liquidity = resilient_rpc_call(lambda: pool.functions.liquidity().call())
    if liquidity == 0:
        raise ValueError("Pool initialised but has zero active liquidity")

    print(f"  - Pool initialised with {liquidity} liquidity")

    # ------------------------------------------------------------------ #
    # ③ detect tokens that break static-calls (fee-on-transfer, rebasing)
    # ------------------------------------------------------------------ #
    def _is_static_safe(token_addr: str) -> bool:
        erc = w3.eth.contract(token_addr, abi=ERC20_ABI)
        try:
            erc.functions.totalSupply().call({"gas": 25_000})
            return True
        except ContractLogicError:
            return False

    static_safe = _is_static_safe(token_in) and _is_static_safe(token_out)

    # ------------------------------------------------------------------ #
    # ④ obtain a quote – primary: QuoterV2, fallback: router/helper, last:
    #    constant-product estimate (reserves)
    # ------------------------------------------------------------------ #
    amount_out_wei = None
    if static_safe:
        quoter_addr = router_info.get("quoter")
        if not quoter_addr:
            raise ValueError("Router config missing 'quoter' address")

        print(f"  - Using quoter {quoter_addr} …")
        quoter = w3.eth.contract(quoter_addr, abi=UNISWAP_V3_QUOTER_ABI)
        params = (token_in, token_out, chosen_fee, amount_in_wei, 0)

        try:
            quote_tuple = resilient_rpc_call(
                lambda: quoter.functions.quoteExactInputSingle(params).call(
                    {"from": account.address, "gas": 500_000}
                )
            )
            amount_out_wei = int(quote_tuple[0])

        except ContractLogicError as err:
            # silent-revert fallback path
            if "execution reverted" in str(err):
                print("  - Quoter reverted without data; trying router helper")
            else:
                raise
    # ↓ Router-level helper (not available on all deployments)
    if amount_out_wei is None and hasattr(pool.functions, "getQuote"):
        try:
            amount_out_wei = pool.functions.getQuote(
                amount_in_wei, token_in).call()
        except Exception:
            pass

    # ↓ constant-product approximation if everything else failed
    if amount_out_wei is None:
        print("  - Falling back to reserve-based estimate")
        # (reserve0, reserve1) mapping depends on token sorting
        reserve0, reserve1, *_ = pool.functions.getReserves().call()
        if token_in.lower() < token_out.lower():
            amount_out_wei = amount_in_wei * reserve1 // (reserve0 + amount_in_wei)
        else:
            amount_out_wei = amount_in_wei * reserve0 // (reserve1 + amount_in_wei)

    # ------------------------------------------------------------------ #
    # ⑤ slippage guard & swap-function build
    # ------------------------------------------------------------------ #
    amount_out_min = int(amount_out_wei * (1 - SLIPPAGE_TOLERANCE_PERCENT / 100))
    print(f"  - Quoted out: {amount_out_wei}, minOut: {amount_out_min}")

    router = w3.eth.contract(router_info["address"], abi=UNISWAP_V3_ROUTER_ABI)
    swap_params = {
        "tokenIn": token_in,
        "tokenOut": token_out,
        "fee": chosen_fee,
        "recipient": account.address,
        "deadline": int(time.time()) + 300,
        "amountIn": amount_in_wei,
        "amountOutMinimum": amount_out_min,
        "sqrtPriceLimitX96": 0,
    }
    swap_fn = router.functions.exactInputSingle(swap_params)
    return swap_fn, amount_out_min

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


def execute_trade(buy_pool, sell_pool, spread, token_address):
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
        print(f"Step 1: Buying {token_address} on {buy_dex_name} (v{buy_router_info['version']})...")
        target_token_contract = w3.eth.contract(address=token_address, abi=ERC20_ABI)
        target_decimals = resilient_rpc_call(lambda: target_token_contract.functions.decimals().call())

        # --- Transaction Preparation ---
        chain_id = resilient_rpc_call(lambda: w3.eth.chain_id)
        router_type = buy_router_info.get('type', 'uniswapv2')
        buy_txn = None

        max_priority_fee = resilient_rpc_call(lambda: w3.eth.max_priority_fee)
        latest_block = resilient_rpc_call(lambda: w3.eth.get_block('latest'))
        base_fee = latest_block['baseFeePerGas']
        max_fee_per_gas = base_fee * 2 + max_priority_fee
        nonce = resilient_rpc_call(lambda: w3.eth.get_transaction_count(account.address))

        swap_function = None
        if router_type == '1inch':
            swap_function, _ = _prepare_1inch_swap(buy_router_info, amount_in_wei, BASE_CURRENCY_ADDRESS, token_address)
        elif buy_router_info['version'] == 2:
            if router_type == 'solidly':
                swap_function, _ = _prepare_solidly_swap(buy_dex_name, buy_router_info, amount_in_wei, BASE_CURRENCY_ADDRESS, token_address)
            else: # Default to uniswapv2
                swap_function, _ = _prepare_uniswap_v2_swap(buy_router_info, amount_in_wei, [BASE_CURRENCY_ADDRESS, token_address])
        elif buy_router_info['version'] == 3:
            swap_function, _ = _prepare_uniswap_v3_swap(buy_dex_name, buy_router_info, amount_in_wei, BASE_CURRENCY_ADDRESS, token_address)
        else:
            raise NotImplementedError(f"DEX version {buy_router_info['version']} or type '{router_type}' is not supported for buys.")
        
        print("  - Building buy transaction...")
        buy_payload = {
            'from': account.address, 'nonce': nonce,
            'maxFeePerGas': max_fee_per_gas, 'maxPriorityFeePerGas': max_priority_fee,
            'chainId': chain_id
        }
        gas_estimate = resilient_rpc_call(lambda: swap_function.estimate_gas(buy_payload))
        buy_payload['gas'] = min(int(gas_estimate * 1.2), MAX_GAS_LIMIT)
        buy_txn = swap_function.build_transaction(buy_payload)

        if not buy_txn:
            raise Exception("Failed to build buy transaction.")

        signed_buy_txn = w3.eth.account.sign_transaction(buy_txn, PRIVATE_KEY)
        buy_tx_hash = w3.eth.send_raw_transaction(signed_buy_txn.raw_transaction)
        print(f"  - Buy Tx sent: {buy_tx_hash.hex()}. Waiting for receipt...")
        buy_receipt = w3.eth.wait_for_transaction_receipt(buy_tx_hash, timeout=120)

        if buy_receipt['status'] == 0:
            print("  - BUY TRANSACTION FAILED (reverted). Aborting arbitrage.")
            return

        print("  - Buy transaction successful! Parsing receipt...")
        amount_received_wei = _parse_receipt_for_amount_out(buy_receipt, buy_router_info, buy_dex_name, token_address, target_decimals)

        if amount_received_wei == 0:
            print("  - CRITICAL: Could not determine received token amount from receipt. Aborting sell.")
            return
        
        # --- 2. SELL TRANSACTION ---
        print(f"Step 2: Selling {amount_received_wei / (10**target_decimals)} of {token_address} on {sell_dex_name} (v{sell_router_info['version']})...")
        
        # --- Sell Transaction Preparation ---
        router_type_sell = sell_router_info.get('type', 'uniswapv2')
        sell_txn = None
        sell_nonce = resilient_rpc_call(lambda: w3.eth.get_transaction_count(account.address))

        sell_swap_function = None
        if router_type_sell == '1inch':
            sell_swap_function, _ = _prepare_1inch_swap(sell_router_info, amount_received_wei, token_address, BASE_CURRENCY_ADDRESS)
        elif sell_router_info['version'] == 2:
            if router_type_sell == 'solidly':
                sell_swap_function, _ = _prepare_solidly_swap(sell_dex_name, sell_router_info, amount_received_wei, token_address, BASE_CURRENCY_ADDRESS)
            else: # Default to uniswapv2
                sell_swap_function, _ = _prepare_uniswap_v2_swap(sell_router_info, amount_received_wei, [token_address, BASE_CURRENCY_ADDRESS])
        elif sell_router_info['version'] == 3:
            sell_swap_function, _ = _prepare_uniswap_v3_swap(sell_dex_name, sell_router_info, amount_received_wei, token_address, BASE_CURRENCY_ADDRESS)
        else:
            raise NotImplementedError(f"DEX version {sell_router_info['version']} or type '{router_type_sell}' is not supported for sells.")

        print("  - Building sell transaction...")
        sell_payload = {
            'from': account.address, 'nonce': sell_nonce,
            'maxFeePerGas': max_fee_per_gas, 'maxPriorityFeePerGas': max_priority_fee,
            'chainId': chain_id
        }
        gas_estimate_sell = resilient_rpc_call(lambda: sell_swap_function.estimate_gas(sell_payload))
        sell_payload['gas'] = min(int(gas_estimate_sell * 1.2), MAX_GAS_LIMIT)
        sell_txn = sell_swap_function.build_transaction(sell_payload)

        if not sell_txn:
            raise Exception("Failed to build sell transaction.")
            
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
