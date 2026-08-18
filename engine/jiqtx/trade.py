# ==============================================================================
# [14/25] trade.py — 배리어 확률 트레이드 · 최소분산 헤지 · 수익 귀인
# ==============================================================================

"""
jiqtx.trade — 트레이드 구성 + 헤지 설계.

"비중 15%"는 결론이 아니다. 실제 운용에서 필요한 것은:
  · 어디서 들어가고 (entry)
  · 어디서 틀렸다고 인정하고 (stop — 변동성 기반, 임의 % 아님)
  · 어디를 목표로 하며 (target — 시나리오에서 도출)
  · 목표/손절 중 어디를 먼저 칠 확률이 얼마고 (배리어 확률, 시뮬 경로에서)
  · 비용 후 기대값이 양수인가 (breakeven)
  · 팩터 노출을 어떻게 상쇄하는가 (헤지 바스켓)
  · 헤지 후 남는 위험이 무엇인가 (residual)

헤지 바스켓은 최소분산 헤지비율이며, 이는 다변량 팩터 회귀 계수와 동일하다.
따라서 팩터 모델이 미스매칭이면 헤지도 무효다 — 이 연결을 명시한다.
"""

import math
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

# ── 패키지 내부 의존 ──────────────────────────────────────────
from .micro import roundtrip_cost



# ================================================================ 트레이드

@dataclass
class TradePlan:
    direction: str                 # LONG / SHORT / NONE
    entry: float
    entry_note: str
    stop: float
    stop_pct: float
    stop_basis: str
    target: float
    target_pct: float
    target_basis: str
    horizon_days: int
    rr_ratio: float                # 보상/위험
    p_target_first: float          # 목표 선도달 확률 (시뮬 경로)
    p_stop_first: float
    p_neither: float
    expected_pnl: float            # 배리어 반영 기대손익
    expected_pnl_net: float        # 비용 차감
    roundtrip_cost: float
    breakeven_hit_rate: float      # 비용 감안 손익분기 승률
    edge_vs_breakeven: float
    size_weight: float
    risk_per_unit: float           # 사이즈 × 손절폭 = 계좌 대비 손실
    max_loss_pct: float
    verdict: str
    notes: List[str] = field(default_factory=list)


def barrier_probabilities(paths: np.ndarray, s0: float,
                          target: float, stop: float,
                          long: bool = True) -> Dict[str, float]:
    """
    시뮬 경로에서 목표/손절 선도달 확률과 기대손익을 계산한다.
    paths: (n_sims, horizon) 가격 경로
    """
    if paths is None or paths.size == 0:
        return {"p_target": np.nan, "p_stop": np.nan, "p_neither": np.nan,
                "expected": np.nan}
    n, H = paths.shape
    if long:
        hit_t = paths >= target
        hit_s = paths <= stop
    else:
        hit_t = paths <= target
        hit_s = paths >= stop

    first_t = np.where(hit_t.any(axis=1), hit_t.argmax(axis=1), H + 1)
    first_s = np.where(hit_s.any(axis=1), hit_s.argmax(axis=1), H + 1)
    tgt = first_t < first_s
    stp = first_s < first_t
    nei = ~tgt & ~stp

    r_t = (target / s0 - 1.0) * (1 if long else -1)
    r_s = (stop / s0 - 1.0) * (1 if long else -1)
    r_n = (paths[nei, -1] / s0 - 1.0) * (1 if long else -1) if nei.any() else np.array([0.0])

    p_t, p_s, p_n = tgt.mean(), stp.mean(), nei.mean()
    exp = float(p_t * r_t + p_s * r_s + p_n * float(np.mean(r_n)))
    return {"p_target": float(p_t), "p_stop": float(p_s),
            "p_neither": float(p_n), "expected": exp}


def build_trade(a, scenarios: Optional[List] = None,
                horizon_days: int = 63,
                stop_sigma: float = 2.0,
                risk_budget: float = 0.02) -> TradePlan:
    """
    stop_sigma  : 손절폭 = stop_sigma × 지평 변동성 (임의 % 가 아님)
    risk_budget : 이 트레이드 하나에 허용하는 계좌 대비 최대 손실
    """
    notes: List[str] = []
    s0 = float(a.prices[-1])
    v = a.verdict
    sz = float(v.risk_budget_weight or 0.0)

    sig_ann = a.vol_profile.garch.ann_vol_current
    if not np.isfinite(sig_ann):
        sig_ann = a.perf.get("vol_ann", 0.25)
    ann = a.classification.spec.ann_factor
    sig_h = sig_ann * math.sqrt(horizon_days / ann)

    # 방향
    p = v.direction_prob
    if v.grade in ("ABSTAIN", "NO_TRADE") or sz <= 0:
        direction = "NONE"
    elif p is not None and np.isfinite(p) and p < 0.45:
        direction = "SHORT"
    else:
        direction = "LONG"
    long = direction != "SHORT"

    # 손절: 변동성 기반. 유동성 비용도 반영
    liq = a.liquidity
    sp = float(liq.spread_used) if np.isfinite(liq.spread_used) else 0.0005
    part = min(sz * 1e7 / max(liq.adv_usd, 1.0), 0.5) if np.isfinite(liq.adv_usd) else 0.01
    rt = roundtrip_cost(sp, part, sig_ann / math.sqrt(ann))

    stop_pct = stop_sigma * sig_h + rt
    stop = s0 * (1 - stop_pct) if long else s0 * (1 + stop_pct)

    # 목표: 기본/강세 시나리오에서 도출
    tgt_pct, tgt_basis = np.nan, ""
    if scenarios:
        bull = next((s for s in scenarios if s.kind == "BULL"), None)
        base = next((s for s in scenarios if s.kind == "BASE"), None)
        if long and bull is not None:
            tgt_pct, tgt_basis = bull.ret_target, f"강세 시나리오 ({bull.driver_desc})"
        elif (not long):
            bear = next((s for s in scenarios if s.kind == "BEAR"), None)
            if bear is not None:
                tgt_pct = -bear.ret_target
                tgt_basis = f"약세 시나리오 ({bear.driver_desc})"
    if not np.isfinite(tgt_pct) or tgt_pct <= 0:
        tgt_pct = 1.5 * stop_sigma * sig_h
        tgt_basis = f"{1.5*stop_sigma:.1f}σ 지평 변동성 (시나리오 미가용)"
        notes.append("시나리오에서 목표를 도출하지 못해 변동성 배수로 대체")
    target = s0 * (1 + tgt_pct) if long else s0 * (1 - tgt_pct)

    # 배리어 확률
    paths = getattr(a.sim, "paths_sample", None)
    bp = barrier_probabilities(paths, s0, target, stop, long) if paths is not None \
        else {"p_target": np.nan, "p_stop": np.nan, "p_neither": np.nan,
              "expected": np.nan}
    if paths is None:
        notes.append("시뮬 경로 미저장 → 배리어 확률 산출 불가")

    rr = float(tgt_pct / stop_pct) if stop_pct > 0 else np.nan
    exp_net = (bp["expected"] - rt) if np.isfinite(bp["expected"]) else np.nan
    be = float(stop_pct / (stop_pct + tgt_pct)) if (stop_pct + tgt_pct) > 0 else np.nan
    edge = (bp["p_target"] - be) if np.isfinite(bp["p_target"]) and np.isfinite(be) \
        else np.nan

    # 리스크 예산 재확인: 사이즈 × 손절폭 ≤ risk_budget
    risk_unit = sz * stop_pct
    if risk_unit > risk_budget and stop_pct > 0:
        capped = risk_budget / stop_pct
        notes.append(f"손절폭 {stop_pct:.1%} × 사이즈 {sz:.1%} = 계좌손실 "
                     f"{risk_unit:.2%} > 예산 {risk_budget:.1%} → "
                     f"사이즈를 {capped:.1%}로 축소해야 함")
        sz_eff = capped
    else:
        sz_eff = sz

    if direction == "NONE":
        verdict = "진입 불가"
        notes.append("판정 엔진이 사이즈 0을 냈으므로 트레이드 계획은 참고용")
    elif not np.isfinite(edge):
        verdict = "판정 보류 — 배리어 확률 미산출"
    elif not np.isfinite(exp_net) or exp_net <= 0:
        verdict = "기대값 음수 — 진입 부적합"
        notes.append("배리어 확률로 계산한 비용 후 기대손익이 0 이하")
    elif edge >= 0.05 and exp_net >= 0.02:
        verdict = "진입 조건 충족"
    elif edge > 0.0:
        verdict = "한계적 — 분할 진입만"
        if np.isfinite(rr) and rr < 1.0:
            notes.append(f"R:R {rr:.2f} < 1 — 승률({bp['p_target']:.0%})이 "
                         f"손익분기({be:.0%})를 넘어야만 성립하는 구조. "
                         f"승률 가정이 조금만 틀려도 기대값이 뒤집힌다.")
    else:
        verdict = "엣지 없음 — 손익분기 승률 미달"

    return TradePlan(
        direction=direction, entry=s0,
        entry_note="종가 기준 참고가. 실제 체결은 다음 거래일 시가/VWAP 가정",
        stop=stop, stop_pct=stop_pct,
        stop_basis=f"{stop_sigma:.1f}σ × {horizon_days}일 변동성 "
                   f"({sig_h:.1%}) + 왕복비용 {rt:.2%}",
        target=target, target_pct=tgt_pct, target_basis=tgt_basis,
        horizon_days=horizon_days, rr_ratio=rr,
        p_target_first=bp["p_target"], p_stop_first=bp["p_stop"],
        p_neither=bp["p_neither"],
        expected_pnl=bp["expected"], expected_pnl_net=exp_net,
        roundtrip_cost=rt, breakeven_hit_rate=be, edge_vs_breakeven=edge,
        size_weight=sz_eff, risk_per_unit=sz_eff * stop_pct,
        max_loss_pct=sz_eff * stop_pct,
        verdict=verdict, notes=notes)


# ================================================================ 헤지

@dataclass
class HedgeLeg:
    factor: str
    instrument: str
    hedge_ratio: float             # 자산 1단위당 헤지 명목
    beta: float
    stability_cv: float
    contribution_removed: float    # 제거되는 분산 비중
    reliable: bool


@dataclass
class HedgePlan:
    legs: List[HedgeLeg]
    gross_hedge_notional: float
    var_removed: float             # 제거되는 총 분산 비중 (= 팩터 R²)
    residual_vol_ann: float
    unhedged_vol_ann: float
    hedge_cost_ann: float
    net_exposure: str
    reliable: bool
    verdict: str
    notes: List[str] = field(default_factory=list)


HEDGE_INSTRUMENTS = {
    "mkt_excess": "SPY / ES 선물",
    "smb": "IWM/SPY 스프레드",
    "hml": "IWD/IWF 스프레드",
    "rmw": "QUAL",
    "cma": "SPY",
    "umd": "MTUM",
    "real_yield_10y": "TIP / 10y TIPS 선물",
    "nominal_10y": "TLT / ZN 선물",
    "nominal_2y": "SHY / ZT 선물",
    "curve_2s10s": "ZN-ZT 커브 스프레드",
    "breakeven_10y": "TIP-IEF 스프레드",
    "broad_dollar": "UUP / DX 선물",
    "hy_oas": "HYG / CDX HY",
    "ig_oas": "LQD / CDX IG",
    "vix": "VIX 선물 / VXX",
    "wti": "USO / CL 선물",
    "crypto_mkt": "BTC 선물",
    "gpr": "헤지 불가 (거래 가능한 상품 없음)",
}


def build_hedge(a, min_abs_beta: float = 0.03,
                cost_per_leg_bps: float = 8.0,
                dedup_corr: float = 0.92) -> HedgePlan:
    """
    최소분산 헤지 = 다변량 팩터 회귀 계수.
    따라서 팩터 모델이 미스매칭이면 헤지도 무효다.
    """
    notes: List[str] = []
    fm = a.factor_model
    dp = a.delta_panel
    vol = a.perf.get("vol_ann", np.nan)

    if fm is None or not len(fm.coefs):
        return HedgePlan([], 0.0, np.nan, vol, vol, np.nan, "헤지 불가",
                         False, "팩터 모델 없음 → 헤지 설계 불가",
                         ["팩터 회귀가 없으면 최소분산 헤지비율을 정의할 수 없다"])

    cv_map = dict(zip(dp["factor"], dp["beta_stability_cv"])) if len(dp) else {}
    F = getattr(a, "factor_panel", None)
    legs: List[HedgeLeg] = []
    chosen: List[str] = []
    for f, b in sorted(fm.coefs.items(), key=lambda kv: -abs(kv[1])):
        if abs(b) < min_abs_beta:
            continue
        # 서로 거의 같은 팩터를 두 레그로 넣으면 헤지를 이중 집행하게 된다
        if F is not None and f in F.columns:
            dup = False
            for g in chosen:
                if g in F.columns:
                    v = F[[f, g]].dropna()
                    if len(v) > 100 and abs(float(v.corr().iloc[0, 1])) > dedup_corr:
                        dup = True
                        notes.append(f"{f} 는 {g} 와 상관 "
                                     f"{float(v.corr().iloc[0,1]):.2f} — "
                                     f"헤지 레그 중복이므로 제외")
                        break
            if dup:
                continue
        chosen.append(f)
        cv = float(cv_map.get(f, np.nan))
        inst = HEDGE_INSTRUMENTS.get(f, f)
        reliable = bool(np.isfinite(cv) and cv <= 0.80) and "불가" not in inst
        legs.append(HedgeLeg(
            factor=f, instrument=inst, hedge_ratio=-float(b), beta=float(b),
            stability_cv=cv, contribution_removed=np.nan, reliable=reliable))

    gross = float(sum(abs(l.hedge_ratio) for l in legs))
    var_removed = float(fm.r2) if np.isfinite(fm.r2) else np.nan
    resid_vol = (vol * math.sqrt(max(1 - var_removed, 0.0))
                 if np.isfinite(vol) and np.isfinite(var_removed) else np.nan)
    cost = gross * cost_per_leg_bps / 1e4 * 4  # 분기 리밸런싱 가정

    unreliable = [l for l in legs if not l.reliable]
    if fm.mismatch:
        verdict = "헤지 무효 — 팩터 미스매칭"
        notes.append("팩터 R²가 자산군 기대밴드를 밑돎. 최소분산 헤지비율은 "
                     "이 회귀 계수와 동일하므로 헤지도 함께 무효다.")
        reliable = False
    elif not legs:
        verdict = "헤지 불필요 — 유의한 팩터 노출 없음"
        reliable = True
    elif unreliable:
        verdict = f"부분 헤지만 가능 ({len(legs)-len(unreliable)}/{len(legs)} 레그)"
        notes.append("β 변동계수 0.8 초과 레그: " +
                     ", ".join(l.factor for l in unreliable) +
                     " — 헤지비율이 불안정해 오히려 위험을 추가할 수 있음")
        reliable = False
    else:
        verdict = "헤지 실행 가능"
        reliable = True

    if np.isfinite(var_removed) and var_removed < 0.25:
        notes.append(f"헤지로 제거 가능한 분산이 {var_removed:.0%}에 불과. "
                     f"위험의 대부분이 고유 요인이므로 헤지보다 "
                     f"**사이즈 축소**가 유효한 통제 수단이다.")

    net = ("팩터 중립 (고유위험만 노출)" if reliable and legs
           else "총노출 유지" if not legs else "부분 중립")
    return HedgePlan(legs, gross, var_removed, resid_vol, vol, cost,
                     net, reliable, verdict, notes)


# ================================================================ 수익 귀인

def return_attribution(r_asset: np.ndarray, F: pd.DataFrame,
                       coefs: Dict[str, float], windows=(21, 63, 252)
                       ) -> pd.DataFrame:
    """
    최근 수익을 팩터 기여 + 알파로 분해한다.
    "최근 성과가 종목 고유인가, 아니면 그냥 베타였나"에 답한다.
    """
    y = np.asarray(r_asset, float)
    rows = []
    for w in windows:
        if len(y) < w + 1:
            continue
        seg_y = y[-w:]
        total = float(np.nansum(seg_y))
        contrib: Dict[str, float] = {}
        expl = 0.0
        for f, b in coefs.items():
            if f not in F.columns:
                continue
            xf = F[f].values[-w:]
            c = float(b * np.nansum(xf))
            if np.isfinite(c):
                contrib[f] = c
                expl += c
        row = {"기간": f"{w}일", "총수익": total,
               "팩터 기여": expl, "알파(잔차)": total - expl,
               "알파 비중": (total - expl) / total if abs(total) > 1e-9 else np.nan}
        row.update({f"β·{k}": v for k, v in contrib.items()})
        rows.append(row)
    return pd.DataFrame(rows)
