# -*- coding: utf-8 -*-
"""
옵션 · 포트폴리오 — 해석해가 있는 곳은 해석해와 대조한다.

블랙숄즈는 항등식(풋-콜 패리티)과 해석적 그릭스가 있어서 "대충 맞는 것
같다" 로 넘어갈 이유가 없다. 포트폴리오 가중치도 합·부호·리스크 기여도
같은 성질이 수학적으로 정해져 있다.
"""
from __future__ import annotations

import math
import warnings

import numpy as np
import pytest

from engine.jiqtx.options import bs_delta, bs_greeks, bs_price, implied_vol

warnings.filterwarnings("ignore")

S, R, Q = 100.0, 0.04, 0.01


# ── 블랙숄즈 ─────────────────────────────────────────────────
@pytest.mark.parametrize("K", [70, 90, 100, 110, 140])
@pytest.mark.parametrize("T", [0.08, 0.5, 2.0])
@pytest.mark.parametrize("sig", [0.12, 0.30, 0.80])
def test_put_call_parity(K, T, sig):
    """
    C - P = S·e^(-qT) - K·e^(-rT) 는 **항등식**이다.
    여기가 틀리면 가격 함수 자체가 틀린 것이라 뒤를 볼 필요도 없다.
    """
    c = bs_price(S, K, T, R, sig, True, Q)
    p = bs_price(S, K, T, R, sig, False, Q)
    rhs = S * math.exp(-Q * T) - K * math.exp(-R * T)
    assert abs((c - p) - rhs) < 1e-8, f"패리티 위반 {(c-p)-rhs:.2e}"


def test_greeks_match_numerical_derivatives():
    """해석적 그릭스가 수치미분과 맞는가."""
    K, T, sig = 105.0, 0.75, 0.28
    g = bs_greeks(S, K, T, R, sig, True, Q)
    h = 1e-4
    d_num = (bs_price(S + h, K, T, R, sig, True, Q)
             - bs_price(S - h, K, T, R, sig, True, Q)) / (2 * h)
    gam_num = (bs_price(S + h, K, T, R, sig, True, Q)
               - 2 * bs_price(S, K, T, R, sig, True, Q)
               + bs_price(S - h, K, T, R, sig, True, Q)) / h ** 2
    assert abs(g["delta"] - d_num) < 1e-6
    assert abs(g["gamma"] - gam_num) < 1e-4


def test_call_delta_is_bounded_and_monotone():
    """콜 델타는 [0,1] 안에 있고 행사가가 오르면 줄어야 한다."""
    ds = [bs_delta(S, K, 1.0, R, 0.25, True, Q) for K in range(60, 161, 10)]
    assert all(0.0 <= d <= 1.0 for d in ds), f"델타 범위 이탈 {ds}"
    assert all(a > b for a, b in zip(ds, ds[1:])), "행사가에 대해 단조가 아니다"


# ── 내재변동성 ───────────────────────────────────────────────
@pytest.mark.parametrize("K,T,sig,is_call", [
    (115, 0.10, 0.10, False),      # 깊은 내가격 풋 — 전에는 전부 NaN 이었다
    (115, 1.00, 0.10, False),
    (115, 3.00, 0.10, False),
    (130, 1.00, 0.15, False),
    (100, 1.00, 0.25, True),
    (90, 1.00, 0.25, False),
    (120, 2.00, 0.35, True),
])
def test_implied_vol_round_trip(K, T, sig, is_call):
    """
    가격 → IV → 원래 σ 가 나와야 한다.

    깊은 내가격 **풋**이 전부 NaN 이었다. 하한을 `(K-S)·e^(-rT)` 로 잡았는데
    유러피언 풋의 실제 하한은 `K·e^(-rT) - S·e^(-qT)` 다. 잘못된 하한이
    실제 BS 가격보다 높게 나와(K=115, T=3 에서 하한 13.17 vs 가격 9.62)
    멀쩡한 가격을 차익거래 위반으로 봤다.

    그 결과 스마일의 **풋 윙이 통째로 빠졌다.** 스큐가 있는 구간이고
    RND 의 좌측 꼬리가 거기서 나온다.
    """
    px = bs_price(S, K, T, R, sig, is_call, Q)
    iv = implied_vol(px, S, K, T, R, is_call, Q)
    assert np.isfinite(iv), f"IV 를 못 찾았다 (K={K} T={T} {'콜' if is_call else '풋'})"
    assert abs(iv - sig) < 1e-3, f"IV {iv:.4f} vs 진짜 {sig}"


def test_implied_vol_rejects_arbitrage_violations():
    """
    차익거래 하한 아래 / 상한 위 가격은 거부해야 한다.
    콜 쪽 하한이 너무 낮아 위반 가격도 통과시키던 문제가 있었다.
    """
    K, T = 90.0, 1.0
    lo = max(S * math.exp(-Q * T) - K * math.exp(-R * T), 0.0)
    hi = S * math.exp(-Q * T)
    assert not np.isfinite(implied_vol(lo * 0.90, S, K, T, R, True, Q)), \
        "하한 아래 가격을 통과시켰다"
    assert not np.isfinite(implied_vol(hi * 1.10, S, K, T, R, True, Q)), \
        "상한 위 가격을 통과시켰다"


def test_implied_vol_is_nan_when_unidentifiable():
    """
    시간가치와 vega 가 사실상 0 이면 IV 는 식별되지 않는다.
    그때 값을 지어내는 것보다 NaN 이 낫다 — 스마일 빌더가 걸러 낸다.
    """
    # 만기 직전 깊은 내가격
    iv = implied_vol(bs_price(S, 50.0, 0.002, R, 0.2, True, Q),
                     S, 50.0, 0.002, R, True, Q)
    assert (not np.isfinite(iv)) or iv <= 0.01, \
        f"식별 불가 구간에서 그럴듯한 값을 지어냈다: {iv}"


# ── 포트폴리오 ───────────────────────────────────────────────
@pytest.fixture(scope="module")
def cov():
    from engine.jiqtx.portfolio import cov_estimate
    rng = np.random.default_rng(13)
    n, k = 900, 5
    # 공통 요인 + 개별 잡음 — 상관이 있는 현실적인 공분산
    f = rng.normal(0, 0.010, (n, 1))
    load = np.array([[1.2], [0.9], [0.6], [1.4], [0.3]])
    R = f @ load.T + rng.normal(0, 0.008, (n, k))
    return cov_estimate(R), R


def test_weights_sum_to_one_and_are_long_only(cov):
    """
    비중이 1로 합쳐지지 않으면 그 뒤의 리스크·수익 계산이 전부 어긋난다.
    """
    from engine.jiqtx.portfolio import w_equal, w_invvol, w_minvar, w_riskparity
    S_, _ = cov
    for name, fn in (("동일", w_equal), ("역변동성", w_invvol),
                     ("최소분산", w_minvar), ("리스크패리티", w_riskparity)):
        w = fn(S_)
        assert np.isfinite(w).all(), f"{name}: 비유한값"
        assert abs(w.sum() - 1.0) < 1e-6, f"{name}: 합 {w.sum():.6f}"
        assert (w >= -1e-9).all(), f"{name}: 음수 비중 {w.min():.4f}"


def test_risk_parity_equalizes_risk_contributions(cov):
    """
    리스크 패리티의 정의는 **각 자산의 리스크 기여가 같다** 는 것이다.
    이름만 그렇고 실제로 안 맞으면 그건 그냥 다른 가중치다.
    """
    from engine.jiqtx.portfolio import w_riskparity
    S_, _ = cov
    w = w_riskparity(S_)
    port_var = float(w @ S_ @ w)
    mrc = S_ @ w                      # 한계 기여
    rc = w * mrc / math.sqrt(port_var)   # 리스크 기여
    rel = rc / rc.sum()
    assert rel.std() < 0.02, f"리스크 기여가 고르지 않다: {np.round(rel, 4)}"


def test_min_variance_really_has_lower_variance(cov):
    """최소분산이 동일가중보다 분산이 크면 이름값을 못 한다."""
    from engine.jiqtx.portfolio import w_equal, w_minvar
    S_, _ = cov
    v_min = float(w_minvar(S_) @ S_ @ w_minvar(S_))
    v_eq = float(w_equal(S_) @ S_ @ w_equal(S_))
    assert v_min <= v_eq + 1e-12, f"최소분산 {v_min:.6e} > 동일가중 {v_eq:.6e}"


def test_covariance_is_positive_semidefinite(cov):
    """공분산이 PSD 가 아니면 음수 분산이 나오고 최적화가 발산한다."""
    S_, _ = cov
    ev = np.linalg.eigvalsh(S_)
    assert ev.min() > -1e-12, f"최소 고유값 {ev.min():.3e} — PSD 위반"
    assert np.allclose(S_, S_.T), "공분산이 대칭이 아니다"
