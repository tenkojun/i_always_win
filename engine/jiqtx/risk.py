# ==============================================================================
# [12/25] risk.py — VaR/ES · 커버리지 검정 · 스트레스 · 낙폭제약 켈리
# ==============================================================================

"""
jiqtx.risk — 리스크 측정·스트레스·포지션 사이징.

원본 리포트의 결함
------------------
1. VaR을 "최대손실"로 표기 (실제로는 하위 5% 경계값)
2. 스트레스 = 주식베타 0.20 × 지수충격 (금에 주식 베타를 곱하는 것은 무의미)
3. half-Kelly 200% (추정오차 미반영 → 파산 확률)

본 모듈
-------
- VaR/ES: 정규 / 히스토리컬 / FHS-EVT 3종 비교 + 커버리지 백테스트
  (Kupiec UC, Christoffersen IND, 조건부 커버리지)
- 드로다운: MDD, CDaR, Ulcer, 최장 수중기간
- 스트레스: 자산군별 리스크팩터 충격 × 시변 베타 (하방 베타 사용)
- 사이징: 드리프트 사후분포를 적분한 켈리 → 추정오차가 크면 자동 축소
"""

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from scipy import stats

# ── 패키지 내부 의존 ──────────────────────────────────────────
from .statcore import christoffersen_ind, conditional_coverage, kupiec_pof




# ---------------------------------------------------------------- VaR / ES

@dataclass
class VaRResult:
    alpha: float
    var_normal: float
    var_historical: float
    var_fhs_evt: float
    es_historical: float
    es_fhs_evt: float
    backtest: Dict[str, Dict[str, float]]
    preferred: str
    note: str


def _gpd_var_es(z: np.ndarray, alpha: float, sigma_now: float,
                mu: float = 0.0, q_thresh: float = 0.05) -> Tuple[float, float]:
    """조건부 EVT (McNeil-Frey 2000): 표준화 잔차 꼬리에 GPD → 조건부 VaR/ES."""
    z = z[np.isfinite(z)]
    if len(z) < 300:
        return np.nan, np.nan
    u = float(np.quantile(z, q_thresh))
    ex = u - z[z < u]
    if len(ex) < 30:
        return np.nan, np.nan
    try:
        xi, _, beta = stats.genpareto.fit(ex, floc=0)
    except Exception:
        return np.nan, np.nan
    nu_ = len(ex) / len(z)
    if alpha >= nu_:
        zq = float(np.quantile(z, alpha))
        tail = z[z <= zq]
        return float(-(mu + sigma_now * zq)), float(-(mu + sigma_now * tail.mean()))
    # ── 좌측 꼬리 ES ────────────────────────────────────────
    # McNeil-Frey 의 GPD ES 는 **우측** 꼬리 공식이다. 여기서는 손실
    # 쪽(좌측)을 보므로 Y = -z 로 뒤집어 유도한 뒤 되돌려야 한다.
    #
    #   우측:  ES_Y = VaR_Y/(1-ξ) + (β - ξ·u_Y)/(1-ξ)
    #   되돌림(u_Y = -u, zq = -VaR_Y):
    #          es_z = zq/(1-ξ) - (β + ξ·u)/(1-ξ)
    #
    # 전에는 두 번째 항의 부호와 u 의 부호가 모두 뒤집힌 채로 더해져서
    # **ES 가 VaR 보다 작게** 나왔다. 정의상 불가능한 값이다(ES 는 VaR
    # 너머의 평균이므로 항상 더 크다). 몬테카를로 대조에서 t(4) 기준
    # 진짜 ES 2.27 을 0.73 으로 냈다 — 꼬리 손실을 3분의 1로 과소평가한
    # 셈이고, 리스크 시스템에서 가장 위험한 방향의 오류다.
    if abs(xi) < 1e-8:
        zq = u - beta * math.log(alpha / nu_)
        es_z = zq - beta
    else:
        zq = u - (beta / xi) * ((alpha / nu_) ** (-xi) - 1.0)
        if xi >= 1:
            # ξ≥1 이면 평균이 존재하지 않는다. 없는 값을 지어내지 않는다.
            return float(-(mu + sigma_now * zq)), float("nan")
        es_z = zq / (1 - xi) - (beta + xi * u) / (1 - xi)
    return float(-(mu + sigma_now * zq)), float(-(mu + sigma_now * es_z))


def var_es(returns: np.ndarray, z_resid: np.ndarray, sigma_now: float,
           alpha: float = 0.05, mu: float = 0.0) -> VaRResult:
    r = np.asarray(returns, float)
    r = r[np.isfinite(r)]
    sd = float(r.std(ddof=1))
    v_norm = float(-(mu + sd * stats.norm.ppf(alpha)))
    v_hist = float(-np.quantile(r, alpha))
    tail = r[r <= np.quantile(r, alpha)]
    es_hist = float(-tail.mean()) if len(tail) else np.nan
    v_evt, es_evt = _gpd_var_es(np.asarray(z_resid, float), alpha, sigma_now, mu)

    bt: Dict[str, Dict[str, float]] = {}
    for name, v in (("normal", v_norm), ("historical", v_hist), ("fhs_evt", v_evt)):
        if not np.isfinite(v):
            continue
        viol = (r < -v).astype(int)
        bt[name] = {
            "hit_rate": float(viol.mean()),
            "kupiec_p": kupiec_pof(viol, alpha)["pvalue"],
            "independence_p": christoffersen_ind(viol)["pvalue"],
            "cc_p": conditional_coverage(viol, alpha)["pvalue"],
        }

    # 선호 모델: 조건부 커버리지 p값이 가장 높고 0.05 이상인 것
    ok = {k: v for k, v in bt.items() if np.isfinite(v.get("cc_p", np.nan))
          and v["cc_p"] > 0.05}
    preferred = max(ok, key=lambda k: ok[k]["cc_p"]) if ok else "historical"
    note = ("VaR은 최대손실이 아니라 하위 %.0f%% 경계값이다. "
            "ES(조건부 기대손실)를 함께 보라." % (alpha * 100))
    if not ok:
        note += " ⚠ 모든 VaR 모델이 커버리지 검정을 통과하지 못함."
    return VaRResult(alpha, v_norm, v_hist, v_evt, es_hist, es_evt,
                     bt, preferred, note)


# ---------------------------------------------------------------- 드로다운

@dataclass
class DrawdownProfile:
    max_drawdown: float
    current_drawdown: float
    cdar_95: float
    ulcer_index: float
    longest_underwater_days: int
    current_underwater_days: int
    recovery_note: str


def drawdown_profile(prices: np.ndarray, alpha: float = 0.05) -> DrawdownProfile:
    p = np.asarray(prices, float)
    p = p[np.isfinite(p)]
    if len(p) < 30:
        return DrawdownProfile(*([np.nan] * 4), 0, 0, "표본 부족")
    rm = np.maximum.accumulate(p)
    dd = p / rm - 1.0
    mdd = float(dd.min())
    cur = float(dd[-1])
    # CDaR: 최악 alpha 구간 낙폭의 평균
    thr = np.quantile(dd, alpha)
    cdar = float(dd[dd <= thr].mean())
    ulcer = float(np.sqrt(np.mean((dd * 100) ** 2)))

    under = dd < -1e-12
    longest = cur_run = 0
    for u in under:
        cur_run = cur_run + 1 if u else 0
        longest = max(longest, cur_run)
    cur_uw = 0
    for u in under[::-1]:
        if u:
            cur_uw += 1
        else:
            break
    note = (f"최장 수중기간 {longest}영업일(약 {longest/252:.1f}년). "
            "장기 보유 시 회복 지연 위험." if longest > 250 else "회복 패턴 정상 범위")
    return DrawdownProfile(mdd, cur, cdar, ulcer, int(longest), int(cur_uw), note)


# ---------------------------------------------------------------- 스트레스

@dataclass
class StressScenario:
    name: str
    shocks: Dict[str, float]
    pnl_static: float
    pnl_downside: float
    within_limit: bool


def stress_test(delta_panel: pd.DataFrame, spec_shocks: Dict[str, float],
                limit: float = 0.35,
                extra_scenarios: Optional[Dict[str, Dict[str, float]]] = None,
                partial_betas: Optional[Dict[str, float]] = None
                ) -> Tuple[pd.DataFrame, Dict[str, float]]:
    """
    자산군 고유 리스크팩터 충격으로 스트레스를 구성한다.
    주식 베타 곱셈 방식이 아니다.

    단일 팩터 vs 복합 시나리오 — 베타를 다르게 써야 한다
    ------------------------------------------------------
    델타 패널의 베타는 **단변량**이다. 즉 "HY OAS 가 벌어질 때 이 종목이
    평균적으로 얼마나 빠지는가" 인데, 여기에는 그때 같이 빠지는 주식시장
    효과가 이미 들어 있다.

    · 단일 팩터 시나리오("HY 만 +300bp") → 단변량이 맞다.
      그 충격에 딸려 오는 동반 움직임까지 포함하는 게 총효과다.
    · 복합 시나리오("주식 −20% AND VIX +20 AND HY +300bp")
      → 단변량을 그냥 더하면 **같은 시장 충격을 3~4번 센다.**
      실제로 애플이 시장 −20% 에 −63.7% 빠지는(실효 베타 3배) 값이
      나왔고, 그 결과 모든 종목이 SIZE_ZERO 거부권에 걸렸다.
      복합 시나리오에는 "다른 팩터를 고정했을 때의 순효과",
      즉 다변량 회귀의 **부분 베타**를 써야 한다.

    delta_panel   : factors.factor_delta_panel 출력 (단변량)
    partial_betas : factor_model.coefs (다변량 부분 베타). 없으면 단변량으로
                    떨어지되 그 사실을 요약에 남긴다.
    반환          : (시나리오 표, 요약)
    """
    if delta_panel is None or len(delta_panel) == 0:
        return pd.DataFrame(), {"worst_pnl": np.nan, "within_limit": False,
                                "note": "델타 패널 없음 → 스트레스 산출 불가"}

    beta_now = dict(zip(delta_panel["factor"], delta_panel["beta_now"]))
    beta_dn = dict(zip(delta_panel["factor"], delta_panel["downside_beta"]))
    beta_st = dict(zip(delta_panel["factor"], delta_panel["beta_static"]))

    pb = {k: float(v) for k, v in (partial_betas or {}).items()
          if v is not None and np.isfinite(v)}

    def _b(f, downside=False):
        """단변량 베타 — 단일 팩터 시나리오용 (총효과)."""
        if downside:
            v = beta_dn.get(f)
            if v is not None and np.isfinite(v):
                return v
        v = beta_now.get(f)
        if v is not None and np.isfinite(v):
            return v
        v = beta_st.get(f)
        return v if v is not None and np.isfinite(v) else 0.0

    def _bp(f, downside=False):
        """
        부분 베타 — 복합 시나리오용 (다른 팩터 고정 시 순효과).

        다변량 회귀에 없는 팩터는 단변량으로 떨어진다. 하방 부분베타는
        따로 추정하지 않으므로, 같은 팩터의 (단변량 하방 ÷ 단변량) 비율을
        부분 베타에 곱해 비대칭만 옮겨 온다. 비율이 이상하면 쓰지 않는다.
        """
        base = pb.get(f)
        if base is None:
            return _b(f, downside)
        if not downside:
            return base
        u, d = beta_now.get(f), beta_dn.get(f)
        if (u is not None and d is not None and np.isfinite(u)
                and np.isfinite(d) and abs(u) > 1e-9):
            ratio = d / u
            if 0.2 <= ratio <= 5.0:          # 터무니없는 증폭은 무시
                return base * ratio
        return base

    scenarios: Dict[str, Dict[str, float]] = {"자산군 표준 충격": dict(spec_shocks)}
    # 단일팩터 시나리오
    for f, s in spec_shocks.items():
        scenarios[f"단일: {f}"] = {f: s}
    # 복합 시나리오
    scenarios["복합: 긴축+강달러"] = {k: v for k, v in
                                 {"real_yield_10y": 1.0, "nominal_10y": 1.0,
                                  "broad_dollar": 0.05}.items()
                                 if k in delta_panel["factor"].values}
    scenarios["복합: 리스크오프"] = {k: v for k, v in
                               {"mkt_excess": -0.20, "vix": 20.0,
                                "hy_oas": 3.0, "crypto_mkt": -0.40}.items()
                               if k in delta_panel["factor"].values}
    if extra_scenarios:
        scenarios.update(extra_scenarios)

    rows = []
    for name, sh in scenarios.items():
        if not sh:
            continue
        # 팩터가 하나면 총효과(단변량), 여럿이면 순효과(부분 베타).
        joint = len(sh) > 1
        bfun = _bp if joint else _b
        p_static = float(sum(bfun(f) * v for f, v in sh.items()))
        p_down = float(sum(bfun(f, True) * v for f, v in sh.items()))
        p_cons = min(p_static, p_down)            # 보수적 채택
        rows.append({"scenario": name,
                     "shocks": "; ".join(f"{k}{v:+g}" for k, v in sh.items()),
                     "beta_basis": "부분(다변량)" if (joint and pb)
                                   else "단변량",
                     "pnl_static": p_static, "pnl_downside": p_down,
                     "pnl_conservative": p_cons,
                     "within_limit": bool(abs(p_cons) <= limit)})
    df = pd.DataFrame(rows).sort_values("pnl_conservative")
    worst = float(df["pnl_conservative"].min()) if len(df) else np.nan
    summary = {
        "worst_pnl": worst,
        "worst_scenario": df.iloc[0]["scenario"] if len(df) else "",
        "within_limit": bool(np.isfinite(worst) and abs(worst) <= limit),
        "limit": limit,
        "beta_basis": "부분(다변량)" if pb else "단변량(부분베타 없음)",
        "note": ("스트레스 손익은 선형 델타 근사이며 역사적 재현값도 손실 상한도 "
                 "아니다. 비선형 반응(유동성 확보 매도 후 안전자산 반등 등)은 "
                 "포함되지 않는다. 복합 시나리오는 부분(다변량) 베타로 계산한다 — "
                 "단변량 베타를 더하면 같은 시장 충격을 여러 번 세게 된다."),
    }
    return df, summary


# ---------------------------------------------------------------- 사이징

@dataclass
class SizingResult:
    kelly_naive: float
    kelly_uncertainty_adjusted: float
    half_kelly: float
    vol_target_weight: float
    stress_cap: float
    liquidity_cap: float
    class_cap: float
    final_weight: float
    binding_constraint: str
    note: str


def kelly_with_drawdown_constraint(
        mu_post_ann: float, se_post_ann: float, sigma_ann: float,
        z_resid: Optional[np.ndarray] = None, ann: int = 252,
        dd_limit: float = 0.25, dd_confidence: float = 0.95,
        n_paths: int = 4000, seed: int = 0) -> Dict[str, float]:
    """
    켈리 비중을 3가지로 산출한다.

    f_naive   : μ/σ²  — 드리프트를 '안다'고 가정. 원본 리포트의 200%가 여기서 나온다.
    f_growth  : 예측분포(드리프트 불확실성 + 조건부변동성 + 팻테일)에서
                일간 리밸런싱 경로를 시뮬레이션해 기대 로그성장을 최대화.
    f_dd      : 위 목적함수에 '낙폭 제약'을 추가.
                P(MDD > dd_limit) ≤ 1 − dd_confidence 를 만족하는 최대 f.
                실제 기관이 쓰는 값은 사실상 항상 이것이다.

    성장 최적 켈리는 수학적으로 옳아도 운용 불가능한 낙폭을 동반한다.
    낙폭 제약이 없으면 어떤 켈리 공식도 실무 권고로 쓸 수 없다.
    """
    out = {"f_naive": np.nan, "f_growth": np.nan, "f_dd": np.nan,
           "mdd_at_growth": np.nan, "mdd_at_dd": np.nan,
           "ruin_prob_growth": np.nan, "dd_limit": dd_limit}
    if not all(np.isfinite([mu_post_ann, se_post_ann, sigma_ann])) or sigma_ann <= 0:
        return out
    out["f_naive"] = float(mu_post_ann / sigma_ann ** 2)

    rng = np.random.default_rng(seed)
    H = ann
    mu = rng.normal(mu_post_ann, max(se_post_ann, 1e-9), n_paths) / ann

    if z_resid is not None and np.isfinite(np.asarray(z_resid, float)).sum() > 300:
        z = np.asarray(z_resid, float)
        z = z[np.isfinite(z)]
        z = z / z.std(ddof=1)
        Z = rng.choice(z, size=(n_paths, H), replace=True)
    else:
        Z = rng.standard_normal((n_paths, H))

    sd_d = sigma_ann / math.sqrt(ann)
    simple = np.expm1(mu[:, None] - 0.5 * sd_d ** 2 + sd_d * Z)   # 일간 단순수익률

    f_grid = np.round(np.arange(0.0, 3.01, 0.05), 2)
    rows = []
    for f in f_grid:
        lev = f * simple
        if np.mean(np.min(lev, axis=1) <= -0.95) > 0.002:
            continue
        w = np.cumprod(1.0 + np.clip(lev, -0.95, None), axis=1)
        term = w[:, -1]
        if np.mean(term <= 1e-4) > 0.002:
            continue
        g = float(np.mean(np.log(np.maximum(term, 1e-6))))
        rmax = np.maximum.accumulate(w, axis=1)
        mdd = (w / rmax - 1.0).min(axis=1)
        mdd_q = float(-np.quantile(mdd, 1.0 - dd_confidence))
        ruin = float(np.mean(term <= 0.2))
        rows.append((float(f), g, mdd_q, ruin))

    if not rows:
        return out
    arr = np.array(rows)
    ig = int(np.argmax(arr[:, 1]))
    out["f_growth"] = float(arr[ig, 0])
    out["mdd_at_growth"] = float(arr[ig, 2])
    out["ruin_prob_growth"] = float(arr[ig, 3])

    feasible = arr[arr[:, 2] <= dd_limit]
    if len(feasible):
        k = int(np.argmax(feasible[:, 1]))
        out["f_dd"] = float(feasible[k, 0])
        out["mdd_at_dd"] = float(feasible[k, 2])
    else:
        out["f_dd"] = 0.0
        out["mdd_at_dd"] = float(arr[0, 2])
    return out


def uncertainty_aware_kelly(mu_post_ann: float, se_post_ann: float,
                            sigma_ann: float, z_resid: Optional[np.ndarray] = None,
                            ann: int = 252, dd_limit: float = 0.25,
                            seed: int = 0, n_paths: int = 4000
                            ) -> Dict[str, float]:
    """kelly_with_drawdown_constraint 의 얇은 래퍼 (하위호환)."""
    k = kelly_with_drawdown_constraint(mu_post_ann, se_post_ann, sigma_ann,
                                       z_resid=z_resid, ann=ann,
                                       dd_limit=dd_limit, seed=seed,
                                       n_paths=n_paths)
    return {"f_naive": k["f_naive"], "f_star": k["f_dd"],
            "half_kelly": k["f_dd"] / 2.0 if np.isfinite(k["f_dd"]) else np.nan,
            "ruin_prob": k["ruin_prob_growth"],
            "growth_at_star": np.nan, "f_growth": k["f_growth"],
            "mdd_at_growth": k["mdd_at_growth"], "mdd_at_dd": k["mdd_at_dd"]}


def position_size(mu_post_ann: float, se_post_ann: float, sigma_ann: float,
                  vol_target: float, class_cap: float,
                  stress_worst: float, stress_budget: float,
                  adv_usd: float, aum_usd: float,
                  max_participation: float = 0.05,
                  kelly_cap: float = 0.25,
                  z_resid: Optional[np.ndarray] = None,
                  ann: int = 252, kelly_paths: int = 4000) -> SizingResult:
    """모든 제약의 최솟값. 어떤 제약이 구속했는지 명시한다."""
    kk = uncertainty_aware_kelly(mu_post_ann, se_post_ann, sigma_ann,
                                 z_resid=z_resid, ann=ann,
                                 n_paths=kelly_paths)
    k_naive, k_adj, half_k = kk["f_naive"], kk["f_star"], kk["half_kelly"]
    w_vol = float(vol_target / sigma_ann) if sigma_ann > 0 else np.nan
    w_stress = float(stress_budget / abs(stress_worst)) \
        if np.isfinite(stress_worst) and abs(stress_worst) > 1e-9 else np.inf
    w_liq = float(max_participation * adv_usd * 5.0 / aum_usd) \
        if np.isfinite(adv_usd) and aum_usd > 0 else np.inf

    cands = {
        "낙폭제약 켈리": k_adj if np.isfinite(k_adj) else np.inf,
        "켈리 상한": kelly_cap,
        "변동성 타깃": w_vol if np.isfinite(w_vol) else np.inf,
        "스트레스 예산": w_stress,
        "유동성 한도": w_liq,
        "자산군 상한": class_cap,
    }
    binding = min(cands, key=lambda k: cands[k])
    final = float(max(min(cands.values()), 0.0))

    note = (f"단순 켈리 μ/σ² = {k_naive:.0%}  →  성장최적(불확실성·팻테일 반영) "
            f"{kk.get('f_growth', float('nan')):.0%} (그때 95% MDD "
            f"{kk.get('mdd_at_growth', float('nan')):.0%})  →  낙폭제약 켈리 "
            f"{k_adj:.0%} (95% MDD {kk.get('mdd_at_dd', float('nan')):.0%}). "
            f"원본 리포트의 '하프켈리 200%'는 첫 번째 값을 쓴 결과다.")
    return SizingResult(k_naive, k_adj, half_k, w_vol, w_stress, w_liq,
                        class_cap, final, binding, note)


# ---------------------------------------------------------------- 성과 요약

def performance_summary(r: np.ndarray, prices: np.ndarray, ann: int = 252,
                        rf_ann: float = 0.04) -> Dict[str, float]:
    r = np.asarray(r, float)
    r = r[np.isfinite(r)]
    n = len(r)
    if n < 30:
        return {}
    rf_d = rf_ann / ann
    ex = r - rf_d
    sd = float(ex.std(ddof=1))
    downside = ex[ex < 0]
    dsd = float(downside.std(ddof=1)) if len(downside) > 5 else np.nan
    cagr = float(np.exp(r.mean() * ann) - 1)
    dd = drawdown_profile(prices)
    return {
        "n_obs": n,
        "cagr": cagr,
        "vol_ann": float(r.std(ddof=1) * math.sqrt(ann)),
        "sharpe": float(ex.mean() / sd * math.sqrt(ann)) if sd > 0 else np.nan,
        "sortino": float(ex.mean() / dsd * math.sqrt(ann)) if dsd and dsd > 0 else np.nan,
        "calmar": float(cagr / abs(dd.max_drawdown)) if dd.max_drawdown else np.nan,
        "skew": float(pd.Series(r).skew()),
        "kurtosis": float(pd.Series(r).kurtosis() + 3),
        "max_drawdown": dd.max_drawdown,
        "cdar_95": dd.cdar_95,
        "ulcer": dd.ulcer_index,
        "longest_underwater_days": dd.longest_underwater_days,
        "hit_rate": float(np.mean(r > 0)),
    }
