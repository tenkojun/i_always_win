# ==============================================================================
# [02/25] statcore.py — PSR/DSR · Purged CV · CPCV/PBO · Murphy · Conformal · MCS
# ==============================================================================

"""
jiqtx.statcore — 검증·다중검정·확률보정 엔진.

여기가 Plutus와 기존 리포트의 결정적 차이다.
기존: DSR 84%·과적합 갭 49%p를 '보고'하고 감점 후 점수 출력.
Plutus: 동일 상황에서 해당 모듈 출력을 '무효화(abstain)'.

구현
----
- PSR / DSR         : Bailey & López de Prado (JPM 2014)
- Purged K-Fold     : López de Prado (2018) — 라벨 중첩 제거 + 엠바고
- CPCV / PBO(CSCV)  : 조합적 퍼지 CV → 백테스트 경로 다수 → 과적합 확률
- Brier / Murphy    : Murphy (1973) 3분해. Resolution ≈ 0 이면 예측력 없음.
- 확률보정          : isotonic (sklearn) + reliability diagram
- Conformal         : ACI (Gibbs & Candès 2021) — 시계열 비교환성 대응
- 커버리지 검정     : Kupiec UC, Christoffersen IND/CC
- 분위회귀          : 하방 베타 추정용 (외부 의존 없음, scipy 최적화)
"""

import itertools
import math
from dataclasses import dataclass, field, asdict
from typing import Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd
from scipy import stats, optimize

try:
    from sklearn.isotonic import IsotonicRegression
    _HAS_SK = True
except Exception:                                    # pragma: no cover
    _HAS_SK = False


# ================================================================ 샤프 통계

def sharpe_ratio(r: np.ndarray, rf_daily: float = 0.0, ann: int = 252) -> float:
    r = np.asarray(r, float)
    r = r[np.isfinite(r)]
    if len(r) < 20:
        return np.nan
    ex = r - rf_daily
    sd = ex.std(ddof=1)
    return float(ex.mean() / sd * math.sqrt(ann)) if sd > 0 else np.nan


def probabilistic_sharpe(sr_hat: float, n: int, skew: float, kurt: float,
                         sr_benchmark: float = 0.0, ann: int = 252) -> float:
    """
    PSR: 관측 샤프가 기준 샤프를 실제로 상회할 확률.
    sr_hat, sr_benchmark 는 연율화 값 → 내부에서 기간 샤프로 환산.
    kurt = 초과첨도가 아니라 '첨도'(정규=3).
    """
    if not np.isfinite(sr_hat) or n < 20:
        return np.nan
    sr = sr_hat / math.sqrt(ann)
    srb = sr_benchmark / math.sqrt(ann)
    denom = 1.0 - skew * sr + (kurt - 1.0) / 4.0 * sr ** 2
    if denom <= 0:
        return np.nan
    z = (sr - srb) * math.sqrt(n - 1) / math.sqrt(denom)
    return float(stats.norm.cdf(z))


def expected_max_sharpe(n_trials: int, sr_var: float) -> float:
    """
    N회 시행에서 '순전히 우연으로' 기대되는 최대 샤프 (기간 단위).
    Bailey-LdP: E[max SR] ≈ sqrt(V) * ((1-γ)Z⁻¹(1-1/N) + γZ⁻¹(1-1/(N·e)))
    """
    N = max(int(n_trials), 2)
    g = 0.5772156649015329                       # Euler-Mascheroni
    z1 = stats.norm.ppf(1.0 - 1.0 / N)
    z2 = stats.norm.ppf(1.0 - 1.0 / (N * math.e))
    return float(math.sqrt(max(sr_var, 0.0)) * ((1 - g) * z1 + g * z2))


def deflated_sharpe(sr_hat: float, n: int, skew: float, kurt: float,
                    n_trials: int, sr_trials_var: Optional[float] = None,
                    ann: int = 252) -> Dict[str, float]:
    """
    DSR: 시행횟수·비정규성·표본길이를 보정한 샤프 유의확률.

    n_trials 를 모르면 DSR은 의미가 없다. 반드시 로깅할 것.
    sr_trials_var: 시도된 전략들의 (기간)샤프 분산. 없으면 보수적 기본값 사용.
    """
    if sr_trials_var is None:
        # 보수적 기본값: 연 0.5 샤프 산포 → 기간 분산
        sr_trials_var = (0.5 / math.sqrt(ann)) ** 2
    sr0_period = expected_max_sharpe(n_trials, sr_trials_var)
    sr0_ann = sr0_period * math.sqrt(ann)
    dsr = probabilistic_sharpe(sr_hat, n, skew, kurt, sr_benchmark=sr0_ann, ann=ann)
    return {"dsr": dsr, "sr_threshold_ann": sr0_ann, "n_trials": float(n_trials)}


def min_track_record_length(sr_hat: float, skew: float, kurt: float,
                            sr_benchmark: float = 0.0, conf: float = 0.95,
                            ann: int = 252) -> float:
    """목표 신뢰수준에서 샤프 유의성을 확보하는 데 필요한 최소 관측 수."""
    if not np.isfinite(sr_hat):
        return np.nan
    sr = sr_hat / math.sqrt(ann)
    srb = sr_benchmark / math.sqrt(ann)
    if sr <= srb:
        return np.inf
    z = stats.norm.ppf(conf)
    denom = 1.0 - skew * sr + (kurt - 1.0) / 4.0 * sr ** 2
    return float(1.0 + denom * (z / (sr - srb)) ** 2)


# ================================================================ Purged CV

def _label_spans(index_len: int, horizon: int) -> np.ndarray:
    """각 관측의 라벨이 끝나는 인덱스(중첩 구간)."""
    end = np.arange(index_len) + horizon
    return np.minimum(end, index_len - 1)


def purged_kfold_indices(n: int, n_splits: int, horizon: int,
                         embargo_frac: float = 0.01) -> List[Tuple[np.ndarray, np.ndarray]]:
    """
    Purged K-Fold + Embargo.
    라벨이 시간적으로 중첩되므로 테스트 구간과 겹치는 학습 표본을 제거(purge)하고,
    테스트 직후 구간을 엠바고한다.
    """
    ends = _label_spans(n, horizon)
    folds = np.array_split(np.arange(n), n_splits)
    emb = int(math.ceil(n * embargo_frac))
    out = []
    for f in folds:
        t0, t1 = f[0], f[-1]
        test = np.arange(t0, t1 + 1)
        keep = np.ones(n, dtype=bool)
        keep[test] = False
        # purge: 학습 라벨이 테스트 구간과 겹치면 제거
        overlap = (np.arange(n) <= t1) & (ends >= t0)
        keep[overlap] = False
        # embargo: 테스트 직후
        keep[t1 + 1: min(t1 + 1 + emb, n)] = False
        train = np.where(keep)[0]
        if len(train) > 30 and len(test) > 5:
            out.append((train, test))
    return out


def cpcv_splits(n: int, n_groups: int = 12, k_test: int = 2,
                horizon: int = 21, embargo_frac: float = 0.01
                ) -> Tuple[List[Tuple[np.ndarray, np.ndarray]], int]:
    """
    Combinatorial Purged CV.
    C(N,k) 개 분할 → k·C(N,k)/N 개 독립 백테스트 경로.
    예) N=12,k=2 → 66분할 → 11경로.
    """
    groups = np.array_split(np.arange(n), n_groups)
    ends = _label_spans(n, horizon)
    emb = int(math.ceil(n * embargo_frac))
    splits = []
    for combo in itertools.combinations(range(n_groups), k_test):
        test = np.concatenate([groups[i] for i in combo])
        keep = np.ones(n, dtype=bool)
        keep[test] = False
        for i in combo:
            t0, t1 = groups[i][0], groups[i][-1]
            overlap = (np.arange(n) <= t1) & (ends >= t0)
            keep[overlap] = False
            keep[t1 + 1: min(t1 + 1 + emb, n)] = False
        train = np.where(keep)[0]
        if len(train) > 50 and len(test) > 10:
            splits.append((train, np.sort(test)))
    n_paths = k_test * math.comb(n_groups, k_test) // n_groups
    return splits, n_paths


def pbo_cscv(perf_matrix: np.ndarray, n_groups: int = 12) -> Dict[str, float]:
    """
    Probability of Backtest Overfitting (CSCV).

    perf_matrix : (T, S) — 시점별 × 전략별 성과(수익률 등)
    절차: 시간축을 N블록으로 분할 → 절반을 IS, 나머지를 OOS로 하는 모든 조합에서
          IS 최우수 전략의 OOS 상대순위를 구하고, 그 logit 분포에서 P(logit<0).

    PBO ≥ 0.5 → 선택 절차가 동전던지기. 전략 폐기.
    """
    M = np.asarray(perf_matrix, float)
    T, S = M.shape
    if S < 2 or T < n_groups * 4:
        return {"pbo": np.nan, "n_combinations": 0.0, "median_logit": np.nan}

    blocks = np.array_split(np.arange(T), n_groups)
    half = n_groups // 2
    logits = []
    for combo in itertools.combinations(range(n_groups), half):
        is_idx = np.concatenate([blocks[i] for i in combo])
        oos_idx = np.concatenate([blocks[i] for i in range(n_groups) if i not in combo])
        is_perf = np.nanmean(M[is_idx, :], axis=0)
        oos_perf = np.nanmean(M[oos_idx, :], axis=0)
        if not np.isfinite(is_perf).any():
            continue
        best = int(np.nanargmax(is_perf))
        # OOS 상대순위 (0~1)
        ranks = stats.rankdata(oos_perf)
        w = ranks[best] / (S + 1.0)
        w = min(max(w, 1e-6), 1 - 1e-6)
        logits.append(math.log(w / (1.0 - w)))
    if not logits:
        return {"pbo": np.nan, "n_combinations": 0.0, "median_logit": np.nan}
    logits = np.array(logits)
    return {"pbo": float(np.mean(logits <= 0.0)),
            "n_combinations": float(len(logits)),
            "median_logit": float(np.median(logits))}


# ================================================================ 확률 품질

def brier_score(p: np.ndarray, y: np.ndarray) -> float:
    p, y = np.asarray(p, float), np.asarray(y, float)
    ok = np.isfinite(p) & np.isfinite(y)
    return float(np.mean((p[ok] - y[ok]) ** 2)) if ok.sum() else np.nan


def murphy_decomposition(p: np.ndarray, y: np.ndarray, n_bins: int = 10) -> Dict[str, float]:
    """
    Brier = Reliability − Resolution + Uncertainty  (Murphy 1973)

    Resolution ≈ 0 → 모델이 기저율(base rate) 이상의 정보를 담고 있지 않다.
    이 한 숫자가 '상승확률 66%'류 출력의 유효성을 판정한다.
    """
    p, y = np.asarray(p, float), np.asarray(y, float)
    ok = np.isfinite(p) & np.isfinite(y)
    p, y = p[ok], y[ok]
    n = len(p)
    if n < 30:
        return {"brier": np.nan, "reliability": np.nan,
                "resolution": np.nan, "uncertainty": np.nan, "skill": np.nan}
    ybar = y.mean()
    edges = np.linspace(0.0, 1.0, n_bins + 1)
    idx = np.clip(np.digitize(p, edges[1:-1], right=False), 0, n_bins - 1)
    rel = res = 0.0
    for b in range(n_bins):
        m = idx == b
        nk = m.sum()
        if nk == 0:
            continue
        pk, yk = p[m].mean(), y[m].mean()
        rel += nk * (pk - yk) ** 2
        res += nk * (yk - ybar) ** 2
    rel /= n
    res /= n
    unc = ybar * (1 - ybar)
    bs = brier_score(p, y)
    skill = 1.0 - bs / unc if unc > 0 else np.nan
    return {"brier": bs, "reliability": float(rel), "resolution": float(res),
            "uncertainty": float(unc), "skill": float(skill)}


def reliability_table(p: np.ndarray, y: np.ndarray, n_bins: int = 10) -> pd.DataFrame:
    p, y = np.asarray(p, float), np.asarray(y, float)
    ok = np.isfinite(p) & np.isfinite(y)
    p, y = p[ok], y[ok]
    edges = np.linspace(0, 1, n_bins + 1)
    idx = np.clip(np.digitize(p, edges[1:-1]), 0, n_bins - 1)
    rows = []
    for b in range(n_bins):
        m = idx == b
        if m.sum() == 0:
            continue
        rows.append({"bin": f"{edges[b]:.1f}-{edges[b+1]:.1f}",
                     "n": int(m.sum()),
                     "pred_mean": float(p[m].mean()),
                     "obs_freq": float(y[m].mean())})
    return pd.DataFrame(rows)


def calibrate_isotonic(p_train: np.ndarray, y_train: np.ndarray,
                       p_apply: np.ndarray) -> np.ndarray:
    """isotonic 보정. sklearn 없으면 항등 반환."""
    if not _HAS_SK:
        return np.asarray(p_apply, float)
    ok = np.isfinite(p_train) & np.isfinite(y_train)
    if ok.sum() < 50:
        return np.asarray(p_apply, float)
    ir = IsotonicRegression(y_min=0.0, y_max=1.0, out_of_bounds="clip")
    ir.fit(np.asarray(p_train)[ok], np.asarray(y_train)[ok])
    return np.clip(ir.predict(np.asarray(p_apply, float)), 1e-6, 1 - 1e-6)


# ================================================================ Conformal

@dataclass
class ACIResult:
    lower: np.ndarray
    upper: np.ndarray
    alpha_path: np.ndarray
    empirical_coverage: float
    target_coverage: float
    coverage_gap: float


def adaptive_conformal(resid_stream: np.ndarray, scale_stream: np.ndarray,
                       target: float = 0.90, gamma: float = 0.01,
                       warmup: int = 60) -> ACIResult:
    """
    Adaptive Conformal Inference (Gibbs & Candès 2021).

    α_{t+1} = α_t + γ(α_target − 1[miss_t])
    비교환성 시계열에서 장기 평균 커버리지를 목표로 수렴시킨다.

    주의: 최근 벤치마크에서 EnbPI/SPCI 등이 명목 커버리지에 미달하는 사례가
    보고됐다. 따라서 '실측 커버리지'를 반드시 산출물로 남긴다.

    resid_stream : 예측오차 (실현 − 예측)
    scale_stream : 예측 시점의 스케일(조건부 표준편차 추정)
    """
    r = np.asarray(resid_stream, float)
    s = np.asarray(scale_stream, float)
    n = len(r)
    a_target = 1.0 - target
    alpha = a_target
    lo = np.full(n, np.nan)
    hi = np.full(n, np.nan)
    apath = np.full(n, np.nan)
    misses = []
    pool: List[float] = []

    for t in range(n):
        if not np.isfinite(r[t]) or not np.isfinite(s[t]) or s[t] <= 0:
            apath[t] = alpha
            continue
        if len(pool) >= warmup:
            q = np.quantile(pool, min(max(1.0 - alpha, 0.01), 0.999))
        else:
            q = stats.norm.ppf(1.0 - a_target / 2.0)
        lo[t] = -q * s[t]
        hi[t] = q * s[t]
        miss = 0 if (lo[t] <= r[t] <= hi[t]) else 1
        misses.append(miss)
        alpha = float(np.clip(alpha + gamma * (a_target - miss), 1e-4, 0.5))
        apath[t] = alpha
        pool.append(abs(r[t]) / s[t])
        if len(pool) > 1000:
            pool.pop(0)

    cov = 1.0 - float(np.mean(misses[warmup:])) if len(misses) > warmup else np.nan
    return ACIResult(lo, hi, apath, cov, target,
                     (cov - target) if np.isfinite(cov) else np.nan)


# ================================================================ 커버리지 검정

def kupiec_pof(violations: np.ndarray, alpha: float) -> Dict[str, float]:
    """Kupiec 무조건부 커버리지 검정 (POF). H0: 위반율 = alpha."""
    v = np.asarray(violations, float)
    v = v[np.isfinite(v)]
    n, x = len(v), float(v.sum())
    if n < 50:
        return {"stat": np.nan, "pvalue": np.nan, "hit_rate": np.nan}
    pi = x / n
    if pi in (0.0, 1.0):
        lr = 2.0 * (0 - (x * math.log(alpha) + (n - x) * math.log(1 - alpha)))
    else:
        lr = -2.0 * ((n - x) * math.log(1 - alpha) + x * math.log(alpha)
                     - (n - x) * math.log(1 - pi) - x * math.log(pi))
    return {"stat": float(lr), "pvalue": float(1 - stats.chi2.cdf(lr, 1)),
            "hit_rate": float(pi)}


def christoffersen_ind(violations: np.ndarray) -> Dict[str, float]:
    """Christoffersen 독립성 검정. 위반의 군집(clustering) 탐지."""
    v = np.asarray(violations, int)
    if len(v) < 50:
        return {"stat": np.nan, "pvalue": np.nan}
    n00 = int(np.sum((v[:-1] == 0) & (v[1:] == 0)))
    n01 = int(np.sum((v[:-1] == 0) & (v[1:] == 1)))
    n10 = int(np.sum((v[:-1] == 1) & (v[1:] == 0)))
    n11 = int(np.sum((v[:-1] == 1) & (v[1:] == 1)))
    if (n00 + n01) == 0 or (n10 + n11) == 0:
        return {"stat": np.nan, "pvalue": np.nan}
    p01 = n01 / (n00 + n01)
    p11 = n11 / (n10 + n11)
    p = (n01 + n11) / (n00 + n01 + n10 + n11)
    def _ll(a, b, c, d, q01, q11):
        eps = 1e-12
        return (a * math.log(max(1 - q01, eps)) + b * math.log(max(q01, eps))
                + c * math.log(max(1 - q11, eps)) + d * math.log(max(q11, eps)))
    lr = -2.0 * (_ll(n00, n01, n10, n11, p, p) - _ll(n00, n01, n10, n11, p01, p11))
    return {"stat": float(lr), "pvalue": float(1 - stats.chi2.cdf(lr, 1))}


def conditional_coverage(violations: np.ndarray, alpha: float) -> Dict[str, float]:
    uc = kupiec_pof(violations, alpha)
    ind = christoffersen_ind(violations)
    if not (np.isfinite(uc["stat"]) and np.isfinite(ind["stat"])):
        return {"stat": np.nan, "pvalue": np.nan}
    s = uc["stat"] + ind["stat"]
    return {"stat": float(s), "pvalue": float(1 - stats.chi2.cdf(s, 2))}


# ================================================================ 예측 비교

def diebold_mariano(e1: np.ndarray, e2: np.ndarray, h: int = 1,
                    power: int = 2) -> Dict[str, float]:
    """Diebold-Mariano 검정 (HLN 소표본 보정 포함)."""
    e1, e2 = np.asarray(e1, float), np.asarray(e2, float)
    ok = np.isfinite(e1) & np.isfinite(e2)
    d = np.abs(e1[ok]) ** power - np.abs(e2[ok]) ** power
    n = len(d)
    if n < 30:
        return {"stat": np.nan, "pvalue": np.nan}
    dbar = d.mean()
    gamma0 = np.sum((d - dbar) ** 2) / n
    gsum = gamma0
    for k in range(1, h):
        gk = np.sum((d[k:] - dbar) * (d[:-k] - dbar)) / n
        gsum += 2 * gk
    var = gsum / n
    if var <= 0:
        return {"stat": np.nan, "pvalue": np.nan}
    dm = dbar / math.sqrt(var)
    corr = math.sqrt((n + 1 - 2 * h + h * (h - 1) / n) / n)
    dm *= corr
    return {"stat": float(dm), "pvalue": float(2 * (1 - stats.t.cdf(abs(dm), n - 1)))}


def stationary_bootstrap_idx(n: int, mean_block: float, rng: np.random.Generator,
                             size: Optional[int] = None) -> np.ndarray:
    """Politis-Romano 정상 부트스트랩 인덱스."""
    size = size or n
    p = 1.0 / max(mean_block, 1.0)
    idx = np.empty(size, dtype=int)
    i = rng.integers(0, n)
    for t in range(size):
        idx[t] = i
        if rng.random() < p:
            i = int(rng.integers(0, n))
        else:
            i = (i + 1) % n
    return idx


def spa_test(bench: np.ndarray, models: np.ndarray, n_boot: int = 1000,
             mean_block: int = 10, seed: int = 0) -> Dict[str, float]:
    """
    Hansen (2005) Superior Predictive Ability — 간이 구현.
    H0: 어떤 모델도 벤치마크를 초과하지 못한다.
    models: (T, S)
    """
    b = np.asarray(bench, float)
    M = np.asarray(models, float)
    T, S = M.shape
    d = M - b[:, None]                       # 초과성과
    dbar = np.nanmean(d, axis=0)
    sd = np.nanstd(d, axis=0, ddof=1)
    sd = np.where(sd > 0, sd, np.nan)
    tstat = np.sqrt(T) * dbar / sd
    obs = float(np.nanmax(np.concatenate([tstat, [0.0]])))

    rng = np.random.default_rng(seed)
    # Hansen 재중심화 임계값
    thr = -np.sqrt(2.0 * np.log(np.log(max(T, 3)))) * sd / np.sqrt(T)
    mu_c = np.where(dbar >= thr, dbar, 0.0)
    boots = np.empty(n_boot)
    for i in range(n_boot):
        idx = stationary_bootstrap_idx(T, mean_block, rng)
        db = np.nanmean(d[idx, :], axis=0) - mu_c
        tb = np.sqrt(T) * db / sd
        boots[i] = float(np.nanmax(np.concatenate([tb, [0.0]])))
    return {"stat": obs, "pvalue": float(np.mean(boots >= obs)),
            "n_models": float(S)}


def model_confidence_set(losses: np.ndarray, alpha: float = 0.10,
                         n_boot: int = 1000, mean_block: int = 10,
                         seed: int = 0) -> Dict[str, object]:
    """
    Hansen-Lunde-Nason (2011) MCS — 범위통계 기반 간이 구현.
    losses: (T, S) 손실. 반환: 살아남은 모델 인덱스 집합.
    """
    L = np.asarray(losses, float)
    T, S = L.shape
    alive = list(range(S))
    rng = np.random.default_rng(seed)
    boot_idx = [stationary_bootstrap_idx(T, mean_block, rng) for _ in range(n_boot)]
    removed = []
    while len(alive) > 1:
        sub = L[:, alive]
        k = len(alive)
        dij = np.zeros((k, k))
        tij = np.zeros((k, k))
        boot_t = np.zeros((n_boot, k, k))
        for a in range(k):
            for b_ in range(k):
                if a == b_:
                    continue
                d = sub[:, a] - sub[:, b_]
                dbar = np.nanmean(d)
                bs = np.array([np.nanmean(d[ix]) for ix in boot_idx])
                v = np.nanvar(bs, ddof=1)
                se = math.sqrt(v) if v > 0 else np.nan
                dij[a, b_] = dbar
                tij[a, b_] = dbar / se if se and np.isfinite(se) else 0.0
                boot_t[:, a, b_] = (bs - dbar) / se if se and np.isfinite(se) else 0.0
        Tr = float(np.nanmax(np.abs(tij)))
        boot_max = np.nanmax(np.abs(boot_t), axis=(1, 2))
        pval = float(np.mean(boot_max >= Tr))
        if pval >= alpha:
            break
        # 최악 모델 제거
        worst_local = int(np.nanargmax(np.nanmean(tij, axis=1)))
        removed.append(alive[worst_local])
        alive.pop(worst_local)
    return {"mcs_set": alive, "removed_order": removed, "alpha": alpha}


# ================================================================ 분위회귀

def quantile_regression(X: np.ndarray, y: np.ndarray, q: float = 0.5,
                        add_const: bool = True) -> np.ndarray:
    """핀볼 손실 최소화 분위회귀 (외부 의존 없음)."""
    X = np.atleast_2d(np.asarray(X, float))
    if X.shape[0] != len(y):
        X = X.T
    y = np.asarray(y, float)
    ok = np.isfinite(y) & np.isfinite(X).all(axis=1)
    X, y = X[ok], y[ok]
    if len(y) < 30:
        return np.full(X.shape[1] + (1 if add_const else 0), np.nan)
    if add_const:
        X = np.column_stack([np.ones(len(X)), X])

    def loss(b):
        r = y - X @ b
        return float(np.sum(np.where(r >= 0, q * r, (q - 1) * r)))

    b0, *_ = np.linalg.lstsq(X, y, rcond=None)
    res = optimize.minimize(loss, b0, method="Powell",
                            options={"maxiter": 20000, "xtol": 1e-8, "ftol": 1e-8})
    return res.x if res.success or np.isfinite(res.fun) else b0


def newey_west_se(X: np.ndarray, y: np.ndarray, lags: int = 5) -> Tuple[np.ndarray, np.ndarray]:
    """OLS 계수 + Newey-West HAC 표준오차 (상수항 포함)."""
    X = np.atleast_2d(np.asarray(X, float))
    if X.shape[0] != len(y):
        X = X.T
    y = np.asarray(y, float)
    ok = np.isfinite(y) & np.isfinite(X).all(axis=1)
    X, y = X[ok], y[ok]
    n = len(y)
    if n < 40:
        k = X.shape[1] + 1
        return np.full(k, np.nan), np.full(k, np.nan)
    Xc = np.column_stack([np.ones(n), X])
    XtX_inv = np.linalg.pinv(Xc.T @ Xc)
    b = XtX_inv @ Xc.T @ y
    e = y - Xc @ b
    S = (Xc * e[:, None]).T @ (Xc * e[:, None])
    for L in range(1, lags + 1):
        w = 1.0 - L / (lags + 1.0)
        A = (Xc[L:] * e[L:, None]).T @ (Xc[:-L] * e[:-L, None])
        S += w * (A + A.T)
    V = XtX_inv @ S @ XtX_inv
    return b, np.sqrt(np.maximum(np.diag(V), 0.0))


# ================================================================ 꼬리 의존성

def tail_dependence(x: np.ndarray, y: np.ndarray, q: float = 0.05) -> Dict[str, float]:
    """
    경험적 하방/상방 꼬리 의존성.
    λ_L = P(Y < F_Y⁻¹(q) | X < F_X⁻¹(q))
    정상 국면 상관과 극단 국면 상관은 다른 숫자다.
    """
    x, y = np.asarray(x, float), np.asarray(y, float)
    ok = np.isfinite(x) & np.isfinite(y)
    x, y = x[ok], y[ok]
    if len(x) < 200:
        return {"lambda_lower": np.nan, "lambda_upper": np.nan,
                "corr_all": np.nan, "corr_lower_tail": np.nan}
    xl, yl = np.quantile(x, q), np.quantile(y, q)
    xu, yu = np.quantile(x, 1 - q), np.quantile(y, 1 - q)
    mx_l, mx_u = x < xl, x > xu
    lam_l = float(np.mean(y[mx_l] < yl)) if mx_l.sum() else np.nan
    lam_u = float(np.mean(y[mx_u] > yu)) if mx_u.sum() else np.nan
    c_all = float(np.corrcoef(x, y)[0, 1])
    c_low = float(np.corrcoef(x[mx_l], y[mx_l])[0, 1]) if mx_l.sum() > 10 else np.nan
    return {"lambda_lower": lam_l, "lambda_upper": lam_u,
            "corr_all": c_all, "corr_lower_tail": c_low}
