# ==============================================================================
# [05/25] regime.py — Statistical Jump Model · 경제적 국면 명명
# ==============================================================================

"""
jiqtx.regime — Statistical Jump Model 기반 레짐 식별.

원본 리포트의 K-means(k=3, 라벨 0·1·2)를 대체한다.
K-means의 문제:
  (1) 지속성 강제가 없어 레짐이 하루 단위로 튄다 → 오경보
  (2) 라벨 번호에 순서·강약 의미가 없어 경제적 해석 불가

Statistical Jump Model (Bemporad et al. 2018; Nystrup et al. 2020/2021)은
전환마다 점프 페널티 λ를 부과해 지속성을 명시적으로 강제한다.
Shu-Yu-Mulvey (Journal of Asset Management 2024)는 미·독·일 지수 1990-2023에서
거래비용·체결지연을 반영한 뒤에도 JM 기반 전략이 HMM 및 buy&hold 대비
변동성·최대낙폭을 줄이고 위험조정수익을 개선했다고 보고한다.

본 구현
-------
- 목적함수 : Σ_t ||y_t − μ_{s_t}||²  +  λ·Σ_t 1[s_t ≠ s_{t−1}]
- 최적화   : 좌표하강 (상태 고정→중심 갱신, 중심 고정→DP로 상태열 최적화)
- 라벨링   : 반드시 경제적 이름 부여. 번호만 출력하는 것을 금지한다.
- 확률화   : 소프트 할당(거리 기반 softmax)으로 레짐 확률 벡터 산출 → 사이징 연결
"""

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd


# ---------------------------------------------------------------- 피처

def regime_features(r: np.ndarray, ann: int = 252) -> Tuple[np.ndarray, List[str]]:
    """
    Nystrup et al. 계열 피처: 수익률과 리스크의 다중 시간축 요약.
    전부 수익률 시계열에서만 파생 (외부 데이터 불필요).
    """
    r = np.asarray(r, float)
    n = len(r)

    def ewm(x, hl):
        a = 1 - math.exp(math.log(0.5) / hl)
        out = np.empty(len(x))
        s = x[0] if np.isfinite(x[0]) else 0.0
        for t in range(len(x)):
            v = x[t] if np.isfinite(x[t]) else s
            s = a * v + (1 - a) * s
            out[t] = s
        return out

    absr = np.abs(r)
    down = np.where(r < 0, r, 0.0)
    feats = {
        "ret_hl5":    ewm(r, 5),
        "ret_hl21":   ewm(r, 21),
        "ret_hl63":   ewm(r, 63),
        "absret_hl5": ewm(absr, 5),
        "absret_hl21": ewm(absr, 21),
        "absret_hl63": ewm(absr, 63),
        "down_hl21":  ewm(np.abs(down), 21),
    }
    names = list(feats.keys())
    X = np.column_stack([feats[k] for k in names])
    # 표준화 (확장 윈도우가 아닌 전체 — 탐색적 용도. 실운영은 확장 윈도우 권장)
    mu = np.nanmean(X, axis=0)
    sd = np.nanstd(X, axis=0, ddof=1)
    sd = np.where(sd > 0, sd, 1.0)
    Z = (X - mu) / sd
    Z = np.nan_to_num(Z)
    return Z, names


# ---------------------------------------------------------------- 적합

@dataclass
class RegimeFit:
    states: np.ndarray                # 정수 상태열
    centers: np.ndarray               # (K, F)
    probs: np.ndarray                 # (T, K) 소프트 확률
    jump_penalty: float
    n_states: int
    labels: Dict[int, str]            # 경제적 이름
    stats: pd.DataFrame               # 레짐별 요약통계
    transition: np.ndarray            # (K,K) 경험적 전이확률
    expected_duration: Dict[int, float]
    current_state: int
    current_probs: Dict[str, float]
    n_switches: int
    objective: float


def _viterbi_jump(D: np.ndarray, lam: float) -> np.ndarray:
    """D: (T,K) 각 시점의 상태별 거리. 점프 페널티 lam 하 최적 상태열 (DP)."""
    T, K = D.shape
    V = np.empty((T, K))
    P = np.zeros((T, K), dtype=int)
    V[0] = D[0]
    # 전이비용 행렬: J[j,k] = lam * 1[j != k]
    J = lam * (1.0 - np.eye(K))
    for t in range(1, T):
        cost = V[t - 1][:, None] + J          # (K_from, K_to)
        j = np.argmin(cost, axis=0)
        P[t] = j
        V[t] = D[t] + cost[j, np.arange(K)]
    s = np.empty(T, dtype=int)
    s[-1] = int(np.argmin(V[-1]))
    for t in range(T - 2, -1, -1):
        s[t] = P[t + 1, s[t + 1]]
    return s


def fit_jump_model(X: np.ndarray, n_states: int = 3, jump_penalty: float = 20.0,
                   n_init: int = 5, max_iter: int = 40,
                   seed: int = 0) -> Tuple[np.ndarray, np.ndarray, float]:
    """좌표하강 적합. 반환: (states, centers, objective)"""
    X = np.asarray(X, float)
    T, F = X.shape
    rng = np.random.default_rng(seed)
    best = (None, None, np.inf)
    for _ in range(n_init):
        idx = rng.choice(T, size=n_states, replace=False)
        C = X[idx].copy()
        prev_obj = np.inf
        s = np.zeros(T, dtype=int)
        for _ in range(max_iter):
            D = ((X[:, None, :] - C[None, :, :]) ** 2).sum(axis=2)
            s = _viterbi_jump(D, jump_penalty)
            for k in range(n_states):
                m = s == k
                if m.sum() > 0:
                    C[k] = X[m].mean(axis=0)
            obj = float(D[np.arange(T), s].sum()
                        + jump_penalty * np.sum(s[1:] != s[:-1]))
            if abs(prev_obj - obj) < 1e-8:
                break
            prev_obj = obj
        if prev_obj < best[2]:
            best = (s.copy(), C.copy(), prev_obj)
    return best


def select_jump_penalty(X: np.ndarray, n_states: int = 3,
                        grid: Optional[List[float]] = None,
                        target_switch_rate: float = 0.02,
                        seed: int = 0) -> float:
    """
    λ 선택. 전환율(스위치/관측)이 목표 근방이 되도록 고른다.
    (실운영에서는 전략성과 기반 시계열 CV로 직접 최적화하는 것이 원논문 권장 방식)
    """
    if grid is None:
        grid = [5, 20, 60, 160, 400]
    T = len(X)
    best_lam, best_gap = grid[0], np.inf
    for lam in grid:
        s, _, _ = fit_jump_model(X, n_states, float(lam), n_init=2, max_iter=25, seed=seed)
        rate = float(np.mean(s[1:] != s[:-1]))
        gap = abs(rate - target_switch_rate)
        if gap < best_gap:
            best_lam, best_gap = float(lam), gap
    return best_lam


# ---------------------------------------------------------------- 경제적 라벨

def _label_regimes(r: np.ndarray, states: np.ndarray, ann: int = 252,
                   macro: Optional[pd.DataFrame] = None) -> Tuple[Dict[int, str], pd.DataFrame]:
    """
    레짐에 반드시 경제적 이름을 붙인다.
    수익률 방향 × 변동성 수준의 2×2(+중간)로 명명하고, 매크로 중심값을 병기한다.
    """
    ks = sorted(set(states.tolist()))
    rows = []
    for k in ks:
        m = states == k
        rk = r[m]
        rk = rk[np.isfinite(rk)]
        if len(rk) < 5:
            rows.append({"state": k, "n": int(m.sum()), "share": float(m.mean()),
                         "mean_ann": np.nan, "vol_ann": np.nan, "sharpe": np.nan,
                         "hit_rate": np.nan, "skew": np.nan, "worst_day": np.nan})
            continue
        mean_ann = float(rk.mean() * ann)
        vol_ann = float(rk.std(ddof=1) * math.sqrt(ann))
        row = {"state": k, "n": int(m.sum()), "share": float(m.mean()),
               "mean_ann": mean_ann, "vol_ann": vol_ann,
               "sharpe": float(mean_ann / vol_ann) if vol_ann > 0 else np.nan,
               "hit_rate": float(np.mean(rk > 0)),
               "skew": float(pd.Series(rk).skew()),
               "worst_day": float(rk.min())}
        if macro is not None and len(macro) == len(states):
            for c in macro.columns:
                v = macro[c].values[m]
                v = v[np.isfinite(v)]
                row[f"macro_{c}"] = float(np.median(v)) if len(v) else np.nan
        rows.append(row)
    df = pd.DataFrame(rows)

    vols = df["vol_ann"].values
    means = df["mean_ann"].values
    vmed = np.nanmedian(vols)
    labels: Dict[int, str] = {}
    for i, k in enumerate(df["state"].values):
        hi_vol = vols[i] > vmed * 1.15
        lo_vol = vols[i] < vmed * 0.85
        vtag = "고변동" if hi_vol else ("저변동" if lo_vol else "중변동")
        if means[i] > 0.05:
            dtag = "상승"
        elif means[i] < -0.05:
            dtag = "하락"
        else:
            dtag = "횡보"
        labels[int(k)] = f"{vtag} {dtag}"
    # 동명 중복 방지
    seen: Dict[str, int] = {}
    for k in list(labels):
        base = labels[k]
        if base in seen.values():
            labels[k] = f"{base}#{k}"
        seen[k] = labels[k]
    df["label"] = df["state"].map(labels)
    return labels, df


def _transition_matrix(states: np.ndarray, K: int) -> np.ndarray:
    M = np.zeros((K, K))
    for a, b in zip(states[:-1], states[1:]):
        M[a, b] += 1
    rs = M.sum(axis=1, keepdims=True)
    return np.divide(M, np.where(rs > 0, rs, 1.0))


def detect_regimes(r: np.ndarray, n_states: int = 3, ann: int = 252,
                   jump_penalty: Optional[float] = None,
                   macro: Optional[pd.DataFrame] = None,
                   seed: int = 0) -> RegimeFit:
    X, _ = regime_features(r, ann)
    lam = jump_penalty if jump_penalty is not None else select_jump_penalty(
        X, n_states, seed=seed)
    states, C, obj = fit_jump_model(X, n_states, lam, seed=seed)

    D = ((X[:, None, :] - C[None, :, :]) ** 2).sum(axis=2)
    with np.errstate(over="ignore"):
        P = np.exp(-0.5 * (D - D.min(axis=1, keepdims=True)))
    P = P / P.sum(axis=1, keepdims=True)

    labels, stats_df = _label_regimes(r, states, ann, macro)
    K = C.shape[0]
    Tm = _transition_matrix(states, K)
    dur = {int(k): float(1.0 / max(1 - Tm[k, k], 1e-6)) for k in range(K)}
    cur = int(states[-1])
    curp = {labels[int(k)]: float(P[-1, k]) for k in range(K)}

    return RegimeFit(states=states, centers=C, probs=P, jump_penalty=lam,
                     n_states=K, labels=labels, stats=stats_df, transition=Tm,
                     expected_duration=dur, current_state=cur,
                     current_probs=curp,
                     n_switches=int(np.sum(states[1:] != states[:-1])),
                     objective=obj)


def regime_conditional_beta(r_asset: np.ndarray, r_factor: np.ndarray,
                            states: np.ndarray) -> pd.DataFrame:
    """레짐별 팩터 베타. '정적 베타 금지' 원칙의 구현."""
    rows = []
    for k in sorted(set(states.tolist())):
        m = (states == k) & np.isfinite(r_asset) & np.isfinite(r_factor)
        if m.sum() < 40:
            rows.append({"state": k, "n": int(m.sum()), "beta": np.nan,
                         "r2": np.nan, "corr": np.nan})
            continue
        x, y = r_factor[m], r_asset[m]
        b = float(np.cov(x, y, ddof=1)[0, 1] / np.var(x, ddof=1))
        c = float(np.corrcoef(x, y)[0, 1])
        rows.append({"state": k, "n": int(m.sum()), "beta": b,
                     "r2": c ** 2, "corr": c})
    return pd.DataFrame(rows)
