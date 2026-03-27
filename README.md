# Blockchain Arbitrage Bot

Atomic DEX arbitrage bot for the Base network. Discovers pools on-chain, monitors price spreads across multiple DEXes, and executes profitable trades via a smart contract that reverts if not profitable (you only lose ~$0.001 gas on failed attempts).

Supports: Uniswap V2/V3, PancakeSwap V3, Aerodrome/Solidly, BaseSwap, SushiSwap, AlienBase, Equalizer, and more.

---

## How It Works

1. **Pool discovery** — queries factory contracts on-chain for each configured DEX + token
2. **Price polling** — reads on-chain prices every 0.5s (V2 reserves, V3 slot0, Solidly prices)
3. **Spread detection** — compares prices across pools, accounting for fees
4. **Simulation** — calls the contract via `eth_call` (free, no gas) to check profitability
5. **Execution** — if simulation passes, sends a real transaction. Buy + sell happen atomically in one tx. If the trade isn't profitable, the contract reverts and you lose only gas (~$0.001 on Base).

---

## Setup

### Prerequisites

- Python 3.10+
- A Base network RPC endpoint (Alchemy, Infura, QuickNode, etc.)
- A wallet with some ETH (for gas) and WETH (for trading) on Base

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure environment

Copy the example config and fill in your values:

```bash
cp .env.example .env
```

Edit `.env` with your settings:

| Variable | Description |
|----------|-------------|
| `BASE_RPC_URL` | WebSocket or HTTP RPC URL for Base network |
| `PRIVATE_KEY` | Your wallet private key (starts with `0x`). **Keep secret.** |
| `BASE_CURRENCY_ADDRESS` | Base token address (WETH on Base: `0x4200000000000000000000000000000000000006`) |
| `TOKEN_ADDRESSES` | Tokens to monitor, format: `NAME:0xAddress,NAME2:0xAddress2` |
| `TRADE_AMOUNT_BASE_TOKEN` | Amount of WETH per trade (e.g., `0.001` for 0.001 WETH) |
| `MIN_SPREAD_PERCENT` | Minimum spread to trigger a trade (default: `1.0`) |
| `SLIPPAGE_TOLERANCE_PERCENT` | Max slippage tolerance (default: `1.0`) |
| `MAX_GAS_LIMIT` | Gas limit for transactions (default: `500000`) |
| `DEX_ROUTERS` | JSON dict of DEX router configs (see `.env.example`) |
| `ON_CHAIN_POLL_INTERVAL` | Seconds between price polls (default: `0.5`) |
| `MAX_PRICE_IMPACT_PCT` | Max price impact per pool (default: `1.0`) |
| `ARB_CONTRACT_ADDRESS` | Deployed contract address (set after step 3) |

### 3. Deploy the smart contract

The bot uses an on-chain contract for atomic execution. Deploy it:

```bash
# Compile the Solidity contract
python deploy.py compile

# Deploy to Base (uses your PRIVATE_KEY from .env)
python deploy.py deploy
```

This will print the deployed contract address. Add it to your `.env`:

```
ARB_CONTRACT_ADDRESS=0x_your_deployed_contract_address
```

### 4. Approve routers

The contract needs token approvals for each DEX router. Run this once (and again if you add new tokens or DEXes):

```bash
python deploy.py approve
```

### 5. Fund the contract

Send WETH to your contract address. This is the trading capital. The amount should match or exceed your `TRADE_AMOUNT_BASE_TOKEN`.

You can send WETH via any wallet (MetaMask, etc.) or via cast/web3:

```python
# Example: send 0.01 WETH to the contract
from config import w3, account, PRIVATE_KEY
weth = w3.eth.contract(address="0x4200000000000000000000000000000000000006", abi=[{"inputs":[{"name":"to","type":"address"},{"name":"amount","type":"uint256"}],"name":"transfer","outputs":[{"name":"","type":"bool"}],"type":"function"}])
tx = weth.functions.transfer("YOUR_CONTRACT_ADDRESS", w3.to_wei(0.01, 'ether')).build_transaction({
    'from': account.address,
    'nonce': w3.eth.get_transaction_count(account.address),
    'gas': 60000,
    'maxFeePerGas': w3.eth.gas_price * 2,
    'maxPriorityFeePerGas': w3.eth.max_priority_fee,
})
signed = w3.eth.account.sign_transaction(tx, PRIVATE_KEY)
w3.eth.send_raw_transaction(signed.raw_transaction)
```

### 6. Run the bot

```bash
python main.py
```

The bot will:
- Discover pools on-chain for your configured tokens
- Poll prices every 0.5s
- Simulate trades for free via `eth_call`
- Execute only profitable trades atomically

### 7. (Optional) Web dashboard

```bash
streamlit run ui.py
```

---

## Withdrawing Profits

To withdraw tokens from the contract back to your wallet:

```python
from config import w3, account, PRIVATE_KEY, ARB_CONTRACT_ADDRESS, ARB_CONTRACT_ABI
contract = w3.eth.contract(address=ARB_CONTRACT_ADDRESS, abi=ARB_CONTRACT_ABI)

# Withdraw WETH
tx = contract.functions.withdraw("0x4200000000000000000000000000000000000006").build_transaction({
    'from': account.address,
    'nonce': w3.eth.get_transaction_count(account.address),
    'gas': 100000,
    'maxFeePerGas': w3.eth.gas_price * 2,
    'maxPriorityFeePerGas': w3.eth.max_priority_fee,
})
signed = w3.eth.account.sign_transaction(tx, PRIVATE_KEY)
w3.eth.send_raw_transaction(signed.raw_transaction)
```

---

## Architecture

```
main.py              Entry point: discovery → polling → trade execution
config.py            Environment config, Web3 setup, constants
dex_utils.py         Pool discovery, on-chain pricing, token approvals, trade sizing
trading.py           Atomic + EOA trade execution, eth_call simulation
abi.py               Contract ABIs for all supported DEXes
deploy.py            Smart contract compilation and deployment
contracts/           Solidity source + compiled artifacts
ui.py                Streamlit dashboard
```

## Supported DEXes

| DEX | Type | Trading | Pricing |
|-----|------|---------|---------|
| Uniswap V2 | V2 | Atomic | getReserves |
| Uniswap V3 | V3 | Atomic | slot0 |
| PancakeSwap V3 | V3 | Atomic | slot0 |
| Aerodrome | Solidly | Atomic | prices() |
| BaseSwap V2/V3 | V2/V3 | Atomic | getReserves/slot0 |
| SushiSwap V2 | V2 | Atomic | getReserves |
| AlienBase | V2 | Atomic | getReserves |
| Equalizer V3 | V3 | Atomic | slot0 |
| Balancer V2 | Balancer | EOA fallback | Not yet |
| Swaap | Balancer | EOA fallback | Not yet |
| 1inch V6 | Aggregator | EOA fallback | Not yet |

## Running Without the Contract

If `ARB_CONTRACT_ADDRESS` is not set, the bot falls back to EOA trading (2 separate transactions). This is less safe but works for testing.

## Running Tests

```bash
pytest tests/
```

---

## Security Notes

- **Private key**: stored in `.env`, never committed to git. Add `.env` to `.gitignore`.
- **Contract**: all functions are `onlyOwner`. Only your wallet can call `executeArb`, `withdraw`, or `approveRouter`.
- **Unverified contract**: bytecode is on-chain but source is not published. This is intentional — your edge is in the off-chain detection logic, not the contract.
- **Honeypot protection**: the `eth_call` simulation catches tokens that can't be sold. Failed tokens are cached and skipped.
