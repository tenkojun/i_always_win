"""
================================================================
  데이터 소스 추상 인터페이스  (Provider Base)
================================================================
모든 데이터 소스는 동일한 계약을 따른다:

  OHLCV DataFrame
  - 인덱스: pandas DatetimeIndex (오름차순)
  - 열: open, high, low, close, volume  (소문자, float)

이 계약은 기존 engine/data/loader.py 와 100% 호환된다.
새 소스를 추가하려면 Provider 를 상속해 fetch_ohlcv 만 구현.
"""
from __future__ import annotations

import abc
from typing import Optional

import pandas as pd

_OHLCV_COLS = ["open", "high", "low", "close", "volume"]


def _normalize(df: pd.DataFrame) -> pd.DataFrame:
    """임의 소스 결과를 표준 OHLCV 계약으로 정규화."""
    if df is None or len(df) == 0:
        raise ValueError("빈 데이터")
    df = df.copy()
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df.columns = [str(c).strip().lower() for c in df.columns]
    alias = {
        "adj close": "close", "adj_close": "close", "adjclose": "close",
        "vol": "volume", "o": "open", "h": "high", "l": "low", "c": "close",
        "v": "volume", "t": "date", "timestamp": "date",
    }
    df = df.rename(columns={k: v for k, v in alias.items()
                            if k in df.columns})
    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"])
        df = df.set_index("date")
    df.index = pd.to_datetime(df.index)
    df = df[~df.index.duplicated(keep="last")].sort_index()
    for c in _OHLCV_COLS:
        if c not in df.columns:
            if c == "volume":
                df[c] = 0.0
            else:
                raise ValueError(f"필수 열 누락: {c}")
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df[_OHLCV_COLS].dropna(subset=["close"])
    if df.empty:
        raise ValueError("정규화 후 데이터 없음")
    return df


class Provider(abc.ABC):
    """데이터 소스 추상 베이스.

    OHLCV 미지원 소스(예: Finnhub 무료)는 fetch_ohlcv를 override 하지 않고,
    capabilities 튜플에서 "ohlcv"를 빼는 것으로 의도 표시. 호출되면
    NotImplementedError를 던져 호출자가 우선순위 다음 소스로 넘어가게 한다.
    """

    name: str = "base"
    needs_key: bool = False
    capabilities: tuple = ("ohlcv",)  # ohlcv / fundamentals / news / realtime

    def fetch_ohlcv(self, ticker: str, start: str = "2010-01-01",
                    end: Optional[str] = None,
                    interval: str = "1d") -> pd.DataFrame:
        """표준 OHLCV DataFrame 반환. 실패 시 예외."""
        raise NotImplementedError(
            f"{self.name}: OHLCV 미지원 (capabilities={self.capabilities})")

    def fetch_fundamentals(self, ticker: str) -> Optional[dict]:
        return None

    def fetch_news(self, ticker: str, limit: int = 10) -> Optional[list]:
        return None

    def is_available(self) -> bool:
        """키 필요 소스는 키 보유 시에만 가용."""
        return True
