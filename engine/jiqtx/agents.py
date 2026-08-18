# ==============================================================================
# [15/25] agents.py — 하드 게이트 · 결정론적 판정 엔진
# ==============================================================================

"""
jiqtx.agents — 게이트 · 에이전트 · 결정론적 판정 엔진.

설계 원칙 (문헌 근거)
---------------------
LLM 멀티에이전트 토론은 자동으로 정확도를 올리지 않는다.
 - 동조(conformity): 약한 모델은 토론 중 자기 편향을 극소량만 교정
 - 다수의 폭정: 정답인 소수 의견이 사회적 압력에 눌린다
 - 마팅게일 정체: 에이전트들이 '동일 입력'을 받으면 라운드가 지나도
   기대 정확도가 개선되지 않는다는 이론적 결과

따라서 본 시스템은:
 1) **정보 비대칭** — 에이전트마다 서로 다른 데이터 슬라이스를 준다
 2) **거부권은 통계 에이전트에만** — 서사·감성 에이전트는 거부권 없음
 3) **최종 판정은 LLM이 아니라 결정론적 규칙 엔진** — 재현성·감사가능성
 4) **기권(abstain)이 정상 출력** — 게이트 실패는 감점이 아니라 무효화
 5) **캘리브레이션 가중 로그오즈 풀링** — 자기평가가 아니라 실적 기반 가중
"""

import math
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd


# ================================================================ 게이트

@dataclass
class GateResult:
    code: str
    name: str
    passed: bool
    detail: str
    action: str            # "HALT" | "DISABLE_MODULE" | "SIZE_ZERO" | "WARN"
    disables: List[str] = field(default_factory=list)


class GateBoard:
    """순차 게이트. 실패는 '감점'이 아니라 '모듈 무효화'."""

    def __init__(self):
        self.results: List[GateResult] = []
        self.disabled: set = set()
        self.halted: bool = False

    def add(self, g: GateResult):
        self.results.append(g)
        if not g.passed:
            if g.action == "HALT":
                self.halted = True
            for m in g.disables:
                self.disabled.add(m)

    def is_enabled(self, module: str) -> bool:
        return (not self.halted) and (module not in self.disabled)

    def table(self) -> pd.DataFrame:
        return pd.DataFrame([{
            "게이트": r.code, "항목": r.name,
            "판정": "통과" if r.passed else "실패",
            "조치": "-" if r.passed else r.action,
            "내용": r.detail} for r in self.results])


def run_gates(integrity, liq, classification, factor_model, ml_result,
              stress_summary, cfg) -> GateBoard:
    gb = GateBoard()

    # G1 데이터 품질
    gb.add(GateResult(
        "G1", "데이터 무결성", bool(integrity.passed),
        (f"{integrity.n_rows}행 {integrity.start}~{integrity.end}, "
         f"결측 {integrity.missing_ratio:.1%}"
         + ("; " + "; ".join(integrity.issues) if integrity.issues else "")),
        "HALT", ["all"]))

    # G2 유동성
    gb.add(GateResult(
        "G2", "거래 가능성", bool(liq.tradable),
        (f"EDGE 스프레드 {liq.spread_bps:.0f}bp, ADV ${liq.adv_usd:,.0f}, "
         f"무거래일 {liq.zero_ret_ratio:.1%}, "
         f"1%AUM 청산 {liq.days_to_liquidate_1pct_aum:.1f}일"
         + ("; " + liq.reason if liq.reason else "")),
        "SIZE_ZERO", ["sizing"]))

    # G3 팩터 모델 적합
    ok3 = bool(factor_model is not None and not factor_model.mismatch)
    gb.add(GateResult(
        "G3", "팩터 모델 적합", ok3,
        factor_model.mismatch_note if factor_model else "팩터 모델 없음",
        "DISABLE_MODULE", ["alpha", "factor_interpretation"]))

    # G4~G6 ML (과적합·보정)
    ml_ok = bool(ml_result is not None and ml_result.verdict == "SIGNAL")
    detail = "; ".join(ml_result.reasons) if ml_result and ml_result.reasons \
        else (f"OOS {ml_result.oos_accuracy:.1%}, resolution "
              f"{ml_result.resolution:.4f}, DSR {ml_result.strategy_dsr:.0%}"
              if ml_result else "ML 미실행")
    gb.add(GateResult("G4", "ML 과적합·보정", ml_ok, detail,
                      "DISABLE_MODULE", ["ml_probability"]))

    # G7 스트레스 한도
    ok7 = bool(stress_summary.get("within_limit", False))
    gb.add(GateResult(
        "G7", "스트레스 한도", ok7,
        (f"최악 {stress_summary.get('worst_pnl', float('nan')):.1%} "
         f"({stress_summary.get('worst_scenario','')}) vs 한도 "
         f"{stress_summary.get('limit', float('nan')):.0%}"),
        "SIZE_ZERO", ["sizing"]))

    # G8 분류 신뢰도
    ok8 = classification.confidence >= 0.5
    gb.add(GateResult(
        "G8", "자산군 분류 신뢰도", ok8,
        f"{classification.spec.label_ko} (신뢰도 {classification.confidence:.0%})",
        "WARN", []))
    return gb


# ================================================================ 에이전트

@dataclass
class AgentView:
    agent: str
    role: str
    stance: str                # "BULL" | "BEAR" | "NEUTRAL" | "ABSTAIN"
    prob_up: Optional[float]   # None 이면 확률 미제출
    confidence: float          # 0~1, 자기 확신이 아니라 근거 강도
    veto: bool
    evidence: List[str]
    data_scope: str            # 정보 비대칭: 이 에이전트가 본 데이터
    weight_hint: float = 1.0


def _stance(p: Optional[float], thr: float = 0.05) -> str:
    if p is None or not np.isfinite(p):
        return "ABSTAIN"
    if p > 0.5 + thr:
        return "BULL"
    if p < 0.5 - thr:
        return "BEAR"
    return "NEUTRAL"


def assemble_agents(ctx: Dict[str, Any]) -> List[AgentView]:
    """
    각 에이전트는 서로 다른 데이터만 본다(정보 비대칭).
    동일 입력을 주면 토론이 마팅게일이 되어 개선이 없다.
    """
    A: List[AgentView] = []
    cls = ctx["classification"]
    fp = cls.fingerprint

    # A1 Data Steward — 거부권
    integ = ctx["integrity"]
    A.append(AgentView(
        "A1 Data Steward", "데이터 무결성",
        "ABSTAIN", None, 1.0, veto=not integ.passed,
        evidence=([f"{integ.n_rows}행, 결측 {integ.missing_ratio:.1%}, "
                   f"조정정합 {integ.adj_close_consistent}"] + integ.issues[:3]),
        data_scope="원시 OHLCV + 배당/분할 이벤트만"))

    # A2 Taxonomy
    A.append(AgentView(
        "A2 Taxonomy", "자산군 분류", "ABSTAIN", None, cls.confidence, False,
        evidence=cls.evidence[:3] + cls.warnings[:2],
        data_scope="메타데이터 + 수익률 통계지문 (가격 수준 미열람)"))

    # A3 Factor Economist — 가격을 보지 않음
    fm = ctx.get("factor_model")
    if fm is not None:
        top = sorted(fm.coefs.items(), key=lambda kv: -abs(kv[1]))[:3]
        A.append(AgentView(
            "A3 Factor Economist", "팩터 경제학",
            "ABSTAIN", None, 0.6 if fm.interpretation_allowed else 0.2, False,
            evidence=([f"R²={fm.r2:.1%} (밴드 {fm.r2_band[0]:.0%}~{fm.r2_band[1]:.0%})"]
                      + [f"{k}: β={v:+.3f} (t={fm.tstats.get(k, float('nan')):+.1f})"
                         for k, v in top]
                      + [fm.mismatch_note]),
            data_scope="팩터 수익률 패널만 (자산 가격 수준 미열람)"))

    # A4 Macro Nowcaster
    dp = ctx["delta_panel"]
    if dp is not None and len(dp):
        worst = dp.iloc[0]
        A.append(AgentView(
            "A4 Macro Nowcaster", "거시 경로",
            _stance(0.5 - float(np.sign(worst["delta_pct"])) * 0.06),
            0.5 - float(np.sign(worst["delta_pct"])) * 0.06, 0.45, False,
            evidence=[f"최대 노출: {worst['shock_label']} → "
                      f"{worst['delta_pct']:+.1%} (하방베타 기준 "
                      f"{worst['delta_pct_downside']:+.1%})",
                      "발표 예정 지표는 선반영하지 않음 (PIT 원칙)"],
            data_scope="매크로 팩터 시계열 + 발표 캘린더"))

    # A5 Microstructure — 거부권
    liq = ctx["liquidity"]
    A.append(AgentView(
        "A5 Microstructure", "유동성·체결", "ABSTAIN", None, 0.9,
        veto=not liq.tradable,
        evidence=[f"EDGE {liq.spread_bps:.0f}bp (추정량 간 산포 "
                  f"{liq.spread_dispersion*1e4:.0f}bp)",
                  f"Amihud {liq.amihud:.3f}, 청산 "
                  f"{liq.days_to_liquidate_1pct_aum:.1f}일",
                  liq.reason or "거래 가능"],
        data_scope="OHLCV 미시구조 지표만"))

    # A6 Derivatives
    osurf = ctx.get("option_surface")
    if osurf is not None:
        p = 0.5 - float(np.clip(osurf.rr25_1m, -0.15, 0.15)) * 1.2
        A.append(AgentView(
            "A6 Derivatives", "변동성 표면", _stance(p), p, 0.5, False,
            evidence=[f"1M ATM IV {osurf.atm_iv_1m:.1%}, 25Δ RR "
                      f"{osurf.rr25_1m:+.1%}",
                      f"기간구조 기울기 {osurf.term_slope:+.1%} "
                      f"({'백워데이션' if osurf.backwardation else '정상'})",
                      f"IV−RV {osurf.iv_rv_spread:+.1%} (VRP 프록시)"],
            data_scope="옵션 체인 스냅샷만"))

    # A7 Quant Modeler / ML
    mlr = ctx.get("ml")
    if mlr is not None:
        A.append(AgentView(
            "A7 Quant Modeler", "방향 예측",
            _stance(mlr.prob_up_now) if mlr.verdict == "SIGNAL" else "ABSTAIN",
            mlr.prob_up_now if mlr.verdict == "SIGNAL" else None,
            0.8 if mlr.verdict == "SIGNAL" else 0.0, False,
            evidence=([f"판정 {mlr.verdict} (승자 모델 {mlr.model_name})",
                       f"OOS {mlr.oos_accuracy:.1%}, 과적합갭 {mlr.overfit_gap:+.1%}, "
                       f"resolution {mlr.resolution:.4f}"]
                      + mlr.reasons[:3]),
            data_scope="가격 파생 피처 + 트리플배리어 라벨"))

    # A8 Regime Historian — 날짜 블라인드
    rg = ctx.get("regime")
    if rg is not None:
        cur = rg.stats[rg.stats["state"] == rg.current_state]
        mean_ann = float(cur["mean_ann"].iloc[0]) if len(cur) else np.nan
        p = 0.5 + float(np.clip(mean_ann, -0.4, 0.4)) * 0.35
        A.append(AgentView(
            "A8 Regime Historian", "국면 식별", _stance(p), p, 0.55, False,
            evidence=[f"현재 국면: {rg.labels[rg.current_state]} "
                      f"(확률 {max(rg.current_probs.values()):.0%})",
                      f"국면 내 연환산 수익 {mean_ann:+.1%}, "
                      f"변동성 {float(cur['vol_ann'].iloc[0]):.1%}",
                      f"기대 지속기간 {rg.expected_duration[rg.current_state]:.0f}일, "
                      f"점프페널티 λ={rg.jump_penalty:.0f}"],
            data_scope="수익률 파생 국면 피처만 (날짜·종목명 블라인드)"))

    # A9 Simulation
    sim = ctx.get("sim")
    if sim is not None:
        A.append(AgentView(
            "A9 Simulation", "분포 예측", _stance(sim.prob_up), sim.prob_up,
            0.5, False,
            evidence=[f"FHS-EVT P(up) {sim.prob_up:.1%} "
                      f"vs 원본식 GBM {sim.prob_up_naive_gbm:.1%}",
                      f"드리프트 {sim.drift.mu_hat_ann:+.1%} ± "
                      f"{sim.drift.se_ann:.1%} → 사후 {sim.drift.mu_post_ann:+.1%}",
                      sim.drift.note,
                      f"불확실성 중 파라미터 기여 "
                      f"{sim.uncertainty_decomposition['param_share']:.0%}"],
            data_scope="수익률 시계열 + GARCH 잔차"))

    # A10 Risk Officer — 거부권
    rk = ctx.get("var")
    st = ctx.get("stress_summary", {})
    A.append(AgentView(
        "A10 Risk Officer", "리스크 한도", "ABSTAIN", None, 1.0,
        veto=not st.get("within_limit", True),
        evidence=[f"VaR95 {rk.var_fhs_evt:.2%} (FHS-EVT) / ES "
                  f"{rk.es_fhs_evt:.2%}; 채택 모델 {rk.preferred}"
                  if rk else "VaR 미산출",
                  f"스트레스 최악 {st.get('worst_pnl', float('nan')):.1%} "
                  f"({st.get('worst_scenario','')})",
                  ctx["drawdown"].recovery_note],
        data_scope="수익률 꼬리 + 스트레스 시나리오"))

    # A11 Red Team — 반증 의무
    A.append(AgentView(
        "A11 Red Team", "적대적 반증", "ABSTAIN", None, 1.0, False,
        evidence=ctx.get("red_team", ["반증 근거 미제출 — 시스템 오류"]),
        data_scope="타 에이전트 결론 (최종 단계에서만 열람)"))
    return A


def red_team_challenges(ctx: Dict[str, Any]) -> List[str]:
    """사전등록된 반증 프로토콜. 최소 3개 제출이 의무."""
    out: List[str] = []
    fm = ctx.get("factor_model")
    dp = ctx.get("delta_panel")
    sim = ctx.get("sim")
    mlr = ctx.get("ml")
    cls = ctx["classification"]
    rg = ctx.get("regime")

    if fm is not None and fm.mismatch:
        out.append(f"[팩터 대체] R²={fm.r2:.1%}. 선택한 팩터 세트가 이 자산을 "
                   f"설명하지 못한다. 결론이 팩터 선택에 의존한다면 그 결론은 무효.")
    if dp is not None and len(dp):
        col = dp[dp["r2_collapse"]] if "r2_collapse" in dp else pd.DataFrame()
        if len(col):
            out.append(f"[구조 변화] {', '.join(col['factor'].tolist())} 의 R²가 "
                       f"최근 급락. 과거 베타로 추정한 델타는 이미 틀렸을 수 있다.")
        uns = dp[dp["beta_stability_cv"] > 0.8]
        if len(uns):
            out.append(f"[베타 불안정] {', '.join(uns['factor'].head(3).tolist())} 의 "
                       f"베타 변동계수가 0.8 초과. 헤지 비율로 사용 불가.")
    if sim is not None and sim.drift.se_ann >= abs(sim.drift.mu_hat_ann):
        out.append(f"[드리프트 무의미] SE(μ)={sim.drift.se_ann:.1%} ≥ "
                   f"|μ̂|={abs(sim.drift.mu_hat_ann):.1%}. 표본 드리프트는 "
                   f"'모른다'와 통계적으로 구별되지 않는다. "
                   f"원본식 GBM 상승확률 {sim.prob_up_naive_gbm:.0%}는 "
                   f"시장이 아니라 가정에 대한 진술이다.")
    if mlr is not None and mlr.verdict == "ABSTAIN":
        out.append(f"[예측력 부재] ML 판정 ABSTAIN. resolution "
                   f"{mlr.resolution:.4f}. 방향 신호를 근거로 쓸 수 없다.")
    if rg is not None and rg.n_switches < 3:
        out.append(f"[국면 표본 부족] 관측 구간에서 국면 전환이 "
                   f"{rg.n_switches}회뿐. 다른 국면에서의 행태는 검증되지 않았다.")
    if cls.fingerprint.n_obs < 1260:
        out.append(f"[이력 부족] {cls.fingerprint.n_obs}영업일 "
                   f"(약 {cls.fingerprint.n_obs/252:.1f}년). 최소 2개 레짐을 "
                   f"포함하지 못했을 가능성이 크다.")
    out.append("[생존편향] Yahoo Finance에는 상장폐지 종목이 없다. "
               "이 분석은 '오늘 존재하는 종목'만 본다.")
    out.append("[체결 가정] 신호 생성 종가가 아니라 다음 거래일 시가/VWAP "
               "체결을 가정해야 한다. 이 선택 하나가 수익성을 뒤집을 수 있다.")
    return out[:8]


# ================================================================ 판정 엔진

@dataclass
class Verdict:
    grade: str                 # BUY / ACCUMULATE / HOLD / REDUCE / AVOID / ABSTAIN
    direction_prob: Optional[float]
    direction_ci: Tuple[float, float]
    risk_budget_weight: float
    model_confidence: str      # HIGH / MEDIUM / LOW / NONE
    vetoes: List[str]
    disabled_modules: List[str]
    pooled_detail: pd.DataFrame
    dispersion: float
    rationale: List[str]
    disclaimer: str


def adjudicate(agents: List[AgentView], gates: GateBoard,
               sizing, calib_weights: Optional[Dict[str, float]] = None,
               ) -> Verdict:
    """
    ⚙️ 결정론적 판정 엔진 (LLM 아님).

    Step 1  거부권 확인
    Step 2  캘리브레이션 가중 로그오즈 풀링
    Step 3  불일치가 크면 0.5로 축소하고 '불확실' 선언 (평균내지 않음)
    Step 4  3축 분리 출력
    """
    vetoes = [a.agent for a in agents if a.veto]
    disabled = sorted(gates.disabled)

    subs = [(a, a.prob_up) for a in agents
            if a.prob_up is not None and np.isfinite(a.prob_up)]
    rows, num, den = [], 0.0, 0.0
    for a, p in subs:
        w_cal = (calib_weights or {}).get(a.agent, 1.0)
        w = a.confidence * w_cal * a.weight_hint
        p_c = float(np.clip(p, 0.02, 0.98))
        lo = math.log(p_c / (1 - p_c))
        rows.append({"에이전트": a.agent, "역할": a.role, "입장": a.stance,
                     "확률": p, "가중치": w, "로그오즈": lo,
                     "데이터범위": a.data_scope})
        num += w * lo
        den += w

    detail = pd.DataFrame(rows)
    if den > 0:
        pooled_logodds = num / den
        pooled = 1.0 / (1.0 + math.exp(-pooled_logodds))
        disp = float(np.std([r["확률"] for r in rows], ddof=1)) if len(rows) > 1 else 0.0
    else:
        pooled, disp = np.nan, np.nan

    rationale: List[str] = []

    # 불일치 축소 — 평균내지 않는다
    if np.isfinite(disp) and disp > 0.10:
        shrink = min(disp / 0.25, 1.0)
        pooled = 0.5 + (pooled - 0.5) * (1 - 0.8 * shrink)
        rationale.append(f"에이전트 간 확률 산포 {disp:.2f} — 불일치가 크므로 "
                         f"통합 확률을 0.5 쪽으로 축소. 단순 평균은 정보를 "
                         f"파괴한다(원본 리포트가 단기/중기/장기 점수를 평균해 "
                         f"67점을 만든 지점).")

    ci_half = 0.5 * (disp if np.isfinite(disp) else 0.15) + 0.05
    ci = (max(pooled - ci_half, 0.0), min(pooled + ci_half, 1.0)) \
        if np.isfinite(pooled) else (np.nan, np.nan)

    # 모델 신뢰도
    ml_agent = next((a for a in agents if a.agent.startswith("A7")), None)
    if gates.halted:
        conf = "NONE"
    elif ml_agent is not None and ml_agent.stance == "ABSTAIN" and len(disabled) >= 2:
        conf = "LOW"
    elif "ml_probability" in disabled or "alpha" in disabled:
        conf = "MEDIUM" if len(disabled) <= 2 else "LOW"
    else:
        conf = "HIGH"

    w = float(sizing.final_weight) if sizing is not None else 0.0
    if vetoes or gates.halted or "sizing" in disabled:
        w = 0.0

    # 등급
    if gates.halted:
        grade = "ABSTAIN"
        rationale.append("데이터 무결성 게이트 실패 → 전체 분석 중단.")
    elif vetoes or "sizing" in disabled:
        # NO_TRADE 와 AVOID 를 분리한다.
        #   NO_TRADE : 리스크·유동성 제약으로 '들어갈 수 없다' (방향 판단 아님)
        #   AVOID    : 방향 자체가 약세다
        # 원장 리플레이에서 AVOID의 평균 실현수익이 양(+2.0%)으로 나온 원인이
        # 이 혼동이었다. 두 개는 사후 채점에서 완전히 다르게 평가돼야 한다.
        grade = "NO_TRADE"
        why = ", ".join(vetoes) if vetoes else "리스크/유동성 게이트"
        rationale.append(f"거래 불가: {why}. 이는 약세 판단이 아니라 "
                         f"'현 조건에서 포지션을 잡을 수 없다'는 뜻이며, "
                         f"방향 확률은 별도로 읽어야 한다.")
    elif not np.isfinite(pooled):
        grade = "ABSTAIN"
        rationale.append("확률을 제출한 에이전트가 없음 → 방향 판단 보류.")
    elif ci[0] <= 0.5 <= ci[1]:
        grade = "HOLD"
        rationale.append(f"통합 확률 {pooled:.1%}의 신뢰구간 "
                         f"[{ci[0]:.1%}, {ci[1]:.1%}]가 50%를 포함 → "
                         f"방향 우위 미확인. 신규 진입 근거 없음.")
    elif pooled >= 0.58:
        grade = "BUY" if conf == "HIGH" else "ACCUMULATE"
    elif pooled >= 0.53:
        grade = "ACCUMULATE"
    elif pooled <= 0.42:
        grade = "AVOID" if conf == "HIGH" else "REDUCE"
    elif pooled <= 0.47:
        grade = "REDUCE"
    else:
        grade = "HOLD"

    if disabled:
        rationale.append(f"무효화된 모듈: {', '.join(disabled)}. "
                         f"게이트 실패는 감점이 아니라 출력 무효화다.")
    if sizing is not None and np.isfinite(sizing.final_weight):
        rationale.append(f"사이즈 {sizing.final_weight:.1%} — 구속 제약: "
                         f"{sizing.binding_constraint}.")

    return Verdict(
        grade=grade, direction_prob=pooled if np.isfinite(pooled) else None,
        direction_ci=ci, risk_budget_weight=w, model_confidence=conf,
        vetoes=vetoes, disabled_modules=disabled, pooled_detail=detail,
        dispersion=disp, rationale=rationale,
        disclaimer=("본 산출물은 방법론 검증용 정보 제공이며 투자 자문이 아니다. "
                    "확률은 모델 가정에 조건부이며, 미래 성과를 보장하지 않는다."),
    )
