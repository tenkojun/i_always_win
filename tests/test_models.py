# -*- coding: utf-8 -*-
"""
모델별 진실값 복원 — 팩터 · 시변베타 · 레짐 · 꼬리 · 드리프트.

`test_estimators.py` 가 스프레드·GARCH·conformal·켈리를 본다면 여기는
나머지 절반이다. 전부 **진실을 아는 합성 데이터**에 넣고 되찾는지 본다.

이런 검증이 없으면 부호 하나가 뒤집혀도 아무도 모른다 — 실제로 ES 공식이
그랬고(v4.2.0), 칼만 필터의 반응 속도가 그랬다(v4.3.0).
"""
from __future__ import annotations

import math
import warnings

import numpy as np
import pandas as pd
import pytest

from engine.jiqtx.factors import fit_factor_model, kalman_beta, time_varying_beta
from engine.jiqtx.regime import detect_regimes
from engine.jiqtx.simulate import drift_posterior, fit_gpd_tails

warnings.filterwarnings("ignore")


# ── 팩터 모델 ────────────────────────────────────────────────
@pytest.fixture(scope="module")
def factor_fit():
    rng = np.random.default_rng(5)
    n = 1500
    F = pd.DataFrame({
        "mkt": rng.normal(0, 0.010, n), "smb": rng.normal(0, 0.006, n),
        "hml": rng.normal(0, 0.006, n), "noise1": rng.normal(0, 0.008, n),
        "noise2": rng.normal(0, 0.008, n)})
    true = {"mkt": 1.15, "smb": -0.40, "hml": 0.25}
    y = sum(true[k] * F[k].values for k in true) + rng.normal(0, 0.006, n)
    return fit_factor_model(y, F, r2_band=(0.2, 0.9)), true


def test_factor_betas_are_recovered(factor_fit):
    """
    주입한 노출을 되찾아야 한다. 여기가 틀리면 델타 패널·헤지·귀인이
    전부 그 위에서 돈다.
    """
    fm, true = factor_fit
    for k, v in true.items():
        got = fm.coefs.get(k)
        assert got is not None, f"{k} 를 아예 못 찾았다"
        assert abs(got - v) < 0.12, f"{k}: 진짜 {v:+.3f} vs 추정 {got:+.3f}"


def test_real_factors_are_significant_noise_is_not(factor_fit):
    """
    진짜 노출은 |t|≥2, 순수 노이즈는 |t|<2 여야 한다.

    Elastic-Net 이 노이즈를 선택 목록에 남기는 것 자체는 문제가 아니다 —
    사후에 유의하지 않은 회귀변수를 빼면 그게 또 다른 선택 편의를 만든다.
    보고서가 계수 옆에 t값을 함께 보여 주므로 읽는 쪽이 판단할 수 있다.
    """
    fm, true = factor_fit
    for k in true:
        assert abs(fm.tstats.get(k, 0)) >= 2.0, f"{k} 가 유의하지 않다"
    for k in ("noise1", "noise2"):
        if k in fm.coefs:
            assert abs(fm.tstats.get(k, 0)) < 2.5, \
                f"순수 노이즈 {k} 가 유의하다고 나왔다"


def test_no_alpha_is_manufactured(factor_fit):
    """알파를 넣지 않았으면 유의한 알파가 나오면 안 된다."""
    fm, _ = factor_fit
    assert abs(fm.alpha_t) < 2.0, \
        f"없는 알파를 유의하다고 주장했다: t={fm.alpha_t:+.2f}"


# ── 시변 베타 ────────────────────────────────────────────────
def _step_beta_series(n=1500, b0=0.5, b1=1.6, seed=5):
    rng = np.random.default_rng(seed)
    half = n // 2
    beta = np.concatenate([np.full(half, b0), np.full(n - half, b1)])
    x = rng.normal(0, 0.011, n)
    y = beta * x + rng.normal(0, 0.004, n)
    return y, x, half


def test_kalman_tracks_a_beta_change():
    """
    베타가 바뀌면 따라가야 한다.

    기본 q 가 1e-5 였을 때는 3년이 지나도 1.44 에 머물렀다(진짜 1.60).
    같은 함수가 내는 롤링 252일 창이 1.62 로 정확했으니, 개선하려고 넣은
    칼만이 단순 롤링보다 못한 상태였다. beta_now 는 헤지 사이징에 쓰이므로
    10% 과소평가는 그대로 미달헤지가 된다.
    """
    y, x, half = _step_beta_series()
    b = kalman_beta(y, x)
    assert abs(np.nanmean(b[200:half - 50]) - 0.5) < 0.10, "전반부를 못 맞춘다"
    assert abs(np.nanmean(b[-100:]) - 1.6) < 0.10, \
        f"전환 후 수렴 실패: {np.nanmean(b[-100:]):.3f} (진짜 1.60)"


def test_kalman_stays_calm_when_beta_is_constant():
    """
    반응성을 얻겠다고 q 를 키우면 고정 베타에서 값이 출렁인다.
    반대쪽 극단으로 가지 않았는지 함께 본다.
    """
    rng = np.random.default_rng(9)
    n = 1500
    x = rng.normal(0, 0.011, n)
    y = 1.0 * x + rng.normal(0, 0.004, n)
    b = kalman_beta(y, x)
    assert np.nanstd(b[300:]) < 0.05, \
        f"고정 베타인데 표준편차 {np.nanstd(b[300:]):.4f}"
    assert abs(np.nanmean(b[300:]) - 1.0) < 0.05


def test_time_varying_beta_reports_both_estimates():
    """롤링과 칼만을 함께 낸다 — 둘이 어긋나면 그 자체가 정보다."""
    y, x, _ = _step_beta_series()
    idx = pd.bdate_range("2019-01-02", periods=len(y))
    tvb = time_varying_beta(y, x, idx, "mkt", window=252)
    assert len(tvb.rolling) == len(y) and len(tvb.kalman) == len(y)
    assert abs(tvb.beta_now - 1.6) < 0.15, f"beta_now {tvb.beta_now:.3f}"


# ── 레짐 ─────────────────────────────────────────────────────
def test_regime_finds_a_volatility_switch():
    """
    저변동 → 고변동 → 저변동 을 넣으면 양 끝을 같은 상태로, 가운데를
    다른 상태로 봐야 한다. 이걸 못 하면 레짐 서사가 근거를 잃는다.
    """
    rng = np.random.default_rng(5)
    r = np.concatenate([rng.normal(0, 0.008, 700),
                        rng.normal(0, 0.028, 500),
                        rng.normal(0, 0.008, 300)])
    rg = detect_regimes(r, n_states=3, jump_penalty=20.0, seed=42)
    st = np.asarray(rg.states)

    def dominant(sl):
        v, c = np.unique(st[sl], return_counts=True)
        return v[c.argmax()], c.max() / len(st[sl])

    lo1, f1 = dominant(slice(0, 700))
    hi, fh = dominant(slice(700, 1200))
    lo2, f2 = dominant(slice(1200, None))
    assert lo1 == lo2, "같은 성질의 두 구간을 다른 상태로 봤다"
    assert hi != lo1, "고변동 구간을 구분하지 못했다"
    assert f1 > 0.7 and f2 > 0.7, f"저변동 구간 일관성 부족 ({f1:.0%}, {f2:.0%})"


# ── 꼬리 ─────────────────────────────────────────────────────
def test_gpd_detects_fat_tails():
    """
    t(4) 의 꼬리지수는 이론적으로 ξ = 1/ν = 0.25 다. 정규는 음수여야 한다.
    이걸 못 가르면 FHS-EVT 시뮬레이션이 팻테일을 정규처럼 다룬다.
    """
    rng = np.random.default_rng(5)
    norm = fit_gpd_tails(rng.standard_normal(4000))
    fat = fit_gpd_tails(rng.standard_t(4, 4000) / math.sqrt(2))
    assert norm.ok and fat.ok
    assert fat.xi_lo > norm.xi_lo, "팻테일을 정규보다 얇게 봤다"
    assert 0.05 < fat.xi_lo < 0.60, f"t(4) 꼬리지수 {fat.xi_lo:.3f} (이론 0.25)"
    assert norm.xi_lo < 0.10, f"정규 꼬리지수 {norm.xi_lo:.3f}"


# ── 드리프트 ─────────────────────────────────────────────────
def test_drift_shrinks_toward_the_prior():
    """
    일봉에서 SE(μ̂) 는 거의 언제나 μ̂ 만큼 크다. 축소 없이 쓰는 것은
    통계적으로 부당하다 — 축소가 실제로 걸리는지 본다.
    """
    rng = np.random.default_rng(3)
    r = rng.normal((0.20 - 0.5 * 0.20**2) / 252, 0.20 / math.sqrt(252), 1260)
    d = drift_posterior(r, prior_mean_ann=0.03, shrink=0.60)
    expect = 0.4 * d.mu_hat_ann + 0.6 * 0.03
    assert d.mu_post_ann == pytest.approx(expect, abs=1e-9)
    assert abs(d.mu_post_ann) < abs(d.mu_hat_ann), "축소가 안 걸렸다"
    assert d.se_post_ann < d.se_ann, "사후 불확실성이 안 줄었다"


def test_drift_ci_belongs_to_the_raw_estimate():
    """
    ci95 는 **μ̂ 기준**이다. 사후 기준이 아니다.

    일부러 그렇게 뒀다 — 보고서가 `μ̂ → SE(μ̂) → 95% 구간 → 사후 드리프트`
    순으로 보여 주면서 "추정치가 자기 표준오차에 묻힌다" 를 드러내는 게
    그 절의 논지다. 사후 구간으로 바꾸면 그 논지가 사라진다.
    """
    rng = np.random.default_rng(3)
    r = rng.normal(0.0002, 0.012, 1260)
    d = drift_posterior(r)
    lo, hi = d.ci95
    assert lo == pytest.approx(d.mu_hat_ann - 1.96 * d.se_ann, abs=1e-9)
    assert hi == pytest.approx(d.mu_hat_ann + 1.96 * d.se_ann, abs=1e-9)


# ── 시뮬레이션 ───────────────────────────────────────────────
@pytest.fixture(scope="module")
def sim_setup():
    from engine.jiqtx.vol import fit_gjr_garch_t
    rng = np.random.default_rng(21)
    sig, mu, n = 0.22, 0.08, 1500
    r = rng.normal((mu - 0.5 * sig**2) / 252, sig / math.sqrt(252), n)
    prices = 100 * np.exp(np.cumsum(r))
    return prices, r, fit_gjr_garch_t(r)


def test_one_day_simulation_matches_garch_sigma(sim_setup):
    """
    1일 지평 시뮬레이션의 표준편차가 GARCH 현재 변동성과 맞아야 한다.
    여기가 어긋나면 뒤의 VaR·시나리오·사이징이 전부 잘못된 척도 위에서 돈다.
    """
    from engine.jiqtx.simulate import simulate_fhs
    prices, r, g = sim_setup
    s = simulate_fhs(prices, r, g, horizon=1, n_sims=40000, seed=1)
    lr = np.log(s.terminal / prices[-1])
    ratio = lr.std() * math.sqrt(252) / g.ann_vol_current
    assert 0.85 < ratio < 1.20, f"시뮬/GARCH 변동성 비 {ratio:.3f}"


def test_simulation_scales_with_sqrt_time(sim_setup):
    """지평이 252배면 표준편차는 √252 배 근처여야 한다."""
    from engine.jiqtx.simulate import simulate_fhs
    prices, r, g = sim_setup
    s1 = simulate_fhs(prices, r, g, horizon=1, n_sims=30000, seed=1)
    s252 = simulate_fhs(prices, r, g, horizon=252, n_sims=30000, seed=1)
    a = np.log(s1.terminal / prices[-1]).std()
    b = np.log(s252.terminal / prices[-1]).std()
    assert 0.8 < b / (a * math.sqrt(252)) < 1.3, "√t 척도가 어긋난다"


def test_simulation_cvar_exceeds_var(sim_setup):
    """시뮬레이션 쪽 CVaR 도 VaR 보다 커야 한다 (ES 와 같은 이유)."""
    from engine.jiqtx.simulate import simulate_fhs
    prices, r, g = sim_setup
    s = simulate_fhs(prices, r, g, horizon=252, n_sims=30000, seed=1)
    assert s.cvar95_pct >= s.var95_pct,         f"CVaR {s.cvar95_pct:.4f} < VaR {s.var95_pct:.4f}"
    assert s.q05 < s.median_price < s.q95, "분위수 순서가 뒤집혔다"
    for k in ("prob_up", "prob_up_naive_gbm", "prob_dd_20"):
        v = getattr(s, k)
        assert 0.0 <= v <= 1.0, f"{k} = {v}"


def test_simulation_is_reproducible(sim_setup):
    """같은 시드면 같은 결과 — 감사 가능성의 전제다."""
    from engine.jiqtx.simulate import simulate_fhs
    prices, r, g = sim_setup
    a = simulate_fhs(prices, r, g, horizon=252, n_sims=4000, seed=7)
    b = simulate_fhs(prices, r, g, horizon=252, n_sims=4000, seed=7)
    c = simulate_fhs(prices, r, g, horizon=252, n_sims=4000, seed=8)
    assert np.array_equal(a.terminal, b.terminal), "같은 시드인데 결과가 다르다"
    assert not np.array_equal(a.terminal, c.terminal), "시드가 안 먹는다"


# ── 다지평 ───────────────────────────────────────────────────
@pytest.fixture(scope="module")
def horizon_panel():
    from engine.jiqtx.horizons import analyze_horizons
    rng = np.random.default_rng(31)
    # 장기 상승 + 최근 1년 하락·고변동 — 지평 간 불일치를 일부러 만든다
    r = np.concatenate([rng.normal(0.0007, 0.010, 1000),
                        rng.normal(-0.0009, 0.016, 252)])
    px = pd.Series(100 * np.exp(np.cumsum(r)),
                   index=pd.bdate_range("2019-01-02", periods=len(r)))
    return analyze_horizons(px)


def test_drift_standard_error_is_annualized_consistently(horizon_panel):
    """
    μ̂ 를 연율화했으면 SE 도 연 단위 T 로 나눠야 한다.

    전에는 `sd·√252/√n` 이라 분자만 연율이고 분모의 T 는 일 단위였다.
    SE 가 √252≈15.9배 작게 나와 t 가 그만큼 부풀었고, **모든 지평이
    유의하다**고 나왔다 — 단기 실제 t=0.36 을 5.66 으로 봤다.
    """
    for s in horizon_panel.stats:
        # 수익률 개수는 가격 개수보다 하나 적다 — 표본 크기는 그쪽이다
        n_ret = s.n_obs - 1
        expect = s.ann_vol / math.sqrt(n_ret / 252)
        assert s.drift_se == pytest.approx(expect, rel=1e-3), (
            f"{s.label_ko}: SE {s.drift_se:.4f} vs 이론 {expect:.4f}")
        # 내부 정합성은 정확히 맞아야 한다
        assert s.drift_t == pytest.approx(s.drift_ann / s.drift_se, rel=1e-9)
        # 옛 버그를 직접 배제한다: sd·√252/√n (분모가 일 단위) 이면
        # 지금 값보다 √252 배 작다. 그 값과 같아지면 되돌아간 것이다.
        buggy = s.ann_vol / math.sqrt(n_ret)
        assert abs(s.drift_se - buggy) > buggy * 0.5, (
            f"{s.label_ko}: SE 가 옛 버그 값({buggy:.4f})으로 되돌아갔다")


def test_short_horizon_drift_is_not_significant(horizon_panel):
    """
    63일 표본으로 드리프트가 유의하다고 나오면 그건 계산이 틀린 것이다.
    σ/√T 가 μ̂ 를 압도하는 게 정상이다.
    """
    short = [s for s in horizon_panel.stats if s.days <= 63]
    assert short, "단기 지평이 없다"
    assert not short[0].drift_meaningful, (
        f"63일 표본에서 |t|={abs(short[0].drift_t):.2f} 로 유의 판정")


def test_horizon_internal_consistency(horizon_panel):
    for s in horizon_panel.stats:
        assert 0.0 <= s.above_sma_ratio <= 1.0
        assert s.ann_vol >= 0
        assert s.drift_meaningful == (abs(s.drift_t) >= 2.0)


def test_horizon_disagreement_is_surfaced(horizon_panel):
    """
    지평을 합치지 않고 **어긋나는 지점을 드러내는 것**이 목적이다.
    상승 구간과 하락 구간을 붙여 넣었으니 불일치가 잡혀야 한다.
    """
    assert horizon_panel.disagreements, "명백한 불일치를 못 잡았다"
