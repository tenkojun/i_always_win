"""
팩터 익스포져 / 스마트베타 모듈
================================
- factor_exposure   : 종목 수익률을 FF 팩터로 회귀
- momentum_factor   : 가격 모멘텀 팩터 (long-short)
- low_vol_factor    : 저변동성 팩터
- quality_factor    : 수익률/위험 비율로 만든 품질 팩터

회귀 결과의 의미
----------------
- alpha  : 팩터로 설명되지 않는 초과수익 (실력 / 알파)
- beta_X : 해당 팩터에 대한 노출도
- R²     : 모델 설명력
"""
from __future__ import annotations
from typing import Dict
import numpy as np
import pandas as pd


def _np_ols(strategy_returns, factors, rf_col):
    """statsmodels 없는 환경용 numpy OLS fallback."""
    df = pd.concat([strategy_returns.rename("y"), factors], axis=1).dropna()
    if df.empty:
        return {}
    rf = df[rf_col].values if rf_col in df.columns else 0.0
    y = df["y"].values - rf
    Xcols = [c for c in df.columns if c not in {"y", rf_col}]
    X = df[Xcols].values
    X1 = np.column_stack([np.ones(len(X)), X])
    coef, *_ = np.linalg.lstsq(X1, y, rcond=None)
    yhat = X1 @ coef
    ss_res = np.sum((y - yhat) ** 2)
    ss_tot = np.sum((y - y.mean()) ** 2) or 1.0
    r2 = 1 - ss_res / ss_tot
    out = {"alpha": float(coef[0]), "r_squared": float(r2)}
    for k, v in zip(Xcols, coef[1:]):
        out[f"beta_{k}"] = float(v)
    return out


def factor_exposure(strategy_returns: pd.Series,
                    factors: pd.DataFrame,
                    rf_col: str = "RF") -> Dict[str, float]:
    """
    종목 수익률을 FF 팩터로 회귀.

    y - rf  =  α + Σ β_i · factor_i + ε

    Returns
    -------
    dict
        - alpha       : 초과수익 (절편)
        - r_squared   : 설명력
        - beta_MKT, beta_SMB, ... : 팩터 노출도
        - tstat_MKT, ...          : t-통계량 (statsmodels 있을 때만)
    """
    try:
        import statsmodels.api as sm
    except ImportError:
        return _np_ols(strategy_returns, factors, rf_col)

    df = pd.concat([strategy_returns.rename("y"), factors], axis=1).dropna()
    if df.empty:
        return {}
    rf = df[rf_col] if rf_col in df.columns else 0.0
    y = df["y"] - rf
    X = df.drop(columns=["y"] + ([rf_col] if rf_col in df.columns else []))
    X = sm.add_constant(X)
    res = sm.OLS(y, X).fit()

    out = {"alpha": float(res.params.get("const", 0.0)),
           "r_squared": float(res.rsquared)}
    for k, v in res.params.items():
        if k != "const":
            out[f"beta_{k}"] = float(v)
    for k, v in res.tvalues.items():
        if k != "const":
            out[f"tstat_{k}"] = float(v)
    return out


# ------------------------------------------------------------------ #
#  스마트베타 팩터 (cross-sectional)
# ------------------------------------------------------------------ #
def momentum_factor(prices: pd.DataFrame, lookback: int = 126) -> pd.Series:
    """
    모멘텀 팩터 — 과거 6개월 수익률 기준 long-high / short-low.

    Parameters
    ----------
    prices    : 종목별 종가 행렬 (열=종목)
    lookback  : 모멘텀 측정 기간 (일)
    """
    rets = prices.pct_change(lookback)
    rank = rets.rank(axis=1, pct=True)
    weights = (rank - 0.5)
    weights = weights.div(weights.abs().sum(axis=1), axis=0)
    daily = prices.pct_change()
    return (weights.shift() * daily).sum(axis=1).rename("MOM")


def low_vol_factor(prices: pd.DataFrame, lookback: int = 60) -> pd.Series:
    """저변동성 팩터 — 변동성이 낮은 종목 long, 높은 종목 short."""
    rets = prices.pct_change()
    vol = rets.rolling(lookback).std()
    rank = (-vol).rank(axis=1, pct=True)
    weights = (rank - 0.5)
    weights = weights.div(weights.abs().sum(axis=1), axis=0)
    return (weights.shift() * rets).sum(axis=1).rename("LOWVOL")


def quality_factor(returns: pd.DataFrame,
                   lookback: int = 126) -> pd.Series:
    """수익률/위험 비율(샤프 유사) 기반 품질 팩터."""
    mu = returns.rolling(lookback).mean()
    sd = returns.rolling(lookback).std()
    score = (mu / sd).rank(axis=1, pct=True)
    weights = (score - 0.5)
    weights = weights.div(weights.abs().sum(axis=1), axis=0)
    return (weights.shift() * returns).sum(axis=1).rename("QUALITY")
