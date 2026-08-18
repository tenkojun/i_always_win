"""
================================================================
  JIQT 설명가능성 레이어  (explain)
================================================================
v4 축 B. 기관 정형 리포팅을 동급화한 위에, 기관 제품에 없는
veto 추적·인과 사슬을 얹는다.

  factor_attribution : 요인/범주 기여 분해 (기관 리포트 동급)
  verdict_trace      : 판정 인과 사슬 (JIQT 차별점)
  risk_chain         : "왜 위험한가" 단계 설명

공개 진입점
-----------
  build_explanation(decide_result, precision, stress, factor)
    → 위 3종을 묶은 통합 설명 번들
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from .factor_attribution import attribute
from .verdict_trace import build_trace, render_trace_text
from .risk_chain import build_risk_chain

__all__ = [
    "attribute", "build_trace", "render_trace_text",
    "build_risk_chain", "build_explanation",
]


def build_explanation(decide_result: Dict[str, Any],
                      precision: Optional[Dict[str, Any]] = None,
                      stress: Optional[Dict[str, Any]] = None,
                      factor: Optional[Dict[str, Any]] = None
                      ) -> Dict[str, Any]:
    """
    메타 의사결정 + 정밀도/스트레스/팩터 → 통합 설명.

    Returns
    -------
    {
      "attribution": {요인 기여 분해},
      "trace": {판정 인과 사슬},
      "trace_text": 사람이 읽는 추적 블록,
      "risk_chain": {리스크 인과 체인},
      "headline": 한 줄,
    }
    """
    attrib = attribute(decide_result)
    trace = build_trace(decide_result)
    rc = build_risk_chain(precision or {}, stress or {}, factor or {})

    return {
        "attribution": attrib,
        "trace": trace,
        "trace_text": render_trace_text(trace),
        "risk_chain": rc,
        "headline": attrib.get("headline", ""),
    }
