"""
================================================================
  다중소스 조율기  (Reconcile / Orchestrator)
================================================================
여러 데이터 소스를 우선순위로 시도하고, 둘 이상 성공하면
**교차검증**하여 이상치를 탐지한다. 모든 결과에 출처·신뢰도
라벨을 부착한다(정직한 표기 원칙).

OHLCV 우선순위
--------------
1. 키 소스(품질·정확도 우수): FMP > AlphaVantage > Finnhub
2. 무키 기본(항상 가용): Yahoo > Stooq
→ 키가 없으면 자동으로 Yahoo/Stooq 만으로 완전 동작.

교차검증
--------
2개 이상 소스 성공 시, 최근 종가의 상대편차를 비교:
- 편차 < 1%   : 일치(높은 신뢰도)
- 1~5%        : 경미한 불일치(주의 라벨)
- > 5%        : 소스 충돌(경고 라벨, 우선순위 소스 채택)
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from .base import Provider
from .free_nokey import YahooProvider, StooqProvider
from .keyed import (FinnhubProvider, AlphaVantageProvider, FMPProvider)

# Optional: KIS Provider — 키 있으면 최상위, 없으면 자동 스킵
try:
    from .kis_provider import KISProvider
    _HAS_KIS = True
except Exception:
    _HAS_KIS = False

# Finnhub 무료 티어는 /stock/candle 차단 → OHLCV 후보에서 제외.
# (유료 업그레이드 시 capabilities에 "ohlcv" 복원하면 자동 포함됨)
#
# 우선순위 정책:
#   1) KIS — 한국투자증권 (한국 + 미국 모두, 키 있을 때만)
#   2) FMP / AV — 키 있을 때 (미국 시장)
#   3) Yahoo / Stooq — 무키 폴백 (항상)
# → KIS 실패해도 자동으로 다음 단계로 fallback
_OHLCV_ORDER = []
if _HAS_KIS:
    _OHLCV_ORDER.append(KISProvider)
_OHLCV_ORDER.extend([
    FMPProvider, AlphaVantageProvider,
    YahooProvider, StooqProvider,
])
_FUND_ORDER = [FMPProvider, AlphaVantageProvider,
               FinnhubProvider, YahooProvider]


def _instances(order) -> List[Provider]:
    out = []
    for cls in order:
        try:
            out.append(cls())
        except Exception:
            pass
    return out


def available_sources() -> Dict[str, bool]:
    """현재 가용한 소스 목록(키 보유 여부 반영)."""
    status = {}
    for cls in [YahooProvider, StooqProvider, FinnhubProvider,
                AlphaVantageProvider, FMPProvider]:
        try:
            p = cls()
            status[p.name] = bool(p.is_available())
        except Exception:
            status[cls.name] = False
    return status


def fetch_ohlcv_best(ticker: str, start: str = "2010-01-01",
                     end: Optional[str] = None,
                     interval: str = "1d",
                     cross_validate: bool = True) -> Dict[str, Any]:
    """
    우선순위로 OHLCV 수집 + (가능하면) 교차검증.

    Returns
    -------
    {
      "df": 표준 OHLCV DataFrame,
      "primary": 채택 소스명,
      "tried": [(소스, 성공여부, 비고)],
      "cross_check": {다른소스: 최근종가편차%} 또는 {},
      "confidence": "high"|"medium"|"low",
      "label": 사람이 읽는 출처·신뢰도 한 줄,
    }
    """
    tried: List[Tuple[str, bool, str]] = []
    primary_df: Optional[pd.DataFrame] = None
    primary_name = ""
    secondary: List[Tuple[str, pd.DataFrame]] = []

    for p in _instances(_OHLCV_ORDER):
        if p.needs_key and not p.is_available():
            tried.append((p.name, False, "키 없음(건너뜀)"))
            continue
        try:
            df = p.fetch_ohlcv(ticker, start=start, end=end,
                               interval=interval)
            if df is None or df.empty:
                tried.append((p.name, False, "빈 데이터"))
                continue
            tried.append((p.name, True, f"{len(df)}행"))
            if primary_df is None:
                primary_df, primary_name = df, p.name
                if not cross_validate:
                    break
            else:
                secondary.append((p.name, df))
                if len(secondary) >= 2:
                    break
        except Exception as e:
            tried.append((p.name, False, f"실패: {str(e)[:40]}"))

    if primary_df is None:
        raise ValueError(
            f"'{ticker}' 모든 소스 실패: "
            + "; ".join(f"{n}({m})" for n, ok, m in tried if not ok))

    # ── 교차검증: 최근 공통일자 종가 비교 ──
    cross: Dict[str, float] = {}
    conf = "medium"
    for name, df2 in secondary:
        try:
            common = primary_df.index.intersection(df2.index)
            if len(common) >= 5:
                a = primary_df.loc[common, "close"].iloc[-1]
                b = df2.loc[common, "close"].iloc[-1]
                if a:
                    cross[name] = float(abs(a - b) / a * 100)
        except Exception:
            pass
    if cross:
        worst = max(cross.values())
        if worst < 1.0:
            conf = "high"
        elif worst < 5.0:
            conf = "medium"
        else:
            conf = "low"
    elif primary_name in ("yahoo", "stooq") and not secondary:
        conf = "medium"

    label = _make_label(primary_name, conf, cross, interval)
    return {
        "df": primary_df,
        "primary": primary_name,
        "tried": tried,
        "cross_check": cross,
        "confidence": conf,
        "label": label,
    }


def _make_label(primary: str, conf: str,
                cross: Dict[str, float], interval: str) -> str:
    delay = {"yahoo": "약 15분 지연", "stooq": "전일 종가",
             "finnhub": "실시간(무료 한도)",
             "alphavantage": "일별",
             "fmp": "일별"}.get(primary, "")
    base = f"출처: {primary.upper()} ({delay})"
    if cross:
        chk = ", ".join(f"{k}±{v:.1f}%" for k, v in cross.items())
        base += f" · 교차검증: {chk}"
    base += f" · 신뢰도: {conf.upper()}"
    if conf == "low":
        base += " ⚠ 소스 간 편차 큼 — 주의"
    return base


def fetch_fundamentals_best(ticker: str) -> Dict[str, Any]:
    """펀더멘털을 깊이 우선순위로 수집(여러 소스 병합)."""
    merged: Dict[str, Any] = {}
    sources_used: List[str] = []
    for p in _instances(_FUND_ORDER):
        if p.needs_key and not p.is_available():
            continue
        try:
            d = p.fetch_fundamentals(ticker)
            if d:
                sources_used.append(d.get("source", p.name))
                for k, v in d.items():
                    if k == "source":
                        continue
                    if merged.get(k) in (None, "") and v not in (None, ""):
                        merged[k] = v
        except Exception:
            pass
    merged["_sources"] = sources_used
    merged["_label"] = (f"펀더멘털 출처: {', '.join(sources_used)}"
                        if sources_used else "펀더멘털: 데이터 없음")
    return merged


def fetch_news_best(ticker: str, limit: int = 12) -> List[Dict]:
    """뉴스를 가용 소스에서 수집·중복제거(Finnhub 우선)."""
    out: List[Dict] = []
    seen = set()
    for p in _instances([FinnhubProvider, YahooProvider]):
        if p.needs_key and not p.is_available():
            continue
        try:
            ns = p.fetch_news(ticker, limit=limit)
            for n in (ns or []):
                k = n.get("title", "")[:60]
                if k and k not in seen:
                    seen.add(k)
                    out.append(n)
        except Exception:
            pass
    return out[:limit]
