"""
시그널 정확도 향상 — 다중 전략 합의 + 시장 필터
=====================================================
원시 시그널(activeStrategies 전체) → 다음 필터 통과만 유효:

  1) Consensus filter
     - 같은 날짜에 N개 이상 전략이 동일 방향 시그널 → 합의 신호
     - 단일 전략 시그널은 'weak', N>=2면 'strong'

  2) ATR 변동성 필터
     - 최근 N봉 ATR < 평균 ATR * 0.5 → 너무 잔잔, 신호 약화
     - 최근 ATR > 평균 ATR * 2.0 → 극단 변동성, 추격매수 위험

  3) 장기 EMA 추세 필터
     - 가격이 EMA200 위 + buy 시그널 → 강세 추세 매수 OK
     - EMA200 아래 + buy 시그널 → 역행 매수, 약화
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, List

import numpy as np
import pandas as pd


def consensus_signals(markers: List[Dict[str, Any]],
                      min_agree: int = 2,
                      same_day: bool = True) -> List[Dict[str, Any]]:
    """
    여러 전략의 markers → 합의 시그널만 추출.

    Parameters
    ----------
    markers : [{ts, kind: 'buy'|'sell', strategy}, ...]
    min_agree : 같은 날짜에 동일 방향 N개 이상 일치해야 confirmed
    same_day : True면 정확히 같은 날짜만 (False면 ±1봉 허용 — 현재 미구현)

    Returns
    -------
    [{ts, kind, strategies: [...], strength: 'confirmed'|'weak',
      agreement: int}]
    """
    grouped = defaultdict(lambda: {"buy": [], "sell": []})
    for m in markers:
        ts, k = m.get("ts"), m.get("kind")
        if not ts or k not in ("buy", "sell"):
            continue
        grouped[ts][k].append(m.get("strategy", "?"))

    out = []
    for ts in sorted(grouped.keys()):
        for kind in ("buy", "sell"):
            strats = grouped[ts][kind]
            if not strats:
                continue
            n = len(strats)
            strength = "confirmed" if n >= min_agree else "weak"
            out.append({
                "ts": ts, "kind": kind, "strategies": strats,
                "agreement": n, "strength": strength,
            })
    return out


def apply_market_filters(signals: List[Dict[str, Any]],
                         price_df: pd.DataFrame,
                         atr_window: int = 14,
                         atr_min_mult: float = 0.5,
                         atr_max_mult: float = 2.0,
                         ema_long: int = 200,
                         psr_data: Dict[str, float] = None,
                         dsr_data: Dict[str, float] = None
                         ) -> List[Dict[str, Any]]:
    """
    Consensus 시그널에 시장 필터 적용 → 추가 메타 부착.

    price_df: OHLCV DataFrame (index가 시그널 ts와 매칭 가능해야)

    각 시그널에 다음 추가:
      - atr_state: 'low'|'normal'|'extreme'
      - trend_state: 'up'|'down'|'unknown'  (EMA200 기준)
      - aligned_trend: bool (시그널이 추세와 일치)
      - confidence: 0.0~1.0 (합산 점수)
    """
    if len(price_df) < ema_long:
        for s in signals:
            s.update({"atr_state": "unknown",
                      "trend_state": "unknown",
                      "aligned_trend": False,
                      "confidence": 0.5})
        return signals

    high, low, close = price_df["high"], price_df["low"], price_df["close"]
    # ATR
    tr = pd.concat([
        high - low,
        (high - close.shift(1)).abs(),
        (low - close.shift(1)).abs(),
    ], axis=1).max(axis=1)
    atr = tr.rolling(atr_window).mean()
    atr_avg = atr.rolling(60, min_periods=20).mean()
    # EMA200
    ema = close.ewm(span=ema_long, adjust=False).mean()

    for s in signals:
        ts = s["ts"]
        try:
            ts_pd = pd.Timestamp(ts)
            # 가장 가까운 인덱스 찾기
            if ts_pd not in price_df.index:
                # ±1일 이내 탐색
                near = price_df.index[
                    abs(price_df.index - ts_pd) < pd.Timedelta(days=2)]
                if len(near) == 0:
                    ts_pd = price_df.index[-1]
                else:
                    ts_pd = near[0]
        except Exception:
            ts_pd = price_df.index[-1]

        a = atr.loc[ts_pd] if ts_pd in atr.index else None
        aa = atr_avg.loc[ts_pd] if ts_pd in atr_avg.index else None
        if a is None or aa is None or np.isnan(a) or np.isnan(aa):
            atr_state = "unknown"
        elif a < aa * atr_min_mult:
            atr_state = "low"
        elif a > aa * atr_max_mult:
            atr_state = "extreme"
        else:
            atr_state = "normal"

        e = ema.loc[ts_pd] if ts_pd in ema.index else None
        p = close.loc[ts_pd] if ts_pd in close.index else None
        if e is None or p is None or np.isnan(e):
            trend_state = "unknown"
        elif p > e:
            trend_state = "up"
        else:
            trend_state = "down"

        # 정렬: buy + up trend OR sell + down trend
        aligned = ((s["kind"] == "buy" and trend_state == "up") or
                   (s["kind"] == "sell" and trend_state == "down"))

        # confidence 점수 합산
        score = 0.4  # 기본
        # consensus 강화
        if s.get("strength") == "confirmed":
            score += 0.20
        if s.get("agreement", 1) >= 3:
            score += 0.08
        # 추세 정렬
        if aligned:
            score += 0.12
        elif trend_state in ("up", "down"):
            score -= 0.10
        # ATR
        if atr_state == "normal":
            score += 0.08
        elif atr_state == "low":
            score -= 0.05
        elif atr_state == "extreme":
            score -= 0.10
        # B10: PSR (통계적 유의도) — 시그널 발생 전략들의 평균 PSR
        psr_avg = None
        dsr_avg = None
        strats = s.get("strategies", [])
        if psr_data and strats:
            psrs = [psr_data[st] for st in strats if st in psr_data]
            if psrs:
                psr_avg = sum(psrs) / len(psrs)
                # PSR >= 0.95 = 매우 유의 → +0.15
                # PSR >= 0.85 → +0.10
                # PSR >= 0.70 → +0.05
                # PSR < 0.50 → -0.15 (통계적 무의미 → 거의 거부)
                if psr_avg >= 0.95:
                    score += 0.15
                elif psr_avg >= 0.85:
                    score += 0.10
                elif psr_avg >= 0.70:
                    score += 0.05
                elif psr_avg < 0.50:
                    score -= 0.15
        # B10: DSR (다중 검정 보정) — 보너스만
        if dsr_data and strats:
            dsrs = [dsr_data[st] for st in strats if st in dsr_data]
            if dsrs:
                dsr_avg = sum(dsrs) / len(dsrs)
                if dsr_avg >= 0.90:
                    score += 0.07
                elif dsr_avg < 0.40:
                    score -= 0.05
        score = max(0.0, min(1.0, score))

        s.update({
            "atr_state": atr_state,
            "trend_state": trend_state,
            "aligned_trend": aligned,
            "confidence": round(score, 2),
            "psr_avg": round(psr_avg, 3) if psr_avg is not None else None,
            "dsr_avg": round(dsr_avg, 3) if dsr_avg is not None else None,
        })
    return signals


def filter_for_alert(signals: List[Dict[str, Any]],
                     min_confidence: float = 0.7) -> List[Dict[str, Any]]:
    """알림 표시용 — confidence >= 임계 + confirmed만."""
    return [s for s in signals
            if s.get("strength") == "confirmed"
            and s.get("confidence", 0) >= min_confidence]
