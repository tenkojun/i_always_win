"""
ML 피처 엔지니어링 모듈
=======================
OHLCV 시계열로부터 머신러닝/딥러닝에 사용할 표준 피처 집합을 만든다.

생성되는 피처 설명
------------------
- ret_1, ret_5, ret_10, ret_20  : 각 기간 수익률
- log_ret_1                     : 로그수익률 (분포가 더 안정적)
- range                         : (고가-저가)/종가 — 변동성 단봉 측정
- co_ratio                      : (종가-시가)/(고-저) — 봉 강도
- vol                           : 거래량
- vol_h                         : 기간 h 의 수익률 표준편차
- sma_h                         : 기간 h 이동평균 대비 괴리율
- vol_z_h                       : 거래량 z-score
- rsi_14                        : RSI(14)
- target                        : 분류 라벨 (다음 h일 수익률 > 0 ? 1 : 0)
- target_reg                    : 회귀 라벨 (다음 h일 실제 수익률)
"""
from __future__ import annotations
import numpy as np
import pandas as pd


def make_features(df: pd.DataFrame,
                  horizons=(1, 5, 10, 20),
                  add_target: bool = True,
                  target_horizon: int = 5) -> pd.DataFrame:
    """
    Parameters
    ----------
    df              : OHLCV (open, high, low, close, volume)
    horizons        : 피처 계산 윈도 묶음
    add_target      : True 면 target / target_reg 컬럼 추가
    target_horizon  : 라벨이 가리키는 미래 봉 수
    """
    df = df.copy()
    df.columns = [c.lower() for c in df.columns]

    out = pd.DataFrame(index=df.index)
    out["ret_1"]     = df["close"].pct_change()
    out["log_ret_1"] = np.log(df["close"] / df["close"].shift())
    out["range"]     = (df["high"] - df["low"]) / df["close"]
    out["co_ratio"]  = (df["close"] - df["open"]) / (df["high"] - df["low"]).replace(0, np.nan)
    out["vol"]       = df["volume"]

    for h in horizons:
        out[f"ret_{h}"] = df["close"].pct_change(h)
        if h >= 2:
            out[f"vol_{h}"] = df["close"].pct_change().rolling(h).std()
            out[f"sma_{h}"] = df["close"].rolling(h).mean() / df["close"] - 1
            std_v = df["volume"].rolling(h).std().replace(0, np.nan)
            out[f"vol_z_{h}"] = ((df["volume"] - df["volume"].rolling(h).mean()) / std_v)

    # RSI(14)
    diff = df["close"].diff()
    up = diff.clip(lower=0).rolling(14).mean()
    dn = (-diff.clip(upper=0)).rolling(14).mean()
    out["rsi_14"] = 100 - 100 / (1 + up / dn.replace(0, np.nan))

    if add_target:
        fwd = df["close"].shift(-target_horizon) / df["close"] - 1
        out["target"]     = (fwd > 0).astype(int)
        out["target_reg"] = fwd

    return out.dropna()
