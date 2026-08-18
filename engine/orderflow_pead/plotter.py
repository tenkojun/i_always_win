"""
시각화 — Plotly 기반 인터랙티브 차트 (P2)
============================================================================
모든 함수는 **Plotly figure dict** ({"data": [...], "layout": {...}}) 반환.
프론트엔드(`Plotly.newPlot(el, fig.data, fig.layout)`)에서 렌더.

이전 base64 PNG 방식을 완전히 대체 — zoom/pan/hover/toggle 모두 지원.
Plotly 없을 때(빌드 누락 등)는 빈 dict 반환 → 프론트에서 무시.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from typing import Any, Dict, List


# ── Plotly 의존성은 *런타임* 사용 안 함 (순수 dict 생성) ────────
# 즉 plotly가 설치 안 돼 있어도 dict는 만들어지고 프론트가 렌더한다.
# (단 dict 구조가 plotly.js와 호환되어야 함)

# ── 다크 테마 ────────────────────────────────────────────────
_BG_PAPER = "#05070a"
_BG_PLOT  = "#0a0f16"
_TXT      = "#cfe6ec"
_TXT_DIM  = "#5d7480"
_GRID     = "#16202e"
_CYAN     = "#3df0ff"
_UP       = "#ff5a52"    # 한국식: 상승=빨강
_DOWN     = "#4d9cff"    # 한국식: 하락=파랑


def _layout(title: str, height: int = 280, showlegend: bool = True) -> Dict[str, Any]:
    return {
        "title": {"text": title, "font": {"color": _CYAN, "size": 12},
                  "x": 0.02, "xanchor": "left"},
        "paper_bgcolor": _BG_PAPER,
        "plot_bgcolor":  _BG_PLOT,
        "font": {"color": _TXT, "size": 10, "family": "JetBrains Mono, monospace"},
        "xaxis": {"gridcolor": _GRID, "color": _TXT_DIM,
                  "showgrid": True, "zeroline": False},
        "yaxis": {"gridcolor": _GRID, "color": _TXT_DIM,
                  "showgrid": True, "zeroline": False},
        "margin": {"l": 50, "r": 15, "t": 30, "b": 30},
        "height": height,
        "showlegend": showlegend,
        "legend": {"bgcolor": "rgba(0,0,0,0)",
                   "font": {"color": _TXT, "size": 9},
                   "orientation": "h", "y": 1.0, "x": 1.0,
                   "xanchor": "right", "yanchor": "bottom"},
        "hovermode": "x unified",
        "hoverlabel": {"bgcolor": _BG_PLOT, "bordercolor": _CYAN,
                       "font": {"color": _TXT, "size": 10}},
    }


def _empty() -> Dict[str, Any]:
    """데이터 부족 시 빈 figure."""
    return {"data": [], "layout": _layout("", height=180, showlegend=False)}


def _dt_list(idx) -> List[str]:
    """DatetimeIndex → ISO 문자열 리스트 (plotly가 timezone-aware 자동 처리)."""
    try:
        return [pd.Timestamp(x).strftime("%Y-%m-%dT%H:%M:%S") for x in idx]
    except Exception:
        return list(idx)


# ── 1) Equity curve + drawdown 음영 ──────────────────────────
def plot_equity(equity: pd.Series, title: str = "Equity Curve") -> Dict[str, Any]:
    if equity is None or len(equity) < 2:
        return _empty()
    dates = _dt_list(equity.index)
    vals  = [float(v) for v in equity.values]
    peak  = list(np.maximum.accumulate(vals))
    data = [
        # peak line (투명, drawdown 채우기 baseline)
        {"type": "scatter", "x": dates, "y": peak, "mode": "lines",
         "line": {"width": 0}, "showlegend": False,
         "hoverinfo": "skip", "name": "Peak"},
        # equity (drawdown 영역을 peak까지 채움)
        {"type": "scatter", "x": dates, "y": vals, "mode": "lines",
         "line": {"color": _CYAN, "width": 1.6},
         "fill": "tonexty", "fillcolor": "rgba(77,156,255,0.10)",
         "name": "Equity",
         "hovertemplate": "%{x|%Y-%m-%d}<br>$%{y:,.2f}<extra></extra>"},
    ]
    return {"data": data, "layout": _layout(title)}


# ── 2) Signals on price (entry/exit 마커) ─────────────────────
def plot_signals(close: pd.Series,
                 entries: pd.Series,
                 exits: pd.Series,
                 title: str = "Signals") -> Dict[str, Any]:
    if close is None or len(close) < 2:
        return _empty()
    dates  = _dt_list(close.index)
    prices = [float(v) for v in close.values]
    en_mask = entries.fillna(False).astype(bool) if entries is not None else pd.Series(False, index=close.index)
    ex_mask = exits.fillna(False).astype(bool)   if exits   is not None else pd.Series(False, index=close.index)
    en_x = _dt_list(close.index[en_mask])
    en_y = [float(v) for v in close[en_mask].values]
    ex_x = _dt_list(close.index[ex_mask])
    ex_y = [float(v) for v in close[ex_mask].values]
    data = [
        {"type": "scatter", "x": dates, "y": prices, "mode": "lines",
         "line": {"color": _TXT, "width": 0.9}, "name": "Price",
         "hovertemplate": "%{x|%Y-%m-%d}<br>$%{y:,.2f}<extra></extra>"},
        {"type": "scatter", "x": en_x, "y": en_y, "mode": "markers",
         "marker": {"symbol": "triangle-up", "size": 10, "color": _UP,
                    "line": {"color": "#fff", "width": 0.5}},
         "name": f"Entry ({len(en_x)})",
         "hovertemplate": "BUY %{x|%Y-%m-%d}<br>$%{y:,.2f}<extra></extra>"},
        {"type": "scatter", "x": ex_x, "y": ex_y, "mode": "markers",
         "marker": {"symbol": "triangle-down", "size": 10, "color": _DOWN,
                    "line": {"color": "#fff", "width": 0.5}},
         "name": f"Exit ({len(ex_x)})",
         "hovertemplate": "SELL %{x|%Y-%m-%d}<br>$%{y:,.2f}<extra></extra>"},
    ]
    return {"data": data, "layout": _layout(title)}


# ── 3) Delta bubble plot ──────────────────────────────────────
def plot_delta_bubbles(close: pd.Series,
                       delta: pd.Series,
                       title: str = "Delta Bubbles") -> Dict[str, Any]:
    if close is None or len(close) < 2:
        return _empty()
    dates  = _dt_list(close.index)
    prices = [float(v) for v in close.values]
    d = delta.reindex(close.index).fillna(0)
    d_max = float(np.abs(d).max() or 1.0)
    sizes  = list(np.clip(np.abs(d.values) / d_max * 22, 2, 22))
    colors = [_UP if v > 0 else _DOWN for v in d.values]
    delta_vals = [float(v) for v in d.values]
    data = [
        {"type": "scatter", "x": dates, "y": prices, "mode": "lines",
         "line": {"color": _TXT_DIM, "width": 0.7}, "name": "Price",
         "showlegend": False,
         "hovertemplate": "%{x|%Y-%m-%d}<br>$%{y:,.2f}<extra></extra>"},
        {"type": "scatter", "x": dates, "y": prices, "mode": "markers",
         "marker": {"size": sizes, "color": colors, "opacity": 0.55,
                    "line": {"width": 0}},
         "customdata": delta_vals, "name": "Volume Δ",
         "hovertemplate": "%{x|%Y-%m-%d}<br>$%{y:,.2f}<br>Δ %{customdata:+,.0f}<extra></extra>"},
    ]
    return {"data": data, "layout": _layout(title)}


# ── 4) PEAD events + drift window (vrect) ─────────────────────
def plot_pead_events(close: pd.Series,
                     events: pd.DataFrame,
                     drift_days: int = 30,
                     title: str = "PEAD Events") -> Dict[str, Any]:
    if close is None or events is None or len(close) < 2 or events.empty:
        return _empty()
    dates  = _dt_list(close.index)
    prices = [float(v) for v in close.values]
    shapes: List[Dict[str, Any]] = []
    ann: List[Dict[str, Any]] = []
    for _, ev in events.iterrows():
        d0 = pd.Timestamp(ev["event_date"])
        d1 = d0 + pd.Timedelta(days=drift_days)
        sue = float(ev.get("sue", 0) or 0)
        col = _UP if sue > 0 else _DOWN
        rgba = f"rgba(255,90,82,0.10)" if sue > 0 else f"rgba(77,156,255,0.10)"
        shapes.append({
            "type": "rect", "xref": "x", "yref": "paper",
            "x0": d0.strftime("%Y-%m-%d"),
            "x1": d1.strftime("%Y-%m-%d"),
            "y0": 0, "y1": 1,
            "fillcolor": rgba, "line": {"width": 0}, "layer": "below",
        })
        shapes.append({
            "type": "line", "xref": "x", "yref": "paper",
            "x0": d0.strftime("%Y-%m-%d"),
            "x1": d0.strftime("%Y-%m-%d"),
            "y0": 0, "y1": 1,
            "line": {"color": col, "width": 0.8, "dash": "dot"},
        })
    layout = _layout(title)
    layout["shapes"] = shapes
    data = [
        {"type": "scatter", "x": dates, "y": prices, "mode": "lines",
         "line": {"color": _TXT, "width": 0.9}, "name": "Price",
         "hovertemplate": "%{x|%Y-%m-%d}<br>$%{y:,.2f}<extra></extra>"},
    ]
    return {"data": data, "layout": layout}


# ── 5) Parameter heatmap/surface (2 params → heatmap, n params → 2D scatter) ──
def plot_param_surface(results: List[Dict[str, Any]],
                       x_key: str, y_key: str,
                       z_key: str = "sharpe",
                       title: str = "Param Surface") -> Dict[str, Any]:
    if not results:
        return _empty()
    pts = [r for r in results
           if isinstance(r, dict) and "params" in r and "error" not in r
           and r["params"].get(x_key) is not None
           and r["params"].get(y_key) is not None]
    if not pts:
        return _empty()
    xs = [r["params"][x_key]    for r in pts]
    ys = [r["params"][y_key]    for r in pts]
    zs = [float(r.get(z_key, 0) or 0) for r in pts]

    # 고유 x/y 추출 — 정렬된 grid가 되면 heatmap, 아니면 scatter
    xu = sorted(set(xs)); yu = sorted(set(ys))
    use_heatmap = len(xs) == len(xu) * len(yu)
    layout = _layout(title, height=340)
    layout["xaxis"]["title"] = {"text": x_key, "font": {"color": _TXT}}
    layout["yaxis"]["title"] = {"text": y_key, "font": {"color": _TXT}}
    if use_heatmap:
        # z matrix: row=y, col=x
        z_mat = [[None] * len(xu) for _ in yu]
        for x, y, z in zip(xs, ys, zs):
            i = yu.index(y); j = xu.index(x)
            z_mat[i][j] = z
        data = [{
            "type": "heatmap", "x": xu, "y": yu, "z": z_mat,
            "colorscale": "Viridis",
            "colorbar": {"tickfont": {"color": _TXT}, "title": {"text": z_key, "font": {"color": _TXT}}},
            "hovertemplate": f"{x_key}=%{{x}}<br>{y_key}=%{{y}}<br>{z_key}=%{{z:.3f}}<extra></extra>",
        }]
    else:
        data = [{
            "type": "scatter", "x": xs, "y": ys, "mode": "markers",
            "marker": {
                "size": 12, "color": zs, "colorscale": "Viridis",
                "showscale": True,
                "colorbar": {"tickfont": {"color": _TXT}, "title": {"text": z_key, "font": {"color": _TXT}}},
                "line": {"color": _GRID, "width": 0.5},
            },
            "customdata": zs,
            "hovertemplate": f"{x_key}=%{{x}}<br>{y_key}=%{{y}}<br>{z_key}=%{{customdata:.3f}}<extra></extra>",
        }]
    return {"data": data, "layout": layout}
