"""
Compile and deploy the ArbitrageExecutor contract.

Usage:
    python deploy.py compile          # Compile only, save ABI + bytecode
    python deploy.py deploy           # Compile + deploy to chain
    python deploy.py approve          # Approve all routers for all tokens on existing contract
"""
import sys
import os
import json
import time
import logging
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(message)s")

# --- Compilation ---

SOLC_VERSION = "0.8.20"
CONTRACT_SRC = Path(__file__).parent / "contracts" / "ArbitrageExecutor.sol"
BUILD_DIR = Path(__file__).parent / "contracts" / "build"


def compile_contract():
    """Compile the Solidity contract using py-solc-x."""
    import solcx

    # Install solc if not present
    installed = [str(v) for v in solcx.get_installed_solc_versions()]
    if SOLC_VERSION not in installed:
        logging.info(f"Installing solc {SOLC_VERSION}...")
        solcx.install_solc(SOLC_VERSION)

    logging.info(f"Compiling {CONTRACT_SRC}...")
    source = CONTRACT_SRC.read_text(encoding="utf-8")

    compiled = solcx.compile_source(
        source,
        output_values=["abi", "bin"],
        solc_version=SOLC_VERSION,
    )

    # Extract the main contract
    contract_key = None
    for key in compiled:
        if "ArbitrageExecutor" in key:
            contract_key = key
            break

    if not contract_key:
        raise RuntimeError(f"ArbitrageExecutor not found in compiled output. Keys: {list(compiled.keys())}")

    abi = compiled[contract_key]["abi"]
    bytecode = compiled[contract_key]["bin"]

    # Save to build directory
    BUILD_DIR.mkdir(parents=True, exist_ok=True)
    (BUILD_DIR / "ArbitrageExecutor.abi.json").write_text(json.dumps(abi, indent=2))
    (BUILD_DIR / "ArbitrageExecutor.bin").write_text(bytecode)

    logging.info(f"Compiled successfully. ABI and bytecode saved to {BUILD_DIR}")
    return abi, bytecode


def load_compiled():
    """Load previously compiled ABI and bytecode."""
    abi_path = BUILD_DIR / "ArbitrageExecutor.abi.json"
    bin_path = BUILD_DIR / "ArbitrageExecutor.bin"

    if not abi_path.exists() or not bin_path.exists():
        logging.info("No compiled artifacts found, compiling...")
        return compile_contract()

    abi = json.loads(abi_path.read_text())
    bytecode = bin_path.read_text()
    return abi, bytecode


# --- Deployment ---

def deploy_contract():
    """Deploy the contract to the configured chain."""
    from config import w3, account, PRIVATE_KEY, MAX_GAS_LIMIT

    if not account:
        raise RuntimeError("No account configured. Set PRIVATE_KEY in .env")

    abi, bytecode = compile_contract()

    logging.info(f"Deploying from {account.address}...")
    logging.info(f"Chain ID: {w3.eth.chain_id}")

    contract = w3.eth.contract(abi=abi, bytecode=bytecode)

    # Build deploy transaction
    max_priority_fee = w3.eth.max_priority_fee
    base_fee = w3.eth.get_block('latest')['baseFeePerGas']
    max_fee = base_fee * 2 + max_priority_fee
    nonce = w3.eth.get_transaction_count(account.address)

    tx = contract.constructor().build_transaction({
        'from': account.address,
        'nonce': nonce,
        'maxFeePerGas': max_fee,
        'maxPriorityFeePerGas': max_priority_fee,
        'gas': MAX_GAS_LIMIT,
        'chainId': w3.eth.chain_id,
    })

    signed = w3.eth.account.sign_transaction(tx, PRIVATE_KEY)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    logging.info(f"Deploy tx sent: {tx_hash.hex()}")

    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)

    if receipt['status'] == 0:
        raise RuntimeError("Deployment transaction reverted!")

    contract_address = receipt['contractAddress']
    logging.info(f"Contract deployed at: {contract_address}")
    logging.info(f"Gas used: {receipt['gasUsed']}")
    logging.info(f"\nAdd to your .env file:")
    logging.info(f'ARB_CONTRACT_ADDRESS={contract_address}')

    return contract_address


def approve_routers():
    """Approve all configured routers for base currency + all watched tokens."""
    from config import w3, account, PRIVATE_KEY, MAX_GAS_LIMIT, DEX_ROUTERS, BASE_CURRENCY_ADDRESS, TOKEN_ADDRESSES

    contract_address = os.getenv("ARB_CONTRACT_ADDRESS")
    if not contract_address:
        raise RuntimeError("ARB_CONTRACT_ADDRESS not set in .env")

    abi, _ = load_compiled()
    contract_address = w3.to_checksum_address(contract_address)
    arb_contract = w3.eth.contract(address=contract_address, abi=abi)

    # Collect all tokens to approve
    tokens_to_approve = {BASE_CURRENCY_ADDRESS}
    for name, addr in TOKEN_ADDRESSES.items():
        tokens_to_approve.add(addr)

    # Collect all router addresses
    routers = set()
    for dex_key, info in DEX_ROUTERS.items():
        routers.add(info['address'])

    logging.info(f"Approving {len(tokens_to_approve)} tokens for {len(routers)} routers...")

    for token in tokens_to_approve:
        for router in routers:
            logging.info(f"  Approving {token} for router {router}...")
            max_priority_fee = w3.eth.max_priority_fee
            base_fee = w3.eth.get_block('latest')['baseFeePerGas']
            max_fee = base_fee * 2 + max_priority_fee
            nonce = w3.eth.get_transaction_count(account.address)

            tx = arb_contract.functions.approveRouter(token, router).build_transaction({
                'from': account.address,
                'nonce': nonce,
                'maxFeePerGas': max_fee,
                'maxPriorityFeePerGas': max_priority_fee,
                'gas': 100000,
                'chainId': w3.eth.chain_id,
            })
            signed = w3.eth.account.sign_transaction(tx, PRIVATE_KEY)
            tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
            receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60)
            if receipt['status'] == 0:
                logging.warning(f"  Approval failed for {token} -> {router}")
            else:
                logging.info(f"  Approved. Gas: {receipt['gasUsed']}")
            time.sleep(0.5)

    logging.info("All approvals complete.")


# --- CLI ---

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python deploy.py [compile|deploy|approve]")
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "compile":
        compile_contract()
    elif cmd == "deploy":
        deploy_contract()
    elif cmd == "approve":
        from dotenv import load_dotenv
        load_dotenv()
        approve_routers()
    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)
