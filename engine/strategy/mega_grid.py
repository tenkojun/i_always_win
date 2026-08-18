"""
All-Strategy Mega Grid
=========================
한 종목에 대해 모든 등록 전략 × 각 파라미터 grid를 순차 백테스트.
Sharpe 또는 알파(α) 기준 상위 N개 반환.

시간이 오래 걸려도 OK — 사용자 명시적 요청.
"""
from __future__ import annotations

import time
from itertools import product
from typing import Any, Dict, List

from .vbt_runner import AVAILABLE_STRATEGIES, run_backtest


def _auto_grid(strategy: str, density: int = 4) -> Dict[str, List[Any]]:
    """전략의 모든 int 파라미터에 대해 N단계 균등값 생성."""
    meta = AVAILABLE_STRATEGIES.get(strategy)
    if not meta:
        return {}
    grid = {}
    for p in meta["params"]:
        if p.get("type") != "int":
            continue
        mn, mx = p["min"], p["max"]
        vals = []
        for i in range(density):
            v = round(mn + (mx - mn) * i / max(1, density - 1))
            if not vals or vals[-1] != v:
                vals.append(v)
        grid[p["name"]] = vals
    return grid


def run_mega_grid(ticker: str, period_days: int = 365,
                  density: int = 3, top_n: int = 15,
                  rank_by: str = "alpha") -> Dict[str, Any]:
    """
    모든 전략 × auto grid 합산 백테스트.

    Parameters
    ----------
    density : int, default=3
        각 파라미터 당 시험 값 개수 (3=낮음, 5=촘촘, 7=매우 촘촘).
        density^N (N=파라미터 수) 조합이 전략당 생성됨.
    rank_by : 'alpha' | 'sharpe' | 'total_return'

    Returns
    -------
    {
      ok: True,
      ticker, n_total, elapsed_sec,
      top: [{strategy, params, sharpe, total_return, alpha,
             max_drawdown, n_trades}, ...]
    }
    """
    if not ticker:
        return {"ok": False, "error": "ticker 필요"}
    t0 = time.time()
    all_results = []
    total_combos = 0
    errors = []

    for strat_id in list(AVAILABLE_STRATEGIES.keys()):
        grid = _auto_grid(strat_id, density)
        if not grid:
            # 파라미터 없으면 default 1회만
            r = run_backtest(ticker, strat_id, params={},
                              period_days=period_days)
            total_combos += 1
            if r.get("ok"):
                m = r["metrics"]
                all_results.append({
                    "strategy": strat_id,
                    "params": r.get("params", {}),
                    "sharpe": m["sharpe"],
                    "total_return": m["total_return"],
                    "alpha": m["alpha"],
                    "max_drawdown": m["max_drawdown"],
                    "n_trades": m["n_trades"],
                })
            else:
                errors.append(f"{strat_id}: {r.get('error', 'unknown')[:50]}")
            continue
        keys = list(grid.keys())
        combos = list(product(*[grid[k] for k in keys]))
        for vals in combos:
            params = dict(zip(keys, vals))
            r = run_backtest(ticker, strat_id, params=params,
                              period_days=period_days)
            total_combos += 1
            if r.get("ok"):
                m = r["metrics"]
                all_results.append({
                    "strategy": strat_id,
                    "params": params,
                    "sharpe": m["sharpe"],
                    "total_return": m["total_return"],
                    "alpha": m["alpha"],
                    "max_drawdown": m["max_drawdown"],
                    "n_trades": m["n_trades"],
                })

    # 정렬
    sort_key = {"alpha": "alpha", "sharpe": "sharpe",
                "total_return": "total_return"}.get(rank_by, "alpha")
    all_results.sort(key=lambda x: -x[sort_key])
    return {
        "ok": True,
        "ticker": ticker,
        "n_total": total_combos,
        "n_valid": len(all_results),
        "n_errors": len(errors),
        "errors_sample": errors[:5],
        "elapsed_sec": round(time.time() - t0, 1),
        "top": all_results[:top_n],
        "rank_by": rank_by,
    }
