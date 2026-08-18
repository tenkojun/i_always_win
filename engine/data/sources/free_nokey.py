"""
무키 기본 소스 2종 — 키가 하나도 없어도 시스템이 완전 동작.

- YahooProvider : yfinance, 1962~ 최장 시세, 펀더멘털/뉴스 일부
- StooqProvider : 가입·키 불필요, CSV로 장기 시세 백필
"""
from __future__ import annotations

import io
from typing import Optional

import pandas as pd

from .base import Provider, _normalize


class YahooProvider(Provider):
    name = "yahoo"
    needs_key = False
    capabilities = ("ohlcv", "fundamentals", "news")

    def fetch_ohlcv(self, ticker, start="2010-01-01", end=None,
                    interval="1d"):
        import yfinance as yf
        if start is None:
            df = yf.Ticker(ticker).history(period="max",
                                           interval=interval,
                                           auto_adjust=True)
        else:
            df = yf.download(ticker, start=start, end=end,
                             interval=interval, auto_adjust=True,
                             progress=False)
        return _normalize(df)

    def fetch_fundamentals(self, ticker):
        try:
            import yfinance as yf
            info = yf.Ticker(ticker).info or {}
            return {
                "source": "yahoo",
                "pe": info.get("trailingPE"),
                "pb": info.get("priceToBook"),
                "roe": info.get("returnOnEquity"),
                "market_cap": info.get("marketCap"),
                "beta": info.get("beta"),
                "sector": info.get("sector"),
                "profit_margin": info.get("profitMargins"),
            }
        except Exception:
            return None

    def fetch_news(self, ticker, limit=10):
        try:
            import yfinance as yf
            out = []
            for n in (yf.Ticker(ticker).news or [])[:limit]:
                c = n.get("content", n)
                t = c.get("title") or n.get("title")
                if t:
                    out.append({"title": t, "source": "yahoo",
                                "link": (c.get("clickThroughUrl") or
                                         {}).get("url", "")})
            return out or None
        except Exception:
            return None


class StooqProvider(Provider):
    """Stooq: 키·가입 불필요. 장기 일별 시세 CSV."""
    name = "stooq"
    needs_key = False
    capabilities = ("ohlcv",)

    @staticmethod
    def _stooq_symbol(ticker: str) -> str:
        t = ticker.strip().lower()
        if t.endswith(".ks") or t.endswith(".kq"):
            return t.split(".")[0] + ".kr"
        if "." not in t and "-" not in t:
            return t + ".us"
        return t

    def fetch_ohlcv(self, ticker, start="2010-01-01", end=None,
                    interval="1d"):
        import requests
        sym = self._stooq_symbol(ticker)
        url = f"https://stooq.com/q/d/l/?s={sym}&i=d"
        r = requests.get(url, timeout=12,
                         headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code != 200 or len(r.content) < 50:
            raise ValueError(f"Stooq 응답 없음: {sym}")
        df = pd.read_csv(io.StringIO(r.text))
        if "Date" not in df.columns or df.empty:
            raise ValueError(f"Stooq 데이터 형식 오류: {sym}")
        df = _normalize(df)
        if start:
            df = df[df.index >= pd.to_datetime(start)]
        if end:
            df = df[df.index <= pd.to_datetime(end)]
        if df.empty:
            raise ValueError("Stooq: 기간 내 데이터 없음")
        return df
