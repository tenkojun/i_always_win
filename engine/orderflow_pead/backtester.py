"""
백테스트 실행 + 메트릭 요약
==============================
vbt.Portfolio 결과를 JSON 친화 dict로 변환.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from typing import Any, Dict


def run_backtest(strategy, bundle: Dict[str, Any],
                 fees: float = 0.0005,
                 init_cash: float = 100000.0,
                 freq: str = "1D") -> Dict[str, Any]:
    """전략 실행 후 메트릭 dict 반환."""
    pf, sig = strategy.run(bundle, fees=fees, init_cash=init_cash, freq=freq)
    metrics = summarize(pf)
    eq = pf.value()
    if len(eq) > 250:
        step = max(1, len(eq) // 250)
        eq = eq.iloc[::step]
    return {
        "ok": True,
        "strategy": strategy.name,
        "params": strategy.params,
        "metrics": metrics,
        "equity_curve": [
            {"d": (d.strftime("%Y-%m-%d") if hasattr(d, "strftime") else str(d)),
             "v": float(v)}
            for d, v in eq.items()
        ],
        "monthly_returns": _monthly_returns(pf),
        "yearly_returns": _yearly_returns(pf),
        "trades_count": int(pf.trades.count()),
    }


def summarize(pf) -> Dict[str, Any]:
    """vectorbt Portfolio → 핵심 지표 dict."""
    def _safe(x, default=0.0):
        try:
            v = float(x)
            return v if np.isfinite(v) else default
        except Exception:
            return default
    total_ret = _safe(pf.total_return())
    sharpe = _safe(pf.sharpe_ratio())
    sortino = _safe(pf.sortino_ratio())
    max_dd = _safe(pf.max_drawdown())
    # CAGR
    eq = pf.value()
    if len(eq) > 1:
        days = max(1, (eq.index[-1] - eq.index[0]).days)
        years = days / 365.25
        cagr = (eq.iloc[-1] / eq.iloc[0]) ** (1 / max(years, 1e-9)) - 1
    else:
        cagr = 0.0
    try:
        win_rate = _safe(pf.trades.win_rate())
    except Exception:
        win_rate = 0.0
    try:
        n_trades = int(pf.trades.count())
    except Exception:
        n_trades = 0
    # 최대 연속 손실
    try:
        trade_pnls = pf.trades.records_readable["PnL"].values
        max_consec_loss = _max_consecutive_loss(trade_pnls)
    except Exception:
        max_consec_loss = 0
    # 회전율 (대략): 거래수 / (기간 일수)
    try:
        days = max(1, (eq.index[-1] - eq.index[0]).days)
        turnover = n_trades / (days / 365.25 + 1e-9)
    except Exception:
        turnover = 0.0
    return {
        "total_return":      round(total_ret, 4),
        "cagr":              round(cagr, 4),
        "sharpe":            round(sharpe, 3),
        "sortino":           round(sortino, 3),
        "max_drawdown":      round(max_dd, 4),
        "win_rate":          round(win_rate, 4),
        "n_trades":          n_trades,
        "max_consec_losses": int(max_consec_loss),
        "turnover_per_year": round(turnover, 2),
    }


def _monthly_returns(pf) -> Dict[str, float]:
    try:
        ret = pf.returns()
        m = (1 + ret).resample("ME").prod() - 1
        return {k.strftime("%Y-%m"): round(float(v), 4)
                for k, v in m.items() if np.isfinite(v)}
    except Exception:
        return {}


def _yearly_returns(pf) -> Dict[str, float]:
    try:
        ret = pf.returns()
        y = (1 + ret).resample("YE").prod() - 1
        return {str(k.year): round(float(v), 4)
                for k, v in y.items() if np.isfinite(v)}
    except Exception:
        return {}


def _max_consecutive_loss(pnls) -> int:
    cur = mx = 0
    for p in pnls:
        if p < 0:
            cur += 1
            mx = max(mx, cur)
        else:
            cur = 0
    return mx
