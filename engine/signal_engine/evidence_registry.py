"""
================================================================
  증거 레지스트리  (Evidence Registry)
================================================================
JIQT v4 메타 의사결정 레이어 — 축 A의 1단계.

문제(전문가 2차 피드백):
  GARCH=bearish, momentum=bullish, regime=transition,
  macro=neutral, DSR=hold → "누가 최종 결정하나?"
  기존 _compute_score 는 점수에 +12/-8 직접 더하고 65/45
  임계로 시그널 → 개인 모멘텀 편향의 원인.

해결:
  모든 모델 출력을 **표준 Evidence 객체**로 등록한다.
  점수에 직접 손대지 않는다. 새 모델 추가 = Evidence 하나 추가.
  이후 confidence_engine → conflict_resolver → verdict_mapper
  파이프라인이 최종 판단을 내린다.

Evidence 스키마
---------------
  source     : 증거 출처 식별자 (예: "trend_ma", "ml_oos")
  direction  : -1(약세) | 0(중립) | +1(강세)
  magnitude  : 0.0~1.0  신호 강도
  confidence : 0.0~1.0  이 증거 자체의 신뢰도(초기값, 이후 보정)
  horizon    : "short" | "mid" | "long" | "all"
  veto       : bool  거부권(예: 과적합 시 BUY 거부)
  veto_target: 거부 대상 방향 (+1 이면 강세신호 무효화)
  rationale  : 자연어 근거 한 줄 (XAI용)
  category   : 묶음(추세/모멘텀/리스크/ML/팩터/시나리오/통계)
  raw        : 원시 수치(추적·디버그용)
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional


@dataclass
class Evidence:
    source: str
    direction: int                 # -1 / 0 / +1
    magnitude: float               # 0.0 ~ 1.0
    confidence: float              # 0.0 ~ 1.0
    horizon: str = "all"           # short/mid/long/all
    veto: bool = False
    veto_target: int = 0           # 거부할 방향 (+1 강세 / -1 약세)
    rationale: str = ""
    category: str = "기타"
    raw: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        self.direction = int(max(-1, min(1, self.direction)))
        self.magnitude = float(max(0.0, min(1.0, self.magnitude)))
        self.confidence = float(max(0.0, min(1.0, self.confidence)))

    @property
    def signed_strength(self) -> float:
        """방향·강도·신뢰도를 결합한 부호 있는 기여값."""
        return self.direction * self.magnitude * self.confidence

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class EvidenceRegistry:
    """한 종목(또는 타임프레임)의 모든 증거를 모으는 컨테이너."""

    def __init__(self, scope: str = ""):
        self.scope = scope
        self._items: List[Evidence] = []

    def add(self, ev: Evidence) -> None:
        self._items.append(ev)

    def extend(self, evs: List[Evidence]) -> None:
        self._items.extend(evs)

    @property
    def items(self) -> List[Evidence]:
        return list(self._items)

    def by_category(self) -> Dict[str, List[Evidence]]:
        out: Dict[str, List[Evidence]] = {}
        for e in self._items:
            out.setdefault(e.category, []).append(e)
        return out

    def vetoes(self) -> List[Evidence]:
        return [e for e in self._items if e.veto]

    def to_list(self) -> List[Dict[str, Any]]:
        return [e.to_dict() for e in self._items]


# ================================================================
#  어댑터: 기존 엔진 출력 → 표준 Evidence
# ================================================================
def _mag(x: float, scale: float) -> float:
    """원시값을 0~1 강도로 사상(부호는 호출부에서 처리)."""
    return float(min(1.0, abs(x) / scale)) if scale else 0.0


def adapt_timeframe(tf: Dict[str, Any],
                    horizon: str) -> List[Evidence]:
    """
    analyze_one_timeframe 의 한 결과 dict 를 Evidence 리스트로.
    점수 계산 로직을 복제하지 않고, 각 신호를 독립 증거로 분리.
    """
    evs: List[Evidence] = []
    trend = tf.get("trend", {}) or {}
    mom = tf.get("momentum", {}) or {}
    risk = tf.get("risk", {}) or {}
    ml = tf.get("ml", {}) or {}
    of = tf.get("orderflow", {}) or {}
    vol = tf.get("volatility", {}) or {}

    # ── 추세 ──
    mac = trend.get("ma_cross_diff")
    if mac is not None:
        d = 1 if mac > 0 else -1
        evs.append(Evidence(
            source=f"trend_ma_{horizon}", direction=d,
            magnitude=_mag(mac, abs(mac) + 1e-9) if mac else 0.4,
            confidence=0.65, horizon=horizon, category="추세",
            rationale=("골든크로스(단기MA>장기MA)" if d > 0
                       else "데드크로스(단기MA<장기MA)"),
            raw={"ma_cross_diff": mac}))
    slope = trend.get("sma_slope_pct")
    if slope is not None:
        d = 1 if slope > 0 else -1
        evs.append(Evidence(
            source=f"trend_slope_{horizon}", direction=d,
            magnitude=_mag(slope, 5.0), confidence=0.55,
            horizon=horizon, category="추세",
            rationale=f"이동평균 기울기 {slope:+.2f}%",
            raw={"sma_slope_pct": slope}))

    # ── 모멘텀 ──
    rsi = mom.get("rsi_last")
    if rsi is not None:
        if rsi >= 70:
            evs.append(Evidence(
                source=f"momentum_rsi_{horizon}", direction=-1,
                magnitude=_mag(rsi - 70, 30), confidence=0.6,
                horizon=horizon, category="모멘텀",
                rationale=f"RSI {rsi:.0f} 과매수",
                raw={"rsi": rsi}))
            if rsi >= 80:
                evs.append(Evidence(
                    source=f"rsi_overbought_veto_{horizon}",
                    direction=-1, magnitude=1.0, confidence=0.7,
                    horizon=horizon, category="모멘텀",
                    veto=True, veto_target=1,
                    rationale=f"RSI {rsi:.0f} 극단 과매수 — 강세 신호 강등",
                    raw={"rsi": rsi}))
        elif rsi <= 30:
            evs.append(Evidence(
                source=f"momentum_rsi_{horizon}", direction=1,
                magnitude=_mag(30 - rsi, 30), confidence=0.55,
                horizon=horizon, category="모멘텀",
                rationale=f"RSI {rsi:.0f} 과매도 → 반등 가능",
                raw={"rsi": rsi}))
        else:
            evs.append(Evidence(
                source=f"momentum_rsi_{horizon}", direction=0,
                magnitude=0.2, confidence=0.4, horizon=horizon,
                category="모멘텀", rationale=f"RSI {rsi:.0f} 중립",
                raw={"rsi": rsi}))
    cr = mom.get("cum_return_pct")
    if cr is not None:
        evs.append(Evidence(
            source=f"momentum_ret_{horizon}",
            direction=1 if cr > 0 else -1,
            magnitude=_mag(cr, 30), confidence=0.5,
            horizon=horizon, category="모멘텀",
            rationale=f"누적수익 {cr:+.1f}%", raw={"cum_return_pct": cr}))

    # ── 리스크-수익 ──
    sh = risk.get("sharpe")
    if sh is not None:
        d = 1 if sh > 0 else -1
        evs.append(Evidence(
            source=f"risk_sharpe_{horizon}", direction=d,
            magnitude=_mag(sh, 2.0), confidence=0.6,
            horizon=horizon, category="리스크",
            rationale=f"샤프 {sh:.2f}", raw={"sharpe": sh}))
    mdd = risk.get("max_drawdown")
    if mdd is not None and abs(mdd) > 0.3:
        evs.append(Evidence(
            source=f"risk_mdd_{horizon}", direction=-1,
            magnitude=_mag(abs(mdd) - 0.3, 0.5), confidence=0.65,
            horizon=horizon, category="리스크",
            rationale=f"최대낙폭 {mdd*100:.0f}% 큼",
            raw={"max_drawdown": mdd}))

    # ── ML (과적합 시 거부권) ──
    p = ml.get("prob_up")
    if p is not None:
        d = 1 if p > 0.55 else (-1 if p < 0.45 else 0)
        evs.append(Evidence(
            source=f"ml_oos_{horizon}", direction=d,
            magnitude=_mag(abs(p - 0.5), 0.5),
            confidence=0.5, horizon=horizon, category="ML",
            rationale=f"ML OOS 상승확률 {p*100:.0f}%",
            raw={"prob_up": p, "accuracy": ml.get("accuracy")}))
    if ml.get("overfit_flag"):
        gap = ml.get("overfit_gap")
        evs.append(Evidence(
            source=f"ml_overfit_veto_{horizon}", direction=-1,
            magnitude=1.0, confidence=0.8, horizon=horizon,
            category="ML", veto=True, veto_target=1,
            rationale=(f"ML 인샘플-OOS 격차"
                       f"{(gap or 0)*100:.0f}%p 과대 — 과적합, 강세 신호 거부"),
            raw={"overfit_gap": gap}))

    # ── 오더플로우 ──
    cvd = of.get("cvd_20d_change")
    if cvd is not None:
        evs.append(Evidence(
            source=f"orderflow_cvd_{horizon}",
            direction=1 if cvd > 0 else -1,
            magnitude=0.35, confidence=0.4, horizon=horizon,
            category="오더플로우",
            rationale=("CVD 상승(매수 우위)" if cvd > 0
                       else "CVD 하락(매도 우위)"),
            raw={"cvd_20d_change": cvd}))

    # ── 변동성 페널티 ──
    if vol.get("vol_regime") == "높음":
        evs.append(Evidence(
            source=f"vol_regime_{horizon}", direction=-1,
            magnitude=0.3, confidence=0.5, horizon=horizon,
            category="리스크", rationale="변동성 높음 — 주의",
            raw={"vol_regime": "높음"}))

    return evs


def adapt_precision(precision: Dict[str, Any]) -> List[Evidence]:
    """
    기관급 정밀도(PSR/DSR/MinTRL/CDaR) → Evidence.
    이것이 v3의 ceiling/capping 을 대체하는 '거부권' 증거.
    """
    evs: List[Evidence] = []
    if not precision:
        return evs

    dsr = (precision.get("dsr") or {}).get("dsr")
    if dsr is not None and dsr == dsr:  # not NaN
        if dsr < 0.90:
            evs.append(Evidence(
                source="dsr_veto", direction=-1,
                magnitude=float(min(1.0, (0.90 - dsr) / 0.40)),
                confidence=0.85, category="통계",
                veto=True, veto_target=1,
                rationale=(f"디플레이티드 샤프 {dsr*100:.0f}% < 90% "
                           f"— 다중검정 보정 후 우위 약함, 강세 강등"),
                raw={"dsr": dsr}))
        else:
            evs.append(Evidence(
                source="dsr_ok", direction=1, magnitude=0.4,
                confidence=0.7, category="통계",
                rationale=f"DSR {dsr*100:.0f}% — 다중검정 통과",
                raw={"dsr": dsr}))

    psr = (precision.get("psr") or {}).get("psr")
    if psr is not None and psr == psr:
        d = 1 if psr >= 0.90 else -1
        evs.append(Evidence(
            source="psr", direction=d,
            magnitude=float(min(1.0, abs(psr - 0.5) / 0.5)),
            confidence=0.7, category="통계",
            rationale=(f"확률적 샤프 {psr*100:.0f}% "
                       + ("(신뢰 가능)" if d > 0 else "(통계적 미확정)")),
            raw={"psr": psr}))

    trl = precision.get("min_trl") or {}
    if trl and not trl.get("enough", True):
        evs.append(Evidence(
            source="min_trl_warn", direction=-1, magnitude=0.5,
            confidence=0.6, category="통계",
            rationale=("표본이 샤프 신뢰 최소길이 미달 — 보수화"),
            raw={"min_trl": trl.get("min_trl"),
                 "have": trl.get("have")}))

    reg = precision.get("regime") or {}
    worst = reg.get("worst_regime_sharpe")
    if worst is not None and worst < -0.5:
        evs.append(Evidence(
            source="regime_fragility", direction=-1,
            magnitude=float(min(1.0, abs(worst) / 2.0)),
            confidence=0.6, category="시나리오",
            rationale=(f"하락국면 샤프 {worst:.2f} — 상승장 의존성 큼"),
            raw={"worst_regime_sharpe": worst}))

    return evs


def adapt_stress(stress: Dict[str, Any]) -> List[Evidence]:
    """스트레스 테스트 → 시나리오 내성 Evidence."""
    if not stress:
        return []
    worst = abs(stress.get("worst_loss_pct", 0.0))
    if worst <= 0:
        return []
    ev = Evidence(
        source="stress_worst", direction=-1,
        magnitude=float(min(1.0, worst / 60.0)),
        confidence=0.65, category="시나리오",
        rationale=f"최악 시나리오 손실 {worst:.0f}%",
        raw={"worst_loss_pct": -worst})
    out = [ev]
    if worst >= 55:
        out.append(Evidence(
            source="stress_fragility_veto", direction=-1,
            magnitude=1.0, confidence=0.7, category="시나리오",
            veto=True, veto_target=1,
            rationale=(f"최악 시나리오 손실 {worst:.0f}% — "
                       f"시나리오 내성 취약, 강세 강등"),
            raw={"worst_loss_pct": -worst}))
    return out


def adapt_factor(factor: Dict[str, Any]) -> List[Evidence]:
    """팩터 분해 → 시스템 위험 노출 Evidence."""
    if not factor or not factor.get("ok", True):
        return []
    sys_pct = factor.get("systematic_pct")
    if sys_pct is None:
        return []
    d = -1 if sys_pct > 70 else 0
    return [Evidence(
        source="factor_systematic", direction=d,
        magnitude=float(min(1.0, max(0.0, (sys_pct - 50) / 50))),
        confidence=0.45, category="팩터",
        rationale=(f"시스템(시장) 위험 {sys_pct:.0f}% "
                   + ("— 분산효과 제한" if d < 0 else "— 보통")),
        raw={"systematic_pct": sys_pct,
             "is_real_market": factor.get("is_real_market")})]
