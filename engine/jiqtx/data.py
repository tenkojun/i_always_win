# ==============================================================================
# [21/25] data.py — yfinance + FRED + GPR 수집 · 무결성 검증
# ==============================================================================

"""
jiqtx.data — 데이터 수집 + 무결성 검증 (Data Steward 에이전트의 실행부).

Yahoo Finance 현실 제약 (반드시 인지)
-------------------------------------
- 상장폐지 종목 없음  → 생존편향. 종목선택 전략은 원리적으로 검증 불가.
- 조정종가 처리 불일치 가능 → Close vs Adj Close 정합성 검증 필수.
- 인트라데이 제한       → 진짜 실현변동성 불가. 일봉 프록시 한계 명시.
- 펀더멘털 PIT 아님     → 재무 팩터는 최소 90~180일 래그 또는 사용 금지.
- 옵션은 현재 스냅샷만  → 백테스트 불가. 오늘부터 스냅샷 축적 권장.
- 비공식 API           → 캐싱·백오프. 상업적 사용 시 라이선스 확인.

무료 보완 소스
--------------
FRED CSV (무인증): https://fred.stlouisfed.org/graph/fredgraph.csv?id=<SERIES>
Iacoviello GPR   : https://www.matteoiacoviello.com/gpr.htm
Ken French       : 팩터 원본 (프록시 ETF보다 정확)
"""

import io
import os
import time
import warnings
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

# ── 패키지 내부 의존 ──────────────────────────────────────────
from .config import FRED_SERIES, PROXY_TICKERS



# 캐시는 앱 폴더 안 .data/cache/jiqtx 에 둔다 — 프로그램 밖에 상태를 만들지 않는다.
from engine.paths import CACHE_DIR as _APP_CACHE

_CACHE_DIR = os.environ.get("JIQTX_CACHE", str(_APP_CACHE / "jiqtx"))


def _ensure_cache():
    os.makedirs(_CACHE_DIR, exist_ok=True)


# ---------------------------------------------------------------- 무결성

@dataclass
class DataIntegrity:
    ticker: str
    n_rows: int
    start: str
    end: str
    missing_ratio: float
    duplicate_dates: int
    nonpositive_prices: int
    ohlc_violations: int          # High < Low, Close 범위 이탈 등
    extreme_moves: int            # |일간 로그수익| > 40%
    adj_close_consistent: Optional[bool]
    implied_total_return_gap: float
    stale_days: int               # 마지막 데이터 이후 경과 영업일
    passed: bool
    issues: List[str] = field(default_factory=list)


def check_integrity(ticker: str, df: pd.DataFrame,
                    max_missing: float = 0.02) -> DataIntegrity:
    issues: List[str] = []
    n = len(df)
    if n == 0:
        return DataIntegrity(ticker, 0, "", "", 1.0, 0, 0, 0, 0, None, np.nan,
                             9999, False, ["데이터 없음"])

    idx = pd.DatetimeIndex(df.index)
    dups = int(idx.duplicated().sum())
    if dups:
        issues.append(f"중복 날짜 {dups}건")

    cols = ["Open", "High", "Low", "Close"]
    px = df[cols].astype(float)
    nonpos = int((px <= 0).sum().sum())
    if nonpos:
        issues.append(f"비양수 가격 {nonpos}건")

    viol = int(((px["High"] < px["Low"]) |
                (px["Close"] > px["High"] * 1.0001) |
                (px["Close"] < px["Low"] * 0.9999) |
                (px["Open"] > px["High"] * 1.0001) |
                (px["Open"] < px["Low"] * 0.9999)).sum())
    if viol:
        issues.append(f"OHLC 논리 위반 {viol}건")

    c = px["Close"].values
    with np.errstate(invalid="ignore", divide="ignore"):
        lr = np.diff(np.log(np.where(c > 0, c, np.nan)))
    extreme = int(np.nansum(np.abs(lr) > 0.40))
    if extreme:
        issues.append(f"|일간수익|>40% {extreme}건 — 분할/배당 조정 오류 가능")

    # 기대 거래일 대비 결측
    #
    # pd.bdate_range 는 월~금을 전부 세지만 주요 거래소는 매년 9~13일 휴장한다.
    # 8년 구간이면 영업일의 약 4% 라, 이걸 결측으로 세면 임계 2% 를 그냥 넘겨
    # **정상적인 미국 종목이 전부 무결성 게이트에서 탈락**했다(→ A1 거부권 →
    # 전 모듈 무효화 → 무조건 ABSTAIN). 휴장일 몫을 빼고 센다.
    # 휴장일 수는 시장마다 다르다(미국 9~10, 한국 13~16, 일본 16~20).
    # 종목마다 거래소를 알아내 달력을 맞추는 건 외부 의존이 필요하므로,
    # 휴장이 가장 많은 주요 시장을 기준으로 넉넉히 잡는다. 그 대신
    # "진짜 구멍"은 아래의 연속공백 검사가 잡는다 — 흩어진 결측은 휴장이지만
    # 15거래일이 통째로 비는 건 휴장으로 설명되지 않는다.
    span_years = max((idx.max() - idx.min()).days / 365.25, 0.0)
    exp_bdays = len(pd.bdate_range(idx.min(), idx.max()))
    holiday_allowance = int(round(16.0 * span_years))
    exp_trading = max(1, exp_bdays - holiday_allowance)
    missing = max(0.0, 1.0 - n / exp_trading)
    if missing > max_missing:
        issues.append(f"거래일 대비 결측 {missing:.1%}")

    # 흩어진 결측은 휴장이지만, 길게 연속으로 비면 진짜 구멍이다.
    # (거래정지·상장이전·수집 실패 — 휴장으로는 10거래일이 비지 않는다.)
    try:
        gap_days = idx.to_series().diff().dt.days
        long_gaps = int((gap_days > 15).sum())
    except Exception:
        long_gaps = 0
    if long_gaps:
        issues.append(f"15일 이상 데이터 공백 {long_gaps}건")

    # Adj Close 정합성: Adj Close 수익률 ≥ Close 수익률 (배당 반영)
    adj_ok, gap = None, np.nan
    if "Adj Close" in df.columns:
        a = df["Adj Close"].astype(float).values
        with np.errstate(invalid="ignore", divide="ignore"):
            lra = np.diff(np.log(np.where(a > 0, a, np.nan)))
        gap = float(np.nansum(lra) - np.nansum(lr))
        adj_ok = bool(gap >= -1e-4)
        if not adj_ok:
            issues.append(f"Adj Close 누적수익이 Close보다 낮음 ({gap:+.2%}) "
                          "— 조정 오류 의심")

    stale = int(len(pd.bdate_range(idx.max(), pd.Timestamp.today()))) - 1
    if stale > 5:
        issues.append(f"최신 데이터가 {stale}영업일 전")

    passed = (nonpos == 0 and viol == 0 and dups == 0
              and missing <= max_missing and extreme <= 2
              and long_gaps <= 2)
    return DataIntegrity(ticker, n, str(idx.min().date()), str(idx.max().date()),
                         missing, dups, nonpos, viol, extreme, adj_ok, gap,
                         max(stale, 0), passed, issues)


# ---------------------------------------------------------------- 캐시 IO
# 로컬 재사용 캐시일 뿐이라 컬럼형 포맷(parquet)의 이점이 없다.
# parquet 을 쓰면 pyarrow 가 딸려 오는데, 배포본에 81MB 를 더한다.
# pandas 내장 pickle 로 충분하다.
_CACHE_EXT = ".pkl"


def _cache_read(path: str) -> pd.DataFrame:
    return pd.read_pickle(path)


def _cache_write(df: pd.DataFrame, path: str) -> None:
    df.to_pickle(path)


def _repair_ohlc(df: pd.DataFrame) -> Tuple[pd.DataFrame, int]:
    """
    고가/저가 봉투를 내부 정합성에 맞게 정규화한다.

    소스(yfinance)는 통화·분할 조정 과정에서 종가가 저가보다 낮거나
    고가보다 높은 봉을 이따금 내보낸다. 대부분은 부동소수점 잡음이지만
    실제로 0.1%대 어긋남도 섞여 있다.

    이런 봉을 그대로 두면 EDGE·Corwin-Schultz 같은 스프레드 추정기가
    음수 분산을 만나 무너지고, 무결성 게이트는 봉 하나 때문에 분석
    전체를 무효화한다. **종가는 건드리지 않고**(수익률의 근거다)
    고가·저가만 O/H/L/C 의 최대·최소로 맞춘다.

    반환: (정규화된 df, 손본 봉 수)
    """
    cols = [c for c in ("Open", "High", "Low", "Close") if c in df.columns]
    if len(cols) < 4:
        return df, 0
    o, h, l, c = (df[k].astype(float) for k in ("Open", "High", "Low", "Close"))
    hi = pd.concat([o, h, l, c], axis=1).max(axis=1)
    lo = pd.concat([o, h, l, c], axis=1).min(axis=1)
    bad = (hi > h + 1e-9) | (lo < l - 1e-9)
    n_bad = int(bad.sum())
    if n_bad:
        df = df.copy()
        df["High"] = hi
        df["Low"] = lo
    return df, n_bad


# ---------------------------------------------------------------- 로더

def load_prices(ticker: str, years: int = 8, use_cache: bool = True,
                auto_adjust: bool = True) -> Tuple[pd.DataFrame, Dict]:
    """
    yfinance로 일봉 + 메타데이터.
    auto_adjust=True 면 OHLC가 총수익 기준으로 조정된다(배당·분할).
    가격 수준이 필요한 계산(스프레드 bp 등)에는 조정 전 종가도 함께 보관.
    """
    _ensure_cache()
    cache = os.path.join(_CACHE_DIR, f"{ticker.replace('/','_')}_{years}y" + _CACHE_EXT)
    meta_cache = cache.replace(_CACHE_EXT, "_meta.json")
    if use_cache and os.path.exists(cache) and \
            time.time() - os.path.getmtime(cache) < 6 * 3600:
        df = _cache_read(cache)
        meta = {}
        if os.path.exists(meta_cache):
            import json
            meta = json.load(open(meta_cache))
        return df, meta

    try:
        import yfinance as yf
    except ImportError as e:                      # pragma: no cover
        raise ImportError("pip install yfinance 가 필요합니다.") from e

    t = yf.Ticker(ticker)
    df = t.history(period=f"{years}y", auto_adjust=auto_adjust,
                   actions=True, raise_errors=False)
    if df is None or len(df) == 0:
        raise ValueError(f"{ticker}: 가격 데이터를 가져오지 못했습니다.")
    df.index = pd.DatetimeIndex(df.index).tz_localize(None)
    keep = [c for c in ["Open", "High", "Low", "Close", "Adj Close", "Volume",
                        "Dividends", "Stock Splits"] if c in df.columns]
    df = df[keep].dropna(subset=["Close"])
    df, _ohlc_fixed = _repair_ohlc(df)

    meta: Dict = {}
    try:
        info = t.get_info()
        for k in ("quoteType", "sector", "industry", "category", "longName",
                  "shortName", "longBusinessSummary", "dividendYield",
                  "marketCap", "currency", "exchange", "fundFamily",
                  "beta", "trailingPE", "totalAssets"):
            if k in info:
                meta[k] = info[k]
    except Exception as e:
        meta["_info_error"] = str(e)

    if use_cache:
        try:
            _cache_write(df, cache)
            import json
            json.dump({k: (v if isinstance(v, (int, float, str, bool, type(None)))
                           else str(v)) for k, v in meta.items()},
                      open(meta_cache, "w"))
        except Exception:
            pass
    return df, meta


def load_fred(series: Optional[List[str]] = None, years: int = 10,
              use_cache: bool = True) -> pd.DataFrame:
    """FRED CSV 직접 수집 (API 키 불필요)."""
    _ensure_cache()
    series = series or list(FRED_SERIES.keys())
    cache = os.path.join(_CACHE_DIR, f"fred_{years}y" + _CACHE_EXT)
    if use_cache and os.path.exists(cache) and \
            time.time() - os.path.getmtime(cache) < 12 * 3600:
        return _cache_read(cache)

    import urllib.request
    frames = []
    for sid in series:
        url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={sid}"
        try:
            with urllib.request.urlopen(url, timeout=30) as resp:
                raw = resp.read().decode("utf-8")
            s = pd.read_csv(io.StringIO(raw))
            dcol = s.columns[0]
            vcol = [c for c in s.columns if c != dcol][0]
            s[dcol] = pd.to_datetime(s[dcol])
            s[vcol] = pd.to_numeric(s[vcol], errors="coerce")
            s = s.set_index(dcol)[[vcol]]
            s.columns = [FRED_SERIES.get(sid, sid)]
            frames.append(s)
        except Exception as e:
            warnings.warn(f"FRED {sid} 실패: {e}")
    if not frames:
        return pd.DataFrame()
    out = pd.concat(frames, axis=1).sort_index()
    cutoff = pd.Timestamp.today() - pd.DateOffset(years=years)
    out = out.loc[out.index >= cutoff]
    if "nominal_10y" in out.columns and "nominal_2y" in out.columns \
            and "curve_2s10s" not in out.columns:
        out["curve_2s10s"] = out["nominal_10y"] - out["nominal_2y"]
    if use_cache:
        try:
            _cache_write(out, cache)
        except Exception:
            pass
    return out


def load_proxies(years: int = 8, tickers: Optional[Dict[str, str]] = None,
                 use_cache: bool = True) -> Dict[str, pd.Series]:
    """팩터 프록시 ETF 종가."""
    tickers = tickers or PROXY_TICKERS
    out: Dict[str, pd.Series] = {}
    for name, tk in tickers.items():
        if not tk:
            continue
        try:
            df, _ = load_prices(tk, years=years, use_cache=use_cache)
            out[name] = df["Close"].astype(float)
        except Exception as e:
            warnings.warn(f"프록시 {name}({tk}) 실패: {e}")
    return out


def load_gpr(use_cache: bool = True) -> Optional[pd.Series]:
    """Caldara-Iacoviello 지정학 리스크 지수 (일별)."""
    _ensure_cache()
    cache = os.path.join(_CACHE_DIR, "gpr_daily" + _CACHE_EXT)
    if use_cache and os.path.exists(cache) and \
            time.time() - os.path.getmtime(cache) < 7 * 86400:
        return _cache_read(cache).iloc[:, 0]
    import urllib.request
    for url in ("https://www.matteoiacoviello.com/gpr_files/data_gpr_daily_recent.xls",
                "https://www.matteoiacoviello.com/gpr_files/gpr_daily_recent.xls"):
        try:
            with urllib.request.urlopen(url, timeout=40) as resp:
                raw = resp.read()
            df = pd.read_excel(io.BytesIO(raw))
            dcol = [c for c in df.columns if "date" in str(c).lower()][0]
            gcol = [c for c in df.columns if str(c).upper().startswith("GPRD")][0]
            s = pd.Series(pd.to_numeric(df[gcol], errors="coerce").values,
                          index=pd.to_datetime(df[dcol]), name="gpr").dropna()
            if use_cache:
                _cache_write(s.to_frame(), cache)
            return s
        except Exception:
            continue
    warnings.warn("GPR 지수 수집 실패 — 지정학 팩터는 제외됩니다.")
    return None


def align_all(df: pd.DataFrame, macro: pd.DataFrame,
              proxies: Dict[str, pd.Series],
              gpr: Optional[pd.Series] = None
              ) -> Tuple[pd.DataFrame, pd.DataFrame, Dict[str, pd.Series]]:
    """자산 인덱스에 매크로·프록시를 forward-fill 정렬 (미래 정보 누출 없음)."""
    idx = pd.DatetimeIndex(df.index)
    m = macro.reindex(idx.union(macro.index)).ffill().reindex(idx) \
        if macro is not None and len(macro) else pd.DataFrame(index=idx)
    if gpr is not None and len(gpr):
        m = m.join(gpr.reindex(idx.union(gpr.index)).ffill().reindex(idx).rename("gpr"))
    p = {k: v.reindex(idx.union(v.index)).ffill().reindex(idx)
         for k, v in proxies.items()}
    return df, m, p
