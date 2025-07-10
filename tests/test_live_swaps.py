import pytest
import logging
import time
import os
from dotenv import load_dotenv

# Load environment variables from .env file for local testing.
# Make sure your .env file is correctly set up with PRIVATE_KEY, BOT_WALLET, and BASE_TESTNET_RPC_URL.
load_dotenv()

from config import w3, account, PRIVATE_KEY, MAX_GAS_LIMIT, DEX_ROUTERS, BASE_CURRENCY_ADDRESS
from trading import (
    _prepare_uniswap_v2_swap,
    _prepare_uniswap_v3_swap,
    _prepare_solidly_swap,
    _parse_receipt_for_amount_out,
    resilient_rpc_call
)
from abi import ERC20_ABI
from dex_utils import find_router_info

# Configure logging for the test
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- Test Configuration ---
# WARNING: This test executes real transactions on the Base network.
# Ensure the account in your .env file has a small amount of ETH for gas
# and ~0.001 WETH for the test swaps.

# Using WETH and USDC on Base network
# The user requested swapping to ETH. However, swapping WETH to ETH is an 'unwrap' operation
# on the WETH contract, not a DEX trade. To test the DEX swapping functions (_prepare_*_swap),
# this test uses USDC as the intermediate token (WETH -> USDC -> WETH).
USDC_ADDRESS = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913" 
TRADE_AMOUNT_WETH_FLOAT = 0.001

# --- Test DEX Setups ---
# This test assumes the DEX routers are defined in your .env file and loaded into DEX_ROUTERS.
DEX_TEST_CONFIG = [
    # BaseSwap is a Uniswap V2 fork
    pytest.param("baseswap", "0xF8FDB2c2b54436533924f36EfeC2D9051873b320", id="baseswap_v2"),
    # Aerodrome is a Solidly fork
    pytest.param("aerodrome", "0x85d5223a311AF83262512316B319b1659a061452", id="aerodrome_v2_solidly"), # Volatile Pool
    # Uniswap V3 - pair address is not strictly needed as the function can find it.
    pytest.param("uniswap_v3", None, id="uniswap_v3"),
]

# Conditional skip: only run if a private key is provided.
PRIVATE_KEY_EXISTS = bool(os.getenv("PRIVATE_KEY") and os.getenv("PRIVATE_KEY") != "0xyour_private_key")

def _approve_token(token_address: str, spender_address: str):
    """Approves the spender to use the maximum amount of a token from the bot's wallet."""
    token_contract = w3.eth.contract(address=token_address, abi=ERC20_ABI)
    allowance = resilient_rpc_call(lambda: token_contract.functions.allowance(account.address, spender_address).call())
    
    # Approve max amount (2^256 - 1)
    max_uint256 = 2**256 - 1
    if allowance < max_uint256 / 2: # Check if allowance is not already high
        logging.info(f"  - Approving {spender_address} to spend token {token_address}...")
        
        max_priority_fee = resilient_rpc_call(lambda: w3.eth.max_priority_fee)
        latest_block = resilient_rpc_call(lambda: w3.eth.get_block('latest'))
        base_fee = latest_block['baseFeePerGas']
        max_fee_per_gas = base_fee * 2 + max_priority_fee
        
        approve_tx = token_contract.functions.approve(
            spender_address,
            max_uint256
        ).build_transaction({
            'from': account.address,
            'nonce': resilient_rpc_call(lambda: w3.eth.get_transaction_count(account.address)),
            'gas': 100000,
            'maxFeePerGas': max_fee_per_gas,
            'maxPriorityFeePerGas': max_priority_fee,
            'chainId': resilient_rpc_call(lambda: w3.eth.chain_id)
        })
        
        signed_tx = w3.eth.account.sign_transaction(approve_tx, PRIVATE_KEY)
        tx_hash = w3.eth.send_raw_transaction(signed_tx.raw_transaction)
        logging.info(f"  - Approval Tx sent: {tx_hash.hex()}. Waiting for receipt...")
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
        
        if receipt.status == 1:
            logging.info("  - Approval successful.")
        else:
            pytest.fail(f"Approval transaction failed for token {token_address} on spender {spender_address}")
    else:
        logging.info(f"  - Token {token_address} already approved for spender {spender_address}.")

def _perform_swap(dex_name: str, token_in_addr: str, token_out_addr: str, amount_in_wei: int, pair_address: str = None):
    """Helper function to perform a single swap, from preparation to execution."""
    logging.info(f"\n--- Swapping on {dex_name}: {token_in_addr[:10]}... -> {token_out_addr[:10]}... ---")

    router_info = find_router_info(dex_name, DEX_ROUTERS)
    if not router_info:
        pytest.fail(f"Router info for '{dex_name}' not found in DEX_ROUTERS. Check your .env config.")
    
    _approve_token(token_in_addr, router_info['address'])

    # Prepare the swap function call based on DEX type and version
    router_type = router_info.get('type', 'uniswapv2')
    swap_function = None

    logging.info(f"  - Preparing swap for {dex_name} (type: {router_type}, version: {router_info.get('version')})")
    if router_type == 'solidly' and router_info['version'] == 2:
        swap_function, _ = _prepare_solidly_swap(dex_name, router_info, amount_in_wei, token_in_addr, token_out_addr, pair_address=pair_address)
    elif router_type == 'uniswapv2' and router_info['version'] == 2:
        swap_function, _ = _prepare_uniswap_v2_swap(router_info, amount_in_wei, [token_in_addr, token_out_addr], pair_address=pair_address)
    elif router_info['version'] == 3:
        swap_function, _ = _prepare_uniswap_v3_swap(dex_name, router_info, amount_in_wei, token_in_addr, token_out_addr, pair_address=pair_address)
    else:
        pytest.fail(f"Unsupported DEX for live testing: type '{router_type}' v{router_info.get('version')}")

    if not swap_function:
        pytest.fail("Failed to prepare swap function.")

    # Build and execute the transaction
    logging.info("  - Building and sending transaction...")
    max_priority_fee = resilient_rpc_call(lambda: w3.eth.max_priority_fee)
    latest_block = resilient_rpc_call(lambda: w3.eth.get_block('latest'))
    base_fee = latest_block['baseFeePerGas']
    max_fee_per_gas = base_fee * 2 + max_priority_fee
    
    tx_payload = {
        'from': account.address,
        'nonce': resilient_rpc_call(lambda: w3.eth.get_transaction_count(account.address)),
        'maxFeePerGas': max_fee_per_gas,
        'maxPriorityFeePerGas': max_priority_fee,
        'gas': MAX_GAS_LIMIT,
        'chainId': resilient_rpc_call(lambda: w3.eth.chain_id)
    }
    
    swap_tx = swap_function.build_transaction(tx_payload)
    signed_tx = w3.eth.account.sign_transaction(swap_tx, PRIVATE_KEY)
    tx_hash = w3.eth.send_raw_transaction(signed_tx.raw_transaction)
    logging.info(f"  - Tx sent: {tx_hash.hex()}. Waiting for receipt...")
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)

    if receipt.status != 1:
        pytest.fail(f"Swap transaction failed on {dex_name}. Tx hash: {tx_hash.hex()}")

    logging.info("  - Swap successful! Parsing receipt for amount out...")
    token_out_contract = w3.eth.contract(address=token_out_addr, abi=ERC20_ABI)
    token_out_decimals = resilient_rpc_call(lambda: token_out_contract.functions.decimals().call())
    
    amount_out_wei = _parse_receipt_for_amount_out(receipt, router_info, dex_name, token_out_addr, token_out_decimals)
    
    if amount_out_wei == 0:
        pytest.fail(f"Could not parse amount out from swap receipt on {dex_name}.")
    
    logging.info(f"  - Received {amount_out_wei / (10**token_out_decimals):.6f} of token {token_out_addr[:10]}...")
    return amount_out_wei

@pytest.mark.skipif(not PRIVATE_KEY_EXISTS, reason="Requires PRIVATE_KEY in .env for live transactions")
@pytest.mark.parametrize("dex_name, pair_address", DEX_TEST_CONFIG)
def test_dex_swap_cycle(dex_name, pair_address):
    """
    Performs a real swap cycle (WETH -> USDC -> WETH) on a given DEX.
    This test verifies that the swap preparation functions and transaction
    execution logic are working correctly on-chain.
    """
    logging.info(f"\n{'='*20} Starting Test: Swap Cycle on {dex_name.upper()} {'='*20}")

    weth_contract = w3.eth.contract(address=BASE_CURRENCY_ADDRESS, abi=ERC20_ABI)
    weth_decimals = resilient_rpc_call(lambda: weth_contract.functions.decimals().call())
    amount_in_wei = int(TRADE_AMOUNT_WETH_FLOAT * (10**weth_decimals))

    # 1. Get initial WETH balance
    initial_weth_balance = resilient_rpc_call(lambda: weth_contract.functions.balanceOf(account.address).call())
    logging.info(f"Initial WETH balance: {initial_weth_balance / (10**weth_decimals):.6f}")
    assert initial_weth_balance >= amount_in_wei, "Not enough WETH to run test."

    # 2. Swap WETH for USDC
    usdc_received_wei = _perform_swap(
        dex_name,
        BASE_CURRENCY_ADDRESS,
        USDC_ADDRESS,
        amount_in_wei,
        pair_address
    )
    assert usdc_received_wei > 0

    # 3. Swap USDC back to WETH
    final_weth_wei = _perform_swap(
        dex_name,
        USDC_ADDRESS,
        BASE_CURRENCY_ADDRESS,
        usdc_received_wei,
        pair_address
    )
    assert final_weth_wei > 0

    # 4. Check final WETH balance and log results
    final_weth_balance = resilient_rpc_call(lambda: weth_contract.functions.balanceOf(account.address).call())
    logging.info(f"Final WETH balance:   {final_weth_balance / (10**weth_decimals):.6f}")

    # The final amount should be slightly less than the initial amount due to fees and slippage
    # but greater than the initial amount minus the full trade size (a sanity check for total loss)
    assert final_weth_balance < initial_weth_balance
    assert final_weth_balance > initial_weth_balance - amount_in_wei

    loss_percent = (1 - (final_weth_balance / initial_weth_balance)) * 100 if initial_weth_balance > 0 else 0
    logging.info(f"Loss from round-trip swap on {dex_name}: {loss_percent:.4f}%")
    logging.info(f"{'='*20} Test Finished: {dex_name.upper()} {'='*20}")
