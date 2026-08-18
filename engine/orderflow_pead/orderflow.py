"""
Orderflow 인디케이터
=====================
- cvd                  : 누적 Volume Delta
- bid_ask_aggression   : 매수/매도 공격성 비율
- delta_divergence     : 가격 vs 델타 방향 불일치 (반전 신호)
- mtf_delta            : 다중 타임프레임 델타 (resample)
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from typing import Dict


# ── CVD (Cumulative Volume Delta) ─────────────────────────────
def cvd(orderflow_df: pd.DataFrame) -> pd.Series:
    """누적 델타. 양수면 buy pressure 누적."""
    if "delta" not in orderflow_df.columns:
        raise ValueError("orderflow_df는 'delta' 컬럼 필요")
    return orderflow_df["delta"].cumsum()


# ── Bid/Ask Aggression ────────────────────────────────────────
def bid_ask_aggression(orderflow_df: pd.DataFrame) -> pd.DataFrame:
    """봉별 공격성 비율 (0~1)."""
    bv = orderflow_df["bid_vol"]
    av = orderflow_df["ask_vol"]
    tot = (bv + av).replace(0, np.nan)
    return pd.DataFrame({
        "ask_ratio": (av / tot).fillna(0.5),
        "bid_ratio": (bv / tot).fillna(0.5),
        "imbalance": ((av - bv) / tot).fillna(0),
    })


# ── Delta Divergence ──────────────────────────────────────────
def delta_divergence(price: pd.Series,
                     orderflow_df: pd.DataFrame,
                     lookback: int = 10,
                     thresh_z: float = 1.5) -> pd.Series:
    """
    가격 vs 델타 z-score 부호 불일치를 감지.
      +1  매수자 고갈 (가격↑·델타↓) → 하락 반전 시그널
      -1  매도자 고갈 (가격↓·델타↑) → 상승 반전 시그널
       0  divergence 없음
    """
    pr_chg = price.pct_change(lookback)
    de_chg = orderflow_df["delta"].rolling(lookback).sum()
    # Z-score 정규화
    def _z(s):
        m = s.rolling(60, min_periods=10).mean()
        sd = s.rolling(60, min_periods=10).std().replace(0, np.nan)
        return ((s - m) / sd).fillna(0)
    pz = _z(pr_chg)
    dz = _z(de_chg)
    out = pd.Series(0, index=price.index, dtype=int)
    out[(pz > thresh_z) & (dz < -thresh_z)] = +1   # 매수자 고갈
    out[(pz < -thresh_z) & (dz > thresh_z)] = -1   # 매도자 고갈
    return out


# ── 다중 타임프레임 델타 ──────────────────────────────────────
def mtf_delta(orderflow_df: pd.DataFrame,
              rules: Dict[str, str] = None) -> pd.DataFrame:
    """1분 델타 → 더 큰 타임프레임으로 resample 합산.
    rules 예: {'5m':'5T','15m':'15T','1h':'1H'}
    """
    rules = rules or {"5m": "5T", "15m": "15T", "1h": "1H"}
    out = pd.DataFrame(index=orderflow_df.index)
    for label, rule in rules.items():
        out[f"delta_{label}"] = (
            orderflow_df["delta"].resample(rule).sum()
            .reindex(orderflow_df.index, method="ffill"))
    return out
