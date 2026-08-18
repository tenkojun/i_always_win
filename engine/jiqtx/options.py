# ==============================================================================
# [11/25] options.py — IV 표면 · Breeden-Litzenberger RND · 그릭스
# ==============================================================================

"""
jiqtx.options — 옵션 체인 기반 델타. 원본 리포트가 전혀 쓰지 않은 정보원.

Yahoo Finance는 `Ticker.option_chain(expiry)` 로 만기별 체인을 제공한다.
여기서 뽑는 것:
  - ATM IV 기간구조 (1M/3M/6M) → 백워데이션 = 단기 스트레스
  - 25Δ Risk Reversal (put IV − call IV) → 하방 공포 프리미엄
  - 25Δ Butterfly → 꼬리 볼록성 가격
  - IV − RV 스프레드 → 분산위험프리미엄(VRP) 프록시
  - RND (Breeden-Litzenberger) → **시장이 함축한 확률분포 전체**

RND의 결정적 용도
-----------------
우리 몬테카를로 분포(simulate.py)와 시장 RND를 겹쳐 그린다.
두 분포의 차이가 곧 트레이드 논지다. 차이가 없으면 엣지가 없다는 뜻이고,
그것도 valuable한 결론이다.

한계 (반드시 명시)
------------------
yfinance 옵션은 **현재 스냅샷만** 제공하고 과거 체인 히스토리가 없다.
→ 백테스트 불가. 실시간 진단용. 오늘부터 매일 스냅샷을 축적해야 한다.
RND는 원시 가격에 직접 미분하면 노이즈가 폭발한다.
→ IV 공간에서 스무딩 → 재변환 → 미분, 그리고 무차익 조건(단조·볼록)을 강제.
"""

import math
import warnings
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from scipy import stats, interpolate, optimize


# ---------------------------------------------------------------- BS 유틸

def bs_price(S, K, T, r, sigma, is_call=True, q=0.0):
    if T <= 0 or sigma <= 0:
        return max(S - K, 0.0) if is_call else max(K - S, 0.0)
    d1 = (math.log(S / K) + (r - q + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    if is_call:
        return S * math.exp(-q * T) * stats.norm.cdf(d1) - K * math.exp(-r * T) * stats.norm.cdf(d2)
    return K * math.exp(-r * T) * stats.norm.cdf(-d2) - S * math.exp(-q * T) * stats.norm.cdf(-d1)


def bs_delta(S, K, T, r, sigma, is_call=True, q=0.0):
    if T <= 0 or sigma <= 0:
        return 1.0 if (is_call and S > K) else 0.0
    d1 = (math.log(S / K) + (r - q + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
    return math.exp(-q * T) * (stats.norm.cdf(d1) if is_call
                               else stats.norm.cdf(d1) - 1.0)


def bs_greeks(S, K, T, r, sigma, is_call=True, q=0.0) -> Dict[str, float]:
    """Δ Γ Vega Θ Vanna Volga — 포트폴리오 레벨 집계용."""
    if T <= 0 or sigma <= 0:
        return {k: np.nan for k in
                ("delta", "gamma", "vega", "theta", "vanna", "volga")}
    sq = math.sqrt(T)
    d1 = (math.log(S / K) + (r - q + 0.5 * sigma ** 2) * T) / (sigma * sq)
    d2 = d1 - sigma * sq
    pdf = stats.norm.pdf(d1)
    disc_q, disc_r = math.exp(-q * T), math.exp(-r * T)
    delta = disc_q * (stats.norm.cdf(d1) if is_call else stats.norm.cdf(d1) - 1)
    gamma = disc_q * pdf / (S * sigma * sq)
    vega = S * disc_q * pdf * sq
    if is_call:
        theta = (-S * disc_q * pdf * sigma / (2 * sq)
                 - r * K * disc_r * stats.norm.cdf(d2)
                 + q * S * disc_q * stats.norm.cdf(d1))
    else:
        theta = (-S * disc_q * pdf * sigma / (2 * sq)
                 + r * K * disc_r * stats.norm.cdf(-d2)
                 - q * S * disc_q * stats.norm.cdf(-d1))
    return {"delta": delta, "gamma": gamma, "vega": vega / 100.0,
            "theta": theta / 365.0, "vanna": -disc_q * pdf * d2 / sigma,
            "volga": vega * d1 * d2 / sigma / 100.0}


def implied_vol(price, S, K, T, r, is_call=True, q=0.0) -> float:
    if price <= 0 or T <= 0:
        return np.nan
    intrinsic = max(S - K, 0) if is_call else max(K - S, 0)
    if price < intrinsic * math.exp(-r * T) * 0.99:
        return np.nan
    try:
        return float(optimize.brentq(
            lambda v: bs_price(S, K, T, r, v, is_call, q) - price,
            1e-4, 5.0, maxiter=100, xtol=1e-6))
    except Exception:
        return np.nan


# ---------------------------------------------------------------- 체인 요약

@dataclass
class OptionSurface:
    expiries: List[str]
    tenor_days: List[float]
    atm_iv: List[float]
    rr25: List[float]                # put IV − call IV (25Δ)
    bf25: List[float]
    term_slope: float                # 3M − 1M
    backwardation: bool
    atm_iv_1m: float
    rr25_1m: float
    iv_rv_spread: float              # VRP 프록시
    total_call_oi: float
    total_put_oi: float
    put_call_oi_ratio: float
    n_quotes: int
    note: str


def _smile_from_chain(calls: pd.DataFrame, puts: pd.DataFrame, S: float,
                      T: float, r: float, q: float = 0.0
                      ) -> Optional[pd.DataFrame]:
    """OTM 위주로 IV 스마일 구성 (bid/ask 중간값 사용)."""
    rows = []
    for df, is_call in ((calls, True), (puts, False)):
        if df is None or len(df) == 0:
            continue
        d = df.copy()
        for c in ("bid", "ask", "lastPrice", "strike", "impliedVolatility",
                  "openInterest", "volume"):
            if c not in d.columns:
                d[c] = np.nan
        mid = (d["bid"].astype(float) + d["ask"].astype(float)) / 2.0
        px = mid.where(mid > 0, d["lastPrice"].astype(float))
        for K, p, iv, oi in zip(d["strike"].astype(float), px,
                                d["impliedVolatility"].astype(float),
                                d["openInterest"].astype(float)):
            if not np.isfinite(K) or K <= 0:
                continue
            otm = (is_call and K >= S) or ((not is_call) and K <= S)
            if not otm:
                continue
            v = iv if (np.isfinite(iv) and 0.01 < iv < 4.0) else \
                implied_vol(p, S, K, T, r, is_call, q)
            if not np.isfinite(v) or v <= 0.01 or v > 4.0:
                continue
            dl = bs_delta(S, K, T, r, v, is_call, q)
            rows.append({"strike": K, "iv": v, "is_call": is_call,
                         "delta": dl, "oi": oi if np.isfinite(oi) else 0.0,
                         "moneyness": math.log(K / S)})
    if not rows:
        return None
    return pd.DataFrame(rows).sort_values("strike").drop_duplicates("strike")


def _interp_iv_at_delta(sm: pd.DataFrame, target: float, is_call: bool) -> float:
    d = sm[sm["is_call"] == is_call]
    if len(d) < 3:
        return np.nan
    x = d["delta"].abs().values
    y = d["iv"].values
    o = np.argsort(x)
    try:
        return float(np.interp(target, x[o], y[o]))
    except Exception:
        return np.nan


def option_surface(ticker: str, spot: float, realized_vol_ann: float,
                   r: float = 0.04, max_expiries: int = 6) -> Optional[OptionSurface]:
    """yfinance 옵션 체인 → 표면 요약. 옵션이 없으면 None."""
    try:
        import yfinance as yf
    except ImportError:
        return None
    t = yf.Ticker(ticker)
    try:
        exps = list(t.options)[:max_expiries]
    except Exception:
        return None
    if not exps:
        return None

    today = pd.Timestamp.today().normalize()
    tenors, ivs, rrs, bfs, exp_ok = [], [], [], [], []
    coi = poi = 0.0
    nq = 0
    for e in exps:
        try:
            ch = t.option_chain(e)
        except Exception:
            continue
        T = max((pd.Timestamp(e) - today).days, 1) / 365.0
        sm = _smile_from_chain(ch.calls, ch.puts, spot, T, r)
        if sm is None or len(sm) < 5:
            continue
        atm = float(np.interp(0.0, sm["moneyness"].values, sm["iv"].values))
        c25 = _interp_iv_at_delta(sm, 0.25, True)
        p25 = _interp_iv_at_delta(sm, 0.25, False)
        rr = (p25 - c25) if np.isfinite(c25) and np.isfinite(p25) else np.nan
        bf = ((c25 + p25) / 2.0 - atm) if np.isfinite(c25) and np.isfinite(p25) else np.nan
        tenors.append(T * 365); ivs.append(atm); rrs.append(rr); bfs.append(bf)
        exp_ok.append(e)
        coi += float(ch.calls.get("openInterest", pd.Series(dtype=float)).sum() or 0)
        poi += float(ch.puts.get("openInterest", pd.Series(dtype=float)).sum() or 0)
        nq += len(sm)

    if not tenors:
        return None
    iv1m = float(np.interp(30, tenors, ivs)) if len(tenors) > 1 else ivs[0]
    iv3m = float(np.interp(90, tenors, ivs)) if len(tenors) > 1 else ivs[0]
    slope = iv3m - iv1m
    rr1m = float(np.interp(30, tenors, np.nan_to_num(rrs, nan=np.nanmean(rrs)))) \
        if len(tenors) > 1 else (rrs[0] if rrs else np.nan)
    return OptionSurface(
        expiries=exp_ok, tenor_days=tenors, atm_iv=ivs, rr25=rrs, bf25=bfs,
        term_slope=slope, backwardation=bool(slope < -0.01),
        atm_iv_1m=iv1m, rr25_1m=rr1m,
        iv_rv_spread=float(iv1m - realized_vol_ann)
        if np.isfinite(realized_vol_ann) else np.nan,
        total_call_oi=coi, total_put_oi=poi,
        put_call_oi_ratio=float(poi / coi) if coi > 0 else np.nan,
        n_quotes=nq,
        note=("IV−RV 스프레드는 분산위험프리미엄 프록시. 양수면 시장이 "
              "실현변동성보다 비싸게 보험을 팔고 있다는 뜻. "
              "옵션 스냅샷은 히스토리가 없어 백테스트 불가."),
    )


# ---------------------------------------------------------------- RND

@dataclass
class RND:
    strikes: np.ndarray
    density: np.ndarray
    cdf: np.ndarray
    q05: float
    q25: float
    q50: float
    q75: float
    q95: float
    prob_up: float
    implied_mean: float
    implied_std: float
    implied_skew: float
    tenor_days: float
    arbitrage_ok: bool
    note: str


def risk_neutral_density(ticker: str, spot: float, target_days: int = 60,
                         r: float = 0.04, n_grid: int = 400) -> Optional[RND]:
    """
    Breeden-Litzenberger:  ∂²C/∂K² = e^{rT} · f_Q(K)

    구현 순서 (노이즈 억제):
      1) OTM 옵션에서 IV 스마일 추출
      2) IV 공간에서 스무딩 스플라인 적합
      3) 조밀한 행사가 격자에서 BS 콜가격 재구성
      4) 2차 중앙차분 → 밀도
      5) 무차익 조건 강제: 밀도 ≥ 0, 적분 = 1
    """
    try:
        import yfinance as yf
    except ImportError:
        return None
    t = yf.Ticker(ticker)
    try:
        exps = list(t.options)
    except Exception:
        return None
    if not exps:
        return None
    today = pd.Timestamp.today().normalize()
    days = np.array([max((pd.Timestamp(e) - today).days, 1) for e in exps])
    e = exps[int(np.argmin(np.abs(days - target_days)))]
    T = max((pd.Timestamp(e) - today).days, 1) / 365.0
    try:
        ch = t.option_chain(e)
    except Exception:
        return None
    sm = _smile_from_chain(ch.calls, ch.puts, spot, T, r)
    if sm is None or len(sm) < 8:
        return None

    x = sm["moneyness"].values
    y = sm["iv"].values
    o = np.argsort(x)
    x, y = x[o], y[o]
    try:
        spl = interpolate.UnivariateSpline(x, y, s=len(x) * 1e-4, k=3)
    except Exception:
        return None

    lo, hi = float(x.min()), float(x.max())
    grid_m = np.linspace(lo, hi, n_grid)
    K = spot * np.exp(grid_m)
    iv = np.clip(spl(grid_m), 0.01, 4.0)
    C = np.array([bs_price(spot, k, T, r, v, True) for k, v in zip(K, iv)])

    dK = np.gradient(K)
    d2 = np.gradient(np.gradient(C, K), K)
    dens = np.exp(r * T) * d2
    arb_ok = bool(np.mean(dens < -1e-8) < 0.05)
    dens = np.clip(dens, 0.0, None)
    area = float(np.trapezoid(dens, K)) if hasattr(np, "trapezoid") \
        else float(np.trapz(dens, K))
    if area <= 0:
        return None
    dens = dens / area
    cdf = np.cumsum(dens * dK)
    cdf = cdf / cdf[-1]

    def q(p):
        return float(np.interp(p, cdf, K))

    mean = float(np.sum(K * dens * dK))
    var = float(np.sum((K - mean) ** 2 * dens * dK))
    sd = math.sqrt(max(var, 1e-12))
    sk = float(np.sum((K - mean) ** 3 * dens * dK) / sd ** 3) if sd > 0 else np.nan
    p_up = float(1.0 - np.interp(spot, K, cdf))

    return RND(K, dens, cdf, q(0.05), q(0.25), q(0.50), q(0.75), q(0.95),
               p_up, mean, sd, sk, T * 365, arb_ok,
               note=("시장 함축 확률분포. 위험중립 측도이므로 리스크 프리미엄이 "
                     "포함돼 있어 실세계 확률과 동일하지 않다. 우리 모델 분포와 "
                     "겹쳐 보고 '차이'를 논지로 삼는 것이 올바른 용법."))


def compare_model_vs_market(sim_terminal: np.ndarray, rnd: RND,
                            spot: float) -> Dict[str, float]:
    """우리 모델 분포 vs 시장 RND 비교 → 트레이드 논지."""
    if rnd is None:
        return {}
    mq = {p: float(np.quantile(sim_terminal, p)) for p in (0.05, 0.25, 0.5, 0.75, 0.95)}
    rq = {0.05: rnd.q05, 0.25: rnd.q25, 0.5: rnd.q50, 0.75: rnd.q75, 0.95: rnd.q95}
    diff = {f"q{int(p*100):02d}_diff_pct": (mq[p] / rq[p] - 1.0) for p in mq}
    model_up = float(np.mean(sim_terminal > spot))
    return {**diff,
            "model_prob_up": model_up,
            "market_prob_up_rn": rnd.prob_up,
            "prob_gap": model_up - rnd.prob_up,
            "model_median": mq[0.5], "market_median": rq[0.5],
            "verdict": ("모델이 시장보다 강세" if model_up - rnd.prob_up > 0.05
                        else "모델이 시장보다 약세" if model_up - rnd.prob_up < -0.05
                        else "모델과 시장이 사실상 동일 — 방향성 엣지 없음")}
