"""
최적화 엔진
============
- grid_search                 : 전수 탐색
- random_search               : 랜덤 샘플링
- walk_forward_optimization   : 시계열 분할 + 각 fold별 best param
"""
from __future__ import annotations

import itertools
import random
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from .backtester import run_backtest


def _eval(strategy_cls, params, bundle, fees, init_cash, freq):
    try:
        strat = strategy_cls(**params)
        r = run_backtest(strat, bundle, fees=fees, init_cash=init_cash,
                          freq=freq)
        m = r["metrics"]
        return {
            "params":  params,
            "sharpe":  m["sharpe"],
            "cagr":    m["cagr"],
            "max_dd":  m["max_drawdown"],
            "n_trades": m["n_trades"],
            "win_rate": m["win_rate"],
        }
    except Exception as e:
        return {"params": params, "error": str(e), "sharpe": -1e9}


def grid_search(strategy_cls,
                param_grid: Dict[str, List[Any]],
                bundle,
                fees: float = 0.0005,
                init_cash: float = 100000.0,
                freq: str = "1D",
                top_n: int = 10) -> Dict[str, Any]:
    """모든 조합 평가, top_n 반환."""
    keys = list(param_grid.keys())
    vals = list(param_grid.values())
    combos = list(itertools.product(*vals))
    results = []
    for cmb in combos:
        params = dict(zip(keys, cmb))
        results.append(_eval(strategy_cls, params, bundle,
                              fees, init_cash, freq))
    results.sort(key=lambda x: x.get("sharpe", -1e9), reverse=True)
    return {"n_combos": len(combos),
            "top": results[:top_n],
            "all": results}


def random_search(strategy_cls,
                  param_grid: Dict[str, List[Any]],
                  bundle,
                  n_samples: int = 30,
                  fees: float = 0.0005,
                  init_cash: float = 100000.0,
                  freq: str = "1D",
                  top_n: int = 10,
                  seed: int = 42) -> Dict[str, Any]:
    rng = random.Random(seed)
    results = []
    for _ in range(n_samples):
        params = {k: rng.choice(v) for k, v in param_grid.items()}
        results.append(_eval(strategy_cls, params, bundle,
                              fees, init_cash, freq))
    results.sort(key=lambda x: x.get("sharpe", -1e9), reverse=True)
    return {"n_samples": n_samples,
            "top": results[:top_n],
            "all": results}


def walk_forward_optimization(strategy_cls,
                              param_grid: Dict[str, List[Any]],
                              bundle,
                              n_folds: int = 5,
                              train_frac: float = 0.7,
                              fees: float = 0.0005,
                              init_cash: float = 100000.0,
                              freq: str = "1D") -> Dict[str, Any]:
    """
    Walk-Forward: 각 fold에서
      in-sample → grid_search → best params
      out-of-sample → 그 params로 백테스트 → 성능 기록
    """
    ohlcv = bundle["ohlcv"]
    n = len(ohlcv)
    fold_size = n // n_folds
    folds = []
    for k in range(n_folds):
        i0 = k * fold_size
        i1 = min((k + 1) * fold_size, n)
        if i1 - i0 < 60:
            continue
        is_end = i0 + int((i1 - i0) * train_frac)
        in_idx = ohlcv.index[i0:is_end]
        out_idx = ohlcv.index[is_end:i1]
        if len(in_idx) < 30 or len(out_idx) < 10:
            continue
        # bundle 분할
        in_bundle = _slice_bundle(bundle, in_idx)
        out_bundle = _slice_bundle(bundle, out_idx)
        # In-sample 최적화
        gs = grid_search(strategy_cls, param_grid, in_bundle,
                          fees=fees, init_cash=init_cash, freq=freq, top_n=1)
        if not gs["top"]:
            continue
        best = gs["top"][0]
        # Out-of-sample 검증
        oos = _eval(strategy_cls, best["params"], out_bundle,
                     fees, init_cash, freq)
        folds.append({
            "fold":  k,
            "is_start":  str(in_idx[0].date()) if len(in_idx) else "",
            "is_end":    str(in_idx[-1].date()) if len(in_idx) else "",
            "oos_start": str(out_idx[0].date()) if len(out_idx) else "",
            "oos_end":   str(out_idx[-1].date()) if len(out_idx) else "",
            "best_params": best["params"],
            "is_sharpe":  best["sharpe"],
            "oos_sharpe": oos.get("sharpe", 0),
            "oos_cagr":   oos.get("cagr", 0),
            "oos_max_dd": oos.get("max_dd", 0),
        })
    avg_oos = np.mean([f["oos_sharpe"] for f in folds]) if folds else 0
    return {
        "n_folds_used": len(folds),
        "folds": folds,
        "avg_oos_sharpe": round(float(avg_oos), 3),
    }


def _slice_bundle(bundle, idx):
    out = {}
    for k, v in bundle.items():
        if isinstance(v, pd.DataFrame) and isinstance(v.index, pd.DatetimeIndex):
            out[k] = v.loc[v.index.intersection(idx)]
        elif isinstance(v, pd.DataFrame) and "event_date" in v.columns:
            mask = (v["event_date"] >= idx[0]) & (v["event_date"] <= idx[-1])
            out[k] = v[mask].copy()
        else:
            out[k] = v
    return out
