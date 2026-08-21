# -*- coding: utf-8 -*-
"""
추정량이 실제로 맞는가 — 합성 진실값 대조.

`engine/jiqtx/_validate.py` 에 같은 시뮬레이션이 있지만 그건 숫자를
**출력만** 한다. 사람이 눈으로 보는 용도라 회귀를 못 막는다. 여기서는
같은 실험에 **판정 기준**을 붙인다.

허용 오차는 2026-08-21 에 실측한 값에서 왔다. 여유를 두되, 진짜 회귀는
잡을 만큼 좁게 잡았다 — 너무 넓으면 통과해도 아무 의미가 없다.
"""
from __future__ import annotations

import numpy as np
import pytest

from engine.jiqtx.micro import (abdi_ranaldo, corwin_schultz, edge_spread,
                                roll_spread)
from engine.jiqtx.risk import kelly_with_drawdown_constraint
from engine.jiqtx.statcore import adaptive_conformal, murphy_decomposition
from engine.jiqtx.vol import fit_gjr_garch_t


def _sim_ohlc_bounce(n_days, true_spread, daily_vol, ticks=390, seed=0):
    """호가 스프레드가 있는 일봉 생성 (진짜 스프레드를 아는 상태)."""
    rng = np.random.default_rng(seed)
    sd_t = daily_vol / np.sqrt(ticks)
    O, H, L, C = (np.empty(n_days) for _ in range(4))
    logp = np.log(100.0)
    for d in range(n_days):
        eff = logp + np.cumsum(rng.normal(0, sd_t, ticks))
        tr = np.exp(eff + rng.choice([-1, 1], ticks) * true_spread / 2)
        O[d], H[d], L[d], C[d] = tr[0], tr.max(), tr.min(), tr[-1]
        logp = eff[-1]
    return O, H, L, C


# ── 스프레드 추정 ────────────────────────────────────────────
@pytest.mark.parametrize("true_bp", [5, 10, 20, 50, 100])
def test_edge_recovers_true_spread(true_bp):
    """
    EDGE 는 진짜 스프레드를 편향 없이 복원해야 한다.

    유동성 게이트(G2)가 이 값으로 거래 가능성을 판정한다. 여기가 틀리면
    거래비용을 잘못 보고 사이징이 통째로 어긋난다.
    """
    s = true_bp / 1e4
    est = [edge_spread(*_sim_ohlc_bounce(500, s, 0.018, seed=k))
           for k in range(6)]
    got = np.nanmean(est) * 1e4
    # 실측: 5bp→5.4, 10bp→9.3, 20bp→20.7, 50bp→51.6, 100bp→102.2
    assert abs(got - true_bp) <= max(2.0, true_bp * 0.12), (
        f"EDGE 가 {true_bp}bp 를 {got:.1f}bp 로 추정")


def test_edge_is_the_most_accurate_at_low_spread():
    """
    저스프레드에서 EDGE 가 가장 정확한가 — 추정량 선택의 근거.

    "대안은 늘 과대추정한다" 로 쓰면 안 된다. CHL 은 음수 추정을 0 으로
    클램프해서 **과소**추정으로 틀리기도 한다(단일 시드에서 0.0 을 봤다).
    그래서 방향이 아니라 **진짜 값과의 거리**로 비교한다. 시드 하나로는
    운이 섞이니 여러 번 돌려 평균낸다.
    """
    true_bp = 5.0
    got = {k: [] for k in ("EDGE", "CS", "CHL", "Roll")}
    for seed in range(6):
        O, H, L, C = _sim_ohlc_bounce(500, true_bp / 1e4, 0.018, seed=seed)
        got["EDGE"].append(edge_spread(O, H, L, C) * 1e4)
        got["CS"].append(corwin_schultz(H, L) * 1e4)
        got["CHL"].append(abdi_ranaldo(H, L, C) * 1e4)
        got["Roll"].append(roll_spread(C) * 1e4)

    err = {k: abs(np.nanmean(v) - true_bp) for k, v in got.items()}
    best = min(err, key=err.get)
    assert best == "EDGE", f"EDGE 가 더 이상 최선이 아니다: {err}"
    assert err["EDGE"] < 3.0, f"EDGE 오차 {err['EDGE']:.1f}bp"
    # 실측 오차: EDGE 0.4 · CS 67 · CHL 20 · Roll 60
    assert err["EDGE"] * 4 < min(err[k] for k in ("CS", "CHL", "Roll")), (
        f"EDGE 의 우위 폭이 크게 줄었다: {err}")


# ── 변동성 ───────────────────────────────────────────────────
@pytest.mark.parametrize("om,a,g,b,nu", [
    (3e-6, 0.05, 0.06, 0.88, 6),
    (2e-6, 0.03, 0.10, 0.86, 8),
])
def test_gjr_garch_recovers_parameters(om, a, g, b, nu):
    """
    GJR-GARCH(1,1)-t 가 모수를 복원하는가.

    MLE 는 n=2000 에서 α/γ 를 살짝 위로, β 를 아래로 잡는 유한표본 편의가
    있다(알려진 성질). 그래서 개별 모수보다 **지속성**과 **레버리지 효과의
    방향**을 본다 — 실제로 예측에 쓰이는 건 그쪽이다.
    """
    rng = np.random.default_rng(3)
    n = 2000
    s2 = np.zeros(n); e = np.zeros(n)
    s2[0] = om / (1 - a - g / 2 - b)
    for t in range(1, n):
        s2[t] = om + (a + g * (e[t-1] < 0)) * e[t-1]**2 + b * s2[t-1]
        e[t] = np.sqrt(s2[t]) * rng.standard_t(nu) / np.sqrt(nu / (nu - 2))

    f = fit_gjr_garch_t(e)
    true_persist = a + g / 2 + b

    assert abs(f.persistence - true_persist) < 0.05, (
        f"지속성 {f.persistence:.3f} vs 진짜 {true_persist:.3f}")
    assert f.persistence < 1.0, "지속성이 1 이상이면 분산이 발산한다"
    assert f.gamma > 0, "레버리지 효과(γ>0)를 못 잡았다"
    assert abs(f.nu - nu) < 2.5, f"자유도 {f.nu:.1f} vs 진짜 {nu}"
    assert f.alpha > 0 and f.beta > 0, "음수 모수는 분산을 음수로 만든다"


def test_garch_variance_stays_positive(rng):
    """어떤 입력에도 분산은 양수여야 한다 — 음수면 √ 에서 NaN 이 번진다."""
    for scale in (0.005, 0.02, 0.08):
        r = rng.standard_t(5, 1200) / np.sqrt(5 / 3) * scale
        f = fit_gjr_garch_t(r)
        assert f.omega > 0 and np.isfinite(f.omega)
        assert np.isfinite(f.persistence) and 0 < f.persistence < 1.05


# ── 확률 예측의 정보량 ───────────────────────────────────────
def test_murphy_separates_signal_from_noise():
    """
    Murphy resolution 이 정보 있는 예측과 없는 예측을 갈라야 한다.

    G4 게이트가 `resolution <= 1e-4` 면 방향 예측을 통째로 무효화한다.
    이 판별력이 죽으면 무의미한 예측이 그대로 사용자에게 나간다.
    """
    rng = np.random.default_rng(0)
    p = rng.uniform(0, 1, 4000)
    good = murphy_decomposition(p, (rng.uniform(0, 1, 4000) < p).astype(float))
    junk = murphy_decomposition(p, (rng.uniform(0, 1, 4000) < 0.5).astype(float))

    assert good["resolution"] > 0.05, "정보성 예측의 resolution 이 무너졌다"
    assert junk["resolution"] < 1e-3, "무정보 예측이 resolution 을 얻었다"
    assert good["resolution"] / max(junk["resolution"], 1e-12) > 50
    assert good["skill"] > 0.2 and junk["skill"] < 0


# ── 예측구간 커버리지 ────────────────────────────────────────
@pytest.mark.parametrize("name,gen", [
    ("정규",   lambda r, n: r.standard_normal(n)),
    ("팻테일", lambda r, n: r.standard_t(3, n) / np.sqrt(3)),
    ("레짐전환", lambda r, n: r.standard_normal(n) *
                 np.where(np.arange(n) < n // 2, 1.0, 2.5)),
])
def test_conformal_coverage_hits_target(name, gen):
    """
    적응형 conformal 이 목표 90% 를 지키는가.

    팻테일과 레짐 전환에서도 지켜야 의미가 있다 — 정규분포에서만 맞는
    구간은 정작 필요한 순간에 틀린다.
    """
    rng = np.random.default_rng(1)
    r = adaptive_conformal(gen(rng, 1500), np.ones(1500), 0.90)
    assert abs(r.empirical_coverage - 0.90) < 0.03, (
        f"{name}: 커버리지 {r.empirical_coverage:.1%}")


# ── 사이징 ───────────────────────────────────────────────────
def test_kelly_shrinks_as_estimation_error_grows():
    """
    μ 추정오차가 커지면 비중이 줄어야 한다.

    켈리의 문제는 공식이 아니라 μ 를 안다고 가정한 것이다. 이 단조성이
    깨지면 불확실할수록 크게 베팅하는 꼴이 된다.
    """
    rng = np.random.default_rng(2)
    zt = rng.standard_t(4, 4000) / np.sqrt(2)
    fs = [kelly_with_drawdown_constraint(0.08, se, 0.18, z_resid=zt,
                                         dd_limit=0.25)["f_dd"]
          for se in (0.02, 0.10, 0.25)]
    assert fs[0] > fs[1] > fs[2], f"추정오차가 커지는데 비중이 안 줄었다: {fs}"


def test_kelly_respects_the_drawdown_limit():
    """낙폭 제약이 실제로 구속하는가 — 안 그러면 제약을 둔 의미가 없다."""
    rng = np.random.default_rng(2)
    zt = rng.standard_t(4, 4000) / np.sqrt(2)
    for se in (0.02, 0.10, 0.25):
        k = kelly_with_drawdown_constraint(0.08, se, 0.18, z_resid=zt,
                                           dd_limit=0.25)
        assert k["mdd_at_dd"] <= 0.25 + 0.02, (
            f"제약 25% 인데 95%MDD 가 {k['mdd_at_dd']:.0%}")
        assert k["f_dd"] <= k["f_growth"], "낙폭제약이 성장최적보다 크다"
