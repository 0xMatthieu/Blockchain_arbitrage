import time
from config import w3, account, PRIVATE_KEY, MAX_GAS_LIMIT
from abi import ERC20_ABI

def find_router_info(dex_id, routers):
    """Finds a router's info with robust matching, preferring higher versions."""
    dex_id = dex_id.lower().strip()
    
    possible_matches = []
    for key, info in routers.items():
        # A key matches if it is the dex_id, or if its first part (split by _) matches the dex_id.
        # e.g., 'uniswap' should match 'uniswap_v2' and 'uniswap_v3'.
        # 'baseswap' should match 'baseswap'.
        # 'swap' should NOT match 'baseswap'.
        key_parts = key.replace('-', '_').split('_')
        if dex_id == key or dex_id == key_parts[0]:
            possible_matches.append(info)

    if not possible_matches:
        print(f"DEBUG: No router match found for dex_id '{dex_id}'. Available router keys: {list(routers.keys())}")
        return None

    if len(possible_matches) == 1:
        return possible_matches[0]

    # If multiple matches are found (e.g., uniswap_v2 and uniswap_v3 for 'uniswap'),
    # prefer the one with the highest version number.
    print(f"DEBUG: Found multiple possible routers for '{dex_id}'. Selecting highest version.")
    possible_matches.sort(key=lambda x: x.get('version', 0), reverse=True)
    return possible_matches[0]

def check_and_approve_token(token_address, spender_address, amount_to_approve_wei):
    if not account or not token_address or not spender_address:
        return
    
    token_contract = w3.eth.contract(address=token_address, abi=ERC20_ABI)
    
    print(f"Checking allowance for {spender_address} to spend {token_address}...")
    allowance = token_contract.functions.allowance(account.address, spender_address).call()
    
    if allowance < amount_to_approve_wei:
        print(f"Allowance is {allowance}. Need {amount_to_approve_wei}. Approving...")
        
        try:
            # --- Two-step approval for safety ---
            # If allowance is not 0, some tokens require resetting it to 0 before setting a new value.
            if allowance > 0:
                print("  - Current allowance is non-zero. Resetting to 0 first to avoid 'unsafe allowance' errors...")
                
                max_priority_fee_reset = w3.eth.max_priority_fee
                base_fee_reset = w3.eth.get_block('latest')['baseFeePerGas']
                max_fee_per_gas_reset = base_fee_reset * 2 + max_priority_fee_reset

                reset_payload = {
                    'from': account.address,
                    'nonce': w3.eth.get_transaction_count(account.address),
                    'maxFeePerGas': max_fee_per_gas_reset,
                    'maxPriorityFeePerGas': max_priority_fee_reset,
                    'chainId': w3.eth.chain_id
                }
                reset_gas_estimate = token_contract.functions.approve(spender_address, 0).estimate_gas(reset_payload)
                reset_payload['gas'] = min(int(reset_gas_estimate * 1.2), MAX_GAS_LIMIT)
                
                reset_txn = token_contract.functions.approve(spender_address, 0).build_transaction(reset_payload)
                signed_reset_txn = w3.eth.account.sign_transaction(reset_txn, PRIVATE_KEY)
                reset_tx_hash = w3.eth.send_raw_transaction(signed_reset_txn.raw_transaction)
                
                print(f"  - Sent reset approval (to 0). Hash: {reset_tx_hash.hex()}. Waiting for confirmation...")
                w3.eth.wait_for_transaction_receipt(reset_tx_hash)
                print("  - Allowance reset to 0 successfully.")
                time.sleep(2) # Give the node a moment to sync state

            # --- Approve the new amount ---
            print(f"  - Now approving the new amount: {amount_to_approve_wei}")
            max_priority_fee = w3.eth.max_priority_fee
            base_fee = w3.eth.get_block('latest')['baseFeePerGas']
            max_fee_per_gas = base_fee * 2 + max_priority_fee

            # Get the new nonce after the potential reset transaction
            current_nonce = w3.eth.get_transaction_count(account.address)

            approve_payload = {
                'from': account.address,
                'nonce': current_nonce,
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
