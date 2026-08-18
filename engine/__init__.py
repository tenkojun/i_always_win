"""
종목 단/중/장 + 기관급 분석 엔진
==================================
주요 임포트 진입점.
"""
from engine.data.loader import load_ticker, synthetic_ohlcv
from engine.analysis.timeframe import analyze_ticker, DEFAULT_TIMEFRAMES
from engine.institutional import (
    mc_by_timeframe, factor_risk_decomposition,
    stress_test, wealth_projection, risk_budget, build_scorecard,
)

__version__ = "2.0.0-kr-institutional"
