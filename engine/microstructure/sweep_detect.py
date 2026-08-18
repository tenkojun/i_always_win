"""
Sweep Detection — 대량 단일 주문이 여러 호가 동시 흡수
============================================================
sweep = 한 트레이더가 시장가로 여러 호가단계를 한 번에 체결.
강한 의향 시그널 (defensive sweep = stop loss, aggressive sweep = 모멘텀).

검출:
  - 짧은 시간(<200ms) 안의 체결 N건이 같은 방향
  - 총 size > 임계 (예: ADV의 0.5% 이상)
  - 가격이 여러 tick 통과 (가격 단계 ≥ 2)
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional


def detect_sweeps(ticks: List[Dict[str, Any]],
                  min_size: int = 500,
                  window_ms: float = 200,
                  min_price_levels: int = 2) -> List[Dict[str, Any]]:
    """체결 stream에서 sweep 검출.

    Returns: [{start_ts, end_ts, direction, total_size, n_trades,
                price_levels, start_price, end_price}, ...]
    """
    if not ticks or len(ticks) < 2:
        return []
    sweeps = []
    n = len(ticks)
    i = 0
    while i < n:
        anchor = ticks[i]
        anchor_ts = anchor.get("_ts", 0)
        anchor_side = str(anchor.get("side", ""))[:1]
        j = i + 1
        cluster = [anchor]
        while j < n:
            t = ticks[j]
            dt_ms = (t.get("_ts", 0) - anchor_ts) * 1000
            if dt_ms > window_ms:
                break
            if str(t.get("side", ""))[:1] == anchor_side:
                cluster.append(t)
            j += 1
        if len(cluster) >= 2:
            total_size = sum(int(t.get("size") or 0) for t in cluster)
            prices = sorted(set(t.get("price", 0) for t in cluster))
            if total_size >= min_size and len(prices) >= min_price_levels:
                sweeps.append({
                    "start_ts":      anchor_ts,
                    "end_ts":        cluster[-1].get("_ts"),
                    "direction":     "buy" if anchor_side == "1" else "sell",
                    "total_size":    total_size,
                    "n_trades":      len(cluster),
                    "price_levels":  len(prices),
                    "start_price":   anchor.get("price"),
                    "end_price":     cluster[-1].get("price"),
                    "duration_ms":   round((cluster[-1].get("_ts", 0) - anchor_ts) * 1000, 1),
                })
                i = j
                continue
        i += 1
    return sweeps
