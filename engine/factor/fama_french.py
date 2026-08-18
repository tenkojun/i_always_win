"""
Fama-French 팩터 모듈
=====================
3팩터: MKT (시장), SMB (소형주), HML (가치주)
5팩터: + RMW (수익성), CMA (보수투자)

함수
----
- synthetic_ff_factors : 데모용 합성 팩터 (인터넷 없이 사용)
- load_ff_factors      : 사용자 CSV 로드

실제 데이터는 Kenneth French 의 웹사이트에서 다운로드 가능.
"""
from __future__ import annotations
import numpy as np
import pandas as pd


def synthetic_ff_factors(index: pd.DatetimeIndex,
                         model: str = "3F",
                         seed: int = 42) -> pd.DataFrame:
    """
    데모용 합성 FF 팩터 수익률 생성.

    Parameters
    ----------
    model : '3F' (3팩터) 또는 '5F' (5팩터)
    """
    rng = np.random.default_rng(seed)
    n = len(index)

    cols_3f = ["MKT", "SMB", "HML", "RF"]
    cols_5f = cols_3f + ["RMW", "CMA"]
    cols = cols_5f if model.upper() == "5F" else cols_3f

    cov = np.eye(len(cols)) * 0.0001
    cov[0, 0] = 0.0002          # 시장 팩터 변동성 더 크게
    means = np.array([0.0004] + [0.0001] * (len(cols) - 2) + [0.00005])
    data = rng.multivariate_normal(means, cov, size=n)

    return pd.DataFrame(data, index=index, columns=cols)


def load_ff_factors(path: str) -> pd.DataFrame:
    """CSV (날짜 인덱스 + 팩터 열) 로드."""
    return pd.read_csv(path, index_col=0, parse_dates=True)
