"""
VPIN (Volume-Synchronized Probability of Informed Trading)
==========================================================
Easley, López de Prado, O'Hara (2012)

거래량을 시간이 아닌 부피 단위로 묶어(volume bucket) 매수/매도
거래량 차이의 절대비를 평균낸 값. 0~1 사이에서 1에 가까울수록
정보 거래자(informed trader) 비중이 높다고 해석한다.

해석
----
- 낮은 VPIN (≈ 0~0.2) : 시장 평온
- 높은 VPIN (> 0.3)    : 변동성/유동성 위험 임박 가능
"""
from __future__ import annotations
import numpy as np
import pandas as pd
from scipy.stats import norm


def vpin(df: pd.DataFrame,
         bucket_volume: float = None,
         n_buckets: int = 50,
         window: int = 50) -> pd.Series:
    """
    Parameters
    ----------
    df            : OHLCV (close, volume 필요)
    bucket_volume : 한 버킷당 거래량. None 이면 전체/n_buckets
    n_buckets     : 자동 모드일 때 총 버킷 개수
    window        : 이동평균 윈도(버킷 단위)
    """
    vol = df["volume"].values
    rets = df["close"].pct_change().fillna(0).values
    sigma = rets.std(ddof=1) or 1e-6

    if bucket_volume is None:
        bucket_volume = vol.sum() / max(n_buckets, 1)

    buy_vol, sell_vol, bucket_idx = [], [], []
    cur_vol = cur_buy = cur_sell = 0.0

    for i, (v, r) in enumerate(zip(vol, rets)):
        z = r / sigma
        b = norm.cdf(z) * v       # 매수로 분류된 거래량
        s = v - b                 # 매도로 분류된 거래량
        cur_buy += b
        cur_sell += s
        cur_vol += v
        if cur_vol >= bucket_volume:
            buy_vol.append(cur_buy)
            sell_vol.append(cur_sell)
            bucket_idx.append(df.index[i])
            cur_vol = cur_buy = cur_sell = 0.0

    if not buy_vol:
        return pd.Series(dtype=float, name="vpin")

    buy = np.array(buy_vol)
    sell = np.array(sell_vol)
    imb = np.abs(buy - sell) / (buy + sell + 1e-9)
    return pd.Series(imb, index=bucket_idx, name="vpin").rolling(
        window, min_periods=1
    ).mean()
