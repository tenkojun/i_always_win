"""
한글 분석 글 자동 생성기 (Narrative Generator)
================================================
각 분석 모듈의 숫자 결과를 받아, 비전문가도 이해할 수 있는
**기관 리서치 코멘트 형식의 한글 문장**으로 풀어 쓴다.

예) 몬테카를로 →
  "향후 1년 후 이 종목이 현재가보다 오를 확률은 58%로 추정됩니다.
   예상 중앙값은 12,300원이며, 90% 신뢰구간은 8,900~17,400원으로
   상·하 표준편차가 ±24%에 달해 변동성이 큰 편입니다. ..."

모든 함수는 문자열(여러 문장)을 반환한다. 결과가 없으면
"분석 불가" 안내 문장을 돌려준다.
"""
from __future__ import annotations
from typing import Any, Dict


def _won(x: float) -> str:
    """숫자를 보기 좋은 통화형 문자열로."""
    try:
        if abs(x) >= 1000:
            return f"{x:,.0f}"
        return f"{x:,.2f}"
    except Exception:
        return str(x)


# ------------------------------------------------------------------ #
#  몬테카를로
# ------------------------------------------------------------------ #
def narrate_montecarlo(mc: Dict[str, Any]) -> str:
    if not mc or "up_prob" in mc and mc.get("note"):
        return "데이터가 부족하여 몬테카를로 예측을 수행하지 못했습니다."
    if mc.get("note"):
        return f"몬테카를로 예측 불가: {mc['note']}"

    name = mc.get("name", "")
    h = mc.get("horizon_days", 0)
    hm = (f"약 {h//21}개월" if h < 252 else
          f"약 {round(h/252,1)}년")
    up = mc.get("up_prob", 0)
    med = mc.get("median_price", 0)
    s0 = mc.get("start_price", 0)
    lo, hi = mc.get("ci90_low", 0), mc.get("ci90_high", 0)
    std = mc.get("std_pct", 0)
    exp_r = mc.get("exp_return_pct", 0)
    p10, p20 = mc.get("prob_up_10", 0), mc.get("prob_up_20", 0)
    d10, d20 = mc.get("prob_dn_10", 0), mc.get("prob_dn_20", 0)
    var = mc.get("var_95_pct", 0)
    skew = mc.get("skew", 0)

    vol_word = ("매우 큰" if std > 35 else "큰" if std > 22 else
                "보통 수준의" if std > 12 else "작은")
    tilt = ("위쪽으로 치우쳐(상승 쪽 꼬리가 두꺼움) 큰 폭의 상승 여지가 있는"
            if skew > 0.3 else
            "아래쪽으로 치우쳐(하락 쪽 꼬리가 두꺼움) 급락 위험이 상대적으로 큰"
            if skew < -0.3 else
            "비교적 대칭적인")

    parts = [
        f"[{name}] {hm} 후를 {mc.get('method','gbm').upper()} 방식으로 "
        f"{len(mc.get('terminal', []))}회 시뮬레이션한 결과입니다.",
        f"현재가({_won(s0)}) 대비 상승할 확률은 약 {up:.0f}%로 추정됩니다.",
        f"예상 중앙값 가격은 {_won(med)}이고, 90% 신뢰구간은 "
        f"{_won(lo)} ~ {_won(hi)}입니다.",
        f"종착 수익률의 표준편차가 ±{std:.0f}%로 {vol_word} 변동성을 보이며, "
        f"분포는 {tilt} 형태입니다.",
        f"+10% 이상 오를 확률은 {p10:.0f}%, +20% 이상은 {p20:.0f}%인 반면, "
        f"-10% 이하로 빠질 확률은 {d10:.0f}%, -20% 이하는 {d20:.0f}%입니다.",
        f"기대수익률은 {exp_r:+.1f}%, 95% 신뢰수준 최대손실(VaR)은 "
        f"약 {var:.0f}% 수준으로 평가됩니다.",
    ]
    return " ".join(parts)


# ------------------------------------------------------------------ #
#  팩터 위험 분해
# ------------------------------------------------------------------ #
def narrate_factor_risk(f: Dict[str, Any]) -> str:
    if not f or f.get("note"):
        return f"팩터 위험 분석 불가: {f.get('note','데이터 부족') if f else '데이터 부족'}"

    labels = f.get("factor_labels", {})
    sysp = f.get("systematic_pct", 0)
    spec = f.get("specific_pct", 0)
    betas = f.get("betas", {})
    bmkt = betas.get("MKT", 1.0)
    alpha = f.get("alpha_ann", 0) * 100
    r2 = f.get("r_squared", 0) * 100
    top = f.get("top_driver", "")
    topn = labels.get(top, top)
    topp = f.get("top_driver_pct", 0)

    beta_word = ("시장보다 더 크게 출렁이는 고베타" if bmkt > 1.2 else
                 "시장보다 둔감한 저베타" if bmkt < 0.8 else
                 "시장과 비슷하게 움직이는")
    div_word = ("종목 고유 요인이 커서 분산투자로 위험을 낮출 여지가 큽니다"
                if spec > 55 else
                "위험 대부분이 시장 전체와 연동되어 분산 효과가 제한적입니다")

    proxy_note = ("" if f.get("market_is_real")
                  else " (※ 시장지수 데이터가 없어 근사 프록시로 추정한 값입니다)")

    return (
        f"이 종목 위험의 약 {sysp:.0f}%는 시장·규모·가치 등 공통 팩터에서, "
        f"나머지 {spec:.0f}%는 종목 고유 요인에서 발생합니다. "
        f"시장 베타는 {bmkt:.2f}로 {beta_word} 특성을 보입니다. "
        f"위험을 가장 키우는 요인은 '{topn}'(기여 {topp:.0f}%)입니다. "
        f"팩터로 설명되지 않는 연환산 알파는 {alpha:+.1f}%, "
        f"모델 설명력(R²)은 {r2:.0f}%입니다. "
        f"결론적으로 {div_word}.{proxy_note}"
    )


# ------------------------------------------------------------------ #
#  스트레스 테스트
# ------------------------------------------------------------------ #
def narrate_stress(s: Dict[str, Any]) -> str:
    if not s or not s.get("scenarios"):
        return "스트레스 테스트를 수행할 데이터가 없습니다."

    scen = s["scenarios"]
    worst = s.get("worst_case", "")
    worst_loss = s.get("worst_loss_pct", 0)
    beta = s.get("beta_used", 1.0)

    lines = [
        f"시장 베타 {beta:.2f}를 적용한 위기 시나리오 분석입니다."
    ]
    for nm, d in scen.items():
        rec = d.get("recovery_days", 0)
        rec_txt = (f", 평소 상승속도 기준 회복에 약 {rec}영업일"
                   f"(≈{round(rec/21)}개월) 소요 추정"
                   if rec > 0 else "")
        lines.append(
            f"· {nm}: 예상 {d['shock_pct']:+.0f}% "
            f"(예상가 {_won(d['price_after'])}){rec_txt}."
        )
    lines.append(
        f"가장 취약한 시나리오는 '{worst}'로 약 {worst_loss:.0f}% 손실이 "
        f"예상됩니다. 이는 보수적 가정이며 실제와 다를 수 있습니다."
    )
    return " ".join(lines)


# ------------------------------------------------------------------ #
#  부(富) 예측
# ------------------------------------------------------------------ #
def narrate_wealth(w: Dict[str, Any]) -> str:
    if not w or w.get("note"):
        return "부 예측을 수행할 데이터가 없습니다."

    init = w.get("initial", 0)
    yrs = w.get("horizon_years", 1)
    expv = w.get("exp_value", 0)
    medv = w.get("median_value", 0)
    p5, p95 = w.get("p5_value", 0), w.get("p95_value", 0)
    ploss = w.get("prob_loss", 0)
    pinf = w.get("prob_beat_infl", 0)
    pdep = w.get("prob_beat_deposit", 0)

    goal_txt = "  ".join(
        f"[{k} → {v['prob']:.0f}%]" for k, v in w.get("goals", {}).items()
    )

    return (
        f"원금 {_won(init)}원을 투자해 약 {yrs}년 보유한다고 가정하면, "
        f"기대 평가금액은 {_won(expv)}원(중앙값 {_won(medv)}원)입니다. "
        f"90% 구간은 {_won(p5)} ~ {_won(p95)}원입니다. "
        f"목표 달성 확률 — {goal_txt}. "
        f"원금 손실 확률은 {ploss:.0f}%이며, "
        f"물가({w.get('infl_target_pct',0):.0f}%) 초과 확률 {pinf:.0f}%, "
        f"예금({w.get('deposit_target_pct',0):.0f}%) 초과 확률 {pdep:.0f}%입니다."
    )


# ------------------------------------------------------------------ #
#  리스크 버짓
# ------------------------------------------------------------------ #
def narrate_risk_budget(b: Dict[str, Any]) -> str:
    if not b or b.get("note"):
        return "리스크 버짓 산출 불가 (데이터 부족)."

    av = b.get("ann_vol", 0) * 100
    tv = b.get("target_vol", 0) * 100
    fw = b.get("final_weight", 0) * 100
    cw = b.get("cash_weight", 0) * 100
    hk = b.get("half_kelly", 0) * 100
    epv = b.get("expected_port_vol", 0) * 100

    if fw >= 100:
        pos_txt = (f"이론적으로는 100%를 초과(레버리지 {fw:.0f}%)해도 "
                   f"목표 변동성 내이지만, 차입 위험을 감안해 100% 이내 권고")
    else:
        pos_txt = (f"전체 자산의 약 {fw:.0f}%를 이 종목에, "
                   f"나머지 {cw:.0f}%는 현금/안전자산에 두는 배분이 적정")

    return (
        f"이 종목의 연변동성은 {av:.0f}%입니다. "
        f"포트폴리오 목표 변동성을 {tv:.0f}%로 둘 경우, {pos_txt}합니다. "
        f"하프 켈리 기준 비중은 {hk:.0f}%이며, 권장 비중 적용 시 "
        f"예상 포트폴리오 변동성은 {epv:.0f}% 수준입니다. "
        f"기관은 단일 종목 100% 보유 대신 이런 변동성 타깃팅으로 "
        f"위험을 통제합니다."
    )


# ------------------------------------------------------------------ #
#  기존 분석 블록 (추세/모멘텀/변동성/리스크/오더플로우/ML/국면)
# ------------------------------------------------------------------ #
def narrate_trend(t: Dict[str, Any]) -> str:
    if not t:
        return ""
    dirn = t.get("trend_direction", "?")
    slope = t.get("sma_slope_pct", 0)
    above = t.get("above_sma_ratio", 0) * 100
    cross = t.get("ma_cross_diff", 0)
    cw = "골든크로스(단기선이 장기선 위)" if cross > 0 else "데드크로스(단기선이 장기선 아래)"
    return (f"추세는 '{dirn}' 국면입니다. 장기 이동평균 기울기는 "
            f"{slope:+.1f}%이고, 구간 내 종가가 장기선 위에 머문 비율은 "
            f"{above:.0f}%입니다. 현재 {cw} 상태로, "
            f"{'상승 모멘텀이 유효' if cross>0 else '하락 압력이 우세'}합니다.")


def narrate_momentum(m: Dict[str, Any]) -> str:
    if not m:
        return ""
    rsi = m.get("rsi_last", 50)
    st = m.get("rsi_state", "중립")
    cum = m.get("cum_return_pct", 0)
    roc = m.get("roc_20_pct", 0)
    add = ("과매수 영역으로 단기 조정 가능성에 유의" if st == "과매수"
           else "과매도 영역으로 기술적 반등 여지" if st == "과매도"
           else "중립 영역으로 추세 추종에 무리가 없음")
    return (f"구간 누적 수익률은 {cum:+.1f}%, 최근 20일 변화율은 {roc:+.1f}%입니다. "
            f"RSI(14)는 {rsi:.0f}으로 '{st}' 상태이며, {add}합니다.")


def narrate_volatility(v: Dict[str, Any]) -> str:
    if not v:
        return ""
    av = v.get("annual_vol", 0) * 100
    reg = v.get("vol_regime", "보통")
    return (f"연환산 변동성은 {av:.0f}%로 '{reg}' 국면입니다. "
            f"{'변동성이 높아 포지션 축소·분할매수가 유리' if reg=='높음' else '변동성이 안정적이어서 추세 전략 적용에 무난' if reg=='낮음' else '평이한 변동성 수준'}합니다.")


def narrate_risk(r: Dict[str, Any]) -> str:
    if not r:
        return ""
    sh = r.get("sharpe", 0)
    so = r.get("sortino", 0)
    cal = r.get("calmar", 0)
    mdd = r.get("max_drawdown", 0) * 100
    sh_w = ("위험 대비 수익이 우수" if sh > 1 else
            "위험 대비 수익이 양호" if sh > 0.5 else
            "위험 대비 수익이 보통" if sh > 0 else
            "위험 대비 수익이 저조")
    return (f"샤프 {sh:.2f} · 소르티노 {so:.2f} · 칼마 {cal:.2f}로 "
            f"{sh_w}합니다. 최대낙폭(MDD)은 {mdd:.0f}%로 "
            f"{'손실 통제가 양호' if abs(mdd)<20 else '낙폭 위험이 큼'}합니다.")


def narrate_orderflow(o: Dict[str, Any]) -> str:
    if not o:
        return ""
    chg = o.get("cvd_20d_change", 0)
    vimb = o.get("vol_imbalance", 0)
    vp = o.get("vpin_mean", 0)
    side = "매수세 우위" if chg > 0 else "매도세 우위"
    return (f"최근 20일 누적거래량델타(CVD)는 {side}이며, "
            f"거래량 불균형은 {vimb:+.2f}입니다. VPIN 평균은 {vp:.2f}로 "
            f"{'정보거래(스마트머니) 비중이 높은' if vp>0.4 else '정상적인'} "
            f"거래 환경으로 해석됩니다.")


def narrate_ml(m: Dict[str, Any]) -> str:
    if not m or m.get("prob_up") is None:
        return f"머신러닝 예측: {m.get('note','데이터 부족') if m else '데이터 부족'}."
    p = m.get("prob_up", 0) * 100
    acc = m.get("accuracy")
    h = m.get("horizon_d", 0)
    acc_txt = f"(검증 정확도 {acc*100:.0f}%)" if acc is not None else ""
    lean = ("상승 우위" if p > 55 else "하락 우위" if p < 45 else "중립")
    return (f"{m.get('model','RF').upper()} 모델이 추정한 {h}영업일 후 "
            f"상승 확률은 {p:.0f}% {acc_txt}로 '{lean}' 신호입니다.")


def narrate_regime(rg: Dict[str, Any]) -> str:
    if not rg or rg.get("current") is None:
        return f"국면 분석: {rg.get('note','데이터 부족') if rg else '데이터 부족'}."
    cur = rg.get("current")
    ns = rg.get("n_states", 3)
    return (f"{rg.get('method','kmeans').upper()} 군집으로 식별한 "
            f"{ns}개 시장 국면 중 현재는 국면 {cur}에 속합니다. "
            f"국면별 평균 수익률 차이를 활용하면 진입·청산 타이밍 "
            f"판단에 참고할 수 있습니다.")


# ------------------------------------------------------------------ #
#  테일 리스크 (앙상블 MC + EVT + CSCV/PBO)
# ------------------------------------------------------------------ #
def narrate_tail_risk(t: Dict[str, Any]) -> str:
    if not t or "error" in t:
        return t.get("error", "테일 리스크 분석 데이터 없음.")
    # tail_risk.py 가 이미 한글 내러티브를 포함하므로 그대로 반환
    narrative = t.get("tail_narrative", "")
    if narrative:
        return narrative
    var5  = t.get("ensemble_var",  0)
    cvar5 = t.get("ensemble_cvar", 0)
    return (
        f"앙상블 VaR(5%) {var5:.1%}, CVaR(5%) {cvar5:.1%}. "
        "4종 MC(블록부트스트랩·GARCH·점프확산·국면전환) 앙상블 기반 테일 리스크 추정."
    )


# ------------------------------------------------------------------ #
#  종합 (스코어카드 → 한 줄 의견)
# ------------------------------------------------------------------ #
def narrate_overall(results: Dict[str, Any],
                    scorecard: Dict[str, Any]) -> str:
    sig = results.get("overall_signal", "HOLD")
    sc = results.get("overall_score", 50)
    grade = scorecard.get("overall_grade", "C")
    verdict = scorecard.get("verdict", "")
    tfs = results.get("timeframes", {})
    sigs = []
    for n in ("단기", "중기", "장기"):
        s = tfs.get(n, {}).get("signal")
        if s:
            sigs.append(f"{n} {s}")
    consist = " / ".join(sigs)
    align = ("세 기간 시그널이 일치해 신뢰도가 높습니다"
             if len(set(s.split()[1] for s in sigs)) == 1 and sigs
             else "기간별 시그널이 엇갈려 신중한 접근이 필요합니다")
    return (
        f"종합 점수 {sc:.1f}/100, 기관 등급 {grade}, "
        f"투자 시그널 {sig}입니다. ({consist}) {align}. "
        f"{verdict} 본 분석은 정보 제공 목적이며 투자 권유가 아닙니다."
    )
