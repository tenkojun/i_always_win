"""
키 기반 보강 소스 3종 — 키가 있으면 자동 활성, 없으면 조용히 건너뜀.

- FinnhubProvider     : 실시간 시세·뉴스·펀더멘털 (분당 60콜)
- AlphaVantageProvider: 일별 시세·기술지표·펀더멘털 (분당 5콜)
- FMPProvider         : 재무제표·SEC공시·밸류에이션 (일 250콜)

키는 engine.data.keyconfig 에서 가져온다(코드·로그에 미기록).
"""
from __future__ import annotations

import time
from typing import Optional

import pandas as pd

from .base import Provider, _normalize
from ..keyconfig import get_key


class FinnhubProvider(Provider):
    name = "finnhub"
    needs_key = True
    # 무료 티어: /stock/candle 차단(2024 변경). OHLCV는 더 이상 지원 X.
    # /quote(실시간), /stock/metric(펀더), /company-news는 무료 유지.
    capabilities = ("fundamentals", "news", "realtime")

    def is_available(self):
        return bool(get_key("finnhub"))

    def fetch_realtime(self, ticker):
        """현재가 한 점 — /quote 엔드포인트(무료 유지)."""
        import requests
        key = get_key("finnhub")
        if not key:
            return None
        try:
            r = requests.get("https://finnhub.io/api/v1/quote",
                             params={"symbol": ticker, "token": key},
                             timeout=8)
            j = r.json() or {}
            if not j.get("c"):
                return None
            return {
                "source": "finnhub",
                "price": j.get("c"),
                "change": j.get("d"),
                "change_pct": j.get("dp"),
                "high": j.get("h"),
                "low": j.get("l"),
                "open": j.get("o"),
                "prev_close": j.get("pc"),
                "timestamp": j.get("t"),
            }
        except Exception:
            return None

    def fetch_fundamentals(self, ticker):
        import requests
        key = get_key("finnhub")
        if not key:
            return None
        try:
            r = requests.get(
                "https://finnhub.io/api/v1/stock/metric",
                params={"symbol": ticker, "metric": "all",
                        "token": key}, timeout=10)
            m = (r.json() or {}).get("metric", {})
            return {
                "source": "finnhub",
                "pe": m.get("peTTM"),
                "pb": m.get("pbAnnual"),
                "roe": m.get("roeTTM"),
                "beta": m.get("beta"),
                "profit_margin": m.get("netProfitMarginTTM"),
                "52w_high": m.get("52WeekHigh"),
                "52w_low": m.get("52WeekLow"),
            }
        except Exception:
            return None

    def fetch_news(self, ticker, limit=10):
        import requests
        key = get_key("finnhub")
        if not key:
            return None
        try:
            today = pd.Timestamp.now()
            frm = (today - pd.Timedelta(days=14)).strftime("%Y-%m-%d")
            to = today.strftime("%Y-%m-%d")
            r = requests.get(
                "https://finnhub.io/api/v1/company-news",
                params={"symbol": ticker, "from": frm, "to": to,
                        "token": key}, timeout=10)
            out = []
            for n in (r.json() or [])[:limit]:
                if n.get("headline"):
                    out.append({"title": n["headline"],
                                "source": "finnhub",
                                "link": n.get("url", "")})
            return out or None
        except Exception:
            return None


class AlphaVantageProvider(Provider):
    name = "alphavantage"
    needs_key = True
    capabilities = ("ohlcv", "fundamentals")

    def is_available(self):
        return bool(get_key("alphavantage"))

    def fetch_ohlcv(self, ticker, start="2010-01-01", end=None,
                    interval="1d"):
        import requests
        key = get_key("alphavantage")
        if not key:
            raise ValueError("alphavantage 키 없음")
        # TIME_SERIES_DAILY_ADJUSTED는 2024년 유료 전환. 무료는 DAILY만.
        # outputsize=full도 2025년 유료 전환 → compact(약 100일)만 가능.
        # 무료 한도: 25콜/일 — 한도 도달 시 'Information' 키만 반환.
        r = requests.get("https://www.alphavantage.co/query", params={
            "function": "TIME_SERIES_DAILY",
            "symbol": ticker, "outputsize": "compact",
            "apikey": key}, timeout=15)
        j = r.json()
        ts = j.get("Time Series (Daily)")
        if not ts:
            info = j.get("Information") or j.get("Note") or ""
            if info:
                raise ValueError(f"alphavantage rate-limit/제한: "
                                 f"{info[:100]}")
            raise ValueError(f"alphavantage 응답 없음: {list(j.keys())[:1]}")
        rows = []
        for d, v in ts.items():
            rows.append({
                "date": d,
                "open": v["1. open"], "high": v["2. high"],
                "low": v["3. low"],
                "close": v["4. close"],
                "volume": v["5. volume"],
            })
        df = _normalize(pd.DataFrame(rows))
        if start:
            df = df[df.index >= pd.to_datetime(start)]
        if end:
            df = df[df.index <= pd.to_datetime(end)]
        return df

    def fetch_fundamentals(self, ticker):
        import requests
        key = get_key("alphavantage")
        if not key:
            return None
        try:
            r = requests.get("https://www.alphavantage.co/query",
                             params={"function": "OVERVIEW",
                                     "symbol": ticker,
                                     "apikey": key}, timeout=12)
            d = r.json() or {}
            if not d.get("Symbol"):
                return None
            def _f(x):
                try:
                    return float(x)
                except Exception:
                    return None
            return {
                "source": "alphavantage",
                "pe": _f(d.get("PERatio")),
                "pb": _f(d.get("PriceToBookRatio")),
                "roe": _f(d.get("ReturnOnEquityTTM")),
                "beta": _f(d.get("Beta")),
                "profit_margin": _f(d.get("ProfitMargin")),
                "sector": d.get("Sector"),
                "market_cap": _f(d.get("MarketCapitalization")),
            }
        except Exception:
            return None


class FMPProvider(Provider):
    name = "fmp"
    needs_key = True
    capabilities = ("ohlcv", "fundamentals")

    def is_available(self):
        return bool(get_key("fmp"))

    def fetch_ohlcv(self, ticker, start="2010-01-01", end=None,
                    interval="1d"):
        import requests
        key = get_key("fmp")
        if not key:
            raise ValueError("fmp 키 없음")
        # /api/v3/* 는 2025-08-31 폐기 → /stable/* 사용.
        # 응답이 {historical: [...]} → [{...}] 배열 직접으로 변경됨.
        r = requests.get(
            "https://financialmodelingprep.com/stable/"
            "historical-price-eod/full",
            params={"symbol": ticker, "apikey": key,
                    "from": start or "2010-01-01",
                    "to": end or pd.Timestamp.now().strftime(
                        "%Y-%m-%d")}, timeout=15)
        j = r.json()
        if isinstance(j, dict) and j.get("Error Message"):
            raise ValueError(f"fmp: {j['Error Message'][:120]}")
        if not isinstance(j, list) or not j:
            raise ValueError("fmp 응답 없음")
        df = pd.DataFrame(j)[
            ["date", "open", "high", "low", "close", "volume"]]
        return _normalize(df)

    def fetch_fundamentals(self, ticker):
        import requests
        key = get_key("fmp")
        if not key:
            return None
        try:
            # /api/v3/ratios-ttm/{t} → /stable/ratios-ttm?symbol={t}
            r = requests.get(
                "https://financialmodelingprep.com/stable/ratios-ttm",
                params={"symbol": ticker, "apikey": key}, timeout=12)
            arr = r.json()
            if not arr or not isinstance(arr, list):
                return None
            d = arr[0]
            # 신규 stable API는 ROE 필드를 ratios-ttm에서 제거함.
            # → ROE는 Finnhub의 roeTTM에서 fetch_fundamentals_best가 병합.
            return {
                "source": "fmp",
                "pe": (d.get("priceToEarningsRatioTTM")
                       or d.get("peRatioTTM")),
                "pb": (d.get("priceToBookRatioTTM")
                       or d.get("pbRatioTTM")),
                "profit_margin": d.get("netProfitMarginTTM"),
                "debt_equity": (d.get("debtToEquityRatioTTM")
                                or d.get("debtEquityRatioTTM")),
                "current_ratio": d.get("currentRatioTTM"),
                "dividend_yield": d.get("dividendYieldTTM"),
                "interest_coverage": d.get("interestCoverageRatioTTM"),
            }
        except Exception:
            return None
