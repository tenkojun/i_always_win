"""
Orderflow Delta + PEAD 통합 엔진 (vectorbt 기반)
=================================================
3개 전략:
  - OrderflowDeltaStrategy   : CVD 기반 모멘텀 + divergence
  - PEADStrategy             : Post-Earnings Announcement Drift
  - Hybrid_OF_PEAD_Strategy  : 두 시그널 합성

실 tick/earnings 데이터는 유료 — 본 엔진은 합성 데이터로 작동하되
인터페이스는 실데이터 플러그인 가능하게 분리됨.
"""
from .loader     import load_ohlcv, synth_orderflow, synth_earnings
from .indicators import (
    sma, ema, macd, rsi, atr,
)
from .orderflow  import cvd, bid_ask_aggression, delta_divergence, mtf_delta
from .pead       import sue, surprise_pct, abnormal_return, cumulative_ar
from .strategy   import (
    OrderflowDeltaStrategy, PEADStrategy, Hybrid_OF_PEAD_Strategy,
)
from .backtester import run_backtest, summarize
from .optimizer  import (
    grid_search, random_search, walk_forward_optimization,
)
from .plotter    import (
    plot_equity, plot_signals, plot_delta_bubbles,
    plot_pead_events, plot_param_surface,
)
from .research   import (
    signal_quality, drift_persistence, of_imbalance_clusters,
)

__all__ = [
    "load_ohlcv", "synth_orderflow", "synth_earnings",
    "sma", "ema", "macd", "rsi", "atr",
    "cvd", "bid_ask_aggression", "delta_divergence", "mtf_delta",
    "sue", "surprise_pct", "abnormal_return", "cumulative_ar",
    "OrderflowDeltaStrategy", "PEADStrategy", "Hybrid_OF_PEAD_Strategy",
    "run_backtest", "summarize",
    "grid_search", "random_search", "walk_forward_optimization",
    "plot_equity", "plot_signals", "plot_delta_bubbles",
    "plot_pead_events", "plot_param_surface",
    "signal_quality", "drift_persistence", "of_imbalance_clusters",
]
