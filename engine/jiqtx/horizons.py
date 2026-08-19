# -*- coding: utf-8 -*-
"""
단기 · 중기 · 장기 다지평 분석
==============================
같은 종목도 보는 기간에 따라 결론이 달라진다. 그 자체가 정보다.

**점수를 합치지 않는다.**
예전 리포트는 단기 55점 · 중기 72점 · 장기 74점을 기간가중 평균해
67점(BUY)을 만들었다. 성질이 다른 기간의 결론을 하나로 뭉개면
"장기 구조적 상승 안의 중기 조정" 같은 정보가 통째로 사라진다.
평균 하나만 남고, 그 평균은 어느 기간에도 해당하지 않는다.

그래서 이 모듈은 **지평별로 따로 계산하고, 서로 어긋나는 지점을 찾아
드러내는 것**을 목적으로 한다. 종합 점수는 만들지 않는다.

지평
----
    단기  63영업일  (약 3개월)
    중기  252영업일 (약 1년)
    장기  1260영업일(약 5년)

각 지평에서 보는 것
-------------------
- 누적수익 · 연율화 변동성 · 샤프 · 최대낙폭
- 추세: SMA 기울기 · SMA 상회 비율 · 골든/데드크로스
- 드리프트 μ̂ 와 표준오차 σ/√T → t값
  (짧은 지평일수록 SE 가 커서 μ̂ 가 의미를 잃는다. 그 사실을 표시한다.)
- 시장 베타 (지평별) → 베타가 기간에 따라 흔들리는지

불일치 탐지
-----------
- 추세 방향이 지평 간 갈리는가
- 샤프 부호가 뒤집히는가
- 변동성 기간구조 (단기 > 장기 = 확대 국면)
- 베타가 지평 간 크게 다른가 (구조 변화 신호)
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

# 지평 정의 — (코드, 한글명, 영업일)
HORIZONS = [
    ("short",  "단기", 63),
    ("mid",    "중기", 252),
    ("long",   "장기", 1260),
]


@dataclass
class HorizonStat:
    code: str
    label_ko: str
    days: int
    n_obs: int
    start: str
    end: str
    price_start: float
    price_end: float

    cum_return: float
    ann_vol: float
    sharpe: float
    max_drawdown: float

    sma_slope_pct: float          # 지평 길이의 SMA 기울기 (%)
    above_sma_ratio: float        # 종가가 SMA 위에 있던 비율
    ma_cross: str                 # 골든크로스 / 데드크로스 / —
    trend: str                    # 상승 / 하락 / 혼조

    drift_ann: float              # μ̂ (연율화)
    drift_se: float               # σ/√T (연율화)
    drift_t: float                # μ̂ / SE
    drift_meaningful: bool        # |t| >= 2

    beta_mkt: Optional[float] = None
    r2_mkt: Optional[float] = None
    note: str = ""


@dataclass
class HorizonPanel:
    stats: List[HorizonStat]
    disagreements: List[str] = field(default_factory=list)
    vol_term_structure: str = ""
    summary: str = ""

    def by_code(self, code: str) -> Optional[HorizonStat]:
        for s in self.stats:
            if s.code == code:
                return s
        return None


# ---------------------------------------------------------------- 유틸

def _max_dd(prices: np.ndarray) -> float:
    if len(prices) < 2:
        return float("nan")
    peak = np.maximum.accumulate(prices)
    dd = prices / np.where(peak > 0, peak, np.nan) - 1.0
    return float(np.nanmin(dd))


def _trend_label(slope_pct: float, above_ratio: float, cross: str) -> str:
    """기울기·상회비율·크로스가 서로 다른 말을 하면 '혼조'로 둔다."""
    votes = 0
    if np.isfinite(slope_pct):
        votes += 1 if slope_pct > 0 else -1
    if np.isfinite(above_ratio):
        votes += 1 if above_ratio > 0.5 else -1
    if cross == "골든크로스":
        votes += 1
    elif cross == "데드크로스":
        votes -= 1
    if votes >= 2:
        return "상승"
    if votes <= -2:
        return "하락"
    return "혼조"


def _one(prices: pd.Series, code: str, label: str, days: int,
         ann: int = 252, mkt: Optional[pd.Series] = None) -> Optional[HorizonStat]:
    px = prices.dropna().astype(float)
    if len(px) < 30:
        return None
    win = px.iloc[-days:] if len(px) > days else px
    n = len(win)
    if n < 20:
        return None

    r = np.diff(np.log(win.values))
    r = r[np.isfinite(r)]
    if len(r) < 10:
        return None

    sd = float(np.std(r, ddof=1))
    ann_vol = sd * math.sqrt(ann)
    cum = float(win.iloc[-1] / win.iloc[0] - 1.0)
    mu_ann = float(np.mean(r)) * ann
    # 드리프트 표준오차 = σ/√T. 짧은 지평일수록 커진다.
    se_ann = sd * math.sqrt(ann) / math.sqrt(len(r))
    t = mu_ann / se_ann if se_ann > 0 else float("nan")
    sharpe = (mu_ann / ann_vol) if ann_vol > 0 else float("nan")

    # 추세 — 지평 길이에 맞춘 SMA
    w = max(5, min(len(win) // 3, days // 3))
    sma = win.rolling(w).mean()
    valid = sma.dropna()
    if len(valid) >= 2 and valid.iloc[0] != 0:
        slope_pct = float((valid.iloc[-1] / valid.iloc[0] - 1.0) * 100.0)
    else:
        slope_pct = float("nan")
    above = float((win > sma).mean()) if len(valid) else float("nan")

    # 단기/장기 MA 교차
    cross = "—"
    if len(win) >= 40:
        f = win.rolling(max(5, w // 2)).mean().iloc[-1]
        s = win.rolling(w).mean().iloc[-1]
        if np.isfinite(f) and np.isfinite(s):
            cross = "골든크로스" if f > s else "데드크로스"

    # 지평별 시장 베타
    beta = r2 = None
    if mkt is not None:
        m = mkt.reindex(win.index).ffill().dropna()
        common = win.index.intersection(m.index)
        if len(common) > 30:
            rr = np.diff(np.log(win.reindex(common).values))
            mm = np.diff(np.log(m.reindex(common).values))
            ok = np.isfinite(rr) & np.isfinite(mm)
            if ok.sum() > 20 and np.var(mm[ok]) > 0:
                beta = float(np.cov(mm[ok], rr[ok], ddof=1)[0, 1] / np.var(mm[ok], ddof=1))
                pred = beta * mm[ok]
                denom = np.var(rr[ok])
                r2 = float(1 - np.var(rr[ok] - pred) / denom) if denom > 0 else None

    return HorizonStat(
        code=code, label_ko=label, days=days, n_obs=n,
        start=str(win.index[0].date()), end=str(win.index[-1].date()),
        price_start=float(win.iloc[0]), price_end=float(win.iloc[-1]),
        cum_return=cum, ann_vol=ann_vol, sharpe=sharpe,
        max_drawdown=_max_dd(win.values),
        sma_slope_pct=slope_pct, above_sma_ratio=above, ma_cross=cross,
        trend=_trend_label(slope_pct, above, cross),
        drift_ann=mu_ann, drift_se=se_ann, drift_t=t,
        drift_meaningful=bool(np.isfinite(t) and abs(t) >= 2.0),
        beta_mkt=beta, r2_mkt=r2,
    )


# ---------------------------------------------------------------- 본체

def analyze_horizons(prices: pd.Series, ann: int = 252,
                     mkt: Optional[pd.Series] = None) -> HorizonPanel:
    """지평별로 따로 계산하고, 어긋나는 지점을 찾는다."""
    stats: List[HorizonStat] = []
    for code, label, days in HORIZONS:
        s = _one(prices, code, label, days, ann=ann, mkt=mkt)
        if s is not None:
            stats.append(s)

    dis: List[str] = []
    if len(stats) >= 2:
        # 추세 불일치
        trends = {s.label_ko: s.trend for s in stats}
        uniq = set(v for v in trends.values() if v != "혼조")
        if len(uniq) > 1:
            dis.append("추세 방향이 지평마다 다르다 — "
                       + " · ".join(f"{k} {v}" for k, v in trends.items())
                       + ". 하나의 '추세'로 요약할 수 없다.")

        # 샤프 부호 역전
        signs = {s.label_ko: (1 if s.sharpe > 0 else -1)
                 for s in stats if np.isfinite(s.sharpe)}
        if len(set(signs.values())) > 1:
            txt = " · ".join(f"{s.label_ko} {s.sharpe:+.2f}" for s in stats
                             if np.isfinite(s.sharpe))
            dis.append(f"샤프 부호가 지평 간 뒤집힌다 — {txt}. "
                       "어느 하나만 인용하면 정반대 결론이 나온다.")

        # 베타 불안정
        betas = [(s.label_ko, s.beta_mkt) for s in stats
                 if s.beta_mkt is not None and np.isfinite(s.beta_mkt)]
        if len(betas) >= 2:
            vals = [b for _, b in betas]
            if max(vals) - min(vals) > 0.5:
                dis.append("시장 베타가 지평 간 "
                           + " · ".join(f"{k} {v:+.2f}" for k, v in betas)
                           + " 로 크게 다르다 — 구조 변화 가능성.")

    # 변동성 기간구조
    vts = ""
    sh = next((s for s in stats if s.code == "short"), None)
    lg = next((s for s in stats if s.code == "long"), None)
    if sh and lg and np.isfinite(sh.ann_vol) and np.isfinite(lg.ann_vol) \
            and lg.ann_vol > 0:
        ratio = sh.ann_vol / lg.ann_vol
        if ratio > 1.25:
            vts = (f"확대 — 단기 {sh.ann_vol:.1%} 가 장기 {lg.ann_vol:.1%} 의 "
                   f"{ratio:.2f}배. '보통' 같은 절대 라벨보다 "
                   "자기 장기 수준 대비 확대됐다는 상대 비교가 정확하다.")
        elif ratio < 0.8:
            vts = (f"축소 — 단기 {sh.ann_vol:.1%} 가 장기 {lg.ann_vol:.1%} 의 "
                   f"{ratio:.2f}배. 조용한 국면이지만 평균 회귀에 유의.")
        else:
            vts = (f"평상 — 단기 {sh.ann_vol:.1%} · 장기 {lg.ann_vol:.1%} "
                   f"({ratio:.2f}배).")

    # 요약 — 종합 '점수'가 아니라 상태 서술
    summary = ""
    if stats:
        parts = [f"{s.label_ko} {s.trend}({s.cum_return:+.1%})" for s in stats]
        summary = " · ".join(parts)
        if dis:
            summary += f" — 지평 간 불일치 {len(dis)}건"

    return HorizonPanel(stats=stats, disagreements=dis,
                        vol_term_structure=vts, summary=summary)
