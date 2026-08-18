"""
다중 종목 × 다중 전략 동시 백테스트 (vectorbt cross-sectional)
=================================================================
vbt.Portfolio가 multi-column close DataFrame을 자동으로 종목별로
독립 평가 — 100종목 × 10전략 = 1000조합도 한 번의 numpy + numba 호출.

설계:
  - close = DataFrame (rows=time, cols=tickers)
  - 각 전략의 인디케이터를 close에 적용 → entries/exits도 DataFrame
  - Portfolio.from_signals → 각 column별 metrics
  - 결과: ticker × strategy 매트릭스
"""
from __future__ import annotations

import time
import math
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd


def _vbt():
    import vectorbt as vbt
    return vbt


# ── 다중 종목 OHLCV fetch ─────────────────────────────────────
def fetch_multi_close(tickers: List[str],
                      period_days: int = 730,
                      interval: str = "1d"
                      ) -> Tuple[pd.DataFrame, List[str], str]:
    """
    여러 종목의 종가를 한 DataFrame으로 결합.
    columns = ticker, index = 공통 날짜 (inner join).
    반환: (close_df, dropped_list, debug_msg)
    실패한 ticker는 dropped 리스트로 반환.
    """
    debug = []
    try:
        import yfinance as yf
    except Exception as e:
        return pd.DataFrame(), tickers, f"yfinance 미설치: {e}"

    end = pd.Timestamp.now()
    start = end - pd.Timedelta(days=period_days)
    closes = {}
    dropped = []

    # 항상 종목별 개별 fetch (가장 안정적)
    # — bulk download는 column 구조가 yf 버전마다 바뀜
    for t in tickers:
        if not t or len(t.strip()) == 0:
            continue
        try:
            h = yf.Ticker(t).history(
                start=start.strftime("%Y-%m-%d"),
                end=end.strftime("%Y-%m-%d"),
                interval=interval,
            )
            if h is None or h.empty or "Close" not in h.columns:
                dropped.append(t)
                debug.append(f"{t}: 빈 결과")
                continue
            s = h["Close"].dropna()
            if len(s) < 30:
                dropped.append(t)
                debug.append(f"{t}: 데이터 부족 ({len(s)} < 30)")
                continue
            # 인덱스 timezone 제거 (다른 ticker와 join 가능하게)
            try:
                if s.index.tz is not None:
                    s.index = s.index.tz_localize(None)
            except Exception:
                pass
            closes[t.upper()] = s.astype(float)
        except Exception as e:
            dropped.append(t)
            debug.append(f"{t}: {type(e).__name__} {str(e)[:60]}")

    if not closes:
        return pd.DataFrame(), tickers, " / ".join(debug[:5])
    # outer join + forward-fill (장 휴일 차이 흡수) + 30봉 미만 컷
    out = pd.DataFrame(closes).sort_index().ffill().dropna(how="all")
    out = out.dropna(thresh=int(len(out) * 0.6), axis=1)  # 60% 이상 비면 drop
    extra = [c for c in closes if c not in out.columns]
    if extra:
        dropped.extend(extra)
        debug.append(f"join 후 데이터 부족 drop: {extra}")
    return out, dropped, " / ".join(debug)


# ── 단일 전략을 multi-asset에 적용 ─────────────────────────────
def _run_sma_cross(close_df: pd.DataFrame,
                   fast: int = 10, slow: int = 50,
                   fees: float = 0.001, init_cash: float = 10000.0,
                   freq: str = "1D") -> Any:
    vbt = _vbt()
    ma_fast = vbt.MA.run(close_df, window=fast)
    ma_slow = vbt.MA.run(close_df, window=slow)
    entries = ma_fast.ma_crossed_above(ma_slow)
    exits = ma_fast.ma_crossed_below(ma_slow)
    return vbt.Portfolio.from_signals(
        close_df, entries, exits,
        fees=fees, init_cash=init_cash, freq=freq)


def _run_rsi_mr(close_df, window=14, low=30, high=70,
                fees=0.001, init_cash=10000.0, freq="1D"):
    vbt = _vbt()
    rsi = vbt.RSI.run(close_df, window=window).rsi
    entries = (rsi < low) & (rsi.shift(1) >= low)
    exits = (rsi > high) & (rsi.shift(1) <= high)
    return vbt.Portfolio.from_signals(
        close_df, entries, exits,
        fees=fees, init_cash=init_cash, freq=freq)


def _run_macd(close_df, fast=12, slow=26, signal=9,
              fees=0.001, init_cash=10000.0, freq="1D"):
    vbt = _vbt()
    m = vbt.MACD.run(close_df, fast_window=fast, slow_window=slow,
                      signal_window=signal)
    entries = (m.macd > m.signal) & (m.macd.shift(1) <= m.signal.shift(1))
    exits = (m.macd < m.signal) & (m.macd.shift(1) >= m.signal.shift(1))
    return vbt.Portfolio.from_signals(
        close_df, entries, exits,
        fees=fees, init_cash=init_cash, freq=freq)


def _run_bb_mean(close_df, window=20, dev=2.0,
                 fees=0.001, init_cash=10000.0, freq="1D"):
    vbt = _vbt()
    bb = vbt.BBANDS.run(close_df, window=window, alpha=dev)
    entries = close_df < bb.lower
    exits = close_df > bb.middle
    return vbt.Portfolio.from_signals(
        close_df, entries, exits,
        fees=fees, init_cash=init_cash, freq=freq)


def _run_zscore(close_df, window=20, entry_z=2.0,
                fees=0.001, init_cash=10000.0, freq="1D"):
    vbt = _vbt()
    m = close_df.rolling(window).mean()
    s = close_df.rolling(window).std()
    z = (close_df - m) / (s + 1e-9)
    entries = z < -entry_z
    exits = z > 0
    return vbt.Portfolio.from_signals(
        close_df, entries, exits,
        fees=fees, init_cash=init_cash, freq=freq)


def _run_roc(close_df, window=20, threshold=0.05,
             fees=0.001, init_cash=10000.0, freq="1D"):
    vbt = _vbt()
    r = close_df.pct_change(window)
    entries = r > threshold
    exits = r < 0
    return vbt.Portfolio.from_signals(
        close_df, entries, exits,
        fees=fees, init_cash=init_cash, freq=freq)


def _run_donchian(close_df, window=20,
                  fees=0.001, init_cash=10000.0, freq="1D"):
    vbt = _vbt()
    upper = close_df.rolling(window).max().shift(1)
    lower = close_df.rolling(window).min().shift(1)
    entries = close_df > upper
    exits = close_df < lower
    return vbt.Portfolio.from_signals(
        close_df, entries, exits,
        fees=fees, init_cash=init_cash, freq=freq)


# ── 전략 디스패처 ─────────────────────────────────────────────
_ENGINES = {
    "sma_cross": _run_sma_cross,
    "rsi_mr":    _run_rsi_mr,
    "macd":      _run_macd,
    "bb_mean":   _run_bb_mean,
    "zscore":    _run_zscore,
    "roc":       _run_roc,
    "donchian":  _run_donchian,
}

MULTI_ASSET_SUPPORTED = list(_ENGINES.keys())


def _freq_for(interval: str) -> str:
    return {"1m": "1T", "5m": "5T", "15m": "15T", "30m": "30T",
            "1h": "1H", "60m": "1H", "1d": "1D"}.get(interval, "1D")


# ── 결과 추출 ──────────────────────────────────────────────────
def _extract_metrics(pf, tickers: List[str]) -> List[Dict[str, Any]]:
    """multi-column Portfolio → ticker별 dict 리스트."""
    if pf is None:
        return []
    def _arr(x):
        if isinstance(x, (int, float, np.floating)):
            return np.array([float(x)] * len(tickers))
        return np.asarray(x.values if hasattr(x, "values") else x)
    try:
        sh = _arr(pf.sharpe_ratio())
    except Exception: sh = np.zeros(len(tickers))
    try:
        tot = _arr(pf.total_return())
    except Exception: tot = np.zeros(len(tickers))
    try:
        dd = _arr(pf.max_drawdown())
    except Exception: dd = np.zeros(len(tickers))
    try:
        so = _arr(pf.sortino_ratio())
    except Exception: so = np.zeros(len(tickers))
    try:
        wr = _arr(pf.trades.win_rate())
    except Exception: wr = np.zeros(len(tickers))
    try:
        nt = _arr(pf.trades.count())
    except Exception: nt = np.zeros(len(tickers))
    rows = []
    for i, t in enumerate(tickers):
        def _v(arr, i):
            if i >= len(arr): return 0.0
            v = float(arr[i])
            return v if math.isfinite(v) else 0.0
        rows.append({
            "ticker":       t,
            "sharpe":       round(_v(sh, i), 3),
            "total_return": round(_v(tot, i), 4),
            "max_drawdown": round(_v(dd, i), 4),
            "sortino":      round(_v(so, i), 3),
            "win_rate":     round(_v(wr, i), 4),
            "n_trades":     int(_v(nt, i)),
        })
    return rows


# ── 메인 진입점 ──────────────────────────────────────────────
def multi_asset_backtest(tickers: List[str],
                          strategies: List[str],
                          period_days: int = 730,
                          interval: str = "1d",
                          fees: float = 0.001,
                          init_cash: float = 10000.0,
                          strategy_params: Dict[str, Dict[str, Any]] = None
                          ) -> Dict[str, Any]:
    """
    N tickers × M strategies 동시 백테스트.
    각 전략당 한 번의 Portfolio.from_signals 호출 (multi-column close).
    """
    t0 = time.time()
    tickers = [t.strip().upper() for t in tickers if t and t.strip()]
    if not tickers:
        return {"ok": False, "error": "tickers 비어있음"}
    if not strategies:
        strategies = ["sma_cross"]
    # 미지원 전략 필터
    bad = [s for s in strategies if s not in _ENGINES]
    strategies = [s for s in strategies if s in _ENGINES]
    if not strategies:
        return {"ok": False,
                "error": f"지원되지 않는 전략: {bad}",
                "supported": MULTI_ASSET_SUPPORTED}

    # 1) close DataFrame
    close_df, dropped, debug = fetch_multi_close(tickers, period_days, interval)
    if close_df.empty or len(close_df.columns) == 0:
        return {"ok": False,
                "error": "데이터 fetch 실패 — 모든 종목 누락",
                "dropped": dropped,
                "debug": debug,
                "tip": ("티커 형식 확인: 미국주=AAPL, 한국주=005930.KS, "
                        "암호화폐=BTC-USD. yfinance 연결 확인.")}
    actual_tickers = list(close_df.columns)
    freq = _freq_for(interval)
    sp = strategy_params or {}

    # 2) 각 전략 평가
    matrix = {}     # strategy → [ticker rows]
    elapsed_per = {}
    for s in strategies:
        ts = time.time()
        try:
            pf = _ENGINES[s](close_df, fees=fees,
                              init_cash=init_cash, freq=freq,
                              **(sp.get(s, {})))
            matrix[s] = _extract_metrics(pf, actual_tickers)
        except Exception as e:
            matrix[s] = [{"ticker": t, "error": str(e)}
                          for t in actual_tickers]
        elapsed_per[s] = round(time.time() - ts, 2)

    # 3) Cross-sectional 분석
    # 각 종목별 평균 sharpe (전략 평균)
    per_ticker = {t: [] for t in actual_tickers}
    for s, rows in matrix.items():
        for r in rows:
            if "sharpe" in r:
                per_ticker[r["ticker"]].append(r["sharpe"])
    ticker_summary = []
    for t, sharpes in per_ticker.items():
        if sharpes:
            ticker_summary.append({
                "ticker": t,
                "mean_sharpe": round(float(np.mean(sharpes)), 3),
                "best_sharpe": round(float(max(sharpes)), 3),
                "n_strats_tested": len(sharpes),
            })
    ticker_summary.sort(key=lambda x: x["best_sharpe"], reverse=True)

    # 4) Top combinations
    top_combos = []
    for s, rows in matrix.items():
        for r in rows:
            if "sharpe" in r:
                top_combos.append({
                    "ticker": r["ticker"],
                    "strategy": s,
                    "sharpe": r["sharpe"],
                    "total_return": r["total_return"],
                    "max_drawdown": r["max_drawdown"],
                    "n_trades": r["n_trades"],
                })
    top_combos.sort(key=lambda x: x["sharpe"], reverse=True)

    return {
        "ok": True,
        "n_tickers": len(actual_tickers),
        "n_strategies": len(strategies),
        "n_combinations": len(actual_tickers) * len(strategies),
        "tickers": actual_tickers,
        "strategies": strategies,
        "dropped_tickers": dropped,
        "fetch_debug": debug,
        "interval": interval,
        "period_days": period_days,
        "elapsed_sec": round(time.time() - t0, 2),
        "elapsed_per_strategy": elapsed_per,
        "matrix": matrix,
        "ticker_summary": ticker_summary,
        "top_combos": top_combos[:30],
    }
