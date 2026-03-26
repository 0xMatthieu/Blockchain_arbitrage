import time
import logging
from config import (w3, account, PRIVATE_KEY, MAX_GAS_LIMIT, DEX_ROUTERS, BASE_CURRENCY_ADDRESS,
                    TRADE_AMOUNT_BASE_TOKEN, TX_RECEIPT_TIMEOUT)
from abi import (ERC20_ABI, SOLIDLY_PAIR_ABI, MINIMAL_V2_PAIR_ABI, UNISWAP_V3_POOL_ABI,
                 PANCAKE_V3_POOL_ABI, V2_FACTORY_ABI, SOLIDLY_FACTORY_ABI,
                 UNISWAP_V3_FACTORY_ABI, PANCAKE_V3_FACTORY_ABI)

# Cache for token decimals — immutable on-chain, no need to re-fetch
_decimals_cache = {}

def get_decimals(token_address):
    """Returns decimals for a token, caching the result."""
    if token_address not in _decimals_cache:
        contract = w3.eth.contract(address=token_address, abi=ERC20_ABI)
        _decimals_cache[token_address] = contract.functions.decimals().call()
    return _decimals_cache[token_address]

def get_token_info(token_address):
    """Fetches name and symbol for a given token address."""
    try:
        token_contract = w3.eth.contract(address=token_address, abi=ERC20_ABI)
        symbol = token_contract.functions.symbol().call()
        name = token_contract.functions.name().call()
        return {'symbol': symbol, 'name': name}
    except Exception as e:
        logging.warning(f"  - Could not fetch name/symbol for {token_address}. Error: {str(e)[:100]}")
        symbol_fallback = f"[{token_address[-6:]}]"
        return {'symbol': symbol_fallback, 'name': token_address}

def find_router_info(dex_id, routers, pair_address=None):
    """Finds a router's info with robust matching, using factory for disambiguation."""
    dex_id = dex_id.lower().strip().replace('-', '_')

    possible_matches = []
    for key, info in routers.items():
        key_parts = key.replace('-', '_').split('_')
        if dex_id == key or dex_id == key_parts[0] or dex_id == info["address"].lower():
            possible_matches.append(info)

    if not possible_matches:
        logging.debug(f"No router match found for dex_id '{dex_id}'. Available router keys: {list(routers.keys())}")
        return None

    if len(possible_matches) == 1:
        return possible_matches[0]

    if pair_address:
        try:
            pair_contract = w3.eth.contract(address=pair_address, abi=MINIMAL_V2_PAIR_ABI)
            on_chain_factory = pair_contract.functions.factory().call()

            for info in possible_matches:
                if 'factory' in info and info['factory'] == on_chain_factory:
                    return info
            logging.warning(f"  - Could not find a router with factory {on_chain_factory} among candidates.")
        except Exception as e:
            logging.debug(f"Could not query factory for pair {pair_address} to disambiguate router: {e}")

    logging.debug(f"Found multiple possible routers for '{dex_id}'. Selecting highest version as fallback.")
    possible_matches.sort(key=lambda x: x.get('version', 0), reverse=True)
    return possible_matches[0]

# --- helpers ---------------------------------------------------------------
def _gas_params(w3, bump_pct: int = 0):
    """Return (priority, max) gas fees, optionally bumped by bump_pct%."""
    prio = w3.eth.max_priority_fee
    base = w3.eth.get_block('latest')['baseFeePerGas']
    max_fee = base * 2 + prio
    if bump_pct:
        prio += prio * bump_pct // 100
        max_fee += max_fee * bump_pct // 100
    return prio, max_fee

def _build_payload(w3, from_addr, nonce, bump=0):
    prio, max_fee = _gas_params(w3, bump)
    return {
        "from": from_addr,
        "nonce": nonce,
        "maxPriorityFeePerGas": prio,
        "maxFeePerGas": max_fee,
        "gas": MAX_GAS_LIMIT,
        "chainId": w3.eth.chain_id,
    }

# --- main approval routine --------------------------------------------------

def check_and_approve_token(token_address: str,
                            spender_address: str,
                            amount_to_approve_wei: int):
    if not all([account, token_address, spender_address]):
        return

    token = w3.eth.contract(address=token_address, abi=ERC20_ABI)
    allowance = token.functions.allowance(account.address,
                                          spender_address).call()
    logging.info(
        f"Allowance for {spender_address} is {allowance}, desired {amount_to_approve_wei}"
    )

    if allowance >= amount_to_approve_wei:
        logging.info("Sufficient allowance already set.")
        return

    try:
        base_nonce = w3.eth.get_transaction_count(account.address)
        bump = 0  # % gas bump

        # Optional reset‑to‑zero step ---------------------------------------
        if allowance > 0:
            logging.info("Resetting allowance to 0 (USDT‑style safeguard)…")
            payload = _build_payload(w3, account.address, base_nonce, bump)
            reset_tx = token.functions.approve(spender_address, 0
                          ).build_transaction(payload)
            signed = w3.eth.account.sign_transaction(reset_tx, PRIVATE_KEY)
            tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
            w3.eth.wait_for_transaction_receipt(tx_hash, timeout=TX_RECEIPT_TIMEOUT)
            logging.info(f"Reset tx mined: {tx_hash.hex()}")
            base_nonce += 1
            bump += 10

        # Final approve -----------------------------------------------------
        logging.info(f"Approving {amount_to_approve_wei}…")
        payload = _build_payload(w3, account.address, base_nonce, bump)
        approve_tx = token.functions.approve(spender_address,
                                             amount_to_approve_wei
                        ).build_transaction(payload)
        signed = w3.eth.account.sign_transaction(approve_tx, PRIVATE_KEY)
        tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
        w3.eth.wait_for_transaction_receipt(tx_hash, timeout=TX_RECEIPT_TIMEOUT)
        logging.info(f"Approve tx mined: {tx_hash.hex()}")

    except Exception as err:
        logging.error(f"Approval flow failed: {err}")


# --- on-chain price functions -----------------------------------------------

def _get_v2_pool_price(pool_address, token_in_address, token_out_address, token_in_decimals, token_out_decimals):
    """
    Retrieves the spot price from a Uniswap V2-style pool using getReserves.
    Returns the price of token_in in terms of token_out (how much token_out per token_in).
    """
    try:
        pair = w3.eth.contract(address=pool_address, abi=MINIMAL_V2_PAIR_ABI)
        reserve0, reserve1, _ = pair.functions.getReserves().call()
        token0 = w3.to_checksum_address(pair.functions.token0().call())

        token_in_address = w3.to_checksum_address(token_in_address)

        if token0 == token_in_address:
            reserve_in, reserve_out = reserve0, reserve1
            dec_in, dec_out = token_in_decimals, token_out_decimals
        else:
            reserve_in, reserve_out = reserve1, reserve0
            dec_in, dec_out = token_in_decimals, token_out_decimals

        if reserve_in == 0:
            return None

        # price = (reserve_out / 10^dec_out) / (reserve_in / 10^dec_in)
        price = (reserve_out / (10 ** dec_out)) / (reserve_in / (10 ** dec_in))

        logging.debug(f"V2 pool {pool_address} price for {token_in_address}: {price}")
        return price
    except Exception as e:
        logging.warning(f"Could not get price from V2 pool {pool_address} via getReserves. Error: {e}")
        return None


def _get_solidly_pool_price(pool_address: str, token_in_address: str, token_out_address: str, token_in_decimals: int,
                            token_out_decimals: int):
    """
    Retrieves the spot price from a Solidly-style pool using the `prices` function.
    Returns the price of token_in in terms of token_out.
    """
    try:
        pool_contract = w3.eth.contract(address=pool_address, abi=SOLIDLY_PAIR_ABI)

        amount_in = int(TRADE_AMOUNT_BASE_TOKEN * (10 ** token_in_decimals))

        prices_out = pool_contract.functions.prices(token_in_address, amount_in, 1).call()

        if not prices_out:
            logging.warning(f"Solidly pool {pool_address} `prices` call returned no data for token {token_in_address}.")
            return None

        amount_out_wei = prices_out[0]
        price = (amount_out_wei / (10 ** token_out_decimals))

        logging.debug(f"Solidly pool {pool_address} price for {token_in_address}: {price} {token_out_address}")
        return price
    except Exception as e:
        logging.warning(f"Could not get price from Solidly pool {pool_address} via `prices` function. Error: {e}")
        return None


def _get_uniswap_or_pancakeswap_pool_price(pool_address: str, router_type: str, token_in_address: str, token_out_address: str, token_in_decimals: int, token_out_decimals: int):
    """
    Retrieves the spot price from a Uniswap/Pancake V3 pool using `slot0`.
    Returns the price of token_in in terms of token_out.
    """
    try:
        if router_type == 'pancakeswap_v3':
            pool_abi = PANCAKE_V3_POOL_ABI
        else:
            pool_abi = UNISWAP_V3_POOL_ABI

        pool_contract = w3.eth.contract(address=pool_address, abi=pool_abi)
        sqrt_price_x96, *_ = pool_contract.functions.slot0().call()

        if sqrt_price_x96 == 0:
            logging.warning(f"V3 pool {pool_address} slot0.sqrtPriceX96 is 0. Pool may not be initialized.")
            return None

        price_raw_t0_t1 = (sqrt_price_x96 / 2**96) ** 2

        pool_token0_addr = w3.to_checksum_address(pool_contract.functions.token0().call())
        token_in_address = w3.to_checksum_address(token_in_address)

        if pool_token0_addr == token_in_address:
            decimals_t0 = token_in_decimals
            decimals_t1 = token_out_decimals
        else:
            decimals_t0 = token_out_decimals
            decimals_t1 = token_in_decimals

        price_t0_t1_adj = price_raw_t0_t1 * (10**decimals_t0) / (10**decimals_t1)

        if pool_token0_addr == token_in_address:
            price = price_t0_t1_adj
        else:
            if price_t0_t1_adj == 0:
                logging.warning(f"Calculated V3 price for pool {pool_address} is zero, cannot invert.")
                return None
            price = 1 / price_t0_t1_adj

        logging.debug(f"Uniswap/Pancake V3 pool {pool_address} price for {token_in_address}: {price} {token_out_address}")
        return price

    except Exception as e:
        logging.warning(f"Could not get price from V3 pool {pool_address} via `slot0`. Error: {e}")
        return None


def get_lp_price(pool, token_address):
    """Get on-chain price for a pool. Returns price of token in terms of BASE_CURRENCY, or None."""
    dex_name = pool['dex']
    router_info = find_router_info(dex_name, DEX_ROUTERS, pair_address=pool.get('pairAddress'))
    if not router_info:
        return None
    router_type = router_info.get('type', 'uniswap_v2')

    base_decimals = get_decimals(BASE_CURRENCY_ADDRESS)
    quote_decimals = get_decimals(token_address)

    if router_type in ('1inch', 'balancer_v2', 'swaap_v2'):
        return None
    elif router_info['version'] == 2:
        if router_type == 'solidly':
            return _get_solidly_pool_price(pool['pairAddress'], token_address, BASE_CURRENCY_ADDRESS,
                                           quote_decimals, base_decimals)
        else:
            # V2 forks: uniswap_v2, sushiswap, baseswap, alienbase
            return _get_v2_pool_price(pool['pairAddress'], token_address, BASE_CURRENCY_ADDRESS,
                                      quote_decimals, base_decimals)
    elif router_info['version'] == 3:
        return _get_uniswap_or_pancakeswap_pool_price(pool['pairAddress'], router_type, token_address,
                                                       BASE_CURRENCY_ADDRESS, quote_decimals, base_decimals)
    else:
        logging.warning(f"DEX version {router_info['version']} or type '{router_type}' is not supported for LP price.")
        return None


# --- on-chain pool discovery ------------------------------------------------

ZERO_ADDRESS = "0x" + "00" * 20
V3_FEE_TIERS = [500, 3000, 10000, 2500, 100]

def _has_code(address):
    """Check if an address has deployed contract code."""
    return address and address != ZERO_ADDRESS and w3.eth.get_code(address)


def discover_pools(token_address):
    """
    Discover all pools for a token across configured DEXes by querying factory contracts on-chain.
    Returns a list of pool dicts with metadata.
    """
    token_address = w3.to_checksum_address(token_address)
    base_address = BASE_CURRENCY_ADDRESS
    token_info = get_token_info(token_address)
    base_info = get_token_info(base_address)
    pair_label = f"{token_info['symbol']}/{base_info['symbol']}"

    pools = []

    for dex_key, router_info in DEX_ROUTERS.items():
        router_type = router_info.get('type', 'uniswap_v2')
        factory_addr = router_info.get('factory')

        # Skip DEXes without a factory (aggregators like 1inch)
        if not factory_addr:
            logging.debug(f"  - {dex_key}: no factory, skipping discovery")
            continue

        try:
            if router_type == 'solidly':
                _discover_solidly(pools, dex_key, router_info, factory_addr, token_address, base_address, pair_label)
            elif router_info['version'] == 3:
                _discover_v3(pools, dex_key, router_info, factory_addr, token_address, base_address, pair_label)
            elif router_info['version'] == 2:
                _discover_v2(pools, dex_key, router_info, factory_addr, token_address, base_address, pair_label)
        except Exception as e:
            logging.warning(f"  - {dex_key}: discovery failed: {e}")

    logging.info(f"  Discovered {len(pools)} pools for {pair_label}")
    return pools


def _discover_v2(pools, dex_key, router_info, factory_addr, token_address, base_address, pair_label):
    """Discover V2-style pools via factory.getPair()."""
    factory = w3.eth.contract(address=factory_addr, abi=V2_FACTORY_ABI)
    pair_addr = factory.functions.getPair(token_address, base_address).call()

    if not _has_code(pair_addr):
        return

    # Verify pool has reserves
    pair = w3.eth.contract(address=pair_addr, abi=MINIMAL_V2_PAIR_ABI)
    r0, r1, _ = pair.functions.getReserves().call()
    if r0 == 0 or r1 == 0:
        logging.info(f"  - {dex_key}: pool {pair_addr} has zero reserves, skipping")
        return

    pools.append({
        'dex': dex_key,
        'pair': pair_label,
        'pairAddress': pair_addr,
        'feeBps': 0,
        'liq_usd': 0,
        'base_currency_price_usd': 0,
    })
    logging.info(f"  - {dex_key}: found V2 pool {pair_addr}")


def _discover_solidly(pools, dex_key, router_info, factory_addr, token_address, base_address, pair_label):
    """Discover Solidly-style pools (volatile + stable)."""
    factory = w3.eth.contract(address=factory_addr, abi=SOLIDLY_FACTORY_ABI)

    for stable in [False, True]:
        pool_addr = factory.functions.getPool(token_address, base_address, stable).call()
        if not _has_code(pool_addr):
            continue

        # Verify pool has reserves
        pair = w3.eth.contract(address=pool_addr, abi=SOLIDLY_PAIR_ABI)
        r0, r1, _ = pair.functions.getReserves().call()
        if r0 == 0 or r1 == 0:
            logging.info(f"  - {dex_key}: pool {pool_addr} ({'stable' if stable else 'volatile'}) has zero reserves, skipping")
            continue

        pool_type = "stable" if stable else "volatile"
        pools.append({
            'dex': dex_key,
            'pair': f"{pair_label} ({pool_type})",
            'pairAddress': pool_addr,
            'feeBps': 0,
            'liq_usd': 0,
            'base_currency_price_usd': 0,
        })
        logging.info(f"  - {dex_key}: found Solidly {pool_type} pool {pool_addr}")


def _discover_v3(pools, dex_key, router_info, factory_addr, token_address, base_address, pair_label):
    """Discover V3-style pools across fee tiers."""
    router_type = router_info.get('type', 'uniswap_v3')
    if router_type == 'pancakeswap_v3':
        factory_abi = PANCAKE_V3_FACTORY_ABI
        pool_abi = PANCAKE_V3_POOL_ABI
    else:
        factory_abi = UNISWAP_V3_FACTORY_ABI
        pool_abi = UNISWAP_V3_POOL_ABI

    factory = w3.eth.contract(address=factory_addr, abi=factory_abi)

    for fee in V3_FEE_TIERS:
        pool_addr = factory.functions.getPool(token_address, base_address, fee).call()
        if not _has_code(pool_addr):
            continue

        # Verify pool has liquidity
        pool_contract = w3.eth.contract(address=pool_addr, abi=pool_abi)
        try:
            liquidity = pool_contract.functions.liquidity().call()
            if liquidity == 0:
                logging.info(f"  - {dex_key}: pool {pool_addr} at {fee} bps has zero liquidity, skipping")
                continue
        except Exception:
            continue

        pools.append({
            'dex': dex_key,
            'pair': pair_label,
            'pairAddress': pool_addr,
            'feeBps': fee,
            'liq_usd': 0,
            'base_currency_price_usd': 0,
        })
        logging.info(f"  - {dex_key}: found V3 pool {pool_addr} at {fee} bps (liquidity: {liquidity})")
