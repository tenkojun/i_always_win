# ==============================================================================
# [07/25] taxonomy.py — 3단계 자산 분류 · 통계 지문
# ==============================================================================

"""
jiqtx.taxonomy — 자산군 분류기.

"금은 다른 관점이 필요하다"를 전 종목으로 일반화하는 모듈.

3단계
-----
Level 0  메타데이터 (quoteType / sector / category / 이름)  ← 신뢰하되 검증
Level 1  통계 지문 (statistical fingerprint)               ← 실제 판정 근거
Level 2  자산군 배정 + 신뢰도 점수

Level 1이 핵심이다. 메타데이터는 자주 틀린다(합성 ETF, 리브랜딩, 카테고리 오분류).
수익률 자체가 말하게 한다.
"""

import math
import re
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

# ── 패키지 내부 의존 ──────────────────────────────────────────
from .config import ASSET_CLASSES, AssetClassSpec




# ---------------------------------------------------------------- 지문

@dataclass
class Fingerprint:
    n_obs: int
    obs_per_year: float
    trades_weekends: bool
    ann_factor: int
    ann_vol: float
    skew: float
    kurtosis: float
    autocorr1: float
    zero_ret_ratio: float
    gap_ratio: float                 # |시가-전일종가| / 일중레인지
    dividend_yield: float
    beta_map: Dict[str, float]       # 프록시별 베타
    r2_map: Dict[str, float]
    best_proxy: Optional[str]
    best_beta: float
    best_r2: float
    leverage_detected: Optional[float]
    smoothing_suspected: bool
    price_level: float
    adv_usd: float

    def to_dict(self):
        return asdict(self)


def _safe_ret(close: pd.Series) -> np.ndarray:
    c = close.astype(float).values
    with np.errstate(divide="ignore", invalid="ignore"):
        r = np.diff(np.log(np.where(c > 0, c, np.nan)))
    return r


def build_fingerprint(df: pd.DataFrame,
                      proxies: Dict[str, pd.Series],
                      dividend_yield: float = 0.0) -> Fingerprint:
    """
    df      : Open/High/Low/Close/Volume, DatetimeIndex
    proxies : 프록시명 -> 종가 시리즈 (동일 인덱스로 정렬됨)
    """
    idx = df.index
    r = _safe_ret(df["Close"])
    n = len(r)

    span_days = max((idx[-1] - idx[0]).days, 1)
    obs_per_year = len(idx) / (span_days / 365.25)
    # 주말 거래 여부 → 크립토/24-7 시장 판별
    wknd = float(np.mean(pd.DatetimeIndex(idx).dayofweek >= 5))
    trades_weekends = wknd > 0.15
    ann = 365 if trades_weekends else 252

    rr = r[np.isfinite(r)]
    ann_vol = float(rr.std(ddof=1) * math.sqrt(ann)) if len(rr) > 20 else np.nan
    skew = float(pd.Series(rr).skew()) if len(rr) > 20 else np.nan
    kurt = float(pd.Series(rr).kurtosis() + 3.0) if len(rr) > 20 else np.nan
    ac1 = float(np.corrcoef(rr[:-1], rr[1:])[0, 1]) if len(rr) > 60 else np.nan
    zr = float(np.mean(np.abs(rr) < 1e-12)) if len(rr) else np.nan

    o, h, l, c = (df[k].astype(float).values for k in ("Open", "High", "Low", "Close"))
    rng_ = np.where((h - l) > 0, h - l, np.nan)
    gap = float(np.nanmedian(np.abs(o[1:] - c[:-1]) / rng_[1:])) if len(o) > 20 else np.nan

    beta_map, r2_map = {}, {}
    for name, px in proxies.items():
        if px is None:
            continue
        pr = _safe_ret(px.reindex(idx).ffill())
        m = np.isfinite(r) & np.isfinite(pr)
        if m.sum() < 100:
            continue
        x, y = pr[m], r[m]
        vx = float(np.var(x, ddof=1))
        if vx <= 0:
            continue
        b = float(np.cov(x, y, ddof=1)[0, 1] / vx)
        cc = float(np.corrcoef(x, y)[0, 1])
        beta_map[name] = b
        r2_map[name] = cc ** 2

    best_proxy = max(r2_map, key=r2_map.get) if r2_map else None
    best_r2 = r2_map.get(best_proxy, np.nan) if best_proxy else np.nan
    best_beta = beta_map.get(best_proxy, np.nan) if best_proxy else np.nan

    # 레버리지 탐지: 특정 프록시에 대해 |β| 이 정수배 근방 + R² 매우 높음
    lev = None
    if best_proxy and np.isfinite(best_r2) and best_r2 > 0.93 and np.isfinite(best_beta):
        for cand in (-3.0, -2.0, -1.0, 2.0, 3.0):
            if abs(best_beta - cand) < 0.30 and abs(cand) != 1.0:
                lev = cand
                break
            if abs(best_beta - cand) < 0.15 and cand == -1.0:
                lev = cand
                break

    smoothing = bool(np.isfinite(ac1) and ac1 > 0.20)

    px_last = float(c[-1]) if len(c) else np.nan
    dv = c * df["Volume"].astype(float).values
    adv = float(np.nanmedian(dv[-252:])) if len(dv) > 20 else np.nan

    return Fingerprint(
        n_obs=len(idx), obs_per_year=float(obs_per_year),
        trades_weekends=trades_weekends, ann_factor=ann,
        ann_vol=ann_vol, skew=skew, kurtosis=kurt, autocorr1=ac1,
        zero_ret_ratio=zr, gap_ratio=gap, dividend_yield=float(dividend_yield),
        beta_map=beta_map, r2_map=r2_map, best_proxy=best_proxy,
        best_beta=best_beta, best_r2=best_r2,
        leverage_detected=lev, smoothing_suspected=smoothing,
        price_level=px_last, adv_usd=adv,
    )


# ---------------------------------------------------------------- 키워드 규칙

_KW = [
    ("VOL_ETP",          r"\b(vix|volatility|uvxy|vxx|svxy|vixy)\b"),
    ("LEVERAGED",        r"(\b[23]x\b|ultra|ultrashort|leveraged|inverse|bear |bull |daily [23])"),
    ("PRECIOUS_METAL",   r"\b(gold|silver|platinum|palladium|bullion|precious metal)\b"),
    ("COMMODITY_ENERGY", r"\b(oil|crude|natural gas|gasoline|energy commodit|petroleum)\b"),
    ("COMMODITY_BROAD",  r"\b(commodit|agricultur|corn|wheat|soybean|copper|base metal)\b"),
    ("BOND_TIPS",        r"\b(tips|inflation.protected|inflation.linked)\b"),
    ("BOND_HY",          r"\b(high yield|junk bond|fallen angel)\b"),
    ("BOND_CREDIT",      r"\b(investment grade|corporate bond|aggregate bond|credit)\b"),
    ("BOND_GOV",         r"\b(treasury|government bond|gilt|jgb|sovereign|municipal)\b"),
    ("REIT",             r"\b(reit|real estate)\b"),
    ("FX",               r"\b(currency|forex|yen|euro trust|dollar index|sterling)\b"),
]


def _keyword_class(text: str) -> Optional[str]:
    t = (text or "").lower()
    for code, pat in _KW:
        if re.search(pat, t):
            return code
    return None


# ---------------------------------------------------------------- 분류

@dataclass
class Classification:
    ticker: str
    asset_class: str
    spec: AssetClassSpec
    confidence: float
    evidence: List[str]
    warnings: List[str]
    fingerprint: Fingerprint
    quote_type: str
    sector: str
    hybrid_with: Optional[str] = None


def classify(ticker: str, df: pd.DataFrame, meta: Dict,
             proxies: Dict[str, pd.Series]) -> Classification:
    """
    meta: yfinance .info 유사 딕셔너리
          (quoteType, sector, industry, category, longName, longBusinessSummary,
           dividendYield, marketCap ...)
    """
    fp = build_fingerprint(df, proxies,
                           dividend_yield=float(meta.get("dividendYield") or 0.0))
    qt = str(meta.get("quoteType", "") or "").upper()
    sector = str(meta.get("sector", "") or "")
    # 키워드는 **상품 이름·카테고리**에만 건다.
    # 예전에는 longBusinessSummary(회사 사업 설명 문단)까지 넣었는데,
    # 애플의 사업 설명에 Apple Card 때문에 'credit' 이 들어 있어 AAPL 이
    # '투자등급 크레딧' 채권으로 분류됐다. 금광회사엔 'gold', 정유사엔
    # 'oil' 이 당연히 들어간다 — 산문에 키워드를 거는 건 과녁이 틀렸다.
    text = " ".join(str(meta.get(k, "") or "") for k in
                    ("longName", "shortName", "category", "industry"))

    ev: List[str] = []
    warn: List[str] = []
    conf = 0.5
    cls: Optional[str] = None
    hybrid = None

    # --- 지문 우선 판정 (메타데이터보다 강함)
    if fp.trades_weekends:
        cls = "CRYPTO"
        conf = 0.95
        ev.append(f"주말 거래 관측 → 24/7 시장. 연율화 √365 적용 "
                  f"(√252 사용 시 변동성 약 {math.sqrt(365/252)-1:.0%} 과소평가)")
    if fp.leverage_detected is not None:
        cls = "LEVERAGED"
        conf = 0.92
        ev.append(f"{fp.best_proxy} 대비 β={fp.best_beta:+.2f}, R²={fp.best_r2:.2f} "
                  f"→ 레버리지 {fp.leverage_detected:+.0f}x 탐지")
        warn.append("경로의존 자산. 시뮬레이션은 기초자산에서 생성 후 "
                    "일간 리밸런싱을 재구성해야 함 (변동성 드래그).")

    # --- 실제 사업회사면 키워드를 건너뛴다
    # 거래소가 EQUITY 라고 알려 주는 건 산문 속 단어 하나보다 강한 증거다.
    # (금광회사 이름엔 'gold', 은행 이름엔 'credit' 이 들어간다.)
    if cls is None and qt == "EQUITY":
        cls = "EQUITY_LARGE"
        conf = 0.85
        ev.append("거래소 quoteType=EQUITY → 개별주. "
                  "명칭 키워드보다 우선한다.")

    # --- 키워드 (주로 ETF·ETN 상품명/카테고리)
    if cls is None:
        kw = _keyword_class(text) or _keyword_class(ticker)
        if kw:
            cls = kw
            conf = 0.75
            ev.append(f"명칭/카테고리 키워드 → {ASSET_CLASSES[kw].label_ko}")

    # --- quoteType 기반 폴백
    if cls is None:
        if qt == "CRYPTOCURRENCY":
            cls, conf = "CRYPTO", 0.9
        elif qt == "CURRENCY":
            cls, conf = "FX", 0.9
        elif qt == "INDEX":
            cls, conf = "INDEX", 0.95
        elif qt == "MUTUALFUND":
            cls, conf = "MUTUALFUND", 0.85
        elif qt == "ETF":
            cat = str(meta.get("category", "") or "").lower()
            if "sector" in cat or (sector and sector.lower() != "n/a"):
                cls, conf = "ETF_SECTOR", 0.7
            else:
                cls, conf = "ETF_EQUITY", 0.65
            ev.append(f"ETF 카테고리='{meta.get('category','')}'")
        elif qt == "EQUITY":
            mc = meta.get("marketCap") or 0
            if sector.lower().startswith("real estate"):
                cls, conf = "REIT", 0.8
            elif mc and mc >= 1e10:
                cls, conf = "EQUITY_LARGE", 0.85
            elif mc:
                cls, conf = "EQUITY_SMALL", 0.8
            else:
                cls, conf = "EQUITY_LARGE", 0.55
            ev.append(f"섹터={sector or 'n/a'}, 시총=${(mc or 0)/1e9:.1f}B")
        else:
            cls, conf = "UNKNOWN", 0.3

    # --- 지문 교차검증
    if cls in ("EQUITY_LARGE", "EQUITY_SMALL", "ETF_EQUITY", "ETF_SECTOR"):
        if fp.best_proxy == "mkt_excess" and np.isfinite(fp.best_r2):
            ev.append(f"SPY 대비 β={fp.best_beta:.2f}, R²={fp.best_r2:.2f}")
            if fp.best_r2 < 0.10:
                warn.append("주식 프록시 설명력이 매우 낮음 — 특수상황/이벤트 자산 가능성")
                conf *= 0.8

    if cls == "PRECIOUS_METAL" and np.isfinite(fp.best_r2) and fp.best_proxy == "mkt_excess" \
            and fp.best_r2 > 0.4:
        hybrid = "EQUITY_SECTOR_LIKE"
        warn.append("금 관련이나 주식 설명력이 높음 → 금광주(생산기업)일 가능성. "
                    "귀금속 현물과 팩터 구조가 다름.")

    if fp.smoothing_suspected:
        warn.append(f"1차 자기상관 {fp.autocorr1:+.2f} → 수익률 평활화 의심. "
                    "언스무딩 없이 산출한 샤프는 과대평가.")
    if np.isfinite(fp.zero_ret_ratio) and fp.zero_ret_ratio > 0.25:
        warn.append(f"무거래일 비율 {fp.zero_ret_ratio:.0%} — 유동성 절벽")
    if fp.n_obs < ASSET_CLASSES[cls].min_history_days:
        warn.append(f"이력 {fp.n_obs}일 < 요구 {ASSET_CLASSES[cls].min_history_days}일")
        conf *= 0.7

    return Classification(
        ticker=ticker, asset_class=cls, spec=ASSET_CLASSES[cls],
        confidence=float(min(conf, 0.99)), evidence=ev, warnings=warn,
        fingerprint=fp, quote_type=qt, sector=sector, hybrid_with=hybrid,
    )
