"""
애널리스트 목표주가 & 컨센서스
================================
소스 우선순위: FMP → Finnhub → yfinance (키 없이도 항상 동작)

반환 dict
---------
current_price   : 현재가
target_mean     : 평균 목표주가
target_high     : 최고 목표주가
target_low      : 최저 목표주가
target_median   : 중앙값 목표주가
upside_pct      : 현재가 대비 평균 목표주가 상승여력 (%)
n_analysts      : 분석 애널리스트 수
recommendation  : 컨센서스 (Strong Buy / Buy / Hold / Sell / Strong Sell)
rec_score       : 1(강매수)~5(강매도) 연속 점수
source          : 데이터 출처
"""
from __future__ import annotations
from typing import Dict, Optional
import requests

from engine.data.keyconfig import get_key


# ── 추천 점수 → 한글 ──────────────────────────────────────────────
def _rec_label(mean_score: Optional[float], key: Optional[str]) -> str:
    if key:
        m = {"strong_buy": "Strong Buy 강력매수",
             "buy":        "Buy 매수",
             "hold":       "Hold 중립",
             "sell":       "Sell 매도",
             "strong_sell": "Strong Sell 강력매도"}.get(str(key).lower(), key)
        return m
    if mean_score is None:
        return "N/A"
    if mean_score <= 1.5:  return "Strong Buy 강력매수"
    if mean_score <= 2.5:  return "Buy 매수"
    if mean_score <= 3.5:  return "Hold 중립"
    if mean_score <= 4.5:  return "Sell 매도"
    return "Strong Sell 강력매도"


def _upside(current: float, target: float) -> float:
    if not current:
        return 0.0
    return round((target - current) / current * 100, 2)


# ── 1) yfinance (키 불필요, 기본 소스) ───────────────────────────
def _from_yfinance(ticker: str) -> Optional[Dict]:
    try:
        import yfinance as yf
        info = yf.Ticker(ticker).info or {}
        cur  = info.get("currentPrice") or info.get("regularMarketPrice")
        mean = info.get("targetMeanPrice")
        if not mean:
            return None
        return {
            "current_price":  round(float(cur),  4) if cur  else None,
            "target_mean":    round(float(mean),  4),
            "target_high":    round(float(info.get("targetHighPrice",  mean)), 4),
            "target_low":     round(float(info.get("targetLowPrice",   mean)), 4),
            "target_median":  round(float(info.get("targetMedianPrice", mean)), 4),
            "upside_pct":     _upside(cur, mean) if cur else None,
            "n_analysts":     int(info.get("numberOfAnalystOpinions", 0)),
            "recommendation": _rec_label(
                info.get("recommendationMean"),
                info.get("recommendationKey"),
            ),
            "rec_score":      round(float(info.get("recommendationMean", 3)), 2),
            "source":         "Yahoo Finance",
        }
    except Exception:
        return None


# ── 2) FMP (키 있으면) ────────────────────────────────────────────
def _from_fmp(ticker: str) -> Optional[Dict]:
    key = get_key("fmp")
    if not key:
        return None
    try:
        url = f"https://financialmodelingprep.com/api/v4/analyst-price-targets-summary?symbol={ticker}&apikey={key}"
        r = requests.get(url, timeout=8)
        data = r.json()
        if not data or not isinstance(data, list):
            return None
        d = data[0]
        cur = d.get("lastPrice") or d.get("stockPrice")
        mean = d.get("averageTarget") or d.get("targetConsensus")
        if not mean:
            return None
        return {
            "current_price":  round(float(cur),  4) if cur  else None,
            "target_mean":    round(float(mean),  4),
            "target_high":    round(float(d.get("highTarget",  mean)), 4),
            "target_low":     round(float(d.get("lowTarget",   mean)), 4),
            "target_median":  round(float(d.get("medianTarget", mean)), 4),
            "upside_pct":     _upside(cur, mean) if cur else None,
            "n_analysts":     int(d.get("numberOfAnalysts", 0)),
            "recommendation": _rec_label(None, d.get("consensusRating")),
            "rec_score":      None,
            "source":         "FMP",
        }
    except Exception:
        return None


# ── 3) Finnhub (키 있으면) ────────────────────────────────────────
def _from_finnhub(ticker: str) -> Optional[Dict]:
    key = get_key("finnhub")
    if not key:
        return None
    try:
        url = f"https://finnhub.io/api/v1/stock/price-target?symbol={ticker}&token={key}"
        r = requests.get(url, timeout=8)
        d = r.json()
        mean = d.get("targetMean")
        if not mean:
            return None
        cur = d.get("lastPrice")
        return {
            "current_price":  round(float(cur),  4) if cur  else None,
            "target_mean":    round(float(mean),  4),
            "target_high":    round(float(d.get("targetHigh",   mean)), 4),
            "target_low":     round(float(d.get("targetLow",    mean)), 4),
            "target_median":  round(float(d.get("targetMedian", mean)), 4),
            "upside_pct":     _upside(cur, mean) if cur else None,
            "n_analysts":     int(d.get("numberOfAnalysts", 0)),
            "recommendation": _rec_label(None, None),
            "rec_score":      None,
            "source":         "Finnhub",
        }
    except Exception:
        return None


# ── 공개 함수 ─────────────────────────────────────────────────────
def get_analyst_targets(ticker: str) -> Dict:
    """
    FMP → Finnhub → yfinance 순으로 시도, 성공한 첫 소스 반환.
    실패 시 empty dict.
    """
    for fn in (_from_fmp, _from_finnhub, _from_yfinance):
        res = fn(ticker)
        if res:
            return res
    return {}
