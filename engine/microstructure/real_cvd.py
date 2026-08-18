"""
Real CVD — 체결 기반 진짜 Cumulative Volume Delta
============================================================
가짜 CVD (close 차분 부호) 대신, 실제 체결의 매수/매도 구분으로 계산.
KIS의 'side' 필드 (1=매수, 2=매도)를 사용.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional


def real_cvd(ticks: List[Dict[str, Any]]) -> Dict[str, Any]:
    """체결 stream → 누적 CVD.

    Returns: {cvd, buy_volume, sell_volume, cvd_series, ...}
    """
    if not ticks:
        return {"cvd": 0, "buy_volume": 0, "sell_volume": 0,
                "n_ticks": 0, "cvd_series": []}
    buy_vol = sell_vol = 0
    cvd_series = []
    for t in ticks:
        sz = int(t.get("size") or 0)
        side = str(t.get("side", ""))[:1]
        if side == "1":
            buy_vol += sz
        elif side == "2":
            sell_vol += sz
        cvd_series.append({"ts": t.get("_ts"),
                            "price": t.get("price"),
                            "cvd": buy_vol - sell_vol})
    cvd = buy_vol - sell_vol
    total = buy_vol + sell_vol
    return {
        "cvd":         cvd,
        "buy_volume":  buy_vol,
        "sell_volume": sell_vol,
        "buy_ratio":   round(buy_vol/total, 3) if total > 0 else 0.5,
        "n_ticks":     len(ticks),
        "cvd_series":  cvd_series[-300:],  # 최대 300점 응답
    }


def cvd_divergence(ticks: List[Dict[str, Any]],
                    lookback: int = 200) -> Dict[str, Any]:
    """가격 vs CVD 다이버전스 — 가짜CVD보다 정확.

    가격 신고가 + CVD 신고가 X → 약세 (매수자 약화)
    가격 신저가 + CVD 신저가 X → 강세 (매도자 약화)
    """
    if len(ticks) < lookback:
        return {"signal": 0, "n_ticks": len(ticks),
                "reason": f"데이터 부족 (< {lookback})"}
    recent = ticks[-lookback:]
    prices = [t.get("price") or 0 for t in recent]
    cvd = real_cvd(recent)
    cvd_vals = [x["cvd"] for x in cvd["cvd_series"]]
    if not cvd_vals or len(cvd_vals) < 10:
        return {"signal": 0, "reason": "CVD 시리즈 부족"}
    p_max = max(prices); p_min = min(prices)
    c_max = max(cvd_vals); c_min = min(cvd_vals)
    p_now = prices[-1]; c_now = cvd_vals[-1]
    if p_now == p_max and c_now < c_max:
        return {"signal": -1, "type": "bearish_divergence",
                "interpretation": "가격 신고가지만 CVD는 약함 → 매수자 고갈"}
    if p_now == p_min and c_now > c_min:
        return {"signal": +1, "type": "bullish_divergence",
                "interpretation": "가격 신저가지만 CVD는 강함 → 매도자 고갈"}
    return {"signal": 0, "type": "none"}
