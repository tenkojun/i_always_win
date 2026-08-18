# ==============================================================================
# [08/25] factors.py — 팩터 라우터 · Kalman 시변베타 · 델타 패널
# ==============================================================================

"""
jiqtx.factors — 자산군 인지 팩터 라우터 + 델타 패널.

원본 리포트의 결함
------------------
GLD에 주식형 FF 회귀를 돌려 R²=2%. 잔차 98%를 "종목 고유위험"으로 해석했다.
실제로는 '누락 변수'다. 그리고 이 상황에서 알파 +10.3%를 계산했다.
R²가 2%인 회귀의 알파는 해석 불가능한 잔차 평균일 뿐이다.

본 모듈이 하는 일
-----------------
1. 자산군 prior로 후보 팩터를 제한
2. Elastic-Net으로 실제 선택 (경제적 메커니즘 없는 팩터는 애초에 후보에 없음)
3. R²가 자산군 기대밴드를 벗어나면 → 팩터 미스매칭 플래그, 알파 해석 차단
4. 모든 베타를 시변으로 추정 (rolling + Kalman)
   근거: 금-10년TIPS R²가 2005-2021 약 84% → 2022-2023 3% → 2024+ 7%로 붕괴.
   정적 베타는 이런 구조 변화를 놓친다.
5. 델타 패널: 표준 충격당 손익 + 분위회귀 하방 베타 + 꼬리 의존성
"""

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd


try:
    from sklearn.linear_model import ElasticNetCV
    _HAS_SK = True
except Exception:                                   # pragma: no cover
    _HAS_SK = False

# ── 패키지 내부 의존 ──────────────────────────────────────────
from .config import FACTOR_LEGS
from .statcore import newey_west_se, quantile_regression, tail_dependence



# ---------------------------------------------------------------- 팩터 패널

def build_factor_panel(index: pd.DatetimeIndex,
                       macro: pd.DataFrame,
                       proxy_prices: Dict[str, pd.Series],
                       wanted: List[str]) -> pd.DataFrame:
    """
    후보 팩터명 -> 일간 '충격' 시계열.
    - 수익률형 프록시(SPY 등): 로그수익률
    - 수준형 매크로(금리, OAS, VIX): 1차 차분 (수준이 아니라 변화가 충격)
    - 지수형(달러지수): 로그수익률
    """
    out = pd.DataFrame(index=index)
    level_diff = {"real_yield_10y", "real_yield_5y", "breakeven_10y",
                  "nominal_10y", "nominal_2y", "curve_2s10s",
                  "hy_oas", "ig_oas", "vix", "gpr"}
    logret = {"broad_dollar", "wti", "eurusd"}

    def _ret(ticker):
        """티커 종가 → 로그수익률. 없으면 None."""
        s = proxy_prices.get(ticker)
        if s is None:
            return None
        s = s.reindex(index).ffill().astype(float)
        return np.log(s.where(s > 0)).diff()

    for f in wanted:
        # (1) 팩터명으로 시세가 바로 주어진 경우 — 합성 데이터/검증 스위트
        if f in proxy_prices and proxy_prices[f] is not None:
            s = proxy_prices[f].reindex(index).ffill().astype(float)
            out[f] = np.log(s.where(s > 0)).diff()
        # (2) 롱숏 다리로 정의된 팩터 — 실제 운용 경로
        elif f in FACTOR_LEGS:
            long_tk, short_tk = FACTOR_LEGS[f]
            rl = _ret(long_tk)
            if rl is None:
                continue
            if short_tk is None:
                out[f] = rl
            else:
                rs = _ret(short_tk)
                # 숏 다리를 못 구하면 스프레드가 아니라 롱온리가 된다.
                # 그건 이 팩터를 시장으로 만드는 짓이라, 차라리 빼는 게 낫다.
                if rs is None:
                    continue
                out[f] = rl - rs
        elif macro is not None and f in macro.columns:
            s = macro[f].reindex(index).ffill().astype(float)
            if f in logret:
                out[f] = np.log(s.where(s > 0)).diff()
            elif f in level_diff:
                out[f] = s.diff()
            else:
                out[f] = s.pct_change()
    return out


# ---------------------------------------------------------------- 팩터 회귀

@dataclass
class FactorModel:
    used_factors: List[str]
    coefs: Dict[str, float]
    tstats: Dict[str, float]
    alpha_ann: float
    alpha_t: float
    r2: float
    r2_band: Tuple[float, float]
    mismatch: bool
    mismatch_note: str
    n_obs: int
    resid: np.ndarray
    systematic_share: float
    interpretation_allowed: bool


def fit_factor_model(r_asset: np.ndarray, F: pd.DataFrame,
                     r2_band: Tuple[float, float], ann: int = 252,
                     min_abs_coef: float = 1e-8) -> FactorModel:
    """Elastic-Net으로 팩터 선택 후 선택된 팩터로 OLS(HAC) 재추정."""
    y = np.asarray(r_asset, float)
    X = F.values.astype(float)
    names = list(F.columns)
    ok = np.isfinite(y) & np.isfinite(X).all(axis=1)
    y_, X_ = y[ok], X[ok]
    n = len(y_)
    if n < 120 or X_.shape[1] == 0:
        return FactorModel([], {}, {}, np.nan, np.nan, np.nan, r2_band, True,
                           "표본 부족 또는 팩터 없음", n, np.array([]), np.nan, False)

    # 1) 선택
    if _HAS_SK and X_.shape[1] > 1:
        sx = X_.std(axis=0, ddof=1)
        sx = np.where(sx > 0, sx, 1.0)
        try:
            en = ElasticNetCV(l1_ratio=[0.2, 0.5, 0.8, 0.95], cv=5,
                              max_iter=8000, random_state=0)
            en.fit(X_ / sx, y_)
            keep = [names[i] for i in range(len(names))
                    if abs(en.coef_[i]) > min_abs_coef]
        except Exception:
            keep = names
    else:
        keep = names
    if not keep:
        keep = [names[int(np.argmax([abs(np.corrcoef(X_[:, i], y_)[0, 1])
                                     for i in range(X_.shape[1])]))]]

    # 2) OLS + Newey-West
    Xk = F[keep].values.astype(float)[ok]
    b, se = newey_west_se(Xk, y_, lags=5)
    yhat = np.column_stack([np.ones(n), Xk]) @ b
    resid = y_ - yhat
    ss_tot = float(np.sum((y_ - y_.mean()) ** 2))
    r2 = 1.0 - float(np.sum(resid ** 2)) / ss_tot if ss_tot > 0 else np.nan

    coefs = {k: float(b[i + 1]) for i, k in enumerate(keep)}
    tst = {k: float(b[i + 1] / se[i + 1]) if se[i + 1] > 0 else np.nan
           for i, k in enumerate(keep)}
    alpha_ann = float(b[0] * ann)
    alpha_t = float(b[0] / se[0]) if se[0] > 0 else np.nan

    lo, hi = r2_band
    mismatch = (not np.isfinite(r2)) or (r2 < lo * 0.55)
    if mismatch:
        note = (f"R²={r2:.1%} 가 자산군 기대밴드 [{lo:.0%}, {hi:.0%}] 하단을 "
                f"크게 밑돎 → 팩터 미스매칭. 잔차 {1-r2:.0%}는 '고유위험'이 아니라 "
                f"'누락 변수'로 해석해야 하며, 알파 추정치는 무효.")
    elif r2 > hi:
        note = (f"R²={r2:.1%} 가 밴드 상단 초과 → 사실상 프록시 복제 관계. "
                f"독립적 알파 원천으로 보기 어려움.")
    else:
        note = f"R²={r2:.1%} — 기대밴드 [{lo:.0%}, {hi:.0%}] 내. 팩터 해석 유효."

    return FactorModel(
        used_factors=keep, coefs=coefs, tstats=tst,
        alpha_ann=alpha_ann, alpha_t=alpha_t, r2=r2, r2_band=r2_band,
        mismatch=bool(mismatch), mismatch_note=note, n_obs=n, resid=resid,
        systematic_share=float(r2) if np.isfinite(r2) else np.nan,
        interpretation_allowed=not mismatch,
    )


# ---------------------------------------------------------------- 시변 베타

@dataclass
class TimeVaryingBeta:
    factor: str
    rolling: pd.Series
    kalman: pd.Series
    beta_now: float
    beta_mean: float
    beta_std: float
    stability_cv: float           # 변동계수 — 클수록 헤지에 쓰면 안 됨
    r2_rolling: pd.Series
    r2_now: float
    r2_collapse: bool             # 최근 R²가 과거 대비 급락
    collapse_note: str


def kalman_beta(y: np.ndarray, x: np.ndarray, q: float = 1e-5,
                r_obs: Optional[float] = None) -> np.ndarray:
    """시변 계수 칼만 필터 (랜덤워크 상태). y_t = β_t x_t + ε_t"""
    y = np.asarray(y, float)
    x = np.asarray(x, float)
    n = len(y)
    beta = np.full(n, np.nan)
    ok = np.isfinite(y) & np.isfinite(x)
    if ok.sum() < 60:
        return beta
    if r_obs is None:
        r_obs = float(np.nanvar(y[ok], ddof=1)) * 0.9
    b, P = 0.0, 1.0
    for t in range(n):
        if not ok[t]:
            beta[t] = b
            continue
        P += q
        S = x[t] * P * x[t] + r_obs
        K = P * x[t] / S if S > 0 else 0.0
        b = b + K * (y[t] - x[t] * b)
        P = (1 - K * x[t]) * P
        beta[t] = b
    return beta


def time_varying_beta(r_asset: np.ndarray, r_factor: np.ndarray,
                      index: pd.DatetimeIndex, factor: str,
                      window: int = 252, stride: int = 1) -> TimeVaryingBeta:
    y, x = np.asarray(r_asset, float), np.asarray(r_factor, float)
    n = len(y)
    roll = np.full(n, np.nan)
    r2r = np.full(n, np.nan)
    steps = list(range(window, n + 1, max(stride, 1)))
    if steps and steps[-1] != n:
        steps.append(n)
    for t in steps:
        yy, xx = y[t - window:t], x[t - window:t]
        m = np.isfinite(yy) & np.isfinite(xx)
        if m.sum() < window * 0.6:
            continue
        vx = float(np.var(xx[m], ddof=1))
        if vx <= 0:
            continue
        roll[t - 1] = float(np.cov(xx[m], yy[m], ddof=1)[0, 1] / vx)
        r2r[t - 1] = float(np.corrcoef(xx[m], yy[m])[0, 1] ** 2)

    kb = kalman_beta(y, x)
    rs = pd.Series(roll, index=index)
    if stride > 1:
        rs = rs.ffill()
    ks = pd.Series(kb, index=index)
    r2s = pd.Series(r2r, index=index)

    bn = float(ks.iloc[-1]) if np.isfinite(ks.iloc[-1]) else float(rs.dropna().iloc[-1]) \
        if rs.notna().any() else np.nan
    bm = float(rs.mean(skipna=True))
    bs = float(rs.std(skipna=True))
    cv = float(bs / abs(bm)) if bm and abs(bm) > 1e-9 else np.nan

    r2_now = float(r2s.dropna().iloc[-1]) if r2s.notna().any() else np.nan
    hist = r2s.dropna()
    r2_hist = float(hist.iloc[:-63].median()) if len(hist) > 130 else np.nan
    collapse = bool(np.isfinite(r2_now) and np.isfinite(r2_hist)
                    and r2_hist > 0.15 and r2_now < r2_hist * 0.35)
    note = ""
    if collapse:
        note = (f"최근 R² {r2_now:.1%} vs 과거 중앙값 {r2_hist:.1%} → 구조 변화 경보. "
                f"팩터 관계가 붕괴 중일 수 있음. 정적 베타 사용 금지.")
    return TimeVaryingBeta(factor, rs, ks, bn, bm, bs, cv, r2s, r2_now,
                           collapse, note)


# ---------------------------------------------------------------- 델타 패널

@dataclass
class FactorDelta:
    factor: str
    shock: float
    shock_label: str
    beta_static: float
    beta_now: float
    beta_stability_cv: float
    beta_q10: float               # 하방 분위 베타
    beta_q50: float
    downside_beta: float          # 팩터 하위구간 조건부 베타
    delta_pct: float              # 표준충격 시 자산 손익(%)
    delta_pct_downside: float     # 하방 베타 적용 시
    t_stat: float
    lambda_lower: float           # 꼬리 의존성
    corr_all: float
    corr_lower_tail: float
    r2_now: float
    r2_collapse: bool
    note: str


def factor_delta_panel(r_asset: np.ndarray, F: pd.DataFrame,
                       shocks: Dict[str, float],
                       index: pd.DatetimeIndex,
                       fast: bool = False) -> pd.DataFrame:
    """
    헤지펀드형 델타 패널.
    '샤프 0.96' 같은 요약통계가 아니라 "무엇이 X만큼 움직이면 얼마를 잃는가".
    """
    rows: List[FactorDelta] = []
    y = np.asarray(r_asset, float)

    labels = {
        "real_yield_10y": "실질금리 +100bp", "nominal_10y": "명목10년 +100bp",
        "broad_dollar": "광의달러 +5%", "mkt_excess": "주식시장 -10%",
        "vix": "VIX +10pt", "hy_oas": "HY OAS +100bp", "ig_oas": "IG OAS +100bp",
        "wti": "WTI +20%", "breakeven_10y": "기대인플레 +50bp",
        "gpr": "지정학위험 +1σ", "curve_2s10s": "커브 +100bp",
        "crypto_mkt": "BTC -30%",
    }
    default_shock = {
        "real_yield_10y": 1.0, "nominal_10y": 1.0, "nominal_2y": 1.0,
        "curve_2s10s": 1.0, "breakeven_10y": 0.5, "hy_oas": 1.0, "ig_oas": 1.0,
        "vix": 10.0, "broad_dollar": 0.05, "mkt_excess": -0.10, "wti": 0.20,
        "gpr": 1.0, "crypto_mkt": -0.30,
    }

    for f in F.columns:
        x = F[f].values.astype(float)
        m = np.isfinite(y) & np.isfinite(x)
        if m.sum() < 200:
            continue
        vx = float(np.var(x[m], ddof=1))
        if vx <= 0:
            continue
        b_static = float(np.cov(x[m], y[m], ddof=1)[0, 1] / vx)
        b_ols, se = newey_west_se(x[m], y[m], lags=5)
        tstat = float(b_ols[1] / se[1]) if se[1] > 0 else np.nan

        tvb = time_varying_beta(y, x, index, f, stride=5 if fast else 1)

        if fast:
            # 리플레이/배치용: 분위회귀(Powell)는 비싸므로 생략.
            # 하방 베타는 조건부 회귀로 대체되며 델타 결론은 바뀌지 않는다.
            b_q10 = b_q50 = np.nan
        else:
            q10 = quantile_regression(x[m], y[m], 0.10)
            q50 = quantile_regression(x[m], y[m], 0.50)
            b_q10 = float(q10[1]) if len(q10) > 1 and np.isfinite(q10[1]) else np.nan
            b_q50 = float(q50[1]) if len(q50) > 1 and np.isfinite(q50[1]) else np.nan

        # 다운사이드 베타: 팩터가 하위 30% 구간일 때
        thr = np.quantile(x[m], 0.30)
        dm = m.copy()
        dm[m] = x[m] <= thr
        if dm.sum() > 60:
            vxd = float(np.var(x[dm], ddof=1))
            b_down = float(np.cov(x[dm], y[dm], ddof=1)[0, 1] / vxd) if vxd > 0 else np.nan
        else:
            b_down = np.nan

        td = tail_dependence(x[m], y[m])

        shock = shocks.get(f, default_shock.get(f, 1.0))
        b_use = tvb.beta_now if np.isfinite(tvb.beta_now) else b_static
        delta = b_use * shock
        delta_dn = (b_down if np.isfinite(b_down) else b_use) * shock

        notes = []
        if tvb.r2_collapse:
            notes.append("R² 붕괴 경보")
        if np.isfinite(tvb.stability_cv) and tvb.stability_cv > 0.8:
            notes.append("β 불안정 — 헤지 사용 부적합")
        if np.isfinite(b_down) and np.isfinite(b_static) and abs(b_static) > 1e-6 \
                and b_down / b_static > 1.5:
            notes.append("하방에서 베타 확대 (비대칭 노출)")

        rows.append(FactorDelta(
            factor=f, shock=shock, shock_label=labels.get(f, f"{f} +1단위"),
            beta_static=b_static, beta_now=tvb.beta_now,
            beta_stability_cv=tvb.stability_cv, beta_q10=b_q10, beta_q50=b_q50,
            downside_beta=b_down, delta_pct=delta, delta_pct_downside=delta_dn,
            t_stat=tstat, lambda_lower=td["lambda_lower"],
            corr_all=td["corr_all"], corr_lower_tail=td["corr_lower_tail"],
            r2_now=tvb.r2_now, r2_collapse=tvb.r2_collapse,
            note="; ".join(notes),
        ))

    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame([r.__dict__ for r in rows])
    return df.sort_values("delta_pct", key=lambda s: s.abs(), ascending=False)


# ---------------------------------------------------------------- 롤 수익 분해

def roll_yield_decomposition(etf_ret: np.ndarray, spot_ret: np.ndarray,
                             ann: int = 252) -> Dict[str, float]:
    """
    원자재/변동성 ETP의 구조적 롤 손익 분해.
    USO 등은 콘탱고에서 롤 손실로 구조적 하락 → 현물 프록시로 쓰면 안 된다.
    """
    e, s = np.asarray(etf_ret, float), np.asarray(spot_ret, float)
    m = np.isfinite(e) & np.isfinite(s)
    if m.sum() < 250:
        return {"roll_drag_ann": np.nan, "tracking_beta": np.nan,
                "tracking_r2": np.nan, "note": "표본 부족"}
    vx = float(np.var(s[m], ddof=1))
    b = float(np.cov(s[m], e[m], ddof=1)[0, 1] / vx) if vx > 0 else np.nan
    resid_mean = float(np.mean(e[m] - b * s[m])) * ann
    r2 = float(np.corrcoef(s[m], e[m])[0, 1] ** 2)
    return {"roll_drag_ann": resid_mean, "tracking_beta": b, "tracking_r2": r2,
            "note": ("연 %.1f%% 의 구조적 롤 %s 추정" %
                     (abs(resid_mean) * 100, "손실" if resid_mean < 0 else "이익"))}
