"""
전략 클래스 — 인디케이터 → 시그널 → 포트폴리오 파이프라인
==========================================================
모두 동일 인터페이스:
  .generate_signals(data_bundle) -> dict(entries, exits, ...)
  .run(data_bundle, **portfolio_kwargs) -> vbt.Portfolio
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional
import numpy as np
import pandas as pd

from . import orderflow as of_mod
from . import pead       as pead_mod


def _vbt():
    try:
        import vectorbt as vbt
        return vbt
    except Exception:
        return None


@dataclass
class _BaseStrategy:
    name: str = "BaseStrategy"
    description: str = ""
    params: Dict[str, Any] = field(default_factory=dict)

    def generate_signals(self, bundle: Dict[str, Any]
                          ) -> Dict[str, pd.Series]:
        raise NotImplementedError

    def run(self, bundle: Dict[str, Any], fees: float = 0.0005,
            init_cash: float = 100000.0,
            freq: str = "1D"):
        vbt = _vbt()
        if vbt is None:
            raise RuntimeError("vectorbt 미설치")
        sig = self.generate_signals(bundle)
        close = bundle["ohlcv"]["close"]
        pf = vbt.Portfolio.from_signals(
            close=close,
            entries=sig["entries"].reindex(close.index).fillna(False),
            exits=sig["exits"].reindex(close.index).fillna(False),
            short_entries=sig.get("short_entries"),
            short_exits=sig.get("short_exits"),
            fees=fees, slippage=0.0001,
            init_cash=init_cash, freq=freq,
        )
        return pf, sig


# ──────────────────────────────────────────────────────────────
#  1) OrderflowDeltaStrategy
# ──────────────────────────────────────────────────────────────
class OrderflowDeltaStrategy(_BaseStrategy):
    """
    규칙:
      LONG  ⇐ price > 이전 세션 고점
              AND 직전봉 음봉
              AND delta > delta_threshold
              AND divergence == 0 (또는 -1 매도자 고갈)
      EXIT  ⇐ ±exit_pnl 또는 세션 종료 (1일 max hold)
    """
    def __init__(self,
                 delta_threshold: float = 500.0,
                 divergence_lookback: int = 10,
                 divergence_z: float = 1.5,
                 max_hold_bars: int = 30):
        super().__init__(
            name="OrderflowDeltaStrategy",
            description=("CVD/Delta 기반 모멘텀 + divergence 필터"),
            params=dict(delta_threshold=delta_threshold,
                        divergence_lookback=divergence_lookback,
                        divergence_z=divergence_z,
                        max_hold_bars=max_hold_bars))

    def generate_signals(self, bundle):
        ohlcv = bundle["ohlcv"]
        ofd = bundle["orderflow"]
        close = ohlcv["close"]
        open_ = ohlcv["open"]
        # 이전 세션 고점 (전일 high)
        prev_high = ohlcv["high"].shift(1)
        # 직전봉 음봉
        prev_bear = (close.shift(1) < open_.shift(1))
        delta = ofd["delta"]
        div = of_mod.delta_divergence(
            close, ofd,
            lookback=self.params["divergence_lookback"],
            thresh_z=self.params["divergence_z"])
        entries = (
            (close > prev_high) &
            prev_bear &
            (delta > self.params["delta_threshold"]) &
            (div != 1)   # 매수자 고갈(+1)이면 진입 회피
        ).fillna(False)
        # 청산: max_hold_bars 후 강제 청산
        # vectorbt는 exits Series 필요 → entries 시점에서 N봉 후 True
        exits = pd.Series(False, index=close.index)
        e_idx = np.where(entries.values)[0]
        max_h = self.params["max_hold_bars"]
        for i in e_idx:
            j = min(i + max_h, len(exits) - 1)
            exits.iloc[j] = True
        return {"entries": entries, "exits": exits,
                "divergence": div, "delta": delta}


# ──────────────────────────────────────────────────────────────
#  2) PEADStrategy
# ──────────────────────────────────────────────────────────────
class PEADStrategy(_BaseStrategy):
    """
    규칙:
      이벤트 발표일 t=0 종가에서:
        SUE 상위 sue_top_pct → LONG
        SUE 하위 sue_top_pct → SHORT (선택)
      Drift 기간 hold (drift_days)
      리밸런싱은 다음 이벤트가 올 때까지 유지.
    """
    def __init__(self,
                 sue_top_pct: float = 0.20,
                 drift_days: int = 30,
                 allow_short: bool = True,
                 enter_offset_days: int = 1):
        super().__init__(
            name="PEADStrategy",
            description=("Post-Earnings Announcement Drift — SUE 분위 기반"),
            params=dict(sue_top_pct=sue_top_pct,
                        drift_days=drift_days,
                        allow_short=allow_short,
                        enter_offset_days=enter_offset_days))

    def generate_signals(self, bundle):
        ohlcv = bundle["ohlcv"]
        events = bundle["earnings"]
        close = ohlcv["close"]
        entries = pd.Series(False, index=close.index)
        exits = pd.Series(False, index=close.index)
        s_ent = pd.Series(False, index=close.index)
        s_exi = pd.Series(False, index=close.index)
        if events is None or events.empty:
            return {"entries": entries, "exits": exits,
                    "short_entries": s_ent, "short_exits": s_exi}
        # SUE 분위 임계
        top = events["sue"].quantile(1 - self.params["sue_top_pct"])
        bot = events["sue"].quantile(self.params["sue_top_pct"])
        drift = self.params["drift_days"]
        off = self.params["enter_offset_days"]
        for _, row in events.iterrows():
            ev = pd.Timestamp(row["event_date"])
            sue = row.get("sue", 0)
            # 진입 시점 = E+off, 청산 시점 = E+off+drift
            try:
                e_pos = close.index.get_indexer([ev], method="bfill")[0]
                if e_pos < 0:
                    continue
                ent_pos = min(e_pos + off, len(close) - 1)
                exit_pos = min(ent_pos + drift, len(close) - 1)
                if sue >= top:
                    entries.iloc[ent_pos] = True
                    exits.iloc[exit_pos] = True
                elif sue <= bot and self.params["allow_short"]:
                    s_ent.iloc[ent_pos] = True
                    s_exi.iloc[exit_pos] = True
            except Exception:
                continue
        return {"entries": entries, "exits": exits,
                "short_entries": s_ent, "short_exits": s_exi,
                "sue_top_threshold": top, "sue_bot_threshold": bot}


# ──────────────────────────────────────────────────────────────
#  3) Hybrid_OF_PEAD_Strategy
# ──────────────────────────────────────────────────────────────
class Hybrid_OF_PEAD_Strategy(_BaseStrategy):
    """
    합성 전략:
      LONG  ⇐ Orderflow LONG OR PEAD LONG
      SHORT ⇐ PEAD SHORT (orderflow는 short 안 함)
      EXIT  ⇐ 각 substrategy의 exit 합집합
    """
    def __init__(self,
                 of_params: Optional[Dict[str, Any]] = None,
                 pead_params: Optional[Dict[str, Any]] = None):
        super().__init__(
            name="Hybrid_OF_PEAD_Strategy",
            description=("Orderflow Delta + PEAD 합성 — 두 시그널 OR 결합"),
            params={"of": of_params or {}, "pead": pead_params or {}})
        self._of = OrderflowDeltaStrategy(**(of_params or {}))
        self._pead = PEADStrategy(**(pead_params or {}))

    def generate_signals(self, bundle):
        s_of = self._of.generate_signals(bundle)
        s_pe = self._pead.generate_signals(bundle)
        entries = (s_of["entries"].fillna(False)
                    | s_pe["entries"].fillna(False))
        exits = (s_of["exits"].fillna(False)
                  | s_pe["exits"].fillna(False))
        s_ent = s_pe.get("short_entries",
                          pd.Series(False, index=entries.index))
        s_exi = s_pe.get("short_exits",
                          pd.Series(False, index=entries.index))
        return {"entries": entries, "exits": exits,
                "short_entries": s_ent, "short_exits": s_exi,
                "of_div": s_of.get("divergence"),
                "of_delta": s_of.get("delta")}
