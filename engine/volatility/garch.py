"""
변동성 모델 모듈 (GARCH / EGARCH)
=================================
- realized_vol     : 단순 실현 변동성 (rolling std × √252)
- fit_garch        : GARCH(1,1) 적합
- fit_egarch       : EGARCH(1,1) 적합 (비대칭, leverage effect 반영)
- forecast_volatility : N봉 앞 변동성 예측

arch 라이브러리가 없으면 EWMA(λ=0.94, Risk-Metrics 표준) fallback.
"""
from __future__ import annotations
from typing import Dict
import numpy as np
import pandas as pd

try:
    from arch import arch_model
    HAS_ARCH = True
except ImportError:
    HAS_ARCH = False


def realized_vol(returns: pd.Series, window: int = 20,
                 annualize: int = 252) -> pd.Series:
    """
    실현 변동성 = rolling std × √annualize.

    Parameters
    ----------
    window     : 이동 윈도 (영업일)
    annualize  : 연환산 계수 (일봉=252, 시간봉=252*24 등)
    """
    return returns.rolling(window).std() * np.sqrt(annualize)


def fit_garch(returns: pd.Series, p: int = 1, q: int = 1,
              dist: str = "normal") -> Dict:
    """
    GARCH(p,q) 적합.

    Parameters
    ----------
    p, q   : 차수
    dist   : 'normal' | 't' | 'skewt'

    Returns
    -------
    dict
        - model                  : 모델 이름
        - conditional_volatility : 시점별 연환산 변동성 시리즈
        - params                 : 파라미터 dict
        - aic, loglik            : 모델 적합도
    """
    if not HAS_ARCH:
        ewma = returns.ewm(alpha=1 - 0.94).var()
        return {
            "model": "EWMA(λ=0.94)",
            "conditional_volatility": np.sqrt(ewma) * np.sqrt(252),
            "params": {"lambda": 0.94},
        }
    am = arch_model(returns * 100, p=p, q=q, mean="zero",
                    vol="GARCH", dist=dist)
    res = am.fit(disp="off")
    return {
        "model":  f"GARCH({p},{q})",
        "conditional_volatility": pd.Series(
            res.conditional_volatility / 100 * np.sqrt(252),
            index=returns.index[-len(res.conditional_volatility):],
            name="cond_vol_annualized",
        ),
        "params": dict(res.params),
        "loglik": float(res.loglikelihood),
        "aic":    float(res.aic),
        "_res":   res,
    }


def fit_egarch(returns: pd.Series, p: int = 1, q: int = 1) -> Dict:
    """EGARCH(1,1) — 음의 충격이 더 큰 변동성을 만드는 비대칭 효과 반영."""
    if not HAS_ARCH:
        return fit_garch(returns, p, q)
    am = arch_model(returns * 100, p=p, q=q, mean="zero", vol="EGARCH")
    res = am.fit(disp="off")
    return {
        "model":  f"EGARCH({p},{q})",
        "conditional_volatility": pd.Series(
            res.conditional_volatility / 100 * np.sqrt(252),
            index=returns.index[-len(res.conditional_volatility):],
            name="cond_vol_annualized",
        ),
        "params": dict(res.params),
        "loglik": float(res.loglikelihood),
        "aic":    float(res.aic),
        "_res":   res,
    }


def forecast_volatility(fit_result: Dict, horizon: int = 10) -> np.ndarray:
    """
    적합된 GARCH 모델로 N봉 앞 변동성 예측 (연환산).
    """
    if "_res" not in fit_result:
        return np.repeat(fit_result["conditional_volatility"].iloc[-1], horizon)
    f = fit_result["_res"].forecast(horizon=horizon, reindex=False)
    return np.sqrt(f.variance.values[-1]) / 100 * np.sqrt(252)
