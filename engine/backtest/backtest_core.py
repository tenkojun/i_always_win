"""
백테스트 엔진 모듈 (선택적)
===========================
종목 분석에는 직접 쓰이지 않지만, 독립적으로 가격 시계열 + 시그널
함수가 있으면 백테스트를 돌릴 수 있도록 모듈을 유지한다.

사용 예
-------
>>> from engine.backtest.backtest_core import Backtest
>>> def my_signal(df):
...     fast = df['close'].rolling(20).mean()
...     slow = df['close'].rolling(60).mean()
...     return (fast > slow).astype(int) * 2 - 1
>>> bt = Backtest(df, signal_fn=my_signal).run()

반환 dict
---------
- equity     : 자본 곡선 시리즈
- returns    : 일별 수익률
- trades     : 거래 내역 DataFrame
- final_cash : 최종 현금
- n_trades   : 거래 수
"""
from __future__ import annotations
from typing import Any, Callable, Dict, Optional
import numpy as np
import pandas as pd

from .position import Position
from .slippage import fixed_slippage, commission


class Backtest:
    """
    Parameters
    ----------
    df            : OHLCV (open, high, low, close, volume)
    signal_fn     : df → ±1 시그널 시리즈
    initial_cash  : 초기 자본
    fee_bps       : 수수료 (basis points)
    slip_bps      : 슬리피지 (basis points)
    size_pct      : 각 거래에 사용할 현금 비중 (1.0 = 100%)
    """

    def __init__(self,
                 df: pd.DataFrame,
                 signal_fn: Optional[Callable[[pd.DataFrame], pd.Series]] = None,
                 initial_cash: float = 100_000.0,
                 fee_bps: float = 2.0,
                 slip_bps: float = 1.0,
                 size_pct: float = 1.0):
        self.df = df.copy()
        self.df.columns = [c.lower() for c in self.df.columns]
        self.signal_fn = signal_fn or self._default_signal
        self.cash0 = initial_cash
        self.fee_bps = fee_bps
        self.slip_bps = slip_bps
        self.size_pct = size_pct

    @staticmethod
    def _default_signal(df: pd.DataFrame) -> pd.Series:
        """기본 시그널 — 20/60 MA cross."""
        fast = df["close"].rolling(20).mean()
        slow = df["close"].rolling(60).mean()
        sig = np.where(fast > slow, 1, np.where(fast < slow, -1, 0))
        return pd.Series(sig, index=df.index).fillna(0)

    def run(self) -> Dict[str, Any]:
        """백테스트 실행."""
        df = self.df
        signal = self.signal_fn(df).reindex(df.index).fillna(0).astype(int)

        cash = self.cash0
        pos = Position()
        equity_curve = np.zeros(len(df))
        trades = []

        prices = df["close"].values
        for i, px in enumerate(prices):
            target = int(signal.iat[i])

            # 청산
            if pos.is_open and target != pos.side:
                exec_px = fixed_slippage(px, -pos.side, self.slip_bps)
                pnl = (exec_px - pos.entry_price) * pos.size * pos.side
                cash += pnl
                cash -= commission(pos.size * exec_px, self.fee_bps)
                trades.append({
                    "entry_idx": pos.entry_idx, "exit_idx": i,
                    "side": pos.side,
                    "entry_price": pos.entry_price, "exit_price": exec_px,
                    "size": pos.size, "pnl": pnl,
                    "ret": pnl / (pos.entry_price * pos.size + 1e-9),
                })
                pos = Position()

            # 진입
            if not pos.is_open and target != 0:
                exec_px = fixed_slippage(px, target, self.slip_bps)
                size = (cash * self.size_pct) / exec_px
                cash -= commission(size * exec_px, self.fee_bps)
                pos = Position(side=target, size=size,
                               entry_price=exec_px, entry_idx=i)

            # mark-to-market
            equity_curve[i] = cash + pos.unrealized(px)

        equity = pd.Series(equity_curve, index=df.index, name="equity")
        equity = equity.replace(0, np.nan).ffill().fillna(self.cash0)
        rets = equity.pct_change().fillna(0.0)

        return {
            "equity": equity, "returns": rets,
            "trades": pd.DataFrame(trades),
            "final_cash": cash, "n_trades": len(trades),
        }
