"""
CSCV / PBO — 백테스트 과적합 확률 (Bailey-Borwein-López de Prado-Zhu 2015)
==========================================================================
Combinatorially Symmetric Cross-Validation (CSCV) 로
PBO(Probability of Backtest Overfitting)를 산출한다.

알고리즘
--------
1. 수익률 행렬 (T×N): T=시간, N=전략 변형(파라미터 그리드)
2. T를 S개 등간격 그룹으로 분할
3. 모든 C(S, S/2) 조합 생성
4. 각 조합: IS=절반 그룹, OOS=나머지 절반
5. IS에서 Sharpe 최고 전략 선택 → OOS에서 해당 전략의 상대 순위 기록
6. PBO = 순위 < 0.5인 비율 (IS 최고가 OOS에서 중앙 미달)

실용 사용법 (단일 전략)
-----------------------
signal_matrix를 파라미터 변형(룩백, 임계값 등)으로 생성해
CSCV에 넣으면 해당 전략군의 PBO가 나온다.

출력
----
pbo             : 과적합 확률 (0~1, 낮을수록 좋음)
n_combinations  : 검증에 사용한 조합 수
logit_ranks     : 상대순위의 logit 분포
is_oos_corr     : IS vs OOS Sharpe 상관 (높을수록 좋음)
degradation     : 평균 IS Sharpe 대비 OOS Sharpe 감쇠율
interpretation  : 한글 해석 문자열
"""
from __future__ import annotations
from itertools import combinations
from typing import Dict, Optional

import numpy as np
import pandas as pd


def _sharpe(returns: np.ndarray, annualize: int = 252) -> np.ndarray:
    """열별 Sharpe (분모 0 보호)."""
    mu = returns.mean(axis=0)
    sd = returns.std(axis=0, ddof=1)
    sd = np.where(sd < 1e-12, 1e-12, sd)
    return mu / sd * np.sqrt(annualize)


def build_signal_matrix(returns: pd.Series,
                        lookbacks: Optional[list] = None,
                        thresholds: Optional[list] = None) -> np.ndarray:
    """
    단일 종목 수익률에서 파라미터 그리드 신호행렬 생성.

    각 열 = 특정 (룩백, 임계) 조합의 일별 전략 수익률.
    신호: 과거 룩백 평균수익률 > 임계 → +1 (매수), 아니면 -1 (매도)
    다음 날 실현수익률 × 신호 = 전략 수익률.
    """
    if lookbacks is None:
        lookbacks = [5, 10, 20, 40, 60, 120]
    if thresholds is None:
        thresholds = [0.0, 0.0005, 0.001]

    r = returns.values
    T = len(r)
    cols = []
    for lb in lookbacks:
        for thr in thresholds:
            sig = np.zeros(T)
            for t in range(lb, T):
                past_mean = r[max(0, t - lb):t].mean()
                sig[t] = 1.0 if past_mean > thr else -1.0
            # 신호는 당일 종가 → 다음날 수익률에 적용
            strat = np.roll(sig, 1) * r
            strat[:lb + 1] = 0.0
            cols.append(strat)

    if not cols:
        return r.reshape(-1, 1)
    return np.column_stack(cols)


def cscv(signal_matrix: np.ndarray,
         n_groups: int = 16,
         annualize: int = 252) -> Dict:
    """
    CSCV 핵심 루틴.

    Parameters
    ----------
    signal_matrix : (T, N) 전략별 일별 수익률
    n_groups      : 분할 그룹 수 S (짝수, 기본 16 → C(16,8)=12870 조합)
    annualize     : 연환산 인수 (일별=252)
    """
    T, N = signal_matrix.shape
    S = n_groups if n_groups % 2 == 0 else n_groups - 1
    S = max(4, min(S, T // 10))       # 그룹당 최소 10개 관측 보장

    groups = np.array_split(np.arange(T), S)
    half = S // 2
    all_combs = list(combinations(range(S), half))

    # 조합이 너무 많으면 균일 샘플링 (최대 5000)
    max_combs = 5000
    if len(all_combs) > max_combs:
        rng = np.random.default_rng(42)
        idx = rng.choice(len(all_combs), size=max_combs, replace=False)
        all_combs = [all_combs[i] for i in idx]

    pbo_hits = 0
    logit_ranks, is_sh_list, oos_sh_list = [], [], []

    for comb in all_combs:
        oos_set = set(range(S)) - set(comb)
        is_idx  = np.concatenate([groups[i] for i in comb])
        oos_idx = np.concatenate([groups[i] for i in oos_set])

        is_r  = signal_matrix[is_idx, :]
        oos_r = signal_matrix[oos_idx, :]

        is_sh  = _sharpe(is_r,  annualize)
        oos_sh = _sharpe(oos_r, annualize)

        best = int(np.argmax(is_sh))
        oos_all = oos_sh
        rank = float((oos_all < oos_all[best]).sum()) / max(N - 1, 1)

        eps = 1e-6
        rank_c = np.clip(rank, eps, 1 - eps)
        logit_ranks.append(np.log(rank_c / (1 - rank_c)))

        is_sh_list.append(float(is_sh[best]))
        oos_sh_list.append(float(oos_all[best]))

        if rank < 0.5:
            pbo_hits += 1

    pbo = pbo_hits / len(all_combs)
    logit_arr = np.array(logit_ranks)
    is_arr  = np.array(is_sh_list)
    oos_arr = np.array(oos_sh_list)

    # IS vs OOS 상관
    mask = np.isfinite(is_arr) & np.isfinite(oos_arr)
    if mask.sum() > 5:
        corr = float(np.corrcoef(is_arr[mask], oos_arr[mask])[0, 1])
    else:
        corr = 0.0

    # 감쇠율 (IS 대비 OOS 하락 비율)
    is_mean  = float(np.nanmean(is_arr))
    oos_mean = float(np.nanmean(oos_arr))
    degradation = 1.0 - (oos_mean / is_mean) if abs(is_mean) > 1e-6 else 0.0

    # 한글 해석
    if pbo < 0.25:
        interp = f"PBO {pbo:.1%} — 과적합 위험 낮음. 전략의 OOS 성과가 안정적."
    elif pbo < 0.50:
        interp = f"PBO {pbo:.1%} — 과적합 위험 보통. OOS 성과가 IS 대비 일부 하락."
    elif pbo < 0.75:
        interp = f"PBO {pbo:.1%} — 과적합 위험 높음. IS 최고 전략이 OOS에서 중앙 미달 빈번."
    else:
        interp = f"PBO {pbo:.1%} — 과적합 확실. IS 성과는 OOS와 무관. 결과 신뢰 불가."

    return {
        "pbo":            round(pbo, 4),
        "n_combinations": len(all_combs),
        "n_strategies":   N,
        "n_groups":       S,
        "logit_ranks":    logit_arr,
        "logit_mean":     round(float(np.nanmean(logit_arr)), 4),
        "is_sharpe_mean": round(is_mean, 4),
        "oos_sharpe_mean": round(oos_mean, 4),
        "is_oos_corr":    round(corr, 4),
        "degradation":    round(degradation, 4),
        "interpretation": interp,
    }


def run_cscv_from_returns(returns: pd.Series,
                          lookbacks: Optional[list] = None,
                          thresholds: Optional[list] = None,
                          n_groups: int = 16) -> Dict:
    """
    편의 함수: 수익률 시리즈 → 자동 파라미터 그리드 생성 → CSCV 실행.
    """
    if len(returns.dropna()) < 60:
        return {
            "pbo": None, "interpretation": "데이터 부족 (60일 미만)으로 CSCV 미실행.",
            "n_combinations": 0, "is_sharpe_mean": None, "oos_sharpe_mean": None,
            "is_oos_corr": None, "degradation": None,
        }
    mat = build_signal_matrix(returns.dropna(), lookbacks, thresholds)
    return cscv(mat, n_groups=n_groups)
