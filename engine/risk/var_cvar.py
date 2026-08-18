"""
VaR / CVaR 분석 모듈
====================
세 가지 VaR 모두 양수(=손실 크기)로 반환한다.
즉 0.03 → "5% 확률로 3% 이상 손실 가능".

지표 설명
---------
- parametric_var : 정규분포 가정 (μ, σ 만 사용)
- historical_var : 실제 수익률 분포의 α 분위수
- monte_carlo_var: μ, σ 로 N회 시뮬레이션 후 분위수
- conditional_var: CVaR (Expected Shortfall) — VaR 이하 영역의 평균 손실
"""
from __future__ import annotations
from typing import Dict
import numpy as np
import pandas as pd
from scipy.stats import norm


def parametric_var(returns: pd.Series, alpha: float = 0.05) -> float:
    """
    파라메트릭 VaR (정규분포 가정).

    Parameters
    ----------
    alpha : 신뢰수준 (0.05 = 95% 신뢰)
    """
    mu, sd = returns.mean(), returns.std(ddof=1)
    z = norm.ppf(alpha)
    return float(-(mu + z * sd))


def historical_var(returns: pd.Series, alpha: float = 0.05) -> float:
    """
    히스토리컬 VaR — 분포 가정 없이 실제 분위수 사용.
    fat-tail 시장에서 파라메트릭보다 안전한 추정.
    """
    return float(-np.quantile(returns.dropna(), alpha))


def monte_carlo_var(returns: pd.Series, alpha: float = 0.05,
                    n_sim: int = 10_000, horizon: int = 1,
                    seed: int = 42) -> float:
    """
    몬테카를로 VaR — μ, σ 로 N회 정규분포 샘플링 후 분위수.

    Parameters
    ----------
    horizon : 며칠 후 손실인지 (1 = 1일 VaR)
    """
    rng = np.random.default_rng(seed)
    mu, sd = returns.mean(), returns.std(ddof=1)
    sims = rng.normal(mu, sd, size=(n_sim, horizon)).sum(axis=1)
    return float(-np.quantile(sims, alpha))


def conditional_var(returns: pd.Series, alpha: float = 0.05) -> float:
    """
    조건부 VaR (Expected Shortfall) — VaR 이하 영역에서의 평균 손실.
    Tail-risk 측정에 가장 신뢰 가능한 지표.
    """
    q = np.quantile(returns.dropna(), alpha)
    tail = returns[returns <= q]
    if len(tail) == 0:
        return 0.0
    return float(-tail.mean())


def all_var_metrics(returns: pd.Series, alpha: float = 0.05) -> Dict[str, float]:
    """네 가지 VaR / CVaR 을 한 번에 계산한다."""
    return {
        "parametric_var": parametric_var(returns, alpha),
        "historical_var": historical_var(returns, alpha),
        "mc_var":         monte_carlo_var(returns, alpha),
        "cvar":           conditional_var(returns, alpha),
    }
