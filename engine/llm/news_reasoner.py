"""
뉴스 → 시장 이벤트 → 자산 영향 → Evidence
============================================
DeepSeek-R1 기반 한 번의 LLM 호출로 다음을 모두 추출:

  1) event_type        — RATE_HIKE / EARNINGS_BEAT / M&A / 등
  2) entities          — 관련 종목/섹터/매크로 지표
  3) affected_assets   — [{ticker, direction, magnitude, horizon}, ...]
  4) reliability       — 0~1 (소스 신뢰 + 내용 명확성)
  5) rationale_kr      — 한글 근거 요약

출력은 signal_engine의 Evidence 객체와 호환 가능한 dict로 변환된다.

비용 효율: 한 뉴스당 1회 호출(평균 5-15초 on GTX 1660 Ti, GPU 모드).
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from .client import generate_json, LLMError

# 이벤트 분류 — 시장 의사결정에 직접 영향
EVENT_TYPES = [
    "EARNINGS_BEAT", "EARNINGS_MISS", "GUIDANCE_RAISE", "GUIDANCE_CUT",
    "MA_ANNOUNCED", "MA_COMPLETED", "BUYBACK", "DIVIDEND_CHANGE",
    "RATE_HIKE", "RATE_CUT", "INFLATION_DATA", "GDP_DATA",
    "FED_STATEMENT", "REGULATORY_ACTION", "LAWSUIT", "FRAUD",
    "ANALYST_UPGRADE", "ANALYST_DOWNGRADE", "CEO_CHANGE",
    "PRODUCT_LAUNCH", "PARTNERSHIP", "GEOPOLITICAL", "OTHER",
]

DIRECTIONS = ["bullish", "bearish", "neutral"]
HORIZONS = ["intraday", "short", "medium", "long"]  # 단기 ≤ 1주, 중기 ≤ 1개월, 장기

_SYSTEM = (
    "You are a senior institutional financial analyst. "
    "Given a news article, you classify the market event, identify "
    "the directly affected assets (with ticker symbols when possible), "
    "and assess reliability. "
    "You ALWAYS respond with a single JSON object. No prose, no markdown."
)

_SYSTEM_DEEP = (
    "당신은 기관급 시니어 애널리스트입니다.\n\n"
    "⚠ 절대 규칙:\n"
    "1. 제공된 뉴스 제목 + 본문에 등장한 종목만 분석. "
    "본문에 없는 다른 종목(예: 삼성 뉴스인데 애플)을 절대 언급 금지.\n"
    "2. 본문에 등장한 종목이 한국 종목이면 한국 종목 그대로 분석 — "
    "미국 종목으로 임의 변환 금지.\n"
    "3. 한국어로만 답변. 영어 단어 직접 사용 금지(ticker는 예외).\n\n"
    "분석 두 단계:\n"
    "  1) 분석가들이 이 종목/이벤트에서 주목하는 핵심 KPI\n"
    "  2) 주어진 정보로 그 KPI들에 대해 어떻게 판단되는가\n\n"
    "출력은 단일 JSON 객체. 마크다운/설명문 금지."
)


def _prompt(title: str, body: str = "", source: str = "") -> str:
    body_block = f"\nBody: {body[:1500]}" if body else ""
    src_block = f"\nSource: {source}" if source else ""
    return f"""Analyze this financial news for institutional decision-making.

Title: {title}{body_block}{src_block}

Return a JSON object with this EXACT schema:
{{
  "event_type": "one of: {', '.join(EVENT_TYPES)}",
  "entities": ["primary tickers or macro indicators, e.g. AAPL, FED, CPI"],
  "affected_assets": [
    {{
      "ticker": "ticker symbol or asset class like QQQ/TLT/DXY/GLD/BTC",
      "direction": "bullish|bearish|neutral",
      "magnitude": 0.0-1.0,
      "horizon": "intraday|short|medium|long",
      "reason_en": "one-sentence reason"
    }}
  ],
  "reliability": 0.0-1.0,
  "novelty": 0.0-1.0,
  "rationale_kr": "한글 2-3 문장으로 사건 의미와 시장 영향 설명"
}}

Rules:
- magnitude reflects expected price impact strength (0.7+ = strong move).
- reliability considers source credibility and content specificity.
- novelty is high if event is unprecedented or surprising.
- For macro events (RATE_HIKE/INFLATION_DATA/FED_STATEMENT), include
  affected assets like QQQ (growth), TLT (bonds), DXY (dollar), GLD (gold).
- For earnings/single-stock events, only include the specific ticker.
- If unclear, set direction to "neutral" and magnitude to 0.3.

Return ONLY the JSON object, nothing else."""


def analyze_news(title: str, body: str = "", source: str = "",
                 model: str = "deepseek-r1:7b",
                 timeout: int = 180) -> Dict[str, Any]:
    """
    뉴스 한 건을 LLM으로 분석.

    Returns
    -------
    {
        "ok": bool,
        "event_type": str,
        "entities": [...],
        "affected_assets": [{ticker, direction, magnitude, horizon, reason_en}],
        "reliability": float,
        "novelty": float,
        "rationale_kr": str,
        "elapsed_sec": float,
        "model": str,
        "error": str | None,
    }
    """
    if not title:
        return {"ok": False, "error": "title 누락"}
    t0 = time.time()
    try:
        obj = generate_json(_prompt(title, body, source),
                            model=model, system=_SYSTEM,
                            temperature=0.0, max_tokens=1500,
                            timeout=timeout)
        elapsed = round(time.time() - t0, 1)
        if not isinstance(obj, dict):
            return {"ok": False, "error": "응답이 dict가 아님",
                    "elapsed_sec": elapsed}
        # 기본 필드 보정
        ev = (obj.get("event_type") or "OTHER").upper()
        if ev not in EVENT_TYPES:
            ev = "OTHER"
        return {
            "ok": True,
            "event_type":      ev,
            "entities":        list(obj.get("entities") or []),
            "affected_assets": _validate_assets(obj.get("affected_assets")),
            "reliability":     _clamp01(obj.get("reliability"), 0.5),
            "novelty":         _clamp01(obj.get("novelty"), 0.3),
            "rationale_kr":    str(obj.get("rationale_kr") or "").strip(),
            "elapsed_sec":     elapsed,
            "model":           model,
            "error":           None,
        }
    except LLMError as e:
        return {"ok": False, "error": str(e),
                "elapsed_sec": round(time.time() - t0, 1)}
    except Exception as e:
        return {"ok": False,
                "error": f"{type(e).__name__}: {e}",
                "elapsed_sec": round(time.time() - t0, 1)}


def _prompt_deep(title: str, body: str, source: str,
                 context_md: str, primary_ticker: str,
                 kpis: List[str]) -> str:
    """심층 분석 프롬프트 — 컨텍스트 + 분석가 KPI 강조."""
    body_block = f"\n\n## 본문\n{body[:1800]}" if body else ""
    src_block = f"\n출처: {source}" if source else ""
    kpi_inline = ", ".join(kpis[:5]) if kpis else ""
    return f"""## 뉴스 헤드라인
{title}{src_block}{body_block}

## 분석 컨텍스트 (펀더멘털 + 최근 뉴스 + KPI)
{context_md}

---

위 정보를 바탕으로 {('해당 종목 ' + primary_ticker if primary_ticker else '이 사건')}에 \
대해 기관 분석가 수준의 평가를 작성하세요.

특히 다음 KPI 관점을 반영하세요: {kpi_inline if kpi_inline else '핵심 재무지표'}

다음 JSON 스키마로만 응답 (한국어로):

{{
  "event_type": "{', '.join(EVENT_TYPES)} 중 하나",
  "entities": ["관련 종목 ticker"],
  "key_points": [
    "분석가들이 이번 사건에서 주목하는 구체적 포인트 (KPI 기준, 한국어 1문장씩, 3-5개)"
  ],
  "affected_assets": [
    {{
      "ticker": "ticker",
      "direction": "bullish|bearish|neutral",
      "magnitude": 0.0-1.0,
      "horizon": "intraday|short|medium|long",
      "reason_kr": "왜 그 방향인지 한국어 1-2문장"
    }}
  ],
  "consensus_view": "현재 시장 컨센서스가 어떤지 (한국어, 알 수 없으면 '데이터 부족')",
  "risks": ["주의해야 할 리스크 요인 (한국어, 2-3개)"],
  "reliability": 0.0-1.0,
  "novelty": 0.0-1.0,
  "rationale_kr": "전체 결론을 한국어 3-5문장으로 — 구체적 KPI 언급, 일반론 금지"
}}

규칙:
- key_points는 일반론('실적이 좋으면 주가가 오른다')이 아니라 \
구체적 KPI 언급('데이터센터 매출 YoY 가속 여부', \
'Gross Margin 75% 방어 가능성')으로 작성.
- rationale_kr에 영어 단어 직접 쓰지 말 것. \
(NVDA같은 ticker는 OK, 'earnings'는 '실적'으로)
- 데이터가 부족하면 솔직히 '데이터 부족'이라고 명시.

JSON만 출력. 다른 텍스트 금지."""


def analyze_news_deep(title: str, body: str = "", source: str = "",
                      ticker_hint: str = "",
                      model: str = "deepseek-r1:7b",
                      timeout: int = 300) -> Dict[str, Any]:
    """심층 분석 — 컨텍스트 빌더 + 분석가 KPI + 한국어 강제."""
    import time
    if not title:
        return {"ok": False, "error": "title 누락"}
    t0 = time.time()
    try:
        # 1) 컨텍스트 빌드 (펀더 + 뉴스 + 웹)
        from .context_builder import build_context
        ctx = build_context(title, body, ticker_hint=ticker_hint)
        # 2) 향상된 프롬프트로 LLM 호출
        obj = generate_json(
            _prompt_deep(title, body, source,
                         ctx["full_context_md"],
                         ctx["primary_ticker"], ctx["kpis"]),
            model=model, system=_SYSTEM_DEEP,
            temperature=0.1, max_tokens=1500,  # 2500→1500 속도↑
            timeout=timeout,
        )
        elapsed = round(time.time() - t0, 1)
        if not isinstance(obj, dict):
            return {"ok": False, "error": "응답이 dict가 아님",
                    "elapsed_sec": elapsed}
        ev = (obj.get("event_type") or "OTHER").upper()
        if ev not in EVENT_TYPES:
            ev = "OTHER"
        # affected_assets — reason_kr 우선, 없으면 reason_en
        raw_assets = obj.get("affected_assets") or []
        for a in raw_assets:
            if isinstance(a, dict) and not a.get("reason_en"):
                a["reason_en"] = a.get("reason_kr", "")

        # DeepSeek-R1은 한국어 강제해도 영어 섞임 — 후처리 번역
        key_points = [str(p).strip() for p in
                      (obj.get("key_points") or []) if p][:6]
        consensus = str(obj.get("consensus_view") or "").strip()
        risks = [str(r).strip() for r in
                 (obj.get("risks") or []) if r][:4]
        rationale = str(obj.get("rationale_kr") or "").strip()

        # DeepL 키 있으면 영어 비율 높은 텍스트를 한국어로 번역
        from .text_utils import polish_korean
        key_points = [polish_korean(p) for p in key_points]
        risks = [polish_korean(r) for r in risks]
        consensus = polish_korean(consensus)
        rationale = polish_korean(rationale)

        return {
            "ok":              True,
            "event_type":      ev,
            "entities":        list(obj.get("entities") or []),
            "key_points":      key_points,
            "affected_assets": _validate_assets(raw_assets),
            "consensus_view":  consensus,
            "risks":           risks,
            "reliability":     _clamp01(obj.get("reliability"), 0.5),
            "novelty":         _clamp01(obj.get("novelty"), 0.3),
            "rationale_kr":    rationale,
            "elapsed_sec":     elapsed,
            "model":           model,
            "context":         {
                "primary_ticker": ctx["primary_ticker"],
                "tickers": ctx["tickers"],
                "sector": ctx["sector"],
                "kpis": ctx["kpis"],
                "has_fundamentals": bool(ctx["fundamentals_md"]),
                "has_news": bool(ctx["news_md"]),
                "has_websearch": bool(ctx["websearch_md"]),
            },
            "error":           None,
        }
    except LLMError as e:
        return {"ok": False, "error": str(e),
                "elapsed_sec": round(time.time() - t0, 1)}
    except Exception as e:
        return {"ok": False,
                "error": f"{type(e).__name__}: {e}",
                "elapsed_sec": round(time.time() - t0, 1)}


def _clamp01(v: Any, default: float = 0.0) -> float:
    try:
        x = float(v)
        return max(0.0, min(1.0, x))
    except (TypeError, ValueError):
        return default


def _validate_assets(assets: Any) -> List[Dict[str, Any]]:
    """affected_assets 항목 유효성 보정."""
    out = []
    if not isinstance(assets, list):
        return out
    for a in assets:
        if not isinstance(a, dict):
            continue
        ticker = str(a.get("ticker") or "").strip().upper()
        if not ticker:
            continue
        direction = str(a.get("direction") or "neutral").lower()
        if direction not in DIRECTIONS:
            direction = "neutral"
        horizon = str(a.get("horizon") or "short").lower()
        if horizon not in HORIZONS:
            horizon = "short"
        out.append({
            "ticker":   ticker,
            "direction": direction,
            "magnitude": _clamp01(a.get("magnitude"), 0.3),
            "horizon":   horizon,
            "reason_en": str(a.get("reason_en") or "").strip()[:200],
        })
    return out


# ── Evidence 객체 변환 (signal_engine 호환) ──────────────────────
def to_evidence_list(analysis: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    LLM 분석 결과 → Evidence dict 리스트.

    affected_assets 각 항목을 별개 Evidence로 변환.
    signal_engine.evidence_registry.Evidence 와 직렬화 호환.

    Returns
    -------
    [
      {source, direction, magnitude, confidence, horizon, veto,
       rationale, category, raw},
      ...
    ]
    """
    if not analysis.get("ok"):
        return []
    out = []
    rel = analysis.get("reliability", 0.5)
    ev_type = analysis.get("event_type", "OTHER")
    rationale_base = analysis.get("rationale_kr", "")
    for asset in analysis.get("affected_assets", []):
        dir_str = asset["direction"]
        direction = (+1 if dir_str == "bullish"
                     else -1 if dir_str == "bearish" else 0)
        horizon = {"intraday": "단기", "short": "단기",
                   "medium": "중기", "long": "장기"}.get(
                       asset["horizon"], "단기")
        out.append({
            "source":     f"llm_news:{ev_type}",
            "direction":  direction,
            "magnitude":  asset["magnitude"],
            "confidence": rel,
            "horizon":    horizon,
            "veto":       False,
            "veto_target": 0,
            "rationale":  (f"[{asset['ticker']}] "
                           f"{asset.get('reason_en', '')} "
                           f"— {rationale_base}").strip(),
            "category":   ev_type,
            "raw": {
                "ticker":    asset["ticker"],
                "novelty":   analysis.get("novelty", 0.3),
                "model":     analysis.get("model"),
                "elapsed":   analysis.get("elapsed_sec"),
            },
        })
    return out
