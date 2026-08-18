"""
HRP (Hierarchical Risk Parity) 모듈
===================================
López de Prado (2016) 가 제안한 위험 균등화 방법.
공분산 행렬 역행렬을 쓰지 않아 수치 안정성이 매우 높다.

알고리즘
--------
1. 자산 간 상관계수로부터 거리 행렬 계산
2. 단일연결법(single linkage)으로 계층 클러스터링
3. 재귀 양분할(recursive bisection) 로 각 클러스터에
   역분산 가중치 분배

함께 포함
---------
- risk_parity(returns) : 단순 역변동성 가중 (HRP 의 단순 버전)
"""
from __future__ import annotations
from typing import Dict
import numpy as np
import pandas as pd
from scipy.cluster.hierarchy import linkage
from scipy.spatial.distance import squareform


def risk_parity(returns: pd.DataFrame) -> Dict[str, float]:
    """
    역변동성 가중 (간이 Risk Parity).
    각 자산에 1/σ 만큼 비중을 주어 위험 기여를 비슷하게 맞춘다.
    """
    vol = returns.std() * np.sqrt(252)
    inv = 1.0 / vol.replace(0, np.nan)
    w = inv / inv.sum()
    return w.fillna(0).to_dict()


# ------------------------------------------------------------------ #
def _correl_dist(corr: pd.DataFrame) -> pd.DataFrame:
    """correlation → distance."""
    return ((1 - corr) / 2.0) ** 0.5


def _quasi_diag(link):
    """클러스터링 결과를 준대각화 순서로 변환."""
    link = link.astype(int)
    sort_ix = pd.Series([link[-1, 0], link[-1, 1]])
    n = link[-1, 3]
    while sort_ix.max() >= n:
        sort_ix.index = range(0, sort_ix.shape[0] * 2, 2)
        df0 = sort_ix[sort_ix >= n]
        i, j = df0.index, df0.values - n
        sort_ix[i] = link[j, 0]
        df1 = pd.Series(link[j, 1], index=i + 1)
        sort_ix = pd.concat([sort_ix, df1]).sort_index()
        sort_ix.index = range(sort_ix.shape[0])
    return sort_ix.tolist()


def _ivp(cov):
    """역분산 가중."""
    ivp = 1.0 / np.diag(cov)
    return ivp / ivp.sum()


def _cluster_var(cov, items):
    """클러스터 분산 = w^T Σ w (w 는 역분산 가중)."""
    cov_ = cov.loc[items, items]
    w = _ivp(cov_).reshape(-1, 1)
    return float((w.T @ cov_.values @ w)[0, 0])


def _recursive_bisection(cov, sort_ix):
    """클러스터를 두 그룹으로 분할하며 가중치를 재귀적으로 분배."""
    w = pd.Series(1.0, index=sort_ix)
    cluster_items = [sort_ix]
    while cluster_items:
        cluster_items = [c[j:k] for c in cluster_items
                         for j, k in ((0, len(c) // 2), (len(c) // 2, len(c)))
                         if len(c) > 1]
        for i in range(0, len(cluster_items), 2):
            c0, c1 = cluster_items[i], cluster_items[i + 1]
            v0, v1 = _cluster_var(cov, c0), _cluster_var(cov, c1)
            alpha = 1 - v0 / (v0 + v1)
            w[c0] *= alpha
            w[c1] *= (1 - alpha)
    return w


def hrp(returns: pd.DataFrame) -> Dict[str, float]:
    """
    HRP 비중을 계산한다.

    Returns
    -------
    dict  {ticker: weight}
    """
    cov = returns.cov() * 252
    corr = returns.corr()
    dist = _correl_dist(corr)
    link = linkage(squareform(dist.values, checks=False), method="single")
    sort_ix = _quasi_diag(link)
    sort_ix = corr.columns[sort_ix].tolist()
    w = _recursive_bisection(cov, sort_ix)
    w = w / w.sum()
    return w.to_dict()
