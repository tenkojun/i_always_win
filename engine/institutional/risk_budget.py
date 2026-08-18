"""
자산배분 · 리스크 버짓 모듈
============================
단일 종목 맥락에서 "이 종목에 자산의 몇 %를 배분해야
포트폴리오 위험이 목표치를 넘지 않는가?" 를 계산한다.

기관은 종목을 100% 사지 않는다. **변동성 타깃팅**으로
포지션 크기를 조절한다:

  배분비중 = 목표변동성 / 종목변동성   (상한 100%, 또는 레버리지)

추가로 **켈리 기준(Kelly)** 의 절반(하프 켈리)을 보조 지표로 제시.

출력
----
- ann_vol           : 종목 연환산 변동성
- target_vol        : 목표 포트폴리오 변동성
- suggested_weight  : 권장 배분 비중 (0~1, 1 초과면 레버리지)
- cash_weight       : 현금 비중 (1 - weight, 음수면 차입)
- half_kelly        : 하프 켈리 비중
- final_weight      : 변동성타깃·하프켈리 중 보수적인 값
- expected_port_vol : 권장 비중 적용 시 예상 포트폴리오 변동성
"""
from __future__ import annotations
from typing import Any, Dict
import numpy as np
import pandas as pd

from ..risk.sharpe import volatility


def risk_budget(returns: pd.Series,
                target_vol: float = 0.10,
                periods_per_year: int = 252) -> Dict[str, Any]:
    """
    Parameters
    ----------
    returns    : 종목 일간 수익률
    target_vol : 목표 연환산 포트폴리오 변동성 (기본 10%)
    """
    r = returns.dropna()
    if len(r) < 20:
        return {"note": "데이터 부족"}

    ann_vol = float(volatility(r, periods_per_year))
    if ann_vol <= 1e-6:
        return {"note": "변동성 0"}

    w_voltarget = target_vol / ann_vol

    # 하프 켈리: f* = μ / σ²  (연환산), 안전을 위해 0.5배
    mu_ann = float(r.mean() * periods_per_year)
    var_ann = ann_vol ** 2
    kelly = mu_ann / var_ann if var_ann > 0 else 0.0
    half_kelly = float(np.clip(kelly * 0.5, -1.0, 2.0))

    # 보수적: 변동성타깃과 하프켈리 중 작은 양수값
    candidates = [w for w in (w_voltarget, half_kelly) if w > 0]
    final_w = float(min(candidates)) if candidates else 0.0
    final_w = float(np.clip(final_w, 0.0, 2.0))

    return {
        "ann_vol":           ann_vol,
        "target_vol":        float(target_vol),
        "suggested_weight":  float(np.clip(w_voltarget, 0.0, 2.0)),
        "cash_weight":       float(1.0 - final_w),
        "half_kelly":        half_kelly,
        "final_weight":      final_w,
        "expected_port_vol": float(final_w * ann_vol),
    }
