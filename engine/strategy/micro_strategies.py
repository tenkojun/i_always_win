"""
micro_strategies.py — 마이크로구조 전략 (일봉 fallback 포함)
============================================================
틱 데이터 기반 마이크로구조 분석을 일봉 백테스트로도 사용 가능하게 wrap.

실시간 (KIS WS 키 있음):
  - 진짜 틱 데이터로 Speed/Imbalance/CVD 계산
일봉 fallback (키 없음 / 백테스트):
  - 일봉 OHLCV로 근사치 계산 (정확도 ↓, 통계 신뢰성 ↑)

전략 ID:
  - speed_of_tape : 거래량/거래 횟수 급증 시 진입
  - book_pressure : 호가 압력 (일봉은 close-open 부호 + 거래량 z-score 근사)
  - real_cvd_div  : 진짜 CVD 다이버전스 (일봉은 close 차분 + volume 가중)
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from typing import Any, Dict, Tuple


# ════════════════════════════════════════════════════════════
#  Speed of Tape — 거래량 급증
#  일봉 근사: rolling volume z-score 임계 돌파 + 양봉
# ════════════════════════════════════════════════════════════
def sig_speed_of_tape(df: pd.DataFrame,
                       lookback: int = 20,
                       z_thresh: float = 2.0,
                       hold_bars: int = 5) -> Tuple[pd.Series, pd.Series]:
    """일봉 데이터로 Speed of Tape 근사:
    거래량이 lookback 평균의 +z_thresh σ 이상 → 매수
    hold_bars 후 자동 청산.

    Tick 데이터 있으면 정확하지만 일봉도 어느 정도 신호 잡힘.
    """
    if "volume" not in df.columns:
        idx = df.index
        return pd.Series(False, index=idx), pd.Series(False, index=idx)
    vol = df["volume"].astype(float)
    vol_mean = vol.rolling(lookback).mean()
    vol_std = vol.rolling(lookback).std().replace(0, np.nan)
    vol_z = (vol - vol_mean) / vol_std
    # 양봉 (close > open) 조건 추가 → 매수자 우위
    bull = df["close"] > df["open"]
    raw_entry = (vol_z > z_thresh) & bull
    entries = raw_entry & ~raw_entry.shift(1).fillna(False)
    # hold_bars 후 청산
    n = len(df)
    exits = pd.Series(False, index=df.index)
    e_idx = np.where(entries.values)[0]
    for i in e_idx:
        j = min(i + hold_bars, n - 1)
        exits.iloc[j] = True
    return entries.fillna(False), exits.fillna(False)


# ════════════════════════════════════════════════════════════
#  Book Pressure — 호가 압력 (일봉 근사)
#  일봉 근사: close-open 부호 + (high-close)/(close-low) 비율
# ════════════════════════════════════════════════════════════
def sig_book_pressure(df: pd.DataFrame,
                       lookback: int = 10,
                       z_thresh: float = 1.5
                       ) -> Tuple[pd.Series, pd.Series]:
    """매수 압력 근사:
    pressure = (close - low) / (high - low + ε)
    rolling z-score > z_thresh → 매수 우위 진입
    """
    high, low, close = df["high"], df["low"], df["close"]
    rng = (high - low).replace(0, np.nan)
    pressure = (close - low) / rng
    p_mean = pressure.rolling(lookback).mean()
    p_std = pressure.rolling(lookback).std().replace(0, np.nan)
    p_z = (pressure - p_mean) / p_std
    entries = (p_z > z_thresh) & (p_z.shift(1).fillna(0) <= z_thresh)
    exits = (p_z < -z_thresh) & (p_z.shift(1).fillna(0) >= -z_thresh)
    return entries.fillna(False), exits.fillna(False)


# ════════════════════════════════════════════════════════════
#  Real CVD divergence — 거래량 가중 CVD
#  일봉 근사: close-open 부호 × volume → 누적 CVD
# ════════════════════════════════════════════════════════════
def sig_real_cvd_div(df: pd.DataFrame,
                      lookback: int = 20,
                      thresh_z: float = 1.5
                      ) -> Tuple[pd.Series, pd.Series]:
    """일봉 근사 CVD = sum(sign(close-open) × volume)
    가격↑·CVD↓ → 약세 다이버전스 → 청산
    가격↓·CVD↑ → 강세 다이버전스 → 매수
    """
    close = df["close"].astype(float)
    vol = df.get("volume", pd.Series(1.0, index=df.index)).astype(float)
    sign = np.sign(close - df["open"].astype(float))
    cvd = (sign * vol).cumsum()
    # 가격 vs CVD lookback 변화
    p_chg = close.pct_change(lookback)
    cvd_chg = cvd.diff(lookback)
    # z-score
    def _z(s, w=60):
        m = s.rolling(w, min_periods=10).mean()
        sd = s.rolling(w, min_periods=10).std().replace(0, np.nan)
        return ((s - m) / sd).fillna(0)
    pz = _z(p_chg)
    cz = _z(cvd_chg)
    # 강세 다이버전스: 가격 약세 + CVD 강세
    entries = (pz < -thresh_z) & (cz > thresh_z)
    # 약세 다이버전스: 가격 강세 + CVD 약세
    exits = (pz > thresh_z) & (cz < -thresh_z)
    return entries.fillna(False), exits.fillna(False)


# ════════════════════════════════════════════════════════════
#  STRATEGIES_MICRO 메타 — AVAILABLE_STRATEGIES에 등록될 dict
# ════════════════════════════════════════════════════════════
STRATEGIES_MICRO = {
    "speed_of_tape": {
        "label": "마이크로 · Speed of Tape",
        "desc":  "거래량 급증 (z>2) + 양봉 진입 · 일봉 근사 / 틱 있으면 정확",
        "fn":    sig_speed_of_tape,
        "params": [
            {"name": "lookback", "type": "int", "default": 20,
             "min": 5, "max": 60, "label": "관찰 기간"},
            {"name": "z_thresh", "type": "float", "default": 2.0,
             "min": 1.0, "max": 4.0, "label": "거래량 z 임계"},
            {"name": "hold_bars", "type": "int", "default": 5,
             "min": 1, "max": 30, "label": "보유 봉수"},
        ],
    },
    "book_pressure": {
        "label": "마이크로 · Book Pressure",
        "desc":  "매수 압력 (close-low)/(high-low) z-score 돌파",
        "fn":    sig_book_pressure,
        "params": [
            {"name": "lookback", "type": "int", "default": 10,
             "min": 5, "max": 30, "label": "관찰 기간"},
            {"name": "z_thresh", "type": "float", "default": 1.5,
             "min": 0.5, "max": 3.0, "label": "z 임계"},
        ],
    },
    "real_cvd_div": {
        "label": "마이크로 · Real CVD (거래량 가중)",
        "desc":  "거래량 가중 CVD 다이버전스 — pseudo CVD보다 정확",
        "fn":    sig_real_cvd_div,
        "params": [
            {"name": "lookback", "type": "int", "default": 20,
             "min": 5, "max": 60, "label": "관찰 기간"},
            {"name": "thresh_z", "type": "float", "default": 1.5,
             "min": 0.5, "max": 3.0, "label": "다이버전스 z"},
        ],
    },
}
