"""
시계열 통계 검정 모듈
=====================
- adf_test          : 단위근 검정 (귀무가설: 비정상). p<0.05 → 정상
- kpss_test         : 정상성 검정 (귀무가설: 정상). p>0.05 → 정상
                       두 결과를 같이 보면 더 신뢰성 높음.
- cointegration_test: 두 시계열의 공적분 (페어트레이딩 적합도)
- bootstrap_metric  : 블록 부트스트랩으로 지표 안정성 측정
- walk_forward_splits : 워크포워드 분할 인덱스

statsmodels 없는 환경에서도 import 깨지지 않도록 lazy import 구현.
"""
from __future__ import annotations
from typing import Callable, Dict, List
import numpy as np
import pandas as pd

try:
    from statsmodels.tsa.stattools import adfuller, kpss, coint
    HAS_SM = True
except ImportError:
    HAS_SM = False


def adf_test(series: pd.Series) -> Dict[str, float]:
    """ADF (Augmented Dickey-Fuller) 단위근 검정."""
    if not HAS_SM:
        return {"error": "statsmodels 가 설치되지 않았습니다"}
    s = series.dropna()
    stat, p, *_ = adfuller(s, autolag="AIC")
    return {"stat": float(stat), "pvalue": float(p),
            "stationary": bool(p < 0.05)}


def kpss_test(series: pd.Series) -> Dict[str, float]:
    """KPSS 정상성 검정."""
    if not HAS_SM:
        return {"error": "statsmodels 가 설치되지 않았습니다"}
    s = series.dropna()
    stat, p, *_ = kpss(s, regression="c", nlags="auto")
    return {"stat": float(stat), "pvalue": float(p),
            "stationary": bool(p > 0.05)}


def cointegration_test(a: pd.Series, b: pd.Series) -> Dict[str, float]:
    """Engle-Granger 공적분 검정 — 페어트레이딩 적합도 확인."""
    if not HAS_SM:
        return {"error": "statsmodels 가 설치되지 않았습니다"}
    df = pd.concat([a, b], axis=1).dropna()
    stat, p, _ = coint(df.iloc[:, 0], df.iloc[:, 1])
    return {"stat": float(stat), "pvalue": float(p),
            "cointegrated": bool(p < 0.05)}


def bootstrap_metric(returns: pd.Series,
                     metric_fn: Callable[[pd.Series], float],
                     n_iter: int = 1000,
                     block: int = 20,
                     seed: int = 42) -> Dict[str, float]:
    """
    블록 부트스트랩 — 지표의 표본 변동을 측정해 신뢰구간을 만든다.

    Parameters
    ----------
    metric_fn : 수익률 시리즈 → float 함수
    n_iter    : 부트스트랩 반복수
    block     : 블록 길이 (시계열 상관 보존)
    """
    rng = np.random.default_rng(seed)
    r = returns.dropna().values
    n = len(r)
    out = []
    for _ in range(n_iter):
        sample = []
        while len(sample) < n:
            start = rng.integers(0, n)
            sample.extend(r[start : start + block])
        out.append(metric_fn(pd.Series(sample[:n])))
    out = np.array(out)
    return {
        "mean":    float(out.mean()),
        "std":     float(out.std()),
        "ci_low":  float(np.quantile(out, 0.025)),
        "ci_high": float(np.quantile(out, 0.975)),
    }


def walk_forward_splits(n: int, n_splits: int = 5,
                        train_size: float = 0.7) -> List[tuple]:
    """
    워크포워드 검증용 (train_idx, test_idx) 리스트를 만든다.
    """
    out = []
    fold = n // (n_splits + 1)
    for k in range(n_splits):
        end_train = int(fold * (k + 1) * (1 + train_size) / 2)
        end_test = end_train + fold
        if end_test > n:
            break
        out.append((np.arange(0, end_train),
                    np.arange(end_train, end_test)))
    return out
