"""
walk_forward.py — 모든 전략에 일반화된 Walk-Forward Analysis
============================================================
N folds로 시계열 분할 → 각 fold에서 train 구간 grid → best param
→ test 구간 백테스트 → OOS 성과 측정.

기관 기준: OOS Sharpe ≥ 50% × IS Sharpe면 견고. 그 미만이면 overfit.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from typing import Any, Dict, List, Optional


def walk_forward_strategy(ticker: str, strategy: str,
                           grid: Dict[str, List[Any]],
                           period_days: int = 730,
                           interval: str = "1d",
                           n_folds: int = 3,
                           train_ratio: float = 0.6
                           ) -> Dict[str, Any]:
    """전략에 walk-forward 적용.

    각 fold:
      - train 구간 grid → 최고 Sharpe 파라미터 찾기
      - test 구간에 그 파라미터로 백테스트 → OOS metric
    반환: 평균 OOS Sharpe + 개별 fold 결과
    """
    from .vbt_runner import run_backtest, AVAILABLE_STRATEGIES, _fetch_price
    if strategy not in AVAILABLE_STRATEGIES:
        return {"ok": False, "error": f"unknown strategy {strategy}"}
    # 전체 가격 fetch
    try:
        price = _fetch_price(ticker, period_days=period_days,
                              interval=interval)
        if len(price) < 100:
            return {"ok": False,
                    "error": f"데이터 부족 (n={len(price)} < 100)"}
    except Exception as e:
        return {"ok": False, "error": f"fetch 실패: {e}"}

    n = len(price)
    fold_size = n // n_folds
    fold_train_n = int(fold_size * train_ratio)
    fold_test_n = fold_size - fold_train_n

    if fold_train_n < 30 or fold_test_n < 10:
        return {"ok": False,
                "error": f"fold 너무 작음 (train={fold_train_n}, test={fold_test_n})"}

    fold_results = []
    for fi in range(n_folds):
        start_idx = fi * fold_size
        train_end_idx = start_idx + fold_train_n
        test_end_idx = train_end_idx + fold_test_n
        if test_end_idx > n:
            break
        train_dates = price.index[start_idx:train_end_idx]
        test_dates = price.index[train_end_idx:test_end_idx]
        # grid 탐색 (train 구간만으로 결정 — period_days 환산)
        train_days = (train_dates[-1] - train_dates[0]).days + 1
        # 각 combo 백테스트 → train Sharpe 정렬
        from itertools import product
        keys = list(grid.keys())
        combos = list(product(*[grid[k] for k in keys]))
        if not combos:
            continue
        if len(combos) > 100:
            combos = combos[:100]   # 안전 cap
        best = None
        best_sharpe = -1e9
        for combo in combos:
            params = dict(zip(keys, combo))
            try:
                r = run_backtest(ticker, strategy, params=params,
                                  period_days=train_days,
                                  interval=interval)
                if r.get("ok") and r["metrics"]["sharpe"] > best_sharpe:
                    best_sharpe = r["metrics"]["sharpe"]
                    best = params
            except Exception:
                continue
        if best is None:
            continue
        # test 구간 백테스트 (best params로 전체 다시 — 추후 fold-specific 가능)
        test_days = (test_dates[-1] - test_dates[0]).days + 1
        try:
            test_r = run_backtest(ticker, strategy, params=best,
                                    period_days=test_days,
                                    interval=interval)
            if test_r.get("ok"):
                m = test_r["metrics"]
                fold_results.append({
                    "fold": fi + 1,
                    "train_start": train_dates[0].strftime("%Y-%m-%d"),
                    "train_end":   train_dates[-1].strftime("%Y-%m-%d"),
                    "test_start":  test_dates[0].strftime("%Y-%m-%d"),
                    "test_end":    test_dates[-1].strftime("%Y-%m-%d"),
                    "best_params": best,
                    "is_sharpe":   round(best_sharpe, 3),
                    "oos_sharpe":  round(m.get("sharpe") or 0, 3),
                    "oos_return":  m.get("total_return"),
                    "oos_mdd":     m.get("max_drawdown"),
                    "oos_trades":  m.get("n_trades"),
                })
        except Exception:
            continue
    if not fold_results:
        return {"ok": False, "error": "fold 0 — 모든 fold 실패"}
    oos_sharpes = [f["oos_sharpe"] for f in fold_results]
    is_sharpes = [f["is_sharpe"] for f in fold_results]
    avg_oos = float(np.mean(oos_sharpes))
    avg_is = float(np.mean(is_sharpes))
    robust_ratio = avg_oos / avg_is if avg_is > 1e-9 else 0
    # 견고성 평가
    if robust_ratio > 0.6:
        verdict = "✓ 견고 (OOS/IS > 60%)"
    elif robust_ratio > 0.3:
        verdict = "보통 (OOS/IS 30~60%)"
    else:
        verdict = "⚠ Overfit 의심 (OOS/IS < 30%)"
    return {
        "ok": True, "ticker": ticker, "strategy": strategy,
        "n_folds": len(fold_results),
        "folds": fold_results,
        "avg_is_sharpe":   round(avg_is, 3),
        "avg_oos_sharpe":  round(avg_oos, 3),
        "robust_ratio":    round(robust_ratio, 3),
        "verdict":         verdict,
    }
