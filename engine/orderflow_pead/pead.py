"""
PEAD 이벤트 인디케이터
========================
- sue              : Standardized Unexpected Earnings (events_df 컬럼에 이미)
- surprise_pct     : (actual - estimate) / |estimate| * 100
- abnormal_return  : 종목 수익률 - 시장 수익률
- cumulative_ar    : 이벤트일 기준 CAR (drift window)
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from typing import Optional


def sue(events: pd.DataFrame) -> pd.Series:
    """events에 미리 계산된 SUE 반환 (loader가 채움)."""
    if "sue" in events.columns:
        return events["sue"]
    sur = events["surprise"]
    return (sur - sur.mean()) / (sur.std(ddof=1) + 1e-9)


def surprise_pct(events: pd.DataFrame) -> pd.Series:
    if "surprise_pct" in events.columns:
        return events["surprise_pct"]
    return events["surprise"] / events["estimate_eps"].abs() * 100


def abnormal_return(stock_ret: pd.Series,
                    market_ret: pd.Series,
                    method: str = "diff") -> pd.Series:
    """비정상 수익률 = stock - market (단순) 또는 CAPM 잔차."""
    aligned = pd.concat([stock_ret, market_ret], axis=1).dropna()
    if aligned.empty:
        return pd.Series(dtype=float)
    if method == "diff":
        return aligned.iloc[:, 0] - aligned.iloc[:, 1]
    # CAPM 잔차 (베타 = cov/var)
    cov = aligned.iloc[:, 0].cov(aligned.iloc[:, 1])
    var = aligned.iloc[:, 1].var()
    beta = cov / (var + 1e-9)
    return aligned.iloc[:, 0] - beta * aligned.iloc[:, 1]


def cumulative_ar(stock_ret: pd.Series,
                  market_ret: pd.Series,
                  event_dates: pd.Series,
                  drift_window: int = 60) -> pd.DataFrame:
    """
    각 이벤트 발표일 t=0 기준으로 [0, drift_window] 일간 CAR 계산.
    Returns DataFrame: index=event_date, columns=['car_5','car_20','car_60',...]
    """
    ar = abnormal_return(stock_ret, market_ret)
    rows = []
    for ev_date in event_dates:
        ev = pd.Timestamp(ev_date)
        # ev 이후 N일 슬라이스
        post = ar[ar.index >= ev].iloc[:drift_window]
        if len(post) < 2:
            rows.append({"event_date": ev,
                          "car_5": np.nan, "car_20": np.nan,
                          "car_60": np.nan})
            continue
        cum = (1 + post).cumprod() - 1
        rows.append({
            "event_date": ev,
            "car_5":  float(cum.iloc[min(4,  len(cum)-1)]),
            "car_20": float(cum.iloc[min(19, len(cum)-1)]),
            "car_60": float(cum.iloc[min(59, len(cum)-1)]),
        })
    return pd.DataFrame(rows).set_index("event_date")
