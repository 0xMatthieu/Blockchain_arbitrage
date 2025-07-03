import os
# Ensure minimal env vars for config import
os.environ.setdefault('BASE_RPC_URL', 'ws://localhost:8545')

from dex_utils import find_router_info


def test_find_router_info_exact_match():
    routers = {'baseswap': {'address': '0x1', 'version': 2}}
    assert find_router_info('baseswap', routers) == routers['baseswap']


def test_find_router_info_selects_highest_version():
    routers = {
        'uniswap_v2': {'address': '0x2', 'version': 2},
        'uniswap_v3': {'address': '0x3', 'version': 3},
    }
    result = find_router_info('uniswap', routers)
    assert result['version'] == 3
    assert result['address'] == '0x3'


def test_find_router_info_returns_none_when_missing():
    routers = {'uniswap_v2': {'address': '0x2', 'version': 2}}
    assert find_router_info('pancakeswap', routers) is None

