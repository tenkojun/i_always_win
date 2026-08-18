# ==============================================================================
# [09/25] equity.py — 9개 주식 아키타입 · 어닝/PEAD · 점프 · 런웨이
# ==============================================================================

"""
jiqtx.equity — 개별 주식의 '성격'을 파악하는 모듈.

왜 필요한가
-----------
금이 실질금리·달러로 봐야 하듯, 개별 주식도 종목마다 봐야 할 것이 다르다.
 - 고성장 적자기업: PER 무의미. 매출성장·현금소진·희석이 핵심.
 - 배당 인컴주   : 배당 커버리지·payout·금리 민감도가 핵심.
 - 경기민감주    : 사이클 위치·재고·마진 레버리지가 핵심.
 - 바이오텍      : 이벤트 드리븐. 평균 통계가 무의미하고 점프 리스크가 지배.
 - 딥밸류        : 밸류에이션 갭보다 '가치 함정' 여부가 핵심.

본 모듈은 가격·팩터·펀더멘털에서 **아키타입(archetype)** 을 판정하고,
그 아키타입에 맞는 진단 항목·경고·밸류에이션 앵커를 결정한다.
이것이 보고서 섹션 구성을 동적으로 바꾼다.

주의: yfinance 펀더멘털은 point-in-time이 아니다(리스테이트먼트 반영).
      따라서 시계열 백테스트에 쓰면 안 되고, '현재 상태 진단'으로만 쓴다.
"""

import math
import warnings
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

# ── 패키지 내부 의존 ──────────────────────────────────────────
from .statcore import newey_west_se



# ================================================================ 아키타입

ARCHETYPES: Dict[str, Dict[str, Any]] = {
    "QUALITY_COMPOUNDER": {
        "ko": "우량 복리성장주",
        "desc": "높은 ROE·안정 마진·낮은 부채. 밸류에이션 프리미엄이 정당화되는지가 핵심.",
        "anchors": ["FCF 수익률", "ROIC vs WACC", "재투자율 × ROIC"],
        "watch": ["멀티플 축소 리스크", "성장 둔화 시 디레이팅 폭"],
        "sections": ["fundamentals", "earnings_event", "style", "idio", "peer"],
    },
    "HYPERGROWTH_UNPROFITABLE": {
        "ko": "고성장 적자기업",
        "desc": "PER 무의미. 매출성장·현금소진·희석·자금조달 접근성이 핵심.",
        "anchors": ["EV/Sales", "Rule of 40", "현금소진 잔여기간(runway)"],
        "watch": ["금리 상승 시 듀레이션 리스크 극대", "희석", "자금조달 창구 폐쇄"],
        "sections": ["fundamentals", "runway", "rate_sensitivity",
                     "earnings_event", "idio", "crowding"],
    },
    "DEEP_VALUE": {
        "ko": "딥밸류",
        "desc": "저PBR·저PER. 핵심은 '싸다'가 아니라 '가치 함정인가'.",
        "anchors": ["P/B vs ROE", "EV/EBIT", "청산가치"],
        "watch": ["가치 함정(구조적 쇠퇴)", "부채 만기", "실적 하향 지속"],
        "sections": ["fundamentals", "value_trap", "earnings_event", "peer"],
    },
    "DIVIDEND_INCOME": {
        "ko": "배당 인컴주",
        "desc": "총수익의 상당부분이 배당. 커버리지와 금리 민감도가 핵심.",
        "anchors": ["배당 커버리지", "payout ratio", "배당 성장률"],
        "watch": ["금리 상승 시 채권 대체재로서 매력 하락", "배당 삭감"],
        "sections": ["fundamentals", "dividend", "rate_sensitivity", "peer"],
    },
    "CYCLICAL": {
        "ko": "경기민감주",
        "desc": "사이클 위치가 밸류에이션보다 중요. 고점 PER이 저점 신호일 수 있음.",
        "anchors": ["정상화 이익(normalized EPS)", "사이클 조정 PER"],
        "watch": ["마진 레버리지(양방향)", "재고 사이클", "고점 실적 함정"],
        "sections": ["fundamentals", "cycle", "earnings_event", "peer",
                     "rate_sensitivity"],
    },
    "DEFENSIVE": {
        "ko": "경기방어주",
        "desc": "낮은 베타·안정 현금흐름. 금리와 상대 밸류에이션이 주 변수.",
        "anchors": ["배당수익률 vs 국채", "안정 마진"],
        "watch": ["금리 상승 시 상대 매력 하락", "저성장 디레이팅"],
        "sections": ["fundamentals", "rate_sensitivity", "dividend", "peer"],
    },
    "HIGH_BETA_SPECULATIVE": {
        "ko": "고베타 투기적",
        "desc": "높은 베타·높은 고유변동성. 포지션 사이징이 분석보다 중요.",
        "anchors": ["없음 — 밸류에이션 앵커 부재"],
        "watch": ["급락 시 갭 리스크", "유동성 증발", "숏스퀴즈/크라우딩"],
        "sections": ["idio", "crowding", "tail", "jump", "earnings_event"],
    },
    "EVENT_DRIVEN": {
        "ko": "이벤트 드리븐",
        "desc": "점프가 수익률을 지배. 평균·표준편차 기반 통계가 대부분 무의미.",
        "anchors": ["이벤트 확률 × 페이오프"],
        "watch": ["단일 이벤트 집중 리스크", "정규분포 가정 전면 붕괴"],
        "sections": ["jump", "tail", "idio", "earnings_event",
                     "fundamentals", "runway", "crowding"],
    },
    "DISTRESSED": {
        "ko": "부실/턴어라운드",
        "desc": "자본구조가 주가를 지배. 에쿼티는 사실상 콜옵션.",
        "anchors": ["순부채/EBITDA", "이자보상배율", "만기 스케줄"],
        "watch": ["유상증자 희석", "채무 재조정", "상장폐지"],
        "sections": ["fundamentals", "runway", "tail", "crowding"],
    },
    "UNCLASSIFIED": {
        "ko": "미분류",
        "desc": "펀더멘털 데이터 부족 또는 특성이 뚜렷하지 않음.",
        "anchors": ["—"],
        "watch": ["분류 불확실 → 최소 사이즈"],
        "sections": ["idio", "peer"],
    },
}


# ================================================================ 데이터 구조

@dataclass
class EarningsStudy:
    n_events: int
    next_date: Optional[str]
    days_to_next: Optional[int]
    mean_abs_move: float           # 발표 다음날 |수익률| 평균
    median_abs_move: float
    p90_abs_move: float
    max_abs_move: float
    mean_move: float
    beat_rate: float               # 서프라이즈 양수 비율
    pead_20d_pos: float            # 서프라이즈 양수 시 t+1~t+20 누적초과
    pead_20d_neg: float
    pead_spread: float             # 양수-음수 (PEAD 강도)
    pead_tstat: float
    gap_share_of_var: float        # 연 분산 중 어닝일이 차지하는 비중
    note: str


@dataclass
class Fundamentals:
    market_cap: float
    trailing_pe: float
    forward_pe: float
    price_to_book: float
    ev_to_ebitda: float
    ev_to_sales: float
    profit_margin: float
    operating_margin: float
    roe: float
    roa: float
    debt_to_equity: float
    current_ratio: float
    revenue_growth: float
    earnings_growth: float
    fcf: float
    fcf_yield: float
    dividend_yield: float
    payout_ratio: float
    beta_reported: float
    short_pct_float: float
    inst_ownership: float
    rule_of_40: float
    net_cash_ratio: float
    available: int                 # 확보된 필드 수
    pit_warning: str


@dataclass
class StyleTilt:
    loadings: Dict[str, float]
    tstats: Dict[str, float]
    r2: float
    dominant: str
    idio_vol_ann: float
    idio_share: float              # 총 분산 중 고유 비중
    residual_momentum_12_1: float  # Blitz-Huij-Martens 잔차 모멘텀
    raw_momentum_12_1: float


@dataclass
class JumpProfile:
    n_jumps: int                   # |r| > 4σ_t
    jump_rate_ann: float
    jump_share_of_var: float       # 점프가 분산에서 차지하는 비중
    largest_up: float
    largest_down: float
    jump_asymmetry: float
    continuous_vol_ann: float      # 점프 제거 후 변동성
    note: str


@dataclass
class PeerRelative:
    benchmark: str
    rel_return_1y: float
    rel_return_3m: float
    beta_to_bench: float
    corr_to_bench: float
    tracking_error: float
    information_ratio: float
    rel_strength_percentile: float


@dataclass
class EquityProfile:
    ticker: str
    archetype: str
    archetype_ko: str
    archetype_desc: str
    archetype_confidence: float
    archetype_evidence: List[str]
    valuation_anchors: List[str]
    watch_items: List[str]
    active_sections: List[str]
    fundamentals: Optional[Fundamentals]
    earnings: Optional[EarningsStudy]
    style: Optional[StyleTilt]
    jumps: Optional[JumpProfile]
    peer: Optional[PeerRelative]
    warnings: List[str]


# ================================================================ 펀더멘털

def _g(meta: Dict, *keys, default=np.nan) -> float:
    for k in keys:
        v = meta.get(k)
        if v is not None and isinstance(v, (int, float)) and np.isfinite(v):
            return float(v)
    return default


def extract_fundamentals(meta: Dict) -> Fundamentals:
    mc = _g(meta, "marketCap")
    ev = _g(meta, "enterpriseValue")
    rev = _g(meta, "totalRevenue")
    ebitda = _g(meta, "ebitda")
    fcf = _g(meta, "freeCashflow")
    dy = _g(meta, "dividendYield", default=0.0)
    if np.isfinite(dy) and dy > 1.0:          # yfinance가 % 로 주는 경우
        dy = dy / 100.0
    rg = _g(meta, "revenueGrowth")
    om = _g(meta, "operatingMargins")
    ro40 = ((rg + om) * 100.0) if np.isfinite(rg) and np.isfinite(om) else np.nan
    tc = _g(meta, "totalCash")
    td = _g(meta, "totalDebt")
    ncr = ((tc - td) / mc) if all(np.isfinite([tc, td, mc])) and mc > 0 else np.nan

    f = Fundamentals(
        market_cap=mc,
        trailing_pe=_g(meta, "trailingPE"),
        forward_pe=_g(meta, "forwardPE"),
        price_to_book=_g(meta, "priceToBook"),
        ev_to_ebitda=(ev / ebitda) if np.isfinite(ev) and np.isfinite(ebitda)
        and ebitda > 0 else _g(meta, "enterpriseToEbitda"),
        ev_to_sales=(ev / rev) if np.isfinite(ev) and np.isfinite(rev)
        and rev > 0 else _g(meta, "enterpriseToRevenue"),
        profit_margin=_g(meta, "profitMargins"),
        operating_margin=om,
        roe=_g(meta, "returnOnEquity"),
        roa=_g(meta, "returnOnAssets"),
        debt_to_equity=_g(meta, "debtToEquity"),
        current_ratio=_g(meta, "currentRatio"),
        revenue_growth=rg,
        earnings_growth=_g(meta, "earningsGrowth", "earningsQuarterlyGrowth"),
        fcf=fcf,
        fcf_yield=(fcf / mc) if np.isfinite(fcf) and np.isfinite(mc) and mc > 0
        else np.nan,
        dividend_yield=dy,
        payout_ratio=_g(meta, "payoutRatio"),
        beta_reported=_g(meta, "beta"),
        short_pct_float=_g(meta, "shortPercentOfFloat"),
        inst_ownership=_g(meta, "heldPercentInstitutions"),
        rule_of_40=ro40,
        net_cash_ratio=ncr,
        available=0,
        pit_warning=("yfinance 펀더멘털은 리스테이트먼트가 반영된 값으로 "
                     "point-in-time이 아니다. 현재 상태 진단용으로만 사용하고 "
                     "시계열 백테스트에는 쓰지 말 것."),
    )
    f.available = sum(1 for k, v in asdict(f).items()
                      if isinstance(v, float) and np.isfinite(v))
    return f


# ================================================================ 어닝 이벤트

def earnings_event_study(ticker: str, prices: pd.Series,
                         bench: Optional[pd.Series] = None,
                         earnings_df: Optional[pd.DataFrame] = None,
                         ann: int = 252) -> Optional[EarningsStudy]:
    """
    어닝 발표 전후 이벤트 스터디.
     - 발표 다음날 초과수익 분포 (갭 리스크)
     - PEAD: 서프라이즈 부호별 t+1~t+20 누적초과수익
     - 연 분산 중 어닝일 기여도
    """
    if earnings_df is None:
        try:
            import yfinance as yf
            earnings_df = yf.Ticker(ticker).get_earnings_dates(limit=40)
        except Exception:
            return None
    if earnings_df is None or len(earnings_df) == 0:
        return None

    ed = earnings_df.copy()
    ed.index = pd.DatetimeIndex(ed.index).tz_localize(None)
    ed = ed.sort_index()
    surp_col = next((c for c in ed.columns
                     if "surprise" in str(c).lower() and "%" in str(c).lower()),
                    None)
    if surp_col is None:
        surp_col = next((c for c in ed.columns if "surprise" in str(c).lower()), None)

    px = prices.astype(float)
    r = np.log(px).diff()
    if bench is not None:
        rb = np.log(bench.reindex(px.index).ffill().astype(float)).diff()
        m = np.isfinite(r) & np.isfinite(rb)
        beta = (float(np.cov(rb[m], r[m], ddof=1)[0, 1] / np.var(rb[m], ddof=1))
                if m.sum() > 100 else 1.0)
        ar = r - beta * rb
    else:
        ar = r - float(np.nanmean(r))

    today = pd.Timestamp.today().normalize()
    future = ed.index[ed.index > today]
    next_dt = str(future[0].date()) if len(future) else None
    days_next = int((future[0] - today).days) if len(future) else None

    past = ed.index[ed.index <= today]
    moves, pead_pos, pead_neg, surps = [], [], [], []
    idx = px.index
    for d in past:
        loc = idx.searchsorted(d)
        if loc >= len(idx) - 21 or loc < 1:
            continue
        # 발표 직후 첫 거래일 반응
        move = float(ar.iloc[min(loc + 1, len(ar) - 1)])
        if not np.isfinite(move):
            continue
        moves.append(move)
        cum20 = float(np.nansum(ar.iloc[loc + 2: loc + 22]))
        s = np.nan
        if surp_col is not None:
            try:
                s = float(ed.loc[d, surp_col])
            except Exception:
                s = np.nan
        if not np.isfinite(s):
            s = move                     # 서프라이즈 미상 → 반응 부호로 대체
        surps.append(s)
        (pead_pos if s > 0 else pead_neg).append(cum20)

    if len(moves) < 4:
        return None
    mv = np.array(moves)
    total_var = float(np.nanvar(r, ddof=1)) * len(r)
    gap_var = float(np.nansum(mv ** 2))
    pp = float(np.mean(pead_pos)) if pead_pos else np.nan
    pn = float(np.mean(pead_neg)) if pead_neg else np.nan
    spread = pp - pn if np.isfinite(pp) and np.isfinite(pn) else np.nan
    if pead_pos and pead_neg and len(pead_pos) > 2 and len(pead_neg) > 2:
        sp = math.sqrt(np.var(pead_pos, ddof=1) / len(pead_pos)
                       + np.var(pead_neg, ddof=1) / len(pead_neg))
        tst = spread / sp if sp > 0 else np.nan
    else:
        tst = np.nan

    note = []
    if np.isfinite(days_next or np.nan) and (days_next or 999) <= 14:
        note.append(f"⚠ {days_next}일 뒤 어닝 — 이벤트 리스크 구간. "
                    f"과거 발표 다음날 |수익률| 중앙값 {np.median(np.abs(mv)):.1%}, "
                    f"90분위 {np.quantile(np.abs(mv), 0.9):.1%}")
    if gap_var / max(total_var, 1e-12) > 0.15:
        note.append(f"연 분산의 {gap_var/total_var:.0%}가 어닝 4일에 집중 — "
                    f"평상시 변동성으로 리스크를 재면 심각히 과소평가")
    if np.isfinite(tst) and abs(tst) > 1.8:
        note.append(f"PEAD 스프레드 {spread:+.1%} (t={tst:+.1f}) — "
                    f"발표 후 드리프트 존재 가능")

    return EarningsStudy(
        n_events=len(mv), next_date=next_dt, days_to_next=days_next,
        mean_abs_move=float(np.mean(np.abs(mv))),
        median_abs_move=float(np.median(np.abs(mv))),
        p90_abs_move=float(np.quantile(np.abs(mv), 0.9)),
        max_abs_move=float(np.max(np.abs(mv))),
        mean_move=float(np.mean(mv)),
        beat_rate=float(np.mean(np.array(surps) > 0)),
        pead_20d_pos=pp, pead_20d_neg=pn, pead_spread=spread, pead_tstat=tst,
        gap_share_of_var=float(gap_var / max(total_var, 1e-12)),
        note=" · ".join(note) or "특이사항 없음")


# ================================================================ 스타일

def style_tilt(r_asset: np.ndarray, F: pd.DataFrame, ann: int = 252
               ) -> Optional[StyleTilt]:
    """스타일 로딩 + 고유변동성 + 잔차 모멘텀(Blitz-Huij-Martens)."""
    cols = [c for c in ("mkt_excess", "smb", "hml", "rmw", "cma", "umd")
            if c in F.columns]
    if len(cols) < 2:
        return None
    y = np.asarray(r_asset, float)
    X = F[cols].values.astype(float)
    m = np.isfinite(y) & np.isfinite(X).all(axis=1)
    if m.sum() < 250:
        return None
    b, se = newey_west_se(X[m], y[m], lags=5)
    yhat = np.column_stack([np.ones(m.sum()), X[m]]) @ b
    resid = y[m] - yhat
    ss = float(np.sum((y[m] - y[m].mean()) ** 2))
    r2 = 1 - float(np.sum(resid ** 2)) / ss if ss > 0 else np.nan
    load = {c: float(b[i + 1]) for i, c in enumerate(cols)}
    tst = {c: float(b[i + 1] / se[i + 1]) if se[i + 1] > 0 else np.nan
           for i, c in enumerate(cols)}

    style_only = {k: v for k, v in load.items() if k != "mkt_excess"}
    dom = max(style_only, key=lambda k: abs(style_only[k])) if style_only else "mkt_excess"
    name_map = {"smb": "소형", "hml": "가치", "rmw": "수익성", "cma": "보수적투자",
                "umd": "모멘텀", "mkt_excess": "시장"}
    dom_label = (f"{name_map.get(dom, dom)} "
                 f"{'(+)' if style_only.get(dom, 0) > 0 else '(−)'}")

    idio = float(np.std(resid, ddof=1) * math.sqrt(ann))
    tot = float(np.std(y[m], ddof=1) * math.sqrt(ann))
    idio_share = float(idio ** 2 / tot ** 2) if tot > 0 else np.nan

    # 12-1 모멘텀 (최근 1개월 제외)
    def mom_12_1(x):
        if len(x) < 273:
            return np.nan
        return float(np.nansum(x[-273:-21]))
    res_full = np.full(len(y), np.nan)
    res_full[m] = resid
    return StyleTilt(load, tst, r2, dom_label, idio, idio_share,
                     mom_12_1(res_full), mom_12_1(y))


# ================================================================ 점프

def jump_profile(r: np.ndarray, sigma_t: np.ndarray, ann: int = 252,
                 k: float = 4.0) -> JumpProfile:
    """
    Lee-Mykland 계열 점프 탐지(간이): |r_t| > k·σ_t 를 점프로 본다.
    이벤트 드리븐 종목은 점프가 분산을 지배하며, 정규분포 기반 통계가 붕괴한다.
    """
    r = np.asarray(r, float)
    s = np.asarray(sigma_t, float)
    n = min(len(r), len(s))
    r, s = r[-n:], s[-n:]
    m = np.isfinite(r) & np.isfinite(s) & (s > 0)
    if m.sum() < 100:
        return JumpProfile(0, np.nan, np.nan, np.nan, np.nan, np.nan, np.nan,
                           "표본 부족")
    z = np.zeros(n, dtype=bool)
    z[m] = np.abs(r[m]) > k * s[m]
    nj = int(z.sum())
    tot_var = float(np.nansum(r[m] ** 2))
    jump_var = float(np.nansum(r[z] ** 2)) if nj else 0.0
    cont = r.copy()
    cont[z] = np.nan
    cvol = float(np.nanstd(cont, ddof=1) * math.sqrt(ann))
    ups = r[z][r[z] > 0]
    dns = r[z][r[z] < 0]
    asym = (len(dns) - len(ups)) / max(nj, 1)
    share = jump_var / max(tot_var, 1e-12)
    note = ""
    if share > 0.25:
        note = (f"점프가 총 분산의 {share:.0%}를 차지 — 이벤트 드리븐 성격. "
                f"평균·표준편차 기반 통계(샤프, 정규 VaR)는 대부분 무의미하다.")
    elif share > 0.12:
        note = f"점프 기여 {share:.0%} — 꼬리 모델링 필수."
    else:
        note = f"점프 기여 {share:.0%} — 연속 확산이 지배."
    return JumpProfile(nj, float(nj / (m.sum() / ann)), share,
                       float(np.max(ups)) if len(ups) else np.nan,
                       float(np.min(dns)) if len(dns) else np.nan,
                       float(asym), cvol, note)


# ================================================================ 피어

def peer_relative(r_asset: np.ndarray, bench: pd.Series, index: pd.DatetimeIndex,
                  bench_name: str = "SPY", ann: int = 252) -> Optional[PeerRelative]:
    rb = np.log(bench.reindex(index).ffill().astype(float)).diff().values
    y = np.asarray(r_asset, float)
    m = np.isfinite(y) & np.isfinite(rb)
    if m.sum() < 250:
        return None
    vb = float(np.var(rb[m], ddof=1))
    beta = float(np.cov(rb[m], y[m], ddof=1)[0, 1] / vb) if vb > 0 else np.nan
    corr = float(np.corrcoef(rb[m], y[m])[0, 1])
    act = y[m] - rb[m]
    te = float(np.std(act, ddof=1) * math.sqrt(ann))
    ir = float(np.mean(act) * ann / te) if te > 0 else np.nan
    n = len(y)
    r1y = float(np.nansum(y[-252:]) - np.nansum(rb[-252:])) if n > 252 else np.nan
    r3m = float(np.nansum(y[-63:]) - np.nansum(rb[-63:])) if n > 63 else np.nan
    # 롤링 63일 상대강도의 백분위
    rs = pd.Series(y - rb).rolling(63).sum()
    pct = float((rs <= rs.iloc[-1]).mean()) if rs.notna().sum() > 100 else np.nan
    return PeerRelative(bench_name, r1y, r3m, beta, corr, te, ir, pct)


# ================================================================ 아키타입 판정

def classify_archetype(f: Optional[Fundamentals], st: Optional[StyleTilt],
                       jp: Optional[JumpProfile], sector: str,
                       ann_vol: float) -> Tuple[str, float, List[str]]:
    ev: List[str] = []
    scores: Dict[str, float] = {k: 0.0 for k in ARCHETYPES}

    if jp is not None and np.isfinite(jp.jump_share_of_var):
        if jp.jump_share_of_var > 0.30:
            scores["EVENT_DRIVEN"] += 3.0
            ev.append(f"점프가 분산의 {jp.jump_share_of_var:.0%} — 이벤트 지배")
        elif jp.jump_share_of_var > 0.18:
            scores["EVENT_DRIVEN"] += 1.2
            scores["HIGH_BETA_SPECULATIVE"] += 0.6

    if st is not None:
        if np.isfinite(st.idio_share) and st.idio_share > 0.75:
            scores["HIGH_BETA_SPECULATIVE"] += 1.2
            scores["EVENT_DRIVEN"] += 0.8
            ev.append(f"고유변동성 비중 {st.idio_share:.0%} — 팩터로 설명 안 됨")
        b = st.loadings.get("mkt_excess", np.nan)
        if np.isfinite(b):
            if b > 1.35:
                scores["HIGH_BETA_SPECULATIVE"] += 1.0
                scores["CYCLICAL"] += 0.5
                ev.append(f"시장 β={b:.2f} — 고베타")
            elif b < 0.75:
                scores["DEFENSIVE"] += 1.2
                ev.append(f"시장 β={b:.2f} — 저베타")
        h = st.loadings.get("hml", np.nan)
        if np.isfinite(h):
            if h > 0.3:
                scores["DEEP_VALUE"] += 0.8
            elif h < -0.3:
                scores["HYPERGROWTH_UNPROFITABLE"] += 0.6
                scores["QUALITY_COMPOUNDER"] += 0.4

    if np.isfinite(ann_vol):
        if ann_vol > 0.60:
            scores["HIGH_BETA_SPECULATIVE"] += 1.0
            scores["EVENT_DRIVEN"] += 0.5
            ev.append(f"연변동성 {ann_vol:.0%} — 투기적 구간")
        elif ann_vol < 0.18:
            scores["DEFENSIVE"] += 0.8

    sec = (sector or "").lower()
    if "biotech" in sec or "healthcare" in sec:
        scores["EVENT_DRIVEN"] += 0.8
    if any(k in sec for k in ("energy", "materials", "industrial",
                              "consumer cyclical")):
        scores["CYCLICAL"] += 1.2
        ev.append(f"섹터 '{sector}' — 경기민감")
    if any(k in sec for k in ("utilities", "consumer defensive", "staples")):
        scores["DEFENSIVE"] += 1.5
        ev.append(f"섹터 '{sector}' — 방어적")
    if "real estate" in sec:
        scores["DIVIDEND_INCOME"] += 1.0

    if f is not None and f.available >= 5:
        pm, roe, rg = f.profit_margin, f.roe, f.revenue_growth
        de, dy, pe = f.debt_to_equity, f.dividend_yield, f.trailing_pe
        pb, ncr = f.price_to_book, f.net_cash_ratio

        if np.isfinite(pm) and pm < 0:
            scores["HYPERGROWTH_UNPROFITABLE"] += 1.5
            ev.append(f"순이익률 {pm:.1%} — 적자")
            if np.isfinite(rg) and rg > 0.20:
                scores["HYPERGROWTH_UNPROFITABLE"] += 1.5
                ev.append(f"매출성장 {rg:.0%} — 고성장 적자")
            else:
                scores["DISTRESSED"] += 1.0
        if np.isfinite(roe) and np.isfinite(pm):
            if roe > 0.18 and pm > 0.12:
                scores["QUALITY_COMPOUNDER"] += 2.0
                ev.append(f"ROE {roe:.0%}, 순이익률 {pm:.0%} — 고수익성")
        if np.isfinite(de):
            if de > 200:
                scores["DISTRESSED"] += 1.2
                ev.append(f"부채비율 {de:.0f}% — 레버리지 높음")
            elif de < 40:
                scores["QUALITY_COMPOUNDER"] += 0.5
        if np.isfinite(ncr) and ncr > 0.10:
            scores["QUALITY_COMPOUNDER"] += 0.5
            scores["DISTRESSED"] -= 1.0
            ev.append(f"순현금이 시총의 {ncr:.0%}")
        if np.isfinite(dy) and dy > 0.030:
            scores["DIVIDEND_INCOME"] += 1.8
            if dy > 0.040:
                # 배당수익률 4% 초과는 섹터 방어성보다 강한 판별자다.
                # 커버리지·payout·금리 민감도가 분석 렌즈 전체를 바꾼다.
                scores["DIVIDEND_INCOME"] += 2.0
                scores["DEFENSIVE"] -= 0.5
                ev.append("배당수익률 4% 초과 — 인컴 렌즈가 섹터 특성보다 지배적")
            ev.append(f"배당수익률 {dy:.1%}")
            if np.isfinite(f.payout_ratio) and f.payout_ratio > 0.85:
                scores["DISTRESSED"] += 0.6
                ev.append(f"payout {f.payout_ratio:.0%} — 커버리지 취약")
        if np.isfinite(pb) and np.isfinite(pe):
            if pb < 1.2 and 0 < pe < 12:
                scores["DEEP_VALUE"] += 2.0
                ev.append(f"P/B {pb:.2f}, PER {pe:.1f} — 딥밸류 영역")
        if np.isfinite(f.rule_of_40) and f.rule_of_40 > 40 and \
                np.isfinite(pm) and pm < 0.05:
            scores["HYPERGROWTH_UNPROFITABLE"] += 1.0
            ev.append(f"Rule of 40 = {f.rule_of_40:.0f}")
        if np.isfinite(f.short_pct_float) and f.short_pct_float > 0.15:
            scores["HIGH_BETA_SPECULATIVE"] += 1.0
            ev.append(f"공매도잔고 {f.short_pct_float:.0%} of float — 스퀴즈 리스크")

    best = max(scores, key=scores.get)
    top = scores[best]
    # 펀더멘털이 없으면 가격만으로 아키타입을 단정하지 않는다
    thr = 1.2 if (f is not None and f.available >= 5) else 2.2
    if top < thr:
        ev.append(f"최고 점수 {top:.1f} < 임계 {thr:.1f}"
                  + (" (펀더멘털 부족으로 임계 상향)"
                     if not (f is not None and f.available >= 5) else ""))
        return "UNCLASSIFIED", 0.3, ev
    srt = sorted(scores.values(), reverse=True)
    margin = top - (srt[1] if len(srt) > 1 else 0.0)
    conf = float(min(0.95, 0.45 + 0.12 * top + 0.10 * margin))
    return best, conf, ev


# ================================================================ 통합

def profile_equity(ticker: str, df: pd.DataFrame, meta: Dict,
                   r: np.ndarray, F: pd.DataFrame, sigma_t: np.ndarray,
                   proxies: Dict[str, pd.Series], ann_vol: float,
                   ann: int = 252,
                   earnings_df: Optional[pd.DataFrame] = None) -> EquityProfile:
    warns: List[str] = []
    idx = pd.DatetimeIndex(df.index)

    fund = extract_fundamentals(meta)
    if fund.available < 5:
        warns.append(f"펀더멘털 필드 {fund.available}개만 확보 — "
                     f"아키타입 판정 신뢰도 저하")

    st = style_tilt(r, F, ann)
    jp = jump_profile(r, sigma_t, ann)

    bench = proxies.get("mkt_excess")
    pr = peer_relative(r, bench, idx) if bench is not None else None

    try:
        es = earnings_event_study(ticker, df["Close"], bench, earnings_df, ann)
    except Exception as e:
        es = None
        warns.append(f"어닝 이벤트 스터디 실패: {e}")

    arch, conf, ev = classify_archetype(fund, st, jp, str(meta.get("sector", "")),
                                        ann_vol)
    spec = ARCHETYPES[arch]
    sections = list(spec["sections"])
    # 데이터가 있는 섹션만 활성화
    if es is None and "earnings_event" in sections:
        sections.remove("earnings_event")
    if fund.available < 4 and "fundamentals" in sections:
        sections.remove("fundamentals")
    if pr is None and "peer" in sections:
        sections.remove("peer")
    # 조건부 추가
    if jp is not None and np.isfinite(jp.jump_share_of_var) \
            and jp.jump_share_of_var > 0.15 and "jump" not in sections:
        sections.append("jump")
    if es is not None and es.days_to_next is not None and es.days_to_next <= 21:
        if "earnings_event" not in sections:
            sections.append("earnings_event")

    return EquityProfile(
        ticker=ticker, archetype=arch, archetype_ko=spec["ko"],
        archetype_desc=spec["desc"], archetype_confidence=conf,
        archetype_evidence=ev, valuation_anchors=spec["anchors"],
        watch_items=spec["watch"], active_sections=sections,
        fundamentals=fund, earnings=es, style=st, jumps=jp, peer=pr,
        warnings=warns)
