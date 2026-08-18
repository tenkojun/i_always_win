"""
vbt_pro.py — D#10 vectorbt 풀 기능 노출
============================================================
vectorbt의 모든 핵심 기능을 카테고리별로 분리해 단일 ticker에 적용:

  1) list_indicators / run_indicator       — 빌트인 인디케이터 카탈로그 + 실행
  2) run_from_orders                       — Portfolio.from_orders (명시적 주문)
  3) run_from_holding                      — Portfolio.from_holding (Buy & Hold)
  4) get_records (drawdowns/trades/orders) — pf records 추출 + Plotly 차트
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd


# ────────────────────────────────────────────────
#  vbt lazy
# ────────────────────────────────────────────────
_vbt = None
def _vbt_lib():
    global _vbt
    if _vbt is None:
        import vectorbt as vbt
        _vbt = vbt
    return _vbt


# Plotly 다크 헬퍼 (P2/P3와 동일 톤)
_BG_PAPER = "#05070a"; _BG_PLOT = "#0a0f16"
_TXT = "#cfe6ec";      _TXT_DIM = "#5d7480"
_GRID = "#16202e";     _CYAN = "#3df0ff"
_UP = "#ff5a52";       _DOWN = "#4d9cff";  _AMBER = "#ffb44c"


def _layout(title: str, height: int = 360) -> Dict[str, Any]:
    return {
        "title": {"text": title, "font": {"color": _CYAN, "size": 12},
                  "x": 0.02, "xanchor": "left"},
        "paper_bgcolor": _BG_PAPER, "plot_bgcolor": _BG_PLOT,
        "font": {"color": _TXT, "size": 10,
                 "family": "JetBrains Mono, monospace"},
        "xaxis": {"gridcolor": _GRID, "color": _TXT_DIM},
        "yaxis": {"gridcolor": _GRID, "color": _TXT_DIM},
        "margin": {"l": 50, "r": 15, "t": 35, "b": 40},
        "height": height,
        "hovermode": "x unified",
        "hoverlabel": {"bgcolor": _BG_PLOT, "bordercolor": _CYAN,
                       "font": {"color": _TXT, "size": 10}},
    }


def _dt_list(idx):
    try:
        return [pd.Timestamp(x).strftime("%Y-%m-%dT%H:%M:%S") for x in idx]
    except Exception:
        return list(idx)


def _safe_num(v, d=4):
    try:
        f = float(v)
        if np.isnan(f) or np.isinf(f):
            return None
        return round(f, d)
    except Exception:
        return None


# ════════════════════════════════════════════════════════════
#  1) Indicators 카탈로그 + 실행
# ════════════════════════════════════════════════════════════
# vbt 빌트인 인디케이터 (입력/파라미터 메타)
_INDICATORS_META = [
    # name, vbt class, inputs, params (default in parens)
    {"id": "MA",     "label": "Moving Average",      "inputs": ["close"],
     "params": [{"name": "window", "default": 20, "type": "int"}],
     "outputs": ["ma"]},
    {"id": "EMA",    "label": "Exponential MA",      "inputs": ["close"],
     "params": [{"name": "span", "default": 20, "type": "int"}],
     "outputs": ["ema"]},
    {"id": "RSI",    "label": "Relative Strength",   "inputs": ["close"],
     "params": [{"name": "window", "default": 14, "type": "int"}],
     "outputs": ["rsi"]},
    {"id": "MACD",   "label": "MACD",                "inputs": ["close"],
     "params": [{"name": "fast_window", "default": 12, "type": "int"},
                {"name": "slow_window", "default": 26, "type": "int"},
                {"name": "signal_window", "default": 9, "type": "int"}],
     "outputs": ["macd", "signal", "hist"]},
    {"id": "BBANDS", "label": "Bollinger Bands",     "inputs": ["close"],
     "params": [{"name": "window", "default": 20, "type": "int"},
                {"name": "alpha",  "default": 2.0, "type": "float"}],
     "outputs": ["upper", "middle", "lower"]},
    {"id": "ATR",    "label": "Average True Range",  "inputs": ["high","low","close"],
     "params": [{"name": "window", "default": 14, "type": "int"}],
     "outputs": ["atr"]},
    {"id": "STOCH",  "label": "Stochastic",          "inputs": ["high","low","close"],
     "params": [{"name": "k_window", "default": 14, "type": "int"},
                {"name": "d_window", "default": 3,  "type": "int"}],
     "outputs": ["k", "d"]},
    {"id": "OBV",    "label": "On-Balance Volume",   "inputs": ["close","volume"],
     "params": [], "outputs": ["obv"]},
    {"id": "MSTD",   "label": "Moving Std Dev",      "inputs": ["close"],
     "params": [{"name": "window", "default": 20, "type": "int"}],
     "outputs": ["mstd"]},
]


def list_indicators() -> Dict[str, Any]:
    """카탈로그 반환 — UI에서 선택지 채우기."""
    return {"ok": True, "indicators": _INDICATORS_META}


def run_indicator(ticker: str, indicator: str,
                   params: Optional[Dict[str, Any]] = None,
                   period_days: int = 365,
                   interval: str = "1d") -> Dict[str, Any]:
    """선택한 인디케이터 실행 + Plotly 차트 (가격 + 인디 line/subplot)."""
    meta = next((x for x in _INDICATORS_META if x["id"] == indicator), None)
    if not meta:
        return {"ok": False, "error": f"unknown indicator {indicator}"}
    try:
        from .vbt_runner import _fetch_ohlcv
        df = _fetch_ohlcv(ticker, period_days=period_days, interval=interval)
        if len(df) < 30:
            return {"ok": False, "error": f"데이터 부족 (n={len(df)})"}
        vbt = _vbt_lib()
        cls = getattr(vbt, indicator, None)
        if cls is None:
            return {"ok": False,
                    "error": f"vbt.{indicator} 없음 — vectorbt 버전 확인"}
        # 인디케이터 실행
        params = params or {}
        defaults = {p["name"]: p["default"] for p in meta["params"]}
        cfg = {**defaults, **params}
        # input 매핑 (close/high/low/volume 컬럼에서)
        inputs = []
        for col in meta["inputs"]:
            if col not in df.columns:
                return {"ok": False,
                        "error": f"입력 컬럼 '{col}' 데이터에 없음"}
            inputs.append(df[col].astype(float))
        # vbt 호출
        result = cls.run(*inputs, **cfg)
        # 출력 dict 만들기
        out_series = {}
        for o_name in meta["outputs"]:
            try:
                arr = getattr(result, o_name)
                if hasattr(arr, "squeeze"):
                    arr = arr.squeeze()
                if hasattr(arr, "values"):
                    out_series[o_name] = pd.Series(arr.values, index=df.index)
                else:
                    out_series[o_name] = pd.Series(arr, index=df.index)
            except Exception:
                continue
        # Plotly 차트 — 가격 + 인디케이터
        # subplot: 위(가격+overlay) / 아래(scalar indicator)
        OVERLAY_INDS = {"MA","EMA","BBANDS"}
        is_overlay = indicator in OVERLAY_INDS
        dates = _dt_list(df.index)
        close_vals = [float(v) for v in df["close"].values]
        traces = []
        # 1) 가격
        traces.append({
            "type": "scatter", "x": dates, "y": close_vals, "mode": "lines",
            "line": {"color": _TXT, "width": 0.9}, "name": "Close",
            "yaxis": "y1",
            "hovertemplate": "%{x|%Y-%m-%d}<br>$%{y:,.2f}<extra></extra>",
        })
        # 2) 인디케이터 출력들
        colors = [_CYAN, _AMBER, _UP, _DOWN, "#a07dff"]
        for i, (o_name, ser) in enumerate(out_series.items()):
            vals = [float(v) if pd.notna(v) else None for v in ser.values]
            col = colors[i % len(colors)]
            traces.append({
                "type": "scatter", "x": dates, "y": vals, "mode": "lines",
                "line": {"color": col, "width": 1.4}, "name": o_name,
                "yaxis": "y1" if is_overlay else "y2",
                "hovertemplate": f"{o_name} %{{y:.4f}}<extra></extra>",
            })
        layout = _layout(f"{ticker} · {indicator} {cfg}", height=440)
        if not is_overlay:
            # 가격(위) + 인디(아래) — 2 subplot
            layout["yaxis"]  = {"domain": [0.42, 1.0], **layout["yaxis"]}
            layout["yaxis2"] = {"domain": [0.0, 0.38], "gridcolor": _GRID,
                                 "color": _TXT_DIM,
                                 "title": {"text": indicator,
                                           "font": {"color": _TXT}}}
            layout["xaxis"]  = {"gridcolor": _GRID, "color": _TXT_DIM,
                                 "anchor": "y2"}
        return {
            "ok": True, "ticker": ticker, "indicator": indicator,
            "params": cfg, "n_bars": int(len(df)),
            "plotly": {"data": traces, "layout": layout},
            "last_values": {k: _safe_num(s.iloc[-1])
                            for k, s in out_series.items()},
        }
    except Exception as e:
        import traceback
        return {"ok": False, "error": f"{type(e).__name__}: {e}",
                "trace": traceback.format_exc()[-400:]}


# ════════════════════════════════════════════════════════════
#  2) Portfolio.from_orders — 명시적 주문 시뮬레이션
# ════════════════════════════════════════════════════════════
def run_from_orders(ticker: str, orders: List[Dict[str, Any]],
                     period_days: int = 730,
                     interval: str = "1d",
                     init_cash: float = 10000.0,
                     fees: float = 0.001,
                     slippage: float = 0.0001) -> Dict[str, Any]:
    """orders: [{date: 'YYYY-MM-DD', size: ±N, ...}, ...]
    size > 0 매수, size < 0 매도, np.inf = 전액, -np.inf = 전량 청산."""
    try:
        from .vbt_runner import _fetch_price
        from .pf_stats import extract_full_stats, monthly_returns_heatmap
        price = _fetch_price(ticker, period_days=period_days, interval=interval)
        if len(price) < 10:
            return {"ok": False, "error": "데이터 부족"}
        vbt = _vbt_lib()
        # 주문 시리즈 만들기 — 날짜별 size
        size_ser = pd.Series(0.0, index=price.index)
        for od in (orders or []):
            d = od.get("date")
            s = od.get("size")
            if d is None or s is None:
                continue
            try:
                ts = pd.Timestamp(d)
                # 가장 가까운 정확/이후 봉
                pos = price.index.get_indexer([ts], method="bfill")[0]
                if pos >= 0 and pos < len(price):
                    sval = float(s)
                    if sval == 0:
                        continue
                    size_ser.iloc[pos] += sval   # 동일 봉 누적
            except Exception:
                continue
        # nonzero count
        nonzero = int((size_ser != 0).sum())
        if nonzero == 0:
            return {"ok": False, "error": "유효 주문 0건 (날짜/사이즈 확인)"}
        pf = vbt.Portfolio.from_orders(
            close=price, size=size_ser,
            init_cash=init_cash, fees=fees, slippage=slippage,
        )
        # 결과
        total = float(pf.total_return())
        sh = float(pf.sharpe_ratio())
        mdd = float(pf.max_drawdown())
        nt = int(pf.trades.count())
        eq = pf.value()
        # equity (다운샘플 200)
        if len(eq) > 200:
            eq = eq.iloc[::max(1, len(eq)//200)]
        equity = [{"d": d.strftime("%Y-%m-%d"), "v": float(v)}
                  for d, v in eq.items()]
        stats_full = extract_full_stats(pf)
        heatmap = monthly_returns_heatmap(
            pf, title=f"{ticker} · from_orders 월별 수익률 %")
        return {
            "ok": True, "ticker": ticker,
            "n_orders": nonzero,
            "metrics": {
                "total_return": _safe_num(total),
                "sharpe":       _safe_num(sh, 3),
                "max_drawdown": _safe_num(mdd),
                "n_trades":     nt,
            },
            "equity_curve": equity,
            "stats_full": stats_full,
            "monthly_heatmap": heatmap,
        }
    except Exception as e:
        import traceback
        return {"ok": False, "error": f"{type(e).__name__}: {e}",
                "trace": traceback.format_exc()[-400:]}


# ════════════════════════════════════════════════════════════
#  3) Portfolio.from_holding — Buy & Hold
# ════════════════════════════════════════════════════════════
def run_from_holding(ticker: str,
                      period_days: int = 730,
                      interval: str = "1d",
                      init_cash: float = 10000.0,
                      fees: float = 0.001) -> Dict[str, Any]:
    """Buy & Hold 벤치마크. 전략 비교용 baseline."""
    try:
        from .vbt_runner import _fetch_price
        from .pf_stats import extract_full_stats, monthly_returns_heatmap
        price = _fetch_price(ticker, period_days=period_days, interval=interval)
        if len(price) < 10:
            return {"ok": False, "error": "데이터 부족"}
        vbt = _vbt_lib()
        try:
            pf = vbt.Portfolio.from_holding(
                close=price, init_cash=init_cash, fees=fees)
        except (TypeError, AttributeError):
            # 일부 vbt 버전 호환: from_orders로 첫 봉 매수
            size_ser = pd.Series(0.0, index=price.index)
            size_ser.iloc[0] = float("inf")   # 전액 매수
            pf = vbt.Portfolio.from_orders(
                close=price, size=size_ser,
                init_cash=init_cash, fees=fees)
        total = float(pf.total_return())
        sh = float(pf.sharpe_ratio())
        mdd = float(pf.max_drawdown())
        eq = pf.value()
        if len(eq) > 200:
            eq = eq.iloc[::max(1, len(eq)//200)]
        equity = [{"d": d.strftime("%Y-%m-%d"), "v": float(v)}
                  for d, v in eq.items()]
        stats_full = extract_full_stats(pf)
        heatmap = monthly_returns_heatmap(
            pf, title=f"{ticker} · Buy&Hold 월별 수익률 %")
        return {
            "ok": True, "ticker": ticker,
            "metrics": {
                "total_return": _safe_num(total),
                "sharpe":       _safe_num(sh, 3),
                "max_drawdown": _safe_num(mdd),
            },
            "equity_curve": equity,
            "stats_full": stats_full,
            "monthly_heatmap": heatmap,
        }
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


# ════════════════════════════════════════════════════════════
#  4) Records — drawdowns / trades / orders / positions
# ════════════════════════════════════════════════════════════
def get_records(ticker: str, strategy: str = "sma_cross",
                 params: Optional[Dict[str, Any]] = None,
                 period_days: int = 730,
                 interval: str = "1d") -> Dict[str, Any]:
    """전략 실행 후 pf records (drawdowns/trades/orders/positions) 추출.
    각각 Plotly 차트 + 표 데이터."""
    try:
        # _vbt_lib 함수 + AVAILABLE_STRATEGIES 모두 vbt_runner에서
        from .vbt_runner import (run_backtest, _fetch_price,
                                  _vbt_lib as _vbt_runner_lib, _vbt_freq,
                                  AVAILABLE_STRATEGIES)
        # 전략 실행 — entries/exits 필요해서 run_backtest 재사용
        r = run_backtest(ticker, strategy, params=params,
                          period_days=period_days, interval=interval)
        if not r.get("ok"):
            return {"ok": False, "error": r.get("error", "백테스트 실패")}
        # pf를 다시 만들어야 함 (run_backtest는 pf 안 반환) — 빠른 재실행
        vbt = _vbt_runner_lib()
        price = _fetch_price(ticker, period_days=period_days, interval=interval)
        # 시그널 직접 생성 — vbt_runner 내부 함수 재호출 (복잡함)
        # 간단히: run_backtest의 entry_signals/exit_signals 활용
        ent_dates = set(r.get("entry_signals") or [])
        ex_dates = set(r.get("exit_signals") or [])
        entries = pd.Series([d.strftime("%Y-%m-%d") in ent_dates
                              for d in price.index], index=price.index)
        exits = pd.Series([d.strftime("%Y-%m-%d") in ex_dates
                            for d in price.index], index=price.index)
        pf = vbt.Portfolio.from_signals(
            price, entries, exits,
            fees=0.001, init_cash=10000.0, freq=_vbt_freq(interval))

        out: Dict[str, Any] = {
            "ok": True, "ticker": ticker, "strategy": strategy,
        }

        # --- Drawdowns ---
        try:
            # records_readable 미지원 시 records 직접 사용
            if hasattr(pf.drawdowns, "records_readable"):
                dd_records = pf.drawdowns.records_readable
            else:
                dd_records = pf.drawdowns.records
            if not isinstance(dd_records, pd.DataFrame):
                dd_records = pd.DataFrame()
            dd_list = []
            for _, row in dd_records.head(30).iterrows():
                dd_list.append({
                    "start": str(row.get("Start Timestamp") or row.get("Start", "")),
                    "valley": str(row.get("Valley Timestamp") or row.get("Valley", "")),
                    "end": str(row.get("End Timestamp") or row.get("End", "")),
                    "drawdown_pct": _safe_num(row.get("Drawdown") or 0),
                    "duration": str(row.get("Duration") or ""),
                    "recovery": str(row.get("Recovery", "")),
                    "status": str(row.get("Status", "")),
                })
            out["drawdowns"] = dd_list
        except Exception as e:
            out["drawdowns_error"] = str(e)[:120]

        # --- Trades ---
        try:
            if hasattr(pf.trades, "records_readable"):
                tr_records = pf.trades.records_readable
            else:
                tr_records = pf.trades.records
            if not isinstance(tr_records, pd.DataFrame):
                tr_records = pd.DataFrame()
            tr_list = []
            for _, row in tr_records.head(50).iterrows():
                tr_list.append({
                    "entry_ts": str(row.get("Entry Timestamp") or row.get("Entry", "")),
                    "exit_ts":  str(row.get("Exit Timestamp")  or row.get("Exit", "")),
                    "size":     _safe_num(row.get("Size") or 0, 4),
                    "entry_price": _safe_num(row.get("Avg Entry Price") or 0, 2),
                    "exit_price":  _safe_num(row.get("Avg Exit Price")  or 0, 2),
                    "pnl":      _safe_num(row.get("PnL") or 0, 2),
                    "return":   _safe_num(row.get("Return") or 0, 4),
                    "status":   str(row.get("Status", "")),
                    "direction": str(row.get("Direction", "")),
                })
            out["trades"] = tr_list
            # PnL 분포 + scatter
            if tr_list:
                pnls = [t["pnl"] for t in tr_list if t["pnl"] is not None]
                if pnls:
                    out["pnl_plotly"] = {
                        "data": [{
                            "type": "histogram", "x": pnls, "nbinsx": 25,
                            "marker": {"color": _CYAN, "opacity": 0.7,
                                        "line": {"color": _GRID, "width": 0.5}},
                            "name": "PnL 분포",
                            "hovertemplate": "PnL %{x:.2f}<br>%{y}건<extra></extra>",
                        }],
                        "layout": _layout(f"트레이드 PnL 분포 (n={len(pnls)})",
                                           height=300),
                    }
        except Exception as e:
            out["trades_error"] = str(e)[:120]

        # --- Orders ---
        try:
            if hasattr(pf.orders, "records_readable"):
                ord_records = pf.orders.records_readable
            else:
                ord_records = pf.orders.records
            if not isinstance(ord_records, pd.DataFrame):
                ord_records = pd.DataFrame()
            ord_list = []
            for _, row in ord_records.head(50).iterrows():
                ord_list.append({
                    "timestamp": str(row.get("Timestamp") or row.get("Date", "")),
                    "size":      _safe_num(row.get("Size") or 0, 4),
                    "price":     _safe_num(row.get("Price") or 0, 2),
                    "fees":      _safe_num(row.get("Fees") or 0, 4),
                    "side":      str(row.get("Side", "")),
                })
            out["orders"] = ord_list
        except Exception as e:
            out["orders_error"] = str(e)[:120]

        # --- Positions ---
        try:
            if hasattr(pf.positions, "records_readable"):
                pos_records = pf.positions.records_readable
            else:
                pos_records = pf.positions.records
            if not isinstance(pos_records, pd.DataFrame):
                pos_records = pd.DataFrame()
            pos_list = []
            for _, row in pos_records.head(30).iterrows():
                pos_list.append({
                    "entry_ts": str(row.get("Entry Timestamp") or ""),
                    "exit_ts":  str(row.get("Exit Timestamp", "")),
                    "size":     _safe_num(row.get("Size") or 0, 4),
                    "pnl":      _safe_num(row.get("PnL") or 0, 2),
                    "return":   _safe_num(row.get("Return") or 0, 4),
                    "status":   str(row.get("Status", "")),
                })
            out["positions"] = pos_list
        except Exception as e:
            out["positions_error"] = str(e)[:120]

        return out
    except Exception as e:
        import traceback
        return {"ok": False, "error": f"{type(e).__name__}: {e}",
                "trace": traceback.format_exc()[-400:]}
