import time
from config import w3, account, PRIVATE_KEY, MAX_GAS_LIMIT
from abi import ERC20_ABI

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
