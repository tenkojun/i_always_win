# ==============================================================================
# [13/25] thesis.py — 드라이버 기반 시나리오 · 반증 조건 · 모니터링
# ==============================================================================

"""
jiqtx.thesis — 투자 논지 엔진.

해지펀드 리서치 메모와 리테일 리포트를 가르는 것은 지표 개수가 아니라
다음 네 가지가 명시되어 있느냐다.

  1) 시나리오가 **드라이버로 정의**되어 있는가 (분위수 슬라이싱이 아니라)
  2) 각 시나리오의 확률이 **경험적 결합분포**에서 나왔는가 (직관이 아니라)
  3) **무엇이 틀리면 논지가 죽는가** (kill criteria)가 사전에 적혀 있는가
  4) **무엇을 언제 확인할 것인가** (monitoring plan)가 정해져 있는가

원본 리포트에는 "시나리오 매트릭스"가 있었지만 확률이 없고 손익이 없었다.
여기서는 각 시나리오의 손익을 **팩터 델타 × 충격**으로 계산하고,
확률을 해당 충격 조합의 **역사적 동시 발생 빈도**에서 추정한다.
"""

import math
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd


# ================================================================ 시나리오

@dataclass
class Scenario:
    name: str
    kind: str                       # BULL / BASE / BEAR / TAIL
    drivers: Dict[str, float]       # 팩터 -> 지평 누적 충격
    driver_desc: str
    prob_empirical: float           # 역사적 동시 발생 빈도
    prob_model: float               # 모델 분포에서의 확률
    pnl_factor: float               # 팩터 델타로 계산한 손익
    pnl_total: float                # + 잔차 드리프트
    price_target: float
    ret_target: float
    n_historical: int
    note: str = ""


def _horizon_shocks(F: pd.DataFrame, horizon: int) -> pd.DataFrame:
    """지평 누적 팩터 변화 (겹치는 창)."""
    return F.rolling(horizon).sum()


def shrink_alpha(alpha_ann: float, alpha_t: float, shrink: float = 0.6,
                 t_min: float = 2.0) -> Tuple[float, str]:
    """
    시나리오 목표가에 알파를 그대로 넣으면 안 된다.
    표본 알파는 드리프트 추정오차를 그대로 물려받고, 유의하지 않은 알파는
    사실상 잡음이다. t < 2 이면 0으로, 유의해도 축소해서 쓴다.
    """
    if not np.isfinite(alpha_ann):
        return 0.0, "알파 미산출 → 0 적용"
    if not np.isfinite(alpha_t) or abs(alpha_t) < t_min:
        return 0.0, (f"알파 t={alpha_t:.2f} < {t_min} → 통계적으로 0과 "
                     f"구별되지 않으므로 시나리오 목표에 반영하지 않음")
    return float(alpha_ann * (1 - shrink)), \
        (f"알파 {alpha_ann:+.1%} (t={alpha_t:.2f})를 {shrink:.0%} 축소해 "
         f"{alpha_ann*(1-shrink):+.1%} 적용")


def build_scenarios(delta_panel: pd.DataFrame, F: pd.DataFrame,
                    spot: float, horizon: int = 63,
                    resid_drift_ann: float = 0.0,
                    ann: int = 252, n_drivers: int = 2) -> List[Scenario]:
    """
    상위 드라이버 2~3개의 ±1σ / ±2σ 조합으로 시나리오를 구성하고,
    각 조합의 역사적 동시 발생 빈도를 확률로 쓴다.
    """
    if delta_panel is None or len(delta_panel) == 0 or F is None or len(F) == 0:
        return []

    dp = delta_panel.copy()
    dp = dp[dp["factor"].isin(F.columns)]
    if len(dp) == 0:
        return []
    dp = dp.reindex(dp["delta_pct"].abs().sort_values(ascending=False).index)
    # 서로 상관이 높은 팩터를 둘 다 드라이버로 쓰면 시나리오가 같은 충격을
    # 두 번 세게 된다. |corr| > 0.9 인 후보는 델타가 큰 쪽만 남긴다.
    drivers: List[str] = []
    for f in dp["factor"]:
        if f not in F.columns:
            continue
        dup = False
        for g in drivers:
            v = F[[f, g]].dropna()
            if len(v) > 100 and abs(float(v.corr().iloc[0, 1])) > 0.90:
                dup = True
                break
        if not dup:
            drivers.append(f)
        if len(drivers) >= n_drivers:
            break

    H = _horizon_shocks(F[drivers], horizon).dropna()
    if len(H) < 100:
        return []
    sd = H.std(ddof=1)
    if (sd <= 0).any():
        return []

    beta_now = dict(zip(dp["factor"], dp["beta_now"]))
    beta_dn = dict(zip(dp["factor"], dp["downside_beta"]))
    beta_st = dict(zip(dp["factor"], dp["beta_static"]))
    label = dict(zip(dp["factor"], dp["shock_label"]))

    def B(f, adverse=False):
        for src in ((beta_dn, beta_now, beta_st) if adverse
                    else (beta_now, beta_st)):
            v = src.get(f)
            if v is not None and np.isfinite(v):
                return float(v)
        return 0.0

    # 자산에 유리한 충격 방향 = 델타 부호와 반대되는 팩터 이동
    fav_sign = {f: (1.0 if B(f) > 0 else -1.0) for f in drivers}

    resid = resid_drift_ann * horizon / ann

    def make(name, kind, mults, adverse):
        sh, cond = {}, []
        for f, m in zip(drivers, mults):
            s = float(sd[f] * m * fav_sign[f])
            sh[f] = s
            cond.append(f"{label.get(f, f).split()[0]} "
                        f"{'+' if s > 0 else ''}{s:.2f}"
                        f"{' (' + f'{m:+.0f}σ' + ')' if m else ''}")
        # 경험적 동시 발생 빈도
        mask = np.ones(len(H), dtype=bool)
        for f, m in zip(drivers, mults):
            s = sh[f]
            if m > 0:
                mask &= (H[f].values >= s * 0.75)
            elif m < 0:
                mask &= (H[f].values <= s * 0.75)
        n_hist = int(mask.sum())
        p_emp = float(n_hist / len(H))
        pnl_f = float(sum(B(f, adverse) * v for f, v in sh.items()))
        pnl = pnl_f + resid
        return Scenario(
            name=name, kind=kind, drivers=sh, driver_desc=" · ".join(cond),
            prob_empirical=p_emp, prob_model=np.nan,
            pnl_factor=pnl_f, pnl_total=pnl,
            price_target=float(spot * (1 + pnl)), ret_target=pnl,
            n_historical=n_hist)

    k = len(drivers)
    out = [
        make("강세 시나리오", "BULL", [1.0] * k, False),
        make("강세 극단", "TAIL", [2.0] * k, False),
        make("기본 시나리오", "BASE", [0.0] * k, False),
        make("약세 시나리오", "BEAR", [-1.0] * k, True),
        make("약세 극단(테일)", "TAIL", [-2.0] * k, True),
    ]
    # 드라이버만으로 손실이 나지 않는 경우를 진단한다.
    bear = next((x for x in out if x.kind == "BEAR"), None)
    if bear is not None and bear.ret_target >= 0:
        for x in out:
            if x.kind in ("BEAR", "TAIL") and x.ret_target >= -0.005:
                x.note = ("⚠ 이 드라이버 조합만으로는 손실이 발생하지 않는다. "
                          "즉 하방 위험의 출처가 선택된 팩터가 아니라 "
                          "고유(잔차)·꼬리·유동성이라는 뜻이다. "
                          "팩터 스트레스로 하방을 재면 과소평가된다 — "
                          "VaR/ES와 낙폭 섹션을 기준으로 보라.")

    # BASE 확률은 나머지의 잔여로
    tot = sum(s.prob_empirical for s in out if s.kind != "BASE")
    for s in out:
        if s.kind == "BASE":
            s.prob_empirical = float(max(1.0 - tot, 0.0))
            s.note = ("드라이버가 중립일 때. 잔차 드리프트만 반영. "
                      "경험확률은 표본 기간의 무조건부 빈도이므로 "
                      "그 기간의 드리프트를 그대로 물려받는다 — "
                      "상승장 표본에서는 강세 시나리오 확률이 구조적으로 높다.")
    return out


def attach_model_probs(scenarios: List[Scenario], terminal: np.ndarray,
                       spot: float) -> List[Scenario]:
    """
    모델 분포에서의 확률을 **초과확률**로 계산한다.

    버킷 분할(구간 질량)로 계산하면 양끝 버킷이 모든 꼬리 질량을 흡수해
    '극단 시나리오 확률 > 강세 시나리오 확률' 같은 인위적 결과가 나온다.
    PM이 실제로 읽는 값은 "목표 이상 갈 확률"이므로 초과확률이 옳다.

      상방 시나리오 : P(수익 ≥ 목표)
      하방 시나리오 : P(수익 ≤ 목표)
      기본 시나리오 : P(|수익 − 목표| ≤ 반폭)
    """
    if terminal is None or len(terminal) == 0:
        return scenarios
    rets = terminal / spot - 1.0
    base = next((x for x in scenarios if x.kind == "BASE"), None)
    b0 = base.ret_target if base is not None else float(np.median(rets))
    others = sorted([abs(x.ret_target - b0) for x in scenarios
                     if x.kind != "BASE" and np.isfinite(x.ret_target)])
    half = (others[0] / 2 if others else 0.02)
    for s in scenarios:
        if s.kind == "BASE":
            s.prob_model = float(np.mean(np.abs(rets - s.ret_target) <= half))
        elif s.ret_target >= b0:
            s.prob_model = float(np.mean(rets >= s.ret_target))
        else:
            s.prob_model = float(np.mean(rets <= s.ret_target))
    return scenarios


def expected_value(scenarios: List[Scenario], use: str = "empirical"
                   ) -> Dict[str, float]:
    key = "prob_empirical" if use == "empirical" else "prob_model"
    ps = np.array([getattr(s, key) for s in scenarios], float)
    rs = np.array([s.ret_target for s in scenarios], float)
    m = np.isfinite(ps) & np.isfinite(rs)
    if not m.any() or ps[m].sum() <= 0:
        return {"ev": np.nan, "ev_up": np.nan, "ev_down": np.nan,
                "payoff_ratio": np.nan}
    p = ps[m] / ps[m].sum()
    r = rs[m]
    ev = float((p * r).sum())
    up = float((p[r > 0] * r[r > 0]).sum())
    dn = float((p[r < 0] * r[r < 0]).sum())
    # 하방 시나리오가 없으면 페이오프 비율은 정의되지 않는다.
    # 무한대를 '좋은 트레이드'로 읽으면 안 되므로 명시적으로 표시한다.
    pr = float(abs(up / dn)) if dn < -1e-6 else np.nan
    return {"ev": ev, "ev_up": up, "ev_down": dn, "payoff_ratio": pr,
            "no_downside_scenario": bool(dn >= -1e-6)}


# ================================================================ 킬 크라이테리아

@dataclass
class KillCriterion:
    trigger: str
    metric: str
    threshold: str
    current: str
    breached: bool
    action: str


def kill_criteria(a) -> List[KillCriterion]:
    """
    사전등록된 반증 조건. '무엇이 사실이면 이 논지는 죽는가'.
    사후에 만들면 의미가 없으므로 분석 시점에 자동 생성해 원장에 남긴다.
    """
    out: List[KillCriterion] = []
    dp = a.delta_panel

    # 1) 팩터 모델 붕괴
    fm = a.factor_model
    if fm is not None:
        lo = fm.r2_band[0]
        out.append(KillCriterion(
            "팩터 모델 설명력 붕괴", "팩터 R²",
            f"< {lo*0.55:.0%} (자산군 밴드 하단의 55%)",
            f"{fm.r2:.1%}", bool(fm.mismatch),
            "팩터 기반 델타·헤지·스트레스 전부 무효화. 가격 모듈만 사용."))

    # 2) 베타 불안정
    if len(dp):
        # β≈0 인 팩터는 변동계수가 발산하므로 '유의한 노출'만 대상으로 한다.
        mat = dp[(dp["delta_pct"].abs() > 0.005) &
                 (dp["beta_now"].abs() > 0.02)]
        if len(mat) and mat["beta_stability_cv"].notna().any():
            worst = mat.loc[mat["beta_stability_cv"].idxmax()]
            cv = float(worst["beta_stability_cv"])
            out.append(KillCriterion(
                "헤지 비율 불안정", f"β 변동계수 ({worst['factor']}, "
                f"Δ={worst['delta_pct']:+.1%})",
                "> 0.80", f"{cv:.2f}", bool(cv > 0.80),
                "해당 팩터로 헤지 불가. 헤지 없이 총노출로 사이징하거나 진입 보류."))
        else:
            out.append(KillCriterion(
                "헤지 비율 불안정", "β 변동계수 (유의 노출)",
                "> 0.80", "유의한 팩터 노출 없음", False,
                "헤지 대상 자체가 없음. 위험 통제는 사이즈로만."))
        col = dp[dp["r2_collapse"]] if "r2_collapse" in dp else pd.DataFrame()
        if len(col):
            out.append(KillCriterion(
                "구조 변화 발생", "롤링 R² 급락",
                "최근 R² < 과거 중앙값의 35%",
                ", ".join(col["factor"].tolist()), True,
                "과거 베타로 추정한 델타는 이미 틀렸을 수 있음. 재추정 전 신규 진입 금지."))

    # 3) 드리프트 무의미
    s = a.sim
    if s is not None:
        breached = bool(s.drift.se_ann >= abs(s.drift.mu_hat_ann))
        out.append(KillCriterion(
            "드리프트 추정 무의미", "SE(μ̂) vs |μ̂|",
            "SE ≥ |μ̂|", f"{s.drift.se_ann:.1%} vs {abs(s.drift.mu_hat_ann):.1%}",
            breached,
            "기대수익 기반 사이징 금지. 변동성·낙폭 기준으로만 사이징."))

    # 4) ML 엣지 소멸
    if a.ml is not None:
        out.append(KillCriterion(
            "방향 엣지 미확인", "Murphy Resolution",
            "≈ 0 또는 게이트 실패", f"{a.ml.resolution:.5f} ({a.ml.verdict})",
            a.ml.verdict != "SIGNAL",
            "확률 출력 사용 금지. 방향 베팅이 아니라 리스크 예산으로만 접근."))

    # 5) 유동성
    l = a.liquidity
    out.append(KillCriterion(
        "거래 비용 초과", "EDGE 스프레드",
        f"> {a.classification.spec.max_spread_bps:.0f}bp",
        f"{l.spread_bps:.1f}bp" if np.isfinite(l.spread_bps) else "산출 불가",
        not l.tradable,
        "체결 비용이 기대 엣지를 잠식. 사이즈 0 또는 지정가 분할."))

    # 6) 스트레스 한도
    st = a.stress_summary
    out.append(KillCriterion(
        "스트레스 한도 초과", "최악 시나리오 손실",
        f"> {st.get('limit', 0.35):.0%}",
        f"{st.get('worst_pnl', float('nan')):.1%}",
        not st.get("within_limit", True),
        "사이즈 0. 헤지로 노출을 줄이기 전까지 진입 불가."))

    # 7) 어닝 이벤트
    eq = getattr(a, "equity", None)
    if eq is not None and eq.earnings is not None:
        es = eq.earnings
        d = es.days_to_next
        out.append(KillCriterion(
            "이벤트 리스크 구간", "다음 어닝까지",
            "≤ 14영업일", f"{d}일" if d is not None else "미상",
            bool(d is not None and d <= 14),
            f"발표 다음날 |수익률| 90분위 {es.p90_abs_move:.1%}. "
            f"이벤트 전 사이즈 축소 또는 옵션으로 대체."))
    return out


# ================================================================ 캘린더 · 모니터링

@dataclass
class MonitorItem:
    what: str
    source: str
    frequency: str
    threshold: str
    why: str


def monitoring_plan(a, scenarios: List[Scenario]) -> List[MonitorItem]:
    out: List[MonitorItem] = []
    dp = a.delta_panel
    src = {"real_yield_10y": "FRED DFII10", "nominal_10y": "FRED DGS10",
           "broad_dollar": "FRED DTWEXBGS", "breakeven_10y": "FRED T10YIE",
           "hy_oas": "FRED BAMLH0A0HYM2", "vix": "FRED VIXCLS",
           "wti": "FRED DCOILWTICO", "curve_2s10s": "FRED T10Y2Y",
           "mkt_excess": "SPY", "gpr": "Iacoviello GPR"}
    if len(dp):
        for _, r in dp.head(3).iterrows():
            f = r["factor"]
            out.append(MonitorItem(
                what=f"{r['shock_label']}",
                source=src.get(f, f),
                frequency="일간",
                threshold=(f"β={r['beta_now']:+.3f} → 1σ 이동 시 "
                           f"{r['delta_pct']:+.1%}"),
                why="포지션 손익의 최대 단일 기여 요인"))
    out.append(MonitorItem(
        "롤링 팩터 R²", "내부 재계산", "주간",
        f"기대밴드 {a.classification.spec.r2_band[0]:.0%}~"
        f"{a.classification.spec.r2_band[1]:.0%} 이탈",
        "R² 붕괴는 버그가 아니라 구조 변화 신호"))
    out.append(MonitorItem(
        "EDGE 스프레드 · ADV", "내부 재계산", "주간",
        f"> {a.classification.spec.max_spread_bps:.0f}bp 또는 ADV 50% 급감",
        "유동성 증발은 청산 국면에만 드러남"))
    if a.regime is not None:
        rg = a.regime
        out.append(MonitorItem(
            "국면 전환", "Jump Model 재적합", "주간",
            f"현재 '{rg.labels[rg.current_state]}' 이탈",
            f"기대 지속 {rg.expected_duration[rg.current_state]:.0f}영업일"))
    eq = getattr(a, "equity", None)
    if eq is not None and eq.earnings is not None and eq.earnings.next_date:
        out.append(MonitorItem(
            f"어닝 발표 ({eq.earnings.next_date})", "발행사 IR", "이벤트",
            f"|수익률| 90분위 {eq.earnings.p90_abs_move:.1%}",
            "연 분산의 "
            f"{eq.earnings.gap_share_of_var:.0%}가 발표일에 집중"))
    out.append(MonitorItem(
        "예측 채점", "캘리브레이션 원장", "지평 도래 시",
        "Brier skill ≤ 0 이 지속되면 확률 출력 중단",
        "시스템이 자기 예측력을 스스로 채점"))
    return out


def catalysts(a, horizon_days: int = 63) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    eq = getattr(a, "equity", None)
    if eq is not None and eq.earnings is not None and eq.earnings.days_to_next:
        d = eq.earnings.days_to_next
        out.append({"시점": f"D-{d}", "이벤트": f"어닝 발표 ({eq.earnings.next_date})",
                    "예상 영향": f"|수익률| 중앙값 {eq.earnings.median_abs_move:.1%} / "
                                f"90분위 {eq.earnings.p90_abs_move:.1%}",
                    "성격": "이벤트"})
    o = a.option_surface
    if o is not None and o.backwardation:
        out.append({"시점": "현재", "이벤트": "IV 기간구조 백워데이션",
                    "예상 영향": f"3M−1M {o.term_slope:+.1%}",
                    "성격": "시장 신호"})
    if a.regime is not None:
        rg = a.regime
        dur = rg.expected_duration[rg.current_state]
        if dur < horizon_days:
            out.append({"시점": f"약 {dur:.0f}영업일",
                        "이벤트": f"국면 전환 예상 ('{rg.labels[rg.current_state]}' 이탈)",
                        "예상 영향": "레짐 조건부 베타 재추정 필요",
                        "성격": "구조"})
    out.append({"시점": "상시", "이벤트": "매크로 발표 캘린더 (FOMC/CPI/고용)",
                "예상 영향": "상위 드라이버가 금리·달러면 직접 충격",
                "성격": "매크로 — 외부 피드 연결 필요"})
    return out
