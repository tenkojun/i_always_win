"""
vbt_indicators.py — IndicatorFactory 래핑 커스텀 인디케이터
=============================================================
vectorbt Param broadcasting + 선택적 numba JIT 적용.

ZScore      : 롤링 Z-score 평균회귀
ROCThresh   : Rate-of-Change 모멘텀
PseudoCVDDiv: 가상 CVD 다이버전스 (close-only)
CVDDiv      : 실제 CVD 다이버전스 (close + delta 필요)

사용:
    from engine.strategy.vbt_indicators import ZScore, PseudoCVDDiv

    # 단일 파라미터
    ind = ZScore.run(close, window=20, threshold=2.0)
    entries = ind.sig_entry.squeeze().astype(bool)
    exits   = ind.sig_exit.squeeze().astype(bool)

    # 그리드 서치 (param_product=True → 카테시안 곱)
    ind = ZScore.run(close, window=[10,20,40], threshold=[1.5,2.0], param_product=True)
    # ind.sig_entry : shape (T, 6) DataFrame — 각 열이 하나의 파라미터 조합
"""
from __future__ import annotations

import numpy as np
import pandas as pd

_vbt_obj = None


def _vbt():
    global _vbt_obj
    if _vbt_obj is None:
        import vectorbt as vbt
        _vbt_obj = vbt
    return _vbt_obj


# ── numba 선택 ────────────────────────────────────────────────
try:
    from numba import njit as _njit
    _HAS_NUMBA = True
except ImportError:
    _HAS_NUMBA = False

    def _njit(*args, **kwargs):
        if args and callable(args[0]):
            return args[0]
        def _wrap(fn):
            return fn
        return _wrap


# ══════════════════════════════════════════════════════════════
#  ZScore — 롤링 Z-score 평균회귀
#    sig_entry: z < -threshold (과매도)
#    sig_exit : z > 0          (평균 복귀)
# ══════════════════════════════════════════════════════════════
@_njit(cache=True)
def _zscore_nb(close, window, threshold):
    n = len(close)
    z         = np.full(n, np.nan)
    sig_entry = np.zeros(n)
    sig_exit  = np.zeros(n)
    w   = int(window)
    thr = float(threshold)
    for i in range(w - 1, n):
        sl = close[i - w + 1: i + 1]
        mean = np.mean(sl)
        std  = np.std(sl)
        if std > 1e-9:
            zi           = (close[i] - mean) / std
            z[i]         = zi
            sig_entry[i] = 1.0 if zi < -thr else 0.0
            sig_exit[i]  = 1.0 if zi >  0.0 else 0.0
    return z, sig_entry, sig_exit


def _zscore_apply(close, window, threshold):
    if _HAS_NUMBA:
        return _zscore_nb(close, int(window), float(threshold))
    s  = pd.Series(close)
    m  = s.rolling(int(window)).mean()
    sd = s.rolling(int(window)).std().replace(0, np.nan)
    z  = ((s - m) / sd).fillna(0).values
    return z, (z < -float(threshold)).astype(float), (z > 0.0).astype(float)


# ══════════════════════════════════════════════════════════════
#  ROCThresh — Rate-of-Change 모멘텀
#    sig_entry: roc > threshold
#    sig_exit : roc < 0
# ══════════════════════════════════════════════════════════════
@_njit(cache=True)
def _roc_nb(close, window, threshold):
    n = len(close)
    roc       = np.full(n, np.nan)
    sig_entry = np.zeros(n)
    sig_exit  = np.zeros(n)
    w   = int(window)
    thr = float(threshold)
    for i in range(w, n):
        prev = close[i - w]
        if prev > 1e-9:
            r            = (close[i] - prev) / prev
            roc[i]       = r
            sig_entry[i] = 1.0 if r >  thr else 0.0
            sig_exit[i]  = 1.0 if r <  0.0 else 0.0
    return roc, sig_entry, sig_exit


def _roc_apply(close, window, threshold):
    if _HAS_NUMBA:
        return _roc_nb(close, int(window), float(threshold))
    r = pd.Series(close).pct_change(int(window)).fillna(0).values
    return r, (r > float(threshold)).astype(float), (r < 0.0).astype(float)


# ══════════════════════════════════════════════════════════════
#  PseudoCVDDiv — 가상 CVD 다이버전스 (close-only)
#    close 차분 부호를 누적 → 가짜 CVD
#    가격 N봉 신저 + CVD는 신저 아님 → 강세 다이버전스 → sig_entry
#    가격 N봉 신고 + CVD는 신고 아님 → 약세 다이버전스 → sig_exit
# ══════════════════════════════════════════════════════════════
@_njit(cache=True)
def _pseudo_cvd_nb(close, lookback):
    n   = len(close)
    cvd = np.zeros(n)
    for i in range(1, n):
        diff = close[i] - close[i - 1]
        cvd[i] = cvd[i - 1] + (1.0 if diff > 0.0 else (-1.0 if diff < 0.0 else 0.0))

    sig_entry = np.zeros(n)
    sig_exit  = np.zeros(n)
    in_pos    = False
    lb = int(lookback)
    for i in range(lb, n):
        p_win = close[i - lb: i + 1]
        c_win = cvd[i - lb: i + 1]
        p_lo  = np.min(p_win);  p_hi = np.max(p_win)
        c_lo  = np.min(c_win);  c_hi = np.max(c_win)
        if not in_pos and close[i] == p_lo and cvd[i] > c_lo:
            sig_entry[i] = 1.0
            in_pos = True
        elif in_pos and close[i] == p_hi and cvd[i] < c_hi:
            sig_exit[i] = 1.0
            in_pos = False
    return sig_entry, sig_exit


def _pseudo_cvd_apply(close, lookback):
    if _HAS_NUMBA:
        return _pseudo_cvd_nb(close, int(lookback))
    pseudo = np.sign(np.diff(close, prepend=close[0]))
    cvd    = np.cumsum(pseudo)
    lb = int(lookback)
    n  = len(close)
    sig_entry = np.zeros(n)
    sig_exit  = np.zeros(n)
    in_pos = False
    for i in range(lb, n):
        p_win = close[i - lb: i + 1]
        c_win = cvd[i - lb: i + 1]
        if not in_pos and close[i] == p_win.min() and cvd[i] > c_win.min():
            sig_entry[i] = 1.0
            in_pos = True
        elif in_pos and close[i] == p_win.max() and cvd[i] < c_win.max():
            sig_exit[i] = 1.0
            in_pos = False
    return sig_entry, sig_exit


# ══════════════════════════════════════════════════════════════
#  CVDDiv — 실제 CVD 다이버전스 (close + delta 입력 필요)
#    +1.0: 매수자 고갈 (가격↑·델타↓) → 하락 반전
#    -1.0: 매도자 고갈 (가격↓·델타↑) → 상승 반전
# ══════════════════════════════════════════════════════════════
@_njit(cache=True)
def _cvd_div_nb(close, delta, lookback, thresh_z):
    n  = len(close)
    lb = int(lookback)
    pr_chg = np.zeros(n)
    de_sum = np.zeros(n)
    for i in range(lb, n):
        prev       = close[i - lb]
        pr_chg[i]  = (close[i] - prev) / (prev + 1e-9)
        s = 0.0
        for j in range(i - lb, i + 1):
            s += delta[j]
        de_sum[i] = s

    z_win  = 60
    tz     = float(thresh_z)
    signal = np.zeros(n)
    for i in range(z_win + lb, n):
        pr_sl = pr_chg[i - z_win: i]
        de_sl = de_sum[i - z_win: i]
        pm = np.mean(pr_sl);  dm = np.mean(de_sl)
        ps = np.std(pr_sl) + 1e-9
        ds = np.std(de_sl) + 1e-9
        pz = (pr_chg[i] - pm) / ps
        dz = (de_sum[i] - dm) / ds
        if pz > tz and dz < -tz:
            signal[i] = 1.0
        elif pz < -tz and dz > tz:
            signal[i] = -1.0
    return signal


def _cvd_div_apply(close, delta, lookback, thresh_z):
    if _HAS_NUMBA:
        return _cvd_div_nb(close, delta, int(lookback), float(thresh_z))
    lb  = int(lookback)
    scl = pd.Series(close)
    sde = pd.Series(delta)
    pr_chg = scl.pct_change(lb).fillna(0).values
    de_sum = sde.rolling(lb + 1, min_periods=1).sum().fillna(0).values

    def _z(arr, w=60):
        s  = pd.Series(arr)
        m  = s.rolling(w, min_periods=10).mean()
        sd = s.rolling(w, min_periods=10).std().replace(0, np.nan)
        return ((s - m) / sd).fillna(0).values

    pz = _z(pr_chg)
    dz = _z(de_sum)
    tz = float(thresh_z)
    return np.where((pz > tz) & (dz < -tz),  1.0,
           np.where((pz < -tz) & (dz > tz), -1.0, 0.0))


# ══════════════════════════════════════════════════════════════
#  IndicatorFactory 지연 등록
#  — vbt는 무거우므로 첫 접근 시에만 빌드
# ══════════════════════════════════════════════════════════════
_FACTORIES: dict = {}


def _build():
    v = _vbt()
    _FACTORIES["ZScore"] = v.IndicatorFactory(
        class_name="ZScore",
        short_name="zscore",
        input_names=["close"],
        param_names=["window", "threshold"],
        output_names=["z", "sig_entry", "sig_exit"],
    ).with_apply_func(_zscore_apply, takes_1d=True)

    _FACTORIES["ROCThresh"] = v.IndicatorFactory(
        class_name="ROCThresh",
        short_name="roc_thresh",
        input_names=["close"],
        param_names=["window", "threshold"],
        output_names=["roc", "sig_entry", "sig_exit"],
    ).with_apply_func(_roc_apply, takes_1d=True)

    _FACTORIES["PseudoCVDDiv"] = v.IndicatorFactory(
        class_name="PseudoCVDDiv",
        short_name="pcvd",
        input_names=["close"],
        param_names=["lookback"],
        output_names=["sig_entry", "sig_exit"],
    ).with_apply_func(_pseudo_cvd_apply, takes_1d=True)

    _FACTORIES["CVDDiv"] = v.IndicatorFactory(
        class_name="CVDDiv",
        short_name="cvd_div",
        input_names=["close", "delta"],
        param_names=["lookback", "thresh_z"],
        output_names=["signal"],
    ).with_apply_func(_cvd_div_apply, takes_1d=True)


def __getattr__(name: str):
    if name in ("ZScore", "ROCThresh", "PseudoCVDDiv", "CVDDiv"):
        if not _FACTORIES:
            _build()
        return _FACTORIES[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
