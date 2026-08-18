"""
몬테카를로 시뮬레이션 모듈 (Axioma/Barra 기관급 업그레이드)
=============================================================
method
------
- bootstrap       : IID 복원추출 (기존)
- parametric      : 정규분포 μ/σ (기존)
- block_bootstrap : 블록 복원추출 — 자기상관 보존 (신규)
- garch           : GARCH(1,1) 변동성 군집 반영 (신규, arch 필요)
- jump_diffusion  : Merton(1976) 점프확산 — fat-tail (신규)
- regime          : HMM 국면전환 기반 파라미터 (신규)

출력 dict
----------
simulated_equity  : (n_sim, horizon) 자본 경로
terminal_pnl      : (n_sim,) 종착 수익률
var / cvar        : 5% 기준
maxdd_distribution: 경로별 MDD
ci_low/ci_high    : 5%/95% 분위
median_path       : 중앙값 경로
method_used       : 실제 사용된 방법 (폴백 포함)
"""
from __future__ import annotations
from typing import Dict, Optional
import numpy as np
import pandas as pd


# ── 공통 통계 추출 ──────────────────────────────────────────────────────────
def _extract_stats(sim_r: np.ndarray) -> Dict:
    """(n_sim, horizon) 수익률 배열 → 공통 통계 dict."""
    sim_eq = (1.0 + sim_r).cumprod(axis=1)
    terminal = sim_eq[:, -1] - 1.0
    roll_max = np.maximum.accumulate(sim_eq, axis=1)
    maxdd = (sim_eq / roll_max - 1.0).min(axis=1)
    q05 = np.quantile(terminal, 0.05)
    tail = terminal[terminal <= q05]
    return {
        "simulated_equity":   sim_eq,
        "terminal_pnl":       terminal,
        "var":                float(-q05),
        "cvar":               float(-tail.mean()) if len(tail) else 0.0,
        "maxdd_distribution": maxdd,
        "ci_low":             np.quantile(sim_eq, 0.05, axis=0),
        "ci_high":            np.quantile(sim_eq, 0.95, axis=0),
        "median_path":        np.quantile(sim_eq, 0.50, axis=0),
    }


# ── 1. IID Bootstrap ───────────────────────────────────────────────────────
def _iid_bootstrap(r: np.ndarray, n_sim: int, horizon: int,
                   rng: np.random.Generator) -> np.ndarray:
    idx = rng.integers(0, len(r), size=(n_sim, horizon))
    return r[idx]


# ── 2. Parametric (Normal) ─────────────────────────────────────────────────
def _parametric(r: np.ndarray, n_sim: int, horizon: int,
                rng: np.random.Generator) -> np.ndarray:
    mu, sd = r.mean(), max(r.std(ddof=1), 1e-10)
    return rng.normal(mu, sd, size=(n_sim, horizon))


# ── 3. Block Bootstrap (자기상관 보존) ────────────────────────────────────
def _block_bootstrap(r: np.ndarray, n_sim: int, horizon: int,
                     rng: np.random.Generator,
                     block_size: Optional[int] = None) -> np.ndarray:
    """
    Politis & Romano (1994) 스테이셔너리 블록 부트스트랩.
    block_size: None이면 len(r)^(1/3) 자동 설정 (실증적 최적)
    """
    n = len(r)
    if block_size is None:
        block_size = max(5, int(round(n ** (1 / 3))))

    paths = np.empty((n_sim, horizon))
    for i in range(n_sim):
        out = []
        while len(out) < horizon:
            start = rng.integers(0, n)
            bl = min(block_size, horizon - len(out), n - start)
            out.extend(r[start: start + bl].tolist())
        paths[i] = out[:horizon]
    return paths


# ── 4. GARCH(1,1) MC (변동성 군집 반영) ───────────────────────────────────
def _garch_mc(r: np.ndarray, n_sim: int, horizon: int,
              rng: np.random.Generator) -> tuple[np.ndarray, str]:
    """
    arch 라이브러리로 GARCH(1,1) 파라미터 추정 후 시뮬레이션.
    arch 없으면 EWMA 근사로 폴백.
    Returns (sim_r, method_name)
    """
    try:
        from arch import arch_model  # type: ignore
        scale = 100.0
        model = arch_model(r * scale, vol='GARCH', p=1, q=1,
                           mean='Zero', rescale=False)
        res = model.fit(disp='off', show_warning=False)
        omega = float(res.params.get('omega', 1e-6))
        alpha = float(res.params.get('alpha[1]', 0.05))
        beta  = float(res.params.get('beta[1]', 0.90))
        # 마지막 조건부 분산
        last_var = float(res.conditional_volatility.iloc[-1] ** 2)

        sim_r = np.empty((n_sim, horizon))
        for i in range(n_sim):
            v = last_var
            for t in range(horizon):
                eps = rng.standard_normal() * np.sqrt(v) / scale
                sim_r[i, t] = eps
                v = omega + alpha * (eps * scale) ** 2 + beta * v
                v = max(v, 1e-12)
        return sim_r, "garch"

    except Exception:
        # EWMA 폴백 (λ=0.94 RiskMetrics)
        lam = 0.94
        var_t = r.var()
        for rt in r[-60:]:
            var_t = lam * var_t + (1 - lam) * rt ** 2
        sd = max(np.sqrt(var_t), 1e-10)
        sim_r = rng.normal(r.mean(), sd, size=(n_sim, horizon))
        return sim_r, "garch_ewma_fallback"


# ── 5. Jump Diffusion — Merton (1976) ─────────────────────────────────────
def _jump_diffusion(r: np.ndarray, n_sim: int, horizon: int,
                    rng: np.random.Generator) -> np.ndarray:
    """
    dS/S = (μ - λμ_J) dt + σ dW + J dN
    N ~ Poisson(λ dt), J ~ LogNormal(μ_J, σ_J)
    파라미터는 초과첨도와 비대칭도에서 GMM-style 추정.
    """
    mu    = r.mean() * 252            # 연율화 드리프트
    sigma = r.std(ddof=1) * np.sqrt(252)  # 연율화 변동성
    dt    = 1.0 / 252

    # 점프 파라미터 (초과첨도에서 추정)
    kurt = pd.Series(r).kurtosis()    # 초과첨도
    skew = pd.Series(r).skew()
    lam   = max(0.05, min(3.0, abs(kurt) / 6))    # 연간 점프 횟수
    mu_j  = skew * r.std() / max(lam, 0.01)        # 평균 점프 크기 (일별)
    sig_j = max(abs(r.std() * 0.5), r.std() * np.sqrt(max(0, kurt / (4 * max(lam, 0.01)))))

    # 점프 보정 드리프트
    mu_adj = mu - lam * (np.exp(mu_j + 0.5 * sig_j ** 2) - 1)

    sim_r = np.empty((n_sim, horizon))
    for i in range(n_sim):
        # 확산
        diff = (mu_adj - 0.5 * sigma ** 2) * dt + sigma * np.sqrt(dt) * rng.standard_normal(horizon)
        # 점프
        n_jumps = rng.poisson(lam * dt, horizon)
        jumps = np.array([
            rng.lognormal(mu_j, sig_j, nj).sum() - nj if nj > 0 else 0.0
            for nj in n_jumps
        ])
        sim_r[i] = diff + jumps
    return sim_r


# ── 6. Regime-Switching MC (HMM 국면전환) ─────────────────────────────────
def _regime_mc(r: np.ndarray, n_sim: int, horizon: int,
               rng: np.random.Generator,
               regimes: Optional[np.ndarray] = None) -> np.ndarray:
    """
    국면별 (μ, σ)와 전이행렬에서 시뮬레이션.
    regimes: 각 시점의 국면 레이블 (없으면 KMeans 자동 분류)
    """
    # 국면 분류 (regimes 없으면 KMeans 3-state)
    if regimes is None or len(regimes) != len(r):
        try:
            from sklearn.cluster import KMeans
            from sklearn.preprocessing import StandardScaler
            vol = pd.Series(r).rolling(20).std().fillna(pd.Series(r).std()).values
            feat = StandardScaler().fit_transform(
                np.column_stack([r, vol, np.abs(r)]))
            regimes = KMeans(n_clusters=3, n_init=5, random_state=42).fit_predict(feat)
        except Exception:
            regimes = np.zeros(len(r), dtype=int)

    unique = np.unique(regimes)
    n_reg = len(unique)

    # 국면별 μ, σ
    params = {}
    for reg in unique:
        mask = regimes == reg
        r_reg = r[mask]
        params[int(reg)] = {
            "mu": float(r_reg.mean()) if len(r_reg) > 1 else 0.0,
            "sd": float(max(r_reg.std(ddof=1), 1e-6)) if len(r_reg) > 1 else 1e-4,
        }

    # 전이행렬 추정
    trans = np.ones((n_reg, n_reg)) * 0.01  # 라플라스 스무딩
    for t in range(len(regimes) - 1):
        fi, ti_ = int(regimes[t]), int(regimes[t + 1])
        if fi < n_reg and ti_ < n_reg:
            trans[fi, ti_] += 1
    trans = trans / trans.sum(axis=1, keepdims=True)

    # 마지막 국면에서 시작
    init_reg = int(regimes[-1]) if len(regimes) else 0
    reg_ids = list(range(n_reg))

    sim_r = np.empty((n_sim, horizon))
    for i in range(n_sim):
        reg = min(init_reg, n_reg - 1)
        for t in range(horizon):
            p = params[reg]
            sim_r[i, t] = rng.normal(p["mu"], p["sd"])
            reg = int(rng.choice(reg_ids, p=trans[reg]))
    return sim_r


# ── 메인 공개 함수 ─────────────────────────────────────────────────────────
def monte_carlo(returns: pd.Series,
                n_sim: int = 10_000,
                horizon: int = 252,
                method: str = "block_bootstrap",
                seed: int = 42,
                regimes: Optional[np.ndarray] = None,
                block_size: Optional[int] = None) -> Dict:
    """
    기관급 몬테카를로 시뮬레이션.

    Parameters
    ----------
    returns      : 과거 일별 수익률
    n_sim        : 시뮬레이션 횟수 (기본 10,000)
    horizon      : 예측 기간 (영업일)
    method       : 'bootstrap' | 'parametric' | 'block_bootstrap'
                   | 'garch' | 'jump_diffusion' | 'regime'
    regimes      : regime 방법 사용 시 외부 국면 배열 (선택)
    block_size   : block_bootstrap 블록 크기 (None=자동)
    """
    rng = np.random.default_rng(seed)
    r = returns.dropna().values
    if len(r) < 10:
        return {}

    method_used = method

    if method == "bootstrap":
        sim_r = _iid_bootstrap(r, n_sim, horizon, rng)

    elif method == "parametric":
        sim_r = _parametric(r, n_sim, horizon, rng)

    elif method == "block_bootstrap":
        sim_r = _block_bootstrap(r, n_sim, horizon, rng, block_size)

    elif method == "garch":
        sim_r, method_used = _garch_mc(r, n_sim, horizon, rng)

    elif method == "jump_diffusion":
        sim_r = _jump_diffusion(r, n_sim, horizon, rng)

    elif method == "regime":
        sim_r = _regime_mc(r, n_sim, horizon, rng, regimes)

    else:
        # 알 수 없는 방법 → block_bootstrap 폴백
        sim_r = _block_bootstrap(r, n_sim, horizon, rng, block_size)
        method_used = "block_bootstrap_fallback"

    result = _extract_stats(sim_r)
    result["method_used"] = method_used
    result["n_sim"] = n_sim
    return result


# ── 앙상블 MC (4종 평균) ───────────────────────────────────────────────────
def monte_carlo_ensemble(returns: pd.Series,
                         n_sim: int = 10_000,
                         horizon: int = 252,
                         seed: int = 42,
                         regimes: Optional[np.ndarray] = None) -> Dict:
    """
    4종 MC 방법을 동시 실행해 VaR/CVaR 앙상블 평균 반환.
    Axioma '풀 재평가' 방식에 가장 근접한 형태.
    """
    methods = ["block_bootstrap", "garch", "jump_diffusion", "regime"]
    results = {}
    all_terminals = []
    all_maxdds = []

    for i, m in enumerate(methods):
        try:
            res = monte_carlo(returns, n_sim=n_sim // len(methods),
                              horizon=horizon, method=m,
                              seed=seed + i, regimes=regimes)
            if res:
                results[m] = res
                all_terminals.append(res["terminal_pnl"])
                all_maxdds.append(res["maxdd_distribution"])
        except Exception:
            pass

    if not all_terminals:
        return monte_carlo(returns, n_sim=n_sim, horizon=horizon,
                           method="block_bootstrap", seed=seed)

    combined = np.concatenate(all_terminals)
    combined_mdd = np.concatenate(all_maxdds)
    q05 = np.quantile(combined, 0.05)
    tail = combined[combined <= q05]

    # 앙상블 경로 (중앙값 대표)
    rep = results.get("block_bootstrap") or list(results.values())[0]

    return {
        "simulated_equity":   rep["simulated_equity"],
        "terminal_pnl":       combined,
        "var":                float(-q05),
        "cvar":               float(-tail.mean()) if len(tail) else 0.0,
        "maxdd_distribution": combined_mdd,
        "ci_low":             rep["ci_low"],
        "ci_high":            rep["ci_high"],
        "median_path":        rep["median_path"],
        "method_used":        "ensemble:" + "+".join(results.keys()),
        "n_sim":              len(combined),
        "per_method":         {k: {"var": v["var"], "cvar": v["cvar"]}
                               for k, v in results.items()},
    }
