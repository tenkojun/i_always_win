# ==============================================================================
# [04/25] vol.py — EWMA · GJR-GARCH-t MLE · HAR-RV · 언스무딩
# ==============================================================================

"""
jiqtx.vol — 조건부 변동성 엔진.

원본 리포트의 '연환산 표준편차 1개'를 대체한다.
GBM 시뮬레이션이 실패하는 근본 이유는 변동성 클러스터링과 팻테일을 무시하기
때문이다. 여기서는 GJR-GARCH(1,1)-t 를 직접 MLE로 적합해 조건부 변동성 경로와
표준화 잔차를 얻고, 그 잔차를 필터드 히스토리컬 시뮬레이션의 재료로 쓴다.

- EWMA        : RiskMetrics 스타일 (빠른 baseline)
- GJR-GARCH-t : Glosten-Jagannathan-Runkle 비대칭 + Student-t 혁신
- HAR         : Corsi (2009) 이질적 자기회귀. 일/주/월 성분 캐스케이드.
- 언스무딩    : Getmansky-Lo-Makarov (평활화된 NAV/비유동 자산의 샤프 과대 보정)

외부 의존 없음(numpy/scipy만). arch 패키지가 있으면 교차검증에 쓸 수 있다.
"""

import math
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

import numpy as np
from scipy import optimize, stats


# ================================================================ EWMA

def ewma_vol(r: np.ndarray, lam: float = 0.94) -> np.ndarray:
    r = np.asarray(r, float)
    v = np.empty(len(r))
    init = np.nanvar(r[:min(60, len(r))], ddof=1)
    s2 = init if np.isfinite(init) and init > 0 else np.nanvar(r, ddof=1)
    for t in range(len(r)):
        v[t] = math.sqrt(max(s2, 1e-12))
        x = r[t] if np.isfinite(r[t]) else 0.0
        s2 = lam * s2 + (1 - lam) * x * x
    return v


def realized_vol(r: np.ndarray, window: int = 21, ann: int = 252) -> np.ndarray:
    r = np.asarray(r, float)
    n = len(r)
    out = np.full(n, np.nan)
    for t in range(window, n + 1):
        seg = r[t - window:t]
        seg = seg[np.isfinite(seg)]
        if len(seg) > 5:
            out[t - 1] = seg.std(ddof=1) * math.sqrt(ann)
    return out


# ================================================================ GJR-GARCH-t

@dataclass
class GarchFit:
    omega: float
    alpha: float
    gamma: float
    beta: float
    nu: float
    mu: float
    sigma: np.ndarray          # 조건부 표준편차 경로 (일간)
    z: np.ndarray              # 표준화 잔차
    loglik: float
    persistence: float
    converged: bool
    at_boundary: bool
    ann_vol_current: float
    ann_vol_longrun: float
    halflife_days: float

    def forecast(self, horizon: int) -> np.ndarray:
        """조건부 분산 예측 경로 (일간 분산)."""
        s2 = self.sigma[-1] ** 2
        e2 = (self.z[-1] * self.sigma[-1]) ** 2
        neg = 1.0 if self.z[-1] < 0 else 0.0
        out = np.empty(horizon)
        # 1스텝
        s2n = self.omega + (self.alpha + self.gamma * neg) * e2 + self.beta * s2
        out[0] = s2n
        p = self.persistence
        lr = self.omega / max(1 - p, 1e-8)
        for h in range(1, horizon):
            out[h] = lr + p * (out[h - 1] - lr)
        return out


def _gjr_recursion(e: np.ndarray, omega: float, a: float, g: float,
                   b: float) -> Optional[np.ndarray]:
    """GJR 분산 재귀. 파이썬 float 루프가 numpy 인덱싱보다 빠르다."""
    el = e.tolist()
    n = len(el)
    v0 = float(np.var(e, ddof=1))
    s = v0 if v0 > 0 else 1e-6
    out = [s]
    for t in range(1, n):
        p = el[t - 1]
        s = omega + (a + g if p < 0.0 else a) * p * p + b * s
        if s <= 0.0 or s != s or s > 1e12:
            return None
        out.append(s)
    return np.asarray(out)


def _gjr_negll(params: np.ndarray, r: np.ndarray) -> float:
    mu, lw, la, lg, lb, lnu = params
    omega = math.exp(lw)
    # 로지스틱 변환으로 alpha+gamma/2+beta < 1 유지
    a = 1.0 / (1.0 + math.exp(-la)) * 0.30
    g = 1.0 / (1.0 + math.exp(-lg)) * 0.40
    b = 1.0 / (1.0 + math.exp(-lb)) * 0.995
    if a + g / 2.0 + b >= 0.9999:
        return 1e10
    nu = 2.05 + math.exp(lnu)
    if nu > 200:
        return 1e10

    e = r - mu
    n = e.shape[0]
    s2 = _gjr_recursion(e, omega, a, g, b)
    if s2 is None:
        return 1e10
    z2 = e * e / s2
    c = (math.lgamma((nu + 1) / 2) - math.lgamma(nu / 2)
         - 0.5 * math.log(math.pi * (nu - 2)))
    ll = np.sum(c - 0.5 * np.log(s2) - (nu + 1) / 2 * np.log1p(z2 / (nu - 2)))
    return -float(ll) if np.isfinite(ll) else 1e10


def fit_gjr_garch_t(r: np.ndarray, ann: int = 252,
                    scale: float = 100.0) -> GarchFit:
    """
    GJR-GARCH(1,1) with Student-t innovations, MLE.
    수치 안정성을 위해 수익률을 100배 스케일해 적합한다.
    """
    x = np.asarray(r, float)
    x = x[np.isfinite(x)] * scale
    n = len(x)
    if n < 250:
        sd = float(np.std(x, ddof=1)) if n > 10 else 1.0
        sig = np.full(max(n, 1), sd) / scale
        z = (x / scale - np.mean(x / scale)) / max(sd / scale, 1e-9)
        return GarchFit(np.nan, np.nan, np.nan, np.nan, np.nan,
                        float(np.mean(x / scale)) if n else 0.0,
                        sig, z, np.nan, np.nan, False, False,
                        float(sd / scale * math.sqrt(ann)),
                        float(sd / scale * math.sqrt(ann)), np.nan)

    v = np.var(x, ddof=1)
    p0 = np.array([np.mean(x), math.log(v * 0.05), -1.5, -1.5, 2.0, math.log(6.0)])
    best, bestf = None, np.inf
    for jitter in (0.0, 0.4):
        p = p0.copy()
        p[2] += jitter
        p[4] -= jitter
        try:
            res = optimize.minimize(_gjr_negll, p, args=(x,), method="Nelder-Mead",
                                    options={"maxiter": 2500, "xatol": 1e-5,
                                             "fatol": 1e-5})
            if res.fun < bestf:
                best, bestf = res.x, res.fun
        except Exception:
            continue
    if best is None:
        best, bestf = p0, _gjr_negll(p0, x)

    mu, lw, la, lg, lb, lnu = best
    omega = math.exp(lw)
    a = 1.0 / (1.0 + math.exp(-la)) * 0.30
    g = 1.0 / (1.0 + math.exp(-lg)) * 0.40
    b = 1.0 / (1.0 + math.exp(-lb)) * 0.995
    nu = 2.05 + math.exp(lnu)

    e = x - mu
    s2 = _gjr_recursion(e, omega, a, g, b)
    if s2 is None:
        s2 = np.full(n, np.var(e, ddof=1))
    sigma = np.sqrt(s2)
    z = e / sigma

    pers = a + g / 2.0 + b
    at_boundary = pers > 0.995
    if at_boundary:
        # IGARCH 근방: 장기평균이 정의되지 않음 → 표본 분산으로 대체 후 플래그
        lr_var = float(np.var(e, ddof=1))
    else:
        lr_var = omega / max(1 - pers, 1e-8)
    hl = math.log(0.5) / math.log(max(pers, 1e-8)) if 0 < pers < 0.9999 else np.inf

    return GarchFit(
        omega=omega / scale ** 2, alpha=a, gamma=g, beta=b, nu=nu,
        mu=mu / scale,
        sigma=sigma / scale, z=z, loglik=-bestf, persistence=pers,
        converged=bestf < 1e9, at_boundary=bool(at_boundary),
        ann_vol_current=float(sigma[-1] / scale * math.sqrt(ann)),
        ann_vol_longrun=float(math.sqrt(lr_var) / scale * math.sqrt(ann)),
        halflife_days=float(hl),
    )


# ================================================================ HAR

@dataclass
class HARFit:
    coef: np.ndarray            # [const, daily, weekly, monthly]
    r2: float
    forecast_ann: float
    resid_std: float


def fit_har(r: np.ndarray, ann: int = 252,
            windows: Tuple[int, int, int] = (1, 5, 22)) -> HARFit:
    """
    HAR-RV (Corsi 2009). 일봉만 있으므로 RV 프록시로 |r| 또는 r²를 쓴다.
    (진짜 실현변동성에는 인트라데이 데이터가 필요 — 한계 명시)
    """
    r = np.asarray(r, float)
    rv = r ** 2
    d, w, m = windows
    n = len(rv)
    if n < m + 60:
        return HARFit(np.full(4, np.nan), np.nan, np.nan, np.nan)

    def roll(a, k):
        out = np.full(len(a), np.nan)
        cs = np.nancumsum(np.nan_to_num(a))
        for t in range(k, len(a) + 1):
            out[t - 1] = (cs[t - 1] - (cs[t - k - 1] if t - k - 1 >= 0 else 0.0)) / k
        return out

    xd, xw, xm = roll(rv, d), roll(rv, w), roll(rv, m)
    y = rv[1:]
    X = np.column_stack([xd[:-1], xw[:-1], xm[:-1]])
    ok = np.isfinite(y) & np.isfinite(X).all(axis=1)
    if ok.sum() < 60:
        return HARFit(np.full(4, np.nan), np.nan, np.nan, np.nan)
    Xc = np.column_stack([np.ones(ok.sum()), X[ok]])
    b, *_ = np.linalg.lstsq(Xc, y[ok], rcond=None)
    yhat = Xc @ b
    ss_res = float(np.sum((y[ok] - yhat) ** 2))
    ss_tot = float(np.sum((y[ok] - y[ok].mean()) ** 2))
    r2 = 1 - ss_res / ss_tot if ss_tot > 0 else np.nan
    last = np.array([1.0, xd[-1], xw[-1], xm[-1]])
    f = float(b @ last)
    fann = math.sqrt(max(f, 1e-12) * ann)
    return HARFit(b, r2, fann, float(np.std(y[ok] - yhat, ddof=1)))


# ================================================================ 언스무딩

def unsmooth_returns(r: np.ndarray, order: int = 2) -> Dict[str, object]:
    """
    Getmansky-Lo-Makarov 계열 언스무딩.
    평활화된 수익률(뮤추얼펀드 NAV, 비유동 소형주)은 변동성을 과소, 샤프를 과대
    평가한다. MA(q) 구조를 추정해 원래 수익률을 복원한다.
    """
    r = np.asarray(r, float)
    r = r[np.isfinite(r)]
    n = len(r)
    if n < 120:
        return {"applied": False, "theta": None, "smoothing_index": np.nan,
                "vol_inflation": 1.0, "unsmoothed": r}
    ac = [float(np.corrcoef(r[:-k], r[k:])[0, 1]) for k in range(1, order + 1)]
    if all(abs(a) < 0.10 for a in ac):
        return {"applied": False, "theta": None,
                "smoothing_index": float(np.sum(np.square([1.0]))),
                "vol_inflation": 1.0, "unsmoothed": r}
    # 간이 MA(q) 적합: theta 를 자기상관에서 근사
    theta = np.array([1.0] + [max(min(a, 0.9), -0.9) for a in ac])
    theta = theta / theta.sum()
    xi = float(np.sum(theta ** 2))                 # smoothing index
    # 역필터 (AR 근사)
    u = np.empty(n)
    u[:order] = r[:order]
    for t in range(order, n):
        u[t] = (r[t] - sum(theta[k] * u[t - k] for k in range(1, order + 1))) / theta[0]
    infl = float(np.std(u, ddof=1) / max(np.std(r, ddof=1), 1e-12))
    return {"applied": True, "theta": theta, "smoothing_index": xi,
            "vol_inflation": infl, "unsmoothed": u}


# ================================================================ 통합

@dataclass
class VolProfile:
    ewma_ann: float
    realized_21d_ann: float
    realized_63d_ann: float
    garch: GarchFit
    har: HARFit
    vol_of_vol: float
    vol_percentile: float          # 자기 이력 대비 현재 위치
    leverage_effect: float         # GJR gamma
    tail_nu: float                 # t 자유도 (작을수록 팻테일)
    unsmoothing: Dict


def vol_profile(r: np.ndarray, ann: int = 252) -> VolProfile:
    r = np.asarray(r, float)
    e = ewma_vol(r) * math.sqrt(ann)
    rv21 = realized_vol(r, 21, ann)
    rv63 = realized_vol(r, 63, ann)
    g = fit_gjr_garch_t(r, ann=ann)
    h = fit_har(r, ann=ann)
    gv = g.sigma * math.sqrt(ann) if len(g.sigma) else np.array([np.nan])
    vov = float(np.nanstd(np.diff(np.log(np.where(gv > 0, gv, np.nan)))) * math.sqrt(ann)) \
        if len(gv) > 30 else np.nan
    cur = gv[-1] if len(gv) else np.nan
    pct = float(np.nanmean(gv <= cur)) if len(gv) > 30 and np.isfinite(cur) else np.nan
    return VolProfile(
        ewma_ann=float(e[-1]) if len(e) else np.nan,
        realized_21d_ann=float(rv21[-1]) if len(rv21) else np.nan,
        realized_63d_ann=float(rv63[-1]) if len(rv63) else np.nan,
        garch=g, har=h, vol_of_vol=vov, vol_percentile=pct,
        leverage_effect=g.gamma, tail_nu=g.nu,
        unsmoothing=unsmooth_returns(r),
    )
