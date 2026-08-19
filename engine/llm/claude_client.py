"""
Claude API 직접 호출 클라이언트
================================
anthropic SDK 미설치 환경에서도 동작 (requests로 직접 호출).

주식 전문 에이전트 페르소나 — 앱 컨텍스트 자동 주입.
프롬프트 캐시(`cache_control`) 활용해 시스템 프롬프트 재전송 비용 절감.

기본 모델: claude-sonnet-4-5 (10회/일 한도 안에서 품질·비용 균형).
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

import requests

from ..data.keyconfig import get_key

API_URL = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"
DEFAULT_MODEL = "claude-sonnet-4-5"
DEFAULT_MAX_TOKENS = 2048


class ClaudeError(Exception):
    pass


# ── 주식 에이전트 시스템 프롬프트 ─────────────────────────────────
_SYSTEM_BASE = """당신은 Plutus(기관급 퀀트 분석 터미널)에 \
탑재된 시니어 주식 분석 에이전트입니다.

역할:
- 한국어로 답변 (사용자가 영어로 물으면 영어 가능)
- 기관급 분석가 수준 — 일반론 금지, 구체적 KPI/숫자 인용
- 데이터 기반 — 모르는 건 솔직히 "데이터 부족"이라 표시
- 간결 + 실용 — 핵심 먼저, 4-5문단 이내
- 투자 권유 아닌 정보 제공 (필요 시 명시)

답변 원칙:
- 종목 질문 → 펀더(PE/PB/ROE/마진) + 기술적 + 최근 뉴스 통합 관점
- 시장 질문 → 매크로(금리/CPI) + 섹터 회전 + 위험 심리 통합
- "오늘/지금" 질문 → 사용자의 현재 컨텍스트(보고 있는 종목, 시장 상태)를 우선 사용
- 가능하면 분석가들이 실제로 주목하는 지표 언급 (NVDA → 데이터센터 매출/Gross Margin, AAPL → iPhone ASP/Services GM 등)
- 추측은 "추정", 사실은 "확정" 같은 메타 라벨 사용
"""


def _build_system_prompt(context: Optional[Dict[str, Any]] = None) -> str:
    """현재 사용자 컨텍스트(종목/시장)를 시스템 프롬프트에 동적 주입."""
    sys = _SYSTEM_BASE
    if not context:
        return sys
    lines = ["", "── 현재 사용자 컨텍스트 ──"]
    if context.get("ticker"):
        lines.append(f"보고 있는 종목: {context['ticker']}")
    if context.get("market_overview"):
        ov = context["market_overview"]
        lines.append("주요 지수/자산 현재가:")
        for item in ov[:10]:
            chg = item.get("pct", 0)
            sign = "+" if chg >= 0 else ""
            lines.append(
                f"  - {item.get('name')}: {item.get('price'):,.2f} "
                f"({sign}{chg:.2f}%)")
    if context.get("alerts"):
        lines.append("\n최근 속보 알림 (자산별 건수):")
        for asset, info in (context["alerts"] or {}).items():
            if info.get("count", 0) > 0:
                lines.append(
                    f"  - {asset}: {info['count']}건"
                    + (" (시급)" if info.get("has_high_impact") else ""))
    if context.get("recent_analysis"):
        ra = context["recent_analysis"]
        lines.append(f"\n사용자의 최근 분석 종목: {ra.get('ticker','')}")
        if ra.get("signal"):
            lines.append(f"  signal: {ra['signal']}, score: "
                         f"{ra.get('score','')}")
    return sys + "\n".join(lines)


def chat(messages: List[Dict[str, str]],
         context: Optional[Dict[str, Any]] = None,
         model: str = DEFAULT_MODEL,
         max_tokens: int = DEFAULT_MAX_TOKENS,
         timeout: int = 60) -> Dict[str, Any]:
    """
    Claude API 호출.

    Parameters
    ----------
    messages : [{role: "user"|"assistant", content: str}, ...]
        최근 대화 히스토리 (10턴 정도 추천)
    context : 현재 ticker/시장/속보 등 (선택)

    Returns
    -------
    {ok, text, usage: {input_tokens, output_tokens,
                       cache_creation_input_tokens, cache_read_input_tokens},
     model, error}
    """
    key = get_key("anthropic")
    if not key:
        return {"ok": False,
                "error": "Anthropic 키가 설정되지 않았습니다 (⚙ 설정)."}

    # 시스템 프롬프트 — base + 동적 컨텍스트
    # base는 캐시, 컨텍스트는 캐시 안 함 (자주 변경)
    sys_base = _SYSTEM_BASE
    sys_ctx = _build_system_prompt(context).replace(sys_base, "", 1)

    system_blocks: List[Dict[str, Any]] = [
        {"type": "text", "text": sys_base,
         "cache_control": {"type": "ephemeral"}},
    ]
    if sys_ctx.strip():
        system_blocks.append({"type": "text", "text": sys_ctx})

    payload = {
        "model": model,
        "max_tokens": max_tokens,
        "system": system_blocks,
        "messages": messages,
    }
    headers = {
        "x-api-key": key,
        "anthropic-version": API_VERSION,
        "content-type": "application/json",
    }
    try:
        t0 = time.time()
        r = requests.post(API_URL, json=payload, headers=headers,
                          timeout=timeout)
        elapsed = round(time.time() - t0, 1)
        if r.status_code != 200:
            return {"ok": False,
                    "error": f"Claude API {r.status_code}: "
                             f"{r.text[:300]}",
                    "elapsed_sec": elapsed}
        j = r.json() or {}
        # content는 [{type:"text", text:"..."}] 형태
        parts = j.get("content") or []
        text = ""
        for p in parts:
            if p.get("type") == "text":
                text += p.get("text", "")
        usage = j.get("usage") or {}
        return {
            "ok": True,
            "text": text.strip(),
            "model": j.get("model") or model,
            "stop_reason": j.get("stop_reason"),
            "usage": {
                "input_tokens": usage.get("input_tokens", 0),
                "output_tokens": usage.get("output_tokens", 0),
                "cache_creation_input_tokens":
                    usage.get("cache_creation_input_tokens", 0),
                "cache_read_input_tokens":
                    usage.get("cache_read_input_tokens", 0),
            },
            "elapsed_sec": elapsed,
        }
    except requests.RequestException as e:
        return {"ok": False, "error": f"네트워크 오류: {e}"}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def validate_api_key(key: Optional[str] = None) -> Dict[str, Any]:
    """짧은 호출로 키 유효성 검사 (모델 list 등)."""
    test_key = key or get_key("anthropic")
    if not test_key:
        return {"ok": False, "error": "키 없음"}
    try:
        r = requests.post(API_URL, json={
            "model": DEFAULT_MODEL, "max_tokens": 10,
            "messages": [{"role": "user", "content": "ok"}],
        }, headers={
            "x-api-key": test_key,
            "anthropic-version": API_VERSION,
            "content-type": "application/json",
        }, timeout=15)
        if r.status_code == 200:
            return {"ok": True, "model": DEFAULT_MODEL}
        return {"ok": False,
                "error": f"HTTP {r.status_code}: {r.text[:200]}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}
