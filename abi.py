# Minimal ABI for ERC20 tokens to check balance, allowance, approve and parse Transfer events
ERC20_ABI = [
    {
        "constant": True,
        "inputs": [],
        "name": "name",
        "outputs": [{"name": "", "type": "string"}],
        "type": "function"
    },
    {
        "constant": True,
        "inputs": [],
        "name": "symbol",
        "outputs": [{"name": "", "type": "string"}],
        "type": "function"
    },
    {
        "constant": True,
        "inputs": [{"name": "_owner", "type": "address"}, {"name": "_spender", "type": "address"}],
        "name": "allowance",
        "outputs": [{"name": "remaining", "type": "uint256"}],
        "type": "function"
    },
    {
        "constant": False,
        "inputs": [{"name": "_spender", "type": "address"}, {"name": "_value", "type": "uint256"}],
        "name": "approve",
        "outputs": [{"name": "success", "type": "bool"}],
        "type": "function"
    },
    {
        "constant": True,
        "inputs": [],
        "name": "decimals",
        "outputs": [{"name": "", "type": "uint8"}],
        "type": "function"
    },
    {
        "constant": True,
        "inputs": [{"name": "_owner", "type": "address"}],
        "name": "balanceOf",
        "outputs": [{"name": "balance", "type": "uint256"}],
        "type": "function"
    },
    {
        "constant": True,
        "inputs": [],
        "name": "totalSupply",
        "outputs": [{"name": "", "type": "uint256"}],
        "type": "function"
    },
    {
        "anonymous": False,
        "inputs": [
            {"indexed": True, "name": "from", "type": "address"},
            {"indexed": True, "name": "to", "type": "address"},
            {"indexed": False, "name": "value", "type": "uint256"}
        ],
        "name": "Transfer",
        "type": "event"
    }
]

# Minimal ABI for a Uniswap V2-style router
UNISWAP_V2_ROUTER_ABI = [
    {
        "inputs": [
            {"internalType": "uint256", "name": "amountIn", "type": "uint256"},
            {"internalType": "uint256", "name": "amountOutMin", "type": "uint256"},
            {"internalType": "address[]", "name": "path", "type": "address[]"},
            {"internalType": "address", "name": "to", "type": "address"},
            {"internalType": "uint256", "name": "deadline", "type": "uint256"}
        ],
        "name": "swapExactTokensForTokens",
        "outputs": [{"internalType": "uint256[]", "name": "amounts", "type": "uint256[]"}],
        "stateMutability": "nonpayable",
        "type": "function"
    },
    {
        "inputs": [
            {"internalType": "uint256", "name": "amountIn", "type": "uint256"},
            {"internalType": "address[]", "name": "path", "type": "address[]"}
        ],
        "name": "getAmountsOut",
        "outputs": [{"internalType": "uint256[]", "name": "amounts", "type": "uint256[]"}],
        "stateMutability": "view",
        "type": "function"
    },
    {
        "inputs": [
            {"internalType": "uint256", "name": "amountOut", "type": "uint256"},
            {"internalType": "address[]", "name": "path", "type": "address[]"}
        ],
        "name": "getAmountsIn",
        "outputs": [{"internalType": "uint256[]", "name": "amounts", "type": "uint256[]"}],
        "stateMutability": "view",
        "type": "function"
    }
]

# Minimal ABI for a Uniswap V3-style router
UNISWAP_V3_ROUTER_ABI = [
    {
        "inputs": [
            {
                "components": [
                    {"internalType": "address", "name": "tokenIn", "type": "address"},
                    {"internalType": "address", "name": "tokenOut", "type": "address"},
                    {"internalType": "uint24", "name": "fee", "type": "uint24"},
                    {"internalType": "address", "name": "recipient", "type": "address"},
                    {"internalType": "uint256", "name": "amountIn", "type": "uint256"},
                    {"internalType": "uint256", "name": "amountOutMinimum", "type": "uint256"},
                    {"internalType": "uint160", "name": "sqrtPriceLimitX96", "type": "uint160"}
                ],
                "internalType": "struct ISwapRouter.ExactInputSingleParams",
                "name": "params",
                "type": "tuple"
            }
        ],
        "name": "exactInputSingle",
        "outputs": [{"internalType": "uint256", "name": "amountOut", "type": "uint256"}],
        "stateMutability": "payable",
        "type": "function"
    }
]

# Minimal ABI for a Uniswap V3-style QuoterV2
UNISWAP_V3_QUOTER_ABI = [
    {
        "name": "quoteExactInputSingle",
        "type": "function",
        "stateMutability": "nonpayable",
        "inputs": [{
            "name": "params",
            "type": "tuple",
            "components": [
                {"name": "tokenIn",          "type": "address"},
                {"name": "tokenOut",         "type": "address"},
                {"name": "fee",              "type": "uint24"},
                {"name": "amountIn",         "type": "uint256"},
                {"name": "sqrtPriceLimitX96","type": "uint160"}
            ]
        }],
        "outputs": [
            {"name": "amountOut",          "type": "uint256"},
            {"name": "sqrtPriceX96After",  "type": "uint160"},
            {"name": "ticksCrossed",       "type": "uint32"},
            {"name": "gasEstimate",        "type": "uint256"}
        ]
    },
    {
        "name": "quoteExactInput",
        "type": "function",
        "stateMutability": "nonpayable",
        "inputs": [
            {"internalType": "bytes", "name": "path", "type": "bytes"},
            {"internalType": "uint256", "name": "amountIn", "type": "uint256"}
        ],
        "outputs": [
            {"name": "amountOut", "type": "uint256"}
        ]
    }
]

# Minimal ABI for a Uniswap V3 pool to parse Swap events and check slot0
UNISWAP_V3_POOL_ABI = [
    {
        "name": "swap",
        "type": "function",
        "stateMutability": "nonpayable",
        "inputs": [
            {"internalType": "address", "name": "recipient", "type": "address"},
            {"internalType": "bool", "name": "zeroForOne", "type": "bool"},
            {"internalType": "int256", "name": "amountSpecified", "type": "int256"},
            {"internalType": "uint160", "name": "sqrtPriceLimitX96", "type": "uint160"},
            {"internalType": "bytes", "name": "data", "type": "bytes"}
        ],
        "outputs": [
            {"internalType": "int256", "name": "amount0", "type": "int256"},
            {"internalType": "int256", "name": "amount1", "type": "int256"}
        ]
    },
    {
        "inputs": [],
        "name": "slot0",
        "outputs": [
            {"internalType": "uint160", "name": "sqrtPriceX96", "type": "uint160"},
            {"internalType": "int24", "name": "tick", "type": "int24"},
            {"internalType": "uint16", "name": "observationIndex", "type": "uint16"},
            {"internalType": "uint16", "name": "observationCardinality", "type": "uint16"},
            {"internalType": "uint16", "name": "observationCardinalityNext", "type": "uint16"},
            {"internalType": "uint8", "name": "feeProtocol", "type": "uint8"},
            {"internalType": "bool", "name": "unlocked", "type": "bool"}
        ],
        "stateMutability": "view",
        "type": "function"
    },
    {
        "name": "liquidity",
        "type": "function",
        "stateMutability": "view",
        "inputs": [],
        "outputs": [
            {"name": "", "type": "uint128"}
        ]
    },
    {
        "name": "fee",
        "type": "function",
        "stateMutability": "view",
        "inputs": [],
        "outputs": [{"name": "", "type": "uint24"}]
    }
]

# Minimal ABI for a Uniswap V3 factory to find pools
UNISWAP_V3_FACTORY_ABI = [
    {
        "inputs": [
            {"internalType": "address", "name": "tokenA", "type": "address"},
            {"internalType": "address", "name": "tokenB", "type": "address"},
            {"internalType": "uint24", "name": "fee", "type": "uint24"}
        ],
        "name": "getPool",
        "outputs": [{"internalType": "address", "name": "pool", "type": "address"}],
        "stateMutability": "view",
        "type": "function"
    }
]

# Minimal ABI for a Pancake V3-style router with deadline support
PANCAKE_V3_ROUTER_ABI = [
    {
        "inputs": [
            {
                "components": [
                    {"internalType": "address", "name": "tokenIn", "type": "address"},
                    {"internalType": "address", "name": "tokenOut", "type": "address"},
                    {"internalType": "uint24", "name": "fee", "type": "uint24"},
                    {"internalType": "address", "name": "recipient", "type": "address"},
                    {"internalType": "uint256", "name": "amountIn", "type": "uint256"},
                    {"internalType": "uint256", "name": "amountOutMinimum", "type": "uint256"},
                    {"internalType": "uint160", "name": "sqrtPriceLimitX96", "type": "uint160"}
                ],
                "internalType": "struct ISwapRouter.ExactInputSingleParams",
                "name": "params",
                "type": "tuple"
            },
            {"internalType": "uint256", "name": "deadline", "type": "uint256"}
        ],
        "name": "exactInputSingle",
        "outputs": [{"internalType": "uint256", "name": "amountOut", "type": "uint256"}],
        "stateMutability": "payable",
        "type": "function"
    }
]

# Minimal ABI for a Pancake V3-style QuoterV2
PANCAKE_V3_QUOTER_ABI = [
    {
        "name": "quoteExactInputSingle",
        "type": "function",
        "stateMutability": "nonpayable",
        "inputs": [{
            "name": "params",
            "type": "tuple",
            "components": [
                {"name": "tokenIn",          "type": "address"},
                {"name": "tokenOut",         "type": "address"},
                {"name": "fee",              "type": "uint24"},
                {"name": "amountIn",         "type": "uint256"},
                {"name": "sqrtPriceLimitX96","type": "uint160"}
            ]
        }],
        "outputs": [
            {"name": "amountOut",          "type": "uint256"},
            {"name": "sqrtPriceX96After",  "type": "uint160"},
            {"name": "ticksCrossed",       "type": "uint32"},
            {"name": "gasEstimate",        "type": "uint256"}
        ]
    },
    {
        "name": "quoteExactInput",
        "type": "function",
        "stateMutability": "nonpayable",
        "inputs": [
            {"internalType": "bytes", "name": "path", "type": "bytes"},
            {"internalType": "uint256", "name": "amountIn", "type": "uint256"}
        ],
        "outputs": [
            {"name": "amountOut", "type": "uint256"}
        ]
    }
]

# Minimal ABI for a Pancake V3 pool to parse Swap events and check slot0
PANCAKE_V3_POOL_ABI = [
    {
        "name": "swap",
        "type": "function",
        "stateMutability": "nonpayable",
        "inputs": [
            {"internalType": "address", "name": "recipient", "type": "address"},
            {"internalType": "bool", "name": "zeroForOne", "type": "bool"},
            {"internalType": "int256", "name": "amountSpecified", "type": "int256"},
            {"internalType": "uint160", "name": "sqrtPriceLimitX96", "type": "uint160"},
            {"internalType": "bytes", "name": "data", "type": "bytes"}
        ],
        "outputs": [
            {"internalType": "int256", "name": "amount0", "type": "int256"},
            {"internalType": "int256", "name": "amount1", "type": "int256"}
        ]
    },
    {
        "inputs": [],
        "name": "slot0",
        "outputs": [
            {"internalType": "uint160", "name": "sqrtPriceX96", "type": "uint160"},
            {"internalType": "int24", "name": "tick", "type": "int24"},
            {"internalType": "uint16", "name": "observationIndex", "type": "uint16"},
            {"internalType": "uint16", "name": "observationCardinality", "type": "uint16"},
            {"internalType": "uint16", "name": "observationCardinalityNext", "type": "uint16"},
            {"internalType": "uint8", "name": "feeProtocol", "type": "uint32"},
            {"internalType": "bool", "name": "unlocked", "type": "bool"}
        ],
        "stateMutability": "view",
        "type": "function"
    },
    {
        "name": "liquidity",
        "type": "function",
        "stateMutability": "view",
        "inputs": [],
        "outputs": [
            {"name": "", "type": "uint128"}
        ]
    },
    {
        "name": "fee",
        "type": "function",
        "stateMutability": "view",
        "inputs": [],
        "outputs": [{"name": "", "type": "uint24"}]
    }
]

# Minimal ABI for a Pancake V3 factory to find pools
PANCAKE_V3_FACTORY_ABI = [
    {
        "inputs": [
            {"internalType": "address", "name": "tokenA", "type": "address"},
            {"internalType": "address", "name": "tokenB", "type": "address"},
            {"internalType": "uint24", "name": "fee", "type": "uint24"}
        ],
        "name": "getPool",
        "outputs": [{"internalType": "address", "name": "pool", "type": "address"}],
        "stateMutability": "view",
        "type": "function"
    }
]

# Minimal ABI for a Solidly-style router (e.g., Aerodrome)
SOLIDLY_ROUTER_ABI = [
    {
        "inputs": [
            {"internalType": "uint256", "name": "amountIn", "type": "uint256"},
            {
                "components": [
                    {"internalType": "address", "name": "from", "type": "address"},
                    {"internalType": "address", "name": "to", "type": "address"},
                    {"internalType": "bool", "name": "stable", "type": "bool"},
                    {"internalType": "address", "name": "factory", "type": "address"}
                ],
                "internalType": "struct IRouter.Route[]",
                "name": "routes",
                "type": "tuple[]"
            }
        ],
        "name": "getAmountsOut",
        "outputs": [
            {"internalType": "uint256[]", "name": "amounts", "type": "uint256[]"}
        ],
        "stateMutability": "view",
        "type": "function"
    },
    {
        "inputs": [
            {"internalType": "uint256", "name": "amountIn", "type": "uint256"},
            {"internalType": "uint256", "name": "amountOutMin", "type": "uint256"},
            {
                "components": [
                    {"internalType": "address", "name": "from", "type": "address"},
                    {"internalType": "address", "name": "to", "type": "address"},
                    {"internalType": "bool", "name": "stable", "type": "bool"},
                    {"internalType": "address", "name": "factory", "type": "address"}
                ],
                "internalType": "struct IRouter.Route[]",
                "name": "routes",
                "type": "tuple[]"
            },
            {"internalType": "address", "name": "to", "type": "address"},
            {"internalType": "uint256", "name": "deadline", "type": "uint256"}
        ],
        "name": "swapExactTokensForTokens",
        "outputs": [
            {"internalType": "uint256[]", "name": "amounts", "type": "uint256[]"}
        ],
        "stateMutability": "nonpayable",
        "type": "function"
    }
]

# Minimal ABI for a Solidly-style factory to get pool info
SOLIDLY_FACTORY_ABI = [
    {
        "inputs": [
            {"internalType": "address", "name": "tokenA", "type": "address"},
            {"internalType": "address", "name": "tokenB", "type": "address"},
            {"internalType": "bool", "name": "stable", "type": "bool"}
        ],
        "name": "getPool",
        "outputs": [{"internalType": "address", "name": "pool", "type": "address"}],
        "stateMutability": "view",
        "type": "function"
    }
]

# Minimal ABI for a Solidly/V2-style pair to check reserves and parse Swap events
SOLIDLY_PAIR_ABI = [
    {
        "constant": True,
        "inputs": [],
        "name": "getReserves",
        "outputs": [
            {"internalType": "uint112", "name": "_reserve0", "type": "uint112"},
            {"internalType": "uint112", "name": "_reserve1", "type": "uint112"},
            {"internalType": "uint32", "name": "_blockTimestampLast", "type": "uint32"}
        ],
        "payable": False,
        "stateMutability": "view",
        "type": "function"
    },
    {
        "name": "prices",
        "type": "function",
        "stateMutability": "view",
        "inputs": [
          { "internalType": "address", "name": "tokenIn", "type": "address" },
          { "internalType": "uint256", "name": "amountIn", "type": "uint256" },
          { "internalType": "uint256", "name": "points",   "type": "uint256" }
        ],
        "outputs": [
          { "internalType": "uint256[]", "name": "", "type": "uint256[]" }
        ]
      },
    {
        "anonymous": False,
        "inputs": [
            {"indexed": True, "name": "sender", "type": "address"},
            {"indexed": False, "name": "amount0In", "type": "uint256"},
            {"indexed": False, "name": "amount1In", "type": "uint256"},
            {"indexed": False, "name": "amount0Out", "type": "uint256"},
            {"indexed": False, "name": "amount1Out", "type": "uint256"},
            {"indexed": True, "name": "to", "type": "address"}
        ],
        "name": "Swap",
        "type": "event"
    }
]

# 1inch Aggregation Router V6 – minimal ABI (swap only)
ONEINCH_V6_ROUTER_ABI = [
    {
        "name": "swap",
        "type": "function",
        "stateMutability": "payable",
        "inputs": [
            {"name": "executor", "type": "address"},      # 1inch executor contract
            {
                "components": [
                    {"name": "srcToken",     "type": "address"},
                    {"name": "dstToken",     "type": "address"},
                    {"name": "srcReceiver",  "type": "address"},
                    {"name": "dstReceiver",  "type": "address"},
                    {"name": "amount",       "type": "uint256"},
                    {"name": "minReturn",    "type": "uint256"},
                    {"name": "flags",        "type": "uint256"},
                    {"name": "permit",       "type": "bytes"}
                ],
                "name": "desc",
                "type": "tuple"
            },
            {"name": "data",   "type": "bytes"}           # encoded calls for executor
        ],
        "outputs": [
            {"name": "returnAmount", "type": "uint256"},
            {"name": "spentAmount",  "type": "uint256"}
        ]
    }
]

# ───────────────────────────── ALIENBASE Uniswap-V2 Router ────────────────────
ALIENBASE_V2_ROUTER_ABI = [
    {   # swapExactTokensForTokens
        "inputs": [
            {"name": "amountIn",      "type": "uint256"},
            {"name": "amountOutMin",  "type": "uint256"},
            {"name": "path",          "type": "address[]"},
            {"name": "to",            "type": "address"},
            {"name": "deadline",      "type": "uint256"}
        ],
        "name": "swapExactTokensForTokens",
        "outputs": [{"name": "amounts", "type": "uint256[]"}],
        "stateMutability": "nonpayable",
        "type": "function"
    },
    {   # swapExactETHForTokens
        "inputs": [
            {"name": "amountOutMin",  "type": "uint256"},
            {"name": "path",          "type": "address[]"},
            {"name": "to",            "type": "address"},
            {"name": "deadline",      "type": "uint256"}
        ],
        "name": "swapExactETHForTokens",
        "outputs": [{"name": "amounts", "type": "uint256[]"}],
        "stateMutability": "payable",
        "type": "function"
    },
    {   # getAmountsOut - useful for quoting
        "inputs": [
            {"name": "amountIn", "type": "uint256"},
            {"name": "path",     "type": "address[]"}
        ],
        "name": "getAmountsOut",
        "outputs": [{"name": "amounts", "type": "uint256[]"}],
        "stateMutability": "view",
        "type": "function"
    },
    {   # factory() view
        "inputs": [],
        "name": "factory",
        "outputs": [{"name": "", "type": "address"}],
        "stateMutability": "view",
        "type": "function"
    },
    {   # WETH() view
        "inputs": [],
        "name": "WETH",
        "outputs": [{"name": "", "type": "address"}],
        "stateMutability": "view",
        "type": "function"
    }
]

# ─────────────────────────────── MAVERICK V1/V2 Router ─────────────────────────
# exactInputSingle is the most common path-encoded swap call
MAVERICK_ROUTER_ABI = [
    {
        "name": "exactInputSingle",
        "type": "function",
        "stateMutability": "payable",
        "inputs": [
            {"name": "recipient",         "type": "address"},
            {"name": "pool",              "type": "address"},  # IMaverickV2Pool
            {"name": "tokenAIn",          "type": "bool"},
            {"name": "amountIn",          "type": "uint256"},
            {"name": "amountOutMinimum",  "type": "uint256"}
        ],
        "outputs": [{"name": "amountOut", "type": "uint256"}]
    }
]
# :contentReference[oaicite:2]{index=2}


# Minimal ABI to get a Balancer pool's ID
BALANCER_POOL_ABI = [
    {
        "inputs": [],
        "name": "getPoolId",
        "outputs": [{"internalType": "bytes32", "name": "", "type": "bytes32"}],
        "stateMutability": "view",
        "type": "function"
    }
]

BALANCER_V2_ROUTER_ABI = [
  {
    "inputs": [
      {
        "components": [
          { "internalType": "bytes32", "name": "poolId", "type": "bytes32" },
          { "internalType": "uint8",   "name": "kind",   "type": "uint8" },
          { "internalType": "address", "name": "assetIn",  "type": "address" },
          { "internalType": "address", "name": "assetOut", "type": "address" },
          { "internalType": "uint256", "name": "amount",   "type": "uint256" },
          { "internalType": "bytes",   "name": "userData", "type": "bytes" }
        ],
        "internalType": "struct SingleSwap",
        "name": "singleSwap",
        "type": "tuple"
      },
      {
        "components": [
          { "internalType": "address", "name": "sender",               "type": "address" },
          { "internalType": "bool",    "name": "fromInternalBalance", "type": "bool" },
          { "internalType": "address", "name": "recipient",            "type": "address" },
          { "internalType": "bool",    "name": "toInternalBalance",   "type": "bool" }
        ],
        "internalType": "struct FundManagement",
        "name": "funds",
        "type": "tuple"
      },
      { "internalType": "uint256", "name": "limit",    "type": "uint256" },
      { "internalType": "uint256", "name": "deadline", "type": "uint256" }
    ],
    "name": "swap",
    "outputs": [
      {
        "internalType": "uint256",
        "name": "amountCalculated",
        "type": "uint256"
      }
    ],
    "stateMutability": "nonpayable",
    "type": "function"
  }
]

# Minimal ABI to get a Balancer pool's ID
SWAAP_POOL_ABI = [
    {
        "inputs": [],
        "name": "getPoolId",
        "outputs": [{"internalType": "bytes32", "name": "", "type": "bytes32"}],
        "stateMutability": "view",
        "type": "function"
    }
]

SWAAP_ROUTER_ABI = [
    {
        "name": "onSwap",
        "type": "function",
        "stateMutability": "nonpayable",
        "inputs": [
            {
                "internalType": "struct IPoolSwapStructs.SwapRequest",
                "name": "request",
                "type": "tuple",
                "components": [
                    {"internalType": "uint8",   "name": "kind",               "type": "uint8"},
                    {"internalType": "address", "name": "tokenIn",            "type": "address"},
                    {"internalType": "address", "name": "tokenOut",           "type": "address"},
                    {"internalType": "uint256", "name": "amount",             "type": "uint256"},
                    {"internalType": "bytes32", "name": "poolId",             "type": "bytes32"},
                    {"internalType": "uint256", "name": "lastChangeBlock",    "type": "uint256"},
                    {"internalType": "address", "name": "from",               "type": "address"},
                    {"internalType": "address", "name": "to",                 "type": "address"},
                    {"internalType": "bytes",   "name": "userData",           "type": "bytes"}
                ]
            },
            {"internalType": "uint256", "name": "balanceTokenIn",  "type": "uint256"},
            {"internalType": "uint256", "name": "balanceTokenOut", "type": "uint256"}
        ],
        "outputs": [
            {"internalType": "uint256", "name": "", "type": "uint256"}
        ]
    }
]

