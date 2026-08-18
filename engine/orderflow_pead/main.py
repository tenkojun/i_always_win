"""
독립 실행 진입점 — 데모/테스트용
==================================
사용법:
  python -m engine.orderflow_pead.main
또는 import:
  from engine.orderflow_pead.main import run_demo
  res = run_demo("AAPL")
"""
from __future__ import annotations

import json
from typing import Any, Dict

from .loader     import load_ohlcv, synth_orderflow, synth_earnings
from .strategy   import (
    OrderflowDeltaStrategy, PEADStrategy, Hybrid_OF_PEAD_Strategy,
)
from .backtester import run_backtest
from .optimizer  import grid_search, walk_forward_optimization
from .research   import signal_quality, drift_persistence


def build_bundle(ticker: str = "AAPL",
                 period_days: int = 730,
                 interval: str = "1d") -> Dict[str, Any]:
    ohlcv = load_ohlcv(ticker, period_days=period_days, interval=interval)
    ofd = synth_orderflow(ohlcv)
    ev = synth_earnings(ohlcv)
    return {"ohlcv": ohlcv, "orderflow": ofd, "earnings": ev,
            "ticker": ticker, "interval": interval}


def run_demo(ticker: str = "AAPL", period_days: int = 730) -> Dict[str, Any]:
    bundle = build_bundle(ticker, period_days)
    # 3개 전략 실행
    strat_of = OrderflowDeltaStrategy(
        delta_threshold=bundle["orderflow"]["delta"].abs().mean() * 1.2)
    strat_pead = PEADStrategy(sue_top_pct=0.2, drift_days=30)
    strat_hyb = Hybrid_OF_PEAD_Strategy()
    res = {
        "ticker": ticker,
        "period_days": period_days,
        "orderflow":  run_backtest(strat_of,  bundle),
        "pead":       run_backtest(strat_pead, bundle),
        "hybrid":     run_backtest(strat_hyb, bundle),
    }
    # 리서치 보강
    sig_of = strat_of.generate_signals(bundle)
    res["research"] = {
        "of_signal_quality":
            signal_quality(bundle["ohlcv"]["close"], sig_of["entries"]),
        "pead_drift":
            drift_persistence(bundle["ohlcv"]["close"], bundle["earnings"]),
    }
    return res


def run_quick_optimize(ticker: str = "AAPL",
                       period_days: int = 730) -> Dict[str, Any]:
    bundle = build_bundle(ticker, period_days)
    grid = {
        "delta_threshold":    [200, 500, 1000, 2000],
        "divergence_lookback":[5, 10, 20],
        "divergence_z":       [1.0, 1.5, 2.0],
        "max_hold_bars":      [10, 30, 60],
    }
    gs = grid_search(OrderflowDeltaStrategy, grid, bundle, top_n=5)
    wfa = walk_forward_optimization(
        OrderflowDeltaStrategy,
        {"delta_threshold": [300, 700, 1500],
         "max_hold_bars":   [20, 40]},
        bundle, n_folds=4)
    return {"grid_top": gs["top"], "wfa": wfa}


if __name__ == "__main__":
    print("=== Orderflow + PEAD demo ===")
    out = run_demo("AAPL", 730)
    print(json.dumps({
        "OF":     out["orderflow"]["metrics"],
        "PEAD":   out["pead"]["metrics"],
        "Hybrid": out["hybrid"]["metrics"],
    }, indent=2, ensure_ascii=False, default=str))
