# ==============================================================================
# [19/25] portfolio.py — 위험기여 · 팩터 넷팅 · 배분 경합(WF+MCS)
# ==============================================================================

"""
jiqtx.portfolio — 포트폴리오 계층.

왜 필요한가
-----------
해지펀드는 종목 하나를 따로 보지 않는다. 실제로 PM이 묻는 것은:
  · 이 포지션이 **책 전체 위험에 얼마를 더하는가** (한계기여위험)
  · 헤지가 **다른 포지션과 상쇄되는가** (팩터 넷팅)
  · 분산이 실제로 되고 있는가 (유효 베팅 수, 분산비율)
  · 어떤 배분 규칙이 실제로 나은가 — **워크포워드로 검정했는가**

설계서 §3.8에서 약속한 "HRP vs 1/N vs 최소분산 vs 리스크패리티를 MCS로 경합"을
여기서 실행한다. HRP가 항상 1/N을 이기지 않는다는 실증이 있으므로,
이기지 못하면 1/N을 쓴다.
"""

import math
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from scipy.cluster.hierarchy import linkage, leaves_list
from scipy.spatial.distance import squareform


try:
    from sklearn.covariance import LedoitWolf
    _HAS_LW = True
except Exception:                                    # pragma: no cover
    _HAS_LW = False

# ── 패키지 내부 의존 ──────────────────────────────────────────
from .statcore import model_confidence_set, sharpe_ratio



# ================================================================ 공분산

def cov_estimate(R: np.ndarray, method: str = "lw") -> np.ndarray:
    """
    표본 공분산은 자산 수가 관측 수에 가까워질수록 심하게 왜곡된다.
    Ledoit-Wolf 축소를 기본으로 쓴다.
    """
    R = np.asarray(R, float)
    R = R[np.isfinite(R).all(axis=1)]
    if len(R) < 30:
        return np.cov(R, rowvar=False, ddof=1) if len(R) > 2 else np.eye(R.shape[1])
    if method == "sample" or not _HAS_LW:
        return np.cov(R, rowvar=False, ddof=1)
    try:
        return LedoitWolf().fit(R).covariance_
    except Exception:
        return np.cov(R, rowvar=False, ddof=1)


def corr_from_cov(S: np.ndarray) -> np.ndarray:
    d = np.sqrt(np.clip(np.diag(S), 1e-16, None))
    C = S / np.outer(d, d)
    return np.clip(C, -1.0, 1.0)


# ================================================================ 배분 규칙

def w_equal(S: np.ndarray) -> np.ndarray:
    n = S.shape[0]
    return np.ones(n) / n


def w_minvar(S: np.ndarray, long_only: bool = True) -> np.ndarray:
    n = S.shape[0]
    inv = np.linalg.pinv(S + np.eye(n) * 1e-10)
    w = inv @ np.ones(n)
    w = w / w.sum() if abs(w.sum()) > 1e-12 else np.ones(n) / n
    if long_only:
        w = np.clip(w, 0, None)
        w = w / w.sum() if w.sum() > 0 else np.ones(n) / n
    return w


def w_invvol(S: np.ndarray) -> np.ndarray:
    v = np.sqrt(np.clip(np.diag(S), 1e-16, None))
    w = 1.0 / v
    return w / w.sum()


def w_riskparity(S: np.ndarray, iters: int = 500, tol: float = 1e-9) -> np.ndarray:
    """동등 위험기여 배분 (반복 해법)."""
    n = S.shape[0]
    w = np.ones(n) / n
    for _ in range(iters):
        mrc = S @ w
        rc = w * mrc
        target = rc.mean()
        grad = rc - target
        if np.max(np.abs(grad)) < tol:
            break
        w = w * (target / np.clip(rc, 1e-16, None)) ** 0.5
        w = np.clip(w, 1e-8, None)
        w = w / w.sum()
    return w


def _quasi_diag(Z: np.ndarray, n: int) -> List[int]:
    return list(leaves_list(Z))


def w_hrp(S: np.ndarray) -> np.ndarray:
    """López de Prado 계층적 위험 패리티."""
    n = S.shape[0]
    if n < 2:
        return np.ones(n)
    C = corr_from_cov(S)
    D = np.sqrt(np.clip((1.0 - C) / 2.0, 0, None))
    np.fill_diagonal(D, 0.0)
    try:
        Z = linkage(squareform(D, checks=False), method="single")
        order = _quasi_diag(Z, n)
    except Exception:
        order = list(range(n))

    w = np.ones(n)
    clusters = [order]
    while clusters:
        nxt = []
        for c in clusters:
            if len(c) <= 1:
                continue
            k = len(c) // 2
            a, b = c[:k], c[k:]

            def cvar(idx):
                Sub = S[np.ix_(idx, idx)]
                wi = w_invvol(Sub)
                return float(wi @ Sub @ wi)
            va, vb = cvar(a), cvar(b)
            alpha = 1.0 - va / (va + vb) if (va + vb) > 0 else 0.5
            for i in a:
                w[i] *= alpha
            for i in b:
                w[i] *= (1 - alpha)
            nxt += [a, b]
        clusters = nxt
    return w / w.sum()


ALLOCATORS: Dict[str, Any] = {
    "1/N (동일가중)": w_equal,
    "역변동성": w_invvol,
    "최소분산": w_minvar,
    "리스크 패리티": w_riskparity,
    "HRP (계층적)": w_hrp,
}


# ================================================================ 위험 분해

@dataclass
class RiskDecomp:
    tickers: List[str]
    weights: np.ndarray
    vol_ann: float
    marginal: np.ndarray           # ∂σ_p/∂w_i
    contribution: np.ndarray       # w_i × marginal  (합 = σ_p)
    pct_contribution: np.ndarray
    standalone_vol: np.ndarray
    diversification_ratio: float
    effective_bets: float
    max_pct_contribution: float
    concentration_flag: bool
    corr_matrix: pd.DataFrame
    avg_corr: float


def risk_decomposition(R: np.ndarray, weights: np.ndarray, tickers: List[str],
                       ann: int = 252) -> RiskDecomp:
    S = cov_estimate(R)
    w = np.asarray(weights, float)
    if w.sum() > 0:
        wn = w / w.sum()
    else:
        wn = np.ones(len(w)) / len(w)
    var_p = float(wn @ S @ wn)
    vol_p = math.sqrt(max(var_p, 1e-18))
    mrc = (S @ wn) / vol_p
    cr = wn * mrc
    pct = cr / vol_p if vol_p > 0 else np.full_like(cr, np.nan)
    sd = np.sqrt(np.clip(np.diag(S), 1e-18, None))
    dr = float((wn @ sd) / vol_p) if vol_p > 0 else np.nan
    p = np.clip(pct, 1e-12, None)
    p = p / p.sum()
    enb = float(math.exp(-float((p * np.log(p)).sum())))
    C = corr_from_cov(S)
    iu = np.triu_indices(len(C), 1)
    return RiskDecomp(
        tickers=tickers, weights=w, vol_ann=vol_p * math.sqrt(ann),
        marginal=mrc * math.sqrt(ann), contribution=cr * math.sqrt(ann),
        pct_contribution=pct, standalone_vol=sd * math.sqrt(ann),
        diversification_ratio=dr, effective_bets=enb,
        max_pct_contribution=float(np.nanmax(pct)),
        concentration_flag=bool(np.nanmax(pct) > 0.40),
        corr_matrix=pd.DataFrame(C, index=tickers, columns=tickers),
        avg_corr=float(np.mean(C[iu])) if len(C) > 1 else np.nan)


# ================================================================ 팩터 넷팅

@dataclass
class FactorNetting:
    table: pd.DataFrame
    gross_by_factor: pd.Series
    net_by_factor: pd.Series
    netting_ratio: pd.Series       # |net| / gross — 낮을수록 상쇄가 잘 됨
    dominant_factor: str
    dominant_net: float
    note: str


def factor_netting(analyses: List[Any], weights: np.ndarray) -> FactorNetting:
    """
    포지션별 팩터 베타를 비중가중 합산한다.
    개별로는 큰 노출이 책 전체에서는 상쇄될 수 있고, 그 반대도 가능하다.
    """
    rows = []
    for a, w in zip(analyses, weights):
        fm = a.factor_model
        if fm is None or not fm.coefs:
            continue
        for f, b in fm.coefs.items():
            rows.append({"ticker": a.ticker, "factor": f,
                         "beta": float(b), "weight": float(w),
                         "wbeta": float(w * b)})
    if not rows:
        return FactorNetting(pd.DataFrame(), pd.Series(dtype=float),
                             pd.Series(dtype=float), pd.Series(dtype=float),
                             "", np.nan, "팩터 모델이 있는 포지션이 없음")
    d = pd.DataFrame(rows)
    piv = d.pivot_table(index="factor", columns="ticker", values="wbeta",
                        aggfunc="sum").fillna(0.0)
    net = piv.sum(axis=1)
    gross = piv.abs().sum(axis=1)
    ratio = (net.abs() / gross.replace(0, np.nan))
    piv["순노출"] = net
    piv["총노출"] = gross
    piv["넷팅비율"] = ratio
    dom = net.abs().idxmax() if len(net) else ""
    # 유의한 노출만 넷팅 진단 대상 (β≈0 팩터의 비율은 잡음)
    mat = gross[gross > 0.02].index
    note = ""
    if len(mat):
        rm = ratio.loc[mat].dropna()
        if len(rm):
            best = rm.idxmin()
            if rm[best] < 0.35:
                note = (f"{best} 노출은 포지션 간 상쇄가 커서 순노출이 총노출의 "
                        f"{rm[best]:.0%}에 불과하다. 개별 종목마다 헤지를 걸면 "
                        f"이중 집행이 된다 — 책 레벨에서 한 번만 헤지하라.")
            elif float(rm.min()) > 0.90:
                note = ("모든 유의 팩터에서 순노출 ≈ 총노출이다. 즉 포지션들이 "
                        "같은 방향으로 같은 팩터에 노출돼 있고 **상쇄가 전혀 "
                        "없다**. 종목 수가 늘어도 이것은 분산이 아니라 "
                        "동일 베팅의 레버리지다. 진짜 분산을 원하면 "
                        "팩터 노출이 반대인 자산을 넣거나 책 레벨에서 헤지하라.")
    return FactorNetting(piv.sort_values("총노출", ascending=False),
                         gross, net, ratio, str(dom),
                         float(net[dom]) if dom else np.nan, note)


# ================================================================ 배분 경합

@dataclass
class AllocationResult:
    table: pd.DataFrame
    weights: Dict[str, np.ndarray]
    mcs_survivors: List[str]
    winner: str
    beats_1n: bool
    n_rebalances: int
    note: str


def allocation_competition(R: np.ndarray, tickers: List[str],
                           lookback: int = 252, step: int = 21,
                           cost_bps: float = 8.0, ann: int = 252,
                           alpha_mcs: float = 0.10,
                           seed: int = 0) -> AllocationResult:
    """
    **워크포워드** 배분 경합.
    각 리밸런싱 시점에서 과거 lookback 만으로 가중치를 만들고,
    다음 step 구간의 실현 수익으로 평가한다. 회전비용도 차감한다.
    그 뒤 Hansen MCS로 통계적으로 구별되지 않는 규칙 집합을 남긴다.

    HRP가 항상 1/N을 이기지는 않는다는 실증이 있으므로,
    MCS에서 1/N을 제외하지 못하면 1/N을 쓴다.
    """
    R = np.asarray(R, float)
    T, n = R.shape
    names = list(ALLOCATORS.keys())
    series: Dict[str, List[float]] = {k: [] for k in names}
    wprev: Dict[str, np.ndarray] = {k: np.zeros(n) for k in names}
    wlast: Dict[str, np.ndarray] = {}
    n_reb = 0

    t = lookback
    while t + step <= T:
        Rtr = R[t - lookback:t]
        S = cov_estimate(Rtr)
        Rte = R[t:t + step]
        for k in names:
            try:
                w = ALLOCATORS[k](S)
            except Exception:
                w = w_equal(S)
            w = np.nan_to_num(w, nan=1.0 / n)
            w = w / w.sum() if w.sum() > 0 else np.ones(n) / n
            turn = float(np.abs(w - wprev[k]).sum())
            cost = turn * cost_bps / 1e4
            pnl = Rte @ w
            pnl = pnl.copy()
            pnl[0] -= cost
            series[k].extend(pnl.tolist())
            wprev[k] = w
            wlast[k] = w
        n_reb += 1
        t += step

    if n_reb < 4:
        return AllocationResult(pd.DataFrame(), {}, [], "1/N (동일가중)",
                                False, n_reb,
                                "워크포워드 구간이 부족해 경합 불가")

    L = min(len(v) for v in series.values())
    P = np.column_stack([np.array(series[k][:L]) for k in names])

    rows = []
    for i, k in enumerate(names):
        r = P[:, i]
        cum = np.cumprod(1 + r)
        dd = cum / np.maximum.accumulate(cum) - 1
        rows.append({"규칙": k,
                     "연수익": float(np.mean(r) * ann),
                     "연변동성": float(np.std(r, ddof=1) * math.sqrt(ann)),
                     "샤프": sharpe_ratio(r, ann=ann),
                     "최대낙폭": float(dd.min()),
                     "칼마": float(np.mean(r) * ann / abs(dd.min()))
                     if dd.min() < 0 else np.nan})
    tbl = pd.DataFrame(rows).sort_values("샤프", ascending=False)

    # MCS: 손실 = -수익
    try:
        mcs = model_confidence_set(-P, alpha=alpha_mcs, n_boot=400,
                                   mean_block=10, seed=seed)
        surv = [names[i] for i in mcs["mcs_set"]]
    except Exception:
        surv = names
    winner = str(tbl.iloc[0]["규칙"])
    beats = ("1/N (동일가중)" not in surv)
    note = (f"MCS(α={alpha_mcs:.2f}) 생존 규칙: {', '.join(surv)}. "
            + ("1/N이 제외되었으므로 정교한 규칙을 쓸 근거가 있다."
               if beats else
               "**1/N이 생존했다 → 통계적으로 구별되지 않으므로 1/N을 쓴다.** "
               "정교한 최적화의 이점이 표본에서 확인되지 않았다."))
    return AllocationResult(tbl, wlast, surv,
                            winner if beats else "1/N (동일가중)",
                            beats, n_reb, note)


# ================================================================ 포트폴리오 스트레스

def portfolio_stress(analyses: List[Any], weights: np.ndarray,
                     limit: float = 0.25) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    """개별 델타를 비중가중 합산해 책 레벨 충격 손익을 계산한다."""
    agg: Dict[str, float] = {}
    aggd: Dict[str, float] = {}
    shocks: Dict[str, float] = {}
    labels: Dict[str, str] = {}
    for a, w in zip(analyses, weights):
        dp = a.delta_panel
        if dp is None or not len(dp):
            continue
        for _, r in dp.iterrows():
            f = r["factor"]
            b = r["beta_now"] if np.isfinite(r["beta_now"]) else r["beta_static"]
            bd = r["downside_beta"] if np.isfinite(r["downside_beta"]) else b
            if not np.isfinite(b):
                continue
            agg[f] = agg.get(f, 0.0) + w * float(b)
            aggd[f] = aggd.get(f, 0.0) + w * float(bd)
            shocks[f] = float(r["shock"])
            labels[f] = str(r["shock_label"])
    if not agg:
        return pd.DataFrame(), {"worst": np.nan, "within_limit": False,
                                "note": "델타 없음"}
    rows = []
    for f, b in agg.items():
        s = shocks[f]
        rows.append({"충격": labels[f], "팩터": f,
                     "책 순베타": b, "책 하방베타": aggd[f],
                     "손익(정적)": b * s, "손익(하방)": aggd[f] * s,
                     "보수적": min(b * s, aggd[f] * s)})
    # 복합
    combo = {"복합: 리스크오프": {"mkt_excess": -0.20, "vix": 20.0, "hy_oas": 3.0},
             "복합: 긴축+강달러": {"real_yield_10y": 1.0, "nominal_10y": 1.0,
                             "broad_dollar": 0.05}}
    for nm, sh in combo.items():
        use = {k: v for k, v in sh.items() if k in agg}
        if not use:
            continue
        ps = sum(agg[k] * v for k, v in use.items())
        pd_ = sum(aggd[k] * v for k, v in use.items())
        rows.append({"충격": nm, "팩터": "+".join(use),
                     "책 순베타": np.nan, "책 하방베타": np.nan,
                     "손익(정적)": ps, "손익(하방)": pd_,
                     "보수적": min(ps, pd_)})
    df = pd.DataFrame(rows).sort_values("보수적")
    worst = float(df["보수적"].min())
    return df, {"worst": worst, "worst_scenario": str(df.iloc[0]["충격"]),
                "within_limit": bool(abs(worst) <= limit), "limit": limit,
                "note": "책 레벨 선형 델타 근사. 비선형·유동성 연쇄는 미포함."}


# ================================================================ 통합

@dataclass
class PortfolioAnalysis:
    tickers: List[str]
    weights: np.ndarray
    weight_source: str
    risk: RiskDecomp
    netting: FactorNetting
    allocation: AllocationResult
    stress_table: pd.DataFrame
    stress_summary: Dict[str, Any]
    var95: float
    es95: float
    limits: pd.DataFrame
    warnings: List[str]


def analyze_portfolio(analyses: List[Any], weights: Optional[np.ndarray] = None,
                      ann: int = 252, max_single: float = 0.25,
                      max_factor_beta: float = 1.20,
                      stress_limit: float = 0.25) -> PortfolioAnalysis:
    warns: List[str] = []
    tickers = [a.ticker for a in analyses]

    # 공통 인덱스에서 수익률 정렬
    ser = {}
    for a in analyses:
        s = pd.Series(np.asarray(a.returns, float), index=pd.DatetimeIndex(a.index))
        ser[a.ticker] = s
    Rdf = pd.DataFrame(ser).dropna()
    if len(Rdf) < 260:
        warns.append(f"공통 관측 {len(Rdf)}일 — 공분산 추정이 불안정할 수 있음")
    R = Rdf.values

    if weights is None:
        w = np.array([float(a.verdict.risk_budget_weight or 0.0) for a in analyses])
        src = "판정 엔진 리스크 예산"
        if w.sum() <= 0:
            w = np.ones(len(analyses)) / len(analyses)
            src = "1/N (판정 엔진 사이즈가 전부 0)"
            warns.append("모든 포지션의 사이즈가 0 — 분석 목적으로 1/N 가정")
    else:
        w = np.asarray(weights, float)
        src = "사용자 지정"

    rk = risk_decomposition(R, w, tickers, ann)
    nt = factor_netting(analyses, w / w.sum() if w.sum() > 0 else w)
    ac = allocation_competition(R, tickers, ann=ann)
    st, ss = portfolio_stress(analyses, w / w.sum() if w.sum() > 0 else w,
                              limit=stress_limit)

    wn = w / w.sum() if w.sum() > 0 else w
    port_r = R @ wn
    var95 = float(-np.quantile(port_r, 0.05))
    tail = port_r[port_r <= np.quantile(port_r, 0.05)]
    es95 = float(-tail.mean()) if len(tail) else np.nan

    lim = []
    lim.append({"한도": "단일 종목 비중", "기준": f"≤ {max_single:.0%}",
                "현재": f"{np.max(wn):.1%} ({tickers[int(np.argmax(wn))]})",
                "충족": bool(np.max(wn) <= max_single)})
    lim.append({"한도": "단일 종목 위험기여", "기준": "≤ 40%",
                "현재": f"{rk.max_pct_contribution:.1%}",
                "충족": not rk.concentration_flag})
    if len(nt.net_by_factor):
        f = nt.dominant_factor
        lim.append({"한도": "최대 팩터 순베타", "기준": f"≤ {max_factor_beta:.2f}",
                    "현재": f"{nt.dominant_net:+.2f} ({f})",
                    "충족": bool(abs(nt.dominant_net) <= max_factor_beta)})
    lim.append({"한도": "책 스트레스 손실", "기준": f"≤ {stress_limit:.0%}",
                "현재": f"{ss.get('worst', float('nan')):.1%}",
                "충족": bool(ss.get("within_limit", False))})
    lim.append({"한도": "유효 베팅 수", "기준": f"≥ {max(2, len(tickers)//2)}",
                "현재": f"{rk.effective_bets:.2f}",
                "충족": bool(rk.effective_bets >= max(2, len(tickers) // 2))})

    return PortfolioAnalysis(
        tickers=tickers, weights=w, weight_source=src, risk=rk, netting=nt,
        allocation=ac, stress_table=st, stress_summary=ss,
        var95=var95, es95=es95, limits=pd.DataFrame(lim), warnings=warns)
