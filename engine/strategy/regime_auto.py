"""
시장 regime 자동 감지 + 전략 자동 추천
============================================
간단한 통계 기반 regime detection (HMM/KMeans 무거우면 fallback):

  - trending_up   : 60일 ADX > 25 + 가격 > EMA200 + 상승추세
  - trending_down : ADX > 25 + 가격 < EMA200
  - ranging       : ADX < 20 (횡보)
  - high_vol      : 최근 변동성 평균의 1.8배 초과

각 regime별 추천 전략 매핑:
  trending_*  → donchian, supertrend, adx_trend, sma_cross
  ranging     → rsi_mr, bb_revert, zscore_mr, vwap_revert
  high_vol    → bb_breakout, keltner, roc (혹은 관망)
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd


def detect_regime(ohlcv: pd.DataFrame,
                  adx_window: int = 14,
                  ema_long: int = 200,
                  vol_window: int = 20) -> Dict[str, Any]:
    """OHLCV 마지막 시점의 regime을 반환."""
    if len(ohlcv) < max(ema_long, 30):
        return {"regime": "unknown", "confidence": 0.0,
                "metrics": {}}

    high, low, close = ohlcv["high"], ohlcv["low"], ohlcv["close"]
    # ADX
    up = (high - high.shift(1)).clip(lower=0)
    dn = (low.shift(1) - low).clip(lower=0)
    tr = pd.concat([high - low,
                    (high - close.shift(1)).abs(),
                    (low - close.shift(1)).abs()], axis=1).max(axis=1)
    atr = tr.rolling(adx_window).mean()
    plus_di = 100 * up.ewm(span=adx_window).mean() / atr
    minus_di = 100 * dn.ewm(span=adx_window).mean() / atr
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di)
    adx = dx.ewm(span=adx_window).mean()
    cur_adx = float(adx.iloc[-1]) if not np.isnan(adx.iloc[-1]) else 20

    # EMA200 trend
    ema = close.ewm(span=ema_long, adjust=False).mean()
    cur_close = float(close.iloc[-1])
    cur_ema = float(ema.iloc[-1])
    above = cur_close > cur_ema

    # 변동성 비율
    rets = np.log(close / close.shift(1)).dropna()
    cur_vol = float(rets.tail(vol_window).std()) if len(rets) > vol_window else 0.01
    avg_vol = float(rets.tail(60).std()) if len(rets) > 60 else cur_vol
    vol_ratio = cur_vol / avg_vol if avg_vol > 0 else 1.0

    # 분기
    if vol_ratio > 1.8:
        regime = "high_vol"
        conf = min(0.95, 0.5 + (vol_ratio - 1.8) * 0.3)
    elif cur_adx >= 25:
        regime = "trending_up" if above else "trending_down"
        conf = min(0.95, 0.5 + (cur_adx - 25) / 50)
    elif cur_adx < 20:
        regime = "ranging"
        conf = min(0.9, 0.5 + (20 - cur_adx) / 40)
    else:
        regime = "transition"
        conf = 0.4

    return {
        "regime": regime,
        "confidence": round(conf, 2),
        "metrics": {
            "adx": round(cur_adx, 1),
            "above_ema200": above,
            "vol_ratio": round(vol_ratio, 2),
            "current_volatility_annualized":
                round(cur_vol * np.sqrt(252) * 100, 2),
        },
    }


# regime별 추천 전략 (vbt_runner.AVAILABLE_STRATEGIES 키)
RECOMMENDED_BY_REGIME = {
    "trending_up":   ["donchian", "supertrend", "adx_trend",
                      "sma_cross", "ichimoku", "macd"],
    "trending_down": ["supertrend", "adx_trend", "engulfing"],
    "ranging":       ["rsi_mr", "bb_revert", "zscore_mr",
                      "vwap_revert", "dva"],
    "high_vol":      ["bb_breakout", "keltner", "roc", "donchian"],
    "transition":    ["sma_cross", "rsi_mr", "macd"],
    "unknown":       ["sma_cross", "rsi_mr"],
}


def recommend(ticker: str, period_days: int = 365) -> Dict[str, Any]:
    """
    종목 fetch → regime 감지 → 추천 전략 + 한국어 해석.
    """
    from .vbt_runner import _fetch_ohlcv, AVAILABLE_STRATEGIES
    try:
        ohlcv = _fetch_ohlcv(ticker, period_days)
    except Exception as e:
        return {"ok": False, "error": f"데이터 fetch 실패: {e}"}
    if len(ohlcv) < 30:
        return {"ok": False, "error": "데이터 부족"}

    det = detect_regime(ohlcv)
    regime = det["regime"]
    rec_ids = RECOMMENDED_BY_REGIME.get(regime, [])
    # 실제 등록된 전략만 필터
    rec = [{"id": sid,
            "label": AVAILABLE_STRATEGIES[sid]["label"],
            "desc": AVAILABLE_STRATEGIES[sid]["desc"]}
           for sid in rec_ids if sid in AVAILABLE_STRATEGIES]

    # 한국어 해석
    regime_kr = {
        "trending_up":   "강한 상승 추세",
        "trending_down": "강한 하락 추세",
        "ranging":       "횡보 (구간 매매)",
        "high_vol":      "고변동성 (돌파 또는 관망)",
        "transition":    "방향성 미확정",
        "unknown":       "데이터 부족",
    }.get(regime, regime)
    m = det["metrics"]
    rationale = (
        f"ADX {m.get('adx','-')}, "
        f"가격은 EMA200 {'위' if m.get('above_ema200') else '아래'}, "
        f"변동성 비율 {m.get('vol_ratio','-')}x — "
        f"{regime_kr} 상태로 판단."
    )

    return {
        "ok": True,
        "ticker": ticker.upper(),
        "regime": regime,
        "regime_kr": regime_kr,
        "confidence": det["confidence"],
        "metrics": m,
        "recommended_strategies": rec,
        "rationale_kr": rationale,
    }
