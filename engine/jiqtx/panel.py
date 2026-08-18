# ==============================================================================
# [16/25] panel.py — 전문가 14명 · 소견문 · 반대신문 · 증거 위계
# ==============================================================================

"""
jiqtx.panel — 전문가 패널 · 반대신문 · 원탁 회의록.

설계 의도
---------
기존 `agents.py`는 에이전트별 확률과 근거 목록을 냈다. 그런데 실제 자문은
"확률 0.53"이 아니라 **직함을 가진 사람이 자기 기준으로 판단하고, 다른
전문가의 주장을 구체적으로 반박하는 것**이다.

여기서는 13명의 전문가를 정의한다. 각자
  · 직함과 소속 관점(bias)을 **먼저 선언**한다 — 독자가 할인해서 읽을 수 있게
  · 자기 체크리스트로 심사한다
  · 숫자에서 도출된 **소견문**을 작성한다
  · 다른 전문가의 특정 주장에 **반대신문**을 건다
  · "무엇이 사실이면 내 의견을 바꾸겠다"를 명시한다

중요: 소견문은 LLM 생성이 아니라 **각 전문가의 결정 규칙에서 도출된 문장**이다.
따라서 같은 입력이면 같은 소견이 나오고, 감사 가능하다.

증거 위계 (Evidence Hierarchy) — 반대신문의 승패는 이 순서로 결정된다
  1. 데이터 무결성        (틀린 데이터 위에서는 아무 주장도 성립하지 않는다)
  2. 체결 가능성          (거래할 수 없으면 옳아도 소용없다)
  3. 표본외 통계 검정      (DSR / PBO / Murphy / 커버리지)
  4. 표본내 통계          (R², t값, 샤프)
  5. 경제적 메커니즘       (왜 그래야 하는지의 논리)
  6. 서사·정성            (이야기)
"""

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd


EVIDENCE_TIERS = {
    "data_integrity": (1, "데이터 무결성"),
    "tradability": (2, "체결 가능성"),
    "oos_statistics": (3, "표본외 통계 검정"),
    "in_sample_statistics": (4, "표본내 통계"),
    "economic_mechanism": (5, "경제적 메커니즘"),
    "narrative": (6, "서사·정성"),
}


# ================================================================ 자료구조

@dataclass
class ChecklistItem:
    item: str
    value: str
    verdict: str            # PASS / FAIL / N/A
    weight: str             # 위계 키


@dataclass
class Challenge:
    challenger: str
    target: str
    challenged_claim: str
    objection: str
    tier: str               # 반대 근거의 증거 위계
    resolution: str = ""    # 결정론적 판정 결과


@dataclass
class Expert:
    key: str
    title: str              # 직함
    desk: str               # 소속 데스크
    lens: str               # 이 사람이 세상을 보는 렌즈
    declared_bias: str      # 선언된 편향 — 독자가 할인해 읽으라고
    data_scope: str         # 정보 비대칭: 이 사람이 본 데이터
    veto_power: bool
    stance: str = "ABSTAIN"      # BULL / BEAR / NEUTRAL / ABSTAIN / BLOCK
    conviction: float = 0.0      # 0~1, 근거 강도 (자기 확신 아님)
    prob_up: Optional[float] = None
    opinion: str = ""            # 소견문
    checklist: List[ChecklistItem] = field(default_factory=list)
    would_change_mind: str = ""
    challenges: List[Challenge] = field(default_factory=list)
    veto_reason: str = ""


def _pct(x, f="{:.1%}", na="산출 불가"):
    try:
        if x is None or (isinstance(x, float) and not np.isfinite(x)):
            return na
        return f.format(x)
    except Exception:
        return na


def _ck(item, value, ok, tier) -> ChecklistItem:
    return ChecklistItem(item, value,
                         "PASS" if ok is True else "FAIL" if ok is False else "N/A",
                         tier)


# ================================================================ 전문가 정의

def _e_data(a) -> Expert:
    i = a.integrity
    e = Expert("data", "데이터 무결성 책임자", "Data Governance",
               "모든 결론은 입력 데이터의 품질을 넘을 수 없다.",
               "품질에 대해 보수적. 의심스러우면 중단시킨다 — "
               "거짓 음성보다 거짓 양성이 훨씬 비싸다고 본다.",
               "원시 OHLCV · 배당/분할 이벤트 · 거래소 캘린더만", True)
    e.checklist = [
        _ck("행 수 / 기간", f"{i.n_rows}행 ({i.start} ~ {i.end})",
            i.n_rows > 250, "data_integrity"),
        _ck("영업일 대비 결측", _pct(i.missing_ratio), i.missing_ratio <= 0.02,
            "data_integrity"),
        _ck("OHLC 논리 위반", f"{i.ohlc_violations}건", i.ohlc_violations == 0,
            "data_integrity"),
        _ck("극단 이동(|일간|>40%)", f"{i.extreme_moves}건", i.extreme_moves <= 2,
            "data_integrity"),
        _ck("Adj Close 정합", str(i.adj_close_consistent),
            i.adj_close_consistent if i.adj_close_consistent is not None else None,
            "data_integrity"),
        _ck("최신성", f"마지막 데이터 {i.stale_days}영업일 전", i.stale_days <= 5,
            "data_integrity"),
    ]
    fails = [c for c in e.checklist if c.verdict == "FAIL"]
    if not i.passed:
        e.stance, e.veto_reason = "BLOCK", "; ".join(i.issues[:3])
        e.opinion = (f"이 데이터로는 아래 어떤 분석도 신뢰할 수 없습니다. "
                     f"{'; '.join(i.issues[:2])}. "
                     f"조정종가 정합성이 깨졌다면 수익률 자체가 틀린 것이므로 "
                     f"변동성·샤프·베타가 모두 오염됩니다. 재수집 전까지 중단을 "
                     f"권고합니다.")
    else:
        e.stance, e.conviction = "NEUTRAL", 0.9
        adj_txt = (f"조정종가 누적수익이 원종가를 "
                   f"{i.implied_total_return_gap:+.1%} 상회해 배당 반영이 "
                   f"정상으로 보입니다."
                   if np.isfinite(i.implied_total_return_gap)
                   else "Adj Close 열이 없어 배당 반영 여부는 별도 검증이 "
                        "필요합니다.")
        e.opinion = (f"{i.n_rows}행({i.start}~{i.end}), 결측 {_pct(i.missing_ratio)}, "
                     f"OHLC 논리 위반 없음. {adj_txt} 다만 "
                     f"**상장폐지 종목이 없는 데이터 소스**라는 점은 구조적 "
                     f"한계이며, 종목선택 전략 검증에는 쓸 수 없습니다.")
    e.would_change_mind = ("결측이 2%를 넘거나 조정종가 누적수익이 원종가보다 "
                           "낮아지면 즉시 차단으로 전환합니다.")
    return e


def _e_taxonomy(a) -> Expert:
    c, fp, sp = a.classification, a.classification.fingerprint, a.classification.spec
    e = Expert("taxonomy", "크로스에셋 분류 전략가", "Cross-Asset Strategy",
               "먼저 이것이 무엇인지 정하지 않으면 어떤 지표도 의미가 없다.",
               "메타데이터를 불신한다. 라벨보다 수익률 지문을 믿는다.",
               "메타데이터 + 수익률 통계지문 (가격 수준 미열람)", False)
    e.prob_up = None
    e.conviction = float(c.confidence)
    e.stance = "NEUTRAL"
    e.checklist = [
        _ck("자산군 배정", f"{sp.label_ko} (신뢰도 {c.confidence:.0%})",
            c.confidence >= 0.5, "economic_mechanism"),
        _ck("연율화 기준", f"√{sp.ann_factor}",
            True, "economic_mechanism"),
        _ck("레버리지 탐지",
            f"{fp.leverage_detected:+.0f}x" if fp.leverage_detected else "없음",
            None, "in_sample_statistics"),
        _ck("평활화 의심", f"ρ₁ = {fp.autocorr1:+.3f}",
            not fp.smoothing_suspected, "in_sample_statistics"),
        _ck("이력 충족", f"{fp.n_obs}일 / 요구 {sp.min_history_days}일",
            fp.n_obs >= sp.min_history_days, "data_integrity"),
    ]
    parts = [f"수익률 지문 기준 이 자산은 **{sp.label_ko}**입니다."]
    if fp.trades_weekends:
        parts.append("주말 거래가 관측되므로 24/7 시장이며, 연율화를 √365로 "
                     "해야 합니다. √252를 쓰면 변동성이 약 17% 과소평가됩니다.")
    if fp.leverage_detected:
        parts.append(f"{fp.best_proxy} 대비 β={fp.best_beta:+.2f}, "
                     f"R²={fp.best_r2:.2f}로 **{fp.leverage_detected:+.0f}배 "
                     f"레버리지**가 탐지됩니다. 일간 리밸런싱 경로의존 자산이므로 "
                     f"기초자산 시뮬레이션 후 레버리지를 재구성해야 하며, "
                     f"ETF 수익률에 직접 GBM을 돌리면 변동성 드래그가 통째로 "
                     f"사라집니다.")
    if fp.smoothing_suspected:
        parts.append(f"1차 자기상관 {fp.autocorr1:+.2f}로 수익률 평활화가 "
                     f"의심됩니다. 언스무딩 없이 산출한 샤프는 과대평가입니다.")
    if sp.notes:
        parts.append(sp.notes)
    if c.confidence < 0.5:
        parts.append("분류 신뢰도가 낮으므로 자산군 특화 해석을 강하게 "
                     "적용하지 말고 최소 사이즈만 허용해야 합니다.")
    e.opinion = " ".join(parts)
    e.would_change_mind = (f"팩터 R²가 자산군 기대밴드 "
                           f"[{sp.r2_band[0]:.0%}, {sp.r2_band[1]:.0%}] 밖으로 "
                           f"나가면 분류를 재검토합니다.")
    return e


def _e_factor(a) -> Expert:
    fm = a.factor_model
    e = Expert("factor", "팩터 이코노미스트", "Quantitative Economics",
               "설명되지 않는 수익은 알파가 아니라 아직 찾지 못한 팩터다.",
               "구조적으로 회의적. 메커니즘을 서술할 수 없는 팩터는 "
               "후보에도 넣지 않는다.",
               "팩터 수익률 패널만 — **자산 가격을 보지 않는다** "
               "(사후합리화 차단)", False)
    if fm is None:
        e.stance, e.opinion = "ABSTAIN", "팩터 데이터가 없어 의견을 낼 수 없습니다."
        return e
    lo, hi = fm.r2_band
    e.checklist = [
        _ck("팩터 R²", _pct(fm.r2), not fm.mismatch, "in_sample_statistics"),
        _ck("기대밴드", f"{lo:.0%} ~ {hi:.0%}", None, "economic_mechanism"),
        _ck("알파 유의성", f"{_pct(fm.alpha_ann,'{:+.1%}')} (t={fm.alpha_t:+.2f})",
            abs(fm.alpha_t) >= 3.0 if np.isfinite(fm.alpha_t) else None,
            "in_sample_statistics"),
        _ck("선택 팩터 수", f"{len(fm.used_factors)}개", None,
            "in_sample_statistics"),
    ]
    parts = []
    if fm.mismatch:
        e.stance, e.conviction = "ABSTAIN", 0.2
        parts.append(f"R²가 {fm.r2:.1%}로 이 자산군 기대밴드 하단을 크게 "
                     f"밑돕니다. 잔차 {1-fm.r2:.0%}를 '고유위험'이라 부르면 안 "
                     f"됩니다 — 이것은 **누락 변수**입니다.")
        parts.append(f"R²가 {fm.r2:.0%}인 회귀에서 나온 알파 "
                     f"{_pct(fm.alpha_ann,'{:+.1%}')}는 해석 불가능한 잔차 "
                     f"평균이므로, 저는 이 알파를 근거로 한 어떤 주장에도 "
                     f"동의하지 않습니다.")
        parts.append("이 상태에서는 최소분산 헤지비율도 함께 무효입니다. "
                     "헤지비율은 이 회귀 계수와 동일하기 때문입니다.")
    else:
        e.stance, e.conviction = "NEUTRAL", 0.6
        top = sorted(fm.coefs.items(), key=lambda kv: -abs(kv[1]))[:2]
        parts.append(f"R² {fm.r2:.1%}로 기대밴드 내이며, 주 노출은 "
                     + ", ".join(f"{k} (β={v:+.3f}, t="
                                 f"{fm.tstats.get(k, float('nan')):+.1f})"
                                 for k, v in top) + "입니다.")
        if np.isfinite(fm.alpha_t) and abs(fm.alpha_t) < 3.0:
            parts.append(f"알파 {_pct(fm.alpha_ann,'{:+.1%}')}의 t값이 "
                         f"{fm.alpha_t:+.2f}로 Harvey-Liu-Zhu 허들(3.0)에 "
                         f"미달합니다. 다중검정을 고려하면 0과 구별되지 않으므로 "
                         f"기대수익 산정에 넣지 말아야 합니다.")
            e.conviction = 0.45
        elif np.isfinite(fm.alpha_t):
            parts.append(f"알파 t={fm.alpha_t:+.2f}로 허들을 넘습니다. 다만 "
                         f"단일 종목 표본에서의 t값이므로, 같은 절차를 여러 "
                         f"종목에 반복했다면 선택편향 보정이 필요합니다.")
    e.opinion = " ".join(parts)
    e.would_change_mind = (f"R²가 밴드 하단({lo:.0%})의 55% 아래로 내려가거나 "
                           f"롤링 R²가 과거 중앙값의 35% 미만이 되면 팩터 해석 "
                           f"전체를 철회합니다.")
    return e


def _e_macro(a) -> Expert:
    dp = a.delta_panel
    e = Expert("macro", "매크로 전략가", "Global Macro",
               "가격은 결국 실질금리·달러·유동성의 함수다.",
               "매크로가 모든 것을 설명한다고 보는 경향이 있음. "
               "종목 고유 요인을 과소평가할 수 있음.",
               "매크로 팩터 시계열 + 발표 캘린더 (발표 예정치 선반영 금지)", False)
    if dp is None or not len(dp):
        e.stance, e.opinion = "ABSTAIN", "매크로 델타를 산출하지 못했습니다."
        return e
    top = dp.iloc[0]
    e.checklist = [
        _ck("최대 매크로 노출", f"{top['shock_label']} → "
            f"{_pct(top['delta_pct'],'{:+.1%}')}", None, "in_sample_statistics"),
        _ck("하방베타 확대 여부",
            f"{_pct(top['delta_pct_downside'],'{:+.1%}')}",
            abs(top["delta_pct_downside"]) <= abs(top["delta_pct"]) * 1.5,
            "in_sample_statistics"),
        _ck("β 안정성", f"CV = {top['beta_stability_cv']:.2f}",
            top["beta_stability_cv"] <= 0.8
            if np.isfinite(top["beta_stability_cv"]) else None,
            "in_sample_statistics"),
        _ck("R² 붕괴 경보", "발동" if bool(top["r2_collapse"]) else "정상",
            not bool(top["r2_collapse"]), "oos_statistics"),
    ]
    sign = float(np.sign(top["delta_pct"]))
    e.prob_up = float(0.5 - sign * 0.06)
    e.stance = "BEAR" if e.prob_up < 0.47 else "BULL" if e.prob_up > 0.53 else "NEUTRAL"
    e.conviction = 0.45
    parts = [f"손익의 최대 단일 기여 요인은 **{top['shock_label']}**이며, "
             f"표준 충격 시 {top['delta_pct']:+.1%}입니다."]
    if abs(top["delta_pct_downside"]) > abs(top["delta_pct"]) * 1.2:
        parts.append(f"하방 구간 베타를 쓰면 {top['delta_pct_downside']:+.1%}로 "
                     f"확대됩니다. 즉 이 자산은 매크로 악화 국면에서 **비대칭적으로 "
                     f"더 맞습니다**. 정적 베타 스트레스는 이를 놓칩니다.")
    if np.isfinite(top["corr_all"]) and np.isfinite(top["corr_lower_tail"]):
        parts.append(f"정상 국면 상관 {top['corr_all']:+.2f}가 하방 꼬리에서 "
                     f"{top['corr_lower_tail']:+.2f}로 이동합니다 — "
                     f"분산 효과가 위기에 어떻게 변하는지의 실측치입니다.")
    if bool(top["r2_collapse"]):
        parts.append("다만 이 팩터의 롤링 R²가 최근 급락했습니다. **과거 베타로 "
                     "계산한 위 수치는 이미 틀렸을 수 있습니다.** 재추정 전에는 "
                     "매크로 시나리오를 근거로 사이즈를 키우면 안 됩니다.")
        e.conviction = 0.2
    parts.append("발표 예정 지표는 선반영하지 않았습니다. 캘린더 이벤트 전에는 "
                 "포지션을 별도로 관리해야 합니다.")
    e.opinion = " ".join(parts)
    e.would_change_mind = (f"{top['factor']}의 롤링 R²가 붕괴하거나 β 변동계수가 "
                           f"0.8을 넘으면 매크로 기반 논지를 철회합니다.")
    return e


def _e_micro(a) -> Expert:
    l = a.liquidity
    e = Expert("micro", "체결 총괄", "Execution & Market Structure",
               "거래할 수 없으면 옳아도 소용없다. 알파는 스프레드에서 죽는다.",
               "비용에 과민하다. 좋은 아이디어를 비용 때문에 죽일 위험이 있음.",
               "OHLCV 미시구조 지표만 (EDGE / Amihud / ADV)", True)
    e.checklist = [
        _ck("EDGE 유효스프레드", _pct(l.spread_bps / 1e4) if np.isfinite(l.spread_bps)
            else "산출 불가", l.spread_bps <= a.classification.spec.max_spread_bps
            if np.isfinite(l.spread_bps) else False, "tradability"),
        _ck("추정량 간 산포",
            f"{l.spread_dispersion*1e4:.0f}bp" if np.isfinite(l.spread_dispersion)
            else "—", None, "tradability"),
        _ck("무거래일 비율", _pct(l.zero_ret_ratio), l.zero_ret_ratio <= 0.35
            if np.isfinite(l.zero_ret_ratio) else None, "tradability"),
        _ck("ADV", f"${l.adv_usd:,.0f}" if np.isfinite(l.adv_usd) else "—",
            None, "tradability"),
        _ck("1% AUM 청산", f"{l.days_to_liquidate_1pct_aum:.1f}일"
            if np.isfinite(l.days_to_liquidate_1pct_aum) else "—",
            l.days_to_liquidate_1pct_aum <= 3
            if np.isfinite(l.days_to_liquidate_1pct_aum) else None, "tradability"),
    ]
    if not l.tradable:
        e.stance, e.veto_reason = "BLOCK", l.reason
        e.opinion = (f"체결 관점에서 이 종목은 거래 대상이 아닙니다. {l.reason}. "
                     f"백테스트 상 어떤 엣지가 나오든 이 비용을 통과하지 못합니다. "
                     f"참고로 일봉 VPIN/CVD 프록시는 체결방향을 모르므로 "
                     f"정보 함량이 사실상 0이며, 저는 EDGE 스프레드만 봅니다.")
    else:
        e.stance, e.conviction = "NEUTRAL", 0.85
        e.opinion = (f"EDGE 유효스프레드 {l.spread_bps:.1f}bp, "
                     f"ADV ${l.adv_usd:,.0f}로 체결은 가능합니다. "
                     f"추정량 간 산포가 {l.spread_dispersion*1e4:.0f}bp이므로 "
                     f"측정 자체의 불확실성도 함께 감안해야 합니다. "
                     f"AUM 1%를 ADV의 10%씩 처분하면 "
                     f"{l.days_to_liquidate_1pct_aum:.1f}일이 걸립니다 — "
                     f"급매 국면에서는 이 값이 몇 배로 늘어납니다. "
                     f"체결은 신호 생성 종가가 아니라 **다음 거래일 시가/VWAP**를 "
                     f"가정해야 하며, 이 선택 하나가 수익성을 뒤집을 수 있습니다.")
    e.would_change_mind = (f"스프레드가 {a.classification.spec.max_spread_bps:.0f}bp를 "
                           f"넘거나 ADV가 절반으로 줄면 즉시 차단합니다.")
    return e


def _e_deriv(a) -> Expert:
    o, mv = a.option_surface, a.model_vs_market
    e = Expert("deriv", "파생 스트럭처러", "Derivatives",
               "옵션은 시장의 확률분포를 직접 보여준다. 우리 모델과 다르면 "
               "둘 중 하나는 틀렸다.",
               "시장이 대체로 옳다고 본다. 모델이 시장과 다르면 모델을 먼저 의심함.",
               "옵션 체인 스냅샷만", False)
    if o is None:
        e.stance = "ABSTAIN"
        e.opinion = ("옵션 체인이 없어 시장 함축 분포를 확인할 수 없습니다. "
                     "이 경우 우리 모델 분포를 검증할 외부 기준이 없다는 뜻이므로, "
                     "분포 기반 확률을 더 보수적으로 읽어야 합니다.")
        return e
    e.checklist = [
        _ck("1M ATM IV", _pct(o.atm_iv_1m), None, "in_sample_statistics"),
        _ck("기간구조 (3M−1M)", _pct(o.term_slope, "{:+.1%}"),
            not o.backwardation, "in_sample_statistics"),
        _ck("25Δ Risk Reversal", _pct(o.rr25_1m, "{:+.1%}"), None,
            "in_sample_statistics"),
        _ck("IV − RV (VRP)", _pct(o.iv_rv_spread, "{:+.1%}"), None,
            "in_sample_statistics"),
    ]
    p = 0.5 - float(np.clip(o.rr25_1m or 0.0, -0.15, 0.15)) * 1.2
    e.prob_up, e.conviction = p, 0.5
    e.stance = "BEAR" if p < 0.47 else "BULL" if p > 0.53 else "NEUTRAL"
    parts = [f"1개월 ATM IV {o.atm_iv_1m:.1%}, 25Δ 리스크리버설 "
             f"{o.rr25_1m:+.1%}입니다."]
    if o.backwardation:
        parts.append(f"기간구조가 {o.term_slope:+.1%}로 백워데이션입니다 — "
                     f"시장이 단기 스트레스를 가격에 반영하고 있습니다.")
    if np.isfinite(o.iv_rv_spread):
        parts.append(f"IV−RV 스프레드 {o.iv_rv_spread:+.1%}는 분산위험프리미엄 "
                     f"프록시이며, "
                     + ("양수이므로 시장이 실현변동성보다 비싸게 보험을 팔고 "
                        "있습니다 — 옵션 매도가 구조적으로 유리한 환경."
                        if o.iv_rv_spread > 0 else
                        "음수이므로 옵션이 실현변동성 대비 싸며, 보호 매수가 "
                        "상대적으로 유리합니다."))
    if mv:
        parts.append(f"우리 모델 상승확률 {mv['model_prob_up']:.1%} vs 시장 "
                     f"위험중립 {mv['market_prob_up_rn']:.1%}. {mv['verdict']}. "
                     f"위험중립 확률에는 리스크 프리미엄이 포함돼 있으므로 실세계 "
                     f"확률과 같지 않지만, **차이의 방향과 크기가 곧 논지**입니다.")
    parts.append("옵션 스냅샷은 히스토리가 없어 백테스트가 불가합니다. "
                 "오늘부터 매일 축적해야 1년 뒤 검증이 가능합니다.")
    e.opinion = " ".join(parts)
    e.would_change_mind = ("모델 분포와 시장 RND가 5%p 이내로 수렴하면 "
                           "방향성 엣지가 없다고 판단합니다.")
    return e


def _e_quant(a) -> Expert:
    m = a.ml
    e = Expert("quant", "계량 리서처", "Quantitative Research",
               "예측력은 표본외에서만 존재를 인정한다.",
               "모델을 만든 당사자이므로 자기 모델에 우호적일 수 있음. "
               "그래서 판정 권한은 감사관에게 있다.",
               "가격 파생 피처 + 트리플배리어 라벨", False)
    if m is None:
        e.stance, e.opinion = "ABSTAIN", "방향 예측 모듈을 실행하지 않았습니다."
        return e
    e.checklist = [
        _ck("OOS 정확도", _pct(m.oos_accuracy),
            m.oos_accuracy > m.base_rate if np.isfinite(m.oos_accuracy) else None,
            "oos_statistics"),
        _ck("과적합 갭", _pct(m.overfit_gap, "{:+.1%}"),
            m.overfit_gap <= 0.15 if np.isfinite(m.overfit_gap) else None,
            "oos_statistics"),
        _ck("Murphy Resolution", f"{m.resolution:.5f}",
            m.resolution > 1e-4 if np.isfinite(m.resolution) else None,
            "oos_statistics"),
        _ck("Brier skill", f"{m.brier_skill:+.4f}",
            m.brier_skill > 0 if np.isfinite(m.brier_skill) else None,
            "oos_statistics"),
        _ck("전략 DSR", _pct(m.strategy_dsr),
            m.strategy_dsr >= 0.90 if np.isfinite(m.strategy_dsr) else None,
            "oos_statistics"),
        _ck("PBO", _pct(m.pbo), m.pbo < 0.75 if np.isfinite(m.pbo) else None,
            "oos_statistics"),
    ]
    if m.verdict == "SIGNAL":
        e.prob_up, e.conviction, e.stance = m.prob_up_now, 0.75, (
            "BULL" if m.prob_up_now > 0.53 else
            "BEAR" if m.prob_up_now < 0.47 else "NEUTRAL")
        e.opinion = (f"게이트를 통과했습니다. 승자 모델은 {m.model_name}이며 "
                     f"OOS 정확도 {m.oos_accuracy:.1%}(기저율 {m.base_rate:.1%}), "
                     f"과적합 갭 {m.overfit_gap:+.1%}, Murphy resolution "
                     f"{m.resolution:.4f}로 기저율 이상의 정보가 확인됩니다. "
                     f"보정 후 상승확률은 {m.prob_up_now:.1%}이고 신뢰구간은 "
                     f"[{m.prob_ci[0]:.1%}, {m.prob_ci[1]:.1%}]로 50%를 "
                     f"배제합니다. 전략 DSR {m.strategy_dsr:.0%}로 "
                     f"다중검정 보정 후에도 유의합니다.")
    else:
        e.stance, e.conviction = "ABSTAIN", 0.0
        e.opinion = (f"제 모듈은 **출력을 내지 않습니다**. "
                     f"{'; '.join(m.reasons[:2])}. "
                     f"OOS 정확도 {m.oos_accuracy:.1%}는 약한 신호가 아니라 "
                     f"신호 없음입니다. 이 상황에서 감점된 확률을 내놓는 것은 "
                     f"없는 정보를 있는 것처럼 만드는 일이므로, 저는 "
                     f"기권합니다.")
    e.would_change_mind = ("Murphy resolution이 0을 유의하게 상회하고 과적합 갭이 "
                           "15%p 아래로 내려오면 확률을 제출합니다.")
    return e


def _e_regime(a) -> Expert:
    rg = a.regime
    e = Expert("regime", "레짐 사관", "Regime Analytics",
               "같은 자산도 국면이 바뀌면 다른 자산이다.",
               "국면 전환을 과하게 읽는 경향. 표본이 적으면 과신 위험.",
               "수익률 파생 국면 피처만 — **날짜·종목명 블라인드**", False)
    if rg is None:
        e.stance, e.opinion = "ABSTAIN", "국면 식별에 실패했습니다."
        return e
    cur = rg.stats[rg.stats["state"] == rg.current_state]
    mean_ann = float(cur["mean_ann"].iloc[0]) if len(cur) else np.nan
    vol_ann = float(cur["vol_ann"].iloc[0]) if len(cur) else np.nan
    e.checklist = [
        _ck("현재 국면", rg.labels[rg.current_state], None, "in_sample_statistics"),
        _ck("국면 확률", _pct(max(rg.current_probs.values())), None,
            "in_sample_statistics"),
        _ck("기대 지속기간",
            f"{rg.expected_duration[rg.current_state]:.0f}영업일", None,
            "in_sample_statistics"),
        _ck("관측 전환 횟수", f"{rg.n_switches}회", rg.n_switches >= 3,
            "in_sample_statistics"),
    ]
    p = 0.5 + float(np.clip(mean_ann, -0.4, 0.4)) * 0.35 if np.isfinite(mean_ann) else None
    e.prob_up, e.conviction = p, 0.5 if rg.n_switches >= 3 else 0.25
    e.stance = ("BULL" if p and p > 0.53 else "BEAR" if p and p < 0.47
                else "NEUTRAL")
    parts = [f"현재 국면은 **{rg.labels[rg.current_state]}**이며 확률 "
             f"{max(rg.current_probs.values()):.0%}, 기대 지속기간 "
             f"{rg.expected_duration[rg.current_state]:.0f}영업일입니다. "
             f"이 국면 안에서 연환산 수익 {mean_ann:+.1%}, 변동성 {vol_ann:.1%}가 "
             f"관측됐습니다."]
    parts.append(f"점프 페널티 λ={rg.jump_penalty:.0f}로 지속성을 강제했습니다 — "
                 f"K-means처럼 라벨이 하루 단위로 튀지 않습니다.")
    if rg.n_switches < 3:
        parts.append(f"다만 관측 구간에서 국면 전환이 {rg.n_switches}회뿐입니다. "
                     f"**다른 국면에서의 행태는 사실상 검증되지 않았으므로** "
                     f"제 의견의 확신도를 낮춰 읽으십시오.")
    e.opinion = " ".join(parts)
    e.would_change_mind = (f"현재 국면 확률이 60% 아래로 내려가면 전환 진행으로 "
                           f"보고 국면 조건부 베타를 재추정합니다.")
    return e


def _e_sim(a) -> Expert:
    s = a.sim
    e = Expert("sim", "확률모형 총괄", "Stochastic Modeling",
               "분포를 모르면 확률을 말할 수 없다. 그리고 우리는 드리프트를 모른다.",
               "추정 불확실성을 크게 본다. 기대수익 기반 의사결정에 부정적.",
               "수익률 시계열 + GARCH 표준화 잔차", False)
    if s is None:
        e.stance, e.opinion = "ABSTAIN", "시뮬레이션을 실행하지 못했습니다."
        return e
    d = s.drift
    e.checklist = [
        _ck("드리프트 추정치", _pct(d.mu_hat_ann, "{:+.1%}"), None,
            "in_sample_statistics"),
        _ck("드리프트 표준오차", _pct(d.se_ann),
            d.se_ann < abs(d.mu_hat_ann) if np.isfinite(d.se_ann) else None,
            "in_sample_statistics"),
        _ck("GPD 꼬리 적합", "성공" if s.tail.ok else "실패", s.tail.ok,
            "in_sample_statistics"),
        _ck("파라미터 무지 기여",
            _pct(s.uncertainty_decomposition.get("param_share")), None,
            "in_sample_statistics"),
    ]
    e.prob_up, e.conviction = s.prob_up, 0.5
    e.stance = ("BULL" if s.prob_up > 0.53 else "BEAR" if s.prob_up < 0.47
                else "NEUTRAL")
    parts = [f"드리프트 추정치는 {d.mu_hat_ann:+.1%}이지만 표준오차가 σ/√T = "
             f"{d.se_ann:.1%}이므로 95% 구간이 "
             f"[{d.ci95[0]:+.1%}, {d.ci95[1]:+.1%}]입니다."]
    if d.se_ann >= abs(d.mu_hat_ann):
        parts.append("**표준오차가 추정치 자체보다 큽니다.** 즉 이 드리프트는 "
                     "'모른다'와 통계적으로 구별되지 않으며, 기대수익 기반 "
                     "사이징은 정당화되지 않습니다.")
        e.conviction = 0.25
    parts.append(f"드리프트를 상수로 고정한 정규 GBM(원본 리포트 방식)에서는 "
                 f"상승확률이 {s.prob_up_naive_gbm:.1%}로 나옵니다. "
                 f"파라미터 불확실성·변동성 클러스터링·팻테일을 반영하면 "
                 f"**{s.prob_up:.1%}**입니다. 이 차이는 시장에 대한 것이 아니라 "
                 f"우리가 무엇을 안다고 가정했는지에 대한 것입니다.")
    ps = s.uncertainty_decomposition.get("param_share")
    if np.isfinite(ps):
        parts.append(f"예측 불확실성의 {ps:.0%}가 시장 변동성이 아니라 "
                     f"**우리의 파라미터 무지**에서 옵니다.")
    e.opinion = " ".join(parts)
    e.would_change_mind = ("표본이 늘어 SE(μ̂)가 |μ̂|의 절반 아래로 내려오면 "
                           "기대수익 기반 논지를 받아들이겠습니다.")
    return e


def _e_risk(a) -> Expert:
    v, dd, st = a.var, a.drawdown, a.stress_summary
    e = Expert("risk", "리스크 책임자", "Risk Management",
               "질문은 '얼마나 벌 수 있나'가 아니라 '틀렸을 때 얼마를 잃나'다.",
               "구조적으로 부정적. 좋은 기회를 놓치게 만들 수 있음.",
               "수익률 꼬리 + 스트레스 시나리오 + 낙폭", True)
    e.checklist = [
        _ck("VaR 채택 모델", v.preferred if v else "—",
            v.preferred != "historical" if v else None, "oos_statistics"),
        _ck("VaR95 (FHS-EVT)", _pct(v.var_fhs_evt, "{:.2%}") if v else "—",
            None, "oos_statistics"),
        _ck("ES95", _pct(v.es_fhs_evt, "{:.2%}") if v else "—", None,
            "oos_statistics"),
        _ck("최대낙폭", _pct(dd.max_drawdown), None, "in_sample_statistics"),
        _ck("최장 수중기간", f"{dd.longest_underwater_days}영업일",
            dd.longest_underwater_days <= 250, "in_sample_statistics"),
        _ck("스트레스 최악", _pct(st.get("worst_pnl")),
            st.get("within_limit", False), "in_sample_statistics"),
    ]
    if not st.get("within_limit", True):
        e.stance, e.veto_reason = "BLOCK", (
            f"스트레스 최악 {st.get('worst_pnl', float('nan')):.1%} > 한도 "
            f"{st.get('limit', 0.35):.0%}")
        e.opinion = (f"스트레스 한도를 초과합니다. {st.get('worst_scenario','')} "
                     f"시나리오에서 {st.get('worst_pnl', float('nan')):.1%} 손실이 "
                     f"예상되며 이는 한도 {st.get('limit',0.35):.0%}를 넘습니다. "
                     f"헤지로 노출을 줄이거나 사이즈를 0으로 두기 전까지 저는 "
                     f"진입에 동의하지 않습니다. 참고로 이 손익은 선형 델타 "
                     f"근사이므로 비선형 반응과 유동성 연쇄는 포함되지 "
                     f"않았습니다 — 실제로는 더 나쁠 수 있습니다.")
    else:
        e.stance, e.conviction = "NEUTRAL", 0.85
        ok = [k for k, d_ in (v.backtest.items() if v else [])
              if d_.get("cc_p", 0) > 0.05]
        e.opinion = (f"VaR95는 {v.var_fhs_evt:.2%}(FHS-EVT), ES95는 "
                     f"{v.es_fhs_evt:.2%}입니다. 조건부 커버리지 검정을 통과한 "
                     f"모델은 {', '.join(ok) if ok else '없습니다'}이며 채택 "
                     f"모델은 {v.preferred}입니다. "
                     f"과거 최대낙폭 {dd.max_drawdown:.1%}, 최장 수중기간 "
                     f"{dd.longest_underwater_days}영업일"
                     f"({dd.longest_underwater_days/252:.1f}년)입니다. "
                     f"강조하자면 **VaR은 최대손실이 아니라 하위 5% 경계값**이며, "
                     f"그보다 큰 손실이 5%의 확률로 발생합니다.")
        if not ok:
            e.conviction = 0.4
            e.opinion += (" 다만 어떤 VaR 모델도 커버리지 검정을 통과하지 "
                          "못했으므로, 제시된 수치를 액면대로 믿지 마십시오.")
    e.would_change_mind = (f"스트레스 손실이 한도 아래로 내려오고 VaR 모델이 "
                           f"조건부 커버리지 검정을 통과하면 사이즈 허용으로 "
                           f"전환합니다.")
    return e


def _e_pm(a) -> Expert:
    t, sz, ev = a.trade, a.sizing, (a.scenario_ev or {})
    e = Expert("pm", "포트폴리오 매니저", "Portfolio Management",
               "좋은 분석과 좋은 트레이드는 다르다. 나는 후자만 본다.",
               "실행 가능성을 중시. 정교하지만 실행 불가능한 결론을 싫어함.",
               "판정 결과 + 시나리오 + 유동성 + 사이징 제약", False)
    if t is None:
        e.stance, e.opinion = "ABSTAIN", "트레이드 계획을 구성하지 못했습니다."
        return e
    e.checklist = [
        _ck("방향", t.direction, t.direction != "NONE", "narrative"),
        _ck("R:R", f"{t.rr_ratio:.2f}" if np.isfinite(t.rr_ratio) else "—",
            t.rr_ratio >= 1.0 if np.isfinite(t.rr_ratio) else None,
            "in_sample_statistics"),
        _ck("P(목표 선도달)", _pct(t.p_target_first), None, "in_sample_statistics"),
        _ck("손익분기 승률", _pct(t.breakeven_hit_rate), None,
            "in_sample_statistics"),
        _ck("엣지", _pct(t.edge_vs_breakeven, "{:+.1%}"),
            t.edge_vs_breakeven > 0 if np.isfinite(t.edge_vs_breakeven) else None,
            "in_sample_statistics"),
        _ck("비용 후 기대손익", _pct(t.expected_pnl_net, "{:+.2%}"),
            t.expected_pnl_net > 0 if np.isfinite(t.expected_pnl_net) else None,
            "in_sample_statistics"),
        _ck("손절 시 계좌 손실", _pct(t.max_loss_pct, "{:.2%}"),
            t.max_loss_pct <= 0.02 if np.isfinite(t.max_loss_pct) else None,
            "tradability"),
    ]
    if t.direction == "NONE":
        e.stance, e.conviction = "ABSTAIN", 0.0
        e.opinion = ("판정 엔진이 사이즈 0을 냈으므로 실행할 트레이드가 "
                     "없습니다. 아래 계획은 조건이 충족됐을 때를 위한 참고입니다. "
                     "다만 분명히 해두겠습니다 — 이것은 약세 판단이 아니라 "
                     "'현 조건에서 포지션을 잡을 수 없다'는 뜻입니다.")
    else:
        e.conviction = 0.6 if "충족" in t.verdict else 0.35
        e.stance = "BULL" if t.direction == "LONG" else "BEAR"
        e.prob_up = t.p_target_first if t.direction == "LONG" else \
            (1 - t.p_target_first if np.isfinite(t.p_target_first) else None)
        parts = [f"{t.direction}, 진입 {t.entry:,.2f}, 손절 {t.stop:,.2f}"
                 f"({t.stop_pct:.1%}), 목표 {t.target:,.2f}"
                 f"({t.target_pct:+.1%})입니다. 손절은 임의 %가 아니라 "
                 f"{t.stop_basis}로 잡았습니다."]
        parts.append(f"시뮬 경로에서 목표 선도달 확률 {t.p_target_first:.1%}, "
                     f"손절 선도달 {t.p_stop_first:.1%}입니다. 손익분기 승률이 "
                     f"{t.breakeven_hit_rate:.1%}이므로 엣지는 "
                     f"{t.edge_vs_breakeven:+.1%}이고, 왕복비용 "
                     f"{t.roundtrip_cost:.2%}를 빼면 기대손익 "
                     f"{t.expected_pnl_net:+.2%}입니다.")
        if np.isfinite(t.rr_ratio) and t.rr_ratio < 1.0:
            parts.append(f"R:R이 {t.rr_ratio:.2f}로 1 미만입니다. 승률이 "
                         f"손익분기를 넘어야만 성립하는 구조이므로, **승률 가정이 "
                         f"조금만 틀려도 기대값이 뒤집힙니다.** 저는 이런 구조를 "
                         f"풀사이즈로 잡지 않습니다.")
        if t.notes:
            parts.append(t.notes[0])
        parts.append(f"판정: {t.verdict}.")
        e.opinion = " ".join(parts)
    e.would_change_mind = ("엣지가 음수로 돌아서거나 손절폭 × 사이즈가 계좌 2%를 "
                           "넘으면 실행하지 않습니다.")
    return e


def _e_hedge(a) -> Expert:
    h = a.hedge
    e = Expert("hedge", "헤지 설계자", "Portfolio Hedging",
               "무엇을 상쇄할 수 있고 무엇이 남는지를 먼저 정한다.",
               "헤지 가능한 위험을 과대평가하는 경향. 잔차 위험을 잊기 쉬움.",
               "팩터 회귀 계수 + β 안정성", False)
    if h is None:
        e.stance, e.opinion = "ABSTAIN", "헤지 설계 정보가 없습니다."
        return e
    e.checklist = [
        _ck("헤지 레그 수", f"{len(h.legs)}개", None, "economic_mechanism"),
        _ck("제거 가능 분산", _pct(h.var_removed),
            h.var_removed >= 0.25 if np.isfinite(h.var_removed) else None,
            "in_sample_statistics"),
        _ck("잔차 변동성", _pct(h.residual_vol_ann), None,
            "in_sample_statistics"),
        _ck("불안정 레그",
            f"{sum(1 for l in h.legs if not l.reliable)}개",
            all(l.reliable for l in h.legs) if h.legs else None,
            "in_sample_statistics"),
        _ck("연 헤지 비용", _pct(h.hedge_cost_ann, "{:.2%}"), None, "tradability"),
    ]
    e.stance, e.conviction = "NEUTRAL", 0.6 if h.reliable else 0.3
    parts = [f"{h.verdict}. 최소분산 헤지비율은 다변량 팩터 회귀 계수와 "
             f"동일하므로, 팩터 모델이 무효면 헤지도 함께 무효입니다."]
    if np.isfinite(h.var_removed):
        parts.append(f"헤지로 제거 가능한 분산은 {h.var_removed:.0%}이고 "
                     f"잔차 변동성은 {h.residual_vol_ann:.1%}입니다"
                     f"(무헤지 {h.unhedged_vol_ann:.1%}).")
        if h.var_removed < 0.25:
            parts.append("**위험의 대부분이 고유 요인입니다.** 이 경우 헤지는 "
                         "비용만 쓰고 효과가 작으므로, 실질적인 통제 수단은 "
                         "헤지가 아니라 **사이즈 축소**입니다.")
    bad = [l for l in h.legs if not l.reliable]
    if bad:
        parts.append("β 변동계수가 0.8을 넘는 레그: " +
                     ", ".join(f"{l.factor}(CV {l.stability_cv:.2f})" for l in bad) +
                     ". 헤지비율이 불안정하면 헤지가 위험을 **추가**할 수 "
                     "있으므로 해당 레그는 집행하지 않는 편이 낫습니다.")
    e.opinion = " ".join(parts)
    e.would_change_mind = ("β 변동계수가 0.5 아래로 안정되면 전량 헤지를 "
                           "권고합니다.")
    return e


def _e_auditor(a) -> Expert:
    m = a.ml
    fm = a.factor_model
    e = Expert("auditor", "검증 감사관", "Validation & Audit",
               "발견은 검증되기 전까지 가설이다. 나는 반증 절차만 본다.",
               "생산성에 무관심하다. 통과시키지 않는 것이 기본값.",
               "검증 통계만 (DSR / PBO / Murphy / 커버리지 / 시행횟수)", True)
    items = []
    if m is not None:
        items += [
            _ck("PBO", _pct(m.pbo), m.pbo < 0.75 if np.isfinite(m.pbo) else None,
                "oos_statistics"),
            _ck("전략 DSR", _pct(m.strategy_dsr),
                m.strategy_dsr >= 0.90 if np.isfinite(m.strategy_dsr) else None,
                "oos_statistics"),
            _ck("시행횟수 로깅", f"{m.n_trials_used}회", m.n_trials_used > 0,
                "oos_statistics"),
            _ck("확률 보정", f"Brier {m.brier:.4f} / skill {m.brier_skill:+.4f}",
                m.brier_skill > 0 if np.isfinite(m.brier_skill) else None,
                "oos_statistics"),
        ]
    if fm is not None:
        items.append(_ck("알파 t값 vs 허들 3.0",
                         f"{fm.alpha_t:+.2f}" if np.isfinite(fm.alpha_t) else "—",
                         abs(fm.alpha_t) >= 3.0 if np.isfinite(fm.alpha_t) else None,
                         "in_sample_statistics"))
    p = a.perf
    if p.get("sharpe") and np.isfinite(p["sharpe"]) and abs(p["sharpe"]) > 1e-6:
        yrs = (3.0 / abs(p["sharpe"])) ** 2
        have = p.get("n_obs", 0) / 252
        items.append(_ck("샤프 유의성에 필요한 표본",
                         f"{yrs:.1f}년 필요 / {have:.1f}년 보유", yrs <= have,
                         "in_sample_statistics"))
    e.checklist = items
    fails = [c for c in items if c.verdict == "FAIL"]
    e.stance = "NEUTRAL"
    e.conviction = 0.95
    parts = []
    if m is not None and m.verdict != "SIGNAL":
        parts.append(f"방향 예측 모듈은 제 게이트를 통과하지 못했습니다: "
                     f"{'; '.join(m.reasons[:2])}.")
    if fails:
        parts.append("현재 미통과 항목: " +
                     ", ".join(f"{c.item}({c.value})" for c in fails) + ".")
    parts.append("한 가지를 분명히 하겠습니다 — **PBO는 전략 선택 절차의 과적합을 "
                 "재는 지표이지 신호의 존재를 재는 지표가 아닙니다.** 변형들이 "
                 "사실상 동일하면 순위가 무작위가 되어 PBO가 기계적으로 0.5 "
                 "근방에 나옵니다. 따라서 PBO는 소프트 게이트(불확실성 확대)로 "
                 "쓰고, 하드 게이트는 전략 DSR로 겁니다.")
    parts.append("그리고 DSR은 시행횟수 N에 극도로 민감합니다. N을 로깅하지 "
                 "않은 DSR 값은 의미가 없으며, 설정을 바꿀 때마다 N에 "
                 "카운트해야 합니다.")
    if not fails:
        parts.append("현 시점에서 제가 차단할 항목은 없습니다. 다만 통과는 "
                     "'검증됐다'가 아니라 '아직 반증되지 않았다'는 뜻입니다.")
    e.opinion = " ".join(parts)
    e.would_change_mind = ("사전등록 후 실시간 페이퍼 트레이딩에서 12개월간 "
                           "Brier skill이 양수로 유지되면 검증됐다고 "
                           "인정하겠습니다.")
    return e


def _e_red(a) -> Expert:
    e = Expert("red", "적대적 검토관", "Adversarial Review",
               "이 결론을 무효화할 가장 그럴듯한 세계는 무엇인가.",
               "구조적으로 반대편에 선다. 모든 신호를 죽일 위험이 있으므로 "
               "반론에도 통계적 근거를 요구받는다.",
               "타 전문가의 결론 — **최종 단계에서만 열람**", False)
    rt = next((v for v in a.agent_views if v.agent.startswith("A11")), None)
    ev = rt.evidence if rt else []
    e.stance, e.conviction = "NEUTRAL", 0.7
    e.checklist = [_ck(f"반증 근거 {i+1}", x[:60], None, "economic_mechanism")
                   for i, x in enumerate(ev[:6])]
    e.opinion = ("제 역할은 동의가 아니라 반증입니다. 사전등록된 프로토콜에 따라 "
                 "최소 3개의 구체적 반대 근거를 제출할 의무가 있고, 제출하지 "
                 "못하면 그 자체가 시스템 오류로 기록됩니다. 아래가 이번 "
                 "분석에서 제가 찾은 취약점입니다. "
                 + (" / ".join(x.split("]")[-1].strip() for x in ev[:3])
                    if ev else "이번에는 유의한 취약점을 찾지 못했습니다 — "
                    "이 경우 오히려 제 탐색 범위를 의심해야 합니다."))
    e.would_change_mind = ("제 반론이 더 높은 위계의 증거로 반박되면 철회합니다. "
                           "다만 '그럴듯하다'는 이유로는 철회하지 않습니다.")
    return e


BUILDERS = [_e_data, _e_taxonomy, _e_factor, _e_macro, _e_micro, _e_deriv,
            _e_quant, _e_regime, _e_sim, _e_risk, _e_hedge, _e_pm,
            _e_auditor, _e_red]


# ================================================================ 반대신문

def cross_examine(experts: Dict[str, Expert], a) -> List[Challenge]:
    """
    전문가 간 반대신문. 각 반론은 **구체적 주장**을 대상으로 하고,
    증거 위계로 승패가 결정된다.
    """
    C: List[Challenge] = []
    g = experts.get

    def add(ch_key, tg_key, claim, obj, tier):
        ce, te = g(ch_key), g(tg_key)
        if ce is None or te is None:
            return
        C.append(Challenge(ce.title, te.title, claim, obj, tier))

    m, fm, s, h = a.ml, a.factor_model, a.sim, a.hedge
    dp, t = a.delta_panel, a.trade

    # 감사관 → 계량 리서처
    if m is not None and m.verdict != "SIGNAL":
        add("auditor", "quant",
            f"방향 예측 모듈의 원시 출력(OOS {m.oos_accuracy:.1%})",
            f"Murphy resolution {m.resolution:.5f}는 기저율 이상의 정보가 "
            f"없다는 뜻입니다. 감점된 확률을 내는 것이 아니라 출력을 "
            f"무효화해야 합니다.", "oos_statistics")
    elif m is not None and np.isfinite(m.pbo) and m.pbo >= 0.5:
        add("auditor", "quant",
            f"보정 상승확률 {m.prob_up_now:.1%}",
            f"PBO {m.pbo:.0%}로 변형 선택이 불안정합니다. 신호 존재를 "
            f"부정하지는 않으나 신뢰구간을 확대해 읽어야 합니다.",
            "oos_statistics")

    # 팩터 이코노미스트 → 확률모형 총괄
    if fm is not None and s is not None:
        if fm.mismatch:
            add("factor", "sim",
                f"시뮬레이션 상승확률 {s.prob_up:.1%}",
                f"팩터 R²가 {fm.r2:.1%}로 이 자산을 설명하지 못합니다. "
                f"드리프트에 섞인 알파는 누락 변수의 잔차이며, 이를 미래로 "
                f"연장할 근거가 없습니다.", "in_sample_statistics")
        elif np.isfinite(fm.alpha_t) and abs(fm.alpha_t) < 3.0:
            add("factor", "sim",
                f"사후 드리프트 {s.drift.mu_post_ann:+.1%}",
                f"알파 t값이 {fm.alpha_t:+.2f}로 허들 3.0에 미달합니다. "
                f"통계적으로 0과 구별되지 않는 값을 기대수익에 넣으면 "
                f"안 됩니다.", "in_sample_statistics")

    # 확률모형 총괄 → PM
    if s is not None and t is not None and t.direction != "NONE":
        if s.drift.se_ann >= abs(s.drift.mu_hat_ann):
            add("sim", "pm",
                f"목표가 {t.target:,.2f} ({t.target_pct:+.1%})",
                f"드리프트 표준오차 {s.drift.se_ann:.1%}가 추정치 "
                f"{abs(s.drift.mu_hat_ann):.1%}보다 큽니다. 기대수익 기반 "
                f"목표가는 근거가 약하며, 변동성 배수로 잡는 편이 정직합니다.",
                "in_sample_statistics")

    # 리스크 책임자 → PM
    if t is not None and np.isfinite(t.max_loss_pct) and t.max_loss_pct > 0.02:
        add("risk", "pm", f"사이즈 {t.size_weight:.1%}",
            f"손절폭 {t.stop_pct:.1%} × 사이즈로 계좌 손실이 "
            f"{t.max_loss_pct:.2%}가 되어 단일 트레이드 예산 2%를 "
            f"초과합니다.", "tradability")
    if t is not None and np.isfinite(t.rr_ratio) and t.rr_ratio < 1.0 \
            and t.direction != "NONE":
        add("risk", "pm", f"R:R {t.rr_ratio:.2f} 구조",
            f"손절폭이 목표폭보다 큽니다. 승률 {t.p_target_first:.0%} 가정이 "
            f"{t.breakeven_hit_rate:.0%} 아래로만 떨어져도 기대값이 "
            f"음수가 됩니다.", "in_sample_statistics")

    # 체결 총괄 → PM
    l = a.liquidity
    if t is not None and t.direction != "NONE" and np.isfinite(t.roundtrip_cost):
        if np.isfinite(t.expected_pnl_net) and \
                t.roundtrip_cost > abs(t.expected_pnl_net) * 0.3:
            add("micro", "pm", f"비용 후 기대손익 {t.expected_pnl_net:+.2%}",
                f"왕복비용 {t.roundtrip_cost:.2%}가 기대손익의 30%를 넘게 "
                f"잠식합니다. 회전이 늘면 엣지가 빠르게 소멸합니다.",
                "tradability")

    # 레짐 사관 → 매크로 전략가
    rg = a.regime
    if rg is not None and rg.n_switches < 3 and dp is not None and len(dp):
        add("regime", "macro",
            f"{dp.iloc[0]['shock_label']} 기반 매크로 논지",
            f"관측 구간의 국면 전환이 {rg.n_switches}회뿐입니다. 현재 국면 "
            f"밖에서 이 베타가 유지된다는 증거가 없습니다.",
            "in_sample_statistics")

    # 매크로 전략가 → 헤지 설계자
    if dp is not None and len(dp) and h is not None:
        col = dp[dp["r2_collapse"]] if "r2_collapse" in dp else pd.DataFrame()
        if len(col):
            add("macro", "hedge",
                f"헤지 레그 {', '.join(col['factor'].tolist())}",
                f"해당 팩터의 롤링 R²가 최근 붕괴했습니다. 과거 계수로 만든 "
                f"헤지비율은 이미 틀렸을 가능성이 큽니다.",
                "in_sample_statistics")

    # 헤지 설계자 → 리스크 책임자
    if h is not None and np.isfinite(h.var_removed) and h.var_removed < 0.25:
        add("hedge", "risk", "헤지를 통한 위험 통제",
            f"제거 가능 분산이 {h.var_removed:.0%}에 불과합니다. 헤지로 "
            f"한도를 맞추려는 시도는 비용만 쓰고 실패합니다 — 사이즈를 "
            f"줄이는 것이 유일한 수단입니다.", "in_sample_statistics")

    # 파생 → 확률모형 총괄
    mv = a.model_vs_market
    if mv and np.isfinite(mv.get("prob_gap", np.nan)) and abs(mv["prob_gap"]) > 0.10:
        add("deriv", "sim", f"모델 상승확률 {mv['model_prob_up']:.1%}",
            f"시장 위험중립 확률은 {mv['market_prob_up_rn']:.1%}로 "
            f"{mv['prob_gap']:+.1%}p 차이납니다. 리스크 프리미엄으로 전부 "
            f"설명되지 않는 크기라면 모델을 먼저 의심해야 합니다.",
            "economic_mechanism")

    # 분류 전략가 → 팩터 이코노미스트
    cls = a.classification
    if fm is not None and fm.mismatch:
        add("taxonomy", "factor", f"선택 팩터 {', '.join(fm.used_factors[:3])}",
            f"이 자산은 {cls.spec.label_ko}입니다. 현재 팩터 세트가 "
            f"자산군에 맞지 않을 가능성을 먼저 배제해야 합니다.",
            "economic_mechanism")

    # 적대적 검토관 → 최고 확신 전문가
    # 레드팀은 '논지를 만드는' 전문가를 겨냥한다. 인프라 담당(데이터·체결·감사)은
    # 방향 주장을 하지 않으므로 반증 대상이 아니다.
    THESIS_KEYS = {"factor", "macro", "deriv", "quant", "regime", "sim",
                   "pm", "hedge"}
    scored = [(k, e) for k, e in experts.items()
              if e.conviction > 0 and e.key in THESIS_KEYS]
    if scored:
        tgt = max(scored, key=lambda kv: kv[1].conviction)[1]
        add("red", tgt.key, f"{tgt.title}의 결론 (확신도 {tgt.conviction:.0%})",
            "생존편향된 데이터 소스, 개발자 look-ahead, 그리고 이 코드가 전체 "
            "기간을 보고 작성됐다는 사실이 모두 이 결론에 유리하게 작용합니다. "
            "실시간 페이퍼 트레이딩 기록이 없는 한 이 확신도는 정당화되지 "
            "않습니다.", "oos_statistics")

    # 결정론적 판정: 증거 위계가 높은(숫자가 작은) 쪽이 우선
    for c in C:
        tier_rank = EVIDENCE_TIERS.get(c.tier, (6, ""))[0]
        tier_name = EVIDENCE_TIERS.get(c.tier, (6, "서사"))[1]
        if tier_rank <= 3:
            c.resolution = (f"반론 인용 — '{tier_name}'(위계 {tier_rank})은 "
                            f"피고발 주장보다 상위 증거입니다. 해당 주장은 "
                            f"하향 조정됩니다.")
        elif tier_rank <= 4:
            c.resolution = (f"부분 인용 — '{tier_name}'(위계 {tier_rank}). "
                            f"주장을 기각하지는 않되 신뢰구간을 확대합니다.")
        else:
            c.resolution = (f"미해결 쟁점으로 기록 — '{tier_name}'"
                            f"(위계 {tier_rank})은 결정적 반박에 이르지 "
                            f"못합니다. 양측 견해를 병기합니다.")
    return C


# ================================================================ 회의록

@dataclass
class PanelMinutes:
    experts: List[Expert]
    challenges: List[Challenge]
    agreed_facts: List[str]
    open_issues: List[str]
    blocks: List[str]
    stance_tally: Dict[str, int]
    dissent_ratio: float
    summary: str


def convene(a) -> PanelMinutes:
    experts: Dict[str, Expert] = {}
    for b in BUILDERS:
        try:
            e = b(a)
            experts[e.key] = e
        except Exception as exc:                          # pragma: no cover
            continue
    chs = cross_examine(experts, a)
    for c in chs:
        for e in experts.values():
            if e.title == c.challenger:
                e.challenges.append(c)

    lst = list(experts.values())
    blocks = [f"{e.title}: {e.veto_reason}" for e in lst
              if e.stance == "BLOCK" or e.veto_reason]

    tally: Dict[str, int] = {}
    for e in lst:
        tally[e.stance] = tally.get(e.stance, 0) + 1
    voting = [e for e in lst if e.prob_up is not None and np.isfinite(e.prob_up)]
    if len(voting) > 1:
        ps = np.array([e.prob_up for e in voting])
        dissent = float(np.std(ps, ddof=1))
    else:
        dissent = np.nan

    # 합의된 사실 = 모든 전문가가 전제로 삼는 통과 항목
    agreed: List[str] = []
    for e in lst:
        for c in e.checklist:
            if c.verdict == "PASS" and EVIDENCE_TIERS.get(c.weight, (6, ""))[0] <= 3:
                agreed.append(f"{c.item}: {c.value} ({e.title})")
    agreed = agreed[:8]

    open_issues = [f"{c.challenger} → {c.target}: {c.challenged_claim}"
                   for c in chs if "미해결" in c.resolution]

    if blocks:
        summary = (f"거부권 {len(blocks)}건이 발동되어 신규 진입은 불가합니다. "
                   f"{blocks[0]}")
    elif np.isfinite(dissent) and dissent > 0.10:
        summary = (f"확률을 제출한 전문가 {len(voting)}명의 견해 산포가 "
                   f"{dissent:.2f}로 큽니다. 평균을 내지 않고 통합 확률을 "
                   f"중립 쪽으로 축소했습니다 — 불일치 자체가 정보입니다.")
    else:
        bull = tally.get("BULL", 0)
        bear = tally.get("BEAR", 0)
        abst = tally.get("ABSTAIN", 0)
        summary = (f"강세 {bull} · 약세 {bear} · 중립 "
                   f"{tally.get('NEUTRAL',0)} · 기권 {abst}. "
                   f"반대신문 {len(chs)}건 중 미해결 쟁점 "
                   f"{len(open_issues)}건이 남았습니다.")

    return PanelMinutes(lst, chs, agreed, open_issues, blocks, tally,
                        dissent, summary)
