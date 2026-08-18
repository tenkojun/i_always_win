"""
================================================================
  종목 단/중/장 + 기관급 분석 엔진  -  main.py
================================================================
파이프라인
----------
1) 데이터 로드              (yfinance 또는 합성)
2) 단/중/장 타임프레임 분석
     - 추세 / 모멘텀 / 변동성 / 리스크 / 오더플로우 / ML / Regime
3) 기관급 분석 (BlackRock Aladdin식)
     - 미래 주가 몬테카를로 (단/중/장 경로 + 분포)
     - 팩터 위험 분해 (체계적 vs 고유)
     - 시나리오 스트레스 테스트
     - 몬테카를로 부(富) 예측 (목표 달성 확률)
     - 자산배분 · 리스크 버짓
4) 기관 스코어카드 (포트폴리오 카드) + 모듈별 한글 분석 글
5) 시각화 + JSON / HTML 리포트 생성

Google Colab 사용법
-------------------
  !pip install -r requirements.txt
  from main import analyze
  res = analyze('AAPL', start='2018-01-01')

  # 한국 종목
  res = analyze('005930.KS')

  # 인터넷 없이 데모
  res = analyze('DEMO', use_synthetic=True)
"""
from __future__ import annotations
from typing import Any, Dict, Optional
import warnings
warnings.filterwarnings("ignore")

import numpy as np
import pandas as pd

from engine.data.loader import load_ticker, synthetic_ohlcv
from engine.analysis.timeframe import analyze_ticker, DEFAULT_TIMEFRAMES
from engine.report import plotter
from engine.report.report_builder import build_report
from engine.risk.montecarlo import monte_carlo
from engine.orderflow.cvd import cvd as _cvd
from engine.ml.regime import RegimeDetector

from engine.factor.fama_french import synthetic_ff_factors
from engine.institutional import (
    mc_by_timeframe, factor_risk_decomposition,
    stress_test, wealth_projection, risk_budget, build_scorecard,
    compute_tail_risk, precision_diagnostics,
)
from engine.institutional import narrative as nv
from engine.signal_engine import decide
from engine.explain import build_explanation


# ------------------------------------------------------------------ #
def _run_institutional(df: pd.DataFrame,
                       results: Dict[str, Any],
                       initial_capital: float = 10_000_000,
                       target_vol: float = 0.10,
                       use_synth: bool = False) -> Dict[str, Any]:
    """기관급 분석 전체를 실행하고 dict 로 묶는다."""
    close = df["close"]
    rets = close.pct_change().dropna()

    # 1) 미래 주가 몬테카를로 (단/중/장)
    mc_tf = mc_by_timeframe(df, DEFAULT_TIMEFRAMES, method="gbm", n_sim=3000)

    # 2) 팩터 위험 분해 (실제 시장지수 시도 → 실패 시 근사 프록시)
    from engine.institutional.market_proxy import build_factor_panel
    factors, is_real_mkt = build_factor_panel(
        rets, ticker=results.get("ticker", ""), try_real=not use_synth)
    factor = factor_risk_decomposition(rets, factors)
    if factor:
        factor["market_is_real"] = bool(is_real_mkt)

    # 3) 시나리오 스트레스 테스트 (시장 베타 사용)
    beta_mkt = factor.get("betas", {}).get("MKT", 1.0) if factor else 1.0
    stress = stress_test(rets, current_price=float(close.iloc[-1]),
                         beta_mkt=beta_mkt)

    # 4) 몬테카를로 부 예측 (중기 1년 기준)
    mc_mid = mc_tf.get("중기", {})
    wealth = wealth_projection(mc_mid, initial=initial_capital)

    # 5) 리스크 버짓
    budget = risk_budget(rets, target_vol=target_vol)

    # 6) 기관 스코어카드
    scorecard = build_scorecard(results, mc_tf, factor, stress)

    # 6b) 테일 리스크 (앙상블 MC + EVT/GPD + CSCV/PBO)
    regime_labels = results.get("timeframes", {}).get("중기", {}).get(
        "regime", {}).get("labels")
    regime_arr = np.array(regime_labels) if regime_labels is not None else None
    tail_risk = compute_tail_risk(rets, horizon=252, n_sim=10_000,
                                  regimes=regime_arr, run_cscv=True, seed=42)

    # 6c) 기관급 정밀도 진단 (PSR/DSR/MinTRL/CDaR — Bailey & López de Prado)
    equity_curve = (1.0 + rets).cumprod()
    precision = precision_diagnostics(rets, equity=equity_curve,
                                      regimes=regime_arr, n_trials=12)

    # 6d) 메타 의사결정 (Evidence → Confidence → Conflict → Verdict)
    tfs_raw = results.get("timeframes", {})
    regime_state = "stable"
    mid_regime = tfs_raw.get("중기", {}).get("regime", {})
    if mid_regime.get("transition_prob", 0.0) > 0.40:
        regime_state = "transition"
    elif mid_regime.get("regime_state") in ("unknown", "불명"):
        regime_state = "unknown"
    meta = decide(
        timeframes=tfs_raw,
        precision=precision,
        stress=stress,
        factor=factor,
        data_confidence="medium",
        regime_state=regime_state,
    )
    explanation = build_explanation(meta, precision=precision,
                                    stress=stress, factor=factor)

    # 7) 모듈별 한글 분석 글
    tfs = results.get("timeframes", {})
    mid = tfs.get("중기", {})
    narratives = {
        "montecarlo":  nv.narrate_montecarlo(mc_mid),
        "factor_risk": nv.narrate_factor_risk(factor),
        "stress":      nv.narrate_stress(stress),
        "wealth":      nv.narrate_wealth(wealth),
        "risk_budget": nv.narrate_risk_budget(budget),
        "tail_risk":   nv.narrate_tail_risk(tail_risk),
        "trend":       nv.narrate_trend(mid.get("trend", {})),
        "momentum":    nv.narrate_momentum(mid.get("momentum", {})),
        "volatility":  nv.narrate_volatility(mid.get("volatility", {})),
        "risk":        nv.narrate_risk(mid.get("risk", {})),
        "orderflow":   nv.narrate_orderflow(mid.get("orderflow", {})),
        "ml":          nv.narrate_ml(mid.get("ml", {})),
        "regime":      nv.narrate_regime(mid.get("regime", {})),
    }
    overall_text = nv.narrate_overall(results, scorecard)

    return {
        "mc_tf":        mc_tf,
        "factor":       factor,
        "stress":       stress,
        "wealth":       wealth,
        "budget":       budget,
        "scorecard":    scorecard,
        "tail_risk":    tail_risk,
        "precision":    precision,
        "meta":         meta,
        "explanation":  explanation,
        "narratives":   narratives,
        "overall_text": overall_text,
    }


# ------------------------------------------------------------------ #
def _add_plots(df: pd.DataFrame,
               results: Dict[str, Any],
               inst: Dict[str, Any]) -> Dict[str, Any]:
    """리포트용 그래프 생성."""
    plots: Dict[str, Any] = {}

    # 점수 막대 차트 (단/중/장 비교)
    score_dict = {
        name: tf.get("score", 50.0)
        for name, tf in results["timeframes"].items()
        if "score" in tf
    }
    if score_dict:
        plots["score_bars"] = plotter.plot_score_bars(score_dict)

    # 기관 스코어카드 레이더
    try:
        plots["scorecard"] = plotter.plot_scorecard(inst["scorecard"])
    except Exception:
        pass

    # 미래 주가 몬테카를로 (단/중/장 통합 + 분포)
    try:
        plots["mc_multi"] = plotter.plot_mc_multi(inst["mc_tf"])
        for name, proj in inst["mc_tf"].items():
            if isinstance(proj, dict) and "terminal" in proj:
                plots[f"mc_dist_{name}"] = plotter.plot_mc_dist(proj)
    except Exception:
        pass

    # 팩터 위험 / 스트레스
    try:
        plots["factor_risk"] = plotter.plot_factor_risk(inst["factor"])
    except Exception:
        pass
    try:
        plots["stress"] = plotter.plot_stress(inst["stress"])
    except Exception:
        pass

    # 가격 + 이동평균
    plots["price"] = plotter.plot_price_with_ma(df, ma_fast=20, ma_slow=60,
                                                title="가격 + SMA 20/60")
    # 낙폭 / 수익률 분포 (전체 기간)
    close = df["close"]
    rets = close.pct_change().fillna(0)
    eq = (1 + rets).cumprod() * 100
    plots["drawdown"]     = plotter.plot_drawdown(eq)
    plots["returns_hist"] = plotter.plot_returns_hist(rets)

    # CVD
    try:
        cvd_s = _cvd(df)
        plots["cvd"] = plotter.plot_cvd(close, cvd_s)
    except Exception:
        pass

    # Regime (전체 기간)
    try:
        labels = RegimeDetector("kmeans", n_states=3).fit_predict(rets)
        plots["regime"] = plotter.plot_regimes(close, labels)
    except Exception:
        pass

    return plots


# ------------------------------------------------------------------ #
def analyze(ticker: str,
            start: str = "1990-01-01",
            end: Optional[str] = None,
            ml_model: str = "rf",
            regime_method: str = "kmeans",
            use_synthetic: bool = False,
            initial_capital: float = 10_000_000,
            target_vol: float = 0.10,
            out_dir: str = "./report_out") -> Dict[str, Any]:
    """
    종목 하나에 대해 단/중/장 + 기관급 분석을 모두 실행하고 리포트를 만든다.

    Parameters
    ----------
    ticker          : 종목 코드 (예: 'AAPL', 'TSLA', '005930.KS', 'BTC-USD')
    start, end      : 데이터 기간
    ml_model        : 'rf' | 'xgb' | 'lstm' | 'gru' | 'transformer'
    regime_method   : 'kmeans' | 'hmm' | 'gmm'
    use_synthetic   : True 면 인터넷 없이 데모 데이터 사용
    initial_capital : 부(富) 예측 시 투자 원금 (기본 1,000만원)
    target_vol      : 리스크 버짓 목표 변동성 (기본 10%)
    out_dir         : 리포트 저장 경로

    Returns
    -------
    dict
        - ticker
        - timeframes: {단기, 중기, 장기}
        - overall_score, overall_signal
        - institutional: {mc_tf, factor, stress, wealth, budget,
                           scorecard, narratives, overall_text}
        - report_paths: {html, json, score, signal}
    """
    print("=" * 64)
    print(f"📥 1) 데이터 로드 : {ticker}")
    if use_synthetic:
        df = synthetic_ohlcv(n=1500)
        print("   (합성 데이터 사용)")
    else:
        df = load_ticker(ticker, start=start, end=end)
    print(f"   기간: {df.index[0].date()} ~ {df.index[-1].date()}, "
          f"{len(df)}개 봉   현재가: {df['close'].iloc[-1]:,.2f}")

    print(f"\n🔍 2) 단/중/장 분석 (ML={ml_model}, Regime={regime_method})")
    results = analyze_ticker(df, ticker=ticker,
                             ml_model=ml_model,
                             regime_method=regime_method)

    print("\n📊 3) 타임프레임 결과")
    for name, tf in results["timeframes"].items():
        if "note" in tf:
            print(f"   {name:4s} : {tf['note']}")
            continue
        print(f"   {name:4s} {tf.get('color','')} {tf.get('signal','?'):4s} "
              f"점수={tf.get('score',0):5.1f}  "
              f"수익률={tf.get('momentum',{}).get('cum_return_pct',0):+6.2f}%  "
              f"샤프={tf.get('risk',{}).get('sharpe',0):5.2f}")

    print(f"\n🎯 4) 종합 시그널: {results['overall_color']} "
          f"{results['overall_signal']} (점수 {results['overall_score']:.1f}/100)")

    print("\n🏛  5) 기관급 분석 (몬테카를로·팩터·스트레스·부예측·버짓)")
    inst = _run_institutional(df, results,
                              initial_capital=initial_capital,
                              target_vol=target_vol,
                              use_synth=use_synthetic)
    results["institutional"] = inst
    sc = inst["scorecard"]
    print(f"   기관 스코어카드: 등급 {sc['overall_grade']} "
          f"({sc['overall_score']:.1f}/100)")
    mc_mid = inst["mc_tf"].get("중기", {})
    if "up_prob" in mc_mid:
        print(f"   중기(1년) 상승확률: {mc_mid['up_prob']:.0f}%  "
              f"기대수익률: {mc_mid.get('exp_return_pct',0):+.1f}%")
    tr = inst.get("tail_risk", {})
    if tr and "ensemble_var" in tr:
        print(f"   테일 리스크: VaR(5%) {tr['ensemble_var']:.1%}  "
              f"CVaR(5%) {tr['ensemble_cvar']:.1%}  "
              f"PBO {tr.get('cscv', {}).get('pbo', 'N/A')}")

    print("\n🖼  6) 차트 생성")
    results["plots"] = _add_plots(df, results, inst)

    print("\n📝 7) 리포트 저장")
    results["recommendation"] = inst["overall_text"]
    paths = build_report(results, out_dir=out_dir)
    print(f"   HTML : {paths['html']}")
    print(f"   JSON : {paths['json']}")
    print("=" * 64)

    results["report_paths"] = paths
    return results


# ------------------------------------------------------------------ #
#  CLI demo
# ------------------------------------------------------------------ #
if __name__ == "__main__":
    res = analyze("DEMO", use_synthetic=True, ml_model="rf")
