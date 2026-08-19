# -*- coding: utf-8 -*-
"""
거시경제 대시보드 — 자산군에 맞는 변수만, 서술이 아니라 계산으로
================================================================
금 리포트에는 실질금리·달러·기대인플레가 있어야 하고, 국채에는 명목금리와
커브가, 개별주에는 시장·VIX·크레딧 스프레드가 있어야 한다. 같은 표를
모든 자산에 붙이면 대부분의 행이 무의미해진다.

**핵심: '영향' 열을 손으로 쓰지 않는다.**
"실질금리 2.44% → 명확한 역풍" 같은 문장은 사람이 쓰면 그럴듯하지만
검증할 수 없다. 여기서는 이렇게 정한다.

    영향 = sign(추정 베타) × sign(최근 변화)

베타는 델타 패널의 단변량 회귀에서 온다. 그리고 **|t| < 2 이면
통계적으로 0과 구별되지 않으므로 방향을 말하지 않고 '중립'** 으로 둔다.
같은 종목·같은 날짜면 같은 표가 나온다 — 감사 가능하다.

구성
----
- 변수별: 최신값 · 1M/3M 변화 · 베타(t) · 영향 · 해석
- 전술적 판단: Σ(베타 × 3M 변화) — 최근 석 달 거시가 준 순방향
- 구조적 판단: 자산군 명세의 고유 주의사항
- 핵심 전환 신호: |베타 × 변수 표준편차| 상위 2개가 뒤집힐 때
- 시나리오 매트릭스: 상위 변수의 상승/하락 조합
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

# 팩터 패널이 각 변수를 어떤 단위로 변환하는지 (factors.build_factor_panel).
# 베타는 그 단위로 추정됐으므로, '최근 변화'도 같은 단위로 재야 한다.
# 예전에는 달러지수의 변화를 지수 포인트(-0.55)로 재고 로그수익률 기준
# 베타(-1.425)와 곱해서, 기여도가 +78% 라는 말도 안 되는 값이 나왔다.
_LOGRET_VARS = {"broad_dollar", "wti", "eurusd", "mkt_excess"}

# 거시 변수 한글명 · 단위 · 표시 형식
MACRO_META: Dict[str, Dict[str, Any]] = {
    "real_yield_10y": {"ko": "10년 실질금리", "unit": "%", "fmt": "{:.2f}%",
                       "src": "FRED DFII10"},
    "real_yield_5y":  {"ko": "5년 실질금리", "unit": "%", "fmt": "{:.2f}%",
                       "src": "FRED DFII5"},
    "breakeven_10y":  {"ko": "10년 기대인플레이션", "unit": "%", "fmt": "{:.2f}%",
                       "src": "FRED T10YIE"},
    "broad_dollar":   {"ko": "광의 달러지수", "unit": "pt", "fmt": "{:.2f}",
                       "src": "FRED DTWEXBGS"},
    "nominal_10y":    {"ko": "10년 국채금리", "unit": "%", "fmt": "{:.2f}%",
                       "src": "FRED DGS10"},
    "nominal_2y":     {"ko": "2년 국채금리", "unit": "%", "fmt": "{:.2f}%",
                       "src": "FRED DGS2"},
    "curve_2s10s":    {"ko": "수익률곡선 2s10s", "unit": "%p", "fmt": "{:+.2f}%p",
                       "src": "FRED T10Y2Y"},
    "hy_oas":         {"ko": "하이일드 스프레드", "unit": "%p", "fmt": "{:.2f}%p",
                       "src": "FRED BAMLH0A0HYM2"},
    "ig_oas":         {"ko": "투자등급 스프레드", "unit": "%p", "fmt": "{:.2f}%p",
                       "src": "FRED BAMLC0A0CM"},
    "vix":            {"ko": "VIX 변동성지수", "unit": "pt", "fmt": "{:.2f}",
                       "src": "FRED VIXCLS"},
    "wti":            {"ko": "WTI 유가", "unit": "$", "fmt": "${:.2f}",
                       "src": "FRED DCOILWTICO"},
    "eurusd":         {"ko": "EUR/USD", "unit": "", "fmt": "{:.4f}",
                       "src": "FRED DEXUSEU"},
    "mkt_excess":     {"ko": "주식시장 (SPY)", "unit": "", "fmt": "{:.2f}",
                       "src": "SPY"},
    "gpr":            {"ko": "지정학 리스크 지수", "unit": "", "fmt": "{:.1f}",
                       "src": "Caldara-Iacoviello GPR"},
}


@dataclass
class MacroRow:
    key: str
    label_ko: str
    latest: float
    latest_str: str
    as_of: str
    chg_1m: float
    chg_3m: float
    beta: Optional[float]
    tstat: Optional[float]
    significant: bool
    impact: str            # 지지 / 역풍 / 중립 / 판단보류
    contribution: float    # 베타 × 3M 변화 (최근 석 달 기여)
    comment: str
    source: str


@dataclass
class MacroBoard:
    rows: List[MacroRow]
    tactical: str = ""          # 전술적 거시 판단
    tactical_detail: str = ""
    structural: str = ""        # 구조적 거시 판단
    structural_detail: str = ""
    pivot: str = ""             # 핵심 전환 신호
    pivot_detail: str = ""
    scenarios: List[Dict[str, str]] = field(default_factory=list)
    sources: List[str] = field(default_factory=list)
    note: str = ""


def _fmt(key: str, v: float) -> str:
    meta = MACRO_META.get(key, {})
    try:
        return meta.get("fmt", "{:.2f}").format(float(v))
    except Exception:
        return "—"


def _impact_label(beta: Optional[float], chg: float,
                  significant: bool) -> str:
    """
    영향은 손으로 쓰지 않는다. 베타 부호 × 최근 변화 방향.
    통계적으로 0과 구별되지 않는 베타로는 방향을 말하지 않는다.
    """
    if beta is None or not np.isfinite(beta) or not np.isfinite(chg):
        return "판단보류"
    if not significant:
        return "중립"
    eff = beta * chg
    if abs(eff) < 1e-4:
        return "중립"
    return "지지" if eff > 0 else "역풍"


def _tstr(t: Optional[float]) -> str:
    """t 값이 없을 수도 있다(델타 패널이 t 를 못 낸 경우)."""
    return f"t={t:+.1f}" if (t is not None and np.isfinite(t)) else "t 미산출"


def _comment(key: str, beta: Optional[float], t: Optional[float],
             chg3: float, significant: bool) -> str:
    meta = MACRO_META.get(key, {})
    ko = meta.get("ko", key)
    if beta is None or not np.isfinite(beta):
        return f"{ko} 민감도를 추정할 표본이 부족합니다."
    if not significant:
        return (f"베타 {beta:+.3f} ({_tstr(t)}) — 통계적으로 0과 구별되지 "
                f"않습니다. 방향을 단정할 근거가 없습니다.")
    direction = "오르면 유리" if beta > 0 else "오르면 불리"
    move = "상승" if (np.isfinite(chg3) and chg3 > 0) else (
        "하락" if (np.isfinite(chg3) and chg3 < 0) else "보합")
    return (f"베타 {beta:+.3f} ({_tstr(t)}) — {ko}가 {direction}. "
            f"최근 3개월 {move} 방향이었습니다.")


def build_macro_board(a) -> Optional[MacroBoard]:
    """
    Analysis 객체에서 거시 대시보드를 만든다.

    쓰는 것: 자산군 명세(관련 변수), 델타 패널(베타·t), 매크로 원본(수준·변화).
    """
    spec = getattr(getattr(a, "classification", None), "spec", None)
    macro = getattr(a, "macro", None)
    dp = getattr(a, "delta_panel", None)
    if spec is None or macro is None or not len(macro):
        return None

    # 이 자산군에서 볼 변수 = 팩터 prior + 스트레스 축 (매크로에 있는 것만)
    wanted: List[str] = []
    for k in list(getattr(spec, "factor_prior", []) or []) + \
             list((getattr(spec, "stress", {}) or {}).keys()):
        if k not in wanted and k in macro.columns:
            wanted.append(k)
    if not wanted:
        return None

    # 델타 패널에서 베타·t
    betas: Dict[str, float] = {}
    tstats: Dict[str, float] = {}
    if dp is not None and len(dp):
        for _, r in dp.iterrows():
            f = r.get("factor")
            if f is None:
                continue
            # t_stat 은 전체표본 정적 베타(beta_static)의 값이다.
            # beta_now(시변 현재값)와 부호가 다를 수 있는데, 그 둘을 섞으면
            # "베타 +0.004 (t=-16.6)" 처럼 앞뒤가 안 맞는 표가 나온다.
            # 유의성을 t 로 판정하므로 베타도 같은 추정치를 쓴다.
            b = r.get("beta_static")
            if b is None or not np.isfinite(b):
                b = r.get("beta_now")
            if b is not None and np.isfinite(b):
                betas[f] = float(b)
            # 델타 패널의 컬럼명은 't_stat' 이다 ('tstat' 아님).
            t = r.get("t_stat")
            if t is None:
                t = r.get("tstat")
            if t is not None and np.isfinite(t):
                tstats[f] = float(t)

    mf = macro.ffill()
    rows: List[MacroRow] = []
    for k in wanted:
        s = mf[k].dropna()
        if len(s) < 70:
            continue
        latest = float(s.iloc[-1])
        as_of = str(s.index[-1].date())

        # 표시용 변화 (사람이 읽는 단위) 와 계산용 변화 (베타의 단위) 를 나눈다
        chg1 = float(latest - s.iloc[-22]) if len(s) > 22 else float("nan")
        chg3 = float(latest - s.iloc[-66]) if len(s) > 66 else float("nan")
        if k in _LOGRET_VARS:
            prev3 = float(s.iloc[-66]) if len(s) > 66 else float("nan")
            chg3_beta = (math.log(latest / prev3)
                         if (np.isfinite(prev3) and prev3 > 0 and latest > 0)
                         else float("nan"))
        else:
            chg3_beta = chg3

        b = betas.get(k)
        t = tstats.get(k)
        sig = bool(t is not None and np.isfinite(t) and abs(t) >= 2.0)
        contrib = float(b * chg3_beta) if (b is not None and np.isfinite(b)
                                           and np.isfinite(chg3_beta)) else 0.0

        rows.append(MacroRow(
            key=k, label_ko=MACRO_META.get(k, {}).get("ko", k),
            latest=latest, latest_str=_fmt(k, latest), as_of=as_of,
            chg_1m=chg1, chg_3m=chg3, beta=b, tstat=t, significant=sig,
            impact=_impact_label(b, chg3_beta, sig), contribution=contrib,
            comment=_comment(k, b, t, chg3, sig),
            source=MACRO_META.get(k, {}).get("src", "FRED"),
        ))

    if not rows:
        return None

    # ── 전술적 판단 — 최근 3개월 거시 기여 합
    sig_rows = [r for r in rows if r.significant]
    total = sum(r.contribution for r in sig_rows)
    if not sig_rows:
        tactical, tdetail = "판단보류", (
            "유의한 거시 민감도가 확인되지 않았습니다. "
            "이 자산의 최근 움직임을 거시 변수로 설명하기 어렵습니다.")
    elif total > 0.02:
        tactical = "우호적"
        tdetail = f"최근 3개월 거시 기여 합 {total:+.1%}"
    elif total < -0.02:
        tactical = "약세 우위"
        tdetail = f"최근 3개월 거시 기여 합 {total:+.1%}"
    else:
        tactical = "중립"
        tdetail = f"최근 3개월 거시 기여 합 {total:+.1%} — 방향성 미미"
    if sig_rows:
        worst = min(sig_rows, key=lambda r: r.contribution)
        best = max(sig_rows, key=lambda r: r.contribution)
        tdetail += (f" · 최대 역풍 {worst.label_ko}({worst.contribution:+.1%})"
                    f" · 최대 지지 {best.label_ko}({best.contribution:+.1%})")

    # ── 구조적 판단 — 자산군 고유 성질
    structural = "자산군 고유 요인"
    sdetail = (getattr(spec, "notes", "") or "").strip() or \
        f"{spec.label_ko}의 기대 팩터 설명력 밴드는 " \
        f"{spec.r2_band[0]:.0%}~{spec.r2_band[1]:.0%} 입니다."

    # ── 핵심 전환 신호 — 영향력(|베타 × 변수 σ|) 상위 2개
    ranked = []
    for r in sig_rows:
        s = mf[r.key].dropna()
        sd = float(s.diff().tail(252).std()) if len(s) > 60 else float("nan")
        if r.beta is not None and np.isfinite(sd):
            ranked.append((abs(r.beta * sd), r))
    ranked.sort(key=lambda x: -x[0])
    top = [r for _, r in ranked[:2]]
    if top:
        parts = []
        for r in top:
            arrow = "↓" if (r.beta or 0) > 0 else "↑"
            # 베타가 양수면 변수가 내려갈 때 불리 → 유리해지려면 올라가야 함
            arrow = "↑" if (r.beta or 0) > 0 else "↓"
            parts.append(f"{r.label_ko}{arrow}")
        pivot = " + ".join(parts)
        pdetail = ("이 조건들이 동반될 때 거시 역풍이 지지로 바뀝니다. "
                   "각각은 아래 표의 베타 부호에서 유도된 방향입니다.")
    else:
        pivot, pdetail = "—", "유의한 거시 민감도가 없어 전환 신호를 정의할 수 없습니다."

    # ── 시나리오 매트릭스 — 상위 변수의 방향 조합
    # 각 변수의 '유리한 방향'은 베타 부호에서 나온다(베타>0 이면 상승이 유리).
    # 두 변수가 서로 다른 말을 하면 결과는 중립으로 둔다 — 그게 사실이다.
    scenarios: List[Dict[str, str]] = []
    if top:
        def _fav(r) -> bool:
            """이 변수가 '올라가는' 것이 유리한가."""
            return (r.beta or 0) > 0

        def _row(name, legs):
            """legs = [(row, 이 시나리오에서 상승하는가)]"""
            score = 0
            for r, up in legs:
                score += 1 if (up == _fav(r)) else -1
            direction = ("상승 우위" if score > 0 else
                         "하락 우위" if score < 0 else "중립")
            return {
                "name": name,
                "condition": " · ".join(
                    f"{r.label_ko} {'상승' if up else '하락'}" for r, up in legs),
                "direction": direction,
                "watch": " · ".join(r.source for r, _ in legs),
            }

        if len(top) >= 2:
            a0, a1 = top[0], top[1]
            scenarios = [
                _row("우호 전환", [(a0, _fav(a0)), (a1, _fav(a1))]),
                _row("혼조",     [(a0, _fav(a0)), (a1, not _fav(a1))]),
                _row("역풍 연장", [(a0, not _fav(a0)), (a1, not _fav(a1))]),
            ]
        else:
            a0 = top[0]
            scenarios = [
                _row("우호 전환", [(a0, _fav(a0))]),
                _row("역풍 연장", [(a0, not _fav(a0))]),
            ]

    return MacroBoard(
        rows=rows, tactical=tactical, tactical_detail=tdetail,
        structural=structural, structural_detail=sdetail,
        pivot=pivot, pivot_detail=pdetail, scenarios=scenarios,
        sources=sorted({r.source for r in rows}),
        note=("'영향' 열은 서술이 아니라 계산입니다 — sign(추정 베타) × "
              "sign(최근 3개월 변화). |t| < 2 인 변수는 통계적으로 0과 "
              "구별되지 않으므로 방향을 말하지 않고 중립으로 둡니다. "
              "베타는 단변량 추정이라 그 변수에 딸려 오는 동반 움직임을 "
              "포함합니다."),
    )
