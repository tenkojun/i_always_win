"""
키워드 → 자산 매핑 (룰 기반, LLM 없음)
=========================================
헤드라인에 포함된 키워드를 빠르게 검출해 영향 자산 리스트로 변환.

자산 심볼은 webapp.server.OVERVIEW_SYMBOLS 와 동일:
  ^KS11 코스피 / ^KQ11 코스닥 / ^GSPC S&P / ^IXIC 나스닥 / ^DJI 다우
  KRW=X 원달러 / ^VIX VIX / GC=F 금 / CL=F 유가 / BTC-USD 비트코인
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Set, Tuple

# 자산 카테고리 (시각화용)
ASSET_LABELS = {
    "^KS11":   "코스피",   "^KQ11":   "코스닥",
    "^GSPC":   "S&P 500", "^IXIC":   "나스닥",   "^DJI": "다우",
    "KRW=X":   "원/달러",  "^VIX":    "VIX",
    "GC=F":    "금",       "CL=F":    "WTI 유가",
    "BTC-USD": "비트코인",
}

# 키워드 → 영향 자산 (소문자 매칭). 키워드는 정확 부분문자열 매칭.
# 같은 키가 여러 자산에 매핑되면 모두에 점수 추가.
EVENT_MAP: Dict[str, List[str]] = {
    # === Macro / Fed ===
    "fed":             ["^IXIC", "^GSPC", "^DJI"],
    "fomc":            ["^IXIC", "^GSPC", "^DJI"],
    "federal reserve": ["^IXIC", "^GSPC", "^DJI"],
    "powell":          ["^IXIC", "^GSPC"],
    "rate hike":       ["^IXIC", "^GSPC"],
    "rate cut":        ["^IXIC", "^GSPC"],
    "interest rate":   ["^IXIC", "^GSPC"],
    "rate decision":   ["^IXIC", "^GSPC", "GC=F"],
    "cpi":             ["^IXIC", "GC=F", "^VIX"],
    "inflation":       ["^IXIC", "GC=F"],
    "ppi":             ["^IXIC"],
    "jobs report":     ["^IXIC", "^DJI"],
    "unemployment":    ["^IXIC", "^DJI"],
    "nonfarm":         ["^IXIC", "^GSPC"],
    "payrolls":        ["^IXIC", "^GSPC"],
    "gdp":             ["^IXIC", "^GSPC"],
    "retail sales":    ["^IXIC", "^GSPC"],

    # === Tech / Semis ===
    "nvidia":          ["^IXIC", "^GSPC"],
    "tsmc":            ["^IXIC", "^GSPC"],
    "ai chip":         ["^IXIC", "^GSPC"],
    "data center":     ["^IXIC", "^GSPC"],
    "apple":           ["^IXIC", "^GSPC"],
    "iphone":          ["^IXIC"],
    "microsoft":       ["^IXIC", "^GSPC"],
    "amazon":          ["^IXIC", "^GSPC"],
    "google":          ["^IXIC", "^GSPC"],
    "alphabet":        ["^IXIC", "^GSPC"],
    "meta":            ["^IXIC", "^GSPC"],
    "tesla":           ["^IXIC", "^GSPC"],
    "amd":             ["^IXIC", "^GSPC"],

    # === Energy ===
    "oil":             ["CL=F"],
    "crude":           ["CL=F"],
    "opec":            ["CL=F"],
    "saudi":           ["CL=F"],
    "barrel":          ["CL=F"],

    # === Korea ===
    "kospi":           ["^KS11"],
    "kosdaq":          ["^KQ11"],
    "samsung":         ["^KS11", "^KQ11"],
    "sk hynix":        ["^KS11"],
    "hyundai":         ["^KS11"],
    "bok":             ["^KS11", "KRW=X"],
    "bank of korea":   ["^KS11", "KRW=X"],
    "south korea":     ["^KS11", "KRW=X"],

    # === FX ===
    "dollar":          ["KRW=X"],
    "won":             ["KRW=X"],
    "yen":             ["KRW=X"],
    "yuan":            ["KRW=X"],

    # === Geopolitics / Risk-off ===
    "russia":          ["CL=F", "GC=F", "^VIX"],
    "ukraine":         ["CL=F", "GC=F", "^VIX"],
    "iran":            ["CL=F", "GC=F", "^VIX"],
    "israel":          ["CL=F", "GC=F", "^VIX"],
    "middle east":     ["CL=F", "GC=F", "^VIX"],
    "china":           ["^IXIC", "^KS11"],
    "taiwan":          ["^IXIC", "^KS11"],
    "tariff":          ["^IXIC", "^GSPC", "^KS11"],
    "sanction":        ["CL=F", "GC=F"],
    "war":             ["GC=F", "^VIX", "CL=F"],
    "crisis":          ["GC=F", "^VIX"],
    "recession":       ["^IXIC", "^VIX"],

    # === Crypto ===
    "bitcoin":         ["BTC-USD"],
    "btc":             ["BTC-USD"],
    "ethereum":        ["BTC-USD"],
    "crypto":          ["BTC-USD"],
    "spot etf":        ["BTC-USD"],
    "halving":         ["BTC-USD"],

    # === Gold / Safe-haven ===
    "gold":            ["GC=F"],
    "bullion":         ["GC=F"],
}

# 키워드 사전 정규화 (다회 검색을 위한 캐시)
_KW_SORTED: List[Tuple[str, List[str]]] = sorted(
    EVENT_MAP.items(), key=lambda x: -len(x[0]))


_BOUNDARY_CACHE: Dict[str, re.Pattern] = {}


def _kw_pattern(kw: str) -> re.Pattern:
    """word-boundary 매칭 패턴 캐시. 'ai chip' 같은 공백 키워드도 OK."""
    if kw not in _BOUNDARY_CACHE:
        _BOUNDARY_CACHE[kw] = re.compile(
            r"\b" + re.escape(kw) + r"\b", re.IGNORECASE)
    return _BOUNDARY_CACHE[kw]


def detect_assets(title: str, body: str = ""
                  ) -> Tuple[List[str], List[str]]:
    """
    텍스트에서 매칭되는 키워드/영향 자산 추출.
    word-boundary 매칭 — 'ppi'가 'Carolina'에 매칭되지 않도록.

    Returns
    -------
    (matched_keywords, affected_assets) — 둘 다 dedupe된 리스트
    """
    text = title + " " + body
    matched: List[str] = []
    assets: Set[str] = set()
    for kw, syms in _KW_SORTED:
        if _kw_pattern(kw).search(text):
            matched.append(kw)
            for s in syms:
                assets.add(s)
    return matched, sorted(assets)


def priority_score(matched_keywords: List[str], age_minutes: float,
                   has_high_impact: bool = False) -> float:
    """
    우선순위 점수 = 신선도 × 매칭 키워드 수 × 보너스.

    age_minutes:
      0~30분 = 3.0,  30~120분 = 2.0,  120~360분 = 1.0,  그 이상 = 0.5
    매칭 키워드 수: log scale (2개 이상부터 가산).
    """
    if age_minutes <= 30:
        fresh = 3.0
    elif age_minutes <= 120:
        fresh = 2.0
    elif age_minutes <= 360:
        fresh = 1.0
    else:
        fresh = 0.5
    kw_factor = 1.0 + 0.4 * max(0, len(matched_keywords) - 1)
    bonus = 1.5 if has_high_impact else 1.0
    return fresh * kw_factor * bonus


# 시급 키워드 (배지 색 결정용)
_HIGH_IMPACT_KW = {
    "war", "crisis", "crash", "halt", "emergency", "breaking",
    "default", "fraud", "shock", "surge", "plunge", "collapse",
}


def is_high_impact(title: str) -> bool:
    t = title.lower()
    return any(kw in t for kw in _HIGH_IMPACT_KW)
