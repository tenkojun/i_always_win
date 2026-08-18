"""
vectorbt 기반 전략 백테스트 + 파라미터 그리드
================================================
3개 클래식 전략 지원:
  - sma_cross : SMA 골든/데드 크로스
  - rsi_mr    : RSI mean reversion (30/70 임계)
  - macd      : MACD 히스토그램 부호 변화

각 전략은 단일 실행 + 파라미터 grid search 둘 다 지원.
"""
from .vbt_runner import (
    run_backtest, run_grid_search,
    AVAILABLE_STRATEGIES, get_strategy_meta,
)
from .mega_grid import run_mega_grid

__all__ = [
    "run_backtest", "run_grid_search", "run_mega_grid",
    "AVAILABLE_STRATEGIES", "get_strategy_meta",
]
