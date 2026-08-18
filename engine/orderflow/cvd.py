"""
CVD (Cumulative Volume Delta) 모듈
==================================
- volume_delta  : 봉별 매수-매도 추정 거래량 차이
- cvd           : 누적 거래량 델타

원리
----
Tick rule: close 가 전 봉보다 오르면 그 봉 거래량은 매수 우위로,
내리면 매도 우위로 간주한다.
실제 tick 데이터가 있으면 그쪽이 정확하지만, OHLCV 만으로
근사할 수 있는 표준 방법이다.

해석
----
- CVD 가 가격과 동행 → 추세 신뢰
- CVD 가 가격과 다이버전스 → 추세 약화 신호
"""
from __future__ import annotations
import numpy as np
import pandas as pd


def cvd(df: pd.DataFrame) -> pd.Series:
    """
    누적 거래량 델타 (Cumulative Volume Delta) 시리즈.

    Parameters
    ----------
    df : OHLCV (close, volume 컬럼 필요)
    """
    sign = np.sign(df["close"].diff().fillna(0))
    delta = sign * df["volume"]
    return delta.cumsum().rename("cvd")


def volume_delta(df: pd.DataFrame) -> pd.Series:
    """봉별 거래량 델타 (cumsum 안 된 raw)."""
    sign = np.sign(df["close"].diff().fillna(0))
    return (sign * df["volume"]).rename("volume_delta")
