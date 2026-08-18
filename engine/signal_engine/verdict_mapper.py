"""
================================================================
  판정 사상기  (Verdict Mapper)
================================================================
메타 의사결정 레이어 — 축 A의 4단계 (최종).

conflict_resolver 의 해소 결과를 **최종 판정**으로 사상한다.
기존 65/45 임계 대신, 합의 강도·충돌·거부권을 모두 반영한
정직한 시그널을 낸다.

시그널 체계
-----------
- STRONG BUY  : 강한 합의 강세, 거부권 없음, 충돌 낮음
- BUY         : 합의 강세, 거부권 없음
- TACTICAL BUY: 강세지만 거부권/충돌 존재 (전술적 제한)
- HOLD        : 중립 또는 거부권으로 강세 차단됨
- REDUCE      : 합의 약세
- SELL        : 강한 합의 약세
- MIXED/관망  : 증거가 팽팽히 충돌 (거짓 확신 회피)
"""
from __future__ import annotations

from typing import Any, Dict


def _grade(score: float) -> str:
    if score >= 85:
        return "A+"
    if score >= 75:
        return "A"
    if score >= 65:
        return "B+"
    if score >= 55:
        return "B"
    if score >= 45:
        return "C+"
    if score >= 35:
        return "C"
    return "D"


def map_verdict(resolved: Dict[str, Any]) -> Dict[str, Any]:
    """resolved(conflict_resolver 출력) → 최종 판정 dict."""
    net = resolved.get("net_score", 0.0)
    stance = resolved.get("stance", "NEUTRAL")
    vetoed = resolved.get("vetoed", [])
    blocked = set(resolved.get("blocked_dirs", []))
    conflict = resolved.get("conflict_ratio", 0.0)

    score = 50.0 + net * 50.0

    # ── 거부권/충돌에 의한 상단 제한 (v3 ceiling 대체) ──
    ceiling = 100.0
    notes = []
    if 1 in blocked:
        ceiling = min(ceiling, 58.0)
        notes.append("강세 거부권 발동 → 점수 상한 58")
    if conflict >= 0.42:
        ceiling = min(ceiling, 62.0)
        notes.append("증거 충돌 심함 → 점수 상한 62")
    if len(vetoed) >= 2:
        ceiling = min(ceiling, 52.0)
        notes.append("복수 거부권 → 점수 상한 52")
    score = float(max(0.0, min(score, ceiling)))
    score = round(score, 1)

    # ── 시그널 사상 ──
    if stance == "MIXED":
        signal, color = "MIXED · 관망", "⚪"
    elif 1 in blocked and net > 0:
        signal, color = "TACTICAL BUY", "🟡"
    elif stance == "STRONG_BULL":
        signal, color = "STRONG BUY", "🟢"
    elif stance == "BULL":
        signal, color = "BUY", "🟢"
    elif stance == "STRONG_BEAR":
        signal, color = "SELL", "🔴"
    elif stance == "BEAR":
        signal, color = "REDUCE", "🟠"
    else:
        signal, color = "HOLD", "🟡"

    if 0.30 <= conflict < 0.42 and "MIXED" not in signal:
        signal += " (증거 혼재)"

    grade = _grade(score)

    if signal.startswith("STRONG BUY"):
        verdict = ("강한 합의 강세. 거부권·충돌 없이 다수 증거가 상방을 지지합니다.")
    elif signal.startswith("TACTICAL BUY"):
        verdict = ("방향은 강세이나 거부권(과적합·통계·시나리오)이 발동되어 "
                   "전술적 제한이 필요합니다. 신규 비중은 분할·소액 권고.")
    elif signal.startswith("BUY"):
        verdict = "합의 강세. 다만 일부 증거 혼재 가능성 점검 권고."
    elif signal.startswith("MIXED"):
        verdict = ("증거가 팽팽히 충돌합니다. 거짓 확신보다 관망이 합리적이며, "
                   "충돌 원인 해소 전 신규 진입 자제.")
    elif signal.startswith("HOLD"):
        verdict = "중립 또는 거부권으로 강세가 차단되었습니다. 관망 권고."
    elif signal.startswith("REDUCE"):
        verdict = "합의 약세. 비중 축소 검토."
    else:
        verdict = "강한 합의 약세. 위험 관리 우선."

    return {
        "signal": signal,
        "color": color,
        "score": score,
        "grade": grade,
        "stance": stance,
        "verdict": verdict,
        "ceiling_applied": ceiling < 100.0,
        "ceiling_notes": notes,
        "n_vetoes": len(vetoed),
        "conflict_ratio": conflict,
    }
