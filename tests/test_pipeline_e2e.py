# -*- coding: utf-8 -*-
"""
파이프라인 end-to-end — 합성 데이터로 전 구간을 돌린다.

개별 추정량은 `test_estimators.py` 가 본다. 여기서는 **엔진 전체를 통과한
숫자가 서로 모순되지 않는가**를 본다. 부분이 다 맞아도 조립이 틀리면
사용자가 보는 값은 틀린다.

네트워크를 타지 않는다 — `analyze()` 에 df/macro/proxies 를 직접 주입한다.

한 번 도는 데 십수 초 걸리므로 모듈 스코프로 한 번만 계산해 공유한다.
"""
from __future__ import annotations

import math
import warnings

import numpy as np
import pandas as pd
import pytest

warnings.filterwarnings("ignore")


def make_ohlcv(n=1500, seed=7, mu=0.08, sigma=0.22, start="2019-01-02", px0=100.0):
    """
    GBM 일봉. 연율 드리프트·변동성을 **의도해서** 넣는다.

    신호가 없는 데이터다 — 엔진이 여기서 방향 우위를 주장하면 그게 곧
    과적합이라는 증거다.
    """
    rng = np.random.default_rng(seed)
    dt = 1 / 252
    r = rng.normal((mu - 0.5 * sigma ** 2) * dt, sigma * math.sqrt(dt), n)
    close = px0 * np.exp(np.cumsum(r))
    intr = np.abs(rng.normal(0, sigma * math.sqrt(dt) * 0.6, n))
    open_ = np.concatenate([[px0], close[:-1]]) * (1 + rng.normal(0, 1e-4, n))
    high = np.maximum.reduce([close * (1 + intr), close, open_])
    low = np.minimum.reduce([close * (1 - intr), close, open_])
    vol = rng.lognormal(15.0, 0.4, n)
    return pd.DataFrame({"open": open_, "high": high, "low": low,
                         "close": close, "volume": vol},
                        index=pd.bdate_range(start, periods=n))


def _run(df, **kw):
    from engine.jiqtx.pipeline import analyze
    opts = dict(meta={"assetClass": "equity"}, macro=pd.DataFrame(), proxies={},
                with_options=False, with_ml=True, fast=True, verbose=False)
    opts.update(kw)
    return analyze("SYNTH", df=df, **opts)


@pytest.fixture(scope="module")
def result():
    return _run(make_ohlcv())


# ── 컬럼 규약 ────────────────────────────────────────────────
def test_accepts_lowercase_columns():
    """
    CLAUDE.md 가 문서화한 계약은 **소문자** 다. 그런데 jiqtx 내부는
    대문자를 요구해서, 문서를 따라 주입하면 KeyError 로 죽었다.
    운영에서는 jiqtx 가 자기 수집 경로를 써서 드러나지 않던 구멍이다.
    """
    from engine.jiqtx.pipeline import _normalize_ohlcv
    lower = make_ohlcv(n=60)
    out = _normalize_ohlcv(lower)
    for c in ("Open", "High", "Low", "Close", "Volume"):
        assert c in out.columns, f"{c} 로 정규화되지 않았다"


def test_accepts_uppercase_columns_unchanged():
    """이미 대문자면 건드리지 않아야 한다."""
    from engine.jiqtx.pipeline import _normalize_ohlcv
    up = make_ohlcv(n=60).rename(columns=str.capitalize)
    out = _normalize_ohlcv(up)
    assert list(out.columns) == list(up.columns)


# ── 감사 가능성 ──────────────────────────────────────────────
@pytest.mark.slow
def test_same_input_gives_same_verdict():
    """
    같은 입력이면 같은 소견이 나와야 한다 — 감사 가능성의 전제다.
    난수 시드가 새면 어제와 오늘의 판정이 달라지고, 그러면 왜 그렇게
    나왔는지 아무도 설명할 수 없다.
    """
    df = make_ohlcv()
    a, b = _run(df), _run(df)
    assert a.verdict.grade == b.verdict.grade
    assert a.verdict.direction_prob == pytest.approx(b.verdict.direction_prob,
                                                     abs=1e-9)
    assert a.verdict.risk_budget_weight == pytest.approx(
        b.verdict.risk_budget_weight, abs=1e-9)


# ── 신호 없는 데이터에 신호를 만들지 않는가 ─────────────────
def test_no_edge_claimed_on_pure_noise(result):
    """
    이 엔진의 핵심 주장이다 — 검정을 통과 못 하면 **출력을 없앤다.**
    순수 GBM 은 방향 정보가 없으므로, 신뢰구간이 50%를 포함해야 하고
    비중을 실으면 안 된다.
    """
    v = result.verdict
    lo, hi = v.direction_ci
    assert lo <= 0.5 <= hi, (
        f"신호 없는 데이터에서 방향 우위를 주장했다: {v.direction_prob:.3f} "
        f"CI [{lo:.3f}, {hi:.3f}]")
    assert v.risk_budget_weight == 0.0, \
        f"근거 없이 비중 {v.risk_budget_weight:.1%} 를 실었다"


def test_probabilities_are_probabilities(result):
    v = result.verdict
    assert 0.0 <= v.direction_prob <= 1.0
    lo, hi = v.direction_ci
    assert 0.0 <= lo <= hi <= 1.0, f"신뢰구간이 뒤집혔다: [{lo}, {hi}]"


# ── 리스크 수치의 정합성 ─────────────────────────────────────
def test_expected_shortfall_exceeds_var(result):
    """
    ES 는 VaR 너머 손실의 **평균**이므로 정의상 VaR 보다 크다.
    작게 나오면 꼬리 위험을 과소평가하는 것이고, 리스크 시스템에서
    가장 위험한 방향의 오류다.

    실제로 FHS-EVT 쪽 ES 공식의 부호가 뒤집혀 있었다 — 좌측 꼬리로
    되돌릴 때 항의 부호를 놓쳤다. 몬테카를로 기준 t(4)에서 진짜 2.27 을
    0.73 으로 냈다.
    """
    var = result.var
    for tag in ("historical", "fhs_evt"):
        v = getattr(var, f"var_{tag}")
        e = getattr(var, f"es_{tag}")
        if not (np.isfinite(v) and np.isfinite(e)):
            continue
        assert e >= v, f"{tag}: ES {e:.5f} < VaR {v:.5f}"


def test_var_estimates_are_positive_and_sane(result):
    """VaR 은 손실 크기라 양수여야 하고, 하루에 100% 를 넘을 수 없다."""
    var = result.var
    for tag in ("normal", "historical", "fhs_evt"):
        v = getattr(var, f"var_{tag}")
        if not np.isfinite(v):
            continue
        assert 0 < v < 1.0, f"{tag} VaR {v}"


def test_var_backtest_is_reported(result):
    """
    커버리지 검정 없이 VaR 을 내면 그 숫자를 믿을 근거가 없다.
    Kupiec/Christoffersen p값이 함께 나와야 한다.
    """
    bt = result.var.backtest
    assert bt, "백테스트 결과가 비어 있다"
    for name, d in bt.items():
        assert 0.0 <= d["hit_rate"] <= 1.0
        for k in ("kupiec_p", "independence_p", "cc_p"):
            p = d.get(k)
            assert p is None or (not np.isfinite(p)) or 0.0 <= p <= 1.0, \
                f"{name}.{k} = {p}"


def test_var_coverage_is_close_to_alpha(result):
    """
    합성 데이터는 분포를 아니까 커버리지가 맞아야 한다. 크게 어긋나면
    VaR 계산이나 표준화가 틀린 것이다.
    """
    bt = result.var.backtest
    alpha = result.var.alpha
    hist = bt.get("historical")
    assert hist, "historical 백테스트가 없다"
    assert abs(hist["hit_rate"] - alpha) < 0.03, (
        f"위반율 {hist['hit_rate']:.1%} vs 목표 {alpha:.1%}")


# ── 사이징 ───────────────────────────────────────────────────
def test_final_weight_is_within_every_cap(result):
    """
    여러 제약 중 **가장 강하게 묶는 것**이 최종 비중이어야 한다.
    어느 하나라도 넘으면 그 제약을 둔 의미가 없다.
    """
    s = result.sizing
    w = s.final_weight
    assert 0.0 <= w <= 1.0, f"비중 {w}"
    for cap in ("vol_target_weight", "stress_cap", "liquidity_cap", "class_cap"):
        c = getattr(s, cap, None)
        if c is None or not np.isfinite(c):
            continue
        assert w <= c + 1e-9, f"{cap}={c:.4f} 인데 최종 비중이 {w:.4f}"
    assert s.binding_constraint, "무엇이 묶었는지 밝히지 않았다"


# ── 게이트 ───────────────────────────────────────────────────
def test_clean_data_passes_integrity(result):
    assert result.integrity.passed, f"합성 데이터가 무결성 실패: {result.integrity.issues}"
    assert result.integrity.ohlc_violations == 0
    assert result.integrity.nonpositive_prices == 0


@pytest.mark.slow
def test_broken_ohlc_is_caught():
    """
    High < Low 같은 모순은 반드시 잡아야 한다. 이걸 통과시키면 뒤의
    모든 계산이 쓰레기 위에서 돈다.
    """
    df = make_ohlcv(n=400)
    df.loc[df.index[50:80], "high"] = df["low"].iloc[50:80] * 0.5   # 고가 < 저가
    from engine.jiqtx import data as dta
    from engine.jiqtx.pipeline import _normalize_ohlcv
    integ = dta.check_integrity("BROKEN", _normalize_ohlcv(df), 0.02)
    assert integ.ohlc_violations > 0, "OHLC 모순을 못 잡았다"
    assert not integ.passed, "모순이 있는데 무결성을 통과시켰다"


# ── NaN 누수 ─────────────────────────────────────────────────
def test_no_nan_in_headline_numbers(result):
    """
    사용자가 보는 숫자에 NaN 이 새면 화면에 그대로 나간다.
    없으면 섹션을 내려야지, NaN 을 보여 주면 안 된다.
    """
    v = result.verdict
    assert np.isfinite(v.direction_prob)
    assert np.isfinite(v.risk_budget_weight)
    assert all(np.isfinite(x) for x in v.direction_ci)
    assert np.isfinite(result.sizing.final_weight)
    assert result.verdict.grade, "판정 등급이 비었다"


def test_performance_summary_is_finite(result):
    bad = [k for k, x in result.perf.items()
           if isinstance(x, float) and not np.isfinite(x)]
    assert not bad, f"성과 지표에 NaN/inf: {bad}"


# ── 변동성 ───────────────────────────────────────────────────
def test_annualized_vol_is_in_the_right_ballpark(result):
    """
    합성 데이터에 연율 22% 를 넣었다. GARCH 가 그 근처를 내야 한다.
    연율화 계수(√252)가 틀리면 여기서 자릿수로 어긋난다.
    """
    g = result.vol_profile.garch
    assert 0.10 < g.ann_vol_longrun < 0.40, (
        f"장기 변동성 {g.ann_vol_longrun:.1%} (넣은 값 22%)")
    assert g.ann_vol_current > 0
    assert 0 < g.persistence < 1.0, f"지속성 {g.persistence}"
