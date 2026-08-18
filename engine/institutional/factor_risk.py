"""
팩터 위험 분해 모듈 (Aladdin-style Factor Risk Decomposition)
===============================================================
블랙록 Aladdin 의 핵심 사고방식:

  "이 자산의 위험은 '어디서' 오는가?"

종목 수익률을 Fama-French 팩터(시장·규모·가치·수익성·투자)로
회귀한 뒤, **총 분산을 팩터별 기여도로 분해**한다.

  Var(r) = Σ_i Σ_j  β_i β_j Cov(F_i, F_j)   (체계적 위험)
         + Var(ε)                            (고유/종목특수 위험)

이렇게 하면 "이 종목 변동성의 70%는 시장 전체 때문이고
30%만 종목 고유 요인" 같은 기관식 설명이 가능하다.

출력 dict 속성
--------------
- betas            : {MKT, SMB, HML, ...} 팩터 노출도
- alpha_ann        : 연환산 알파 (팩터로 설명 안 되는 초과수익)
- r_squared        : 모델 설명력
- total_vol_ann    : 연환산 총 변동성
- systematic_pct   : 체계적(팩터) 위험 비중 (%)
- specific_pct     : 고유(종목특수) 위험 비중 (%)
- contrib_pct      : {팩터명: 위험기여 %} (고유분 포함)
- top_driver       : 위험을 가장 많이 키우는 요인 이름
- top_driver_pct   : 그 요인의 위험 기여 (%)
- factor_labels    : 팩터 코드 → 한글 이름 매핑
"""
from __future__ import annotations
from typing import Any, Dict
import numpy as np
import pandas as pd

from ..factor.exposure import factor_exposure

FACTOR_KR = {
    "MKT": "시장(전체 증시)",
    "SMB": "규모(소형주)",
    "HML": "가치(저PBR)",
    "RMW": "수익성(우량)",
    "CMA": "투자성향(보수)",
    "RF":  "무위험",
    "specific": "종목 고유 요인",
}


def factor_risk_decomposition(returns: pd.Series,
                              factors: pd.DataFrame,
                              rf_col: str = "RF",
                              periods_per_year: int = 252) -> Dict[str, Any]:
    """
    종목 수익률을 팩터로 회귀하고 위험을 분해한다.

    Parameters
    ----------
    returns  : 종목 일간 수익률
    factors  : 팩터 일간 수익률 (MKT, SMB, HML, [RMW, CMA], RF)
    rf_col   : 무위험 수익률 열 이름
    """
    exp = factor_exposure(returns, factors, rf_col=rf_col)
    if not exp:
        return {"note": "회귀 실패 (데이터 정렬 불가)"}

    betas = {k.replace("beta_", ""): v
             for k, v in exp.items() if k.startswith("beta_")}
    alpha_daily = exp.get("alpha", 0.0)
    r2 = exp.get("r_squared", 0.0)

    # 정렬된 공통 구간으로 공분산 계산
    fac_cols = [c for c in factors.columns if c != rf_col and c in betas]
    df = pd.concat([returns.rename("y"), factors[fac_cols]], axis=1).dropna()
    if len(df) < 10 or not fac_cols:
        return {"note": "데이터 부족"}

    cov_f = df[fac_cols].cov().values * periods_per_year       # 연환산 팩터 공분산
    b = np.array([betas[c] for c in fac_cols])

    total_var = float(df["y"].var() * periods_per_year)
    systematic_var = float(b @ cov_f @ b)
    systematic_var = max(0.0, min(systematic_var, total_var))
    specific_var = max(total_var - systematic_var, 1e-12)

    # 팩터별 한계 기여 (b_i · (Σβ)_i) — 합 = systematic_var
    marg = b * (cov_f @ b)
    contrib_pct: Dict[str, float] = {}
    if systematic_var > 0:
        for c, m in zip(fac_cols, marg):
            contrib_pct[c] = float(m / total_var * 100)
    contrib_pct["specific"] = float(specific_var / total_var * 100)

    # 가장 큰 위험 동인
    top = max(contrib_pct.items(), key=lambda kv: abs(kv[1]))

    return {
        "betas":          {k: float(v) for k, v in betas.items()},
        "alpha_ann":      float(alpha_daily * periods_per_year),
        "r_squared":      float(r2),
        "total_vol_ann":  float(np.sqrt(total_var)),
        "systematic_pct": float(systematic_var / total_var * 100),
        "specific_pct":   float(specific_var / total_var * 100),
        "contrib_pct":    contrib_pct,
        "top_driver":     top[0],
        "top_driver_pct": float(top[1]),
        "factor_labels":  FACTOR_KR,
    }
