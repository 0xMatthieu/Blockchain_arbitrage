"""Detect-and-attribute mode: watch cross-DEX spreads block by block, never trade, and find out
who closed each spread.

Why: on Base there is no public mempool, so the only MEV that matters for us is the backrun race
after a block is published. This mode measures (a) how often and how long spreads above our
threshold exist, (b) how late we see them versus the block timestamp, and (c) which addresses
take them, by reading the logs of the two pools between the block where the spread opened and
the block where it closed.

Outputs (all under ./logs):
  arb_events.jsonl   one JSON per event: spread_open, spread_close, attribution, note
  arb_status.json    current mode, block, per-token spreads, leaderboard of takers
  control.json       {"mode": "run" | "pause"} written by the dashboard, polled here
"""

from __future__ import annotations

import json
import logging
import os
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from config import (
    w3, TOKEN_ADDRESSES, V2_FEE_BPS, V3_FEE_MAP, DETECT_MIN_SPREAD_PERCENT, ATTRIBUTION_MAX_BLOCKS,
    STATUS_INTERVAL_SECONDS,
)
from dex_utils import calc_max_trade_size, discover_pools, get_decimals, get_lp_price, get_token_info

UNTAKEN_COOLDOWN_S = 600  # a spread nobody takes for the whole window is dead liquidity: ignore that pair for a while

logger = logging.getLogger(__name__)

LOGS_DIR = Path("logs")
EVENTS_PATH = LOGS_DIR / "arb_events.jsonl"
STATUS_PATH = LOGS_DIR / "arb_status.json"
CONTROL_PATH = LOGS_DIR / "control.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def log_event(kind: str, **fields: Any) -> Dict[str, Any]:
    event = {"ts": _now_iso(), "kind": kind, **fields}
    try:
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        with EVENTS_PATH.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(event, default=str) + "\n")
    except OSError as exc:
        logger.warning("cannot write %s: %s", EVENTS_PATH, exc)
    logger.info("EVENT %s %s", kind, json.dumps({k: v for k, v in event.items() if k not in ("ts", "kind")}, default=str))
    return event


def read_control() -> Dict[str, Any]:
    if not CONTROL_PATH.exists():
        return {"mode": "run"}
    try:
        data = json.loads(CONTROL_PATH.read_text(encoding="utf-8"))
        return data if data.get("mode") in ("run", "pause") else {"mode": "run"}
    except (OSError, ValueError):
        return {"mode": "run"}


def _router_fee_bps(pool: Dict[str, Any]) -> int:
    if pool["dex"] in ("uniswap", "pancakeswap"):
        return V3_FEE_MAP.get(pool.get("feeBps"), 30)
    return V2_FEE_BPS


def best_spread(priced_pools: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Fee-adjusted spread between the cheapest and the richest pool of one token."""
    if len(priced_pools) < 2:
        return None
    pools = sorted(priced_pools, key=lambda p: p["price"])
    buy, sell = pools[0], pools[-1]
    buy_fee = _router_fee_bps(buy) / 10_000
    sell_fee = _router_fee_bps(sell) / 10_000
    effective_buy = buy["price"] * (1 + buy_fee)
    effective_sell = sell["price"] * (1 - sell_fee)
    spread_pct = (effective_sell - effective_buy) / effective_buy * 100
    return {
        "spread_pct": spread_pct, "buy_dex": buy["dex"], "sell_dex": sell["dex"],
        "buy_pool": buy["pairAddress"], "sell_pool": sell["pairAddress"],
        "buy_price": buy["price"], "sell_price": sell["price"],
    }


class Attribution:
    """Who touched the two pools of a spread between its opening and closing block."""

    def __init__(self, web3=None) -> None:
        self._w3 = web3 or w3

    def takers(self, pools: List[str], from_block: int, to_block: int) -> List[Dict[str, Any]]:
        try:
            logs = self._w3.eth.get_logs({
                "fromBlock": from_block, "toBlock": to_block,
                "address": [self._w3.to_checksum_address(p) for p in pools],
            })
        except Exception as exc:
            logger.warning("get_logs %s-%s failed: %s", from_block, to_block, exc)
            return []
        by_tx: Dict[str, Dict[str, Any]] = {}
        for lg in logs:
            txh = lg["transactionHash"].hex() if hasattr(lg["transactionHash"], "hex") else str(lg["transactionHash"])
            entry = by_tx.setdefault(txh, {"tx": txh, "block": lg["blockNumber"], "pools": set(), "log_index": lg["logIndex"]})
            entry["pools"].add(lg["address"].lower())
        out = []
        for txh, entry in by_tx.items():
            try:
                tx = self._w3.eth.get_transaction(txh)
                sender, to = tx["from"], tx.get("to")
                tx_index = tx.get("transactionIndex")
                prio = tx.get("maxPriorityFeePerGas") or tx.get("gasPrice")
            except Exception as exc:
                logger.debug("get_transaction %s failed: %s", txh, exc)
                sender, to, tx_index, prio = None, None, None, None
            out.append({
                "tx": txh, "block": entry["block"], "tx_index": tx_index, "from": sender, "to": to,
                "pools_touched": len(entry["pools"]), "both_pools": len(entry["pools"]) >= 2,
                "priority_fee_gwei": (float(prio) / 1e9) if prio else None,
            })
        out.sort(key=lambda t: (t["block"], t["tx_index"] if t["tx_index"] is not None else 1 << 30))
        return out


class SpreadDetector:
    def __init__(self, min_spread_pct: float = DETECT_MIN_SPREAD_PERCENT, attribution: Optional[Attribution] = None) -> None:
        self.min_spread_pct = min_spread_pct
        self.attribution = attribution or Attribution()
        self.token_info: Dict[str, Dict[str, Any]] = {}
        self.watched_pools: Dict[str, List[Dict[str, Any]]] = {}
        self.open_spreads: Dict[str, Dict[str, Any]] = {}   # token -> opening event
        self.latest: Dict[str, Dict[str, Any]] = {}         # token -> last spread snapshot
        self.leaderboard: Dict[str, Dict[str, Any]] = defaultdict(lambda: {"count": 0, "both_pools": 0, "tokens": set(), "to": set()})
        self.counters = {"blocks": 0, "spreads_opened": 0, "spreads_closed": 0, "attributed": 0, "unattributed": 0}
        self.latency_samples: List[float] = []
        self.cooldowns: Dict[str, float] = {}  # "buy_pool|sell_pool" -> until (epoch s)
        self._base_decimals: Optional[int] = None
        self.started_at = _now_iso()
        self.last_block = 0
        self.running = True

    # -- setup ---------------------------------------------------------------
    def discover(self) -> None:
        for name, address in TOKEN_ADDRESSES.items():
            info = get_token_info(address)
            self.token_info[address] = info
            pools = discover_pools(address)
            if pools:
                self.watched_pools[address] = pools
            logger.info("%s (%s): %d pools", name, info.get("symbol"), len(pools))
        log_event("note", message="detector started", tokens=len(self.watched_pools),
                  pools=sum(len(p) for p in self.watched_pools.values()), min_spread_pct=self.min_spread_pct)

    # -- per block -------------------------------------------------------------
    def on_block(self, block_number: int, block_ts: float) -> None:
        seen_at = time.time()
        latency = seen_at - block_ts
        self.latency_samples.append(latency)
        self.latency_samples = self.latency_samples[-500:]
        self.counters["blocks"] += 1
        self.last_block = block_number
        for token, pools in self.watched_pools.items():
            priced = []
            for pool in pools:
                try:
                    price = get_lp_price(pool, token)
                    if price and price > 0:
                        priced.append({**pool, "price": price})
                except Exception as exc:
                    logger.debug("price %s failed: %s", pool.get("pairAddress"), exc)
            spread = best_spread(priced)
            symbol = self.token_info.get(token, {}).get("symbol", token[-6:])
            opened = self.open_spreads.get(token)
            if spread is None:
                # Not enough priced pools this block (RPC hiccup). Unknown is not "closed": keep any open
                # spread open and let the attribution window decide.
                self.latest[token] = {"symbol": symbol, "block": block_number, "spread_pct": None, "pools": len(priced),
                                      "unpriced": len(pools) - len(priced)}
                if opened and block_number - opened["block"] >= ATTRIBUTION_MAX_BLOCKS:
                    self._close(token, symbol, block_number, block_ts, {"spread_pct": None}, reason="window elapsed (unpriced)")
                continue
            self.latest[token] = {"symbol": symbol, "block": block_number, "pools": len(priced), "unpriced": len(pools) - len(priced), **spread}
            above = spread["spread_pct"] >= self.min_spread_pct
            pair_key = f"{spread['buy_pool']}|{spread['sell_pool']}".lower()
            if above and not opened:
                cooled_until = self.cooldowns.get(pair_key, 0.0)
                if cooled_until > time.time():
                    self.latest[token]["cooldown_s"] = int(cooled_until - time.time())
                    continue
                sizing = self._sizing(priced, spread, token)
                ev = log_event("spread_open", symbol=symbol, token=token, block=block_number, block_ts=block_ts,
                               seen_latency_s=round(latency, 3), **spread, **sizing)
                self.open_spreads[token] = ev
                self.counters["spreads_opened"] += 1
            elif above and opened:
                if spread["spread_pct"] > opened.get("max_spread_pct", opened["spread_pct"]):
                    opened["max_spread_pct"] = spread["spread_pct"]
                if block_number - opened["block"] >= ATTRIBUTION_MAX_BLOCKS:
                    self._close(token, symbol, block_number, block_ts, spread, reason="still open, attribution window elapsed")
            elif not above and opened:
                self._close(token, symbol, block_number, block_ts, spread, reason="closed")

    def _sizing(self, priced: List[Dict[str, Any]], spread: Dict[str, Any], token: str) -> Dict[str, Any]:
        """How much base token the two pools can absorb, and the profit that would be worth."""
        try:
            if self._base_decimals is None:
                from config import BASE_CURRENCY_ADDRESS
                self._base_decimals = get_decimals(BASE_CURRENCY_ADDRESS)
            by_addr = {p["pairAddress"].lower(): p for p in priced}
            buy = by_addr.get(spread["buy_pool"].lower()); sell = by_addr.get(spread["sell_pool"].lower())
            buy_max = calc_max_trade_size(buy, token) if buy else None
            sell_max = calc_max_trade_size(sell, token) if sell else None
            scale = 10 ** self._base_decimals
            size = min(x for x in (buy_max, sell_max) if x) if (buy_max or sell_max) else None
            return {
                "buy_max_base": (buy_max / scale) if buy_max else None,
                "sell_max_base": (sell_max / scale) if sell_max else None,
                "tradeable_base": (size / scale) if size else None,
                "potential_profit_base": (size / scale * spread["spread_pct"] / 100) if size else None,
            }
        except Exception as exc:
            logger.debug("sizing failed: %s", exc)
            return {}

    def _close(self, token: str, symbol: str, block_number: int, block_ts: float, spread: Dict[str, Any], *, reason: str) -> None:
        opened = self.open_spreads.pop(token)
        blocks_open = block_number - opened["block"]
        log_event("spread_close", symbol=symbol, token=token, open_block=opened["block"], close_block=block_number,
                  blocks_open=blocks_open, seconds_open=round(block_ts - opened["block_ts"], 1),
                  open_spread_pct=opened["spread_pct"], max_spread_pct=opened.get("max_spread_pct", opened["spread_pct"]),
                  close_spread_pct=spread["spread_pct"], reason=reason)
        self.counters["spreads_closed"] += 1
        takers = self.attribution.takers([opened["buy_pool"], opened["sell_pool"]], opened["block"], block_number)
        # The first tx after the opening block that touched both pools is the arb; anything that touched
        # one pool is still worth listing (a swap that happened to close the gap, or a two-leg arb).
        atomic = [t for t in takers if t["both_pools"] and t["block"] > opened["block"]] or [t for t in takers if t["both_pools"]]
        winner = atomic[0] if atomic else (takers[0] if takers else None)
        if winner:
            self.counters["attributed"] += 1
            for t in takers:
                key = (t["from"] or "?").lower()
                lb = self.leaderboard[key]
                lb["count"] += 1
                lb["both_pools"] += 1 if t["both_pools"] else 0
                lb["tokens"].add(symbol)
                if t["to"]:
                    lb["to"].add(t["to"].lower())
        else:
            self.counters["unattributed"] += 1
            if reason.startswith("still open") or reason.startswith("window elapsed"):
                # Nobody touched either pool for the whole window although the spread was there:
                # dead or untradeable liquidity (or the fee model is wrong for these pools). Cool it down.
                pair_key = f"{opened['buy_pool']}|{opened['sell_pool']}".lower()
                self.cooldowns[pair_key] = time.time() + UNTAKEN_COOLDOWN_S
                self.counters["untaken"] = self.counters.get("untaken", 0) + 1
                log_event("note", message="spread persisted with no taker: dead or untradeable liquidity, pair cooled down",
                          symbol=symbol, buy_pool=opened["buy_pool"], sell_pool=opened["sell_pool"],
                          spread_pct=opened["spread_pct"], tradeable_base=opened.get("tradeable_base"),
                          cooldown_s=UNTAKEN_COOLDOWN_S)
        log_event("attribution", symbol=symbol, token=token, open_block=opened["block"], close_block=block_number,
                  our_first_sight_latency_s=opened.get("seen_latency_s"), spread_pct=opened["spread_pct"],
                  potential_profit_base=opened.get("potential_profit_base"),
                  winner=winner, candidates=takers[:10], n_candidates=len(takers))

    # -- status ------------------------------------------------------------------
    def write_status(self, mode: str) -> None:
        lat = sorted(self.latency_samples)
        p50 = lat[len(lat) // 2] if lat else None
        board = sorted(
            ({"from": k, "count": v["count"], "both_pools": v["both_pools"], "tokens": sorted(v["tokens"]), "contracts": sorted(v["to"])[:3]}
             for k, v in self.leaderboard.items()),
            key=lambda r: r["count"], reverse=True,
        )[:15]
        status = {
            "generated_at": _now_iso(), "started_at": self.started_at, "mode": mode, "control": read_control(),
            "chain": "base", "last_block": self.last_block, "min_spread_pct": self.min_spread_pct,
            "tokens": [{"token": t, **self.latest.get(t, {"symbol": self.token_info.get(t, {}).get("symbol")}),
                        "n_pools": len(p), "open": t in self.open_spreads} for t, p in self.watched_pools.items()],
            "counters": self.counters, "seen_latency_p50_s": round(p50, 3) if p50 is not None else None,
            "leaderboard": board,
        }
        try:
            LOGS_DIR.mkdir(parents=True, exist_ok=True)
            tmp = STATUS_PATH.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(status, indent=2, default=str), encoding="utf-8")
            tmp.replace(STATUS_PATH)
        except OSError as exc:
            logger.warning("cannot write status: %s", exc)

    # -- loop ----------------------------------------------------------------------
    def run(self, poll_s: float = 0.25) -> None:
        self.discover()
        if not self.watched_pools:
            logger.error("no pools discovered; nothing to watch")
            return
        last_status = 0.0
        last_seen = 0
        while self.running:
            mode = read_control().get("mode", "run")
            if time.time() - last_status >= STATUS_INTERVAL_SECONDS:
                self.write_status(mode)
                last_status = time.time()
            if mode == "pause":
                time.sleep(2)
                continue
            try:
                bn = w3.eth.block_number
                if bn > last_seen:
                    blk = w3.eth.get_block(bn)
                    self.on_block(bn, float(blk["timestamp"]))
                    last_seen = bn
            except Exception as exc:
                logger.warning("block loop error: %s", exc)
                time.sleep(2)
            time.sleep(poll_s)
