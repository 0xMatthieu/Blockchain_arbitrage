# Blockchain Arbitrage Bot

This project provides a simple arbitrage trading bot that monitors prices across multiple DEXes using the DexScreener API and can optionally execute trades via Web3.  Configuration is provided through environment variables placed in a `.env` file.

## Environment Variables

| Variable | Description |
|----------|-------------|
| `TOKEN_ADDRESS` | Address of the token to monitor for arbitrage opportunities. |
| `MIN_LIQUIDITY_USD` | Minimum pool liquidity in USD required for consideration. Defaults to `1000`. |
| `MIN_SPREAD_PERCENT` | Minimum price spread between DEXes before a trade is attempted. Defaults to `1.0`. |
| `BASE_RPC_URL` | Websocket RPC URL for the chain you are trading on. |
| `PRIVATE_KEY` | Private key of the wallet performing trades. **Keep this secret.** |
| `RPC_MAX_RETRIES` | Number of retries for RPC calls. Defaults to `5`. |
| `RPC_BACKOFF_FACTOR` | Seconds to wait before the first retry when an RPC call fails. Defaults to `0.5`. |
| `BASE_CURRENCY_ADDRESS` | Address of the base currency used to buy the target token (e.g. WETH). |
| `TRADE_AMOUNT_BASE_TOKEN` | Amount of the base currency to use for each trade. |
| `SLIPPAGE_TOLERANCE_PERCENT` | Maximum slippage percentage allowed for swaps. Defaults to `1.0`. |
| `MAX_GAS_LIMIT` | Upper gas limit used when sending transactions. Defaults to `500000`. |
| `DEX_ROUTERS` | JSON object describing router addresses and versions for each supported DEX. For V3 routers include `factory` and `quoter` fields. |
| `API_CALLS_PER_MINUTE` | API rate limit used to compute polling interval. Defaults to `280`. |
| `TRADE_COOLDOWN_SECONDS` | Cooldown period between trade attempts. Defaults to `60`. |

See `.env.example` for a fully documented example configuration.

## Usage

1. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
2. Create a `.env` file based on `.env.example` and fill in the required values.
3. Run the bot:
   ```bash
   python main.py
   ```

### Running Tests

Unit tests use `pytest`.  Execute them with:

```bash
pytest
```
