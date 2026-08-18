"""
확장 전략 시그널 생성기 (12+개)
================================
모두 OHLCV로 동작. 시그널은 causal (미래 데이터 누설 없음).

가능한 것:
  ✓ VWAP/Bollinger/Donchian/Ichimoku/Z-score/Supertrend/Keltner
  ✓ ADX 트렌드, ROC 모멘텀, Triple Screen, Engulfing, Heikin-Ashi
  ⚠ TPO/DVA — 일봉 단순화 (정통 Market Profile은 tick 필요)
  ⚠ Pseudo orderflow (close-open 부호) — 진짜 CVD는 매수/매도 체결 필요

전략 메타 (UI 자동 생성용)는 STRATEGIES_EXT에 정의.
각 시그널 함수는 (entries, exits) Series 쌍 반환.
"""
from __future__ import annotations

from typing import Any, Dict, Tuple

import numpy as np
import pandas as pd


# ── 헬퍼 ─────────────────────────────────────────────────────────
def _to_series(s):
    return s if isinstance(s, pd.Series) else pd.Series(s)


def _ema(s, n):
    return s.ewm(span=n, adjust=False).mean()


def _sma(s, n):
    return s.rolling(n).mean()


def _atr(high, low, close, n=14):
    tr = pd.concat([
        high - low,
        (high - close.shift(1)).abs(),
        (low - close.shift(1)).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(n).mean()


def _make_empty(idx):
    return pd.Series(False, index=idx), pd.Series(False, index=idx)


# ── 1. VWAP 평균회귀 ──────────────────────────────────────────────
def sig_vwap_revert(df, lookback=20, k=2.0):
    """일봉 VWAP (typical price * volume 누적 / 누적 volume).
       가격이 VWAP - k*std 아래면 매수, VWAP + k*std 위에서 매도."""
    if "volume" not in df:
        return _make_empty(df.index)
    typ = (df["high"] + df["low"] + df["close"]) / 3
    pv = (typ * df["volume"]).rolling(lookback).sum()
    vsum = df["volume"].rolling(lookback).sum()
    vwap = pv / vsum
    # 가격-VWAP 스프레드의 std로 밴드 만들기
    diff = df["close"] - vwap
    std = diff.rolling(lookback).std()
    lower = vwap - k * std
    upper = vwap + k * std
    entries = (df["close"] < lower) & \
              (df["close"].shift(1) >= lower.shift(1))
    exits = (df["close"] > upper) & \
            (df["close"].shift(1) <= upper.shift(1))
    return entries.fillna(False), exits.fillna(False)


# ── 2. Bollinger 평균회귀 ─────────────────────────────────────────
def sig_bb_revert(df, window=20, k=2.0):
    sma = df["close"].rolling(window).mean()
    std = df["close"].rolling(window).std()
    lower = sma - k * std
    upper = sma + k * std
    entries = (df["close"] < lower) & \
              (df["close"].shift(1) >= lower.shift(1))
    exits = (df["close"] > sma) & \
            (df["close"].shift(1) <= sma.shift(1))
    return entries.fillna(False), exits.fillna(False)


# ── 3. Bollinger Breakout ─────────────────────────────────────────
def sig_bb_breakout(df, window=20, k=2.0):
    sma = df["close"].rolling(window).mean()
    std = df["close"].rolling(window).std()
    upper = sma + k * std
    lower = sma - k * std
    entries = (df["close"] > upper) & \
              (df["close"].shift(1) <= upper.shift(1))
    exits = (df["close"] < lower) & \
            (df["close"].shift(1) >= lower.shift(1))
    return entries.fillna(False), exits.fillna(False)


# ── 4. Donchian Breakout (터틀 변형) ──────────────────────────────
def sig_donchian(df, window=20):
    high_n = df["high"].shift(1).rolling(window).max()
    low_n = df["low"].shift(1).rolling(window).min()
    entries = df["close"] > high_n
    exits = df["close"] < low_n
    return entries.fillna(False), exits.fillna(False)


# ── 5. Ichimoku Kumo Cross ───────────────────────────────────────
def sig_ichimoku(df, tenkan=9, kijun=26):
    high, low = df["high"], df["low"]
    th = (high.rolling(tenkan).max() + low.rolling(tenkan).min()) / 2
    kh = (high.rolling(kijun).max() + low.rolling(kijun).min()) / 2
    # 매수: tenkan이 kijun 상향 돌파 + 가격이 둘 다 위
    entries = (th > kh) & (th.shift(1) <= kh.shift(1)) & \
              (df["close"] > th)
    exits = (th < kh) & (th.shift(1) >= kh.shift(1))
    return entries.fillna(False), exits.fillna(False)


# ── 6. Z-score Mean Reversion ────────────────────────────────────
def sig_zscore_mr(df, window=20, z_in=-2.0, z_out=0.0):
    mean = df["close"].rolling(window).mean()
    std = df["close"].rolling(window).std()
    z = (df["close"] - mean) / std
    entries = (z < z_in) & (z.shift(1) >= z_in)
    exits = (z > z_out) & (z.shift(1) <= z_out)
    return entries.fillna(False), exits.fillna(False)


# ── 7. Supertrend ────────────────────────────────────────────────
def sig_supertrend(df, atr_period=10, mult=3.0):
    hl2 = (df["high"] + df["low"]) / 2
    atr = _atr(df["high"], df["low"], df["close"], atr_period)
    upper = hl2 + mult * atr
    lower = hl2 - mult * atr
    n = len(df)
    direction = np.zeros(n)
    final_lower = lower.copy()
    final_upper = upper.copy()
    direction[0] = 1
    for i in range(1, n):
        # carry forward — 상승 추세면 lower 유지 또는 상향만
        if direction[i - 1] == 1:
            final_lower.iloc[i] = max(final_lower.iloc[i - 1],
                                       lower.iloc[i])
            if df["close"].iloc[i] < final_lower.iloc[i]:
                direction[i] = -1
                final_upper.iloc[i] = upper.iloc[i]
            else:
                direction[i] = 1
        else:
            final_upper.iloc[i] = min(final_upper.iloc[i - 1],
                                       upper.iloc[i])
            if df["close"].iloc[i] > final_upper.iloc[i]:
                direction[i] = 1
                final_lower.iloc[i] = lower.iloc[i]
            else:
                direction[i] = -1
    dir_s = pd.Series(direction, index=df.index)
    entries = (dir_s == 1) & (dir_s.shift(1) == -1)
    exits = (dir_s == -1) & (dir_s.shift(1) == 1)
    return entries.fillna(False), exits.fillna(False)


# ── 8. Keltner Channel Breakout ──────────────────────────────────
def sig_keltner(df, ema_n=20, atr_n=10, mult=2.0):
    ema = _ema(df["close"], ema_n)
    atr = _atr(df["high"], df["low"], df["close"], atr_n)
    upper = ema + mult * atr
    lower = ema - mult * atr
    entries = (df["close"] > upper) & \
              (df["close"].shift(1) <= upper.shift(1))
    exits = (df["close"] < lower) & \
            (df["close"].shift(1) >= lower.shift(1))
    return entries.fillna(False), exits.fillna(False)


# ── 9. ADX 트렌드 필터 + 모멘텀 ──────────────────────────────────
def sig_adx_trend(df, adx_n=14, adx_th=25, mom_n=14):
    high, low, close = df["high"], df["low"], df["close"]
    plus_dm = (high - high.shift(1)).clip(lower=0)
    minus_dm = (low.shift(1) - low).clip(lower=0)
    atr = _atr(high, low, close, adx_n)
    plus_di = 100 * (_ema(plus_dm, adx_n) / atr)
    minus_di = 100 * (_ema(minus_dm, adx_n) / atr)
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di)
    adx = _ema(dx, adx_n)
    # 모멘텀
    mom = close - close.shift(mom_n)
    # 매수: ADX > th + +DI > -DI + 모멘텀 양수
    entries = (adx > adx_th) & (plus_di > minus_di) & (mom > 0) & \
              ~((adx.shift(1) > adx_th) & (plus_di.shift(1) > minus_di.shift(1)))
    exits = (plus_di < minus_di) & (plus_di.shift(1) >= minus_di.shift(1))
    return entries.fillna(False), exits.fillna(False)


# ── 10. ROC 모멘텀 ────────────────────────────────────────────────
def sig_roc(df, window=12, th=2.0):
    roc = (df["close"] / df["close"].shift(window) - 1) * 100
    entries = (roc > th) & (roc.shift(1) <= th)
    exits = (roc < -th) & (roc.shift(1) >= -th)
    return entries.fillna(False), exits.fillna(False)


# ── 11. Triple Screen (Elder) ────────────────────────────────────
def sig_triple_screen(df, ema_long=50, rsi_n=14):
    """장기 EMA 위 + RSI 과매도 → 매수, EMA 하향 → 청산."""
    ema = _ema(df["close"], ema_long)
    # RSI
    delta = df["close"].diff()
    gain = delta.clip(lower=0).rolling(rsi_n).mean()
    loss = -delta.clip(upper=0).rolling(rsi_n).mean()
    rs = gain / loss.replace(0, np.nan)
    rsi = 100 - 100 / (1 + rs)
    trend = df["close"] > ema
    entries = trend & (rsi < 35) & (rsi.shift(1) >= 35)
    exits = (~trend) & trend.shift(1)
    return entries.fillna(False), exits.fillna(False)


# ── 12. Engulfing Pattern + Trend Filter ────────────────────────
def sig_engulfing(df, ema_n=50):
    """장기 EMA 위에서 bullish engulfing → 매수, bearish → 매도."""
    ema = _ema(df["close"], ema_n)
    o, c = df["open"], df["close"]
    bull_eng = (c > o) & (o < c.shift(1)) & (c > o.shift(1)) & \
               (c.shift(1) < o.shift(1))
    bear_eng = (c < o) & (o > c.shift(1)) & (c < o.shift(1)) & \
               (c.shift(1) > o.shift(1))
    entries = bull_eng & (df["close"] > ema)
    exits = bear_eng
    return entries.fillna(False), exits.fillna(False)


# ── 13. Heikin-Ashi Trend Following ──────────────────────────────
def sig_heikin_ashi(df, confirm=2):
    """HA 캔들 색이 N개 연속 같은 방향이면 진입/청산."""
    ha_close = (df["open"] + df["high"] + df["low"] + df["close"]) / 4
    ha_open = ((df["open"].shift(1) + df["close"].shift(1)) / 2).fillna(
        df["open"])
    bull = ha_close > ha_open
    bear = ha_close < ha_open
    # confirm 봉 연속
    bull_n = bull.rolling(confirm).sum() == confirm
    bear_n = bear.rolling(confirm).sum() == confirm
    entries = bull_n & ~bull_n.shift(1).fillna(False)
    exits = bear_n & ~bear_n.shift(1).fillna(False)
    return entries.fillna(False), exits.fillna(False)


# ── 14. Daily Value Area (일봉 단순화 TPO) ──────────────────────
def sig_dva(df, window=20):
    """
    Market Profile 정통은 30분 tick 필요. 여기는 일봉 OHLC로 단순화:
    - 최근 N일의 일봉 high/low로 value area 추정 (1 std band)
    - 가격이 value area 하단 이탈 후 복귀 → 매수 (failed auction)
    - 상단 돌파 → 청산
    """
    typ = (df["high"] + df["low"] + df["close"]) / 3
    vwap = typ.rolling(window).mean()
    std = typ.rolling(window).std()
    va_low = vwap - 0.7 * std   # 70% value area
    va_high = vwap + 0.7 * std
    # failed auction: 어제 va_low 이탈, 오늘 복귀
    below = df["close"] < va_low
    entries = (~below) & below.shift(1).fillna(False)
    exits = (df["close"] > va_high) & \
            (df["close"].shift(1) <= va_high.shift(1))
    return entries.fillna(False), exits.fillna(False)


# ── 메타 정보 ─────────────────────────────────────────────────────
STRATEGIES_EXT = {
    "vwap_revert": {
        "label": "VWAP 평균회귀",
        "desc": "VWAP-band 이탈 후 복귀",
        "fn": sig_vwap_revert,
        "params": [
            {"name": "lookback", "type": "int", "default": 20,
             "min": 5, "max": 60, "label": "VWAP 기간"},
            {"name": "k", "type": "int", "default": 2,
             "min": 1, "max": 4, "label": "밴드 σ"},
        ],
    },
    "bb_revert": {
        "label": "Bollinger 평균회귀",
        "desc": "BB 하단 터치 → 매수, 중심선 도달 → 청산",
        "fn": sig_bb_revert,
        "params": [
            {"name": "window", "type": "int", "default": 20,
             "min": 10, "max": 50, "label": "기간"},
            {"name": "k", "type": "int", "default": 2,
             "min": 1, "max": 3, "label": "σ"},
        ],
    },
    "bb_breakout": {
        "label": "Bollinger 돌파",
        "desc": "BB 상단 돌파 → 매수, 하단 이탈 → 청산",
        "fn": sig_bb_breakout,
        "params": [
            {"name": "window", "type": "int", "default": 20,
             "min": 10, "max": 50, "label": "기간"},
            {"name": "k", "type": "int", "default": 2,
             "min": 1, "max": 3, "label": "σ"},
        ],
    },
    "donchian": {
        "label": "Donchian 돌파 (터틀)",
        "desc": "N일 최고가 돌파 → 매수",
        "fn": sig_donchian,
        "params": [
            {"name": "window", "type": "int", "default": 20,
             "min": 5, "max": 80, "label": "기간"},
        ],
    },
    "ichimoku": {
        "label": "Ichimoku TK 크로스",
        "desc": "전환선·기준선 골든크로스",
        "fn": sig_ichimoku,
        "params": [
            {"name": "tenkan", "type": "int", "default": 9,
             "min": 5, "max": 20, "label": "전환선"},
            {"name": "kijun", "type": "int", "default": 26,
             "min": 15, "max": 60, "label": "기준선"},
        ],
    },
    "zscore_mr": {
        "label": "Z-score 평균회귀",
        "desc": "Z<-2 진입, Z>0 청산",
        "fn": sig_zscore_mr,
        "params": [
            {"name": "window", "type": "int", "default": 20,
             "min": 10, "max": 60, "label": "기간"},
            {"name": "z_in", "type": "int", "default": -2,
             "min": -3, "max": -1, "label": "진입Z"},
            {"name": "z_out", "type": "int", "default": 0,
             "min": -1, "max": 2, "label": "청산Z"},
        ],
    },
    "supertrend": {
        "label": "Supertrend",
        "desc": "ATR 기반 동적 트레일링",
        "fn": sig_supertrend,
        "params": [
            {"name": "atr_period", "type": "int", "default": 10,
             "min": 5, "max": 30, "label": "ATR 기간"},
            {"name": "mult", "type": "int", "default": 3,
             "min": 1, "max": 5, "label": "ATR 배수"},
        ],
    },
    "keltner": {
        "label": "Keltner 돌파",
        "desc": "EMA ± ATR 채널 돌파",
        "fn": sig_keltner,
        "params": [
            {"name": "ema_n", "type": "int", "default": 20,
             "min": 10, "max": 60, "label": "EMA 기간"},
            {"name": "atr_n", "type": "int", "default": 10,
             "min": 5, "max": 30, "label": "ATR 기간"},
            {"name": "mult", "type": "int", "default": 2,
             "min": 1, "max": 4, "label": "ATR 배수"},
        ],
    },
    "adx_trend": {
        "label": "ADX 트렌드+모멘텀",
        "desc": "ADX>25 강한 트렌드 + 모멘텀 양수",
        "fn": sig_adx_trend,
        "params": [
            {"name": "adx_n", "type": "int", "default": 14,
             "min": 7, "max": 30, "label": "ADX 기간"},
            {"name": "adx_th", "type": "int", "default": 25,
             "min": 15, "max": 40, "label": "ADX 임계"},
            {"name": "mom_n", "type": "int", "default": 14,
             "min": 5, "max": 30, "label": "모멘텀 기간"},
        ],
    },
    "roc": {
        "label": "ROC 모멘텀",
        "desc": "Rate of Change 임계 돌파",
        "fn": sig_roc,
        "params": [
            {"name": "window", "type": "int", "default": 12,
             "min": 5, "max": 30, "label": "기간"},
            {"name": "th", "type": "int", "default": 2,
             "min": 1, "max": 10, "label": "임계 %"},
        ],
    },
    "triple_screen": {
        "label": "Triple Screen (Elder)",
        "desc": "장기 EMA + RSI 과매도",
        "fn": sig_triple_screen,
        "params": [
            {"name": "ema_long", "type": "int", "default": 50,
             "min": 20, "max": 200, "label": "장기 EMA"},
            {"name": "rsi_n", "type": "int", "default": 14,
             "min": 7, "max": 30, "label": "RSI 기간"},
        ],
    },
    "engulfing": {
        "label": "Engulfing + 추세",
        "desc": "장기 EMA 위 bullish engulfing",
        "fn": sig_engulfing,
        "params": [
            {"name": "ema_n", "type": "int", "default": 50,
             "min": 20, "max": 200, "label": "EMA 기간"},
        ],
    },
    "heikin_ashi": {
        "label": "Heikin-Ashi 추세",
        "desc": "HA 캔들 N개 연속 동일 방향",
        "fn": sig_heikin_ashi,
        "params": [
            {"name": "confirm", "type": "int", "default": 2,
             "min": 1, "max": 5, "label": "확정 봉수"},
        ],
    },
    "dva": {
        "label": "DVA (Daily Value Area)",
        "desc": "value area failed auction (일봉 단순화)",
        "fn": sig_dva,
        "params": [
            {"name": "window", "type": "int", "default": 20,
             "min": 10, "max": 60, "label": "기간"},
        ],
    },
}
