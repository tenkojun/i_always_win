"""
================================================================
  신뢰도 엔진  (Confidence Engine)
================================================================
JIQT v4 메타 의사결정 레이어 — 축 A의 2단계.

각 Evidence 의 초기 confidence 를 **객관적 신뢰도 요인**으로
재보정한다. 모델이 "강하게 주장"해도 표본이 짧거나 데이터
출처가 약하면 신뢰도를 깎는다.

보정 요인
---------
1. 데이터 출처 신뢰도 (df.attrs source_confidence)
   high → ×1.0,  medium → ×0.9,  low → ×0.7
2. 표본 충분성 (precision.min_trl.enough)
   미달이면 통계·리스크 범주 증거 ×0.75
3. ML 범주는 OOS 정확도가 0.5 근처면 추가 감쇠
4. 카테고리 신뢰도 사전(prior): 통계>리스크>추세>모멘텀>
   오더플로우 (오더플로우는 노이즈 많음)
"""
from __future__ import annotations

from typing import Any, Dict, List

from .evidence_registry import Evidence

_CATEGORY_PRIOR = {
    "통계": 1.00,
    "시나리오": 0.95,
    "리스크": 0.92,
    "팩터": 0.88,
    "추세": 0.85,
    "ML": 0.82,
    "모멘텀": 0.78,
    "오더플로우": 0.65,
    "기타": 0.70,
}

_SRC_CONF = {"high": 1.0, "medium": 0.9, "low": 0.7}


def recalibrate(evidences: List[Evidence],
                context: Dict[str, Any]) -> List[Evidence]:
    """
    context 키:
      - data_confidence : "high"|"medium"|"low" (df.attrs)
      - sample_enough   : bool (precision.min_trl.enough)
      - ml_accuracy     : float|None (중기 ML OOS 정확도)
    Evidence 의 confidence 를 in-place 보정 후 같은 리스트 반환.
    """
    dc = _SRC_CONF.get(str(context.get("data_confidence", "medium")), 0.9)
    sample_ok = bool(context.get("sample_enough", True))
    ml_acc = context.get("ml_accuracy")

    for e in evidences:
        c = e.confidence

        # 1) 카테고리 사전
        c *= _CATEGORY_PRIOR.get(e.category, 0.7)

        # 2) 데이터 출처 신뢰도 (거부권 증거는 약하게만 영향)
        c *= dc if not e.veto else (0.5 + 0.5 * dc)

        # 3) 표본 미달 → 통계·리스크·ML 범주 감쇠
        if not sample_ok and e.category in ("통계", "리스크", "ML"):
            c *= 0.75

        # 4) ML 정확도가 동전던지기 수준이면 ML 증거 추가 감쇠
        if e.category == "ML" and ml_acc is not None:
            edge = abs(float(ml_acc) - 0.5)
            if edge < 0.05:
                c *= 0.5
            elif edge < 0.10:
                c *= 0.75

        e.confidence = float(max(0.0, min(1.0, c)))

    return evidences


def confidence_summary(evidences: List[Evidence]) -> Dict[str, Any]:
    """진단용: 카테고리별 평균 신뢰도·증거 수."""
    by: Dict[str, List[float]] = {}
    for e in evidences:
        by.setdefault(e.category, []).append(e.confidence)
    return {
        cat: {"n": len(v),
              "avg_conf": round(sum(v) / len(v), 3)}
        for cat, v in by.items()
    }
