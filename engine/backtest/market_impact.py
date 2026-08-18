"""
market_impact.py — Almgren-Chriss 시장 임팩트 모델
============================================================
기존 고정 슬리피지 대신 거래량 대비 비선형 임팩트 적용.

수식:
  temporary_impact = η × σ × √(Q / ADV)
  permanent_impact = γ × Q / ADV
  total_impact_bps = temporary + permanent

  Q   : 주문 수량 (주식 수 or 달러)
  ADV : 평균 일거래량 (Average Daily Volume)
  σ   : 일별 변동성 (annualized 변환)
  η, γ: 모델 상수 (Almgren-Chriss 추정)

기관 입장:
- $10K 주문 vs $10M 주문은 슬리피지가 다르다
- 거래량 대비 비율이 핵심 (50% 이상이면 매우 비쌈)
- 백테스트의 alpha는 임팩트 차감 후 측정해야 현실적
"""
from __future__ import annotations

import math
import numpy as np
import pandas as pd
from typing import Any, Dict, Optional


def almgren_chriss_impact(
    order_size_usd: float,
    avg_daily_volume_usd: float,
    daily_vol_pct: float = 0.02,
    eta: float = 0.142,     # temporary impact coefficient (Almgren et al. 2005)
    gamma: float = 0.314,   # permanent impact coefficient
) -> Dict[str, float]:
    """단일 주문에 대한 임팩트 (bps).

    Parameters
    ----------
    order_size_usd      : 주문 크기 ($)
    avg_daily_volume_usd: 평균 일거래량 ($)
    daily_vol_pct       : 일별 변동성 (예: 0.02 = 2%)
    eta, gamma          : 모델 상수 (NYSE 기준 기본값)

    Returns
    -------
    dict with keys: temporary_bps, permanent_bps, total_bps, ratio_to_adv
    """
    if avg_daily_volume_usd <= 0 or order_size_usd <= 0:
        return {"temporary_bps": 0.0, "permanent_bps": 0.0,
                "total_bps": 0.0, "ratio_to_adv": 0.0}
    ratio = order_size_usd / avg_daily_volume_usd
    # σ는 일별 변동성을 그대로 사용
    sigma = max(daily_vol_pct, 0.001)   # 최소 10bps
    # Temporary impact (시간적, 주문 종료 후 사라짐) — √ 비선형
    temp = eta * sigma * math.sqrt(ratio) * 10000   # to bps
    # Permanent impact (영구적, 시장가 변화) — 선형
    perm = gamma * sigma * ratio * 10000
    return {
        "temporary_bps": round(temp, 2),
        "permanent_bps": round(perm, 2),
        "total_bps":     round(temp + perm, 2),
        "ratio_to_adv":  round(ratio, 4),
    }


def estimate_slippage_pct(
    order_size_usd: float,
    avg_daily_volume_usd: float,
    daily_vol_pct: float = 0.02,
    base_spread_bps: float = 5.0,   # 기본 bid-ask 스프레드
) -> float:
    """슬리피지 비율 (소수) 반환 — vbt fees/slippage 파라미터에 직접 사용.

    예: 0.0008 = 0.08% = 8bps
    """
    impact = almgren_chriss_impact(
        order_size_usd, avg_daily_volume_usd, daily_vol_pct)
    total_bps = impact["total_bps"] + base_spread_bps / 2  # 절반은 매수, 절반은 매도
    return total_bps / 10000.0


def corwin_schultz_spread(high: pd.Series, low: pd.Series,
                            n_days: int = 60) -> float:
    """Corwin-Schultz (2012) bid-ask spread estimator from high-low.

    종목별 + 시기별 동적 추정. 일봉 데이터만 있으면 사용 가능.

    공식:
      β = [log(H/L)]²의 인접 2일 합
      γ = [log(2일 max H / 2일 min L)]²
      α = (√(2β) - √β) / (3 - 2√2) - √(γ / (3 - 2√2))
      spread = 2(e^α - 1) / (1 + e^α)   (음수 α는 0으로 클리핑)

    Returns:
      평균 spread (소수, 0.001 = 0.1%)
    """
    try:
        h = np.log(high.tail(n_days).values).astype(float)
        l = np.log(low.tail(n_days).values).astype(float)
        if len(h) < 2:
            return 0.0
        hl_sq = (h - l) ** 2
        beta = hl_sq[:-1] + hl_sq[1:]                 # 2일 페어
        h2 = np.maximum(h[:-1], h[1:])
        l2 = np.minimum(l[:-1], l[1:])
        gamma = (h2 - l2) ** 2
        denom = 3 - 2 * np.sqrt(2)
        # Negative spread (모델 가정 위배) → 클리핑
        alpha = ((np.sqrt(2 * beta) - np.sqrt(beta)) / denom
                  - np.sqrt(gamma / denom))
        alpha = np.maximum(alpha, 0)
        spread = 2 * (np.exp(alpha) - 1) / (1 + np.exp(alpha))
        spread = spread[~np.isnan(spread)]
        if len(spread) == 0:
            return 0.0
        return float(np.median(spread))   # median 이 outlier 강함
    except Exception:
        return 0.0


def latency_cost_bps(daily_vol_pct: float, latency_ms: float = 200.0,
                      bars_per_day: int = 1) -> float:
    """주문 지연 비용 (bps).

    시그널 → 주문 도달까지 지연. 봉 안 가격 변동 일부가 슬리피지로 작용.
    일봉: 종가 시그널 → 다음날 시초가 (이미 next_bar_exec에서 반영, latency 효과 미미)
    분봉/실시간: 봉 안 분산에 비례

    Parameters:
      daily_vol_pct  : 일별 변동성 (예: 0.02 = 2%)
      latency_ms     : 지연 (밀리초)
      bars_per_day   : 1=일봉, 78=5분봉, 390=1분봉 (미국)

    Returns:
      bps (basis points)
    """
    # 봉당 시간 (분)
    minutes_per_bar = (390 if bars_per_day >= 1 else 1) / max(bars_per_day, 1)
    seconds_per_bar = minutes_per_bar * 60
    if seconds_per_bar <= 0:
        return 0.0
    # 봉당 변동성 (sqrt rule)
    bar_vol_pct = daily_vol_pct / np.sqrt(max(bars_per_day, 1))
    # latency 비율 (봉 시간 대비)
    latency_ratio = min(1.0, (latency_ms / 1000.0) / seconds_per_bar)
    # 변동성 × ratio × √2/π (Brownian motion 평균 절대 변화)
    return float(bar_vol_pct * latency_ratio * 0.7979 * 10000)


def auto_slippage_for_backtest(
    close: pd.Series,
    volume: Optional[pd.Series],
    init_cash: float,
    position_size_pct: float = 1.0,
    high: Optional[pd.Series] = None,
    low: Optional[pd.Series] = None,
    latency_ms: float = 0.0,
    bars_per_day: int = 1,
) -> Dict[str, Any]:
    """백테스트 시작 시 데이터로부터 자동 슬리피지 추정.

    구성요소:
      1. 시장 임팩트 (Almgren-Chriss) — ADV + 일변동성 + 주문크기
      2. Bid-Ask Spread (Corwin-Schultz, high/low 있으면) — 종목별 동적
      3. Latency (옵션) — 봉 안 지연 비용

    하위 호환: high/low/latency 미지정 시 기존 동작 유지.
    """
    if volume is None or len(volume) < 30:
        return {
            "slippage_pct": 0.0001,
            "method": "default",
            "note": "거래량 데이터 부족 → 기본 1bp",
        }
    try:
        recent = min(60, len(close))
        adv_usd = float((close.iloc[-recent:] * volume.iloc[-recent:]).mean())
        ret_std = float(close.pct_change().dropna().tail(60).std())
        avg_order = init_cash * float(position_size_pct)
        # 1) 시장 임팩트 (Almgren-Chriss)
        impact = almgren_chriss_impact(avg_order, adv_usd, ret_std)
        impact_bps = impact["total_bps"]
        # 2) Bid-Ask Spread (Corwin-Schultz) — high/low 있을 때만
        spread_pct = 0.0
        spread_bps = 5.0   # 기본 5bps (없을 때 보수적)
        if high is not None and low is not None and len(high) >= 30:
            cs_spread = corwin_schultz_spread(high, low, n_days=60)
            if cs_spread > 0:
                spread_pct = cs_spread
                spread_bps = cs_spread * 10000
        # 양방향 spread의 절반 (한 거래당)
        half_spread_bps = spread_bps / 2
        # 3) Latency cost
        latency_bps = (latency_cost_bps(ret_std, latency_ms, bars_per_day)
                        if latency_ms > 0 else 0.0)
        # 총 슬리피지 (bps)
        total_bps = impact_bps + half_spread_bps + latency_bps
        slip = max(0.00001, min(total_bps / 10000.0, 0.01))   # 0.001%~1% cap
        return {
            "slippage_pct":    round(slip, 6),
            "method":          "alm_chriss + corwin_schultz + latency",
            "adv_usd":         round(adv_usd, 0),
            "daily_vol_pct":   round(ret_std, 4),
            "avg_order_usd":   round(avg_order, 0),
            "impact":          impact,
            "components_bps": {
                "market_impact":  round(impact_bps, 2),
                "half_spread":    round(half_spread_bps, 2),
                "latency":        round(latency_bps, 2),
                "total":          round(total_bps, 2),
            },
            "spread_pct":      round(spread_pct, 6),
            "note": (f"ADV ${adv_usd/1e6:.1f}M · 변동성 {ret_std*100:.2f}% · "
                      f"임팩트 {impact_bps:.1f}bp + spread {half_spread_bps:.1f}bp"
                      + (f" + latency {latency_bps:.1f}bp" if latency_bps>0 else "")
                      + f" = {total_bps:.1f}bps ({slip*100:.3f}%)"),
        }
    except Exception as e:
        return {
            "slippage_pct": 0.0001,
            "method": "fallback",
            "error": str(e)[:120],
        }
