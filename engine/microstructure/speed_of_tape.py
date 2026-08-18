"""
Speed of Tape — 체결 속도 분석
============================================================
틱 단위로 들어오는 체결의 속도(빈도/볼륨)를 측정.
급격히 빨라지면 대형 주문 진입 시그널.

수식:
  speed_t = trades_in_window / window_seconds
  volume_speed_t = sum_size_in_window / window_seconds
  acceleration = (speed_t - speed_{t-1}) / speed_{t-1}
"""
from __future__ import annotations

import time
from collections import deque
from typing import Any, Dict, List, Optional


def speed_of_tape(ticks: List[Dict[str, Any]],
                  window_sec: float = 5.0) -> Dict[str, Any]:
    """최근 window_sec 동안의 체결 속도.

    ticks: [{ticker, time, price, side, size, _ts}, ...]
    Returns: {trades_per_sec, volume_per_sec, n_ticks, ...}
    """
    if not ticks:
        return {"trades_per_sec": 0.0, "volume_per_sec": 0.0,
                "n_ticks": 0, "buy_ratio": 0.5}
    now = time.time()
    recent = [t for t in ticks if (now - t.get("_ts", now)) <= window_sec]
    if not recent:
        return {"trades_per_sec": 0.0, "volume_per_sec": 0.0,
                "n_ticks": 0, "buy_ratio": 0.5}
    n = len(recent)
    vol = sum(int(t.get("size") or 0) for t in recent)
    # 매수 vs 매도 — KIS는 'side'에 1(매수)/2(매도) 또는 다른 값. 임시로 size>0 매수 가정
    buys = sum(1 for t in recent if str(t.get("side", "")).startswith("1"))
    sells = sum(1 for t in recent if str(t.get("side", "")).startswith("2"))
    total_sides = buys + sells
    buy_ratio = buys / total_sides if total_sides > 0 else 0.5
    return {
        "trades_per_sec":  round(n / window_sec, 3),
        "volume_per_sec":  round(vol / window_sec, 1),
        "n_ticks":         n,
        "total_volume":    vol,
        "buy_count":       buys,
        "sell_count":      sells,
        "buy_ratio":       round(buy_ratio, 3),
        "window_sec":      window_sec,
    }


def tape_acceleration(ticks: List[Dict[str, Any]],
                       short_sec: float = 5.0,
                       long_sec: float = 30.0) -> Dict[str, Any]:
    """단기 vs 장기 평균 속도 비율 — 가속 측정.

    ratio > 2.0 → 평소 대비 2배 빠름 (큰 주문/뉴스 가능성)
    """
    short = speed_of_tape(ticks, window_sec=short_sec)
    long_  = speed_of_tape(ticks, window_sec=long_sec)
    long_speed = long_.get("trades_per_sec", 0) or 1e-9
    ratio = short.get("trades_per_sec", 0) / long_speed
    vol_long = long_.get("volume_per_sec", 0) or 1e-9
    vol_ratio = short.get("volume_per_sec", 0) / vol_long
    return {
        "short": short, "long": long_,
        "speed_ratio":  round(ratio, 3),
        "volume_ratio": round(vol_ratio, 3),
        "alert":        ratio >= 2.0 or vol_ratio >= 3.0,
    }
