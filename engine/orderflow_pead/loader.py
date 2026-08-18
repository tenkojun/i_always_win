"""
데이터 로더
============
- load_ohlcv      : yfinance 실데이터 (1m/5m/15m/1h/1d)
- synth_orderflow : OHLCV → 합성 1분 델타 (실 tick 데이터 없을 때)
- synth_earnings  : 분기별 합성 earnings event + SUE
"""
from __future__ import annotations

import datetime as dt
import numpy as np
import pandas as pd
from typing import Optional, Dict, Any


# ── 1) 실 OHLCV ───────────────────────────────────────────────
def load_ohlcv(ticker: str = "AAPL",
               period_days: int = 365,
               interval: str = "1d") -> pd.DataFrame:
    """yfinance로 OHLCV fetch. 실패 시 합성으로 폴백."""
    try:
        import yfinance as yf
        end = pd.Timestamp.now()
        start = end - pd.Timedelta(days=period_days)
        df = yf.Ticker(ticker).history(
            start=start.strftime("%Y-%m-%d"),
            end=end.strftime("%Y-%m-%d"),
            interval=interval,
        )
        if df is not None and not df.empty:
            df = df.rename(columns=str.lower)
            df = df[["open", "high", "low", "close", "volume"]].dropna()
            df.index = pd.to_datetime(df.index)
            return df
    except Exception:
        pass
    return _synth_ohlcv(period_days, interval)


def _synth_ohlcv(period_days: int, interval: str) -> pd.DataFrame:
    """오프라인 폴백 — 기하 브라운 운동."""
    bars_per_day = {"1m": 390, "5m": 78, "15m": 26, "1h": 7, "1d": 1}.get(
        interval, 1)
    n = period_days * bars_per_day
    rng = np.random.default_rng(42)
    rets = rng.normal(0.0003, 0.015, n)
    close = 100 * np.cumprod(1 + rets)
    high = close * (1 + np.abs(rng.normal(0, 0.005, n)))
    low = close * (1 - np.abs(rng.normal(0, 0.005, n)))
    open_ = np.r_[close[0], close[:-1]]
    vol = rng.lognormal(13, 0.5, n)
    idx_freq = {"1m": "1T", "5m": "5T", "15m": "15T", "1h": "1H", "1d": "1D"}.get(
        interval, "1D")
    idx = pd.date_range(end=pd.Timestamp.now().normalize(),
                         periods=n, freq=idx_freq)
    return pd.DataFrame({"open": open_, "high": high, "low": low,
                         "close": close, "volume": vol}, index=idx)


# ── 2) 합성 Orderflow Delta ────────────────────────────────────
def synth_orderflow(ohlcv: pd.DataFrame,
                    aggressor_bias: float = 0.55,
                    noise_scale: float = 0.15) -> pd.DataFrame:
    """
    OHLCV → 합성 (bid_vol, ask_vol, delta) DataFrame.

    모델 가설:
      - 양봉(close > open)이면 매수 측 공격성 우세 (aggressor_bias)
      - 음봉이면 매도 측 공격성 우세
      - 봉 안에서의 노이즈는 정규분포

    실 tick 데이터가 없을 때만 사용. 실데이터 있으면 같은 컬럼 형태로
    교체하면 됨 (인터페이스 호환).
    """
    if ohlcv is None or ohlcv.empty:
        return pd.DataFrame()
    close = ohlcv["close"].values
    open_ = ohlcv["open"].values
    vol = ohlcv["volume"].values.astype(float)
    n = len(vol)
    rng = np.random.default_rng(7)
    # 봉별 매수 비율 (0~1)
    up = (close > open_).astype(float)
    base_buy_ratio = np.where(up > 0, aggressor_bias, 1.0 - aggressor_bias)
    noise = rng.normal(0, noise_scale, n)
    buy_ratio = np.clip(base_buy_ratio + noise, 0.05, 0.95)
    ask_vol = vol * buy_ratio        # 공격적 매수 (ask에서 체결)
    bid_vol = vol * (1.0 - buy_ratio)  # 공격적 매도 (bid에서 체결)
    delta = ask_vol - bid_vol
    return pd.DataFrame({
        "bid_vol": bid_vol,
        "ask_vol": ask_vol,
        "delta":   delta,
    }, index=ohlcv.index)


# ── 3) 합성 Earnings (PEAD용) ──────────────────────────────────
def synth_earnings(ohlcv: pd.DataFrame,
                   freq: str = "Q",
                   surprise_std: float = 8.0) -> pd.DataFrame:
    """
    분기 단위 earnings event 생성.

    Columns:
      - event_date   : 발표일 (분기 마지막 영업일)
      - actual_eps   : 실제 EPS
      - estimate_eps : 컨센서스 EPS
      - surprise     : actual - estimate
      - surprise_pct : surprise / |estimate| * 100
      - sue          : surprise / std(surprises)   (Z-score)
    """
    if ohlcv is None or ohlcv.empty:
        return pd.DataFrame()
    # 분기 말 영업일
    q_ends = pd.date_range(ohlcv.index.min(), ohlcv.index.max(), freq="QE")
    rng = np.random.default_rng(101)
    est = rng.normal(1.5, 0.4, len(q_ends))
    # surprise는 가격 모멘텀과 살짝 상관 (현실성)
    price_mom = ohlcv["close"].pct_change(60).reindex(q_ends, method="nearest")
    price_mom = price_mom.fillna(0).values
    surprise = (rng.normal(0, surprise_std, len(q_ends))
                + price_mom * 30)
    actual = est + surprise / 100 * np.abs(est)
    df = pd.DataFrame({
        "event_date":   q_ends,
        "actual_eps":   actual,
        "estimate_eps": est,
        "surprise":     actual - est,
        "surprise_pct": surprise,
    })
    # SUE — Z-score across history
    df["sue"] = (df["surprise"] - df["surprise"].mean()) / (
        df["surprise"].std(ddof=1) + 1e-9)
    return df
