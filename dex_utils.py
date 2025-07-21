import time
import logging
from config import w3, account, PRIVATE_KEY, MAX_GAS_LIMIT
from abi import ERC20_ABI

def get_token_info(token_address):
    """Fetches name and symbol for a given token address."""
    try:
        token_contract = w3.eth.contract(address=token_address, abi=ERC20_ABI)
        symbol = token_contract.functions.symbol().call()
        name = token_contract.functions.name().call()
        return {'symbol': symbol, 'name': name}
    except Exception as e:
        # Some tokens might not have string name/symbol, or might fail for other reasons.
        # Fallback to using address for identification.
        logging.warning(f"  - Could not fetch name/symbol for {token_address}. Error: {str(e)[:100]}")
        symbol_fallback = f"[{token_address[-6:]}]"
        return {'symbol': symbol_fallback, 'name': token_address}

def find_router_info(dex_id, routers):
    """Finds a router's info with robust matching, preferring higher versions."""
    dex_id = dex_id.lower().strip().replace('-', '_')
    
    possible_matches = []
    for key, info in routers.items():
        # A key matches if it is the dex_id, or if its first part (split by _) matches the dex_id.
        # e.g., 'uniswap' should match 'uniswap_v2' and 'uniswap_v3'.
        # 'baseswap' should match 'baseswap'.
        # 'swap' should NOT match 'baseswap'.
        key_parts = key.replace('-', '_').split('_')
        if dex_id == key or dex_id == key_parts[0] or dex_id == info["address"].lower():
            possible_matches.append(info)

    if not possible_matches:
        logging.debug(f"No router match found for dex_id '{dex_id}'. Available router keys: {list(routers.keys())}")
        return None

    if len(possible_matches) == 1:
        return possible_matches[0]

    # If multiple matches are found (e.g., uniswap_v2 and uniswap_v3 for 'uniswap'),
    # prefer the one with the highest version number.
    logging.debug(f"Found multiple possible routers for '{dex_id}'. Selecting highest version.")
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
            w3.eth.wait_for_transaction_receipt(tx_hash)
            logging.info(f"Reset tx mined: {tx_hash.hex()}")
            base_nonce += 1      # increment local nonce
            bump += 10           # +10 % gas bump for next tx  :contentReference[oaicite:3]{index=3}

        # Final approve -----------------------------------------------------
        logging.info(f"Approving {amount_to_approve_wei}…")
        payload = _build_payload(w3, account.address, base_nonce, bump)
        approve_tx = token.functions.approve(spender_address,
                                             amount_to_approve_wei
                        ).build_transaction(payload)
        signed = w3.eth.account.sign_transaction(approve_tx, PRIVATE_KEY)
        tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
        w3.eth.wait_for_transaction_receipt(tx_hash)
        logging.info(f"Approve tx mined: {tx_hash.hex()}")

    except Exception as err:
        logging.error(f"Approval flow failed: {err}")

