"""
벡터화 Grid Search — vectorbt의 진짜 강점 활용
=================================================
파라미터 N개 조합을 numpy broadcast로 한 번에 평가.
일반 Python 루프 대비 50~500배 빠름.

전략별 벡터화 가능 매트릭스:
  ✅ 완전 벡터화: sma_cross, rsi_mr, macd, bb_mean, zscore, roc,
                  cvd_div, donchian, keltner, vwap, engulfing, adx
                  (zscore/roc/cvd_div 는 IndicatorFactory + numba JIT)
  ⚠️  부분 벡터화: ichimoku, supertrend
  ❌  벡터화 불가 (for-loop 폴백): smc_ob, dva, heikin_ashi, triple_screen

벡터화 불가 전략은 자동으로 기존 run_backtest 루프 사용.
"""
from __future__ import annotations

import itertools
import time
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd


def _vbt():
    import vectorbt as vbt
    return vbt


def _fetch_close(ticker: str, period_days: int = 730,
                 interval: str = "1d") -> pd.Series:
    from .vbt_runner import _fetch_price
    return _fetch_price(ticker, period_days, interval=interval)


def _fetch_ohlcv_df(ticker: str, period_days: int = 730,
                    interval: str = "1d") -> pd.DataFrame:
    from .vbt_runner import _fetch_ohlcv
    return _fetch_ohlcv(ticker, period_days, interval=interval)


def _freq_for(interval: str) -> str:
    return {"1m": "1T", "5m": "5T", "15m": "15T", "30m": "30T",
            "1h": "1H", "60m": "1H", "1d": "1D"}.get(interval, "1D")


# ───────────────────────────────────────────────────────────────
#  벡터화 가능 전략 목록
# ───────────────────────────────────────────────────────────────
# 실제 엔진이 등록된 전략만 벡터화 가능 (아래 _VECTORIZED_ENGINES와 자동 동기화됨)
# keltner/vwap/adx 등은 엔진 구현 전까지 자동으로 폴백 처리됨


def is_vectorizable(strategy: str) -> bool:
    return strategy in _VECTORIZED_ENGINES


# ───────────────────────────────────────────────────────────────
#  엔진별 벡터화 grid 구현
# ───────────────────────────────────────────────────────────────
def _grid_sma_cross(close: pd.Series, grid: Dict[str, List],
                    fees: float, init_cash: float, freq: str
                    ) -> Tuple[Any, List[Tuple]]:
    vbt = _vbt()
    fast_arr = grid.get("fast", [5, 10, 20])
    slow_arr = grid.get("slow", [50, 100, 200])
    # 카테시안 곱 (fast < slow만 유효)
    combos = [(f, s) for f in fast_arr for s in slow_arr if f < s]
    if not combos:
        return None, []
    fs = [c[0] for c in combos]
    ss = [c[1] for c in combos]
    # vbt.MA에 리스트로 한 번에 — 결과는 multi-column
    ma_fast = vbt.MA.run(close, window=fs, short_name="fast",
                          per_column=True)
    ma_slow = vbt.MA.run(close, window=ss, short_name="slow",
                          per_column=True)
    entries = ma_fast.ma_crossed_above(ma_slow)
    exits = ma_fast.ma_crossed_below(ma_slow)
    entries, exits = _shift_signals(entries, exits)
    pf = vbt.Portfolio.from_signals(
        close, entries, exits,
        fees=fees, init_cash=init_cash, freq=freq)
    return pf, combos


def _grid_rsi_mr(close: pd.Series, grid: Dict[str, List],
                 fees: float, init_cash: float, freq: str
                 ) -> Tuple[Any, List[Tuple]]:
    vbt = _vbt()
    wins = grid.get("window", [7, 14, 21])
    lows = grid.get("low", [20, 30])
    highs = grid.get("high", [70, 80])
    combos = [(w, l, h) for w in wins for l in lows for h in highs if l < h]
    if not combos:
        return None, []
    ws = [c[0] for c in combos]
    ls = [c[1] for c in combos]
    hs = [c[2] for c in combos]
    rsi = vbt.RSI.run(close, window=ws, per_column=True)
    # crossed_below/above은 스칼라 threshold만 받으므로 직접 비교
    rsi_vals = rsi.rsi
    low_thr = pd.Series(ls, index=rsi_vals.columns)
    high_thr = pd.Series(hs, index=rsi_vals.columns)
    # broadcast: rsi < low_thr 행별로
    entries = rsi_vals.lt(low_thr, axis=1) & rsi_vals.shift(1).ge(
        low_thr, axis=1)
    exits = rsi_vals.gt(high_thr, axis=1) & rsi_vals.shift(1).le(
        high_thr, axis=1)
    entries, exits = _shift_signals(entries, exits)
    pf = vbt.Portfolio.from_signals(
        close, entries, exits,
        fees=fees, init_cash=init_cash, freq=freq)
    return pf, combos


def _grid_macd(close: pd.Series, grid: Dict[str, List],
               fees: float, init_cash: float, freq: str
               ) -> Tuple[Any, List[Tuple]]:
    vbt = _vbt()
    fasts = grid.get("fast", [8, 12, 16])
    slows = grid.get("slow", [21, 26, 32])
    signals = grid.get("signal", [7, 9, 11])
    combos = [(f, s, sg) for f in fasts for s in slows for sg in signals
              if f < s]
    if not combos:
        return None, []
    fs = [c[0] for c in combos]; ss = [c[1] for c in combos]
    sg = [c[2] for c in combos]
    macd = vbt.MACD.run(close, fast_window=fs, slow_window=ss,
                        signal_window=sg, per_column=True)
    entries = macd.macd_above(macd.signal) & macd.macd.shift(1).lt(
        macd.signal.shift(1))
    exits = macd.macd_below(macd.signal) & macd.macd.shift(1).gt(
        macd.signal.shift(1))
    entries, exits = _shift_signals(entries, exits)
    pf = vbt.Portfolio.from_signals(
        close, entries, exits,
        fees=fees, init_cash=init_cash, freq=freq)
    return pf, combos


def _grid_bb_mean(close: pd.Series, grid: Dict[str, List],
                  fees: float, init_cash: float, freq: str
                  ) -> Tuple[Any, List[Tuple]]:
    vbt = _vbt()
    wins = grid.get("window", [10, 20, 30])
    devs = grid.get("dev", [1.5, 2.0, 2.5])
    combos = [(w, d) for w in wins for d in devs]
    ws = [c[0] for c in combos]; ds = [c[1] for c in combos]
    bb = vbt.BBANDS.run(close, window=ws, alpha=ds, per_column=True)
    entries = close.values[:, None] < bb.lower.values   # broadcast
    exits = close.values[:, None] > bb.middle.values
    entries = pd.DataFrame(entries, index=close.index,
                            columns=bb.lower.columns)
    exits = pd.DataFrame(exits, index=close.index,
                          columns=bb.lower.columns)
    pf = vbt.Portfolio.from_signals(
        close, entries, exits,
        fees=fees, init_cash=init_cash, freq=freq)
    return pf, combos


def _grid_cvd_div(close: pd.Series, grid: Dict[str, List],
                  fees: float, init_cash: float, freq: str
                  ) -> Tuple[Any, List[Tuple]]:
    """PseudoCVDDiv IndicatorFactory 기반 — cvd_div 전략 벡터화."""
    from .vbt_indicators import PseudoCVDDiv
    vbt_lib = _vbt()
    lbs    = grid.get("lookback", [10, 15, 20, 30])
    combos = [(lb,) for lb in lbs]
    ind     = PseudoCVDDiv.run(close, lookback=lbs)
    entries = ind.sig_entry.astype(bool)
    exits   = ind.sig_exit.astype(bool)
    entries, exits = _shift_signals(entries, exits)
    pf = vbt_lib.Portfolio.from_signals(
        close, entries, exits,
        fees=fees, init_cash=init_cash, freq=freq)
    return pf, combos


def _grid_smc_ob(ohlcv: pd.DataFrame, grid: Dict[str, List],
                 fees: float, init_cash: float, freq: str
                 ) -> Tuple[Any, List[Tuple]]:
    """SMC Order Block — 재귀 state 로직이라 numba/broadcasting 불가.
    한 번만 데이터 fetch + N combos batch portfolio = 폴백 대비 빠름."""
    from .vbt_runner import _signals_smc_ob
    vbt_lib = _vbt()
    close = ohlcv["close"].astype(float)
    swings  = grid.get("swing",  [3, 5, 8])
    retests = grid.get("retest", [5, 8, 12])
    combos = list(itertools.product(swings, retests))
    cols_e, cols_x = {}, {}
    for (sw, rt) in combos:
        try:
            e, x = _signals_smc_ob(close, swing=int(sw), retest=int(rt))
            cols_e[f"s{sw}_r{rt}"] = e
            cols_x[f"s{sw}_r{rt}"] = x
        except Exception:
            continue
    if not cols_e:
        return None, []
    entries = pd.DataFrame(cols_e).fillna(False)
    exits = pd.DataFrame(cols_x).fillna(False)
    entries, exits = _shift_signals(entries, exits)
    pf = vbt_lib.Portfolio.from_signals(
        close, entries, exits,
        fees=fees, init_cash=init_cash, freq=freq)
    return pf, combos


# ───────────────────────────────────────────────────────────────
#  Generic wrapper — 임의의 sig_fn(df, **params) → 벡터화 engine
#  strategies_ext의 14개 전략을 한 줄로 자동 등록할 수 있게 함.
#  데이터는 1회 fetch + N combos batch portfolio → 폴백 대비 5~20x 빠름.
# ───────────────────────────────────────────────────────────────
# 룩어헤드 bias 제거 — 모든 벡터화 엔진에 적용
_NEXT_BAR_EXEC = True


def _shift_signals(entries, exits):
    """t 시점 시그널 → t+1 시점 실행 (현실적 거래 시뮬레이션)."""
    if not _NEXT_BAR_EXEC:
        return entries, exits
    try:
        if hasattr(entries, "shift"):
            entries = entries.shift(1).fillna(False).astype(bool)
        if hasattr(exits, "shift"):
            exits = exits.shift(1).fillna(False).astype(bool)
    except Exception:
        pass
    return entries, exits


def _make_grid_from_sig_fn(sig_fn, param_names: List[str],
                            defaults: Dict[str, Any]):
    """sig_fn: (ohlcv_df, **params) -> (entries:Series, exits:Series)
    반환된 engine은 (ohlcv, grid, fees, init_cash, freq) 시그너처."""
    def _engine(ohlcv, grid, fees, init_cash, freq):
        vbt_lib = _vbt()
        # 각 param의 grid 값(없으면 default)
        param_vals = [grid.get(p, [defaults.get(p)]) for p in param_names]
        # 빈 리스트나 None 방어
        param_vals = [v if (isinstance(v, list) and v) else [defaults.get(p)]
                      for p, v in zip(param_names, param_vals)]
        combos = list(itertools.product(*param_vals))
        if not combos:
            return None, []
        cols_e, cols_x, valid_combos = {}, {}, []
        for combo in combos:
            params = dict(zip(param_names, combo))
            try:
                e, x = sig_fn(ohlcv, **params)
                key = "_".join(f"{k}{v}" for k, v in zip(param_names, combo))
                cols_e[key] = e
                cols_x[key] = x
                valid_combos.append(combo)
            except Exception:
                continue
        if not cols_e:
            return None, []
        entries = pd.DataFrame(cols_e).fillna(False).astype(bool)
        exits = pd.DataFrame(cols_x).fillna(False).astype(bool)
        # 룩어헤드 fix
        entries, exits = _shift_signals(entries, exits)
        close = (ohlcv["close"].astype(float)
                 if hasattr(ohlcv, "columns") else ohlcv)
        pf = vbt_lib.Portfolio.from_signals(
            close, entries, exits,
            fees=fees, init_cash=init_cash, freq=freq)
        return pf, valid_combos
    return _engine


# ───────────────────────────────────────────────────────────────
#  OF / PEAD / Hybrid — 번들 1회 빌드 + N combos batch
# ───────────────────────────────────────────────────────────────
def _grid_orderflow(bundle, grid: Dict[str, List],
                    fees: float, init_cash: float, freq: str
                    ) -> Tuple[Any, List[Tuple]]:
    from ..orderflow_pead import OrderflowDeltaStrategy
    vbt_lib = _vbt()
    close = bundle["ohlcv"]["close"].astype(float)
    ds   = grid.get("delta_threshold",     [200, 500, 1000])
    lbs  = grid.get("divergence_lookback", [10, 20])
    zs   = grid.get("divergence_z",        [1.5])
    mhs  = grid.get("max_hold_bars",       [20, 40])
    combos = list(itertools.product(ds, lbs, zs, mhs))
    cols_e, cols_x, valid = {}, {}, []
    for (d, lb, z, mh) in combos:
        try:
            strat = OrderflowDeltaStrategy(
                delta_threshold=float(d),
                divergence_lookback=int(lb),
                divergence_z=float(z),
                max_hold_bars=int(mh),
            )
            sig = strat.generate_signals(bundle)
            key = f"d{d}_l{lb}_z{z}_h{mh}"
            cols_e[key] = sig["entries"].reindex(close.index).fillna(False)
            cols_x[key] = sig["exits"].reindex(close.index).fillna(False)
            valid.append((d, lb, z, mh))
        except Exception:
            continue
    if not cols_e:
        return None, []
    entries = pd.DataFrame(cols_e).astype(bool)
    exits   = pd.DataFrame(cols_x).astype(bool)
    entries, exits = _shift_signals(entries, exits)
    pf = vbt_lib.Portfolio.from_signals(
        close, entries, exits,
        fees=fees, init_cash=init_cash, freq=freq)
    return pf, valid


def _grid_pead(bundle, grid: Dict[str, List],
               fees: float, init_cash: float, freq: str
               ) -> Tuple[Any, List[Tuple]]:
    from ..orderflow_pead import PEADStrategy
    vbt_lib = _vbt()
    close = bundle["ohlcv"]["close"].astype(float)
    if bundle.get("earnings") is None or bundle["earnings"].empty:
        return None, []
    tps  = grid.get("sue_top_pct", [0.1, 0.2, 0.3])
    dds  = grid.get("drift_days",  [10, 20, 30, 60])
    offs = grid.get("enter_offset_days", [1])
    combos = list(itertools.product(tps, dds, offs))
    cols_e, cols_x, valid = {}, {}, []
    for (tp, dd, off) in combos:
        try:
            strat = PEADStrategy(sue_top_pct=float(tp),
                                  drift_days=int(dd),
                                  enter_offset_days=int(off))
            sig = strat.generate_signals(bundle)
            key = f"p{tp}_d{dd}_o{off}"
            cols_e[key] = sig["entries"].reindex(close.index).fillna(False)
            cols_x[key] = sig["exits"].reindex(close.index).fillna(False)
            valid.append((tp, dd, off))
        except Exception:
            continue
    if not cols_e:
        return None, []
    entries = pd.DataFrame(cols_e).astype(bool)
    exits   = pd.DataFrame(cols_x).astype(bool)
    entries, exits = _shift_signals(entries, exits)
    pf = vbt_lib.Portfolio.from_signals(
        close, entries, exits,
        fees=fees, init_cash=init_cash, freq=freq)
    return pf, valid


def _grid_hybrid_of_pead(bundle, grid: Dict[str, List],
                          fees: float, init_cash: float, freq: str
                          ) -> Tuple[Any, List[Tuple]]:
    from ..orderflow_pead import Hybrid_OF_PEAD_Strategy
    vbt_lib = _vbt()
    close = bundle["ohlcv"]["close"].astype(float)
    if bundle.get("earnings") is None or bundle["earnings"].empty:
        return None, []
    ds   = grid.get("delta_threshold", [500, 1000])
    tps  = grid.get("sue_top_pct",     [0.1, 0.2])
    dds  = grid.get("drift_days",      [20, 40])
    combos = list(itertools.product(ds, tps, dds))
    cols_e, cols_x, valid = {}, {}, []
    for (d, tp, dd) in combos:
        try:
            strat = Hybrid_OF_PEAD_Strategy(
                of_params={"delta_threshold": float(d)},
                pead_params={"sue_top_pct": float(tp),
                             "drift_days":  int(dd)},
            )
            sig = strat.generate_signals(bundle)
            key = f"d{d}_p{tp}_dd{dd}"
            cols_e[key] = sig["entries"].reindex(close.index).fillna(False)
            cols_x[key] = sig["exits"].reindex(close.index).fillna(False)
            valid.append((d, tp, dd))
        except Exception:
            continue
    if not cols_e:
        return None, []
    entries = pd.DataFrame(cols_e).astype(bool)
    exits   = pd.DataFrame(cols_x).astype(bool)
    entries, exits = _shift_signals(entries, exits)
    pf = vbt_lib.Portfolio.from_signals(
        close, entries, exits,
        fees=fees, init_cash=init_cash, freq=freq)
    return pf, valid


# ───────────────────────────────────────────────────────────────
#  엔진 등록 — 모든 전략 (총 22개) 벡터화
# ───────────────────────────────────────────────────────────────
# 1) 원래 빌트인 — vbt 빌트인 인디케이터 활용 (가장 빠름)
_VECTORIZED_ENGINES: Dict[str, Any] = {
    "sma_cross": _grid_sma_cross,
    "rsi_mr":    _grid_rsi_mr,
    "macd":      _grid_macd,
    "cvd_div":   _grid_cvd_div,   # PseudoCVDDiv IndicatorFactory + numba
    "smc_ob":    _grid_smc_ob,    # 재귀 state — 데이터 1회 + batch portfolio
    "orderflow": _grid_orderflow,
    "pead":      _grid_pead,
    "hybrid_of_pead": _grid_hybrid_of_pead,
}

# 2) strategies_ext의 14개 — generic wrapper로 자동 등록
def _register_strategies_ext():
    """strategies_ext의 모든 전략을 generic wrapper로 _VECTORIZED_ENGINES에 등록.
    각각 1회 데이터 fetch + N combos batch portfolio."""
    try:
        from .strategies_ext import STRATEGIES_EXT
    except Exception:
        return
    for sid, meta in STRATEGIES_EXT.items():
        if sid in _VECTORIZED_ENGINES:
            continue  # 이미 빌트인이 있으면 건너뜀
        param_names = [p["name"] for p in meta["params"]]
        defaults = {p["name"]: p["default"] for p in meta["params"]}
        _VECTORIZED_ENGINES[sid] = _make_grid_from_sig_fn(
            meta["fn"], param_names, defaults)
        _PARAM_NAMES_AUTO[sid] = param_names

_PARAM_NAMES_AUTO: Dict[str, List[str]] = {}
_register_strategies_ext()

# 외부 export용 (서버 API + 프론트 picker가 참조)
VECTORIZABLE = set(_VECTORIZED_ENGINES.keys())


# ───────────────────────────────────────────────────────────────
#  Portfolio (multi-column) → 결과 추출
# ───────────────────────────────────────────────────────────────
def _extract_metrics(pf, combos: List[Tuple], param_names: List[str]
                     ) -> List[Dict[str, Any]]:
    """multi-column Portfolio에서 각 컬럼(=조합)별 지표 추출."""
    if pf is None:
        return []
    try:
        sharpe = pf.sharpe_ratio()
        total = pf.total_return()
        max_dd = pf.max_drawdown()
        try:
            sortino = pf.sortino_ratio()
        except Exception:
            sortino = pd.Series([0]*len(combos))
        try:
            wr = pf.trades.win_rate()
        except Exception:
            wr = pd.Series([0]*len(combos))
        try:
            n_tr = pf.trades.count()
        except Exception:
            n_tr = pd.Series([0]*len(combos))
    except Exception as e:
        return [{"error": str(e)}]
    # multi-column일 경우 Series, 단일 컬럼이면 scalar
    def _as_arr(x):
        if isinstance(x, (int, float)):
            return np.array([x])
        return np.asarray(x.values if hasattr(x, "values") else x)
    sh = _as_arr(sharpe)
    tot = _as_arr(total)
    dd = _as_arr(max_dd)
    so = _as_arr(sortino)
    w = _as_arr(wr)
    n = _as_arr(n_tr)
    n_combos = len(combos)
    results = []
    for i, c in enumerate(combos):
        params = {param_names[j]: c[j] for j in range(len(param_names))}
        results.append({
            "params": params,
            "sharpe":  float(sh[i]) if i < len(sh) and np.isfinite(sh[i]) else 0,
            "total_return": float(tot[i]) if i < len(tot) and np.isfinite(tot[i]) else 0,
            "max_drawdown": float(dd[i]) if i < len(dd) and np.isfinite(dd[i]) else 0,
            "sortino": float(so[i]) if i < len(so) and np.isfinite(so[i]) else 0,
            "win_rate": float(w[i]) if i < len(w) and np.isfinite(w[i]) else 0,
            "n_trades": int(n[i]) if i < len(n) else 0,
        })
    return results


# 빌트인 엔진의 param 순서 (auto-register는 _PARAM_NAMES_AUTO에서 추가)
_PARAM_NAMES_BUILTIN = {
    "sma_cross": ["fast", "slow"],
    "rsi_mr":    ["window", "low", "high"],
    "macd":      ["fast", "slow", "signal"],
    "cvd_div":   ["lookback"],
    "smc_ob":    ["swing", "retest"],
    "orderflow": ["delta_threshold", "divergence_lookback",
                  "divergence_z", "max_hold_bars"],
    "pead":      ["sue_top_pct", "drift_days", "enter_offset_days"],
    "hybrid_of_pead": ["delta_threshold", "sue_top_pct", "drift_days"],
}
# 빌트인 + auto-register 통합 dict (서버 API export 용)
_PARAM_NAMES = {**_PARAM_NAMES_BUILTIN, **_PARAM_NAMES_AUTO}

# 데이터 종류별 분류 — vectorized_grid의 dispatch에 사용
_DATA_KIND_BUNDLE = {"orderflow", "pead", "hybrid_of_pead"}
_DATA_KIND_OHLCV  = {"smc_ob"}  # close+high+low+open+volume 모두 사용
# strategies_ext는 모두 OHLCV (df) 받음
def _data_kind_for(strategy: str) -> str:
    if strategy in _DATA_KIND_BUNDLE:
        return "bundle"
    if strategy in _DATA_KIND_OHLCV or strategy in _PARAM_NAMES_AUTO:
        return "ohlcv"
    return "close"


# ───────────────────────────────────────────────────────────────
#  공개 진입점
# ───────────────────────────────────────────────────────────────
def _get_param_labels(strategy: str) -> Dict[str, str]:
    """전략의 파라미터 영문 key → 한글 label 매핑.
    AVAILABLE_STRATEGIES에서 label 필드 추출.
    """
    try:
        from .vbt_runner import AVAILABLE_STRATEGIES
        meta = AVAILABLE_STRATEGIES.get(strategy, {})
        return {p["name"]: p.get("label", p["name"])
                for p in meta.get("params", [])}
    except Exception:
        return {}


def vectorized_grid(strategy: str, ticker: str,
                    grid: Dict[str, List[Any]],
                    period_days: int = 730,
                    interval: str = "1d",
                    fees: float = 0.001,
                    init_cash: float = 10000.0,
                    top_n: int = 10) -> Dict[str, Any]:
    """
    벡터화 grid search — 단일 전략, 모든 파라미터 조합을 한 번에 평가.
    벡터화 불가능 전략은 자동으로 루프 폴백.
    """
    t0 = time.time()
    if strategy not in _VECTORIZED_ENGINES:
        # 폴백: 기존 run_grid_search (Python 루프)
        try:
            from .vbt_runner import run_grid_search
            r = run_grid_search(ticker, strategy, grid,
                                 period_days=period_days, fees=fees,
                                 top_n=top_n)
            r["vectorized"] = False
            r["elapsed_sec"] = round(time.time() - t0, 2)
            return r
        except Exception as e:
            return {"ok": False, "vectorized": False,
                    "error": f"폴백 grid search 실패: {e}",
                    "strategy": strategy}

    # 데이터 fetch — 전략별 필요 데이터 자동 dispatch (close/ohlcv/bundle)
    kind = _data_kind_for(strategy)
    try:
        if kind == "bundle":
            # OF/PEAD/Hybrid — 어닝/orderflow 포함 번들 1회 빌드
            from ..orderflow_pead.main import build_bundle
            bundle = build_bundle(ticker, period_days=period_days,
                                   interval=interval)
            ohlcv_for_check = bundle.get("ohlcv")
            if ohlcv_for_check is None or len(ohlcv_for_check) < 30:
                return {"ok": False, "error": "데이터 부족"}
            engine_args = (bundle, grid, fees, init_cash, _freq_for(interval))
        elif kind == "ohlcv":
            ohlcv = _fetch_ohlcv_df(ticker, period_days, interval)
            if len(ohlcv) < 30:
                return {"ok": False, "error": "데이터 부족"}
            engine_args = (ohlcv, grid, fees, init_cash, _freq_for(interval))
        else:  # close
            close = _fetch_close(ticker, period_days, interval)
            if len(close) < 30:
                return {"ok": False, "error": "데이터 부족"}
            engine_args = (close, grid, fees, init_cash, _freq_for(interval))
    except Exception as e:
        return {"ok": False, "error": f"데이터 로드 실패: {e}"}

    engine_fn = _VECTORIZED_ENGINES[strategy]
    try:
        pf, combos = engine_fn(*engine_args)
    except Exception as e:
        return {"ok": False, "error": f"엔진 실행 실패: {e}",
                "vectorized": True}
    if not combos:
        return {"ok": False, "error": "유효 조합 없음 (제약 위반)",
                "vectorized": True}

    param_names = _PARAM_NAMES.get(strategy, ["p1", "p2", "p3"])[:len(combos[0])]
    results = _extract_metrics(pf, combos, param_names)
    results.sort(key=lambda x: x.get("sharpe", -1e9), reverse=True)
    return {
        "ok": True,
        "ticker": ticker,
        "strategy": strategy,
        "vectorized": True,
        "n_combos": len(combos),
        "interval": interval,
        "elapsed_sec": round(time.time() - t0, 2),
        "top": results[:top_n],
        "all": results,
        "param_labels": _get_param_labels(strategy),
    }


def batch_grid(strategies: List[str],
               grids: Dict[str, Dict[str, List]],
               ticker: str,
               period_days: int = 730,
               interval: str = "1d",
               fees: float = 0.001,
               init_cash: float = 10000.0,
               top_n_per: int = 5
               ) -> Dict[str, Any]:
    """
    여러 전략을 동시 grid search — 각 전략마다 벡터화/폴백 자동 선택.
    """
    t0 = time.time()
    out = {
        "ok": True,
        "ticker": ticker,
        "period_days": period_days,
        "interval": interval,
        "results": {},        # strategy → vectorized_grid 결과
        "leaderboard": [],    # 전체 top 결과 (strategy + params + sharpe)
    }
    n_vec = 0; n_loop = 0
    for s in strategies:
        g = grids.get(s, {})
        r = vectorized_grid(s, ticker, g,
                             period_days=period_days, interval=interval,
                             fees=fees, init_cash=init_cash, top_n=top_n_per)
        out["results"][s] = r
        if r.get("vectorized"):
            n_vec += 1
        else:
            n_loop += 1
        # leaderboard 누적
        if r.get("ok") and r.get("top"):
            for item in r["top"]:
                out["leaderboard"].append({
                    "strategy": s,
                    "params": item.get("params", {}),
                    "sharpe": item.get("sharpe", 0),
                    "total_return": item.get("total_return", 0),
                    "max_drawdown": item.get("max_drawdown", 0),
                    "n_trades": item.get("n_trades", 0),
                })
    out["leaderboard"].sort(key=lambda x: x["sharpe"], reverse=True)
    out["leaderboard"] = out["leaderboard"][:20]   # 글로벌 top 20
    out["elapsed_sec"] = round(time.time() - t0, 2)
    out["n_vectorized"] = n_vec
    out["n_loop_fallback"] = n_loop
    # 전략별 param 한글 라벨 매핑 (UI 표시용)
    out["param_labels_by_strategy"] = {
        s: _get_param_labels(s) for s in strategies
    }
    return out
