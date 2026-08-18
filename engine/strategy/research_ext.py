"""
research_ext.py — P6 리서치 확장
====================================================
- strategy_correlation_matrix : 여러 전략의 daily returns 간 상관계수 (Plotly heatmap)
- ticker_correlation_matrix   : 여러 종목의 가격 returns 상관 (Plotly heatmap)
- param_sensitivity            : 단일 전략의 파라미터 ±N% 변화 vs Sharpe (sensitivity plot)
- feature_importance_shap      : ML 모델 + SHAP feature importance (가벼운 RF)
"""
from __future__ import annotations

import math
import time
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd


# ── Plotly 헬퍼 (P2와 동일 톤) ────────────────────────────────
_BG_PAPER = "#05070a"; _BG_PLOT = "#0a0f16"
_TXT = "#cfe6ec";      _TXT_DIM = "#5d7480"
_GRID = "#16202e";     _CYAN = "#3df0ff"
_UP = "#ff5a52";       _DOWN = "#4d9cff"


def _layout(title: str, height: int = 360) -> Dict[str, Any]:
    return {
        "title": {"text": title, "font": {"color": _CYAN, "size": 12},
                  "x": 0.02, "xanchor": "left"},
        "paper_bgcolor": _BG_PAPER, "plot_bgcolor": _BG_PLOT,
        "font": {"color": _TXT, "size": 10,
                 "family": "JetBrains Mono, monospace"},
        "xaxis": {"gridcolor": _GRID, "color": _TXT_DIM},
        "yaxis": {"gridcolor": _GRID, "color": _TXT_DIM},
        "margin": {"l": 60, "r": 15, "t": 35, "b": 50},
        "height": height,
    }


def _empty() -> Dict[str, Any]:
    return {"data": [], "layout": _layout("", height=180)}


# ════════════════════════════════════════════════════════════
#  1) 전략 간 상관행렬 — 같은 종목에 N전략의 daily returns
# ════════════════════════════════════════════════════════════
def strategy_correlation_matrix(ticker: str,
                                 strategies: List[str],
                                 period_days: int = 730,
                                 interval: str = "1d"
                                 ) -> Dict[str, Any]:
    """ticker에 strategies 각각의 daily returns 계산 → 상관행렬 + Plotly heatmap."""
    from .vbt_runner import run_backtest
    rets_dict: Dict[str, pd.Series] = {}
    for s in strategies:
        try:
            r = run_backtest(ticker, s, period_days=period_days,
                              interval=interval)
            if not r.get("ok"):
                continue
            eq = r.get("equity_curve") or []
            if len(eq) < 2:
                continue
            # equity_curve → daily returns
            idx = [pd.Timestamp(p["d"]) for p in eq]
            vals = [float(p["v"]) for p in eq]
            ser = pd.Series(vals, index=idx)
            ret = ser.pct_change().fillna(0)
            rets_dict[s] = ret
        except Exception:
            continue
    if len(rets_dict) < 2:
        return {"ok": False, "error": "유효 전략 < 2 — 상관 계산 불가",
                "plotly": _empty()}
    df = pd.DataFrame(rets_dict).dropna(how="all").fillna(0)
    corr = df.corr().round(3)
    labels = list(corr.columns)
    z = corr.values.tolist()
    text = [[f"{v:+.2f}" for v in row] for row in z]
    plotly = {
        "data": [{
            "type": "heatmap", "x": labels, "y": labels, "z": z,
            "text": text, "texttemplate": "%{text}",
            "textfont": {"size": 9, "color": "#fff"},
            "colorscale": [[0, _DOWN], [0.5, _GRID], [1.0, _UP]],
            "zmid": 0, "zmin": -1, "zmax": 1,
            "colorbar": {"tickfont": {"color": _TXT}, "thickness": 10},
            "hovertemplate": "%{y} vs %{x}<br>corr %{z:+.3f}<extra></extra>",
        }],
        "layout": _layout(f"{ticker} · 전략 간 수익률 상관", height=420),
    }
    return {"ok": True, "ticker": ticker, "n_strategies": len(rets_dict),
            "corr": corr.to_dict(), "plotly": plotly}


# ════════════════════════════════════════════════════════════
#  2) 종목 간 상관행렬 — 종가 returns
# ════════════════════════════════════════════════════════════
def ticker_correlation_matrix(tickers: List[str],
                               period_days: int = 365,
                               interval: str = "1d"
                               ) -> Dict[str, Any]:
    from .vbt_runner import _fetch_price
    rets: Dict[str, pd.Series] = {}
    for tk in tickers[:15]:  # 안전
        try:
            p = _fetch_price(tk, period_days=period_days, interval=interval)
            if len(p) < 20:
                continue
            rets[tk] = p.pct_change().fillna(0)
        except Exception:
            continue
    if len(rets) < 2:
        return {"ok": False, "error": "유효 종목 < 2", "plotly": _empty()}
    df = pd.DataFrame(rets).dropna(how="all").fillna(0)
    corr = df.corr().round(3)
    labels = list(corr.columns)
    z = corr.values.tolist()
    text = [[f"{v:+.2f}" for v in row] for row in z]
    plotly = {
        "data": [{
            "type": "heatmap", "x": labels, "y": labels, "z": z,
            "text": text, "texttemplate": "%{text}",
            "textfont": {"size": 9, "color": "#fff"},
            "colorscale": [[0, _DOWN], [0.5, _GRID], [1.0, _UP]],
            "zmid": 0, "zmin": -1, "zmax": 1,
            "colorbar": {"tickfont": {"color": _TXT}, "thickness": 10},
            "hovertemplate": "%{y} vs %{x}<br>corr %{z:+.3f}<extra></extra>",
        }],
        "layout": _layout(f"종목 간 수익률 상관 ({len(rets)}종목)", height=400),
    }
    return {"ok": True, "n_tickers": len(rets),
            "corr": corr.to_dict(), "plotly": plotly}


# ════════════════════════════════════════════════════════════
#  3) Parameter Sensitivity — 단일 전략의 단일 파라미터 ±N% 변화 vs Sharpe
# ════════════════════════════════════════════════════════════
def param_sensitivity(ticker: str, strategy: str,
                      base_params: Dict[str, Any],
                      param_name: str,
                      pct_range: float = 0.5,   # ±50%
                      n_points: int = 11,
                      period_days: int = 730,
                      interval: str = "1d"
                      ) -> Dict[str, Any]:
    """기준 파라미터 base_params 중 1개를 ±pct_range 범위로 흔들면서
    Sharpe / total_return 변화 측정. Plotly line 차트 반환."""
    from .vbt_runner import run_backtest, AVAILABLE_STRATEGIES
    if strategy not in AVAILABLE_STRATEGIES:
        return {"ok": False, "error": f"unknown strategy {strategy}"}
    meta = AVAILABLE_STRATEGIES[strategy]
    pmeta = next((p for p in meta["params"] if p["name"] == param_name), None)
    if pmeta is None:
        return {"ok": False,
                "error": f"param '{param_name}' not in {strategy}"}
    base_val = base_params.get(param_name, pmeta.get("default"))
    if base_val is None:
        return {"ok": False, "error": "no base value"}
    # int/float 자동 감지
    is_int = pmeta.get("type") == "int" or isinstance(base_val, int)
    pmin = pmeta.get("min", base_val * (1 - pct_range))
    pmax = pmeta.get("max", base_val * (1 + pct_range))
    # ±pct_range 범위로 n_points 균등
    lo = max(pmin, base_val * (1 - pct_range))
    hi = min(pmax, base_val * (1 + pct_range))
    if lo >= hi:
        return {"ok": False,
                "error": f"range collapsed: lo={lo} hi={hi}"}
    points = np.linspace(lo, hi, n_points)
    if is_int:
        points = sorted(set(int(round(p)) for p in points))
    results = []
    for v in points:
        cfg = dict(base_params)
        cfg[param_name] = v
        try:
            r = run_backtest(ticker, strategy, params=cfg,
                              period_days=period_days, interval=interval)
            if r.get("ok"):
                m = r["metrics"]
                results.append({
                    "value": float(v),
                    "sharpe": float(m.get("sharpe") or 0),
                    "total_return": float(m.get("total_return") or 0),
                    "n_trades": int(m.get("n_trades") or 0),
                })
            else:
                results.append({"value": float(v), "sharpe": 0,
                                 "total_return": 0, "n_trades": 0,
                                 "err": r.get("error", "")[:60]})
        except Exception as e:
            results.append({"value": float(v), "sharpe": 0,
                             "total_return": 0, "n_trades": 0,
                             "err": str(e)[:60]})
    if not results:
        return {"ok": False, "error": "no results"}
    xs = [r["value"] for r in results]
    sh = [r["sharpe"] for r in results]
    tr = [r["total_return"] * 100 for r in results]
    nt = [r["n_trades"] for r in results]
    plotly = {
        "data": [
            {"type": "scatter", "x": xs, "y": sh, "mode": "lines+markers",
             "name": "Sharpe", "line": {"color": _CYAN, "width": 2},
             "marker": {"size": 7, "color": _CYAN},
             "yaxis": "y",
             "hovertemplate": f"{param_name}=%{{x}}<br>Sharpe %{{y:.2f}}<extra></extra>"},
            {"type": "scatter", "x": xs, "y": tr, "mode": "lines+markers",
             "name": "Total Return %", "line": {"color": _UP, "width": 2, "dash": "dot"},
             "marker": {"size": 6, "color": _UP},
             "yaxis": "y2",
             "hovertemplate": f"{param_name}=%{{x}}<br>Return %{{y:+.1f}}%<extra></extra>"},
            {"type": "bar", "x": xs, "y": nt, "name": "Trades",
             "marker": {"color": _TXT_DIM, "opacity": 0.3}, "yaxis": "y3",
             "hovertemplate": f"{param_name}=%{{x}}<br>%{{y}} trades<extra></extra>"},
        ],
        "layout": {
            **_layout(f"{ticker} · {strategy} · {param_name} sensitivity (base={base_val})",
                      height=420),
            "xaxis": {"gridcolor": _GRID, "color": _TXT_DIM,
                       "title": {"text": param_name, "font": {"color": _TXT}}},
            "yaxis": {"gridcolor": _GRID, "color": _CYAN,
                       "title": {"text": "Sharpe", "font": {"color": _CYAN}}},
            "yaxis2": {"overlaying": "y", "side": "right", "color": _UP,
                        "title": {"text": "Return %", "font": {"color": _UP}},
                        "showgrid": False},
            "yaxis3": {"overlaying": "y", "side": "right", "position": 1.0,
                        "showgrid": False, "showticklabels": False,
                        "range": [0, max(nt) * 4 if nt else 1]},
            "legend": {"orientation": "h", "y": 1.05, "x": 1, "xanchor": "right",
                        "font": {"color": _TXT, "size": 9}, "bgcolor": "rgba(0,0,0,0)"},
        },
    }
    return {
        "ok": True, "ticker": ticker, "strategy": strategy,
        "param_name": param_name, "base_value": float(base_val),
        "results": results, "plotly": plotly,
    }


# ════════════════════════════════════════════════════════════
#  4) ML feature importance + SHAP
# ════════════════════════════════════════════════════════════
def feature_importance_shap(ticker: str,
                             period_days: int = 730,
                             interval: str = "1d",
                             horizon: int = 5,
                             ) -> Dict[str, Any]:
    """가격 + 기본 인디케이터를 feature로 N봉 후 수익률을 target으로
    RandomForest 학습 → SHAP feature importance + Plotly bar."""
    try:
        from .vbt_runner import _fetch_ohlcv
        from sklearn.ensemble import RandomForestRegressor
    except Exception as e:
        return {"ok": False, "error": f"의존성 부족: {e}"}
    try:
        df = _fetch_ohlcv(ticker, period_days=period_days, interval=interval)
        if len(df) < 100:
            return {"ok": False, "error": f"데이터 부족 (n={len(df)} < 100)"}
        close = df["close"].astype(float)
        # ── Feature engineering ──
        feats = pd.DataFrame(index=close.index)
        feats["ret_1"]   = close.pct_change(1)
        feats["ret_5"]   = close.pct_change(5)
        feats["ret_20"]  = close.pct_change(20)
        feats["sma_ratio_20_50"] = (
            close.rolling(20).mean() / close.rolling(50).mean() - 1)
        feats["vol_20"]  = close.pct_change().rolling(20).std()
        feats["rsi_14"]  = _quick_rsi(close, 14)
        if "volume" in df:
            feats["vol_chg_5"] = df["volume"].pct_change(5)
            feats["volume_ratio"] = df["volume"] / df["volume"].rolling(20).mean()
        feats["hl_range"] = (df["high"] - df["low"]) / close
        feats["close_loc"] = (close - df["low"].rolling(20).min()) / (
            df["high"].rolling(20).max() - df["low"].rolling(20).min() + 1e-9)
        # ── Target: horizon봉 후 수익률 ──
        target = close.shift(-horizon) / close - 1
        data = pd.concat([feats, target.rename("target")], axis=1).dropna()
        if len(data) < 50:
            return {"ok": False, "error": f"clean 데이터 부족 (n={len(data)})"}
        X = data.drop(columns=["target"]).values
        y = data["target"].values
        feat_names = list(data.drop(columns=["target"]).columns)
        # ── 모델 ──
        rf = RandomForestRegressor(n_estimators=80, max_depth=6,
                                    n_jobs=-1, random_state=42)
        rf.fit(X, y)
        # train R² (sanity check)
        train_r2 = float(rf.score(X, y))
        # ── importance ──
        importances = rf.feature_importances_
        order = np.argsort(importances)[::-1]
        labels_sorted = [feat_names[i] for i in order]
        vals_sorted = [float(importances[i]) for i in order]
        # ── SHAP (optional) ──
        shap_vals = None
        shap_msg = ""
        try:
            import shap
            # TreeExplainer가 빠름
            explainer = shap.TreeExplainer(rf)
            # 100개 샘플만 (속도)
            sample = X[-min(100, len(X)):]
            sv = explainer.shap_values(sample)
            # mean abs SHAP per feature
            mean_abs = np.abs(sv).mean(axis=0)
            shap_vals = [{"feature": feat_names[i],
                          "mean_abs_shap": float(mean_abs[i])}
                         for i in np.argsort(mean_abs)[::-1]]
            shap_msg = f"SHAP TreeExplainer ({len(sample)} 샘플)"
        except Exception as _e:
            shap_msg = f"shap 미설치 — RF importance만 표시 ({_e})"
        # ── Plotly 바 차트 ──
        plotly = {
            "data": [{
                "type": "bar", "orientation": "h",
                "x": vals_sorted[::-1], "y": labels_sorted[::-1],
                "marker": {"color": _CYAN, "opacity": 0.85,
                            "line": {"color": _GRID, "width": 0.5}},
                "hovertemplate": "%{y}<br>importance %{x:.4f}<extra></extra>",
            }],
            "layout": {
                **_layout(f"{ticker} · RF feature importance "
                          f"(horizon={horizon}봉)", height=max(280, 30*len(feat_names)+60)),
                "xaxis": {"gridcolor": _GRID, "color": _TXT_DIM,
                           "title": {"text": "importance",
                                     "font": {"color": _TXT}}},
                "yaxis": {"color": _TXT, "automargin": True},
                "margin": {"l": 120, "r": 15, "t": 35, "b": 50},
            },
        }
        return {
            "ok": True, "ticker": ticker, "horizon_bars": horizon,
            "n_samples": int(len(data)), "n_features": len(feat_names),
            "train_r2": round(train_r2, 4),
            "importance": [{"feature": l, "importance": v}
                           for l, v in zip(labels_sorted, vals_sorted)],
            "shap_top": shap_vals[:10] if shap_vals else None,
            "shap_note": shap_msg,
            "plotly": plotly,
        }
    except Exception as e:
        import traceback
        return {"ok": False, "error": f"{type(e).__name__}: {e}",
                "trace": traceback.format_exc()[-400:]}


def _quick_rsi(s: pd.Series, n: int = 14) -> pd.Series:
    delta = s.diff()
    gain = delta.clip(lower=0).rolling(n).mean()
    loss = (-delta.clip(upper=0)).rolling(n).mean()
    rs = gain / (loss.replace(0, np.nan))
    return (100 - 100 / (1 + rs)).fillna(50)
