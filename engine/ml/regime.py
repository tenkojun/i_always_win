"""
시장 국면 (Regime) 탐지 모듈
============================
수익률 시계열을 학습하여 시장을 N개 국면(예: 하락/보통/상승)으로
분류한다.

지원 방법
---------
- 'hmm'    : Hidden Markov Model — 상태 간 전이확률 학습. hmmlearn 필요
- 'kmeans' : KMeans 클러스터링   — 가장 빠르고 안정적
- 'gmm'    : Gaussian Mixture Model

피처
----
- 수익률
- 20일 이동 변동성
- 절대수익률
세 가지를 표준화 후 사용한다.
"""
from __future__ import annotations
from typing import Optional
import numpy as np
import pandas as pd
from sklearn.cluster import KMeans
from sklearn.mixture import GaussianMixture
from sklearn.preprocessing import StandardScaler

try:
    from hmmlearn import hmm as _hmm
    HAS_HMM = True
except ImportError:
    HAS_HMM = False


def _featurize(returns: pd.Series, vol_window: int = 20) -> pd.DataFrame:
    """수익률 / 변동성 / 절대수익률 피처를 만든다."""
    rets = returns.fillna(0)
    df = pd.DataFrame({
        "ret":  rets,
        "vol":  rets.rolling(vol_window).std(),
        "abs":  rets.abs(),
    }).dropna()
    return df


class RegimeDetector:
    """
    Parameters
    ----------
    method        : 'hmm' | 'kmeans' | 'gmm'
    n_states      : 국면 개수 (보통 2~4)
    random_state  : 재현성 시드

    Usage
    -----
    >>> rd = RegimeDetector('hmm', n_states=3)
    >>> labels = rd.fit_predict(returns)
    """

    def __init__(self, method: str = "hmm", n_states: int = 3,
                 random_state: int = 42):
        self.method = method.lower()
        self.n_states = n_states
        self.random_state = random_state
        self.scaler = StandardScaler()
        self.model = None

    def fit_predict(self, returns: pd.Series) -> pd.Series:
        """학습과 동시에 라벨을 반환한다."""
        feats = _featurize(returns)
        if len(feats) == 0:
            return pd.Series(dtype=int, name="regime")
        X = self.scaler.fit_transform(feats.values)

        if self.method == "hmm":
            if not HAS_HMM:
                raise ImportError("hmmlearn 이 설치되지 않았습니다")
            self.model = _hmm.GaussianHMM(
                n_components=self.n_states, covariance_type="full",
                n_iter=200, random_state=self.random_state,
            )
            self.model.fit(X)
            labels = self.model.predict(X)
        elif self.method == "kmeans":
            self.model = KMeans(n_clusters=self.n_states,
                                random_state=self.random_state, n_init=10)
            labels = self.model.fit_predict(X)
        elif self.method == "gmm":
            self.model = GaussianMixture(n_components=self.n_states,
                                         random_state=self.random_state)
            self.model.fit(X)
            labels = self.model.predict(X)
        else:
            raise ValueError(self.method)

        return pd.Series(labels, index=feats.index, name="regime")

    def stats_by_regime(self, returns: pd.Series, labels: pd.Series) -> pd.DataFrame:
        """각 국면의 평균/표준편차/샤프 통계."""
        df = pd.concat([returns.rename("ret"), labels], axis=1).dropna()
        return df.groupby("regime").agg(
            n=("ret", "count"),
            mean=("ret", "mean"),
            std=("ret", "std"),
            sharpe=("ret", lambda r: r.mean() / r.std() * np.sqrt(252) if r.std() else 0),
        )
