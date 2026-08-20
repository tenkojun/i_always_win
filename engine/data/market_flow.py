# -*- coding: utf-8 -*-
"""
시장 수급 스캐너
================
"지금 이 세션에서 돈이 어디로 몰리는가" 를 당일 기준으로 계속 갱신한다.

먼저 못 하는 것부터
-------------------
**미국 주식에는 기관/외국인/개인 구분이 없다.** 공개된 실시간 주체별
매매동향이 존재하지 않는다. 13F 는 분기별에 45일 지연이고, 세션 단위
주체 분해는 유료 틱 데이터 영역이다. 그래서 미국은 **주체**가 아니라
**행동**을 본다 — 얼마나 비정상적으로 몰리는가(RVOL·거래대금),
그리고 그 거래가 매수 쪽인가 매도 쪽인가(순매수 프록시).

한국 종목은 KRX 가 투자자별 매매동향을 무료로 공개하므로 그쪽은
**진짜 주체별 수급**을 쓴다. 이 분기는 화면에도 표시한다.

거래량은 통합(consolidated)인가
-------------------------------
그렇다. 흔히 "무료 데이터는 IEX 라 전체 거래소가 아니다" 라고 하는데,
그건 **실시간 스트림** 이야기다.

- 야후(무키): 일봉·분봉 모두 통합 거래량. 실측 AAPL 일 4,000만 주대 —
  IEX 단독이면 100만 주대여야 한다.
- Alpaca 무료 Basic: `end` 가 15분 이전이면 SIP(전 거래소) 바를 준다.
  15분 이내만 막힌다(42210000).

수급 대시보드에 15분 지연은 문제가 되지 않으므로 둘 다 통합 데이터로
쓴다. 키가 없으면 야후만으로 완전히 동작한다.

RVOL — 반드시 같은 시각끼리 비교한다
------------------------------------
오전 10시의 누적거래량을 과거 **하루 전체** 평균과 비교하면 언제나
"거래량 부족" 이 나온다. 장 초반이니 당연하다. 그래서 과거 N일의
**같은 시각까지의 누적거래량** 과 비교한다.

    RVOL = 오늘 09:30~현재 누적거래량
           ────────────────────────────────
           과거 N일 09:30~같은시각 누적거래량의 중앙값

평균이 아니라 중앙값을 쓴다. 실적발표일 하루가 평균을 통째로 끌어올려
그 뒤 한 달간 모든 종목이 "조용함" 으로 보이는 일을 막는다.

방향 — 거래량만으로는 알 수 없다
--------------------------------
RVOL 과 거래대금은 "얼마나 활발한가" 이지 "사는가 파는가" 가 아니다.
봉 데이터로 방향을 추정하는 표준 방법 두 가지를 함께 쓴다.

1) 업/다운 볼륨 — 봉 수익률 부호로 거래량을 매수/매도로 가른다.
2) CMF(Chaikin Money Flow) — ((종가-저가)-(고가-종가))/(고가-저가).
   봉 안에서 종가가 위쪽에 찍혔으면 매수 우위로 본다.

둘 다 **프록시**다. 진짜 체결 주체를 아는 게 아니라 봉 모양에서
추정하는 것이다. 서로 어긋나면 그 사실을 그대로 표시한다.
"""
from __future__ import annotations

import datetime as dt
import math
import threading
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

# ── 내장 유니버스 — 키가 없어도 즉시 도는 폴백 ──────────────────
# 미국 대형주 + 거래대금 상위 ETF. Alpaca 키가 있으면 전체 목록으로
# 대체된다. "무키로도 완전히 동작" 규칙을 지키기 위한 기본값이다.
US_MEGACAP = [
    "AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "TSLA", "AVGO",
    "BRK-B", "LLY", "JPM", "V", "XOM", "UNH", "MA", "COST", "HD",
    "PG", "JNJ", "WMT", "NFLX", "BAC", "CRM", "ORCL", "AMD", "CVX",
    "KO", "PEP", "ADBE", "TMO", "LIN", "MRK", "ABBV", "ACN", "CSCO",
    "MCD", "INTC", "QCOM", "TXN", "DIS", "INTU", "IBM", "GE", "CAT",
    "VZ", "NOW", "AMGN", "PFE", "UBER", "BKNG", "MU", "PLTR", "COIN",
    "SMCI", "ARM", "PANW", "SNOW", "SHOP", "MSTR", "RIVN", "LCID",
]

US_SECTOR_ETF = {
    "XLK": "기술", "XLF": "금융", "XLE": "에너지", "XLV": "헬스케어",
    "XLI": "산업재", "XLY": "경기소비재", "XLP": "필수소비재",
    "XLU": "유틸리티", "XLB": "소재", "XLRE": "부동산",
    "XLC": "커뮤니케이션",
}

US_BROAD_ETF = ["SPY", "QQQ", "IWM", "DIA", "VXX", "TLT", "GLD", "SLV",
                "USO", "HYG", "EEM", "FXI", "ARKK", "SOXL", "TQQQ"]

DEFAULT_US_UNIVERSE = (US_MEGACAP + list(US_SECTOR_ETF) + US_BROAD_ETF)

# 종목 → 섹터. 야후 메타에서 못 얻었을 때 쓰는 최소 매핑.
_FALLBACK_SECTOR = {
    "AAPL": "기술", "MSFT": "기술", "NVDA": "기술", "AVGO": "기술",
    "AMD": "기술", "MU": "기술", "QCOM": "기술", "TXN": "기술",
    "INTC": "기술", "CSCO": "기술", "ORCL": "기술", "CRM": "기술",
    "ADBE": "기술", "NOW": "기술", "IBM": "기술", "ACN": "기술",
    "INTU": "기술", "PANW": "기술", "SNOW": "기술", "SMCI": "기술",
    "ARM": "기술", "PLTR": "기술",
    "GOOGL": "커뮤니케이션", "META": "커뮤니케이션",
    "NFLX": "커뮤니케이션", "DIS": "커뮤니케이션", "VZ": "커뮤니케이션",
    "AMZN": "경기소비재", "TSLA": "경기소비재", "HD": "경기소비재",
    "MCD": "경기소비재", "BKNG": "경기소비재", "UBER": "경기소비재",
    "SHOP": "경기소비재", "RIVN": "경기소비재", "LCID": "경기소비재",
    "JPM": "금융", "BAC": "금융", "V": "금융", "MA": "금융",
    "BRK-B": "금융", "COIN": "금융", "MSTR": "금융",
    "XOM": "에너지", "CVX": "에너지",
    "LLY": "헬스케어", "UNH": "헬스케어", "JNJ": "헬스케어",
    "MRK": "헬스케어", "ABBV": "헬스케어", "TMO": "헬스케어",
    "AMGN": "헬스케어", "PFE": "헬스케어",
    "PG": "필수소비재", "KO": "필수소비재", "PEP": "필수소비재",
    "COST": "필수소비재", "WMT": "필수소비재",
    "GE": "산업재", "CAT": "산업재",
    "LIN": "소재",
}


# ══════════════════════════════════════════════════════════════
#  자료구조
# ══════════════════════════════════════════════════════════════

@dataclass
class FlowRow:
    """한 종목의 당일 수급 상태."""
    ticker: str
    name: str = ""
    sector: str = "기타"

    price: float = float("nan")
    chg_pct: float = float("nan")        # 전일 종가 대비 %

    cum_volume: float = float("nan")     # 오늘 누적 거래량(주)
    base_volume: float = float("nan")    # 과거 같은 시각 누적 중앙값
    rvol: float = float("nan")           # 상대거래량 배수
    dollar_vol: float = float("nan")     # 오늘 누적 거래대금(달러)

    # 방향 프록시 — 진짜 주체가 아니라 봉 모양에서의 추정
    up_vol_share: float = float("nan")   # 상승봉 거래량 비중 0~1
    cmf: float = float("nan")            # -1 ~ +1
    net_flow: float = float("nan")       # 순매수 프록시(달러)

    # 어제 **같은 시각까지** 의 값. 하루 전체와 비교하면 장중엔 언제나
    # "어제보다 줄었다" 가 나온다 — RVOL 과 같은 함정이다.
    dollar_vol_prev: float = float("nan")
    net_flow_prev: float = float("nan")
    net_flow_delta: float = float("nan")  # 오늘 - 어제 (순매수 프록시)

    score: float = float("nan")          # 종합 이상활동 점수
    direction: str = "중립"              # 매수우위 / 매도우위 / 중립
    agree: bool = True                   # 두 방향 프록시가 일치하는가
    note: str = ""

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        # NaN 은 JSON 에서 깨진다 — None 으로 낮춘다
        return {k: (None if isinstance(v, float) and not math.isfinite(v) else v)
                for k, v in d.items()}


@dataclass
class SectorFlow:
    sector: str
    n: int = 0
    dollar_vol: float = 0.0
    net_flow: float = 0.0
    avg_rvol: float = float("nan")
    avg_chg: float = float("nan")
    share: float = float("nan")          # 전체 거래대금 중 비중

    # 어제 같은 시각 대비 — "어제와 달리 어디로 몰리는가" 가 여기서 나온다
    share_prev: float = float("nan")
    share_delta: float = float("nan")    # %p. 양수면 자금이 이 섹터로 이동
    net_flow_prev: float = float("nan")
    net_flow_delta: float = float("nan")
    strength: str = "중립"               # 강세 / 약세 / 중립

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        return {k: (None if isinstance(v, float) and not math.isfinite(v) else v)
                for k, v in d.items()}


@dataclass
class FlowBoard:
    market: str = "US"                   # US | KR
    kind: str = "proxy"                  # proxy | investor  (데이터 성격)
    asof: str = ""
    session: str = ""                    # 장중 / 장마감 / 장전
    progress: float = float("nan")       # 세션 경과 0~1
    rows: List[FlowRow] = field(default_factory=list)
    top_buy: List[FlowRow] = field(default_factory=list)    # 집중 매수
    top_sell: List[FlowRow] = field(default_factory=list)   # 집중 매도
    rotation: List[SectorFlow] = field(default_factory=list)  # 어제 대비 이동
    sectors: List[SectorFlow] = field(default_factory=list)
    breadth: Dict[str, Any] = field(default_factory=dict)
    headline: str = ""                   # 한 줄 요약
    source: str = ""
    caveat: str = ""
    error: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "market": self.market, "kind": self.kind, "asof": self.asof,
            "session": self.session,
            "progress": (None if not math.isfinite(self.progress)
                         else round(self.progress, 4)),
            "rows": [r.to_dict() for r in self.rows],
            "top_buy": [r.to_dict() for r in self.top_buy],
            "top_sell": [r.to_dict() for r in self.top_sell],
            "rotation": [s.to_dict() for s in self.rotation],
            "sectors": [s.to_dict() for s in self.sectors],
            "breadth": self.breadth, "source": self.source,
            "headline": self.headline,
            "caveat": self.caveat, "error": self.error,
        }


# ══════════════════════════════════════════════════════════════
#  계산 코어 — 순수 함수. 데이터 출처와 분리해 테스트 가능하게 둔다.
# ══════════════════════════════════════════════════════════════

def _session_progress(idx: pd.DatetimeIndex) -> Tuple[str, float]:
    """마지막 봉 시각으로 세션 상태와 경과율을 추정한다."""
    if len(idx) == 0:
        return "알수없음", float("nan")
    last = idx[-1]
    mins = last.hour * 60 + last.minute
    open_m, close_m = 9 * 60 + 30, 16 * 60          # 미국 정규장
    if mins < open_m:
        return "장전", 0.0
    if mins >= close_m:
        return "장마감", 1.0
    return "장중", (mins - open_m) / (close_m - open_m)


def _cum_by_day(bars: pd.DataFrame) -> pd.DataFrame:
    """
    분봉을 날짜×시각 누적거래량 표로 바꾼다.

    반환: index=거래일, columns=장중 분(minute-of-day), 값=그 시각까지 누적.
    """
    b = bars.dropna(subset=["volume"])
    if b.empty:
        return pd.DataFrame()
    day = b.index.date
    mod = b.index.hour * 60 + b.index.minute
    tmp = pd.DataFrame({"day": day, "mod": mod,
                        "vol": b["volume"].astype(float).values})
    # 같은 (날짜,분) 이 여러 개면 합친다
    piv = tmp.groupby(["day", "mod"])["vol"].sum().unstack(fill_value=0.0)
    return piv.sort_index(axis=1).cumsum(axis=1)


def compute_rvol(bars: pd.DataFrame, lookback: int = 20
                 ) -> Tuple[float, float, float]:
    """
    (오늘 누적거래량, 과거 같은 시각 누적 중앙값, RVOL) 를 낸다.

    과거 하루 **전체** 와 비교하면 장 초반엔 언제나 거래량 부족이 나온다.
    같은 시각끼리 비교해야 의미가 생긴다. 중앙값을 쓰는 이유는 실적발표
    같은 하루가 평균을 끌어올려 이후 한 달을 전부 '조용함' 으로 만들기
    때문이다.
    """
    cum = _cum_by_day(bars)
    if cum.empty or len(cum) < 2:
        return float("nan"), float("nan"), float("nan")

    # '지금' 은 **원본 마지막 봉** 에서 읽어야 한다.
    # 피벗은 모든 날짜의 시각을 합집합으로 갖고 fill_value=0 뒤 cumsum 하므로,
    # 오늘 행에도 장 마감 시각 컬럼까지 값이 채워져 있다(마지막 값이 그대로
    # 옆으로 흐른다). 거기서 시각을 읽으면 장 초반인데도 '종일' 로 잡혀
    # 기준값이 하루 전체가 되고, 결국 이 함수가 막으려던 바로 그 비교를
    # 하게 된다. 실제로 3배 몰린 종목이 0.41배로 나왔었다.
    last_ts = bars.dropna(subset=["volume"]).index[-1]
    today = last_ts.date()
    now_mod = int(last_ts.hour * 60 + last_ts.minute)
    if today not in cum.index:
        return float("nan"), float("nan"), float("nan")
    tcols = [c for c in cum.columns if c <= now_mod]
    if not tcols:
        return float("nan"), float("nan"), float("nan")
    today_cum = float(cum.loc[today, tcols].iloc[-1])

    hist = cum.iloc[:-1]
    if len(hist) > lookback:
        hist = hist.iloc[-lookback:]
    # 과거 각 날짜에서 '지금과 같은 시각까지' 의 누적
    cols = [c for c in hist.columns if c <= now_mod]
    if not cols or hist.empty:
        return today_cum, float("nan"), float("nan")
    base_series = hist[cols].ffill(axis=1).iloc[:, -1].dropna()
    base_series = base_series[base_series > 0]
    if base_series.empty:
        return today_cum, float("nan"), float("nan")

    base = float(np.median(base_series.values))
    rvol = today_cum / base if base > 0 else float("nan")
    return today_cum, base, rvol


def compute_direction(bars_today: pd.DataFrame) -> Tuple[float, float, float]:
    """
    (상승봉 거래량 비중, CMF, 순매수 프록시 달러) 를 낸다.

    둘 다 봉 모양에서의 추정이다. 실제 체결 주체를 아는 게 아니다.
    """
    b = bars_today.dropna(subset=["close", "volume"])
    if len(b) < 2:
        return float("nan"), float("nan"), float("nan")

    v = b["volume"].astype(float).values
    c = b["close"].astype(float).values
    h = b["high"].astype(float).values if "high" in b else c
    lo = b["low"].astype(float).values if "low" in b else c

    tot = float(np.nansum(v))
    if tot <= 0:
        return float("nan"), float("nan"), float("nan")

    # 1) 업/다운 볼륨 — 봉 수익률 부호로 가른다
    d = np.diff(c, prepend=c[0])
    up = float(np.nansum(v[d > 0]))
    up_share = up / tot

    # 2) CMF — 봉 안에서 종가 위치. 고가=저가면 정보가 없으므로 0.
    rng = h - lo
    with np.errstate(divide="ignore", invalid="ignore"):
        mfm = np.where(rng > 0, ((c - lo) - (h - c)) / rng, 0.0)
    mfv = mfm * v
    cmf = float(np.nansum(mfv) / tot)

    # 순매수 프록시(달러) — CMF 가중 거래대금
    net = float(np.nansum(mfv * c))
    return up_share, cmf, net


def prev_session_slice(bars: "pd.DataFrame", now_mod: int
                       ) -> Optional["pd.DataFrame"]:
    """
    직전 거래일을 **오늘과 같은 시각까지만** 잘라 돌려준다.

    오늘 오전 10시의 자금 유입을 어제 **하루 전체** 와 비교하면 언제나
    "어제보다 줄었다" 가 나온다. RVOL 에서 이미 겪은 함정과 같은 종류라
    여기서도 같은 시각으로 자른다.
    """
    b = bars.dropna(subset=["volume"])
    if b.empty:
        return None
    days = sorted(set(b.index.date))
    if len(days) < 2:
        return None
    prev_day = days[-2]
    prev = b[b.index.date == prev_day]
    if prev.empty:
        return None
    pm = prev.index.hour * 60 + prev.index.minute
    sliced = prev[pm <= now_mod]
    return sliced if len(sliced) >= 2 else None


def _score(rvol: float, dollar_vol: float, chg_pct: float,
           dv_rank: float) -> float:
    """
    이상활동 점수. 세 축을 곱이 아니라 **가중합** 으로 묶는다.

    곱으로 묶으면 한 축이 0 에 가까울 때 나머지가 무의미해진다.
    거래대금은 절대값 스케일이 종목마다 100배씩 차이 나므로 원값이
    아니라 **순위(0~1)** 로 넣는다. 안 그러면 상위권이 항상 SPY·QQQ 로
    고정된다.
    """
    if not (np.isfinite(rvol) and rvol > 0):
        return float("nan")
    # RVOL 은 로그로 눌러 5배와 50배가 같은 급으로 뭉치지 않게 한다
    s_rvol = math.log(max(rvol, 0.05)) / math.log(10.0)      # 1배=0, 10배=1
    s_dv = dv_rank if np.isfinite(dv_rank) else 0.0
    s_chg = min(abs(chg_pct) / 5.0, 1.5) if np.isfinite(chg_pct) else 0.0
    return round(2.2 * s_rvol + 1.0 * s_dv + 0.8 * s_chg, 4)


def build_rows(frames: Dict[str, pd.DataFrame],
               prev_close: Dict[str, float],
               names: Optional[Dict[str, str]] = None,
               sectors: Optional[Dict[str, str]] = None,
               lookback: int = 20) -> List[FlowRow]:
    """종목별 분봉 프레임 묶음에서 수급 행을 만든다."""
    names = names or {}
    sectors = sectors or {}
    rows: List[FlowRow] = []

    for tk, bars in frames.items():
        if bars is None or bars.empty:
            continue
        try:
            cum, base, rvol = compute_rvol(bars, lookback=lookback)
            today = bars.index.date[-1]
            btoday = bars[bars.index.date == today]
            up_share, cmf, net = compute_direction(btoday)

            px = float(btoday["close"].iloc[-1]) if len(btoday) else float("nan")
            pc = prev_close.get(tk, float("nan"))
            chg = ((px / pc - 1.0) * 100.0
                   if np.isfinite(px) and np.isfinite(pc) and pc > 0
                   else float("nan"))

            # 거래대금 = Σ(봉 거래량 × 봉 종가). 종가×총거래량 으로 하면
            # 장중 급등락 종목의 거래대금이 크게 왜곡된다.
            if len(btoday):
                dv = float(np.nansum(btoday["volume"].astype(float).values
                                     * btoday["close"].astype(float).values))
            else:
                dv = float("nan")

            # 어제 같은 시각까지 — "어제와 달리" 를 답하기 위한 기준선
            now_mod = int(btoday.index[-1].hour * 60
                          + btoday.index[-1].minute) if len(btoday) else 0
            pslice = prev_session_slice(bars, now_mod)
            if pslice is not None and len(pslice):
                _, _, pnet = compute_direction(pslice)
                pdv = float(np.nansum(pslice["volume"].astype(float).values
                                      * pslice["close"].astype(float).values))
            else:
                pnet, pdv = float("nan"), float("nan")

            r = FlowRow(
                ticker=tk,
                name=names.get(tk, tk),
                sector=sectors.get(tk) or _FALLBACK_SECTOR.get(tk, "기타"),
                price=px, chg_pct=chg,
                cum_volume=cum, base_volume=base, rvol=rvol,
                dollar_vol=dv,
                up_vol_share=up_share, cmf=cmf, net_flow=net,
                dollar_vol_prev=pdv, net_flow_prev=pnet,
                net_flow_delta=(net - pnet
                                if np.isfinite(net) and np.isfinite(pnet)
                                else float("nan")),
            )
            rows.append(r)
        except Exception as e:                      # 한 종목 실패가 전체를
            rows.append(FlowRow(ticker=tk,          # 죽이지 않게 한다
                                name=names.get(tk, tk),
                                note=f"계산 실패: {type(e).__name__}"))

    # 거래대금 순위(0~1) 를 점수에 넣기 위해 여기서 계산
    dvs = [r.dollar_vol for r in rows if np.isfinite(r.dollar_vol)]
    if dvs:
        order = pd.Series(dvs).rank(pct=True)
        rank_map = {}
        i = 0
        for r in rows:
            if np.isfinite(r.dollar_vol):
                rank_map[r.ticker] = float(order.iloc[i]); i += 1
        for r in rows:
            r.score = _score(r.rvol, r.dollar_vol, r.chg_pct,
                             rank_map.get(r.ticker, float("nan")))

    # 방향 라벨 — 두 프록시가 어긋나면 숨기지 않고 '혼조' 로 둔다
    for r in rows:
        votes = 0
        if np.isfinite(r.up_vol_share):
            votes += 1 if r.up_vol_share > 0.55 else (
                -1 if r.up_vol_share < 0.45 else 0)
        if np.isfinite(r.cmf):
            votes += 1 if r.cmf > 0.05 else (-1 if r.cmf < -0.05 else 0)
        r.direction = ("매수우위" if votes >= 2 else
                       "매도우위" if votes <= -2 else
                       "혼조" if votes == 0 and np.isfinite(r.cmf) else "중립")
        r.agree = abs(votes) != 1

        # 순매수 프록시와 당일 등락이 어긋나는 경우를 표시한다.
        # CMF 는 봉 **안에서** 종가가 어디 찍혔는지만 보므로, 하루 종일
        # 흘러내리면서도 매 봉 저가를 딛고 올라오면 순매수로 잡힌다.
        # 그대로 '집중 매수' 라고만 쓰면 오해를 부른다.
        if np.isfinite(r.net_flow) and np.isfinite(r.chg_pct):
            if r.net_flow > 0 and r.chg_pct < -0.5:
                r.note = "하락 중 저가 매수세 (봉 내 매수, 일간은 하락)"
            elif r.net_flow < 0 and r.chg_pct > 0.5:
                r.note = "상승 중 차익 매도세 (봉 내 매도, 일간은 상승)"
    return rows


def aggregate_sectors(rows: List[FlowRow]) -> List[SectorFlow]:
    """섹터별로 묶는다 — '돈이 어느 섹터로 쏠리는가' 가 여기서 나온다."""
    acc: Dict[str, SectorFlow] = {}
    for r in rows:
        s = acc.setdefault(r.sector, SectorFlow(sector=r.sector))
        s.n += 1
        if np.isfinite(r.dollar_vol):
            s.dollar_vol += r.dollar_vol
        if np.isfinite(r.net_flow):
            s.net_flow += r.net_flow

    # 어제 같은 시각까지의 섹터 합계
    prev_dv: Dict[str, float] = {}
    for r in rows:
        if np.isfinite(r.dollar_vol_prev):
            prev_dv[r.sector] = prev_dv.get(r.sector, 0.0) + r.dollar_vol_prev
        if np.isfinite(r.net_flow_prev):
            s = acc.get(r.sector)
            if s is not None:
                s.net_flow_prev = (0.0 if not np.isfinite(s.net_flow_prev)
                                   else s.net_flow_prev) + r.net_flow_prev

    for name, s in acc.items():
        rs = [r.rvol for r in rows if r.sector == name and np.isfinite(r.rvol)]
        cs = [r.chg_pct for r in rows
              if r.sector == name and np.isfinite(r.chg_pct)]
        s.avg_rvol = float(np.median(rs)) if rs else float("nan")
        s.avg_chg = float(np.mean(cs)) if cs else float("nan")

    total = sum(s.dollar_vol for s in acc.values()) or 1.0
    total_prev = sum(prev_dv.values()) or float("nan")
    for name, s in acc.items():
        s.share = s.dollar_vol / total
        if np.isfinite(total_prev) and total_prev > 0:
            s.share_prev = prev_dv.get(name, 0.0) / total_prev
            s.share_delta = (s.share - s.share_prev) * 100.0   # %p
        if np.isfinite(s.net_flow_prev):
            s.net_flow_delta = s.net_flow - s.net_flow_prev

        # 강세 판정 — 등락과 자금 방향이 **함께** 말할 때만 라벨을 준다.
        # 오르는데 순매도이거나, 내리는데 순매수면 그건 중립으로 남긴다.
        up = np.isfinite(s.avg_chg) and s.avg_chg > 0.2
        dn = np.isfinite(s.avg_chg) and s.avg_chg < -0.2
        inflow = np.isfinite(s.net_flow) and s.net_flow > 0
        s.strength = ("강세" if (up and inflow) else
                      "약세" if (dn and not inflow) else "중립")
    return sorted(acc.values(), key=lambda x: -x.dollar_vol)


def compute_breadth(rows: List[FlowRow]) -> Dict[str, Any]:
    """시장 폭 — 몇 종목이 오르고 몇이 내리는가, 거래대금은 어디에."""
    chg = [r.chg_pct for r in rows if np.isfinite(r.chg_pct)]
    up = sum(1 for c in chg if c > 0)
    dn = sum(1 for c in chg if c < 0)
    hot = [r for r in rows if np.isfinite(r.rvol) and r.rvol >= 1.5]
    up_dv = sum(r.dollar_vol for r in rows
                if np.isfinite(r.dollar_vol) and np.isfinite(r.chg_pct)
                and r.chg_pct > 0)
    dn_dv = sum(r.dollar_vol for r in rows
                if np.isfinite(r.dollar_vol) and np.isfinite(r.chg_pct)
                and r.chg_pct < 0)
    tot_dv = up_dv + dn_dv
    return {
        "advancing": up, "declining": dn, "total": len(rows),
        "hot_count": len(hot),
        "up_dollar_share": (up_dv / tot_dv) if tot_dv > 0 else None,
        "total_dollar_vol": tot_dv or None,
    }


# ══════════════════════════════════════════════════════════════
#  데이터 수집 — 야후(무키) 기본, Alpaca 는 키 있으면 승격
# ══════════════════════════════════════════════════════════════

_CACHE: Dict[str, Tuple[float, "FlowBoard"]] = {}
_CACHE_LOCK = threading.Lock()
CACHE_TTL_SEC = 180.0          # 15분 지연 데이터라 3분 캐시로 충분하다


def _norm_bars(df: "pd.DataFrame") -> "pd.DataFrame":
    """OHLCV 소문자 컬럼 + DatetimeIndex 계약으로 맞춘다."""
    if df is None or len(df) == 0:
        return pd.DataFrame()
    d = df.copy()
    if isinstance(d.columns, pd.MultiIndex):
        d.columns = d.columns.get_level_values(0)
    d.columns = [str(c).strip().lower() for c in d.columns]
    keep = [c for c in ("open", "high", "low", "close", "volume")
            if c in d.columns]
    d = d[keep]
    if not isinstance(d.index, pd.DatetimeIndex):
        d.index = pd.to_datetime(d.index, errors="coerce")
    d = d[~d.index.isna()]
    # 시각 비교를 하므로 거래소 현지시각으로 통일한다(tz 정보는 떼어낸다)
    try:
        if d.index.tz is not None:
            d.index = d.index.tz_localize(None)
    except (TypeError, AttributeError):
        pass
    return d.sort_index()


def _fetch_yahoo(tickers: List[str], interval: str = "5m",
                 period: str = "1mo") -> Dict[str, "pd.DataFrame"]:
    """
    야후에서 분봉을 한 번에 받는다.

    야후는 무키인데도 **통합 거래량**을 준다(실측 AAPL 일 4,000만 주대 —
    IEX 단독이면 100만 주대). 다만 비공식 경로라 종종 빈 프레임이
    돌아오므로 종목 단위로 실패를 흡수한다.
    """
    import yfinance as yf
    out: Dict[str, pd.DataFrame] = {}
    CH = 40                                    # 한 번에 너무 많이 요청하면 잘린다
    for i in range(0, len(tickers), CH):
        chunk = tickers[i:i + CH]
        try:
            raw = yf.download(chunk, period=period, interval=interval,
                              progress=False, auto_adjust=False,
                              group_by="ticker", threads=True)
        except Exception:
            continue
        for tk in chunk:
            try:
                sub = raw[tk] if isinstance(raw.columns, pd.MultiIndex) else raw
                nb = _norm_bars(sub)
                if not nb.empty and "volume" in nb:
                    out[tk] = nb
            except Exception:
                continue
    return out


def _fetch_prev_close(tickers: List[str],
                      session_date: Optional[dt.date] = None
                      ) -> Dict[str, float]:
    """
    전일 종가 — 등락률의 기준선.

    기준일을 **로컬 달력 날짜로 잡으면 안 된다.** 한국에서 오전에 보면
    로컬은 8/19 인데 미국장 세션은 8/18 이다. `date < today` 로 자르면
    8/18 이 '과거' 로 분류돼 그날 종가를 전일종가로 쓰게 되고, 분봉의
    마지막 종가도 8/18 이라 **같은 날끼리 비교**해 등락률이 전부 0.0%
    으로 나온다. 실제로 그렇게 나왔다.

    그래서 기준일은 분봉에서 읽은 **세션 날짜**를 받아 쓴다.
    """
    import yfinance as yf
    out: Dict[str, float] = {}
    try:
        raw = yf.download(tickers, period="14d", interval="1d",
                          progress=False, auto_adjust=False,
                          group_by="ticker", threads=True)
    except Exception:
        return out
    for tk in tickers:
        try:
            sub = raw[tk] if isinstance(raw.columns, pd.MultiIndex) else raw
            d = _norm_bars(sub).dropna(subset=["close"])
            if d.empty:
                continue
            if session_date is not None:
                past = d[d.index.date < session_date]
            else:
                past = d.iloc[:-1]
            src = past if not past.empty else d.iloc[:-1]
            if len(src):
                out[tk] = float(src["close"].iloc[-1])
        except Exception:
            continue
    return out



def _sector_map(tickers: List[str]) -> Dict[str, str]:
    """
    종목 → 섹터.

    ETF 를 전부 '기타' 로 몰면 그 칸이 거래대금의 35% 를 먹어 섹터 표가
    무의미해진다(실측). 섹터 ETF 는 해당 섹터로 접고, 지수·원자재·채권
    ETF 는 섹터가 아니므로 따로 세운다.
    """
    m: Dict[str, str] = {}
    for tk in tickers:
        if tk in US_SECTOR_ETF:
            m[tk] = US_SECTOR_ETF[tk]
        elif tk in US_BROAD_ETF:
            m[tk] = "지수·원자재 ETF"
        else:
            m[tk] = _FALLBACK_SECTOR.get(tk, "기타")
    return m


def alpaca_available() -> bool:
    """Alpaca 키 한 쌍이 모두 있는가."""
    try:
        from engine.data.keyconfig import get_key
        return bool(get_key("alpaca") and get_key("alpaca_secret"))
    except Exception:
        return False


def alpaca_universe(limit: int = 400) -> List[str]:
    """
    Alpaca 에서 거래 가능한 미국 주식 목록을 받는다.

    키가 없거나 실패하면 내장 유니버스로 떨어진다 — 이 함수는 절대
    예외를 밖으로 내보내지 않는다.
    """
    if not alpaca_available():
        return []
    try:
        import json as _json
        import urllib.request
        from engine.data.keyconfig import get_key
        req = urllib.request.Request(
            "https://api.alpaca.markets/v2/assets"
            "?status=active&asset_class=us_equity",
            headers={
                "APCA-API-KEY-ID": get_key("alpaca") or "",
                "APCA-API-SECRET-KEY": get_key("alpaca_secret") or "",
            })
        with urllib.request.urlopen(req, timeout=15) as r:
            data = _json.loads(r.read().decode("utf-8"))
        syms = [a["symbol"] for a in data
                if a.get("tradable") and a.get("exchange") in
                ("NASDAQ", "NYSE", "ARCA", "AMEX")
                and "/" not in a.get("symbol", "")]
        return sorted(syms)[:limit]
    except Exception:
        return []


def _fmt_money(x: float) -> str:
    """달러 금액을 읽기 좋게. 부호는 유지한다."""
    if not np.isfinite(x):
        return "-"
    a = abs(x)
    sign = "+" if x > 0 else ("-" if x < 0 else "")
    if a >= 1e9:
        return f"{sign}${a/1e9:.1f}B"
    if a >= 1e6:
        return f"{sign}${a/1e6:.0f}M"
    return f"{sign}${a:,.0f}"


def _headline(b: "FlowBoard") -> str:
    """
    한 줄 요약 — 세 질문에 바로 답한다.
    데이터가 약하면 단정하지 않고 그 사실을 말한다.
    """
    parts: List[str] = []
    strong = [s for s in b.sectors if s.strength == "강세"]
    weak = [s for s in b.sectors if s.strength == "약세"]
    if strong:
        top = max(strong, key=lambda s: (s.avg_chg if np.isfinite(s.avg_chg)
                                         else -99))
        parts.append(f"강세 {top.sector} ({top.avg_chg:+.1f}%)")
    if weak:
        bot = min(weak, key=lambda s: (s.avg_chg if np.isfinite(s.avg_chg)
                                       else 99))
        parts.append(f"약세 {bot.sector} ({bot.avg_chg:+.1f}%)")
    if b.rotation:
        mv = b.rotation[0]
        d = mv.share_delta
        if abs(d) >= 1.0:
            parts.append(f"어제 대비 자금 {mv.sector} "
                         f"{'유입' if d > 0 else '이탈'} {abs(d):.1f}%p")
    share = b.breadth.get("up_dollar_share")
    if share is not None:
        parts.append(f"상승측 거래대금 {share*100:.0f}%")
    if not parts:
        return "판단할 만한 차이가 없다 — 자금이 한쪽으로 쏠리지 않았다."
    return " · ".join(parts)


def build_board(market: str = "US",
                universe: Optional[List[str]] = None,
                lookback: int = 20,
                top: int = 50,
                use_cache: bool = True) -> FlowBoard:
    """
    당일 수급 보드를 만든다. 실패해도 예외 대신 error 필드를 채운 보드를
    돌려준다 — 대시보드 위젯 하나가 화면 전체를 죽이면 안 된다.
    """
    import time as _time
    # 캐시 키에 파라미터를 다 넣지 않으면 값을 바꿔도 예전 결과가 나온다.
    # lookback 이 빠져 있어서 20일 기준과 60일 기준이 같은 칸을 썼다.
    key = f"{market}:{top}:{lookback}:{len(universe or [])}"
    if use_cache:
        with _CACHE_LOCK:
            hit = _CACHE.get(key)
            if hit and (_time.time() - hit[0]) < CACHE_TTL_SEC:
                return hit[1]

    board = FlowBoard(market=market, kind="proxy",
                      asof=dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

    if market.upper() == "KR":
        board.kind = "investor"
        board.error = ("한국 투자자별 수급(기관/외국인/개인)은 KRX 연동이 "
                       "아직 붙지 않았습니다.")
        board.caveat = ("한국은 KRX 가 실제 주체별 매매동향을 공개하므로 "
                        "프록시가 아니라 실데이터를 쓸 예정입니다.")
        return board

    tickers = universe or alpaca_universe() or DEFAULT_US_UNIVERSE
    tickers = list(dict.fromkeys(tickers))[:400]

    try:
        frames = _fetch_yahoo(tickers)
        if not frames:
            board.error = "분봉 데이터를 받지 못했습니다 (네트워크/소스 문제)."
            return board
        # 세션 날짜는 분봉에서 읽는다 — 로컬 달력과 다를 수 있다
        any_bars0 = next(iter(frames.values()))
        sess_date = any_bars0.index[-1].date()
        prev = _fetch_prev_close(list(frames), session_date=sess_date)
        rows = build_rows(frames, prev, sectors=_sector_map(list(frames)),
                          lookback=lookback)
        rows = [r for r in rows if np.isfinite(r.score)]
        rows.sort(key=lambda r: -r.score)

        sess, prog = _session_progress(any_bars0.index)

        board.rows = rows[:top]
        board.sectors = aggregate_sectors(rows)
        board.breadth = compute_breadth(rows)
        board.session, board.progress = sess, prog

        # ① 지금 집중 매수/매도가 어디인가 — 순매수 프록시 절대액 기준.
        #    점수(이상활동) 순이 아니라 **금액 방향** 순이어야 답이 된다.
        buys = [r for r in rows if np.isfinite(r.net_flow) and r.net_flow > 0]
        sells = [r for r in rows if np.isfinite(r.net_flow) and r.net_flow < 0]
        board.top_buy = sorted(buys, key=lambda r: -r.net_flow)[:10]
        board.top_sell = sorted(sells, key=lambda r: r.net_flow)[:10]

        # ② 어제와 달리 어디로 몰리는가 — 거래대금 비중 변화(%p) 순
        rot = [s for s in board.sectors if np.isfinite(s.share_delta)]
        board.rotation = sorted(rot, key=lambda s: -abs(s.share_delta))[:8]

        # ③ 오늘 강세 섹터 + 한 줄 요약
        board.headline = _headline(board)
        board.source = ("Alpaca 유니버스 + 야후 통합 분봉"
                        if alpaca_available() else "야후 통합 분봉 (무키)")
        board.caveat = (
            "미국은 기관/외국인/개인 구분이 공개되지 않습니다. 여기 방향은 "
            "봉 모양에서 추정한 프록시(업다운 볼륨 · CMF)이며 실제 체결 "
            "주체가 아닙니다. 데이터는 약 15분 지연입니다.")
    except Exception as e:
        board.error = f"{type(e).__name__}: {e}"
        return board

    if use_cache:
        with _CACHE_LOCK:
            _CACHE[key] = (_time.time(), board)
    return board
