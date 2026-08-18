"""
pf_stats.py — vectorbt Portfolio 풍부 지표 추출 (P3)
======================================================
- extract_full_stats(pf)      : pf.stats() 30+ 지표를 깔끔한 dict로
- monthly_returns_heatmap(pf) : 월별 수익률 매트릭스 (Plotly heatmap dict)
- qs_extra_stats(returns)     : quantstats 설치 시 추가 지표 (skew/kurtosis/VaR/CVaR/tail/calmar)

사용처: run_backtest() → response["stats_full"] / ["monthly_heatmap"] / ["qs_extra"]
"""
from __future__ import annotations

import math
import numpy as np
import pandas as pd
from typing import Any, Dict, List, Optional


# ════════════════════════════════════════════════════════════
#  pf.stats() 추출 + 안전 변환
# ════════════════════════════════════════════════════════════
def _safe(v: Any) -> Any:
    """JSON-safe 변환 — nan/inf/Timedelta/Timestamp 모두 처리."""
    if v is None:
        return None
    if isinstance(v, (pd.Timestamp, pd.Period)):
        try:
            return str(v)
        except Exception:
            return None
    if isinstance(v, pd.Timedelta):
        try:
            return f"{v.days}일 {v.seconds // 3600}시간"
        except Exception:
            return str(v)
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (np.floating, float)):
        f = float(v)
        if math.isnan(f) or math.isinf(f):
            return None
        return round(f, 6)
    if isinstance(v, (int, bool)):
        return v
    if isinstance(v, str):
        return v
    # fallback
    try:
        s = str(v)
        return s if len(s) < 200 else s[:200]
    except Exception:
        return None


# pf.stats()의 영어 키 → 한글 라벨 + 그룹 매핑
# (모든 키는 Optional — 없으면 자동 무시)
_STATS_GROUPS: List[Dict[str, Any]] = [
    {"label": "기간", "items": [
        ("Start",                  "시작"),
        ("End",                    "종료"),
        ("Period",                 "기간"),
    ]},
    {"label": "수익률", "items": [
        ("Start Value",            "초기자본"),
        ("End Value",              "최종자본"),
        ("Total Return [%]",       "총수익률 %"),
        ("Benchmark Return [%]",   "벤치마크수익률 %"),
        ("Annualized Return [%]",  "연환산수익률 %"),
    ]},
    {"label": "리스크/조정수익", "items": [
        ("Annualized Volatility [%]","연환산변동성 %"),
        ("Max Drawdown [%]",         "최대낙폭 %"),
        ("Max Drawdown Duration",    "MDD 회복기간"),
        ("Sharpe Ratio",             "샤프"),
        ("Sortino Ratio",            "소르티노"),
        ("Calmar Ratio",             "칼마"),
        ("Omega Ratio",              "오메가"),
    ]},
    {"label": "트레이드", "items": [
        ("Total Trades",                "총트레이드"),
        ("Total Closed Trades",         "청산 완료"),
        ("Total Open Trades",           "미청산"),
        ("Open Trade PnL",              "미청산 PnL"),
        ("Win Rate [%]",                "승률 %"),
        ("Best Trade [%]",              "최고 트레이드 %"),
        ("Worst Trade [%]",             "최악 트레이드 %"),
        ("Avg Winning Trade [%]",       "평균 수익 트레이드 %"),
        ("Avg Losing Trade [%]",        "평균 손실 트레이드 %"),
        ("Avg Winning Trade Duration",  "평균 수익 보유"),
        ("Avg Losing Trade Duration",   "평균 손실 보유"),
        ("Profit Factor",               "Profit Factor"),
        ("Expectancy",                  "Expectancy/트레이드"),
    ]},
    {"label": "노출/비용", "items": [
        ("Max Gross Exposure [%]",  "최대 노출 %"),
        ("Total Fees Paid",         "총 수수료"),
    ]},
]


def extract_full_stats(pf) -> Dict[str, Any]:
    """vectorbt Portfolio.stats() → 그룹별 dict.

    반환 형식:
      {"groups": [
         {"label": "기간",   "items": [{"key": "Start", "ko": "시작", "value": "2023-01-01"}, ...]},
         ...
      ], "raw": {모든 원본 키: 변환된 값}}
    """
    if pf is None:
        return {"groups": [], "raw": {}}
    try:
        st = pf.stats()  # pandas Series
    except Exception:
        return {"groups": [], "raw": {}}
    if st is None:
        return {"groups": [], "raw": {}}
    raw: Dict[str, Any] = {}
    try:
        for k, v in st.items():
            raw[str(k)] = _safe(v)
    except Exception:
        pass
    groups = []
    used = set()
    for grp in _STATS_GROUPS:
        items = []
        for en, ko in grp["items"]:
            if en in raw:
                items.append({"key": en, "ko": ko, "value": raw[en]})
                used.add(en)
        if items:
            groups.append({"label": grp["label"], "items": items})
    # 매핑 안 된 기타 키
    extras = []
    for k, v in raw.items():
        if k not in used:
            extras.append({"key": k, "ko": k, "value": v})
    if extras:
        groups.append({"label": "기타", "items": extras})
    return {"groups": groups, "raw": raw}


# ════════════════════════════════════════════════════════════
#  월별 수익률 히트맵 (Plotly)
# ════════════════════════════════════════════════════════════
_MONTHS = ["Jan","Feb","Mar","Apr","May","Jun",
           "Jul","Aug","Sep","Oct","Nov","Dec"]


def monthly_returns_heatmap(pf, title: str = "Monthly Returns (%)") -> Dict[str, Any]:
    """월별 수익률 매트릭스 → Plotly heatmap figure dict."""
    if pf is None:
        return {"data": [], "layout": {}}
    try:
        rets = pf.returns()
        if rets is None or len(rets) < 2:
            return {"data": [], "layout": {}}
    except Exception:
        return {"data": [], "layout": {}}

    # 인덱스가 DatetimeIndex가 아닐 수 있음 → 변환
    if not isinstance(rets.index, pd.DatetimeIndex):
        try:
            rets.index = pd.to_datetime(rets.index)
        except Exception:
            return {"data": [], "layout": {}}

    # 월별 복리 수익률
    try:
        monthly = rets.resample("M").apply(lambda x: (1 + x).prod() - 1)
    except Exception:
        return {"data": [], "layout": {}}
    if monthly is None or len(monthly) == 0:
        return {"data": [], "layout": {}}

    years = sorted(set(monthly.index.year.tolist()))
    matrix: List[List[Optional[float]]] = []
    text:   List[List[str]] = []
    for y in years:
        row: List[Optional[float]] = [None] * 12
        trow: List[str] = [""] * 12
        for ts, v in monthly.items():
            if ts.year == y:
                pct = float(v) * 100
                if math.isnan(pct) or math.isinf(pct):
                    continue
                row[ts.month - 1]  = pct
                trow[ts.month - 1] = f"{pct:+.1f}%"
        matrix.append(row)
        text.append(trow)

    data = [{
        "type": "heatmap",
        "x": _MONTHS,
        "y": [str(y) for y in years],
        "z": matrix,
        "text": text,
        "texttemplate": "%{text}",
        "textfont": {"size": 9, "color": "#fff"},
        "colorscale": [
            [0.0,  "#4d9cff"],   # 짙은 파랑 (큰 손실)
            [0.45, "#16202e"],   # 중립 어두움
            [0.55, "#16202e"],
            [1.0,  "#ff5a52"],   # 빨강 (큰 수익) — 한국식
        ],
        "zmid": 0,
        "showscale": True,
        "colorbar": {
            "tickfont":  {"color": "#cfe6ec"},
            "title":     {"text": "%", "font": {"color": "#cfe6ec"}},
            "thickness": 10,
        },
        "hovertemplate": "%{y} %{x}<br>%{z:+.2f}%<extra></extra>",
    }]
    layout = {
        "title":  {"text": title, "font": {"color": "#3df0ff", "size": 12},
                   "x": 0.02, "xanchor": "left"},
        "paper_bgcolor": "#05070a",
        "plot_bgcolor":  "#0a0f16",
        "font":   {"color": "#cfe6ec", "size": 10,
                   "family": "JetBrains Mono, monospace"},
        "xaxis":  {"side": "top", "color": "#cfe6ec", "showgrid": False},
        "yaxis":  {"autorange": "reversed", "color": "#cfe6ec",
                   "showgrid": False},
        "margin": {"l": 50, "r": 15, "t": 50, "b": 20},
        "height": max(180, 32 + 26 * max(1, len(years))),
    }
    return {"data": data, "layout": layout}


# ════════════════════════════════════════════════════════════
#  quantstats 추가 지표 (optional)
# ════════════════════════════════════════════════════════════
def qs_extra_stats(returns: Optional[pd.Series]) -> Dict[str, Any]:
    """quantstats 설치 시 핵심 추가 지표 dict. 없으면 빈 dict."""
    if returns is None or len(returns) < 10:
        return {}
    try:
        import quantstats as qs  # type: ignore
    except Exception:
        # quantstats 미설치 시 핵심만 수동 계산 (skew/kurtosis/VaR/CVaR)
        r = pd.Series(returns).dropna()
        if len(r) < 10:
            return {}
        try:
            from scipy.stats import skew as _skew, kurtosis as _kurt
            sk = float(_skew(r.values))
            kt = float(_kurt(r.values))
        except Exception:
            sk = float(r.skew())
            kt = float(r.kurt())
        var95  = float(np.percentile(r.values, 5))
        cvar95 = float(r.values[r.values <= var95].mean()) if (r.values <= var95).any() else var95
        tail   = float(abs(np.percentile(r.values, 95) / var95)) if var95 != 0 else 0.0
        return {
            "skew":      _safe(sk),
            "kurtosis":  _safe(kt),
            "var_95":    _safe(var95),
            "cvar_95":   _safe(cvar95),
            "tail_ratio":_safe(tail),
            "_source":   "manual (scipy)",
        }
    # quantstats 가용 시 사용
    try:
        r = pd.Series(returns).dropna()
        return {
            "skew":              _safe(qs.stats.skew(r)),
            "kurtosis":          _safe(qs.stats.kurtosis(r)),
            "var_95":            _safe(qs.stats.value_at_risk(r)),
            "cvar_95":           _safe(qs.stats.conditional_value_at_risk(r)),
            "tail_ratio":        _safe(qs.stats.tail_ratio(r)),
            "common_sense_ratio":_safe(qs.stats.common_sense_ratio(r)),
            "cpc_index":         _safe(qs.stats.cpc_index(r)),
            "outlier_win_ratio": _safe(qs.stats.outlier_win_ratio(r)),
            "outlier_loss_ratio":_safe(qs.stats.outlier_loss_ratio(r)),
            "ulcer_index":       _safe(qs.stats.ulcer_index(r)),
            "kelly_criterion":   _safe(qs.stats.kelly_criterion(r)),
            "_source":           "quantstats",
        }
    except Exception:
        return {}
