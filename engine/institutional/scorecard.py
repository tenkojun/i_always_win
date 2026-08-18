"""
기관 스코어카드 모듈 (Institutional Scorecard)
================================================
단일 종목의 모든 분석 결과를 하나의 **포트폴리오 카드**로 종합한다.
블랙록·기관 IC(Investment Committee) 가 종목을 평가할 때 쓰는
다축 등급표 형식.

평가 축 (Pillar)
----------------
- 수익성 (Return)        : 누적/기대 수익, 알파
- 추세   (Trend)         : 단/중/장 추세 일관성
- 안정성 (Stability)     : 변동성, MDD, Ulcer
- 위험효율(Risk-Adj.)    : 샤프/소르티노/칼마
- 팩터건전성(Factor)     : 고유위험 비중, 분산 가능성
- 시나리오내성(Stress)   : 위기 시 손실 폭
- 상승확률(Probability)  : 몬테카를로 상승/목표달성 확률

각 축을 0~100 으로 채점 → A+ ~ D 등급 부여 →
가중 평균으로 종합 기관 등급 산출.

출력
----
- pillars      : {축이름: {score, grade, comment}}
- overall_score: 0~100
- overall_grade: A+ / A / B+ / B / C / D
- verdict      : 한 줄 기관 의견
"""
from __future__ import annotations
from typing import Any, Dict
import numpy as np


def _grade(score: float) -> str:
    if score >= 85: return "A+"
    if score >= 75: return "A"
    if score >= 65: return "B+"
    if score >= 55: return "B"
    if score >= 45: return "C"
    return "D"


def _clip(x: float) -> float:
    return float(max(0.0, min(100.0, x)))


def build_scorecard(results: Dict[str, Any],
                    mc_tf: Dict[str, Any],
                    factor: Dict[str, Any],
                    stress: Dict[str, Any]) -> Dict[str, Any]:
    """
    Parameters
    ----------
    results : analyze_ticker 결과 (timeframes, overall_score 포함)
    mc_tf   : mc_by_timeframe 결과
    factor  : factor_risk_decomposition 결과
    stress  : stress_test 결과
    """
    tfs = results.get("timeframes", {})

    def tf_val(name, *path, default=0.0):
        d = tfs.get(name, {})
        for p in path:
            d = d.get(p, {}) if isinstance(d, dict) else {}
        return d if not isinstance(d, dict) else default

    # ---- 수익성 ----
    rets = [tf_val(n, "momentum", "cum_return_pct", default=0.0)
            for n in ("단기", "중기", "장기")]
    avg_ret = float(np.mean(rets))
    ret_score = _clip(50 + avg_ret * 0.6)

    # ---- 추세 일관성 ----
    dirs = [tfs.get(n, {}).get("trend", {}).get("trend_direction")
            for n in ("단기", "중기", "장기")]
    up = sum(1 for d in dirs if d == "상승")
    trend_score = _clip({3: 90, 2: 68, 1: 40, 0: 20}.get(up, 50))

    # ---- 안정성 (변동성/MDD) ----
    vol = tf_val("중기", "volatility", "annual_vol", default=0.3) or 0.3
    mdd = abs(tf_val("중기", "risk", "max_drawdown", default=0.3) or 0.3)
    stab_score = _clip(100 - vol * 130 - mdd * 90)

    # ---- 위험효율 (샤프) ----
    sharpe = tf_val("중기", "risk", "sharpe", default=0.0) or 0.0
    risk_score = _clip(50 + sharpe * 22)

    # ---- 팩터 건전성 (고유위험 비중 높을수록 분산투자 가치) ----
    spec = factor.get("specific_pct", 50.0) if factor else 50.0
    r2 = factor.get("r_squared", 0.0) if factor else 0.0
    factor_score = _clip(40 + spec * 0.4 + (1 - r2) * 20)

    # ---- 시나리오 내성 ----
    worst = abs(stress.get("worst_loss_pct", -50.0)) if stress else 50.0
    stress_score = _clip(100 - worst * 1.1)

    # ---- 상승 확률 (몬테카를로 중기) ----
    mc_mid = mc_tf.get("중기", {}) if mc_tf else {}
    up_prob = mc_mid.get("up_prob", 50.0)
    prob_score = _clip(up_prob)

    pillars = {
        "수익성":      {"score": ret_score,    "weight": 0.18},
        "추세 일관성":  {"score": trend_score,  "weight": 0.15},
        "안정성":      {"score": stab_score,   "weight": 0.15},
        "위험효율":     {"score": risk_score,   "weight": 0.18},
        "팩터 건전성":  {"score": factor_score, "weight": 0.10},
        "시나리오 내성": {"score": stress_score, "weight": 0.12},
        "상승 확률":    {"score": prob_score,   "weight": 0.12},
    }

    comments = {
        "수익성":      f"단/중/장 평균 누적수익 {avg_ret:+.1f}%",
        "추세 일관성":  f"단·중·장 중 {up}개 구간이 상승 추세",
        "안정성":      f"연변동성 {vol*100:.0f}% · MDD {mdd*100:.0f}%",
        "위험효율":     f"중기 샤프 {sharpe:.2f}",
        "팩터 건전성":  f"종목 고유위험 {spec:.0f}% (분산 여지)",
        "시나리오 내성": f"최악 시나리오 손실 {-worst:.0f}%",
        "상승 확률":    f"몬테카를로 상승확률 {up_prob:.0f}%",
    }

    for k, v in pillars.items():
        v["grade"] = _grade(v["score"])
        v["comment"] = comments.get(k, "")

    overall = sum(v["score"] * v["weight"] for v in pillars.values())
    overall = float(round(overall, 1))
    grade = _grade(overall)

    if overall >= 70:
        verdict = ("기관 관점 우호적(Overweight) — 위험 대비 기대수익이 "
                   "양호하며 추세·확률 지표가 우상향을 지지합니다.")
    elif overall >= 55:
        verdict = ("중립(Neutral) — 일부 축은 양호하나 변동성·시나리오 "
                   "위험이 상존합니다. 분할·관망 권고.")
    else:
        verdict = ("기관 관점 비우호적(Underweight) — 위험 효율과 추세가 "
                   "약하여 신규 비중 확대는 신중해야 합니다.")

    return {
        "pillars":       pillars,
        "overall_score": overall,
        "overall_grade": grade,
        "verdict":       verdict,
    }
