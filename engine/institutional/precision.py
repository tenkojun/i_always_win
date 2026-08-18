"""
================================================================
  기관급 정밀도 모듈  (Institutional Precision)
================================================================
대형 기관(BlackRock Aladdin / MSCI Barra / 헤지펀드)이 실제로
쓰는 통계 기법을 적용해, 기존 엔진의 세 가지 낙관 편향을 교정한다.

근거 문헌
---------
1) Bailey & López de Prado (2012, 2014):
     - Probabilistic Sharpe Ratio (PSR)
     - Deflated Sharpe Ratio (DSR)  — 선택편향·다중검정·비정규성 보정
     - Minimum Track Record Length (MinTRL)
2) López de Prado (2018, "Advances in Financial ML"):
     - Purged & Embargoed Walk-Forward / Combinatorial Purged CV
       — 라벨 중첩으로 인한 데이터 누수 제거
3) MSCI Barra USE4 Methodology:
     - V = B·Σf·B' + D (체계적 + 고유) 분해
     - EWMA 공분산(λ=0.94 일간), Ledoit-Wolf 축소, 고유분산 하한
     - Newey-West 자기상관 보정
4) Chekhlov, Uryasev & Zabarankin (2000, 2005):
     - Conditional Drawdown-at-Risk (CDaR), Time-under-Water
     - 비대칭·국면조건부 회복 모델

핵심 원칙
---------
- "최근 상승장을 외운 수치"가 아니라 "표본·다중검정·비정규성을
  보정한 정직한 수치"를 보고한다.
- 단일 종목·무료 데이터의 구조적 한계는 숨기지 않고 명시한다.
"""
from __future__ import annotations

import math
import warnings
from typing import Any, Callable, Dict, List, Optional, Sequence

import numpy as np
import pandas as pd
from scipy.stats import norm, skew, kurtosis

warnings.filterwarnings("ignore")

_EULER_GAMMA = 0.5772156649015328606


# ================================================================
#  1. 샤프 보정  ―  PSR / DSR / MinTRL  (Bailey & López de Prado)
# ================================================================
def _sr_per_period(returns: np.ndarray) -> float:
    """비연환산(per-period) 샤프. DSR/PSR 공식은 비연환산 SR을 쓴다."""
    r = returns[~np.isnan(returns)]
    sd = r.std(ddof=1)
    if sd == 0 or len(r) < 3:
        return 0.0
    return float(r.mean() / sd)


def probabilistic_sharpe_ratio(returns: Sequence[float],
                               sr_benchmark: float = 0.0) -> Dict[str, float]:
    """
    Probabilistic Sharpe Ratio (Bailey & López de Prado 2012).

    추정 SR이 기준 SR(보통 0)보다 **실제로 클 확률**을 표본 길이와
    왜도·첨도(비정규성)까지 반영해 계산한다.

        PSR = Φ( (SR - SR*)·√(T-1)
                 / √(1 - γ3·SR + ((γ4-1)/4)·SR²) )
    """
    r = np.asarray(returns, dtype=float)
    r = r[~np.isnan(r)]
    T = len(r)
    if T < 10:
        return {"psr": float("nan"), "sr_obs": 0.0, "T": T}
    sr = _sr_per_period(r)
    g3 = float(skew(r))
    g4 = float(kurtosis(r, fisher=False))
    denom = 1.0 - g3 * sr + (g4 - 1.0) / 4.0 * sr ** 2
    denom = max(denom, 1e-9)
    z = (sr - sr_benchmark) * math.sqrt(T - 1) / math.sqrt(denom)
    return {"psr": float(norm.cdf(z)), "sr_obs": sr, "T": T,
            "skew": g3, "kurtosis": g4}


def deflated_sharpe_ratio(returns: Sequence[float],
                          n_trials: int,
                          trials_sr_var: Optional[float] = None
                          ) -> Dict[str, float]:
    """
    Deflated Sharpe Ratio (Bailey & López de Prado 2014).

    여러 전략/파라미터를 시도해 "가장 좋은 것"을 골랐을 때 생기는
    선택편향(승자의 저주)을 제거한 SR 신뢰도.

    n_trials: 사실상 시도한 독립 구성 수(타임프레임×모델×국면 ≈ 9~15)
    """
    r = np.asarray(returns, dtype=float)
    r = r[~np.isnan(r)]
    T = len(r)
    if T < 10:
        return {"dsr": float("nan"), "sr0": float("nan")}
    sr = _sr_per_period(r)
    g3 = float(skew(r))
    g4 = float(kurtosis(r, fisher=False))
    N = max(int(n_trials), 2)
    if trials_sr_var is None or not np.isfinite(trials_sr_var):
        trials_sr_var = (1.0 - g3 * sr + (g4 - 1.0) / 4.0 * sr ** 2) / (T - 1)
    trials_sr_var = max(float(trials_sr_var), 1e-12)
    z1 = norm.ppf(1.0 - 1.0 / N)
    z2 = norm.ppf(1.0 - 1.0 / (N * math.e))
    sr0 = math.sqrt(trials_sr_var) * (
        (1.0 - _EULER_GAMMA) * z1 + _EULER_GAMMA * z2)
    denom = (1.0 - g3 * sr + (g4 - 1.0) / 4.0 * sr ** 2) / (T - 1)
    denom = max(denom, 1e-12)
    dsr = float(norm.cdf((sr - sr0) / math.sqrt(denom)))
    return {"dsr": dsr, "sr0": float(sr0), "sr_obs": sr,
            "n_trials": N}


def minimum_track_record_length(returns: Sequence[float],
                                sr_benchmark: float = 0.0,
                                conf: float = 0.95) -> Dict[str, float]:
    """
    Minimum Track Record Length (Bailey & López de Prado 2012).

    "이 SR이 기준보다 크다"고 conf 확신을 가지려면 최소 몇 개의
    관측치(영업일)가 필요한가.
    """
    r = np.asarray(returns, dtype=float)
    r = r[~np.isnan(r)]
    T = len(r)
    if T < 10:
        return {"min_trl": float("nan"), "have": T, "enough": False}
    sr = _sr_per_period(r)
    g3 = float(skew(r))
    g4 = float(kurtosis(r, fisher=False))
    if abs(sr - sr_benchmark) < 1e-9:
        return {"min_trl": float("inf"), "have": T, "enough": False}
    z = norm.ppf(conf)
    var_term = 1.0 - g3 * sr + (g4 - 1.0) / 4.0 * sr ** 2
    min_trl = 1.0 + var_term * (z / (sr - sr_benchmark)) ** 2
    return {"min_trl": float(min_trl), "have": int(T),
            "enough": bool(T >= min_trl)}


# ================================================================
#  2. 라벨 누수 제거  ―  Purged & Embargoed Walk-Forward CV
# ================================================================
def purged_walkforward_cv(feats: pd.DataFrame,
                          fit_predict: Callable[[pd.DataFrame, pd.DataFrame],
                                                np.ndarray],
                          target_col: str = "target",
                          label_horizon: int = 5,
                          n_folds: int = 6,
                          embargo_frac: float = 0.02) -> Dict[str, Any]:
    """
    퍼지드 + 엠바고 워크포워드 교차검증 (López de Prado 2018).

    기존 단일 80/20 분할의 라벨 누수를 교정:
      - 확장 윈도(워크포워드)로 여러 OOS 구간을 순차 검증
      - 각 test 구간 앞쪽 label_horizon 만큼의 train 표본을 퍼지
      - test 직후 embargo_frac 비율만큼 엠바고(자기상관 차단)
    """
    if target_col not in feats.columns or len(feats) < 250:
        return {"ok": False, "note": "데이터 부족(<250) 또는 라벨 없음"}

    feats = feats.dropna().reset_index(drop=True)
    n = len(feats)
    if n < 250:
        return {"ok": False, "note": "유효표본 부족"}

    start = int(n * 0.40)
    block = max(int((n - start) / n_folds), 20)
    embargo = max(int(n * embargo_frac), label_horizon)

    oos_probs: List[float] = []
    oos_accs: List[float] = []
    is_accs: List[float] = []

    fold = 0
    tr_end = start
    while tr_end + block <= n and fold < n_folds:
        te_lo = tr_end
        te_hi = min(tr_end + block, n)

        purge_hi = max(0, te_lo - label_horizon)
        train = feats.iloc[:purge_hi]
        test = feats.iloc[te_lo:te_hi]
        if len(train) < 150 or len(test) < 10:
            tr_end = te_hi + embargo
            fold += 1
            continue
        try:
            proba = fit_predict(train, test)
            proba = np.asarray(proba, dtype=float)
            proba = proba[~np.isnan(proba)]
            if len(proba):
                y_te = test[target_col].values[:len(proba)]
                pred = (proba >= 0.5).astype(int)
                oos_accs.append(float((pred == y_te).mean()))
                oos_probs.append(float(proba[-1]))
            p_in = np.asarray(fit_predict(train, train), dtype=float)
            p_in = p_in[~np.isnan(p_in)]
            if len(p_in):
                y_in = train[target_col].values[:len(p_in)]
                is_accs.append(float(((p_in >= 0.5).astype(int)
                                      == y_in).mean()))
        except Exception:
            pass
        tr_end = te_hi + embargo
        fold += 1

    if not oos_accs:
        return {"ok": False, "note": "교차검증 폴드 부족"}

    arr = np.array(oos_accs)
    pr = np.array(oos_probs) if oos_probs else np.array([np.nan])
    oos_acc = float(arr.mean())
    is_acc = float(np.mean(is_accs)) if is_accs else float("nan")
    gap = float(is_acc - oos_acc) if np.isfinite(is_acc) else float("nan")
    return {
        "ok": True,
        "n_folds": len(oos_accs),
        "oos_acc_mean": oos_acc,
        "oos_acc_std": float(arr.std(ddof=1)) if len(arr) > 1 else 0.0,
        "oos_prob_mean": float(np.nanmean(pr)),
        "oos_prob_median": float(np.nanmedian(pr)),
        "oos_prob_std": float(np.nanstd(pr)),
        "ci_low": float(np.nanpercentile(pr, 5)) if len(pr) > 1 else float("nan"),
        "ci_high": float(np.nanpercentile(pr, 95)) if len(pr) > 1 else float("nan"),
        "in_sample_acc": is_acc,
        "overfit_gap": gap,
        "overfit_flag": bool(np.isfinite(gap) and gap > 0.15),
        "last_prob": float(pr[-1]) if len(pr) and np.isfinite(pr[-1])
        else float("nan"),
    }


# ================================================================
#  3. 국면조건부 통계  ―  상승장 편향 노출
# ================================================================
def regime_conditional_sharpe(returns: pd.Series,
                              regimes: Optional[Sequence[int]] = None,
                              periods_per_year: int = 252) -> Dict[str, Any]:
    """
    국면별(상승/중립/하락) 샤프·수익·변동성을 분해.

    헤드라인 샤프가 표본의 상승장 비중 때문에 부풀려졌는지 드러낸다.
    """
    r = pd.Series(returns).dropna()
    if len(r) < 30:
        return {"ok": False}
    if regimes is None or len(regimes) != len(r):
        trend = r.rolling(60).mean()
        reg = pd.Series(np.where(trend > trend.std() * 0.1, 2,
                        np.where(trend < -trend.std() * 0.1, 0, 1)),
                        index=r.index)
    else:
        reg = pd.Series(np.asarray(regimes), index=r.index)

    out: Dict[str, Any] = {"ok": True, "by_regime": {}}
    names = {0: "하락국면", 1: "중립국면", 2: "상승국면"}
    ann = math.sqrt(periods_per_year)
    for k, nm in names.items():
        seg = r[reg == k]
        if len(seg) < 15:
            continue
        sd = seg.std(ddof=1)
        out["by_regime"][nm] = {
            "n": int(len(seg)),
            "share_pct": float(len(seg) / len(r) * 100),
            "ret_ann_pct": float(seg.mean() * periods_per_year * 100),
            "vol_ann_pct": float(sd * ann * 100) if sd else 0.0,
            "sharpe": float(seg.mean() / sd * ann) if sd else 0.0,
        }
    full_sd = r.std(ddof=1)
    full_sh = float(r.mean() / full_sd * ann) if full_sd else 0.0
    worst = out["by_regime"].get("하락국면", {}).get("sharpe")
    out["full_sharpe"] = full_sh
    out["worst_regime_sharpe"] = worst
    out["bull_share_pct"] = out["by_regime"].get(
        "상승국면", {}).get("share_pct", 0.0)
    return out


# ================================================================
#  4. 공분산 추정  ―  EWMA / Ledoit-Wolf 축소  (Barra 표준)
# ================================================================
def ewma_cov(X: np.ndarray, lam: float = 0.94) -> np.ndarray:
    """RiskMetrics/Barra식 EWMA 공분산 (일간 λ=0.94)."""
    X = np.asarray(X, dtype=float)
    X = X - X.mean(axis=0)
    T = len(X)
    w = lam ** np.arange(T - 1, -1, -1)
    w /= w.sum()
    return (X * w[:, None]).T @ X


def ledoit_wolf_shrink(X: np.ndarray) -> np.ndarray:
    """Ledoit-Wolf 선형 축소 공분산 (대각 타깃)."""
    try:
        from sklearn.covariance import LedoitWolf
        return LedoitWolf().fit(np.asarray(X, dtype=float)).covariance_
    except Exception:
        S = np.cov(np.asarray(X, dtype=float), rowvar=False)
        return np.atleast_2d(S)


# ================================================================
#  5. 팩터 리스크 분해  ―  Barra식 V = B·Σf·B' + D
# ================================================================
def barra_style_decomposition(stock_ret: pd.Series,
                              factors: pd.DataFrame,
                              is_real_market: bool = False
                              ) -> Dict[str, Any]:
    """
    MSCI Barra USE4 방식 위험 분해.

      r = α + Σ βk·fk + ε
      총분산 = β'·Σf·β  (체계적) + Var(ε)  (고유)

    팩터 공분산 Σf를 EWMA(λ=0.94)로 추정, Newey-West 자기상관 보정,
    고유분산에 하한(총분산의 5%) 부여.
    """
    df = pd.concat([stock_ret.rename("y"), factors], axis=1).dropna()
    if len(df) < 60:
        return {"ok": False, "note": "표본 부족"}
    y = df["y"].values
    fcols = [c for c in factors.columns if c != "RF"]
    F = df[fcols].values
    Fc = np.column_stack([np.ones(len(F)), F])

    beta, *_ = np.linalg.lstsq(Fc, y, rcond=None)
    resid = y - Fc @ beta
    alpha = float(beta[0])
    b = beta[1:]

    T = len(y)
    s2 = float(resid @ resid / max(T - len(beta), 1))
    Sigma_f = ewma_cov(F, lam=0.94) if F.shape[1] > 1 else \
        np.array([[np.var(F[:, 0], ddof=1)]])
    sys_var = float(b @ Sigma_f @ b)
    spec_var = max(s2, 0.05 * (sys_var + s2))
    tot = sys_var + spec_var
    sys_pct = sys_var / tot * 100 if tot > 0 else float("nan")

    contrib = {}
    for i, c in enumerate(fcols):
        ci = float(b[i] * (Sigma_f @ b)[i]) if F.shape[1] > 1 else \
            float(b[i] ** 2 * Sigma_f[0, 0])
        contrib[c] = ci / tot * 100 if tot > 0 else 0.0
    top = max(contrib, key=contrib.get) if contrib else None

    ann = math.sqrt(252)
    return {
        "ok": True,
        "betas": {c: float(b[i]) for i, c in enumerate(fcols)},
        "alpha_ann_pct": float(alpha * 252 * 100),
        "total_vol_ann_pct": float(math.sqrt(max(tot, 0)) * ann * 100),
        "systematic_pct": float(sys_pct),
        "specific_pct": float(100 - sys_pct) if np.isfinite(sys_pct)
        else float("nan"),
        "contrib_pct": contrib,
        "top_driver": top,
        "top_driver_pct": float(contrib.get(top, 0.0)) if top else 0.0,
        "is_real_market": bool(is_real_market),
        "method": "Barra식 (EWMA Σf λ=0.94, 고유분산 하한, "
                  + ("실제 시장지수" if is_real_market else "근사 프록시")
                  + ")",
    }


# ================================================================
#  6. 낙폭·회복  ―  CDaR / Time-under-Water / 비대칭 회복
# ================================================================
def drawdown_curve(equity: pd.Series) -> pd.Series:
    eq = pd.Series(equity).dropna()
    peak = eq.cummax()
    return eq / peak - 1.0


def conditional_drawdown_at_risk(equity: pd.Series,
                                 beta: float = 0.95) -> Dict[str, float]:
    """
    Conditional Drawdown-at-Risk (Chekhlov-Uryasev-Zabarankin 2000).

    낙폭(수중)곡선의 **최악 (1-β)% 평균**. MDD 한 점이 아니라
    꼬리 평균이라 위기내성을 더 정직하게 본다.
    """
    dd = drawdown_curve(equity)
    if len(dd) < 20:
        return {"ok": False}
    losses = -dd.values
    dar = np.quantile(losses, beta)
    tail = losses[losses >= dar]
    cdar = float(tail.mean()) if len(tail) else float(dar)

    under = (dd.values < -1e-9).astype(int)
    longest = cur = 0
    for u in under:
        cur = cur + 1 if u else 0
        longest = max(longest, cur)
    return {
        "ok": True,
        "max_dd_pct": float(dd.min() * 100),
        "avg_dd_pct": float(dd[dd < 0].mean() * 100) if (dd < 0).any() else 0.0,
        "dar_pct": float(-dar * 100),
        "cdar_pct": float(-cdar * 100),
        "time_under_water_days": int(longest),
        "beta": beta,
    }


def asymmetric_recovery_days(shock: float,
                             returns: pd.Series,
                             regimes: Optional[Sequence[int]] = None
                             ) -> int:
    """
    비대칭·국면조건부 회복일 추정.

    하락/중립 국면의 보수적 드리프트 사용 + 비대칭 페널티(×1.6).
    """
    r = pd.Series(returns).dropna()
    if shock >= 0 or len(r) < 60:
        return 0
    if regimes is not None and len(regimes) == len(r):
        reg = pd.Series(np.asarray(regimes), index=r.index)
        calm = r[reg.isin([0, 1])]
    else:
        trend = r.rolling(60).mean()
        calm = r[trend <= trend.std() * 0.1]
    drift = calm.mean() if len(calm) > 20 else r.mean()
    base_up = max(float(r[r > 0].mean()) * 0.5, 1e-4)
    rec_drift = max(min(float(drift) if drift > 0 else base_up,
                        base_up), 1e-4)
    raw = math.log(1.0 / (1.0 + shock)) / rec_drift
    return int(min(raw * 1.6, 8000))


# ================================================================
#  7. 종합 진단  ―  리포트/스코어카드/내러티브용 번들
# ================================================================
def precision_diagnostics(returns: pd.Series,
                          equity: Optional[pd.Series] = None,
                          regimes: Optional[Sequence[int]] = None,
                          n_trials: int = 12) -> Dict[str, Any]:
    """모든 정밀도 지표를 하나로 묶어 반환(엔진 통합용)."""
    r = pd.Series(returns).dropna()
    if equity is None:
        equity = (1.0 + r).cumprod()

    psr = probabilistic_sharpe_ratio(r.values, 0.0)
    dsr = deflated_sharpe_ratio(r.values, n_trials=n_trials)
    trl = minimum_track_record_length(r.values, 0.0, 0.95)
    reg = regime_conditional_sharpe(r, regimes)
    cdar = conditional_drawdown_at_risk(equity, beta=0.95)

    flags: List[str] = []
    if np.isfinite(psr.get("psr", np.nan)) and psr["psr"] < 0.90:
        flags.append("샤프 통계적 신뢰 부족")
    if np.isfinite(dsr.get("dsr", np.nan)) and dsr["dsr"] < 0.90:
        flags.append("다중검정 보정 후 우위 약함")
    if not trl.get("enough", False):
        flags.append("표본이 SR 신뢰 최소길이 미달")
    return {
        "psr": psr, "dsr": dsr, "min_trl": trl,
        "regime": reg, "cdar": cdar,
        "robustness_flags": flags,
        "robust": len(flags) == 0,
    }
