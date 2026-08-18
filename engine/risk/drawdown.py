"""
드로다운 분석 모듈
==================
- drawdown_series   : 시점별 손실폭 (peak 대비 %)
- max_drawdown      : 최대 손실폭 (MDD). 음수로 반환됨
- drawdown_duration : 손실 구간이 가장 길게 지속된 봉 수
"""
from __future__ import annotations
import numpy as np
import pandas as pd


def drawdown_series(equity: pd.Series) -> pd.Series:
    """
    각 시점에서 누적 최고점 대비 손실폭(비율) 시리즈를 만든다.
    예: -0.12 = 고점 대비 12% 손실.
    """
    roll_max = equity.cummax()
    return equity / roll_max - 1.0


def max_drawdown(equity: pd.Series) -> float:
    """
    최대 손실폭(MDD).

    Returns
    -------
    float  (음수, 예: -0.25 = 25% 손실)
    """
    return float(drawdown_series(equity).min())


def drawdown_duration(equity: pd.Series) -> int:
    """
    손실 구간이 가장 길게 지속된 봉 수(연속 일수).
    회복 시간이 짧을수록 좋다.
    """
    dd = drawdown_series(equity)
    under = (dd < 0).astype(int).values
    longest, run = 0, 0
    for x in under:
        run = run + 1 if x else 0
        longest = max(longest, run)
    return int(longest)
