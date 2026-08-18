"""
시장 미시구조 모듈
==================
- order_imbalance     : 호가 잔량 불균형 (LOB 데이터 필요)
- volume_imbalance    : OHLCV 근사 — 양봉/음봉 거래량 비율
- microprice          : LOB 마이크로프라이스 (체결 가능 가격)
- fair_value_price    : VWAP 기반 공정가격

OHLCV 만 있을 때는 order_imbalance / microprice 는 사용할 수 없고
volume_imbalance, fair_value_price 가 LOB 의 근사 대체로 쓰인다.
"""
from __future__ import annotations
import numpy as np
import pandas as pd


def order_imbalance(bid_size: pd.Series, ask_size: pd.Series) -> pd.Series:
    """
    호가 잔량 불균형 = (bid_size - ask_size) / (bid_size + ask_size)

    양수면 매수 잔량 우위(상승 압력), 음수면 매도 우위.
    LOB(Level-1 호가) 데이터가 필요하다.
    """
    return ((bid_size - ask_size) / (bid_size + ask_size)).rename("order_imbalance")


def volume_imbalance(df: pd.DataFrame, window: int = 20) -> pd.Series:
    """
    거래량 불균형 (OHLCV 근사).
    최근 N봉의 양봉 거래량 - 음봉 거래량 비율.

    +0.2 이상 : 매수 우위
    -0.2 이하 : 매도 우위
    """
    sign = np.sign(df["close"].diff().fillna(0))
    up = (df["volume"] * (sign > 0)).rolling(window).sum()
    dn = (df["volume"] * (sign < 0)).rolling(window).sum()
    return ((up - dn) / (up + dn + 1e-9)).rename("volume_imbalance")


def microprice(bid: pd.Series, ask: pd.Series,
               bid_size: pd.Series, ask_size: pd.Series) -> pd.Series:
    """
    마이크로프라이스 = (bid·ask_size + ask·bid_size) / (bid_size + ask_size)
    호가 잔량 가중평균. mid-price 보다 다음 체결가에 더 가깝다.
    """
    return (bid * ask_size + ask * bid_size) / (bid_size + ask_size)


def fair_value_price(df: pd.DataFrame, window: int = 20) -> pd.Series:
    """
    공정가치(VWAP) — 거래량 가중 평균가.
    현재 가격이 공정가에서 크게 벗어나면 회귀 신호로 해석.
    """
    typ = (df["high"] + df["low"] + df["close"]) / 3
    return ((typ * df["volume"]).rolling(window).sum() /
            df["volume"].rolling(window).sum()).rename("fair_value")
