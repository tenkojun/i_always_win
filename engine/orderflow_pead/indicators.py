"""
표준 인디케이터 (vectorbt 우선, 없으면 pandas 폴백)
====================================================
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from typing import Optional


def _vbt():
    try:
        import vectorbt as vbt
        return vbt
    except Exception:
        return None


# ── SMA / EMA ─────────────────────────────────────────────────
def sma(price: pd.Series, window: int) -> pd.Series:
    vbt = _vbt()
    if vbt is not None:
        return vbt.MA.run(price, window).ma.squeeze()
    return price.rolling(window).mean()


def ema(price: pd.Series, span: int) -> pd.Series:
    return price.ewm(span=span, adjust=False).mean()


# ── MACD ──────────────────────────────────────────────────────
def macd(price: pd.Series, fast: int = 12, slow: int = 26,
         signal: int = 9) -> pd.DataFrame:
    vbt = _vbt()
    if vbt is not None:
        m = vbt.MACD.run(price, fast_window=fast, slow_window=slow,
                          signal_window=signal)
        out = pd.DataFrame({
            "macd":   m.macd.squeeze(),
            "signal": m.signal.squeeze(),
            "hist":   m.hist.squeeze(),
        })
        return out
    # 폴백
    ef = ema(price, fast); es = ema(price, slow)
    line = ef - es
    sig = ema(line, signal)
    return pd.DataFrame({"macd": line, "signal": sig, "hist": line - sig})


# ── RSI ───────────────────────────────────────────────────────
def rsi(price: pd.Series, window: int = 14) -> pd.Series:
    vbt = _vbt()
    if vbt is not None:
        return vbt.RSI.run(price, window=window).rsi.squeeze()
    delta = price.diff()
    gain = delta.clip(lower=0).rolling(window).mean()
    loss = (-delta.clip(upper=0)).rolling(window).mean()
    rs = gain / (loss.replace(0, np.nan))
    return (100 - 100 / (1 + rs)).fillna(50)


# ── ATR ───────────────────────────────────────────────────────
def atr(ohlcv: pd.DataFrame, window: int = 14) -> pd.Series:
    vbt = _vbt()
    high, low, close = ohlcv["high"], ohlcv["low"], ohlcv["close"]
    if vbt is not None:
        return vbt.ATR.run(high, low, close, window=window).atr.squeeze()
    pc = close.shift(1)
    tr = pd.concat([(high - low),
                    (high - pc).abs(),
                    (low - pc).abs()], axis=1).max(axis=1)
    return tr.rolling(window).mean()
