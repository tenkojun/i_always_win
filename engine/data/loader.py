"""
데이터 로더 모듈
================
종목 티커(예: 'AAPL', 'TSLA', '005930.KS')를 입력받아 OHLCV
데이터프레임을 반환한다.

지원 소스
---------
1. yfinance     : 인터넷 연결이 가능한 경우 (Colab 기본)
2. synthetic    : 인터넷이 없을 때 데모용 합성 데이터

반환 DataFrame 의 열
--------------------
- open   : 시가
- high   : 고가
- low    : 저가
- close  : 종가 (수정주가, auto_adjust=True)
- volume : 거래량
"""
from __future__ import annotations
from typing import Optional
import numpy as np
import pandas as pd


def load_ticker(
    ticker: str,
    start: str = "2018-01-01",
    end: Optional[str] = None,
    interval: str = "1d",
) -> pd.DataFrame:
    """
    yfinance 로 OHLCV 데이터를 가져온다.

    Parameters
    ----------
    ticker   : 종목 코드 (예: 'AAPL', 'NVDA', '005930.KS')
    start    : 시작일 (YYYY-MM-DD)
    end      : 종료일 (None 이면 최신)
    interval : '1d' / '1h' / '1wk' 등

    Returns
    -------
    pd.DataFrame  열: open, high, low, close, volume
    """
    try:
        import yfinance as yf
    except ImportError as e:
        raise ImportError(
            "yfinance 가 설치되지 않았습니다. !pip install yfinance"
        ) from e

    df = yf.download(
        ticker,
        start=start,
        end=end,
        interval=interval,
        auto_adjust=True,
        progress=False,
    )
    if df is None or df.empty:
        raise ValueError(f"'{ticker}' 데이터를 가져올 수 없습니다.")

    # MultiIndex columns 처리 (yfinance 최신 버전 호환)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df.columns = [c.lower() for c in df.columns]

    needed = ["open", "high", "low", "close", "volume"]
    df = df[[c for c in needed if c in df.columns]].dropna()
    df.index = pd.to_datetime(df.index)
    return df


def synthetic_ohlcv(
    n: int = 1500,
    start: str = "2020-01-01",
    seed: int = 1,
    annual_return: float = 0.08,
    annual_vol: float = 0.25,
) -> pd.DataFrame:
    """
    인터넷 없이 사용할 수 있는 합성 OHLCV 데이터를 만든다.

    Parameters
    ----------
    n              : 영업일 개수
    start          : 시작일
    annual_return  : 연 환산 기대수익률
    annual_vol     : 연 환산 변동성
    """
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range(start, periods=n)

    daily_mu = annual_return / 252
    daily_sd = annual_vol / np.sqrt(252)
    rets = rng.normal(daily_mu, daily_sd, n)

    close = 100.0 * np.exp(np.cumsum(rets))
    high  = close * (1 + np.abs(rng.normal(0, 0.005, n)))
    low   = close * (1 - np.abs(rng.normal(0, 0.005, n)))
    open_ = close * (1 + rng.normal(0, 0.002, n))
    vol   = rng.integers(1e5, 5e6, n).astype(float)

    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": vol},
        index=idx,
    )


def slice_by_lookback(df: pd.DataFrame, lookback_days: int) -> pd.DataFrame:
    """
    최근 N 거래일만 잘라낸다.

    Parameters
    ----------
    df            : OHLCV DataFrame (영업일 인덱스)
    lookback_days : 잘라낼 영업일 수 (예: 단기 60, 중기 252)
    """
    if lookback_days <= 0 or lookback_days >= len(df):
        return df
    return df.iloc[-lookback_days:].copy()
