"""
multi_strategy.py — 다중 전략 포트폴리오 합성
============================================================
컬렉션의 N개 전략을 가중치 비중으로 합성한 통합 포트폴리오 분석.

기관 운용의 핵심: 단일 전략이 아닌 multi-strategy alpha sleeve.
50개 전략을 각각 2%씩 운용 → 상관 분산으로 위험 ↓ Sharpe ↑

주요 함수:
  - combine_strategies(ticker, items, weights, ...) : 가중 합성 + 메트릭
  - 반환: 합성 equity / 메트릭 / 전략간 상관 / Plotly overlay
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd


# Plotly 다크 헬퍼
_BG_PAPER = "#05070a"; _BG_PLOT = "#0a0f16"
_TXT = "#cfe6ec";      _TXT_DIM = "#5d7480"
_GRID = "#16202e";     _CYAN = "#3df0ff"
_UP = "#ff5a52";       _DOWN = "#4d9cff";  _AMBER = "#ffb44c"

_PALETTE = [_CYAN, _AMBER, _UP, _DOWN, "#a07dff", "#ffdd44",
            "#48d8ff", "#ff9550", "#7ec8ff", "#ff9fc8"]


def _layout(title: str, height: int = 380) -> Dict[str, Any]:
    return {
        "title": {"text": title, "font": {"color": _CYAN, "size": 12},
                  "x": 0.02, "xanchor": "left"},
        "paper_bgcolor": _BG_PAPER, "plot_bgcolor": _BG_PLOT,
        "font": {"color": _TXT, "size": 10,
                 "family": "JetBrains Mono, monospace"},
        "xaxis": {"gridcolor": _GRID, "color": _TXT_DIM},
        "yaxis": {"gridcolor": _GRID, "color": _TXT_DIM},
        "margin": {"l": 60, "r": 15, "t": 35, "b": 40},
        "height": height,
        "hovermode": "x unified",
        "legend": {"bgcolor": "rgba(0,0,0,0)",
                   "font": {"color": _TXT, "size": 9},
                   "orientation": "h", "y": 1.0, "x": 1.0,
                   "xanchor": "right", "yanchor": "bottom"},
    }


def _safe(v, d=4):
    try:
        f = float(v)
        if np.isnan(f) or np.isinf(f):
            return None
        return round(f, d)
    except Exception:
        return None


# ════════════════════════════════════════════════════════════
#  핵심: 다중 전략 합성
# ════════════════════════════════════════════════════════════
def combine_strategies(ticker: str,
                       items: List[Dict[str, Any]],
                       period_days: int = 730,
                       interval: str = "1d",
                       init_cash: float = 10000.0,
                       fees: float = 0.001,
                       normalize_weights: bool = True
                       ) -> Dict[str, Any]:
    """N개 전략을 가중치 비중으로 합성.

    items: [{strategy: 'sma_cross', params: {...}, weight: 1.0, label?: 'name'}, ...]
    weights는 비율 자동 정규화 (Σ=1) 또는 그대로 사용.
    """
    t0 = time.time()
    if not items or not isinstance(items, list):
        return {"ok": False, "error": "items 비어 있음"}
    if len(items) > 20:
        return {"ok": False, "error": "최대 20 전략 (속도 보호)"}

    from .vbt_runner import run_backtest

    # 1) 각 전략 백테스트
    rets_dict: Dict[str, pd.Series] = {}
    eq_dict: Dict[str, pd.Series] = {}
    metrics_dict: Dict[str, Dict[str, Any]] = {}
    labels: List[str] = []
    weights_raw: List[float] = []

    for i, it in enumerate(items):
        strat = it.get("strategy")
        params = it.get("params") or {}
        w = float(it.get("weight") or 1.0)
        label = it.get("label") or f"{strat}_{i+1}"
        if not strat:
            continue
        try:
            r = run_backtest(
                ticker=ticker, strategy=strat, params=params,
                period_days=period_days, interval=interval,
                fees=fees, init_cash=init_cash,
            )
            if not r.get("ok"):
                metrics_dict[label] = {
                    "ok": False, "error": r.get("error", "백테스트 실패"),
                    "weight": w}
                continue
            eq_curve = r.get("equity_curve") or []
            if len(eq_curve) < 2:
                metrics_dict[label] = {
                    "ok": False, "error": "equity 부족", "weight": w}
                continue
            idx = [pd.Timestamp(p["d"]) for p in eq_curve]
            vals = [float(p["v"]) for p in eq_curve]
            eq_ser = pd.Series(vals, index=idx)
            ret_ser = eq_ser.pct_change().fillna(0)
            eq_dict[label] = eq_ser
            rets_dict[label] = ret_ser
            metrics_dict[label] = {
                **(r.get("metrics") or {}),
                "ok": True, "weight": w, "strategy": strat,
            }
            labels.append(label)
            weights_raw.append(w)
        except Exception as e:
            metrics_dict[label] = {"ok": False,
                                    "error": f"{type(e).__name__}: {e}",
                                    "weight": w}

    if not rets_dict:
        return {"ok": False, "error": "유효 전략 0개 — 모두 실패"}

    # 2) 가중치 정규화 (옵션)
    weights_arr = np.array(weights_raw, dtype=float)
    if normalize_weights and weights_arr.sum() > 0:
        weights_norm = weights_arr / weights_arr.sum()
    else:
        weights_norm = weights_arr

    # 3) 일별 returns DataFrame (전략 결과 인덱스 합집합)
    rets_df = pd.DataFrame(rets_dict).fillna(0)
    # 4) 가중 합산: 합성 일별 수익률
    weighted = rets_df.values @ weights_norm
    combined_ret = pd.Series(weighted, index=rets_df.index)
    # 5) 합성 equity (cumulative)
    combined_eq = (1 + combined_ret).cumprod() * init_cash

    # 6) 합성 메트릭
    annualization_factor = {
        "1d": 252, "1h": 252*6.5, "30m": 252*13,
        "15m": 252*26, "5m": 252*78, "1m": 252*390,
    }.get(interval, 252)
    daily_mean = combined_ret.mean()
    daily_std = combined_ret.std()
    sharpe = (daily_mean / daily_std * np.sqrt(annualization_factor)
              if daily_std > 1e-9 else 0)
    downside = combined_ret[combined_ret < 0]
    sortino = (daily_mean / downside.std() * np.sqrt(annualization_factor)
                if len(downside) > 0 and downside.std() > 1e-9 else 0)
    total_ret = float(combined_eq.iloc[-1] / init_cash - 1)
    # MDD
    peak = combined_eq.cummax()
    dd_series = (combined_eq - peak) / peak
    mdd = float(dd_series.min())

    # Buy&Hold 비교
    try:
        from .vbt_runner import _fetch_price
        bh_price = _fetch_price(ticker, period_days=period_days,
                                  interval=interval)
        bh_ret = float(bh_price.iloc[-1] / bh_price.iloc[0] - 1)
    except Exception:
        bh_ret = None

    # 7) 전략 간 상관행렬 (분산 효과 측정)
    corr_df = rets_df.corr().round(3) if len(rets_df.columns) > 1 else pd.DataFrame()
    avg_corr = (float(corr_df.where(~np.eye(len(corr_df), dtype=bool)).stack().mean())
                if len(corr_df) > 1 else None)
    diversification_ratio = None
    if avg_corr is not None:
        # 1에 가까우면 분산 효과 적음 / 0 이하면 헷지 효과
        diversification_ratio = round(1 - max(0, avg_corr), 3)

    # 8) Plotly 차트들
    plotly_equity = _build_equity_overlay_chart(
        combined_eq, eq_dict, weights_norm, labels, ticker)
    plotly_corr = _build_corr_heatmap(corr_df)
    plotly_weights = _build_weights_pie(labels, weights_norm)
    plotly_dd = _build_drawdown_chart(combined_eq, dd_series)

    # 9) 합성 equity 다운샘플 (응답 가볍게)
    eq_out = combined_eq
    if len(eq_out) > 250:
        eq_out = eq_out.iloc[::max(1, len(eq_out)//250)]
    equity_curve = [{"d": d.strftime("%Y-%m-%d"), "v": float(v)}
                     for d, v in eq_out.items()]

    return {
        "ok": True,
        "ticker": ticker,
        "period_days": period_days,
        "interval": interval,
        "elapsed_sec": round(time.time() - t0, 2),
        "n_strategies": len(labels),
        "n_failed":     len(items) - len(labels),
        "weights": {l: float(w) for l, w in zip(labels, weights_norm)},
        "metrics_combined": {
            "total_return":     _safe(total_ret),
            "buy_hold_return":  _safe(bh_ret),
            "alpha":            _safe(total_ret - bh_ret) if bh_ret is not None else None,
            "sharpe":           _safe(sharpe, 3),
            "sortino":          _safe(sortino, 3),
            "max_drawdown":     _safe(mdd),
            "avg_correlation":  _safe(avg_corr, 3),
            "diversification":  diversification_ratio,
            "n_bars":           int(len(combined_ret)),
        },
        "per_strategy": metrics_dict,
        "equity_curve": equity_curve,
        "plotly_equity":   plotly_equity,
        "plotly_corr":     plotly_corr,
        "plotly_weights":  plotly_weights,
        "plotly_drawdown": plotly_dd,
    }


# ════════════════════════════════════════════════════════════
#  Plotly 차트 헬퍼
# ════════════════════════════════════════════════════════════
def _dt_list(idx):
    return [pd.Timestamp(x).strftime("%Y-%m-%dT%H:%M:%S") for x in idx]


def _build_equity_overlay_chart(combined_eq, eq_dict, weights, labels, ticker):
    traces = []
    # 1) 개별 전략 (얇은 라인)
    for i, (label, eq) in enumerate(eq_dict.items()):
        color = _PALETTE[i % len(_PALETTE)]
        # 다운샘플
        if len(eq) > 200:
            eq = eq.iloc[::max(1, len(eq)//200)]
        w_pct = float(weights[i]) * 100 if i < len(weights) else 0
        traces.append({
            "type": "scatter", "x": _dt_list(eq.index),
            "y": [float(v) for v in eq.values], "mode": "lines",
            "line": {"color": color, "width": 0.9, "dash": "dot"},
            "name": f"{label} ({w_pct:.0f}%)",
            "opacity": 0.6,
            "hovertemplate": f"{label}<br>$%{{y:,.0f}}<extra></extra>",
        })
    # 2) 합성 (굵은 시안 라인)
    ce = combined_eq
    if len(ce) > 250:
        ce = ce.iloc[::max(1, len(ce)//250)]
    traces.append({
        "type": "scatter", "x": _dt_list(ce.index),
        "y": [float(v) for v in ce.values], "mode": "lines",
        "line": {"color": "#fff", "width": 2.4},
        "name": "🎯 합성 포트폴리오",
        "hovertemplate": "합성<br>$%{y:,.0f}<extra></extra>",
    })
    layout = _layout(f"{ticker} · 다중 전략 합성 + 개별 비교", height=460)
    layout["yaxis"]["title"] = {"text": "Equity ($)",
                                  "font": {"color": _TXT}}
    return {"data": traces, "layout": layout}


def _build_corr_heatmap(corr_df):
    if corr_df.empty or len(corr_df) < 2:
        return {"data": [], "layout": _layout("", height=200)}
    labels = list(corr_df.columns)
    z = corr_df.values.tolist()
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
        "layout": _layout("전략 간 일별 수익률 상관 (낮을수록 분산 효과 ↑)",
                           height=max(280, 35*len(labels)+80)),
    }


def _build_weights_pie(labels, weights):
    if not labels:
        return {"data": [], "layout": _layout("", height=200)}
    values = [float(w)*100 for w in weights]
    colors = [_PALETTE[i % len(_PALETTE)] for i in range(len(labels))]
    return {
        "data": [{
            "type": "pie", "labels": labels, "values": values,
            "marker": {"colors": colors, "line": {"color": _BG_PAPER, "width": 2}},
            "textinfo": "label+percent", "textfont": {"size": 11, "color": _TXT},
            "hovertemplate": "%{label}<br>%{value:.1f}%<extra></extra>",
            "hole": 0.4,
        }],
        "layout": {
            "title": {"text": "비중 (정규화)",
                       "font": {"color": _CYAN, "size": 12},
                       "x": 0.02, "xanchor": "left"},
            "paper_bgcolor": _BG_PAPER, "plot_bgcolor": _BG_PLOT,
            "font": {"color": _TXT, "size": 10},
            "margin": {"l": 15, "r": 15, "t": 35, "b": 15},
            "height": 320,
            "showlegend": False,
        },
    }


def _build_drawdown_chart(combined_eq, dd_series):
    if len(dd_series) < 2:
        return {"data": [], "layout": _layout("", height=200)}
    ds = dd_series
    if len(ds) > 250:
        ds = ds.iloc[::max(1, len(ds)//250)]
    return {
        "data": [{
            "type": "scatter", "x": _dt_list(ds.index),
            "y": [float(v)*100 for v in ds.values],
            "mode": "lines", "fill": "tozeroy",
            "fillcolor": "rgba(77,156,255,0.20)",
            "line": {"color": _DOWN, "width": 1.2},
            "name": "Drawdown %",
            "hovertemplate": "%{x|%Y-%m-%d}<br>%{y:.2f}%<extra></extra>",
        }],
        "layout": _layout("합성 포트폴리오 Drawdown", height=240),
    }
