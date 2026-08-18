"""
portfolio_optimizer.py — 다종목 최적 비중 (Tier 2 #8)
============================================================
engine.portfolio의 Markowitz/HRP/Black-Litterman를 HUB UI에 노출.

입력: 종목 리스트
출력: 각 방법별 최적 비중 + 백테스트 비교 (vs Equal Weight)

방법:
  - MV (Markowitz Mean-Variance)
  - HRP (Hierarchical Risk Parity)
  - BL (Black-Litterman) — views 입력 받으면 활용
  - EW (Equal Weight) — baseline
"""
from __future__ import annotations

import time
import numpy as np
import pandas as pd
from typing import Any, Dict, List, Optional


_BG_PAPER = "#05070a"; _BG_PLOT = "#0a0f16"
_TXT = "#cfe6ec";      _TXT_DIM = "#5d7480"
_GRID = "#16202e";     _CYAN = "#3df0ff"
_UP = "#ff5a52";       _DOWN = "#4d9cff";  _AMBER = "#ffb44c"

_PALETTE = [_CYAN, _AMBER, _UP, _DOWN, "#a07dff", "#ffdd44",
            "#48d8ff", "#ff9550", "#7ec8ff", "#ff9fc8"]


def _safe(v, d=4):
    try:
        f = float(v)
        if np.isnan(f) or np.isinf(f):
            return None
        return round(f, d)
    except Exception:
        return None


def optimize_portfolio(tickers: List[str],
                        period_days: int = 365,
                        interval: str = "1d",
                        methods: Optional[List[str]] = None,
                        views: Optional[Dict[str, float]] = None,
                        init_cash: float = 10000.0
                        ) -> Dict[str, Any]:
    """다종목 최적 비중 계산 + 백테스트 비교.

    methods: ['mv','hrp','bl','ew'] (기본 모두)
    views (BL용): {ticker: expected_return}
    """
    t0 = time.time()
    if not tickers or len(tickers) < 2:
        return {"ok": False, "error": "최소 2 종목 필요"}
    if len(tickers) > 30:
        return {"ok": False, "error": "최대 30 종목 (속도 보호)"}
    methods = methods or ["mv", "hrp", "bl", "ew"]

    # 1) 종목별 가격 + 일별 수익률
    try:
        from .vbt_runner import _fetch_price
    except Exception as e:
        return {"ok": False, "error": f"의존성 부족: {e}"}

    rets_dict = {}
    failed = []
    for tk in tickers:
        try:
            p = _fetch_price(tk.upper(), period_days, interval=interval)
            if len(p) < 30:
                failed.append({"ticker": tk, "reason": "데이터 부족"})
                continue
            rets_dict[tk.upper()] = p.pct_change().fillna(0)
        except Exception as e:
            failed.append({"ticker": tk, "reason": str(e)[:80]})

    if len(rets_dict) < 2:
        return {"ok": False, "error": "유효 종목 < 2",
                "failed": failed}

    rets_df = pd.DataFrame(rets_dict).dropna()
    if len(rets_df) < 20:
        return {"ok": False, "error": "정렬 후 데이터 부족"}

    valid_tickers = list(rets_df.columns)
    n_assets = len(valid_tickers)

    # 2) 각 방법별 비중 계산
    weights_by_method: Dict[str, Dict[str, float]] = {}

    # 2-1) Equal Weight
    if "ew" in methods:
        weights_by_method["ew"] = {tk: 1.0/n_assets for tk in valid_tickers}

    # 2-2) Markowitz
    if "mv" in methods:
        try:
            from ..portfolio.markowitz import markowitz_optimize
            r = markowitz_optimize(rets_df)
            w_arr = r.get("weights") if isinstance(r, dict) else r
            if hasattr(w_arr, 'tolist'):
                w_arr = w_arr.tolist()
            if w_arr and len(w_arr) == n_assets:
                weights_by_method["mv"] = dict(zip(valid_tickers,
                                                    [float(w) for w in w_arr]))
            else:
                weights_by_method["mv"] = {"_error":
                    "markowitz_optimize 결과 비정상"}
        except Exception as e:
            weights_by_method["mv"] = {"_error": f"{type(e).__name__}: {e}"}

    # 2-3) HRP
    if "hrp" in methods:
        try:
            from ..portfolio.hrp import hrp as hrp_fn
            r = hrp_fn(rets_df)
            if isinstance(r, dict) and not any(k.startswith('_') for k in r):
                # 정상 dict {ticker: weight}
                weights_by_method["hrp"] = {tk: float(r.get(tk, 0))
                                              for tk in valid_tickers}
            else:
                weights_by_method["hrp"] = r if isinstance(r, dict) else {
                    "_error": "결과 비정상"}
        except Exception as e:
            weights_by_method["hrp"] = {"_error": f"{type(e).__name__}: {e}"}

    # 2-4) Black-Litterman (views 옵션)
    if "bl" in methods:
        try:
            from ..portfolio.black_litterman import black_litterman
            # views가 dict[ticker -> ret] 형태로 들어오면 변환
            bl_views = None
            if views and isinstance(views, dict):
                bl_views = {k.upper(): float(v) for k, v in views.items()
                             if k.upper() in valid_tickers}
            r = black_litterman(rets_df, views=bl_views) if bl_views else \
                black_litterman(rets_df)
            w_arr = r.get("weights") if isinstance(r, dict) else r
            if hasattr(w_arr, 'tolist'):
                w_arr = w_arr.tolist()
            if w_arr and len(w_arr) == n_assets:
                weights_by_method["bl"] = dict(zip(valid_tickers,
                                                    [float(w) for w in w_arr]))
            else:
                weights_by_method["bl"] = {"_error": "BL 결과 비정상"}
        except Exception as e:
            weights_by_method["bl"] = {"_error": f"{type(e).__name__}: {e}"}

    # 3) 각 방법별 백테스트 (가중 합산 일별 수익률)
    method_metrics: Dict[str, Dict[str, Any]] = {}
    method_equity: Dict[str, pd.Series] = {}
    annual_factor = 252
    for m_id, w_dict in weights_by_method.items():
        if "_error" in w_dict:
            method_metrics[m_id] = {"ok": False,
                                     "error": w_dict["_error"]}
            continue
        try:
            w_arr = np.array([w_dict.get(tk, 0) for tk in valid_tickers])
            # 정규화
            if w_arr.sum() > 0:
                w_arr = w_arr / w_arr.sum()
            port_ret = (rets_df.values @ w_arr)
            port_ser = pd.Series(port_ret, index=rets_df.index)
            eq = (1 + port_ser).cumprod() * init_cash
            mean_d = float(port_ser.mean())
            std_d = float(port_ser.std())
            sharpe = (mean_d / std_d * np.sqrt(annual_factor)
                       if std_d > 1e-9 else 0)
            total = float(eq.iloc[-1] / init_cash - 1)
            # MDD
            peak = eq.cummax()
            mdd = float(((eq - peak) / peak).min())
            method_metrics[m_id] = {
                "ok": True,
                "total_return": _safe(total),
                "sharpe":       _safe(sharpe, 3),
                "max_drawdown": _safe(mdd),
                "annual_vol":   _safe(std_d * np.sqrt(annual_factor)),
            }
            method_equity[m_id] = eq
        except Exception as e:
            method_metrics[m_id] = {"ok": False, "error": str(e)[:100]}

    # 4) Plotly: equity 비교 + weight 비교
    plotly_equity = _build_equity_compare(method_equity)
    plotly_weights = _build_weights_compare(weights_by_method, valid_tickers)
    # 5) 상관 heatmap (보너스)
    plotly_corr = _build_corr_heatmap(rets_df.corr())

    return {
        "ok": True,
        "n_tickers": n_assets,
        "n_failed":  len(failed),
        "failed":    failed,
        "valid_tickers": valid_tickers,
        "n_obs": int(len(rets_df)),
        "elapsed_sec": round(time.time() - t0, 2),
        "weights": weights_by_method,
        "metrics": method_metrics,
        "plotly_equity":  plotly_equity,
        "plotly_weights": plotly_weights,
        "plotly_corr":    plotly_corr,
        "method_labels": {
            "ew": "Equal Weight (1/N)",
            "mv": "Markowitz Mean-Variance",
            "hrp": "HRP (Hierarchical Risk Parity)",
            "bl": "Black-Litterman" + (" (views 적용)" if views else ""),
        },
    }


def _dt_list(idx):
    return [pd.Timestamp(x).strftime("%Y-%m-%dT%H:%M:%S") for x in idx]


def _build_equity_compare(method_equity):
    if not method_equity:
        return {"data": [], "layout": {}}
    traces = []
    for i, (m_id, eq) in enumerate(method_equity.items()):
        color = _PALETTE[i % len(_PALETTE)]
        if len(eq) > 200:
            eq = eq.iloc[::max(1, len(eq)//200)]
        traces.append({
            "type": "scatter", "x": _dt_list(eq.index),
            "y": [float(v) for v in eq.values], "mode": "lines",
            "line": {"color": color, "width": 1.6},
            "name": m_id.upper(),
            "hovertemplate": f"{m_id.upper()}<br>$%{{y:,.0f}}<extra></extra>",
        })
    return {
        "data": traces,
        "layout": {
            "title": {"text": "방법별 equity 비교 (같은 종목 풀)",
                       "font": {"color": _CYAN, "size": 12},
                       "x": 0.02, "xanchor": "left"},
            "paper_bgcolor": _BG_PAPER, "plot_bgcolor": _BG_PLOT,
            "font": {"color": _TXT, "size": 10,
                      "family": "JetBrains Mono, monospace"},
            "xaxis": {"gridcolor": _GRID, "color": _TXT_DIM},
            "yaxis": {"gridcolor": _GRID, "color": _TXT_DIM,
                       "title": {"text": "Equity ($)",
                                 "font": {"color": _TXT}}},
            "margin": {"l": 60, "r": 15, "t": 35, "b": 40},
            "height": 380,
            "hovermode": "x unified",
            "legend": {"bgcolor": "rgba(0,0,0,0)",
                        "font": {"color": _TXT, "size": 10},
                        "orientation": "h", "y": 1.0, "x": 1.0,
                        "xanchor": "right", "yanchor": "bottom"},
        },
    }


def _build_weights_compare(weights_by_method, tickers):
    """방법별 비중을 stacked bar로 비교."""
    if not weights_by_method:
        return {"data": [], "layout": {}}
    method_ids = [m for m, w in weights_by_method.items()
                   if "_error" not in w]
    if not method_ids or not tickers:
        return {"data": [], "layout": {}}
    traces = []
    for i, tk in enumerate(tickers):
        color = _PALETTE[i % len(_PALETTE)]
        ys = [float(weights_by_method[m].get(tk, 0)) * 100 for m in method_ids]
        traces.append({
            "type": "bar", "x": [m.upper() for m in method_ids],
            "y": ys, "name": tk,
            "marker": {"color": color, "line": {"color": _GRID, "width": 0.5}},
            "hovertemplate": f"{tk}<br>%{{x}}: %{{y:.1f}}%<extra></extra>",
        })
    return {
        "data": traces,
        "layout": {
            "title": {"text": "방법별 비중 (%)",
                       "font": {"color": _CYAN, "size": 12},
                       "x": 0.02, "xanchor": "left"},
            "paper_bgcolor": _BG_PAPER, "plot_bgcolor": _BG_PLOT,
            "font": {"color": _TXT, "size": 10,
                      "family": "JetBrains Mono, monospace"},
            "xaxis": {"color": _TXT_DIM},
            "yaxis": {"gridcolor": _GRID, "color": _TXT_DIM,
                       "title": {"text": "%", "font": {"color": _TXT}}},
            "margin": {"l": 50, "r": 15, "t": 35, "b": 40},
            "height": 320,
            "barmode": "stack",
            "legend": {"bgcolor": "rgba(0,0,0,0)",
                        "font": {"color": _TXT, "size": 9}},
        },
    }


def _build_corr_heatmap(corr_df):
    if corr_df.empty or len(corr_df) < 2:
        return {"data": [], "layout": {}}
    labels = list(corr_df.columns)
    z = corr_df.round(3).values.tolist()
    text = [[f"{v:+.2f}" for v in row] for row in z]
    return {
        "data": [{
            "type": "heatmap", "x": labels, "y": labels, "z": z,
            "text": text, "texttemplate": "%{text}",
            "textfont": {"size": 9, "color": "#fff"},
            "colorscale": [[0, _DOWN], [0.5, _GRID], [1.0, _UP]],
            "zmid": 0, "zmin": -1, "zmax": 1,
            "colorbar": {"tickfont": {"color": _TXT}, "thickness": 10},
            "hovertemplate": "%{y} vs %{x}<br>corr %{z:+.3f}<extra></extra>",
        }],
        "layout": {
            "title": {"text": "종목 간 일별 수익률 상관",
                       "font": {"color": _CYAN, "size": 12},
                       "x": 0.02, "xanchor": "left"},
            "paper_bgcolor": _BG_PAPER, "plot_bgcolor": _BG_PLOT,
            "font": {"color": _TXT, "size": 10},
            "xaxis": {"color": _TXT_DIM},
            "yaxis": {"color": _TXT_DIM},
            "margin": {"l": 60, "r": 15, "t": 35, "b": 40},
            "height": max(280, 30*len(labels)+80),
        },
    }
