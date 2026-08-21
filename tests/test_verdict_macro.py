# -*- coding: utf-8 -*-
"""
판정 엔진 · 거시보드 · RND.

이 셋은 **사용자가 실제로 읽는 문장**을 만든다. 숫자가 맞아도 여기서
과장하면 앞의 절제가 전부 무의미해진다.
"""
from __future__ import annotations

import math
import sys
import types
import warnings

import numpy as np
import pandas as pd
import pytest

from engine.jiqtx.agents import AgentView, GateBoard, adjudicate
from engine.jiqtx.macro_board import _comment, _impact_label

warnings.filterwarnings("ignore")


def _v(name, p, conf=1.0, hint=1.0, veto=False):
    return AgentView(name, "역할", "NEUTRAL", p, conf, weight_hint=hint,
                     veto=veto, evidence=[], data_scope="")


class _Sizing:
    final_weight = 0.05
    binding_constraint = "테스트"


@pytest.fixture
def gates():
    return GateBoard()


# ── 판정 엔진 ────────────────────────────────────────────────
@pytest.mark.parametrize("probs,expect", [
    ([0.70, 0.72, 0.68], 0.70),
    ([0.30, 0.28, 0.32], 0.30),
    ([0.50, 0.50, 0.50], 0.50),
])
def test_consensus_is_followed(gates, probs, expect):
    """의견이 모이면 통합 확률이 그쪽으로 가야 한다."""
    v = adjudicate([_v(f"A{i}", p) for i, p in enumerate(probs)],
                   gates, _Sizing())
    assert abs(v.direction_prob - expect) < 0.02


def test_disagreement_shrinks_and_widens(gates):
    """
    의견이 갈리면 0.5 로 축소하고 **구간을 넓혀야** 한다.

    단순 평균도 0.5 를 주지만 그건 "확신 있는 0.5" 로 읽힌다.
    정보는 구간 폭에 담긴다 — 그게 평균과의 결정적 차이다.
    """
    agree = adjudicate([_v("A", 0.50), _v("B", 0.50)], gates, _Sizing())
    split = adjudicate([_v("A", 0.95), _v("B", 0.05)], gates, _Sizing())
    w_agree = agree.direction_ci[1] - agree.direction_ci[0]
    w_split = split.direction_ci[1] - split.direction_ci[0]
    assert abs(split.direction_prob - 0.5) < 0.02, "분열인데 0.5 로 안 갔다"
    assert w_split > w_agree * 2, (
        f"분열인데 구간이 안 넓어졌다 (합의 {w_agree:.3f} vs 분열 {w_split:.3f})")


def test_one_loud_agent_cannot_hijack_the_pool(gates):
    """
    극단 의견 하나가 전체를 끌고 가면 안 된다.
    로그오즈 가중 풀링을 쓰는 이유가 이것이다.
    """
    base = [_v(f"A{i}", 0.52) for i in range(4)]
    before = adjudicate(base, gates, _Sizing()).direction_prob
    after = adjudicate(base + [_v("X", 0.99)], gates, _Sizing()).direction_prob
    assert after - before < 0.10, f"극단값 하나가 {after-before:+.3f} 를 움직였다"


def test_veto_forces_zero_weight(gates):
    """
    거부권이 있으면 비중은 0 이어야 한다. 게이트 실패는 감점이 아니라
    출력 무효화라는 원칙이 여기서 실행된다.
    """
    v = adjudicate([_v("A", 0.80), _v("B", 0.82, veto=True)], gates, _Sizing())
    assert v.risk_budget_weight == 0.0
    assert v.vetoes, "거부권이 기록되지 않았다"
    ok = adjudicate([_v("A", 0.80), _v("B", 0.82)], gates, _Sizing())
    assert ok.risk_budget_weight > 0, "거부권이 없는데도 0 이다"


@pytest.mark.parametrize("probs", [[0.99, 0.99], [0.01, 0.01], [0.99, 0.01]])
def test_probability_and_interval_stay_in_bounds(gates, probs):
    """확률과 구간이 [0,1] 을 벗어나면 화면에 이상한 값이 나간다."""
    v = adjudicate([_v(f"A{i}", p) for i, p in enumerate(probs)],
                   gates, _Sizing())
    lo, hi = v.direction_ci
    assert 0.0 <= lo <= v.direction_prob <= hi <= 1.0, f"{lo} {v.direction_prob} {hi}"


# ── 거시보드 ─────────────────────────────────────────────────
@pytest.mark.parametrize("beta,chg,t", [
    (0.80, 0.5, 1.9), (-0.60, -0.5, -1.2), (0.05, 0.5, 0.3),
])
def test_insignificant_macro_beta_says_nothing(beta, chg, t):
    """
    |t| < 2 면 방향을 말하면 안 된다.
    유의하지 않은 베타로 서사를 만드는 것이 이 엔진이 거부하는 바로 그것이다.
    """
    sig = abs(t) >= 2.0
    assert _impact_label(beta, chg, sig) == "중립"
    cm = _comment("real_yield_10y", beta, t, chg, sig)
    assert "구별되지 않" in cm, f"중립인데 설명이 다르다: {cm}"
    assert "유리" not in cm and "불리" not in cm, f"방향을 말했다: {cm}"


@pytest.mark.parametrize("beta,chg,expect", [
    (0.80, 0.5, "지지"), (0.80, -0.5, "역풍"),
    (-0.60, 0.5, "역풍"), (-0.60, -0.5, "지지"),
])
def test_significant_macro_beta_sign_logic(beta, chg, expect):
    """영향은 손으로 쓰지 않는다 — 베타 부호 × 변화 방향으로 정해진다."""
    assert _impact_label(beta, chg, True) == expect


def test_macro_significance_boundary():
    """|t| = 2.0 이 경계다. 1.99 는 중립, 2.00 은 유의."""
    assert _impact_label(0.8, 0.5, abs(1.99) >= 2.0) == "중립"
    assert _impact_label(0.8, 0.5, abs(2.00) >= 2.0) == "지지"


@pytest.mark.parametrize("beta,chg", [
    (float("nan"), 0.5), (0.8, float("nan")), (None, 0.5),
])
def test_macro_missing_inputs_are_held(beta, chg):
    """값이 없으면 '판단보류' — 빈칸을 그럴듯한 말로 채우지 않는다."""
    assert _impact_label(beta, chg, True) == "판단보류"


# ── RND ──────────────────────────────────────────────────────
SPOT, RATE, SIGMA, DAYS = 100.0, 0.04, 0.28, 60


@pytest.fixture(scope="module")
def rnd():
    """
    알려진 로그정규에서 합성 옵션 체인을 만들어 주입한다.
    yfinance 를 갈아 끼우므로 네트워크를 타지 않는다.
    """
    from engine.jiqtx.options import bs_price
    import engine.jiqtx.options as O

    T = DAYS / 365.0
    Ks = np.round(np.linspace(SPOT * 0.60, SPOT * 1.45, 60), 2)

    def chain():
        mk = lambda cp: pd.DataFrame({
            "strike": Ks,
            "lastPrice": [bs_price(SPOT, k, T, RATE, SIGMA, cp) for k in Ks],
            "openInterest": np.full(len(Ks), 500.0),
            "bid": np.nan, "ask": np.nan})
        return types.SimpleNamespace(calls=mk(True), puts=mk(False))

    class FakeTicker:
        def __init__(self, *a, **k): pass
        @property
        def options(self):
            d = pd.Timestamp.today().normalize() + pd.Timedelta(days=DAYS)
            return [d.strftime("%Y-%m-%d")]
        def option_chain(self, e):
            return chain()

    saved = sys.modules.get("yfinance")
    fake = types.ModuleType("yfinance")
    fake.Ticker = FakeTicker
    sys.modules["yfinance"] = fake
    try:
        yield O.risk_neutral_density("FAKE", SPOT, target_days=DAYS, r=RATE)
    finally:
        if saved is not None:
            sys.modules["yfinance"] = saved
        else:
            sys.modules.pop("yfinance", None)


def _lognormal_truth():
    from scipy import stats as st
    T = DAYS / 365.0
    mu = math.log(SPOT) + (RATE - 0.5 * SIGMA ** 2) * T
    sd = SIGMA * math.sqrt(T)
    mean = math.exp(mu + 0.5 * sd ** 2)
    return {
        "median": math.exp(mu),
        "mean": mean,
        "std": mean * math.sqrt(math.exp(sd ** 2) - 1),
        "q05": math.exp(mu + sd * st.norm.ppf(0.05)),
        "q95": math.exp(mu + sd * st.norm.ppf(0.95)),
    }


def test_rnd_is_produced(rnd):
    assert rnd is not None, "합성 체인으로도 RND 를 못 만들었다"


@pytest.mark.parametrize("field,key,tol", [
    ("q50", "median", 0.01), ("implied_mean", "mean", 0.01),
    ("implied_std", "std", 0.03), ("q05", "q05", 0.02), ("q95", "q95", 0.02),
])
def test_rnd_recovers_the_lognormal(rnd, field, key, tol):
    """
    Breeden-Litzenberger (∂²C/∂K² = e^{rT}·f_Q) 가 넣은 분포를 되찾아야 한다.
    여기가 틀리면 시장이 함의하는 분포를 잘못 읽는 것이다.
    """
    truth = _lognormal_truth()[key]
    got = getattr(rnd, field)
    assert abs(got - truth) / truth < tol, f"{field} {got:.4f} vs 이론 {truth:.4f}"


def test_rnd_is_a_proper_density(rnd):
    """밀도는 음수가 없고 적분이 1, CDF 는 단조여야 한다."""
    assert (rnd.density >= 0).all(), "음수 밀도"
    area = (np.trapezoid(rnd.density, rnd.strikes)
            if hasattr(np, "trapezoid") else np.trapz(rnd.density, rnd.strikes))
    assert abs(area - 1.0) < 1e-3, f"적분 {area:.6f}"
    assert np.all(np.diff(rnd.cdf) >= -1e-12), "CDF 가 감소한다"
    assert rnd.q05 < rnd.q25 < rnd.q50 < rnd.q75 < rnd.q95, "분위수 순서"
