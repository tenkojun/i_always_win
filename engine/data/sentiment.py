"""
뉴스 감성 분석 (키워드 기반 + Finnhub 보강)
============================================
키 없어도 항상 동작하는 키워드 스코어링 기본,
Finnhub 키 있으면 시장 감성 데이터로 보강.

반환 dict
---------
score       : -1.0 (강부정) ~ +1.0 (강긍정)
label       : 강한 긍정 / 긍정 / 중립 / 부정 / 강한 부정
label_en    : strong_positive / positive / neutral / negative / strong_negative
pos_words   : 감지된 긍정 키워드 목록
neg_words   : 감지된 부정 키워드 목록
summary_kr  : 한글 한 줄 요약
confidence  : 신뢰도 (0~1, 키워드 수 기반)
source      : keyword / finnhub
"""
from __future__ import annotations
from typing import Dict, List
import re

# ── 긍정/부정 키워드 사전 ──────────────────────────────────────────
_POS = {
    # 실적/성장
    "beat","beats","exceed","exceeds","surpass","record","profit","profits",
    "growth","grow","grew","surge","surges","soar","soars","rally","rallies",
    "jump","jumps","rise","rises","rose","gain","gains","strong","strength",
    "upgrade","upgraded","outperform","buy","bullish","bull",
    # 기업 이벤트
    "dividend","buyback","partnership","deal","win","wins","approval","approved",
    "launch","launches","innovation","breakthrough","acquisition",
    # 전망
    "raise","raises","forecast","upbeat","optimistic","confident","positive",
    "opportunity","recover","recovery","rebound",
}
_NEG = {
    # 실적/손실
    "miss","misses","below","disappoint","disappoints","loss","losses",
    "decline","declines","declined","fall","falls","fell","drop","drops",
    "plunge","plunges","crash","crashes","sink","sinks","slump","slumps",
    "downgrade","downgraded","underperform","sell","bearish","bear","weak",
    "weakness","concern","concerns","risk","risks","warn","warns","warning",
    # 기업 이벤트
    "layoff","layoffs","lawsuit","fraud","investigation","recall","ban",
    "sanction","fine","penalty","debt","bankruptcy","default",
    # 전망
    "cut","cuts","lower","lowered","reduce","reduced","uncertain","uncertainty",
    "slowdown","recession","inflation","crisis","trouble","problem",
}
_STRONG_BOOST = {"record","surge","soar","plunge","crash","bankruptcy","fraud","breakthrough"}


def _tokenize(text: str) -> List[str]:
    return re.findall(r"[a-zA-Z]+", text.lower())


def analyze_sentiment(title: str, body: str = "") -> Dict:
    tokens = _tokenize(title + " " + body)
    pos_found = [w for w in tokens if w in _POS]
    neg_found = [w for w in tokens if w in _NEG]

    pos_score = sum(2 if w in _STRONG_BOOST else 1 for w in pos_found)
    neg_score = sum(2 if w in _STRONG_BOOST else 1 for w in neg_found)
    total = pos_score + neg_score

    if total == 0:
        raw = 0.0
    else:
        raw = (pos_score - neg_score) / max(total * 1.5, 1)
    score = max(-1.0, min(1.0, raw))

    if score >= 0.4:
        label, label_en = "강한 긍정", "strong_positive"
    elif score >= 0.1:
        label, label_en = "긍정", "positive"
    elif score <= -0.4:
        label, label_en = "강한 부정", "strong_negative"
    elif score <= -0.1:
        label, label_en = "부정", "negative"
    else:
        label, label_en = "중립", "neutral"

    confidence = min(1.0, total / 6)
    summary_kr = _build_summary(score, label, list(set(pos_found)), list(set(neg_found)), title)

    return {
        "score":      round(score, 3),
        "label":      label,
        "label_en":   label_en,
        "pos_words":  list(set(pos_found))[:6],
        "neg_words":  list(set(neg_found))[:6],
        "summary_kr": summary_kr,
        "confidence": round(confidence, 2),
        "source":     "keyword",
    }


def _build_summary(score: float, label: str, pos: List[str],
                   neg: List[str], title: str) -> str:
    if score >= 0.4:
        triggers = "·".join(pos[:3]) if pos else ""
        return (f"매우 긍정적인 뉴스입니다. "
                + (f"'{triggers}' 등 강한 호재 신호 감지." if triggers else "")
                + " 단기 주가에 상승 압력 가능.")
    elif score >= 0.1:
        triggers = "·".join(pos[:2]) if pos else ""
        return (f"긍정적 내용의 뉴스입니다. "
                + (f"'{triggers}' 키워드 포함." if triggers else "")
                + " 투자 심리에 우호적.")
    elif score <= -0.4:
        triggers = "·".join(neg[:3]) if neg else ""
        return (f"매우 부정적인 뉴스입니다. "
                + (f"'{triggers}' 등 악재 신호 감지." if triggers else "")
                + " 단기 주가 하락 압력 가능. 포지션 점검 권장.")
    elif score <= -0.1:
        triggers = "·".join(neg[:2]) if neg else ""
        return (f"부정적 내용의 뉴스입니다. "
                + (f"'{triggers}' 키워드 포함." if triggers else "")
                + " 투자 심리에 불리할 수 있음.")
    else:
        return ("중립적인 뉴스입니다. "
                "뚜렷한 호재·악재 신호 없음. 추가 맥락 확인 권장.")
