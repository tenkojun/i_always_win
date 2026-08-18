"""
몬테카를로 부(富) 예측 모듈
============================
"이 종목에 N원을 넣으면, M년 뒤 목표를 달성할 확률은?"

연기금·자산운용사가 고객 자산을 운용할 때 쓰는
**목표 기반 투자(Goal-Based Investing)** 관점을 단일 종목에 적용.

mc_projection 의 종착 수익률 분포를 받아
- 원금 대비 기대 평가금액
- 목표 수익률(예: +10%, +20%, 2배) 달성 확률
- 물가(기본 3%)·예금(기본 3.5%) 초과 확률
- 손실(원금 미만) 확률
을 계산한다.

출력
----
- initial            : 투자 원금
- horizon_days       : 예측 영업일
- exp_value          : 기대 평가금액
- median_value       : 중앙값 평가금액
- p5_value/p95_value : 5% / 95% 평가금액
- goals              : {라벨: {target_pct, prob}} 달성 확률표
- prob_loss          : 원금 손실 확률 (%)
- prob_beat_infl     : 물가 상승률 초과 확률 (%)
- prob_beat_deposit  : 예금 금리 초과 확률 (%)
"""
from __future__ import annotations
from typing import Any, Dict, List, Optional
import numpy as np


def wealth_projection(mc_result: Dict[str, Any],
                      initial: float = 10_000_000,
                      goals_pct: Optional[List[float]] = None,
                      annual_inflation: float = 0.03,
                      annual_deposit: float = 0.035) -> Dict[str, Any]:
    """
    Parameters
    ----------
    mc_result        : mc_projection.project_price_paths 결과
    initial          : 투자 원금 (기본 1,000만원)
    goals_pct        : 목표 수익률 리스트. None 이면 [0.10, 0.20, 0.50, 1.00]
    annual_inflation : 연 물가상승률 가정
    annual_deposit   : 연 예금/무위험 금리 가정
    """
    if "terminal" not in mc_result or "start_price" not in mc_result:
        return {"note": "몬테카를로 결과 없음"}

    goals_pct = goals_pct or [0.10, 0.20, 0.50, 1.00]
    s0 = mc_result["start_price"]
    terminal = np.asarray(mc_result["terminal"], dtype=float)
    ret = terminal / s0 - 1.0
    horizon = int(mc_result.get("horizon_days", 252))
    yrs = horizon / 252.0

    values = initial * (1.0 + ret)

    infl_target = (1 + annual_inflation) ** yrs - 1
    dep_target = (1 + annual_deposit) ** yrs - 1

    labels = {0.10: "+10% 달성", 0.20: "+20% 달성",
              0.50: "+50% 달성", 1.00: "원금 2배"}
    goals: Dict[str, Any] = {}
    for g in goals_pct:
        goals[labels.get(g, f"+{int(g*100)}% 달성")] = {
            "target_pct": float(g * 100),
            "prob":       float((ret >= g).mean() * 100),
        }

    return {
        "initial":          float(initial),
        "horizon_days":     horizon,
        "horizon_years":    round(yrs, 2),
        "exp_value":        float(values.mean()),
        "median_value":     float(np.median(values)),
        "p5_value":         float(np.percentile(values, 5)),
        "p95_value":        float(np.percentile(values, 95)),
        "goals":            goals,
        "prob_loss":        float((ret < 0).mean() * 100),
        "prob_beat_infl":   float((ret >= infl_target).mean() * 100),
        "prob_beat_deposit": float((ret >= dep_target).mean() * 100),
        "infl_target_pct":  float(infl_target * 100),
        "deposit_target_pct": float(dep_target * 100),
    }
