# ==============================================================================
# [24/25] replay.py — 워크포워드 리플레이
# ==============================================================================

"""
jiqtx.replay — 워크포워드 리플레이 하니스.

무엇을 하는가
-------------
과거 여러 시점으로 데이터를 **잘라내고**(point-in-time) 분석기를 그대로 돌려
그때의 예측을 원장에 남긴 뒤, 실현값으로 채점한다.

왜 필요한가
-----------
게이트·DSR·PBO는 ML 모듈 하나에 대한 진단이다. 그런데 실제로 사용자가 보는
것은 **판정 엔진의 최종 출력**이다. 그 출력이 잘 맞는지는 시스템 전체를
워크포워드로 재생해봐야 안다.

원장이 비어 있으면 에이전트 가중치를 줄 수 없으므로, 리플레이는
**원장을 부트스트랩하는 수단**이기도 하다.

주의
----
- 리플레이는 '과거에 이 코드를 돌렸다면'의 근사다. 코드 자체는 전체 기간을
  보고 개발됐으므로 순수 OOS가 아니다(개발자 look-ahead).
  진짜 검증은 사전등록 후의 **실시간 페이퍼 트레이딩**이다.
- 매크로/프록시도 동일 시점으로 잘라내야 한다. 이 모듈은 그것을 강제한다.
"""

import os
import time
import warnings
from dataclasses import replace
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

# ── 패키지 내부 의존 ──────────────────────────────────────────
from .config import RUN, RunConfig
from .ledger import Ledger, price_lookup_from_frame
from .pipeline import analyze




def replay(assets: Dict[str, Tuple[pd.DataFrame, Dict]],
           macro: Optional[pd.DataFrame] = None,
           proxies: Optional[Dict[str, pd.Series]] = None,
           horizon_days: int = 21,
           step_days: int = 21,
           n_points: int = 20,
           min_history: int = 756,
           cfg: RunConfig = RUN,
           ledger_path: Optional[str] = None,
           aum_usd: float = 2e7,
           with_ml: bool = False,
           verbose: bool = True) -> Tuple[Ledger, pd.DataFrame]:
    """
    assets : {ticker: (전체 OHLCV DataFrame, meta)}
    반환   : (Ledger, 리플레이 로그 DataFrame)
    """
    led = Ledger(ledger_path) if ledger_path else Ledger()
    fast = replace(cfg, n_sims=max(cfg.n_sims // 4, 3000))
    log: List[Dict] = []

    # 공통 인덱스에서 리플레이 시점 산출
    any_df = next(iter(assets.values()))[0]
    idx = pd.DatetimeIndex(any_df.index)
    end_pos = len(idx) - horizon_days - 1
    positions = [end_pos - k * step_days for k in range(n_points)]
    positions = sorted([p for p in positions if p >= min_history])
    if not positions:
        raise ValueError(f"이력 부족: {len(idx)}행으로는 리플레이 불가 "
                         f"(최소 {min_history + horizon_days}행 필요)")

    if verbose:
        print(f"리플레이 시점 {len(positions)}개 × 자산 {len(assets)}개 "
              f"= {len(positions)*len(assets)}회 분석")

    t0 = time.time()
    for k, pos in enumerate(positions):
        cut = idx[pos]
        m_cut = macro.loc[macro.index <= cut] if macro is not None else None
        p_cut = ({kk: vv.loc[vv.index <= cut] for kk, vv in proxies.items()}
                 if proxies else None)
        for tk, (df, meta) in assets.items():
            d_cut = df.loc[df.index <= cut]
            if len(d_cut) < min_history:
                continue
            try:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    a = analyze(tk, df=d_cut, meta=meta, macro=m_cut,
                                proxies=p_cut, cfg=fast, aum_usd=aum_usd,
                                with_options=False, with_ml=with_ml,
                                fast=True, verbose=False)
                pid = led.record(a, horizon_days=horizon_days,
                                 notes=f"replay@{cut.date()}")
                log.append({
                    "asof": str(cut.date()), "ticker": tk, "pred_id": pid,
                    "grade": a.verdict.grade, "prob": a.verdict.direction_prob,
                    "size": a.verdict.risk_budget_weight,
                    "conf": a.verdict.model_confidence,
                    "asset_class": a.classification.asset_class,
                    "archetype": (a.equity.archetype if a.equity else None),
                })
            except Exception as e:
                log.append({"asof": str(cut.date()), "ticker": tk,
                            "pred_id": None, "grade": "ERROR", "prob": np.nan,
                            "size": np.nan, "conf": str(e)[:60],
                            "asset_class": None, "archetype": None})
        if verbose:
            done = (k + 1) * len(assets)
            tot = len(positions) * len(assets)
            print(f"  [{time.time()-t0:6.1f}s] {cut.date()} "
                  f"({done}/{tot})", flush=True)

    # 채점
    price_map = {tk: df["Close"].astype(float) for tk, (df, _) in assets.items()}
    res = led.score(price_lookup_from_frame(price_map),
                    as_of=str(idx[-1].date()))
    if verbose:
        print(f"채점: {res}")
    return led, pd.DataFrame(log)


def print_report(led: Ledger, top: int = 30) -> None:
    rep = led.calibration_report()
    if not rep:
        print("채점된 예측이 없습니다.")
        return
    print("\n" + "=" * 74)
    print("시스템 캘리브레이션 리포트 (워크포워드 리플레이 기반)")
    print("=" * 74)
    order = ["에이전트", "등급별 실현수익", "자산군", "아키타입", "등급",
             "모델신뢰도", "ML판정"]
    for name in order:
        if name not in rep or rep[name] is None or len(rep[name]) == 0:
            continue
        print(f"\n── {name} " + "─" * max(0, 60 - len(name)))
        print(rep[name].round(4).to_string())
    w = led.agent_weights()
    if w:
        print("\n── 실적 기반 에이전트 가중치 (판정 엔진으로 환류) " + "─" * 14)
        for k, v in sorted(w.items(), key=lambda kv: -kv[1]):
            print(f"   {k:<24} {v:.3f}")
    print("\n" + "=" * 74)
    print(f"원장 요약: {led.summary()}")
    print("=" * 74)
