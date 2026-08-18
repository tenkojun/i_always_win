# ==============================================================================
# [06/25] simulate.py — 드리프트 사후분포 · FHS · GPD 꼬리 · 레버리지 경로
# ==============================================================================

"""
jiqtx.simulate — GBM을 대체하는 4계층 시뮬레이션 엔진.

원본 리포트의 결함
------------------
GBM으로 3,000회, 드리프트 = 최근 실현수익 +26.2%, 상승확률 87%.
그런데 드리프트의 표준오차는 σ/√T = 28.35%p 이므로 95% 구간이
[-29.4%, +81.8%] 이다. 즉 "+26.2%"는 통계적으로 "모른다"와 구별되지 않는다.
이 불확실성을 적분하면 상승확률은 78% → 71%로, 장기앵커로 축소하면
56~60%로 붕괴한다.

4계층 구조
----------
L1 파라미터 불확실성 : 드리프트 사후분포 (shrinkage prior) 에서 표집
L2 변동성 동학       : GJR-GARCH(1,1)-t 조건부 분산 경로
L3 경로 생성         : 표준화 잔차를 정상 부트스트랩으로 재샘플 (FHS)
L4 꼬리              : 잔차 꼬리에 GPD(POT) 적합 → 조건부 EVT (McNeil-Frey 2000)
   레짐              : 현재 레짐 사후확률로 시나리오 가중

산출물
------
단일 '상승확률'이 아니라 fan chart + 불확실성 분해(시장 변동성 vs 우리의 무지).
"""

import math
from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple

import numpy as np
import pandas as pd
from scipy import stats

# ── 패키지 내부 의존 ──────────────────────────────────────────
from .vol import GarchFit




# ---------------------------------------------------------------- 꼬리 적합

@dataclass
class TailFit:
    threshold_lo: float
    xi_lo: float
    beta_lo: float
    threshold_hi: float
    xi_hi: float
    beta_hi: float
    n_exceed_lo: int
    n_exceed_hi: int
    ok: bool


def fit_gpd_tails(z: np.ndarray, q: float = 0.05) -> TailFit:
    """표준화 잔차의 양측 꼬리에 GPD(POT) 적합."""
    z = np.asarray(z, float)
    z = z[np.isfinite(z)]
    if len(z) < 300:
        return TailFit(*([np.nan] * 6), 0, 0, False)
    u_lo = float(np.quantile(z, q))
    u_hi = float(np.quantile(z, 1 - q))
    ex_lo = u_lo - z[z < u_lo]
    ex_hi = z[z > u_hi] - u_hi
    try:
        xi_l, _, b_l = stats.genpareto.fit(ex_lo, floc=0)
        xi_h, _, b_h = stats.genpareto.fit(ex_hi, floc=0)
    except Exception:
        return TailFit(u_lo, np.nan, np.nan, u_hi, np.nan, np.nan,
                       len(ex_lo), len(ex_hi), False)
    return TailFit(u_lo, float(xi_l), float(b_l), u_hi, float(xi_h), float(b_h),
                   len(ex_lo), len(ex_hi), True)


def _sample_with_evt(z_pool: np.ndarray, tail: TailFit, size: int,
                     rng: np.random.Generator, q: float = 0.05) -> np.ndarray:
    """
    반모수 표집: 몸통은 경험분포 재샘플, 꼬리는 GPD.
    (McNeil-Frey 조건부 EVT의 시뮬레이션 버전)
    """
    u = rng.random(size)
    out = np.empty(size)
    body = (u > q) & (u < 1 - q)
    lo = u <= q
    hi = u >= 1 - q

    zb = z_pool[(z_pool >= tail.threshold_lo) & (z_pool <= tail.threshold_hi)] \
        if tail.ok else z_pool
    if len(zb) < 10:
        zb = z_pool
    out[body] = rng.choice(zb, size=body.sum(), replace=True)

    if tail.ok and np.isfinite(tail.xi_lo):
        p = rng.random(lo.sum())
        out[lo] = tail.threshold_lo - stats.genpareto.ppf(
            np.clip(p, 1e-9, 1 - 1e-9), tail.xi_lo, loc=0, scale=tail.beta_lo)
    else:
        out[lo] = rng.choice(z_pool[z_pool < np.quantile(z_pool, q)],
                             size=lo.sum(), replace=True) if lo.sum() else np.array([])
    if tail.ok and np.isfinite(tail.xi_hi):
        p = rng.random(hi.sum())
        out[hi] = tail.threshold_hi + stats.genpareto.ppf(
            np.clip(p, 1e-9, 1 - 1e-9), tail.xi_hi, loc=0, scale=tail.beta_hi)
    else:
        out[hi] = rng.choice(z_pool[z_pool > np.quantile(z_pool, 1 - q)],
                             size=hi.sum(), replace=True) if hi.sum() else np.array([])
    return out


# ---------------------------------------------------------------- 드리프트

@dataclass
class DriftPosterior:
    mu_hat_ann: float
    se_ann: float
    prior_mean_ann: float
    shrink: float
    mu_post_ann: float
    se_post_ann: float
    ci95: Tuple[float, float]
    note: str


def drift_posterior(r: np.ndarray, ann: int = 252, prior_mean_ann: float = 0.03,
                    shrink: float = 0.60) -> DriftPosterior:
    """
    드리프트 사후분포.
    SE(μ̂) = σ/√T 이며, 일봉 표본에서 이 값은 거의 항상 μ̂ 자체만큼 크다.
    따라서 shrinkage 없이 추정 드리프트를 그대로 쓰는 것은 통계적으로 부당하다.
    """
    r = np.asarray(r, float)
    r = r[np.isfinite(r)]
    n = len(r)
    if n < 60:
        return DriftPosterior(np.nan, np.nan, prior_mean_ann, shrink,
                              prior_mean_ann, np.nan, (np.nan, np.nan),
                              "표본 부족")
    T_years = n / ann
    mu_hat = float(r.mean() * ann)
    sigma_ann = float(r.std(ddof=1) * math.sqrt(ann))
    se = sigma_ann / math.sqrt(T_years)
    mu_post = (1 - shrink) * mu_hat + shrink * prior_mean_ann
    # 사후 SE: 축소된 만큼 감소하되 prior 불확실성 유지
    se_post = math.sqrt(((1 - shrink) * se) ** 2 + (shrink * 0.05) ** 2)
    note = ("드리프트 추정오차가 추정치 자체보다 큼 — 방향 신호로 사용 불가"
            if se >= abs(mu_hat) else "드리프트 추정오차 허용 범위")
    return DriftPosterior(mu_hat, se, prior_mean_ann, shrink, mu_post, se_post,
                          (mu_hat - 1.96 * se, mu_hat + 1.96 * se), note)


# ---------------------------------------------------------------- 메인 엔진

@dataclass
class SimResult:
    terminal: np.ndarray               # 종착 가격 배열
    log_paths_quantiles: pd.DataFrame  # fan chart
    prob_up: float
    prob_up_naive_gbm: float           # 원본 방식(비교용)
    median_price: float
    mean_price: float
    q05: float
    q95: float
    var95_pct: float
    cvar95_pct: float
    prob_dd_20: float                  # 경로 중 -20% 이상 낙폭 확률
    max_dd_median: float
    drift: DriftPosterior
    tail: TailFit
    uncertainty_decomposition: Dict[str, float]
    n_sims: int
    engine: str
    paths_sample: Optional[np.ndarray] = None
    notes: list = field(default_factory=list)


def simulate_fhs(prices: np.ndarray, r: np.ndarray, garch: GarchFit,
                 horizon: int = 252, n_sims: int = 20000, ann: int = 252,
                 prior_mean_ann: float = 0.03, shrink: float = 0.60,
                 mean_block: int = 10, seed: int = 0,
                 leverage: Optional[float] = None,
                 regime_vol_multiplier: float = 1.0,
                 keep_paths: int = 4000) -> SimResult:
    """
    필터드 히스토리컬 시뮬레이션 + 조건부 EVT + 파라미터 불확실성.

    leverage: 레버리지 ETP인 경우 기초자산 경로를 만든 뒤 일간 리밸런싱을
              재구성한다 (경로의존 + 변동성 드래그 반영).
    """
    rng = np.random.default_rng(seed)
    r = np.asarray(r, float)
    r = r[np.isfinite(r)]
    S0 = float(prices[-1])
    notes = []

    dp = drift_posterior(r, ann, prior_mean_ann, shrink)
    tail = fit_gpd_tails(garch.z) if len(garch.z) > 300 else TailFit(*([np.nan] * 6), 0, 0, False)
    if not tail.ok:
        notes.append("GPD 꼬리 적합 실패 → 경험분포 재샘플로 대체")

    z_pool = garch.z[np.isfinite(garch.z)]
    if len(z_pool) < 100:
        z_pool = (r - r.mean()) / max(r.std(ddof=1), 1e-9)
        notes.append("GARCH 잔차 부족 → 원시 표준화 수익률 사용")

    # 조건부 분산 예측 경로
    if np.isfinite(garch.omega):
        s2_path = garch.forecast(horizon) * (regime_vol_multiplier ** 2)
        omega, a, g, b = garch.omega, garch.alpha, garch.gamma, garch.beta
        dynamic = True
    else:
        v = float(np.var(r, ddof=1))
        s2_path = np.full(horizon, v) * (regime_vol_multiplier ** 2)
        omega = a = g = b = np.nan
        dynamic = False
        notes.append("GARCH 미수렴 → 정적 분산 사용")

    # 드리프트 표집 (파라미터 불확실성)
    mu_draws = rng.normal(dp.mu_post_ann, max(dp.se_post_ann, 1e-6), n_sims) / ann

    log_paths = np.empty((n_sims, horizon))
    s2 = np.full(n_sims, s2_path[0])
    e_prev = np.zeros(n_sims)
    for t in range(horizon):
        z = _sample_with_evt(z_pool, tail, n_sims, rng)
        if dynamic and t > 0:
            s2 = omega + (a + g * (e_prev < 0)) * e_prev ** 2 + b * s2
            s2 = np.maximum(s2, 1e-12) * (regime_vol_multiplier ** 2)
        eps = np.sqrt(s2) * z
        e_prev = eps
        log_paths[:, t] = mu_draws - 0.5 * s2 + eps

    cum = np.cumsum(log_paths, axis=1)

    if leverage is not None and abs(leverage) > 1e-9:
        # 레버리지 ETP: 일간 단순수익률에 배수 적용 후 복리
        simple = np.expm1(log_paths)
        lev_simple = np.clip(leverage * simple, -0.95, None)
        cum = np.cumsum(np.log1p(lev_simple), axis=1)
        notes.append(f"레버리지 {leverage:+.1f}x 일간 리밸런싱 경로 재구성 "
                     f"(변동성 드래그 반영)")

    terminal = S0 * np.exp(cum[:, -1])
    paths_px = S0 * np.exp(cum)

    running_max = np.maximum.accumulate(paths_px, axis=1)
    dd = paths_px / running_max - 1.0
    maxdd = dd.min(axis=1)

    ret_t = terminal / S0 - 1.0
    q05, q95 = float(np.quantile(terminal, 0.05)), float(np.quantile(terminal, 0.95))
    var95 = float(-np.quantile(ret_t, 0.05))
    cvar95 = float(-ret_t[ret_t <= np.quantile(ret_t, 0.05)].mean())

    # 원본 GBM 방식(비교용): 드리프트 고정 + 정규
    sig_ann = float(r.std(ddof=1) * math.sqrt(ann))
    m_naive = dp.mu_hat_ann - 0.5 * sig_ann ** 2
    p_naive = float(1 - stats.norm.cdf(0, loc=m_naive, scale=sig_ann)) \
        if np.isfinite(m_naive) else np.nan

    # 불확실성 분해
    var_market = float(np.var(cum[:, -1] - (mu_draws - dp.mu_post_ann / ann) * horizon))
    var_param = float((dp.se_post_ann / ann * horizon) ** 2)
    var_total = float(np.var(cum[:, -1]))
    decomp = {
        "total_log_var": var_total,
        "param_share": float(var_param / var_total) if var_total > 0 else np.nan,
        "market_share": float(1 - var_param / var_total) if var_total > 0 else np.nan,
        "drift_se_ann": dp.se_ann,
        "drift_se_post_ann": dp.se_post_ann,
    }

    # 배리어 확률 계산을 위해 경로 일부를 보관한다(트레이드 구성에서 사용).
    keep = min(int(keep_paths), n_sims) if keep_paths else 0
    paths_keep = (paths_px[:keep].astype(np.float32) if keep > 0 else None)

    qs = [0.05, 0.10, 0.25, 0.50, 0.75, 0.90, 0.95]
    fan = pd.DataFrame(
        {f"q{int(q*100):02d}": S0 * np.exp(np.quantile(cum, q, axis=0)) for q in qs}
    )
    fan.index.name = "day"

    return SimResult(
        terminal=terminal, log_paths_quantiles=fan,
        prob_up=float(np.mean(terminal > S0)),
        prob_up_naive_gbm=p_naive,
        median_price=float(np.median(terminal)),
        mean_price=float(np.mean(terminal)),
        q05=q05, q95=q95, var95_pct=var95, cvar95_pct=cvar95,
        prob_dd_20=float(np.mean(maxdd <= -0.20)),
        max_dd_median=float(np.median(maxdd)),
        drift=dp, tail=tail, uncertainty_decomposition=decomp,
        n_sims=n_sims,
        engine="FHS-GJR-GARCH-t + GPD tail + drift posterior",
        notes=notes, paths_sample=paths_keep,
    )


def wealth_projection_from_returns(terminal: np.ndarray, S0: float,
                                   principal: float = 10_000_000.0,
                                   fee_ann: float = 0.004,
                                   tax_rate: float = 0.22,
                                   hurdle_ann: float = 0.03,
                                   years: float = 1.0) -> Dict[str, float]:
    """수수료·세금·허들을 반영한 부 투영."""
    gross_ret = terminal / S0 - 1.0
    net_of_fee = (1 + gross_ret) * (1 - fee_ann) ** years - 1.0
    taxable = np.maximum(net_of_fee, 0.0) * tax_rate
    net = net_of_fee - taxable
    wealth = principal * (1 + net)
    hurdle = (1 + hurdle_ann) ** years - 1.0
    return {
        "expected_value": float(np.mean(wealth)),
        "median_value": float(np.median(wealth)),
        "q05": float(np.quantile(wealth, 0.05)),
        "q95": float(np.quantile(wealth, 0.95)),
        "prob_loss_nominal": float(np.mean(net < 0)),
        "prob_beat_hurdle": float(np.mean(net > hurdle)),
        "hurdle_ann": hurdle_ann,
        "fee_ann": fee_ann,
        "tax_rate": tax_rate,
        "note": ("수수료·세금·허들 반영 후 값. 환율·추적오차는 미반영. "
                 "'물가 초과'와 '예금 초과'가 같은 3% 기준이면 하나의 명목 "
                 "허들 초과확률로 해석해야 한다."),
    }
