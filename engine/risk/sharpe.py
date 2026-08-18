"""
리스크/성과 지표 모듈
=====================
수익률 시계열(returns) 또는 자본곡선(equity) 을 입력받아
표준 퀀트 지표들을 계산한다.

각 지표 설명
------------
- cagr            : 연복리 환산 수익률 (Compound Annual Growth Rate)
- volatility      : 연 환산 변동성 (수익률 표준편차 × √252)
- sharpe_ratio    : 위험조정 수익률  > 1 양호 / > 2 우수 / > 3 매우 우수
- sortino_ratio   : 하방 변동성만 분모로 쓰는 샤프
- calmar_ratio    : CAGR / |MDD|. 손실 1단위당 수익
- mar_ratio       : 실무적으로 Calmar 와 동일
- ulcer_index     : 손실 깊이 × 손실 기간 함께 측정 (작을수록 좋음)
- hit_ratio       : 승률
- expectancy      : 거래당 기대 손익 = p·avg_W + (1-p)·avg_L
- kelly_criterion : 켈리 비율. 이론적 최적 베팅 비율
"""
from __future__ import annotations
import numpy as np
import pandas as pd


def _ann(periods_per_year: int) -> float:
    """연 환산 계수 (일봉=252)."""
    return float(periods_per_year)


def cagr(equity: pd.Series, periods_per_year: int = 252) -> float:
    """
    연복리 환산 수익률(CAGR).

    Parameters
    ----------
    equity            : 자본 곡선 시계열
    periods_per_year  : 1년에 해당하는 봉 수 (일봉=252)

    Returns
    -------
    float  (예: 0.12 = 연 12%)
    """
    if len(equity) < 2 or equity.iloc[0] <= 0:
        return 0.0
    n_years = len(equity) / periods_per_year
    return (equity.iloc[-1] / equity.iloc[0]) ** (1 / n_years) - 1


def volatility(returns: pd.Series, periods_per_year: int = 252) -> float:
    """연 환산 변동성."""
    return float(returns.std(ddof=1) * np.sqrt(_ann(periods_per_year)))


def sharpe_ratio(returns: pd.Series, rf: float = 0.0,
                 periods_per_year: int = 252) -> float:
    """
    샤프 비율.

    Parameters
    ----------
    rf : 무위험 이자율 (연율)
    """
    rf_p = rf / periods_per_year
    excess = returns - rf_p
    sd = excess.std(ddof=1)
    if sd == 0 or np.isnan(sd):
        return 0.0
    return float(excess.mean() / sd * np.sqrt(_ann(periods_per_year)))


def sortino_ratio(returns: pd.Series, rf: float = 0.0,
                  periods_per_year: int = 252) -> float:
    """소르티노 비율 — 하방 변동성만 분모로 사용한다."""
    rf_p = rf / periods_per_year
    excess = returns - rf_p
    downside = excess[excess < 0].std(ddof=1)
    if downside == 0 or np.isnan(downside):
        return 0.0
    return float(excess.mean() / downside * np.sqrt(_ann(periods_per_year)))


def calmar_ratio(equity: pd.Series, periods_per_year: int = 252) -> float:
    """칼마 비율 = CAGR / |Max Drawdown|."""
    from .drawdown import max_drawdown
    mdd = abs(max_drawdown(equity))
    if mdd == 0:
        return 0.0
    return cagr(equity, periods_per_year) / mdd


def mar_ratio(equity: pd.Series, periods_per_year: int = 252) -> float:
    """MAR 비율 (실무상 Calmar 와 동일)."""
    return calmar_ratio(equity, periods_per_year)


def ulcer_index(equity: pd.Series) -> float:
    """얼서 지수 — 손실의 깊이와 기간을 함께 측정 (작을수록 좋음)."""
    roll_max = equity.cummax()
    dd = (equity / roll_max - 1) * 100
    return float(np.sqrt((dd ** 2).mean()))


def hit_ratio(trades: pd.DataFrame) -> float:
    """승률."""
    if trades is None or len(trades) == 0:
        return 0.0
    return float((trades["pnl"] > 0).mean())


def expectancy(trades: pd.DataFrame) -> float:
    """거래당 기대 손익."""
    if trades is None or len(trades) == 0:
        return 0.0
    wins = trades[trades["pnl"] > 0]["pnl"]
    losses = trades[trades["pnl"] < 0]["pnl"]
    p = len(wins) / len(trades)
    avg_w = wins.mean() if len(wins) else 0
    avg_l = losses.mean() if len(losses) else 0
    return float(p * avg_w + (1 - p) * avg_l)


def kelly_criterion(trades: pd.DataFrame) -> float:
    """켈리 비율 = p - (1-p)/b   (b = |평균이익/평균손실|)."""
    if trades is None or len(trades) == 0:
        return 0.0
    wins = trades[trades["pnl"] > 0]["pnl"]
    losses = trades[trades["pnl"] < 0]["pnl"]
    p = len(wins) / len(trades)
    if len(wins) == 0 or len(losses) == 0:
        return 0.0
    b = abs(wins.mean() / losses.mean())
    return float(p - (1 - p) / b)
