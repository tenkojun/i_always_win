"""
슬리피지 / 수수료 모델 모듈
===========================
백테스트 시 체결가 보정과 수수료 계산.

- fixed_slippage : bps 단위 고정 슬리피지
- vol_slippage   : 변동성에 비례하는 슬리피지
- commission     : 거래 수수료 (bps)
"""
from __future__ import annotations


def fixed_slippage(price: float, side: int, bps: float = 1.0) -> float:
    """
    고정 슬리피지.

    Parameters
    ----------
    side : +1=매수, -1=매도
    bps  : basis point (1bp = 0.01%)
    """
    return price * (1.0 + side * bps / 1e4)


def vol_slippage(price: float, side: int, vol: float, k: float = 0.1) -> float:
    """변동성 비례 슬리피지."""
    return price * (1.0 + side * k * vol)


def commission(notional: float, rate_bps: float = 2.0) -> float:
    """거래 수수료 (notional × rate_bps)."""
    return abs(notional) * rate_bps / 1e4
