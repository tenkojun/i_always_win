"""
Black-Litterman 모델 모듈
=========================
시장 균형 수익률(equilibrium return)을 사전 분포로 하고, 투자자의
'견해(View)' 를 관측치로 결합해 사후 수익률을 얻은 뒤
Markowitz 최적화를 돌린다.

장점
----
- 평균-분산 최적화의 노이즈 민감성을 완화
- 일부 자산에 대한 견해만 있어도 사용 가능 ("나는 A 가 B보다 2% 더 오를 것")

속성 설명
---------
- market_caps  : 자산별 시가총액 (None 이면 동일가중)
- P            : 견해 행렬 (k×n) — 각 행은 견해 1개
- Q            : 견해 기대수익률 벡터 (k,)
- Omega        : 견해 불확실성 공분산 (None 이면 자동)
- tau          : 사전분포 신뢰도 스칼라 (보통 0.025~0.05)
- risk_aversion: 시장 위험 회피 계수 (보통 2.5)
"""
from __future__ import annotations
from typing import Dict, Optional
import numpy as np
import pandas as pd

from .markowitz import markowitz_optimize


def black_litterman(returns: pd.DataFrame,
                    market_caps: Optional[Dict[str, float]] = None,
                    P: Optional[np.ndarray] = None,
                    Q: Optional[np.ndarray] = None,
                    Omega: Optional[np.ndarray] = None,
                    tau: float = 0.05,
                    risk_aversion: float = 2.5) -> Dict:
    """
    Parameters
    ----------
    returns       : DataFrame 자산 수익률
    market_caps   : {ticker: market_cap}
    P, Q, Omega   : 견해 (없으면 균형 수익률만 사용)
    tau           : 사전분포 신뢰도
    risk_aversion : 위험 회피 계수
    """
    cov = returns.cov().values * 252
    n = cov.shape[0]
    cols = list(returns.columns)

    # 1) 시장 균형 가중치
    if market_caps:
        w_mkt = np.array([market_caps.get(c, 0) for c in cols])
        w_mkt = w_mkt / w_mkt.sum()
    else:
        w_mkt = np.repeat(1 / n, n)

    # 균형 수익률 (implied equilibrium returns)
    pi = risk_aversion * cov @ w_mkt

    # 2) 견해가 없으면 prior 그대로
    if P is None or Q is None:
        post_mu, post_cov = pi, cov
    else:
        if Omega is None:
            Omega = np.diag(np.diag(P @ (tau * cov) @ P.T))
        tau_cov = tau * cov
        inv = np.linalg.inv(np.linalg.inv(tau_cov)
                            + P.T @ np.linalg.inv(Omega) @ P)
        post_mu = inv @ (np.linalg.inv(tau_cov) @ pi
                         + P.T @ np.linalg.inv(Omega) @ Q)
        post_cov = cov + inv

    # 3) 사후 분포에서 표본 추출 후 Markowitz
    post_rets = pd.DataFrame(
        np.random.default_rng(0).multivariate_normal(
            post_mu / 252, post_cov / 252, 1000),
        columns=cols,
    )
    opt = markowitz_optimize(post_rets, objective="max_sharpe", allow_short=False)
    return {
        "posterior_mu":     dict(zip(cols, post_mu.tolist())),
        "implied_returns":  dict(zip(cols, pi.tolist())),
        "weights":          opt["weights"],
        "expected_return":  opt["expected_return"],
        "volatility":       opt["volatility"],
        "sharpe":           opt["sharpe"],
    }
