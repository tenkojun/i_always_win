"""
================================================================
  충돌 해소기  (Conflict Resolver)
================================================================
메타 의사결정 레이어 — 축 A의 3단계 (핵심).

여러 증거가 서로 다른 방향을 가리킬 때 **구조적으로** 해소한다.
기존 _compute_score 의 +12/-8 누적 + 65/45 임계 방식을 대체.
v3 설계의 ceiling/capping 은 여기 규칙으로 흡수됨.

규칙 우선순위 (높을수록 먼저 적용)
----------------------------------
1. 거부권(veto): 활성 거부권은 해당 방향(veto_target) 상향
   신호를 무효화/강등.
2. 신뢰도 가중 합의: net = Σ signed_strength
3. 국면 게이트: regime=transition 이면 |net| ×0.6
4. 표본 패널티: confidence 엔진에서 이미 반영됨
5. 충돌 표면화: 강세/약세 증거가 팽팽하면 "혼재"로 명시

출력
----
resolved = {
  net_score      : -1.0 ~ +1.0
  raw_net        : 게이트 전 원시 net
  vetoed         : 발동된 거부권 목록
  blocked_dirs   : 거부된 방향 집합
  conflict_ratio : 0~1 (반대 진영 비중)
  regime_gate    : 적용된 감쇠 배수
  contributions  : [(source, signed, rationale)] 상위 기여
  stance         : "STRONG_BULL"|"BULL"|"NEUTRAL"|"BEAR"|"STRONG_BEAR"|"MIXED"
}
"""
from __future__ import annotations

from typing import Any, Dict, List

from .evidence_registry import Evidence


def resolve(evidences: List[Evidence],
            regime_state: str = "stable") -> Dict[str, Any]:
    """
    regime_state: "stable" | "transition" | "unknown"
    """
    if not evidences:
        return {
            "net_score": 0.0, "raw_net": 0.0, "vetoed": [],
            "blocked_dirs": [], "conflict_ratio": 0.0,
            "regime_gate": 1.0, "contributions": [],
            "stance": "NEUTRAL",
        }

    # ── 1) 거부권 수집 ──
    vetoed: List[Dict[str, Any]] = []
    blocked: set = set()
    for e in evidences:
        if e.veto and e.confidence >= 0.40:
            vetoed.append({"source": e.source,
                           "target": e.veto_target,
                           "rationale": e.rationale,
                           "confidence": round(e.confidence, 3)})
            blocked.add(e.veto_target)

    # ── 2) 신뢰도 가중 합의 (거부권 증거 자체는 합산서 제외) ──
    contribs: List[tuple] = []
    pos_w = 0.0
    neg_w = 0.0
    net = 0.0
    for e in evidences:
        if e.veto:
            continue
        s = e.signed_strength
        if e.direction in blocked and e.direction != 0:
            s *= 0.15
        net += s
        if s > 0:
            pos_w += s
        elif s < 0:
            neg_w += -s
        if abs(s) > 1e-6:
            contribs.append((e.source, round(s, 4),
                             e.rationale, e.category))

    raw_net = net

    # ── 3) 국면 게이트 ──
    gate = 1.0
    if regime_state == "transition":
        gate = 0.60
    elif regime_state == "unknown":
        gate = 0.85
    net *= gate

    denom = max(1.0, (pos_w + neg_w))
    net_score = float(max(-1.0, min(1.0, net / denom)))

    # ── 5) 충돌 표면화 ──
    total = pos_w + neg_w
    minority = min(pos_w, neg_w)
    conflict_ratio = float(minority / total) if total > 0 else 0.0

    # ── 스탠스 결정 ──
    if conflict_ratio >= 0.42:
        stance = "MIXED"
    elif net_score >= 0.45:
        stance = "STRONG_BULL"
    elif net_score >= 0.15:
        stance = "BULL"
    elif net_score <= -0.45:
        stance = "STRONG_BEAR"
    elif net_score <= -0.15:
        stance = "BEAR"
    else:
        stance = "NEUTRAL"

    if 1 in blocked and stance in ("STRONG_BULL", "BULL"):
        stance = "NEUTRAL" if stance == "BULL" else "BULL"

    contribs.sort(key=lambda x: -abs(x[1]))
    return {
        "net_score": round(net_score, 4),
        "raw_net": round(raw_net, 4),
        "vetoed": vetoed,
        "blocked_dirs": sorted(blocked),
        "conflict_ratio": round(conflict_ratio, 3),
        "regime_gate": gate,
        "contributions": contribs[:8],
        "pos_weight": round(pos_w, 3),
        "neg_weight": round(neg_w, 3),
        "stance": stance,
    }
