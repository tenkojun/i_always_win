"""
데이터 소스 패키지 — 다중소스 OHLCV/펀더멘털/뉴스 수집.

공개 진입점
-----------
  fetch_ohlcv_best(ticker, start, ...)   : 우선순위 OHLCV + 교차검증
  fetch_fundamentals_best(ticker)        : 다중소스 펀더멘털 병합
  fetch_news_best(ticker, limit)         : Finnhub→Yahoo 뉴스
  available_sources()                    : 가용 소스 상태
"""
from .reconcile import (fetch_ohlcv_best, fetch_fundamentals_best,
                        fetch_news_best, available_sources)
from .base import Provider, _normalize
from .free_nokey import YahooProvider, StooqProvider
from .keyed import FinnhubProvider, AlphaVantageProvider, FMPProvider

__all__ = [
    "fetch_ohlcv_best", "fetch_fundamentals_best",
    "fetch_news_best", "available_sources",
    "Provider", "YahooProvider", "StooqProvider",
    "FinnhubProvider", "AlphaVantageProvider", "FMPProvider",
]
