"""
================================================================
  I ALWAYS WIN 메타 의사결정 레이어  (signal_engine)
================================================================
v4 축 A. 모델 출력을 표준 Evidence 로 모아 →신뢰도 보정
→충돌 해소 →최종 판정. 기존 _compute_score 의 모멘텀 편향을
구조적으로 대체한다.

파이프라인
----------
  evidence_registry  : 모델 출력 → Evidence
  confidence_engine  : Evidence 신뢰도 재보정
  conflict_resolver  : 거부권·합의·국면게이트·충돌표면화
  verdict_mapper     : 최종 시그널/점수/등급/판정문

공개 진입점
-----------
  decide(...)  : 위 4단계를 한 번에 실행
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from .evidence_registry import (
    Evidence, EvidenceRegistry,
    adapt_timeframe, adapt_precision, adapt_stress, adapt_factor,
)
from .confidence_engine import recalibrate, confidence_summary
from .conflict_resolver import resolve
from .verdict_mapper import map_verdict

__all__ = [
    "Evidence", "EvidenceRegistry", "decide",
    "adapt_timeframe", "adapt_precision", "adapt_stress",
    "adapt_factor", "recalibrate", "resolve", "map_verdict",
]


def decide(timeframes: Dict[str, Any],
           precision: Optional[Dict[str, Any]] = None,
           stress: Optional[Dict[str, Any]] = None,
           factor: Optional[Dict[str, Any]] = None,
           data_confidence: str = "medium",
           regime_state: str = "stable") -> Dict[str, Any]:
    """
    종목 전체의 메타 의사결정.

    Parameters
    ----------
    timeframes : {"단기":{...}, "중기":{...}, "장기":{...}}
                 (analyze_one_timeframe 결과들)
    precision  : precision_diagnostics 결과 (PSR/DSR/CDaR)
    stress     : stress_test 결과
    factor     : factor 분해 결과
    data_confidence : df.attrs 의 출처 신뢰도
    regime_state    : "stable"|"transition"|"unknown"

    Returns
    -------
    {
      verdict: {signal, score, grade, verdict, ...},
      resolved: {net_score, vetoed, conflict_ratio, ...},
      evidence: [Evidence dict ...],
      confidence_summary: {...},
      n_evidence: int,
    }
    """
    reg = EvidenceRegistry(scope="overall")

    # 1) 타임프레임 → Evidence
    hmap = {"단기": "short", "중기": "mid", "장기": "long"}
    for kr, hz in hmap.items():
        tf = timeframes.get(kr)
        if tf:
            reg.extend(adapt_timeframe(tf, hz))

    # 2) 기관급 정밀도/스트레스/팩터 → Evidence (거부권 포함)
    if precision:
        reg.extend(adapt_precision(precision))
    if stress:
        reg.extend(adapt_stress(stress))
    if factor:
        reg.extend(adapt_factor(factor))

    evs = reg.items
    if not evs:
        return {
            "verdict": map_verdict(resolve([])),
            "resolved": resolve([]),
            "evidence": [], "confidence_summary": {},
            "n_evidence": 0,
        }

    # 3) 신뢰도 재보정
    sample_enough = True
    ml_acc = None
    if precision:
        sample_enough = (precision.get("min_trl") or {}).get("enough", True)
    mid = timeframes.get("중기", {}) or {}
    ml_acc = (mid.get("ml") or {}).get("accuracy")
    ctx = {"data_confidence": data_confidence,
           "sample_enough": sample_enough,
           "ml_accuracy": ml_acc}
    recalibrate(evs, ctx)

    # 4) 충돌 해소 → 판정
    resolved = resolve(evs, regime_state=regime_state)
    verdict = map_verdict(resolved)

    return {
        "verdict": verdict,
        "resolved": resolved,
        "evidence": [e.to_dict() for e in evs],
        "confidence_summary": confidence_summary(evs),
        "n_evidence": len(evs),
    }
