"""
시나리오 / 스트레스 테스트 모듈
================================
블랙록식 위험관리의 또 다른 축: "최악의 상황에서 얼마나 버티나?"

종목의 시장 베타(β_MKT)를 이용해 과거 위기·가상 충격 시
**예상 손실과 예상 가격**을 추정한다.

  종목충격 ≈ β_MKT × 시장충격  +  종목 고유충격(보수적 가정)

지원 시나리오 (DEFAULT_SCENARIOS)
---------------------------------
- 2008 금융위기   : 시장 -50%, 변동성 급등
- 2020 코로나 쇼크 : 시장 -34% (빠른 폭락)
- 2022 금리인상    : 시장 -20%, 성장주 추가 타격
- 인플레 쇼크      : 시장 -15%
- 일반 약세장      : 시장 -10%

출력
----
각 시나리오별
- shock_pct        : 종목 예상 충격 (%)
- price_after      : 충격 후 예상 가격
- loss_amount_pct  : 손실 폭 (%)
- recovery_days    : 과거 평균 회복 속도 가정 기반 추정 회복일
"""
from __future__ import annotations
from typing import Any, Dict, Optional
import numpy as np
import pandas as pd


# (시장충격 %, 고유 추가충격 %, 라벨 설명)
DEFAULT_SCENARIOS: Dict[str, Dict[str, Any]] = {
    "2008 금융위기": {"mkt": -0.50, "idio": -0.05,
                   "desc": "리먼 사태급 신용경색 — 시장 전반 -50%"},
    "2020 코로나 쇼크": {"mkt": -0.34, "idio": -0.03,
                     "desc": "팬데믹 초기 한 달 급락 — 시장 -34%"},
    "2022 금리 인상 쇼크": {"mkt": -0.20, "idio": -0.08,
                       "desc": "급격한 긴축 — 시장 -20%, 성장주 추가 타격"},
    "인플레이션 쇼크": {"mkt": -0.15, "idio": -0.02,
                  "desc": "예상 밖 물가 급등 — 시장 -15%"},
    "일반 약세장": {"mkt": -0.10, "idio": -0.01,
               "desc": "통상적 조정 국면 — 시장 -10%"},
}


def stress_test(returns: pd.Series,
                current_price: float,
                beta_mkt: float = 1.0,
                scenarios: Optional[Dict[str, Dict[str, Any]]] = None
                ) -> Dict[str, Any]:
    """
    시나리오별 예상 손실/가격을 계산한다.

    Parameters
    ----------
    returns       : 종목 일간 수익률 (회복속도 추정용)
    current_price : 현재가
    beta_mkt      : 시장 베타 (factor_risk 의 betas['MKT'])
    scenarios     : None 이면 DEFAULT_SCENARIOS 사용
    """
    scen = scenarios or DEFAULT_SCENARIOS
    beta_mkt = float(beta_mkt) if np.isfinite(beta_mkt) else 1.0
    beta_mkt = max(0.2, min(beta_mkt, 3.0))   # 비현실적 베타 클리핑

    # 평소 일평균 상승폭(양의 수익률 평균)으로 회복 속도 가정
    pos = returns[returns > 0]
    daily_up = float(pos.mean()) if len(pos) else 0.0005
    daily_up = max(daily_up, 1e-4)

    results: Dict[str, Any] = {}
    for name, cfg in scen.items():
        shock = beta_mkt * cfg["mkt"] + cfg["idio"]
        shock = max(shock, -0.95)
        price_after = current_price * (1 + shock)
        # 손실 회복: log(1/(1+shock)) / daily_up  (대략)
        rec_days = int(np.log(1.0 / (1.0 + shock)) / daily_up) if shock < 0 else 0
        results[name] = {
            "desc":            cfg["desc"],
            "shock_pct":       float(shock * 100),
            "price_after":     float(price_after),
            "loss_amount_pct": float(shock * 100),
            "recovery_days":   int(min(rec_days, 5000)),
        }

    worst = min(results.items(), key=lambda kv: kv[1]["shock_pct"])
    return {
        "beta_used":    beta_mkt,
        "scenarios":    results,
        "worst_case":   worst[0],
        "worst_loss_pct": worst[1]["shock_pct"],
    }
