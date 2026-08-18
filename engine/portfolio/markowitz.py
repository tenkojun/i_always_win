"""
마코위츠 평균-분산 최적화 모듈
==============================
주요 함수
---------
- markowitz_optimize  : 효율적 프론티어 최적화
                         - max_sharpe : 샤프 최대화
                         - min_var    : 분산 최소화
                         - target     : 목표수익률 분산 최소화
- volatility_targeting: 단일 자산 변동성 타겟팅 레버리지 시리즈

가정/한계
---------
- 정규분포 / 평균 변동 안정 가정
- 입력 공분산은 표본 추정치라 노이즈에 민감 → 실무에서는
  Black-Litterman 또는 Ledoit-Wolf shrinkage 와 조합.
"""
from __future__ import annotations
from typing import Dict, Optional
import numpy as np
import pandas as pd
from scipy.optimize import minimize


def _portfolio_stats(w, mu, cov, rf=0.0):
    """포트폴리오 (연환산) 수익률 / 변동성 / 샤프."""
    ret = float(w @ mu) * 252
    vol = float(np.sqrt(w @ cov @ w) * np.sqrt(252))
    sharpe = (ret - rf) / vol if vol > 0 else 0.0
    return ret, vol, sharpe


def markowitz_optimize(returns: pd.DataFrame,
                       objective: str = "max_sharpe",
                       rf: float = 0.0,
                       target_return: Optional[float] = None,
                       allow_short: bool = False) -> Dict:
    """
    Parameters
    ----------
    returns       : DataFrame (열=자산, 행=일별 수익률)
    objective     : 'max_sharpe' | 'min_var' | 'target'
    rf            : 무위험 이자율 (연율)
    target_return : 목표 연수익률 (objective='target' 인 경우 필수)
    allow_short   : 공매도 허용 여부
    """
    rets = returns.dropna()
    mu = rets.mean().values
    cov = rets.cov().values
    n = len(mu)

    bounds = [(-1, 1)] * n if allow_short else [(0, 1)] * n
    cons = [{"type": "eq", "fun": lambda w: w.sum() - 1}]

    if objective == "max_sharpe":
        def f(w):
            _, _, s = _portfolio_stats(w, mu, cov, rf)
            return -s
    elif objective == "min_var":
        def f(w):
            return float(w @ cov @ w)
    elif objective == "target":
        if target_return is None:
            raise ValueError("target_return 이 필요합니다")
        cons.append({"type": "eq",
                     "fun": lambda w: float(w @ mu) * 252 - target_return})
        def f(w):
            return float(w @ cov @ w)
    else:
        raise ValueError(objective)

    w0 = np.repeat(1 / n, n)
    res = minimize(f, w0, method="SLSQP", bounds=bounds, constraints=cons)
    w = res.x
    r, v, s = _portfolio_stats(w, mu, cov, rf)
    return {
        "weights":          dict(zip(returns.columns, w.tolist())),
        "expected_return":  r,
        "volatility":       v,
        "sharpe":           s,
        "success":          bool(res.success),
    }


def volatility_targeting(returns: pd.Series, target_vol: float = 0.15,
                         lookback: int = 20) -> pd.Series:
    """
    변동성 타겟팅 레버리지 시리즈.

    Parameters
    ----------
    target_vol : 목표 연환산 변동성 (예: 0.15 = 15%)
    lookback   : 변동성 추정 윈도

    Returns
    -------
    pd.Series  시점별 레버리지 (1.0 = 100% 풀투자)
    """
    realized = returns.rolling(lookback).std() * np.sqrt(252)
    lev = (target_vol / realized).clip(upper=3.0).fillna(1.0)
    return lev.rename("leverage")
