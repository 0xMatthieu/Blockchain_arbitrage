// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

interface IERC20 {
    function balanceOf(address) external view returns (uint256);
    function approve(address spender, uint256 amount) external returns (bool);
    function transfer(address to, uint256 amount) external returns (bool);
}

interface IUniswapV2Router {
    function swapExactTokensForTokens(
        uint256 amountIn,
        uint256 amountOutMin,
        address[] calldata path,
        address to,
        uint256 deadline
    ) external returns (uint256[] memory amounts);
}

interface IUniswapV3Router {
    struct ExactInputSingleParams {
        address tokenIn;
        address tokenOut;
        uint24 fee;
        address recipient;
        uint256 amountIn;
        uint256 amountOutMinimum;
        uint160 sqrtPriceLimitX96;
    }
    function exactInputSingle(ExactInputSingleParams calldata params)
        external payable returns (uint256 amountOut);
}

interface ISolidlyRouter {
    struct Route {
        address from;
        address to;
        bool stable;
        address factory;
    }
    function swapExactTokensForTokens(
        uint256 amountIn,
        uint256 amountOutMin,
        Route[] calldata routes,
        address to,
        uint256 deadline
    ) external returns (uint256[] memory amounts);
}

contract ArbitrageExecutor {
    address public immutable owner;

    uint8 constant DEX_V2 = 0;
    uint8 constant DEX_V3 = 1;
    uint8 constant DEX_SOLIDLY = 2;

    struct ArbParams {
        uint8 buyDexType;
        address buyRouter;
        bytes buyData;
        uint8 sellDexType;
        address sellRouter;
        bytes sellData;
        address baseToken;
        address targetToken;
        uint256 amountIn;
        uint256 minProfit;
    }

    modifier onlyOwner() {
        require(msg.sender == owner, "not owner");
        _;
    }

    constructor() {
        owner = msg.sender;
    }

    /// @notice Execute an atomic arbitrage: buy on one DEX, sell on another.
    ///         Reverts if not profitable (costs only gas on revert).
    function executeArb(ArbParams calldata p) external onlyOwner {
        uint256 initialBalance = IERC20(p.baseToken).balanceOf(address(this));

        // --- BUY: baseToken -> targetToken ---
        _doSwap(p.buyDexType, p.buyRouter, p.buyData, p.baseToken, p.targetToken, p.amountIn);

        // --- SELL: targetToken -> baseToken (sell everything received) ---
        uint256 targetBalance = IERC20(p.targetToken).balanceOf(address(this));
        require(targetBalance > 0, "buy returned 0");
        _doSwap(p.sellDexType, p.sellRouter, p.sellData, p.targetToken, p.baseToken, targetBalance);

        // --- PROFIT CHECK ---
        uint256 finalBalance = IERC20(p.baseToken).balanceOf(address(this));
        require(finalBalance >= initialBalance + p.minProfit, "not profitable");
    }

    /// @notice Pre-approve a token for a router (max allowance)
    function approveRouter(address token, address router) external onlyOwner {
        IERC20(token).approve(router, type(uint256).max);
    }

    /// @notice Withdraw any token to owner
    function withdraw(address token) external onlyOwner {
        uint256 bal = IERC20(token).balanceOf(address(this));
        if (bal > 0) {
            IERC20(token).transfer(owner, bal);
        }
    }

    /// @notice Withdraw ETH to owner
    function withdrawETH() external onlyOwner {
        uint256 bal = address(this).balance;
        if (bal > 0) {
            payable(owner).transfer(bal);
        }
    }

    receive() external payable {}

    // --- Internal swap dispatcher ---

    function _doSwap(
        uint8 dexType,
        address router,
        bytes calldata data,
        address tokenIn,
        address tokenOut,
        uint256 amountIn
    ) internal {
        if (dexType == DEX_V2) {
            _swapV2(router, data, tokenIn, tokenOut, amountIn);
        } else if (dexType == DEX_V3) {
            _swapV3(router, data, tokenIn, tokenOut, amountIn);
        } else if (dexType == DEX_SOLIDLY) {
            _swapSolidly(router, data, tokenIn, tokenOut, amountIn);
        } else {
            revert("unknown dex type");
        }
    }

    function _swapV2(
        address router,
        bytes calldata data,
        address tokenIn,
        address tokenOut,
        uint256 amountIn
    ) internal {
        uint256 amountOutMin = abi.decode(data, (uint256));

        address[] memory path = new address[](2);
        path[0] = tokenIn;
        path[1] = tokenOut;

        IUniswapV2Router(router).swapExactTokensForTokens(
            amountIn, amountOutMin, path, address(this), block.timestamp + 300
        );
    }

    function _swapV3(
        address router,
        bytes calldata data,
        address tokenIn,
        address tokenOut,
        uint256 amountIn
    ) internal {
        (uint24 fee, uint256 amountOutMin) = abi.decode(data, (uint24, uint256));

        IUniswapV3Router(router).exactInputSingle(
            IUniswapV3Router.ExactInputSingleParams({
                tokenIn: tokenIn,
                tokenOut: tokenOut,
                fee: fee,
                recipient: address(this),
                amountIn: amountIn,
                amountOutMinimum: amountOutMin,
                sqrtPriceLimitX96: 0
            })
        );
    }

    function _swapSolidly(
        address router,
        bytes calldata data,
        address tokenIn,
        address tokenOut,
        uint256 amountIn
    ) internal {
        (bool stable, address factory, uint256 amountOutMin) = abi.decode(data, (bool, address, uint256));

        ISolidlyRouter.Route[] memory routes = new ISolidlyRouter.Route[](1);
        routes[0] = ISolidlyRouter.Route({
            from: tokenIn,
            to: tokenOut,
            stable: stable,
            factory: factory
        });

        ISolidlyRouter(router).swapExactTokensForTokens(
            amountIn, amountOutMin, routes, address(this), block.timestamp + 300
        );
    }
}
