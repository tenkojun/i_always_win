"""
================================================================
  QUANT TERMINAL  ―  웹 서버 (Flask)
================================================================
eDEX-UI 풍 사이파이 터미널 + 토스 편의성 대시보드의 백엔드.

역할
----
1) 단일 페이지 앱(static/index.html) 서빙
2) 야후 파이낸스 실시간(약 15분 지연) 데이터 API
3) 기존 engine_kr 분석 엔진 연동 (종목 분석 / 포트폴리오 스코어카드)
4) 시장 데이터 + 속보 뉴스 피드

설계 원칙
---------
- 인터넷이 없으면(또는 yfinance 실패) **합성 데이터로 폴백**해서
  화면이 항상 뜨도록 한다. 응답의 ``live`` 플래그로 구분.
- PC: 이 서버를 창으로 감싸 .exe 로 배포 (run_desktop.py)
- 폰: 같은 와이파이에서 http://<PC-IP>:8765 로 접속

실행
----
  python -m webapp.server          # http://127.0.0.1:8765
"""
from __future__ import annotations

import os
import sys
import time
import json
import threading
import datetime as dt
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd
from flask import (
    Flask, jsonify, request, send_from_directory, abort, g,
    make_response, Response, stream_with_context,
)

# ── 엔진 경로 등록 ────────────────────────────────────────────────
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

_STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
_REPORTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reports")
os.makedirs(_REPORTS, exist_ok=True)

app = Flask(__name__, static_folder=None)

# ── 인증 시스템 초기화 (어드민 seed 포함) ────────────────────────
from engine.auth import init_db as _auth_init
from engine.auth.middleware import (
    attach_user, require_auth, require_admin,
    set_session_cookie, clear_session_cookie, COOKIE_NAME,
)

_auth_init()
# C4: 커뮤니티 테이블 초기화
try:
    from engine.community import init_community_db as _comm_init
    _comm_init()
except Exception as _e:
    print("[community] init skipped:", _e)
# C9: 분석 이력 테이블 초기화
try:
    from engine.analyze_history import init_history_db as _hist_init
    _hist_init()
except Exception as _e:
    print("[analyze_history] init skipped:", _e)
# C12: 실시간 워커 시작 (속보 + awareness 모니터링)
try:
    from engine.realtime_worker import start as _rt_start
    _rt_start(interval_sec=30)
except Exception as _e:
    print("[realtime_worker] start skipped:", _e)


# ── 중앙 인증 (원격) 설정 / 셋업 / 토글 ─────────────────────────
@app.route("/api/auth/remote/status")
def api_auth_remote_status():
    from engine.auth_remote import get_config, is_configured, me
    cfg = get_config()
    return jsonify({
        "configured": is_configured(),
        "server_url": cfg.get("server_url", ""),
        "session": me() if is_configured() else {"authenticated": False},
    })


@app.route("/api/auth/remote/configure", methods=["POST"])
def api_auth_remote_configure():
    from engine.auth_remote import configure
    data = request.get_json(force=True, silent=True) or {}
    url = (data.get("server_url") or "").strip()
    return jsonify(configure(url))


@app.route("/api/auth/remote/register", methods=["POST"])
def api_auth_remote_register():
    from engine.auth_remote import register
    data = request.get_json(force=True, silent=True) or {}
    return jsonify(register(
        username=(data.get("username") or "").strip(),
        password=data.get("password") or "",
        nickname=(data.get("nickname") or "").strip()))


@app.route("/api/auth/remote/login", methods=["POST"])
def api_auth_remote_login():
    from engine.auth_remote import login
    data = request.get_json(force=True, silent=True) or {}
    return jsonify(login(
        username=(data.get("username") or "").strip(),
        password=data.get("password") or ""))


@app.route("/api/auth/remote/logout", methods=["POST"])
def api_auth_remote_logout():
    from engine.auth_remote import logout
    return jsonify(logout())


@app.route("/api/auth/remote/admin/users")
def api_auth_remote_admin_users():
    from engine.auth_remote import admin_users
    return jsonify(admin_users())


@app.route("/api/auth/remote/admin/approve", methods=["POST"])
def api_auth_remote_admin_approve():
    from engine.auth_remote import admin_approve
    data = request.get_json(force=True, silent=True) or {}
    return jsonify(admin_approve(int(data.get("user_id") or 0)))


@app.route("/api/auth/remote/admin/reject", methods=["POST"])
def api_auth_remote_admin_reject():
    from engine.auth_remote import admin_reject
    data = request.get_json(force=True, silent=True) or {}
    return jsonify(admin_reject(int(data.get("user_id") or 0)))


@app.route("/api/auth/remote/pc/register", methods=["POST"])
def api_auth_remote_pc_register():
    """본인 메인 PC의 외부 접근 URL을 중앙 서버에 등록 (A6 redirect용)."""
    from engine.auth_remote import register_pc
    data = request.get_json(force=True, silent=True) or {}
    return jsonify(register_pc(
        public_url=(data.get("public_url") or "").strip(),
        pc_label=(data.get("pc_label") or "").strip()))

# Flask 종료 시 cloudflared 프로세스도 정리
import atexit as _atexit
@_atexit.register
def _cleanup_tunnel():
    try:
        from engine.cloud.tunnel import stop_quick
        stop_quick()
    except Exception:
        pass


@app.before_request
def _before():
    attach_user()

# ── 한국어 종목 별칭(검색 편의) ───────────────────────────────────
TICKER_ALIASES: Dict[str, str] = {
    "삼성전자": "005930.KS", "samsung": "005930.KS",
    "sk하이닉스": "000660.KS", "하이닉스": "000660.KS",
    "네이버": "035420.KS", "naver": "035420.KS",
    "카카오": "035720.KS", "kakao": "035720.KS",
    "현대차": "005380.KS", "기아": "000270.KS",
    "lg에너지솔루션": "373220.KS", "삼성바이오로직스": "207940.KS",
    "셀트리온": "068270.KS", "포스코": "005490.KS",
    "애플": "AAPL", "apple": "AAPL", "엔비디아": "NVDA", "nvidia": "NVDA",
    "테슬라": "TSLA", "tesla": "TSLA", "마이크로소프트": "MSFT",
    "구글": "GOOGL", "google": "GOOGL", "아마존": "AMZN",
    "메타": "META", "비트코인": "BTC-USD", "이더리움": "ETH-USD",
}

# 상단에 띄울 지수 / 환율 / 원자재
OVERVIEW_SYMBOLS: List[Dict[str, str]] = [
    {"sym": "^KS11",  "name": "코스피",     "grp": "kr"},
    {"sym": "^KQ11",  "name": "코스닥",     "grp": "kr"},
    {"sym": "^GSPC",  "name": "S&P 500",   "grp": "us"},
    {"sym": "^IXIC",  "name": "나스닥",     "grp": "us"},
    {"sym": "^DJI",   "name": "다우",       "grp": "us"},
    {"sym": "KRW=X",  "name": "원/달러",    "grp": "fx"},
    {"sym": "^VIX",   "name": "VIX 공포",   "grp": "fx"},
    {"sym": "GC=F",   "name": "금",         "grp": "cm"},
    {"sym": "CL=F",   "name": "WTI 유가",   "grp": "cm"},
    {"sym": "BTC-USD","name": "비트코인",   "grp": "cm"},
]

# ── yfinance 안전 래퍼 ───────────────────────────────────────────
_yf = None


def _get_yf():
    global _yf
    if _yf is None:
        try:
            import yfinance as yf
            _yf = yf
        except Exception:
            _yf = False
    return _yf


def _synth_series(seed: int, n: int = 180, base: float = 100.0):
    """오프라인 폴백용 합성 가격 시계열."""
    rng = np.random.default_rng(seed)
    rets = rng.normal(0.0004, 0.018, n)
    price = base * np.cumprod(1 + rets)
    idx = pd.bdate_range(end=dt.date.today(), periods=n)
    return idx, price


def _quote_one(sym: str) -> Dict[str, Any]:
    """단일 심볼 현재가/등락. 실패 시 합성."""
    yf = _get_yf()
    if yf:
        try:
            t = yf.Ticker(sym)
            h = t.history(period="5d", interval="1d")
            if h is not None and len(h) >= 2:
                last = float(h["Close"].iloc[-1])
                prev = float(h["Close"].iloc[-2])
                chg = last - prev
                pct = chg / prev * 100 if prev else 0.0
                return {"sym": sym, "price": last, "chg": chg,
                        "pct": pct, "live": True}
        except Exception:
            pass
    # 폴백
    idx, p = _synth_series(abs(hash(sym)) % 9999)
    last, prev = float(p[-1]), float(p[-2])
    chg = last - prev
    return {"sym": sym, "price": last, "chg": chg,
            "pct": chg / prev * 100 if prev else 0.0, "live": False}


# ── 라우트: 정적 파일 ────────────────────────────────────────────
@app.route("/")
def index():
    return send_from_directory(_STATIC, "index.html")


@app.route("/strategy")
def strategy_page():
    """별도 창 STRATEGY 페이지."""
    return send_from_directory(_STATIC, "strategy.html")


@app.route("/static/<path:fn>")
def static_files(fn):
    return send_from_directory(_STATIC, fn)


@app.route("/report/<path:fn>")
def report_files(fn):
    return send_from_directory(_REPORTS, fn)


@app.route("/docs/<path:fn>")
def doc_files(fn):
    """프로젝트 문서(가이드 등) 정적 서빙."""
    _DOCS = os.path.join(_ROOT, "docs")
    if not fn.endswith((".md", ".html", ".txt", ".pdf")):
        abort(404)
    return send_from_directory(_DOCS, fn)


# ── API: 시장 개요 (지수/환율/원자재) ────────────────────────────
@app.route("/api/overview")
def api_overview():
    out, any_live = [], False
    for item in OVERVIEW_SYMBOLS:
        q = _quote_one(item["sym"])
        any_live = any_live or q["live"]
        out.append({**item, **q})
    return jsonify({
        "ts": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "live": any_live, "items": out,
    })


# ── 거시 히트맵 (TradingView 풍 sector 트리맵) — B1 ──────────────
# (sector, ticker, 회사명) — 미국 대형주 약 40종, S&P500 상위
HEATMAP_UNIVERSE: List[tuple] = [
    # Technology
    ("Technology","AAPL","Apple"),("Technology","MSFT","Microsoft"),
    ("Technology","NVDA","NVIDIA"),("Technology","AVGO","Broadcom"),
    ("Technology","ORCL","Oracle"),("Technology","CRM","Salesforce"),
    ("Technology","AMD","AMD"),("Technology","ADBE","Adobe"),
    # Communication
    ("Communication","GOOGL","Alphabet"),("Communication","META","Meta"),
    ("Communication","NFLX","Netflix"),("Communication","DIS","Disney"),
    ("Communication","T","AT&T"),("Communication","VZ","Verizon"),
    # Consumer Discretionary
    ("Consumer Disc.","AMZN","Amazon"),("Consumer Disc.","TSLA","Tesla"),
    ("Consumer Disc.","HD","Home Depot"),("Consumer Disc.","MCD","McDonald's"),
    ("Consumer Disc.","NKE","Nike"),
    # Financials
    ("Financials","BRK-B","Berkshire"),("Financials","JPM","JPMorgan"),
    ("Financials","V","Visa"),("Financials","MA","Mastercard"),
    ("Financials","BAC","Bank of America"),("Financials","GS","Goldman"),
    # Healthcare
    ("Healthcare","LLY","Eli Lilly"),("Healthcare","UNH","UnitedHealth"),
    ("Healthcare","JNJ","J&J"),("Healthcare","PFE","Pfizer"),
    ("Healthcare","MRK","Merck"),("Healthcare","ABBV","AbbVie"),
    # Energy
    ("Energy","XOM","ExxonMobil"),("Energy","CVX","Chevron"),
    # Industrials
    ("Industrials","CAT","Caterpillar"),("Industrials","BA","Boeing"),
    ("Industrials","GE","GE"),
    # Consumer Staples
    ("Cons. Staples","WMT","Walmart"),("Cons. Staples","PG","P&G"),
    ("Cons. Staples","KO","Coca-Cola"),("Cons. Staples","PEP","PepsiCo"),
    # Utilities + Materials
    ("Utilities","NEE","NextEra"),
    ("Materials","LIN","Linde"),
]

# 60초 캐시 (yfinance rate-limit 방지)
_HEATMAP_CACHE: Dict[str, Any] = {"ts": 0.0, "data": None}


def _ticker_marketcap(yf, sym: str) -> float:
    """fast_info에서 market_cap 시도. 실패 시 0."""
    try:
        t = yf.Ticker(sym)
        fi = getattr(t, "fast_info", None)
        if fi is not None:
            mc = getattr(fi, "market_cap", None)
            if mc and mc > 0:
                return float(mc)
            # fallback: shares * last_price
            shares = getattr(fi, "shares", None)
            last = getattr(fi, "last_price", None)
            if shares and last:
                return float(shares) * float(last)
    except Exception:
        pass
    return 0.0


@app.route("/api/heatmap/treemap")
def api_heatmap_treemap():
    """섹터 그룹화 + 시총 비례 크기. TradingView 스타일 treemap용."""
    import time as _time
    now = _time.time()
    if _HEATMAP_CACHE["data"] and (now - _HEATMAP_CACHE["ts"]) < 60:
        return jsonify(_HEATMAP_CACHE["data"])

    yf = _get_yf()
    sectors: Dict[str, Dict[str, Any]] = {}
    for sector, sym, name in HEATMAP_UNIVERSE:
        q = _quote_one(sym)
        cap = _ticker_marketcap(yf, sym) if yf else 0.0
        # cap이 0이면 (오프라인 또는 fast_info 실패) 가격 기반 가중치
        if cap <= 0:
            cap = max(1.0, float(q.get("price") or 1.0)) * 1e9
        item = {
            "ticker": sym,
            "name": name,
            "price": q.get("price"),
            "pct": q.get("pct") or 0.0,
            "cap": cap,
            "live": q.get("live", False),
        }
        sectors.setdefault(sector, {"name": sector, "cap": 0.0, "items": []})
        sectors[sector]["cap"] += cap
        sectors[sector]["items"].append(item)

    # 섹터별 정렬 (cap desc), 섹터 리스트도 cap desc
    sector_list = []
    for s in sectors.values():
        s["items"].sort(key=lambda x: x["cap"], reverse=True)
        sector_list.append(s)
    sector_list.sort(key=lambda x: x["cap"], reverse=True)

    total = sum(s["cap"] for s in sector_list) or 1.0
    out = {
        "ts": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "total_cap": total,
        "sectors": sector_list,
    }
    _HEATMAP_CACHE["ts"] = now
    _HEATMAP_CACHE["data"] = out
    return jsonify(out)


# ── API: 다중 종목 시세 (왓치리스트용) ───────────────────────────
@app.route("/api/quotes")
def api_quotes():
    """쉼표 구분 심볼 리스트의 현재가/등락 — 왓치리스트 위젯용."""
    syms = (request.args.get("symbols") or "").strip()
    if not syms:
        return jsonify({"quotes": {}})
    symbols = [s.strip() for s in syms.split(",") if s.strip()][:30]
    out: Dict[str, Any] = {}
    yf = _get_yf()
    for sym in symbols:
        try:
            q = _quote_one(sym)
            name = ""
            if yf:
                try:
                    info = yf.Ticker(sym).fast_info
                    name = (getattr(info, "shortName", "") or "")[:50]
                except Exception:
                    pass
            out[sym] = {
                "price": q.get("price"),
                "change": q.get("chg"),
                "change_pct": q.get("pct"),
                "live": q.get("live", False),
                "name": name,
            }
        except Exception:
            out[sym] = None
    return jsonify({"quotes": out})


# ── API: 종목 상세 정보 (Symbol Info 위젯) ───────────────────────
@app.route("/api/symbol_info")
def api_symbol_info():
    """현재가 + 펀더멘털 병합 — 종목 상세 위젯용."""
    ticker = (request.args.get("ticker") or "").strip()
    if not ticker:
        return jsonify({"error": "ticker 누락"}), 400
    out: Dict[str, Any] = {"ticker": ticker}
    # quote
    try:
        q = _quote_one(ticker)
        yf = _get_yf()
        name = ""
        market = ""
        if yf:
            try:
                t = yf.Ticker(ticker)
                fi = getattr(t, "fast_info", None)
                if fi is not None:
                    name = (getattr(fi, "shortName", "") or "")[:60]
                    market = (getattr(fi, "exchange", "")
                              or getattr(fi, "quoteType", "") or "")
            except Exception:
                pass
        out["quote"] = {
            "price": q.get("price"),
            "change": q.get("chg"),
            "change_pct": q.get("pct"),
            "live": q.get("live", False),
            "name": name,
            "market": market,
        }
    except Exception as e:
        out["quote"] = {"error": str(e)}
    # fundamentals (다중소스 병합)
    try:
        from engine.data.sources import fetch_fundamentals_best
        f = fetch_fundamentals_best(ticker) or {}
        # _label 같은 내부 필드는 제외
        out["fundamentals"] = {k: v for k, v in f.items()}
    except Exception:
        out["fundamentals"] = {}
    return jsonify(out)


# ── API: 캘린더 (어닝 — AV 무료 / 경제 — 별도) ──────────────────
@app.route("/api/calendar")
def api_calendar():
    """이번주~3개월 어닝 캘린더 (Alpha Vantage 무료 키).

    FMP/Finnhub의 경제 캘린더는 모두 유료 전환 → 무료로는 어닝만 제공.
    매크로 이벤트(FOMC/CPI 등)는 별도 [B] 단계에서 GDELT alert로 보완 예정.
    """
    import datetime as _dt
    import csv
    import io
    today = _dt.date.today()
    end_date = today + _dt.timedelta(days=14)
    events: List[Dict[str, Any]] = []
    note = ""
    try:
        from engine.data.keyconfig import get_key
        av_key = get_key("alphavantage")
        if not av_key:
            return jsonify({"events": [], "note":
                "어닝 캘린더는 Alpha Vantage 키가 필요합니다 (⚙ 설정).",
                "from": today.strftime("%Y-%m-%d"),
                "to": end_date.strftime("%Y-%m-%d")})
        import requests
        r = requests.get("https://www.alphavantage.co/query",
                         params={"function": "EARNINGS_CALENDAR",
                                 "horizon": "3month",
                                 "apikey": av_key}, timeout=15)
        if r.status_code != 200:
            return jsonify({"events": [],
                            "note": f"AV 응답 실패 ({r.status_code})"})
        # AV는 CSV로 반환
        txt = r.text
        if txt.strip().startswith("{"):
            # JSON이면 Information 메시지(rate limit 등)
            return jsonify({"events": [],
                            "note": f"AV: {txt[:120]}"})
        reader = csv.DictReader(io.StringIO(txt))
        count = 0
        for row in reader:
            try:
                rep_date = row.get("reportDate", "")
                if not rep_date:
                    continue
                d = _dt.date.fromisoformat(rep_date)
                if d < today or d > end_date:
                    continue
                tod = (row.get("timeOfTheDay") or "").lower()
                tod_label = ("개장전" if "pre" in tod
                             else "마감후" if "post" in tod else "—")
                est = row.get("estimate") or ""
                events.append({
                    "date":     rep_date,
                    "time":     tod_label,
                    "country":  row.get("currency", "")[:3],
                    "event":    f"{row.get('symbol','')} 실적 발표 "
                                f"— {(row.get('name','') or '')[:50]}",
                    "impact":   "high",
                    "actual":   "",
                    "estimate": (f"EPS {est}" if est else ""),
                    "prev":     "",
                })
                count += 1
                if count >= 80:
                    break
            except Exception:
                continue
        if not events:
            note = "이번 2주간 등록된 어닝 이벤트 없음."
    except Exception as e:
        note = f"캘린더 오류: {type(e).__name__}: {str(e)[:80]}"
    return jsonify({"events": events, "note": note,
                    "from": today.strftime("%Y-%m-%d"),
                    "to": end_date.strftime("%Y-%m-%d")})


def _fmt_cal_val(v: Any) -> str:
    if v is None or v == "":
        return ""
    try:
        f = float(v)
        if abs(f) >= 1e9:
            return f"{f/1e9:.2f}B"
        if abs(f) >= 1e6:
            return f"{f/1e6:.2f}M"
        if abs(f) >= 1000:
            return f"{f:,.1f}"
        return f"{f:.2f}"
    except (TypeError, ValueError):
        return str(v)[:20]


# ── API: 종목 검색 ───────────────────────────────────────────────
@app.route("/api/search")
def api_search():
    q = (request.args.get("q") or "").strip()
    if not q:
        return jsonify({"results": []})
    key = q.lower()
    results: List[Dict[str, str]] = []
    if key in TICKER_ALIASES:
        results.append({"ticker": TICKER_ALIASES[key], "label": q})
    # yahoo 검색 시도
    yf = _get_yf()
    if yf:
        try:
            import requests
            r = requests.get(
                "https://query2.finance.yahoo.com/v1/finance/search",
                params={"q": q, "quotesCount": 8, "newsCount": 0},
                headers={"User-Agent": "Mozilla/5.0"}, timeout=6,
            )
            for it in r.json().get("quotes", []):
                sym = it.get("symbol")
                if not sym:
                    continue
                nm = it.get("shortname") or it.get("longname") or sym
                results.append({"ticker": sym, "label": nm})
        except Exception:
            pass
    if not results:  # 입력을 그대로 티커로 간주
        results.append({"ticker": q.upper(), "label": q.upper()})
    # 중복 제거
    seen, uniq = set(), []
    for r in results:
        if r["ticker"] in seen:
            continue
        seen.add(r["ticker"])
        uniq.append(r)
    return jsonify({"results": uniq[:8]})


# ── API: 차트 데이터 ─────────────────────────────────────────────
# range=1d → 인트라데이 5분봉 (오늘+어제)
# 그 외    → 전체 이력 일봉 (프론트에서 zoom)
@app.route("/api/chart")
def api_chart():
    ticker = (request.args.get("ticker") or "AAPL").strip()
    rng = request.args.get("range", "full")
    # 인트라데이 옵션 (1m/5m/15m/1h)
    intraday_map = {
        "1m": ("7d", "1m"),
        "5m": ("60d", "5m"),
        "15m": ("60d", "15m"),
        "1h": ("60d", "60m"),
        "1d": ("2d", "5m"),    # 기존 호환 (인트라데이 기본)
    }
    if rng in intraday_map:
        period, interval = intraday_map[rng]
        intraday = True
    else:
        period, interval = "max", "1d"
        intraday = False
    yf = _get_yf()
    if yf:
        try:
            h = yf.Ticker(ticker).history(period=period, interval=interval)
            if h is not None and not h.empty:
                h = h.dropna()
                candles = [{
                    "t": int(pd.Timestamp(ix).timestamp()),
                    "o": round(float(r["Open"]), 4),
                    "h": round(float(r["High"]), 4),
                    "l": round(float(r["Low"]), 4),
                    "c": round(float(r["Close"]), 4),
                    "v": float(r.get("Volume", 0) or 0),
                } for ix, r in h.iterrows()]
                last  = candles[-1]["c"]
                first = candles[0]["c"]
                return jsonify({
                    "ticker": ticker, "live": True, "candles": candles,
                    "last": last,
                    "pct": (last - first) / first * 100 if first else 0,
                })
        except Exception:
            pass
    # 폴백 합성 (오프라인)
    n = 80 if intraday else 1500
    idx, p = _synth_series(abs(hash(ticker)) % 9999, n=n)
    candles = []
    for i in range(len(p)):
        c = float(p[i])
        o = float(p[i - 1]) if i else c
        candles.append({
            "t": int(pd.Timestamp(idx[i]).timestamp()),
            "o": round(o, 4), "h": round(max(o, c) * 1.01, 4),
            "l": round(min(o, c) * 0.99, 4), "c": round(c, 4),
            "v": float(abs(np.random.randn()) * 1e6),
        })
    return jsonify({
        "ticker": ticker, "live": False, "candles": candles,
        "last": candles[-1]["c"],
        "pct": (candles[-1]["c"] - candles[0]["c"]) / candles[0]["c"] * 100,
    })


# ── API: 단일 시세 ───────────────────────────────────────────────
@app.route("/api/quote")
def api_quote():
    ticker = (request.args.get("ticker") or "AAPL").strip()
    return jsonify(_quote_one(ticker))


# ── API: 뉴스 (속보 피드) ────────────────────────────────────────
def _fetch_rss(url: str, limit: int = 12) -> List[Dict[str, str]]:
    import requests
    import xml.etree.ElementTree as ET
    items: List[Dict[str, str]] = []
    try:
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"},
                          timeout=7)
        root = ET.fromstring(r.content)
        for it in root.iter("item"):
            title = it.findtext("title") or ""
            link = it.findtext("link") or ""
            pub = it.findtext("pubDate") or ""
            if title:
                items.append({"title": title.strip(),
                              "link": link.strip(), "pub": pub.strip()})
            if len(items) >= limit:
                break
    except Exception:
        pass
    return items


# ── API: 인증 (회원가입/로그인/로그아웃/내정보) ─────────────────
@app.route("/api/auth/register", methods=["POST"])
def api_auth_register():
    """신규 가입 — status=pending. 어드민 승인 후 로그인 가능."""
    from engine.auth import create_user
    data = request.get_json(force=True, silent=True) or {}
    username = (data.get("username") or "").strip()
    password = (data.get("password") or "")
    nickname = (data.get("nickname") or "").strip()
    r = create_user(username, password, nickname=nickname)
    if not r.get("ok"):
        return jsonify({"ok": False, "error": r.get("error")}), 400
    return jsonify({"ok": True,
                    "message": "가입 신청 완료 — 어드민 승인 대기 중입니다.",
                    "user": r["user"]})


@app.route("/api/auth/login", methods=["POST"])
def api_auth_login():
    from engine.auth import (get_user_by_name, create_session,
                              verify_password)
    from engine.auth.store import _conn
    data = request.get_json(force=True, silent=True) or {}
    username = (data.get("username") or "").strip()
    password = (data.get("password") or "")
    if not username or not password:
        return jsonify({"ok": False,
                        "error": "username/password 필요"}), 400
    u = get_user_by_name(username)
    if not u:
        return jsonify({"ok": False,
                        "error": "사용자/패스워드 불일치"}), 401
    # 패스워드 검증 (raw row 필요)
    row = _conn().execute(
        "SELECT password_hash, salt FROM users WHERE id=?",
        (u["id"],)).fetchone()
    if not verify_password(password, row["password_hash"], row["salt"]):
        return jsonify({"ok": False,
                        "error": "사용자/패스워드 불일치"}), 401
    if u.get("status") == "pending":
        return jsonify({"ok": False,
                        "error": "어드민 승인 대기 중입니다.",
                        "code": "PENDING"}), 403
    if u.get("status") != "active":
        return jsonify({"ok": False,
                        "error": "비활성 계정입니다.",
                        "code": "DISABLED"}), 403
    device = (request.headers.get("User-Agent") or "")[:80]
    token = create_session(u["id"], device_label=device)
    resp = make_response(jsonify({"ok": True, "user": u}))
    set_session_cookie(resp, token)
    return resp


@app.route("/api/auth/logout", methods=["POST"])
def api_auth_logout():
    from engine.auth import delete_session
    token = request.cookies.get(COOKIE_NAME)
    if token:
        delete_session(token)
    resp = make_response(jsonify({"ok": True}))
    clear_session_cookie(resp)
    return resp


@app.route("/api/auth/me")
def api_auth_me():
    if not getattr(g, "user", None):
        return jsonify({"authenticated": False})
    from engine.auth import check_claude_quota, get_main_pc, get_user_by_id
    u = g.user
    quota = check_claude_quota(u["id"], limit=10)
    main_pc = get_main_pc(u["id"])
    # 닉네임 — DB에서 직접 fetch (session row에는 없음)
    full = get_user_by_id(u["id"]) or {}
    nickname = full.get("nickname") or u["username"]
    return jsonify({
        "authenticated": True,
        "user": {
            "id": u["id"], "username": u["username"],
            "nickname": nickname,
            "role": u["role"], "status": u["status"],
        },
        "claude_quota": quota,
        "main_pc": main_pc,
    })


# ── API: QR 일회용 로그인 토큰 ──────────────────────────────────
@app.route("/api/auth/qr_token", methods=["POST"])
@require_auth
def api_auth_qr_token():
    """현재 로그인된 사용자용 일회용 QR 토큰 발급 (5분 TTL)."""
    from engine.auth.qr_token import issue
    r = issue(g.user["id"])
    return jsonify(r)


@app.route("/qr_login")
def qr_login():
    """QR 자동로그인 — 토큰 검증 → 세션 쿠키 → /."""
    from engine.auth.qr_token import consume
    from engine.auth import create_session, get_user_by_id
    from flask import redirect
    token = (request.args.get("token") or "").strip()
    print(f"[QR_LOGIN] token={token[:10]}... len={len(token)}")
    if not token:
        return redirect("/?qr_error=no_token")
    user_id = consume(token)
    if not user_id:
        print(f"[QR_LOGIN] consume failed (만료 또는 사용됨)")
        return redirect("/?qr_error=invalid_or_expired")
    print(f"[QR_LOGIN] user_id={user_id}")
    u = get_user_by_id(user_id)
    if not u or u.get("status") != "active":
        return redirect("/?qr_error=inactive_user")
    device = (request.headers.get("User-Agent") or "")[:80]
    sess_token = create_session(user_id, device_label=device)
    resp = make_response(redirect("/"))
    set_session_cookie(resp, sess_token)
    return resp


# ── API: 어드민 (가입 승인/거부, 사용자 목록) ───────────────────
@app.route("/api/admin/users")
@require_admin
def api_admin_users():
    from engine.auth import list_all_users
    return jsonify({"users": list_all_users()})


@app.route("/api/admin/pending")
@require_admin
def api_admin_pending():
    from engine.auth import list_pending_users
    return jsonify({"users": list_pending_users()})


@app.route("/api/admin/approve", methods=["POST"])
@require_admin
def api_admin_approve():
    from engine.auth import approve_user
    data = request.get_json(force=True, silent=True) or {}
    user_id = int(data.get("user_id") or 0)
    if not user_id:
        return jsonify({"ok": False, "error": "user_id 필요"}), 400
    return jsonify(approve_user(user_id, approver_id=g.user["id"]))


@app.route("/api/admin/reject", methods=["POST"])
@require_admin
def api_admin_reject():
    from engine.auth import reject_user
    data = request.get_json(force=True, silent=True) or {}
    user_id = int(data.get("user_id") or 0)
    if not user_id:
        return jsonify({"ok": False, "error": "user_id 필요"}), 400
    if user_id == g.user["id"]:
        return jsonify({"ok": False,
                        "error": "본인 계정은 거부할 수 없습니다."}), 400
    return jsonify(reject_user(user_id))


@app.route("/api/admin/reset_quota", methods=["POST"])
@require_admin
def api_admin_reset_quota():
    from engine.auth import reset_claude_quota
    data = request.get_json(force=True, silent=True) or {}
    user_id = int(data.get("user_id") or 0)
    if not user_id:
        return jsonify({"ok": False, "error": "user_id 필요"}), 400
    reset_claude_quota(user_id)
    return jsonify({"ok": True})


@app.route("/api/admin/stats")
@require_admin
def api_admin_stats():
    """C6: 어드민 사용자 통계 — 사용량/로그인/활동 요약."""
    from engine.auth import list_all_users
    from engine.auth.store import _conn
    users = list_all_users()
    total = len(users)
    active = sum(1 for u in users if u.get("status") == "active")
    pending = sum(1 for u in users if u.get("status") == "pending")
    rejected = sum(1 for u in users if u.get("status") == "rejected")
    # 활성 세션 (현재 로그인 중)
    try:
        live_sessions = _conn().execute(
            "SELECT COUNT(DISTINCT user_id) AS n FROM sessions "
            "WHERE datetime(expires_at) > datetime('now')").fetchone()
        live = int(live_sessions["n"]) if live_sessions else 0
    except Exception:
        live = 0
    # 최근 24시간 가입
    try:
        recent_signup = _conn().execute(
            "SELECT COUNT(*) AS n FROM users "
            "WHERE datetime(created_at) > datetime('now','-1 day')"
        ).fetchone()
        signup_24h = int(recent_signup["n"]) if recent_signup else 0
    except Exception:
        signup_24h = 0
    # 사용자별 디테일 (Claude 사용 + 로그인)
    detail = []
    for u in users:
        detail.append({
            "id": u["id"],
            "username": u.get("username", ""),
            "nickname": u.get("nickname") or u.get("username", ""),
            "role": u.get("role", "user"),
            "status": u.get("status", ""),
            "created_at": u.get("created_at", ""),
            "approved_at": u.get("approved_at", ""),
            "last_login_at": u.get("last_login_at", ""),
            "login_count": u.get("login_count", 0) or 0,
            "claude_used": u.get("claude_used", 0) or 0,
            "claude_quota_date": u.get("claude_quota_date", ""),
            "main_pc_label": u.get("main_pc_label", ""),
            "main_pc_last_seen": u.get("main_pc_last_seen", ""),
        })
    # Top Claude 사용자 (오늘)
    top_claude = sorted(detail,
                         key=lambda x: x["claude_used"], reverse=True)[:5]
    # Top 로그인 사용자
    top_login = sorted(detail,
                        key=lambda x: x["login_count"], reverse=True)[:5]
    return jsonify({
        "summary": {
            "total_users": total,
            "active_users": active,
            "pending_users": pending,
            "rejected_users": rejected,
            "live_sessions": live,
            "new_users_24h": signup_24h,
        },
        "users": detail,
        "top_claude_today": top_claude,
        "top_login": top_login,
    })


# ── API: 커뮤니티 (C4) ──────────────────────────────────────────
@app.route("/api/community/posts")
@require_auth
def api_community_posts():
    from engine.community import list_posts
    limit = int(request.args.get("limit") or 50)
    offset = int(request.args.get("offset") or 0)
    limit = max(1, min(100, limit))
    offset = max(0, offset)
    return jsonify({"posts": list_posts(limit=limit, offset=offset)})


@app.route("/api/community/post/<int:pid>")
@require_auth
def api_community_post_detail(pid):
    import json as _json
    from engine.community import get_post, list_comments
    p = get_post(pid, inc_view=True)
    if not p:
        return jsonify({"error": "글 없음"}), 404
    # P7: attached_strategy_json → dict로 디코딩
    asj = p.get("attached_strategy_json")
    if asj:
        try:
            p["attached_strategy"] = _json.loads(asj)
        except Exception:
            p["attached_strategy"] = None
    return jsonify({
        "post": p,
        "comments": list_comments(pid),
    })


@app.route("/api/community/post", methods=["POST"])
@require_auth
def api_community_create_post():
    from engine.community import create_post
    data = request.get_json(force=True, silent=True) or {}
    title = data.get("title") or ""
    body = data.get("body") or ""
    pinned = bool(data.get("pinned"))
    # pinned는 어드민만 허용
    if pinned and g.user.get("role") != "admin":
        pinned = False
    # P7: 전략 첨부 (optional)
    attached = data.get("attached_strategy")
    r = create_post(g.user["id"], title, body, pinned=pinned,
                     attached_strategy=attached)
    if not r.get("ok"):
        return jsonify(r), 400
    return jsonify(r)


# ── P7: 커뮤니티 → 내 컬렉션으로 전략 가져오기 ─────────────
@app.route("/api/community/post/<int:pid>/import_strategy", methods=["POST"])
@require_auth
def api_community_import_strategy(pid):
    """글에 첨부된 전략을 본인 컬렉션으로 복사."""
    import json as _json
    from engine.community import get_post
    from engine.strategy.collection_store import add_to_collection
    p = get_post(pid, inc_view=False)
    if not p:
        return jsonify({"ok": False, "error": "글 없음"}), 404
    asj = p.get("attached_strategy_json")
    if not asj:
        return jsonify({"ok": False,
                        "error": "이 글에는 첨부 전략 없음"}), 400
    try:
        att = _json.loads(asj)
    except Exception:
        return jsonify({"ok": False, "error": "첨부 데이터 손상"}), 400
    name = (att.get("name") or
            f"{p.get('username','?')}의 {att.get('strategy','?')}")
    notes = (att.get("notes") or
             f"커뮤니티 글 #{pid} '{p.get('title','')}'에서 가져옴")
    r = add_to_collection(
        user_id=g.user["id"],
        name=name,
        strategy=att.get("strategy") or "",
        params=att.get("params") or {},
        ticker=att.get("ticker"),
        notes=notes,
        source=f"COMMUNITY_POST_{pid}",
        metrics=att.get("metrics") or {},
    )
    return jsonify(r)


@app.route("/api/community/post/<int:pid>", methods=["DELETE"])
@require_auth
def api_community_delete_post(pid):
    from engine.community import delete_post
    is_admin = (g.user.get("role") == "admin")
    r = delete_post(pid, g.user["id"], is_admin=is_admin)
    if not r.get("ok"):
        return jsonify(r), 403
    return jsonify(r)


@app.route("/api/community/comment", methods=["POST"])
@require_auth
def api_community_create_comment():
    from engine.community import create_comment
    data = request.get_json(force=True, silent=True) or {}
    pid = int(data.get("post_id") or 0)
    body = data.get("body") or ""
    if not pid:
        return jsonify({"ok": False, "error": "post_id 필요"}), 400
    r = create_comment(pid, g.user["id"], body)
    if not r.get("ok"):
        return jsonify(r), 400
    return jsonify(r)


@app.route("/api/community/comment/<int:cid>", methods=["DELETE"])
@require_auth
def api_community_delete_comment(cid):
    from engine.community import delete_comment
    is_admin = (g.user.get("role") == "admin")
    r = delete_comment(cid, g.user["id"], is_admin=is_admin)
    if not r.get("ok"):
        return jsonify(r), 403
    return jsonify(r)


# ── API: 전략 백테스트 (vectorbt) ───────────────────────────────
@app.route("/api/strategy/meta")
@require_auth
def api_strategy_meta():
    """사용 가능한 전략 + 파라미터 메타 (UI 폼 생성용)."""
    try:
        from engine.strategy import get_strategy_meta
        return jsonify(get_strategy_meta())
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/strategy/backtest", methods=["POST"])
@require_auth
def api_strategy_backtest():
    """단일 백테스트. P5: 포지션 사이징/레버리지/SL·TP 옵션 전달."""
    try:
        from engine.strategy import run_backtest
        d = request.get_json(force=True, silent=True) or {}
        def _fopt(k):
            v = d.get(k)
            try:
                return float(v) if v is not None and v != "" else None
            except Exception:
                return None
        return jsonify(run_backtest(
            ticker=(d.get("ticker") or "").upper(),
            strategy=d.get("strategy") or "sma_cross",
            params=d.get("params") or {},
            period_days=int(d.get("period_days") or 730),
            fees=float(d.get("fees") or 0.001),
            init_cash=float(d.get("init_cash") or 10000),
            interval=(d.get("interval") or "1d"),
            # P5: 포지션 옵션 (모두 optional, None이면 vbt 기본값)
            size=_fopt("size"),
            size_type=(d.get("size_type") or "amount"),
            leverage=float(d.get("leverage") or 1.0),
            slippage=float(d.get("slippage") or 0.0001),
            sl_stop=_fopt("sl_stop"),
            tp_stop=_fopt("tp_stop"),
            trail_stop=_fopt("trail_stop"),
            # Tier1 #2 + P1 #4/#5: spread + latency 자동
            auto_slippage=bool(d.get("auto_slippage", False)),
            latency_ms=float(d.get("latency_ms") or 0.0),
        ))
    except Exception as e:
        return jsonify({"ok": False,
                        "error": f"{type(e).__name__}: {e}"}), 500


def _grid_impl():
    try:
        from engine.strategy import run_grid_search
        d = request.get_json(force=True, silent=True) or {}
        return jsonify(run_grid_search(
            ticker=(d.get("ticker") or "").upper(),
            strategy=d.get("strategy") or "sma_cross",
            grid=d.get("grid") or {},
            period_days=int(d.get("period_days") or 730),
            fees=float(d.get("fees") or 0.001),
            top_n=int(d.get("top_n") or 10),
        ))
    except Exception as e:
        return jsonify({"ok": False,
                        "error": f"{type(e).__name__}: {e}"}), 500


@app.route("/api/strategy/grid", methods=["POST"])
@require_auth
def api_strategy_grid():
    return _grid_impl()


@app.route("/api/strategy/grid_search", methods=["POST"])
@require_auth
def api_strategy_grid_search():
    """alias — 클라이언트 호환용."""
    return _grid_impl()


@app.route("/api/strategy/active", methods=["GET", "POST"])
@require_auth
def api_strategy_active():
    """GET: 사용자 활성 전략 목록 (ticker 옵션).
       POST: 활성 전략 추가/갱신."""
    from engine.strategy.active_store import (
        upsert_active, list_active,
    )
    if request.method == "GET":
        tk = (request.args.get("ticker") or "").strip().upper() or None
        return jsonify({"items": list_active(g.user["id"], ticker=tk)})
    # POST
    d = request.get_json(force=True, silent=True) or {}
    return jsonify(upsert_active(
        g.user["id"],
        ticker=(d.get("ticker") or "").upper(),
        strategy=d.get("strategy") or "",
        params=d.get("params") or {},
        expected_alpha=d.get("alpha"),
        expected_sharpe=d.get("sharpe"),
    ))


@app.route("/api/strategy/active/delete", methods=["POST"])
@require_auth
def api_strategy_active_delete():
    from engine.strategy.active_store import delete_active
    d = request.get_json(force=True, silent=True) or {}
    sid = int(d.get("id") or 0)
    if not sid:
        return jsonify({"ok": False, "error": "id 필요"}), 400
    return jsonify(delete_active(g.user["id"], sid))


# ── C #11: 전략 컬렉션 (즐겨찾기) ─────────────────────────────
@app.route("/api/strategy/collection", methods=["GET", "POST"])
@require_auth
def api_strategy_collection():
    """GET: 컬렉션 목록 (ticker 옵션)
    POST: 신규 추가 {name, strategy, params, ticker?, notes?, source?, metrics?}
    """
    from engine.strategy.collection_store import (
        add_to_collection, list_collection)
    if request.method == "GET":
        ticker = (request.args.get("ticker") or "").strip().upper() or None
        return jsonify({
            "items": list_collection(g.user["id"], ticker=ticker),
        })
    d = request.get_json(force=True, silent=True) or {}
    return jsonify(add_to_collection(
        user_id=g.user["id"],
        name=(d.get("name") or "").strip(),
        strategy=(d.get("strategy") or "").strip(),
        params=d.get("params") or {},
        ticker=d.get("ticker"),
        notes=d.get("notes"),
        source=d.get("source"),
        metrics=d.get("metrics"),
    ))


@app.route("/api/strategy/collection/delete", methods=["POST"])
@require_auth
def api_strategy_collection_delete():
    from engine.strategy.collection_store import delete_from_collection
    d = request.get_json(force=True, silent=True) or {}
    iid = int(d.get("id") or 0)
    if not iid:
        return jsonify({"ok": False, "error": "id 필요"}), 400
    return jsonify(delete_from_collection(g.user["id"], iid))


@app.route("/api/strategy/collection/rename", methods=["POST"])
@require_auth
def api_strategy_collection_rename():
    from engine.strategy.collection_store import rename_collection_item
    d = request.get_json(force=True, silent=True) or {}
    iid = int(d.get("id") or 0)
    if not iid:
        return jsonify({"ok": False, "error": "id 필요"}), 400
    return jsonify(rename_collection_item(
        g.user["id"], iid,
        name=(d.get("name") or "").strip(),
        notes=d.get("notes"),
    ))


@app.route("/api/strategy/markers")
@require_auth
def api_strategy_markers():
    """활성 전략 마커 + 다중 전략 consensus + 시장 필터 + PSR/DSR (B10)."""
    try:
        from engine.strategy.active_store import list_active, mark_signal
        from engine.strategy import run_backtest
        from engine.strategy.signal_filter import (
            consensus_signals, apply_market_filters, filter_for_alert,
        )
        from engine.strategy.vbt_runner import _fetch_ohlcv
        tk = (request.args.get("ticker") or "").strip().upper()
        if not tk:
            return jsonify({"markers": [], "strategies": [],
                            "confirmed": []})
        period = int(request.args.get("period_days") or 365)
        # B10: 사용자 조정 가능 임계 (URL 쿼리)
        min_conf = float(request.args.get("min_confidence") or 0.65)
        min_conf = max(0.0, min(0.99, min_conf))
        min_agree = int(request.args.get("min_agree") or 2)
        min_agree = max(1, min(5, min_agree))

        active = list_active(g.user["id"], ticker=tk)
        all_markers = []
        applied = []
        psr_data: Dict[str, float] = {}
        dsr_data: Dict[str, float] = {}
        for s in active:
            r = run_backtest(tk, s["strategy"], params=s["params"],
                              period_days=period)
            if not r.get("ok"):
                continue
            entries = r.get("entry_signals") or []
            exits = r.get("exit_signals") or []
            for ts in entries:
                all_markers.append({"ts": ts, "kind": "buy",
                                    "strategy": s["strategy"]})
            for ts in exits:
                all_markers.append({"ts": ts, "kind": "sell",
                                    "strategy": s["strategy"]})
            last = (entries + exits)
            if last:
                last_ts = max(last)
                last_kind = ("buy" if last_ts in entries else "sell")
                mark_signal(s["id"], last_ts, last_kind)
            # B10: 전략별 PSR/DSR 수집 (signal_filter에 전달)
            mx = r.get("metrics", {})
            psr_data[s["strategy"]] = float(mx.get("psr", 0.5))
            dsr_data[s["strategy"]] = float(mx.get("dsr", 0.5))
            applied.append({
                "id": s["id"], "strategy": s["strategy"],
                "params": s["params"],
                "n_entries": len(entries), "n_exits": len(exits),
                "sharpe": mx.get("sharpe", 0),
                "alpha": mx.get("alpha", 0),
                "psr": mx.get("psr", 0.5),
                "dsr": mx.get("dsr", 0.5),
            })

        # 다중 전략 consensus + 시장 필터 + PSR/DSR 가중
        consensus = consensus_signals(all_markers, min_agree=min_agree)
        confirmed = []
        try:
            ohlcv = _fetch_ohlcv(tk, period)
            consensus = apply_market_filters(consensus, ohlcv,
                                              psr_data=psr_data,
                                              dsr_data=dsr_data)
            confirmed = filter_for_alert(consensus, min_confidence=min_conf)
        except Exception:
            pass

        return jsonify({
            "markers": all_markers,
            "strategies": applied,
            "consensus": consensus,
            "confirmed": confirmed,
            "ticker": tk,
            "filters": {
                "min_confidence": min_conf,
                "min_agree": min_agree,
            },
        })
    except Exception as e:
        return jsonify({"error": str(e), "markers": []}), 500


@app.route("/api/strategy/regime")
@require_auth
def api_strategy_regime():
    """현재 종목의 시장 regime + 추천 전략."""
    try:
        from engine.strategy.regime_auto import recommend
        tk = (request.args.get("ticker") or "").strip().upper()
        period = int(request.args.get("period_days") or 365)
        if not tk:
            return jsonify({"ok": False, "error": "ticker 필요"}), 400
        return jsonify(recommend(tk, period_days=period))
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/strategy/mega_grid", methods=["POST"])
@require_auth
def api_strategy_mega_grid():
    """모든 전략 × 모든 파라미터 메가 grid (5-15분 가능)."""
    try:
        from engine.strategy import run_mega_grid
        d = request.get_json(force=True, silent=True) or {}
        ticker = (d.get("ticker") or "").upper()
        if not ticker:
            return jsonify({"ok": False,
                            "error": "ticker 필요"}), 400
        return jsonify(run_mega_grid(
            ticker=ticker,
            period_days=int(d.get("period_days") or 365),
            density=int(d.get("density") or 3),
            top_n=int(d.get("top_n") or 15),
            rank_by=d.get("rank_by") or "alpha",
        ))
    except Exception as e:
        return jsonify({"ok": False,
                        "error": f"{type(e).__name__}: {e}"}), 500


# ── API: 포트폴리오 (사용자별 보유 종목 추적) ────────────────────
@app.route("/api/portfolio")
@require_auth
def api_portfolio_list():
    """현재 사용자 보유 종목 + 실시간 시세 + 손익."""
    from engine.portfolio.holdings_store import list_holdings
    holdings = list_holdings(g.user["id"])
    total_cost = 0.0
    total_value = 0.0
    enriched = []
    for h in holdings:
        ticker = h["ticker"]
        qty = float(h["quantity"])
        avg = float(h["avg_cost"])
        cost = qty * avg
        cur_price = None
        try:
            q = _quote_one(ticker)
            cur_price = q.get("price")
        except Exception:
            pass
        value = (cur_price or avg) * qty
        pnl = value - cost
        pnl_pct = (pnl / cost * 100) if cost else 0.0
        total_cost += cost
        total_value += value
        enriched.append({
            "id": h["id"],
            "ticker": ticker,
            "quantity": qty,
            "avg_cost": avg,
            "currency": h.get("currency") or "USD",
            "note": h.get("note") or "",
            "current_price": cur_price,
            "cost_basis": round(cost, 2),
            "market_value": round(value, 2),
            "pnl": round(pnl, 2),
            "pnl_pct": round(pnl_pct, 2),
        })
    total_pnl = total_value - total_cost
    total_pnl_pct = (total_pnl / total_cost * 100) if total_cost else 0.0
    return jsonify({
        "holdings": enriched,
        "summary": {
            "count": len(enriched),
            "total_cost": round(total_cost, 2),
            "total_value": round(total_value, 2),
            "total_pnl": round(total_pnl, 2),
            "total_pnl_pct": round(total_pnl_pct, 2),
        },
    })


@app.route("/api/portfolio/add", methods=["POST"])
@require_auth
def api_portfolio_add():
    from engine.portfolio.holdings_store import add_holding
    d = request.get_json(force=True, silent=True) or {}
    return jsonify(add_holding(
        g.user["id"],
        ticker=d.get("ticker", ""),
        quantity=d.get("quantity", 0),
        avg_cost=d.get("avg_cost", 0),
        currency=d.get("currency", "USD"),
        note=d.get("note", ""),
    ))


@app.route("/api/portfolio/update", methods=["POST"])
@require_auth
def api_portfolio_update():
    from engine.portfolio.holdings_store import update_holding
    d = request.get_json(force=True, silent=True) or {}
    hid = int(d.get("id") or 0)
    if not hid:
        return jsonify({"ok": False, "error": "id 필요"}), 400
    return jsonify(update_holding(
        g.user["id"], hid,
        quantity=d.get("quantity"),
        avg_cost=d.get("avg_cost"),
        note=d.get("note"),
    ))


@app.route("/api/portfolio/analyze")
@require_auth
def api_portfolio_analyze():
    """보유 종목 전체 포괄 분석."""
    try:
        from engine.portfolio.holdings_store import list_holdings
        from engine.portfolio.portfolio_analyze import analyze
        period = int(request.args.get("period_days") or 365)
        holdings = list_holdings(g.user["id"])
        return jsonify(analyze(holdings, period_days=period))
    except Exception as e:
        return jsonify({"ok": False,
                        "error": f"{type(e).__name__}: {e}"}), 500


@app.route("/api/portfolio/delete", methods=["POST"])
@require_auth
def api_portfolio_delete():
    from engine.portfolio.holdings_store import delete_holding
    d = request.get_json(force=True, silent=True) or {}
    hid = int(d.get("id") or 0)
    if not hid:
        return jsonify({"ok": False, "error": "id 필요"}), 400
    return jsonify(delete_holding(g.user["id"], hid))


# ── API: Claude 에이전트 채팅 (인증 + 10회/일) ──────────────────
def _gather_chat_context() -> Dict[str, Any]:
    """현재 시장/속보 상태를 chat 컨텍스트로 수집 (시스템 프롬프트용)."""
    ctx: Dict[str, Any] = {}
    try:
        # 시장 개요 (가벼움)
        items = []
        for it in OVERVIEW_SYMBOLS[:6]:
            q = _quote_one(it["sym"])
            items.append({"name": it["name"], "price": q.get("price"),
                          "pct": q.get("pct", 0)})
        ctx["market_overview"] = items
    except Exception:
        pass
    try:
        from engine.awareness.alert_engine import get_alert_summary
        sm = get_alert_summary()
        ctx["alerts"] = sm.get("alerts") or {}
    except Exception:
        pass
    return ctx


@app.route("/api/claude/quota")
@require_auth
def api_claude_quota():
    from engine.auth import check_claude_quota
    return jsonify(check_claude_quota(g.user["id"], limit=10))


@app.route("/api/claude/chats")
@require_auth
def api_claude_chat_list():
    from engine.llm.chat_store import list_chats
    return jsonify({"chats": list_chats(g.user["id"])})


@app.route("/api/claude/chats/<chat_id>")
@require_auth
def api_claude_chat_get(chat_id):
    from engine.llm.chat_store import load_chat
    data = load_chat(g.user["id"], chat_id)
    if not data:
        return jsonify({"error": "대화를 찾을 수 없습니다."}), 404
    return jsonify(data)


@app.route("/api/claude/chats/<chat_id>", methods=["DELETE"])
@require_auth
def api_claude_chat_delete(chat_id):
    from engine.llm.chat_store import delete_chat
    ok = delete_chat(g.user["id"], chat_id)
    return jsonify({"ok": ok})


@app.route("/api/claude/chat", methods=["POST"])
@require_auth
def api_claude_chat():
    """
    Claude 에이전트에게 질문.

    Body: {message, chat_id?, ticker?}
      chat_id 없으면 새 대화 생성.
      ticker는 현재 사용자가 보고 있는 종목 (컨텍스트 주입용).

    인증 필요 + 10회/일 쿼터 소비 (성공 시에만).
    """
    from engine.auth import consume_claude_quota, check_claude_quota
    from engine.llm.claude_client import chat as claude_chat
    from engine.llm.chat_store import (create_chat, load_chat,
                                        append_message)
    data = request.get_json(force=True, silent=True) or {}
    message = (data.get("message") or "").strip()
    chat_id = (data.get("chat_id") or "").strip()
    ticker = (data.get("ticker") or "").strip().upper()
    if not message:
        return jsonify({"ok": False, "error": "message 필요"}), 400
    if len(message) > 4000:
        return jsonify({"ok": False,
                        "error": "메시지가 너무 깁니다 (4000자 제한)."}), 400

    # 쿼터 사전 체크 (소비는 호출 성공 후)
    q = check_claude_quota(g.user["id"], limit=10)
    if q["remaining"] <= 0:
        return jsonify({"ok": False,
                        "error": "오늘 사용량 한도(10회) 도달",
                        "quota": q}), 429

    # 대화 로드 또는 생성
    if chat_id:
        cur = load_chat(g.user["id"], chat_id)
        if not cur:
            return jsonify({"ok": False,
                            "error": "대화를 찾을 수 없습니다."}), 404
    else:
        cur = create_chat(g.user["id"], first_message=message)
        chat_id = cur["id"]

    # user 메시지 저장
    append_message(g.user["id"], chat_id, "user", message)

    # 히스토리 구성 (최근 12 메시지)
    history = [{"role": m["role"], "content": m["content"]}
               for m in cur["messages"][-12:]]
    history.append({"role": "user", "content": message})

    # 컨텍스트 (시장/속보 + ticker)
    ctx = _gather_chat_context()
    if ticker:
        ctx["ticker"] = ticker

    # Claude 호출
    r = claude_chat(history, context=ctx)
    if not r.get("ok"):
        return jsonify({"ok": False, "error": r.get("error"),
                        "chat_id": chat_id}), 502

    # 쿼터 소비 (성공 시에만)
    quota_after = consume_claude_quota(g.user["id"], limit=10)

    # assistant 응답 저장
    saved = append_message(g.user["id"], chat_id,
                           "assistant", r["text"])
    return jsonify({
        "ok": True,
        "chat_id": chat_id,
        "title": saved["title"] if saved else "",
        "reply": r["text"],
        "model": r.get("model"),
        "elapsed_sec": r.get("elapsed_sec"),
        "usage": r.get("usage"),
        "quota": quota_after,
    })


# ── C12: 실시간 SSE (Server-Sent Events) ────────────────────────
@app.route("/api/stream")
def api_stream():
    """SSE 엔드포인트 — EventBus 구독 후 메시지 push.
    클라이언트: new EventSource('/api/stream')
    """
    import json as _json
    import queue as _queue
    from engine import eventbus

    @stream_with_context
    def gen():
        q = eventbus.subscribe()
        try:
            # 즉시 연결 확인 ping
            yield "event: ping\ndata: {\"ok\":true}\n\n"
            while True:
                try:
                    msg = q.get(timeout=15)
                    et = msg.get("type", "message")
                    data = _json.dumps(
                        {"data": msg.get("data"), "ts": msg.get("ts")},
                        ensure_ascii=False, default=str)
                    yield f"event: {et}\ndata: {data}\n\n"
                except _queue.Empty:
                    # 15초 idle → keepalive (proxy 끊김 방지)
                    yield ": ka\n\n"
        except GeneratorExit:
            eventbus.unsubscribe(q)

    return Response(gen(), mimetype="text/event-stream",
                    headers={
                        "Cache-Control": "no-cache, no-transform",
                        "X-Accel-Buffering": "no",
                        "Connection": "keep-alive",
                    })


@app.route("/api/stream/stats")
@require_auth
def api_stream_stats():
    from engine import eventbus
    return jsonify(eventbus.stats())


@app.route("/api/stream/test", methods=["POST"])
@require_admin
def api_stream_test():
    """어드민 수동 broadcast 테스트."""
    from engine import eventbus
    data = request.get_json(force=True, silent=True) or {}
    delivered = eventbus.publish(
        data.get("type", "test"),
        data.get("data", {"msg": "테스트 이벤트"}))
    return jsonify({"ok": True, "delivered": delivered})


# ── API: 외부 접근 (Cloudflare Tunnel) ────────────────────────────
@app.route("/api/cloud/status")
@require_auth
def api_cloud_status():
    from engine.cloud.tunnel import status
    from engine.cloud.pc_id import get_pc_id, get_pc_label
    s = status()
    s["pc_id"] = get_pc_id()
    s["pc_label"] = get_pc_label()
    return jsonify(s)


@app.route("/api/cloud/install", methods=["POST"])
@require_auth
def api_cloud_install():
    from engine.cloud.tunnel import install_async
    return jsonify(install_async())


@app.route("/api/cloud/start_quick", methods=["POST"])
@require_auth
def api_cloud_start_quick():
    """Quick Tunnel 시작 + 메인 PC로 지정."""
    from engine.cloud.tunnel import start_quick, status
    from engine.cloud.pc_id import get_pc_id, get_pc_label
    from engine.auth import set_main_pc
    r = start_quick(local_port=int(os.environ.get("JIQT_PORT", "8765")))
    # 사용자의 메인 PC로 등록
    try:
        set_main_pc(g.user["id"], get_pc_id(), get_pc_label())
    except Exception:
        pass
    return jsonify(r)


@app.route("/api/cloud/stop", methods=["POST"])
@require_auth
def api_cloud_stop():
    from engine.cloud.tunnel import stop_quick
    return jsonify(stop_quick())


@app.route("/api/cloud/healthcheck")
@require_auth
def api_cloud_healthcheck():
    """Tunnel URL이 외부에서 접근 가능한지 검증."""
    from engine.cloud.tunnel import health_check
    return jsonify(health_check())


@app.route("/api/cloud/restart", methods=["POST"])
@require_auth
def api_cloud_restart():
    """Tunnel 죽었으면 강제 재시작."""
    from engine.cloud.tunnel import restart_quick
    return jsonify(restart_quick())


# ── C11: 정식 Tunnel 자동화 (Cloudflare API) ─────────────────────
@app.route("/api/cloud/cf/verify", methods=["POST"])
@require_admin
def api_cf_verify():
    """API 토큰 검증 + 계정/Zone 목록."""
    from engine.cloud import cf_api
    data = request.get_json(force=True, silent=True) or {}
    token = (data.get("token") or "").strip()
    v = cf_api.verify_token(token)
    if not v.get("ok"):
        return jsonify(v), 400
    accounts = cf_api.list_accounts(token)
    zones = cf_api.list_zones(token)
    return jsonify({
        "ok": True,
        "status": v.get("status"),
        "accounts": accounts,
        "zones": zones,
    })


@app.route("/api/cloud/cf/setup", methods=["POST"])
@require_admin
def api_cf_setup():
    """tunnel 생성 + DNS 라우트 + 로컬 파일 저장 (한 번에)."""
    from engine.cloud.named_tunnel import setup
    data = request.get_json(force=True, silent=True) or {}
    return jsonify(setup(
        token=(data.get("token") or "").strip(),
        account_id=(data.get("account_id") or "").strip(),
        zone_id=(data.get("zone_id") or "").strip(),
        hostname=(data.get("hostname") or "").strip(),
        tunnel_name=(data.get("tunnel_name") or "jiqt-tunnel").strip(),
        local_port=int(data.get("local_port") or 8765),
    ))


@app.route("/api/cloud/cf/status")
@require_auth
def api_cf_status():
    from engine.cloud.named_tunnel import status
    return jsonify(status())


@app.route("/api/cloud/cf/start", methods=["POST"])
@require_admin
def api_cf_start():
    from engine.cloud.named_tunnel import start_named
    return jsonify(start_named())


@app.route("/api/cloud/cf/stop", methods=["POST"])
@require_admin
def api_cf_stop():
    from engine.cloud.named_tunnel import stop_named
    return jsonify(stop_named())


# ── 벡터화 Grid + 배치 전략 (vectorbt 본래 강점 활용) ──────────
@app.route("/api/strategy/vec_grid", methods=["POST"])
@require_auth
def api_strategy_vec_grid():
    """단일 전략 벡터화 grid — 100~500x 빠름."""
    try:
        from engine.strategy.vbt_vectorized import vectorized_grid
        d = request.get_json(force=True, silent=True) or {}
        return jsonify(vectorized_grid(
            strategy=d.get("strategy") or "sma_cross",
            ticker=(d.get("ticker") or "").upper(),
            grid=d.get("grid") or {},
            period_days=int(d.get("period_days") or 730),
            interval=(d.get("interval") or "1d"),
            fees=float(d.get("fees") or 0.001),
            init_cash=float(d.get("init_cash") or 10000),
            top_n=int(d.get("top_n") or 10),
        ))
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/strategy/batch_grid", methods=["POST"])
@require_auth
def api_strategy_batch_grid():
    """
    여러 전략을 동시 grid search (사용자가 선택한 전략들만).
    body: {ticker, period_days, interval, strategies:[...], grids:{...}, top_n_per}
    """
    try:
        from engine.strategy.vbt_vectorized import batch_grid
        d = request.get_json(force=True, silent=True) or {}
        return jsonify(batch_grid(
            strategies=d.get("strategies") or ["sma_cross", "rsi_mr"],
            grids=d.get("grids") or {},
            ticker=(d.get("ticker") or "").upper(),
            period_days=int(d.get("period_days") or 730),
            interval=(d.get("interval") or "1d"),
            fees=float(d.get("fees") or 0.001),
            init_cash=float(d.get("init_cash") or 10000),
            top_n_per=int(d.get("top_n_per") or 5),
        ))
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/strategy/multi_asset", methods=["POST"])
@require_auth
def api_strategy_multi_asset():
    """N종목 × M전략 동시 백테스트 (vectorbt cross-sectional)."""
    try:
        from engine.strategy.multi_asset import multi_asset_backtest
        d = request.get_json(force=True, silent=True) or {}
        return jsonify(multi_asset_backtest(
            tickers=d.get("tickers") or [],
            strategies=d.get("strategies") or ["sma_cross"],
            period_days=int(d.get("period_days") or 730),
            interval=(d.get("interval") or "1d"),
            fees=float(d.get("fees") or 0.001),
            init_cash=float(d.get("init_cash") or 10000),
            strategy_params=d.get("strategy_params") or {},
        ))
    except Exception as e:
        import traceback
        return jsonify({"ok": False, "error": str(e),
                        "trace": traceback.format_exc()[-600:]}), 500


@app.route("/api/strategy/multi_asset/supported")
@require_auth
def api_strategy_multi_asset_supported():
    try:
        from engine.strategy.multi_asset import MULTI_ASSET_SUPPORTED
        return jsonify({"supported": MULTI_ASSET_SUPPORTED})
    except Exception as e:
        return jsonify({"supported": [], "error": str(e)})


@app.route("/api/strategy/vectorizable")
@require_auth
def api_strategy_vectorizable():
    """벡터화 가능 여부 + 메타 (UI에서 strategies meta와 함께 사용)."""
    try:
        from engine.strategy.vbt_vectorized import (
            VECTORIZABLE, _PARAM_NAMES,
        )
        return jsonify({
            "vectorizable": list(VECTORIZABLE),
            "param_names": _PARAM_NAMES,
        })
    except Exception as e:
        return jsonify({"vectorizable": [], "error": str(e)})


# ── Tear Sheet PDF 생성 (Tier 2 #10) ─────────────────────────
@app.route("/api/tearsheet/strategy", methods=["POST"])
@require_auth
def api_tearsheet_strategy():
    """body: backtest result dict 그대로 → PDF 다운로드"""
    try:
        from engine.report.tearsheet import build_strategy_tearsheet
        d = request.get_json(force=True, silent=True) or {}
        bt_result = d.get("result")
        if not bt_result:
            # 결과 없으면 run_backtest 직접 호출
            from engine.strategy import run_backtest
            bt_result = run_backtest(
                ticker=(d.get("ticker") or "").upper(),
                strategy=d.get("strategy") or "sma_cross",
                params=d.get("params") or {},
                period_days=int(d.get("period_days") or 730),
                interval=(d.get("interval") or "1d"),
            )
        pdf_bytes = build_strategy_tearsheet(bt_result)
        if not pdf_bytes:
            return jsonify({"ok": False, "error": "PDF 생성 실패 (matplotlib 또는 결과 부족)"}), 500
        from flask import Response
        tk = bt_result.get("ticker", "STRATEGY")
        strat = bt_result.get("strategy", "")
        resp = Response(pdf_bytes, mimetype="application/pdf")
        resp.headers["Content-Disposition"] = (
            f'attachment; filename="{tk}_{strat}_tearsheet.pdf"')
        return resp
    except Exception as e:
        import traceback
        return jsonify({"ok": False, "error": str(e),
                        "trace": traceback.format_exc()[-400:]}), 500


@app.route("/api/tearsheet/paper", methods=["GET"])
@require_auth
def api_tearsheet_paper():
    """Paper Trading 이력 → PDF."""
    try:
        from engine.report.tearsheet import build_paper_tearsheet
        pdf_bytes = build_paper_tearsheet(g.user["id"])
        if not pdf_bytes:
            return jsonify({"ok": False, "error": "PDF 생성 실패"}), 500
        from flask import Response
        resp = Response(pdf_bytes, mimetype="application/pdf")
        resp.headers["Content-Disposition"] = (
            'attachment; filename="paper_trading_report.pdf"')
        return resp
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/tearsheet/kronos", methods=["POST"])
@require_auth
def api_tearsheet_kronos():
    """🌌 Kronos 예측 정확도 보고서 (walk-forward) → PDF."""
    try:
        from engine.report.tearsheet import build_kronos_tearsheet
        d = request.get_json(force=True, silent=True) or {}
        ticker = (d.get("ticker") or "AAPL").upper()
        pdf_bytes = build_kronos_tearsheet(
            ticker=ticker,
            model_key=(d.get("model_key") or "kronos_small"),
            period_days=int(d.get("period_days") or 730),
            slide_step=int(d.get("slide_step") or 20),
            lookback=int(d.get("lookback") or 128),
            pred_len=int(d.get("pred_len") or 5),
        )
        if not pdf_bytes:
            return jsonify({"ok": False,
                            "error": "PDF 생성 실패 — 모델 미설치 or 데이터 부족"}), 500
        from flask import Response
        resp = Response(pdf_bytes, mimetype="application/pdf")
        resp.headers["Content-Disposition"] = (
            f'attachment; filename="kronos_accuracy_{ticker}.pdf"')
        return resp
    except Exception as e:
        import traceback
        return jsonify({"ok": False, "error": str(e),
                        "trace": traceback.format_exc()[-500:]}), 500


# ── Paper Trading (모의 거래 — KIS 모의계좌 or in-memory) ─────
@app.route("/api/paper/state", methods=["GET"])
@require_auth
def api_paper_state():
    from engine.trading import get_paper_state
    return jsonify(get_paper_state(g.user["id"]))


@app.route("/api/paper/pnl", methods=["GET"])
@require_auth
def api_paper_pnl():
    from engine.trading import get_paper_pnl
    return jsonify(get_paper_pnl(g.user["id"]))


@app.route("/api/paper/orders", methods=["GET"])
@require_auth
def api_paper_orders():
    from engine.trading import get_paper_engine
    limit = int(request.args.get("limit") or 50)
    return jsonify({"ok": True,
                    "orders": get_paper_engine(g.user["id"]).get_orders(limit)})


@app.route("/api/paper/order", methods=["POST"])
@require_auth
def api_paper_order():
    """body: {ticker, side, qty, price?, source?, note?}"""
    from engine.trading import place_paper_order
    d = request.get_json(force=True, silent=True) or {}
    return jsonify(place_paper_order(
        g.user["id"],
        ticker=(d.get("ticker") or "").upper(),
        side=(d.get("side") or "").lower(),
        qty=int(d.get("qty") or 0),
        price=float(d["price"]) if d.get("price") not in (None, "") else None,
        source=(d.get("source") or "manual"),
        note=(d.get("note") or ""),
    ))


@app.route("/api/paper/reset", methods=["POST"])
@require_auth
def api_paper_reset():
    """전체 초기화 — 위험. body: {initial_cash?, confirm: true}"""
    from engine.trading import get_paper_engine
    d = request.get_json(force=True, silent=True) or {}
    if not d.get("confirm"):
        return jsonify({"ok": False, "error": "confirm=true 필요"}), 400
    ic = float(d.get("initial_cash") or 10_000_000)
    return jsonify(get_paper_engine(g.user["id"]).reset(initial_cash=ic))


@app.route("/api/paper/limits", methods=["GET"])
@require_auth
def api_paper_limits_get():
    from engine.trading.risk_limits import get_limits
    return jsonify({"ok": True, "limits": get_limits(g.user["id"])})


@app.route("/api/paper/limits", methods=["POST"])
@require_auth
def api_paper_limits_save():
    from engine.trading.risk_limits import save_limits
    d = request.get_json(force=True, silent=True) or {}
    return jsonify(save_limits(g.user["id"], d.get("limits") or {}))


@app.route("/api/paper/violations", methods=["GET"])
@require_auth
def api_paper_violations():
    from engine.trading.risk_limits import violations_report
    return jsonify(violations_report(g.user["id"]))


# ── Paper 자동매매 (컬렉션 전략 자동 실행) ────────────────────
@app.route("/api/paper/auto", methods=["GET"])
@require_auth
def api_paper_auto_get():
    from engine.trading.auto_trader import get_state
    return jsonify({"ok": True, "state": get_state(g.user["id"])})


@app.route("/api/paper/auto", methods=["POST"])
@require_auth
def api_paper_auto_save():
    """body: {enabled: bool, strategies: [id, ...], poll_sec?: int}"""
    from engine.trading.auto_trader import save_state
    d = request.get_json(force=True, silent=True) or {}
    return jsonify(save_state(
        g.user["id"],
        enabled=bool(d.get("enabled")),
        strategies=[int(x) for x in (d.get("strategies") or [])],
        poll_sec=int(d.get("poll_sec") or 60),
    ))


@app.route("/api/paper/auto/run", methods=["POST"])
@require_auth
def api_paper_auto_run():
    """1회 즉시 실행 (수동 트리거)."""
    from engine.trading.auto_trader import run_once
    return jsonify(run_once(g.user["id"]))


# 서버 부팅 시 자동매매 스케줄러 활성화 (모듈 import 시 자동)
try:
    from engine.trading import auto_trader as _at_init  # noqa
except Exception:
    pass

# 🌌 Kronos 자동매매 스케줄러 (별도 — 일봉 기준 긴 주기)
try:
    from engine.trading import kronos_auto as _ka_init
    _ka_init.start_scheduler()
except Exception:
    pass


@app.route("/api/paper/snapshot", methods=["POST"])
@require_auth
def api_paper_snapshot():
    from engine.trading import get_paper_engine
    return jsonify(get_paper_engine(g.user["id"]).snapshot_nav())


# ── 한국투자증권 (KIS) OpenAPI ────────────────────────────────
@app.route("/api/kis/keys", methods=["GET"])
@require_auth
def api_kis_keys_get():
    """저장된 키 정보 반환 (시크릿은 마스킹)."""
    from engine.data.sources.kis import load_keys
    keys = load_keys()
    safe = {}
    for mode in ("real", "vts"):
        k = keys.get(mode, {})
        if k.get("app_key"):
            safe[mode] = {
                "configured": True,
                "app_key_preview": k["app_key"][:8] + "..." + k["app_key"][-4:],
                "has_secret":  bool(k.get("app_secret")),
                "account_no":  k.get("account_no") or "",
            }
        else:
            safe[mode] = {"configured": False}
    return jsonify({"ok": True, "keys": safe})


@app.route("/api/kis/keys", methods=["POST"])
@require_auth
def api_kis_keys_save():
    """키 저장. body: {mode: 'real'|'vts', app_key, app_secret, account_no?}"""
    from engine.data.sources.kis import load_keys, save_keys
    d = request.get_json(force=True, silent=True) or {}
    mode = (d.get("mode") or "").strip()
    if mode not in ("real", "vts"):
        return jsonify({"ok": False, "error": "mode = real | vts"}), 400
    app_key = (d.get("app_key") or "").strip()
    app_secret = (d.get("app_secret") or "").strip()
    if not app_key or not app_secret:
        return jsonify({"ok": False, "error": "app_key/secret 필요"}), 400
    keys = load_keys()
    keys[mode] = {
        "app_key": app_key, "app_secret": app_secret,
        "account_no": (d.get("account_no") or "").strip(),
    }
    return jsonify(save_keys(keys))


@app.route("/api/kis/test", methods=["POST"])
@require_auth
def api_kis_test():
    """모드별 연결 테스트 — 토큰 발급 + 시세 호출."""
    from engine.data.sources.kis import test_connection
    d = request.get_json(force=True, silent=True) or {}
    mode = (d.get("mode") or "vts").strip()
    return jsonify(test_connection(mode=mode))


@app.route("/api/kis/quote", methods=["GET"])
@require_auth
def api_kis_quote():
    """ticker로 시세 조회. ?ticker=005930 (국내) or ?ticker=AAPL&market=us"""
    from engine.data.sources.kis import quote_kr, quote_us
    tk = (request.args.get("ticker") or "").strip()
    market = (request.args.get("market") or "kr").lower()
    mode = (request.args.get("mode") or "real").strip()
    if not tk:
        return jsonify({"ok": False, "error": "ticker 필요"}), 400
    if market == "us":
        return jsonify(quote_us(tk, mode=mode))
    return jsonify(quote_kr(tk, mode=mode))


@app.route("/api/kis/orderbook", methods=["GET"])
@require_auth
def api_kis_orderbook():
    """국내 호가 10단계 (실시간 1 스냅샷)."""
    from engine.data.sources.kis import orderbook_kr
    tk = (request.args.get("ticker") or "").strip()
    mode = (request.args.get("mode") or "real").strip()
    if not tk:
        return jsonify({"ok": False, "error": "ticker 필요"}), 400
    return jsonify(orderbook_kr(tk, mode=mode))


@app.route("/api/kis/balance", methods=["GET"])
@require_auth
def api_kis_balance():
    """계좌 잔고 (모의 권장). ?mode=vts|real"""
    from engine.data.sources.kis import account_balance
    mode = (request.args.get("mode") or "vts").strip()
    return jsonify(account_balance(mode=mode))


# ── KIS WebSocket 제어 (실시간 호가/체결) ─────────────────────
@app.route("/api/kis/ws/start", methods=["POST"])
@require_auth
def api_kis_ws_start():
    """body: {mode?: 'vts'|'real', tickers: ['005930', ...]}"""
    try:
        from engine.data.sources.kis_websocket import get_ws_client
        d = request.get_json(force=True, silent=True) or {}
        mode = (d.get("mode") or "vts").strip()
        tickers = d.get("tickers") or []
        cli = get_ws_client(mode=mode)
        r = cli.start()
        if not r.get("ok"):
            return jsonify(r), 400
        for tk in tickers:
            cli.subscribe_ticks(tk)
            cli.subscribe_orderbook(tk)
        return jsonify({"ok": True, "mode": mode,
                        "subscribed": list(cli.subscribed_ticks),
                        "status": cli.status()})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/kis/ws/stop", methods=["POST"])
@require_auth
def api_kis_ws_stop():
    try:
        from engine.data.sources.kis_websocket import get_ws_client
        for mode in ("vts", "real"):
            try:
                get_ws_client(mode=mode).stop()
            except Exception:
                pass
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/kis/ws/status")
@require_auth
def api_kis_ws_status():
    try:
        from engine.data.sources.kis_websocket import get_ws_client
        out = {}
        for mode in ("vts", "real"):
            try:
                out[mode] = get_ws_client(mode=mode).status()
            except Exception:
                out[mode] = {"running": False}
        return jsonify({"ok": True, "status": out})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ── 마이크로구조 분석 (틱/호가 기반) ──────────────────────────
@app.route("/api/micro/snapshot", methods=["GET"])
@require_auth
def api_micro_snapshot():
    """단일 호가 스냅샷 + imbalance 계산.
    KIS 호가 API 한 번 호출 — WebSocket 없이도 동작.
    """
    from engine.data.sources.kis import orderbook_kr, has_keys
    from engine.microstructure import book_imbalance
    tk = (request.args.get("ticker") or "").strip()
    if not tk:
        return jsonify({"ok": False, "error": "ticker 필요"}), 400
    if not (has_keys("vts") or has_keys("real")):
        return jsonify({"ok": False,
                        "error": "KIS 키 미설정 — 설정 모달 → 🏛 KIS 한국투자증권에서 등록"}), 400
    mode = "vts" if has_keys("vts") else "real"
    ob = orderbook_kr(tk, mode=mode)
    if not ob.get("ok"):
        return jsonify(ob), 400
    imb = book_imbalance({"bids": ob.get("bids", []),
                            "asks": ob.get("asks", [])})
    return jsonify({"ok": True, "ticker": tk,
                    "orderbook": ob, "imbalance": imb})


@app.route("/api/micro/analyze", methods=["GET"])
@require_auth
def api_micro_analyze():
    """WebSocket buffer에서 실시간 마이크로구조 분석 (5종)."""
    try:
        from engine.data.sources.kis_websocket import get_ws_client
        from engine.microstructure import (
            speed_of_tape, tape_acceleration, book_imbalance,
            pressure_score, detect_sweeps, real_cvd, cvd_divergence,
            trade_size_distribution,
        )
        tk = (request.args.get("ticker") or "").strip()
        mode = (request.args.get("mode") or "vts").strip()
        if not tk:
            return jsonify({"ok": False, "error": "ticker 필요"}), 400
        cli = get_ws_client(mode=mode)
        ticks = cli.get_recent_ticks(tk, n=500)
        books = cli.get_recent_books(tk, n=20)
        if not ticks and not books:
            return jsonify({"ok": False,
                "error": "버퍼 비어 있음 — WebSocket 시작 후 30초+ 누적 필요"}), 200
        out = {
            "ok": True, "ticker": tk,
            "n_ticks": len(ticks),
            "n_books": len(books),
        }
        if ticks:
            out["speed_5s"]      = speed_of_tape(ticks, 5.0)
            out["speed_30s"]     = speed_of_tape(ticks, 30.0)
            out["acceleration"]  = tape_acceleration(ticks, 5.0, 30.0)
            out["cvd"]           = real_cvd(ticks)
            out["cvd_div"]       = cvd_divergence(ticks)
            out["trade_cluster"] = trade_size_distribution(ticks)
            out["sweeps"]        = detect_sweeps(ticks)
        if books:
            out["pressure"]      = pressure_score(books)
            out["latest_book_imbalance"] = book_imbalance(books[-1])
        return jsonify(out)
    except Exception as e:
        import traceback
        return jsonify({"ok": False, "error": str(e),
                        "trace": traceback.format_exc()[-300:]}), 500


# ── 백그라운드 작업 관리자 (JobManager) ────────────────────────
@app.route("/api/jobs", methods=["GET"])
@require_auth
def api_jobs_list():
    from engine.jobs import list_jobs
    uid = g.user["id"]
    status = (request.args.get("status") or "").strip() or None
    limit = int(request.args.get("limit") or 50)
    return jsonify({"ok": True, "items": list_jobs(user_id=uid,
                                                       status_filter=status,
                                                       limit=limit)})


@app.route("/api/jobs/submit", methods=["POST"])
@require_auth
def api_jobs_submit():
    """body: {kind, payload, title?}"""
    try:
        from engine.jobs import submit_job
        d = request.get_json(force=True, silent=True) or {}
        kind = (d.get("kind") or "").strip()
        payload = d.get("payload") or {}
        title = d.get("title")
        uid = g.user["id"]
        job_id = submit_job(kind, payload, user_id=uid, title=title)
        return jsonify({"ok": True, "job_id": job_id})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/jobs/<job_id>", methods=["GET"])
@require_auth
def api_jobs_get(job_id):
    from engine.jobs import get_job
    job = get_job(job_id, user_id=g.user["id"])
    if not job:
        return jsonify({"ok": False, "error": "작업 없음"}), 404
    return jsonify({"ok": True, "job": job})


@app.route("/api/jobs/<job_id>/cancel", methods=["POST"])
@require_auth
def api_jobs_cancel(job_id):
    from engine.jobs import get_job, cancel_job
    job = get_job(job_id, user_id=g.user["id"])
    if not job:
        return jsonify({"ok": False, "error": "작업 없음 또는 권한 없음"}), 404
    return jsonify(cancel_job(job_id))


@app.route("/api/jobs/<job_id>/result", methods=["GET"])
@require_auth
def api_jobs_result(job_id):
    from engine.jobs import get_result, get_job
    job = get_job(job_id, user_id=g.user["id"])
    if not job:
        return jsonify({"ok": False, "error": "작업 없음"}), 404
    if job["status"] != "done":
        return jsonify({"ok": False,
                        "error": f"아직 종료 안 됨 ({job['status']})",
                        "job": job})
    return jsonify({"ok": True, "result": get_result(job_id, user_id=g.user["id"]),
                     "job": job})


# ── 다중 전략 포트폴리오 합성 (Tier 1 #1) ────────────────────
@app.route("/api/portfolio/combine", methods=["POST"])
@require_auth
def api_portfolio_combine():
    try:
        from engine.strategy.multi_strategy import combine_strategies
        d = request.get_json(force=True, silent=True) or {}
        return jsonify(combine_strategies(
            ticker=(d.get("ticker") or "").upper(),
            items=d.get("items") or [],
            period_days=int(d.get("period_days") or 730),
            interval=(d.get("interval") or "1d"),
            init_cash=float(d.get("init_cash") or 10000),
            fees=float(d.get("fees") or 0.001),
            normalize_weights=bool(d.get("normalize_weights", True)),
        ))
    except Exception as e:
        import traceback
        return jsonify({"ok": False, "error": str(e),
                        "trace": traceback.format_exc()[-400:]}), 500


# ── 사용자 prefs 영구 저장 (위젯 배치, 테마, 폰트 등) ──────
@app.route("/api/prefs", methods=["GET"])
@require_auth
def api_prefs_get():
    from engine.auth.prefs import get_prefs
    return jsonify({"ok": True, "prefs": get_prefs(g.user["id"])})


@app.route("/api/prefs", methods=["POST"])
@require_auth
def api_prefs_save():
    """전체 prefs 덮어쓰기 — body: {prefs: {...}}"""
    from engine.auth.prefs import save_prefs
    d = request.get_json(force=True, silent=True) or {}
    return jsonify(save_prefs(g.user["id"], d.get("prefs") or {}))


@app.route("/api/prefs/patch", methods=["POST"])
@require_auth
def api_prefs_patch():
    """부분 수정 — body: {patch: {key1:val1, key2:val2}}"""
    from engine.auth.prefs import patch_prefs
    d = request.get_json(force=True, silent=True) or {}
    return jsonify(patch_prefs(g.user["id"], d.get("patch") or {}))


# ── 뉴스 제목 자동 번역 (DeepL) — 뉴스탭 즉시 표시용 ───────
_translate_cache = {}  # 메모리 캐시 (재시작 시 사라짐)

@app.route("/api/news/translate", methods=["POST"])
@require_auth
def api_news_translate():
    try:
        d = request.get_json(force=True, silent=True) or {}
        text = (d.get("text") or "").strip()
        target = (d.get("target") or "KO").upper()
        if not text or len(text) > 500:
            return jsonify({"ok": False, "error": "text 없거나 너무 김"})
        cache_key = f"{target}::{text}"
        if cache_key in _translate_cache:
            return jsonify({"ok": True, "translated": _translate_cache[cache_key],
                            "cached": True})
        from engine.data.news_summary import _translate_deepl
        translated = _translate_deepl(text, target_lang=target)
        if not translated:
            return jsonify({"ok": False, "error": "DeepL 응답 없음"})
        _translate_cache[cache_key] = translated
        # 캐시 크기 제한 (메모리 보호)
        if len(_translate_cache) > 2000:
            for k in list(_translate_cache.keys())[:500]:
                _translate_cache.pop(k, None)
        return jsonify({"ok": True, "translated": translated})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ── Kronos foundation model (K-line forecasting) ─────────────
@app.route("/api/kronos/status")
@require_auth
def api_kronos_status():
    """Kronos 사용 가능 + 다운로드된 모델 목록."""
    from engine.strategy.kronos_predictor import get_status
    return jsonify(get_status())


@app.route("/api/kronos/install", methods=["POST"])
@require_auth
def api_kronos_install():
    """body: {models?: ['kronos_small', ...]} — 백그라운드 설치 시작."""
    from engine.strategy.kronos_installer import install_all_async
    d = request.get_json(force=True, silent=True) or {}
    return jsonify(install_all_async(models=d.get("models")))


@app.route("/api/kronos/install/model", methods=["POST"])
@require_auth
def api_kronos_install_model():
    """단일 모델 추가 다운로드. body: {model_key}"""
    from engine.strategy.kronos_installer import install_single_model_async
    d = request.get_json(force=True, silent=True) or {}
    return jsonify(install_single_model_async(d.get("model_key") or "kronos_small"))


@app.route("/api/kronos/install/status")
@require_auth
def api_kronos_install_status():
    """진행률 polling."""
    from engine.strategy.kronos_installer import get_status as _is
    return jsonify(_is())


@app.route("/api/kronos/predict", methods=["POST"])
@require_auth
def api_kronos_predict():
    """body: {ticker, model_key, period_days, interval, lookback, pred_len}"""
    try:
        from engine.strategy.kronos_predictor import kronos_predict
        from engine.strategy.vbt_runner import _fetch_ohlcv
        d = request.get_json(force=True, silent=True) or {}
        ticker = (d.get("ticker") or "").upper()
        if not ticker:
            return jsonify({"ok": False, "error": "ticker 필요"}), 400
        ohlcv = _fetch_ohlcv(ticker,
            period_days=int(d.get("period_days") or 730),
            interval=(d.get("interval") or "1d"))
        return jsonify(kronos_predict(
            ohlcv,
            model_key=(d.get("model_key") or "kronos_small"),
            lookback=int(d.get("lookback") or 256),
            pred_len=int(d.get("pred_len") or 5),
            temperature=float(d.get("temperature") or 1.0),
            top_p=float(d.get("top_p") or 0.9),
            sample_count=int(d.get("sample_count") or 1),
        ))
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ── 🌌 Kronos 자동매매 모드 ────────────────────────────────
@app.route("/api/kronos/auto/state")
@require_auth
def api_kronos_auto_state():
    from engine.trading.kronos_auto import get_state
    uid = (g.user or {}).get("id")
    if not uid:
        return jsonify({"ok": False, "error": "비로그인"}), 401
    return jsonify(get_state(uid))


@app.route("/api/kronos/auto/save", methods=["POST"])
@require_auth
def api_kronos_auto_save():
    from engine.trading.kronos_auto import save_state
    uid = (g.user or {}).get("id")
    if not uid:
        return jsonify({"ok": False, "error": "비로그인"}), 401
    d = request.get_json(force=True, silent=True) or {}
    try:
        r = save_state(uid,
            enabled=bool(d.get("enabled", False)),
            tickers=d.get("tickers") or [],
            model_key=(d.get("model_key") or "kronos_small"),
            buy_thresh=float(d.get("buy_thresh") or 0.02),
            sell_thresh=float(d.get("sell_thresh") or -0.02),
            position_size_pct=float(d.get("position_size_pct") or 0.10),
            poll_sec=int(d.get("poll_sec") or 3600),
            pred_len=int(d.get("pred_len") or 5))
        return jsonify(r)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/kronos/auto/run", methods=["POST"])
@require_auth
def api_kronos_auto_run():
    from engine.trading.kronos_auto import run_once
    uid = (g.user or {}).get("id")
    if not uid:
        return jsonify({"ok": False, "error": "비로그인"}), 401
    return jsonify(run_once(uid, force=True))


# ── D#10 GPU: ML 가격예측 (LSTM/GRU/Transformer/RF/XGB) ────
@app.route("/api/ml/device")
@require_auth
def api_ml_device():
    from engine.strategy.ml_predict import get_device_info
    return jsonify(get_device_info())


@app.route("/api/ml/predict", methods=["POST"])
@require_auth
def api_ml_predict():
    try:
        from engine.strategy.ml_predict import train_predict_model
        d = request.get_json(force=True, silent=True) or {}
        return jsonify(train_predict_model(
            ticker=(d.get("ticker") or "").upper(),
            model_type=(d.get("model_type") or "lstm").lower(),
            task=(d.get("task") or "regression").lower(),
            period_days=int(d.get("period_days") or 730),
            interval=(d.get("interval") or "1d"),
            horizon=int(d.get("horizon") or 5),
            seq_len=int(d.get("seq_len") or 30),
            epochs=int(d.get("epochs") or 20),
            test_pct=float(d.get("test_pct") or 0.2),
        ))
    except Exception as e:
        import traceback
        return jsonify({"ok": False, "error": str(e),
                        "trace": traceback.format_exc()[-400:]}), 500


@app.route("/api/ml/backtest", methods=["POST"])
@require_auth
def api_ml_backtest():
    try:
        from engine.strategy.ml_predict import ml_signal_backtest
        d = request.get_json(force=True, silent=True) or {}
        return jsonify(ml_signal_backtest(
            ticker=(d.get("ticker") or "").upper(),
            model_type=(d.get("model_type") or "lstm").lower(),
            task=(d.get("task") or "classification").lower(),
            period_days=int(d.get("period_days") or 730),
            interval=(d.get("interval") or "1d"),
            horizon=int(d.get("horizon") or 5),
            seq_len=int(d.get("seq_len") or 30),
            epochs=int(d.get("epochs") or 20),
            buy_thresh=float(d.get("buy_thresh") or 0.55),
            sell_thresh=float(d.get("sell_thresh") or 0.45),
            fees=float(d.get("fees") or 0.001),
            init_cash=float(d.get("init_cash") or 10000),
        ))
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ── D#10: vbt PRO — 전체 기능 노출 ────────────────────────────
@app.route("/api/vbt/indicators")
@require_auth
def api_vbt_indicators():
    from engine.strategy.vbt_pro import list_indicators
    return jsonify(list_indicators())


@app.route("/api/vbt/indicator/run", methods=["POST"])
@require_auth
def api_vbt_indicator_run():
    try:
        from engine.strategy.vbt_pro import run_indicator
        d = request.get_json(force=True, silent=True) or {}
        return jsonify(run_indicator(
            ticker=(d.get("ticker") or "").upper(),
            indicator=(d.get("indicator") or "MA").upper(),
            params=d.get("params") or {},
            period_days=int(d.get("period_days") or 365),
            interval=(d.get("interval") or "1d"),
        ))
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/vbt/from_orders", methods=["POST"])
@require_auth
def api_vbt_from_orders():
    try:
        from engine.strategy.vbt_pro import run_from_orders
        d = request.get_json(force=True, silent=True) or {}
        return jsonify(run_from_orders(
            ticker=(d.get("ticker") or "").upper(),
            orders=d.get("orders") or [],
            period_days=int(d.get("period_days") or 730),
            interval=(d.get("interval") or "1d"),
            init_cash=float(d.get("init_cash") or 10000),
            fees=float(d.get("fees") or 0.001),
            slippage=float(d.get("slippage") or 0.0001),
        ))
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/vbt/from_holding", methods=["POST"])
@require_auth
def api_vbt_from_holding():
    try:
        from engine.strategy.vbt_pro import run_from_holding
        d = request.get_json(force=True, silent=True) or {}
        return jsonify(run_from_holding(
            ticker=(d.get("ticker") or "").upper(),
            period_days=int(d.get("period_days") or 730),
            interval=(d.get("interval") or "1d"),
            init_cash=float(d.get("init_cash") or 10000),
            fees=float(d.get("fees") or 0.001),
        ))
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/vbt/records", methods=["POST"])
@require_auth
def api_vbt_records():
    try:
        from engine.strategy.vbt_pro import get_records
        d = request.get_json(force=True, silent=True) or {}
        return jsonify(get_records(
            ticker=(d.get("ticker") or "").upper(),
            strategy=(d.get("strategy") or "sma_cross"),
            params=d.get("params") or {},
            period_days=int(d.get("period_days") or 730),
            interval=(d.get("interval") or "1d"),
        ))
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ── P6: 리서치 확장 — correlation / sensitivity / SHAP ──────
@app.route("/api/research/strategy_corr", methods=["POST"])
@require_auth
def api_research_strategy_corr():
    try:
        from engine.strategy.research_ext import strategy_correlation_matrix
        d = request.get_json(force=True, silent=True) or {}
        return jsonify(strategy_correlation_matrix(
            ticker=(d.get("ticker") or "").upper(),
            strategies=d.get("strategies") or
                ["sma_cross","rsi_mr","macd","bb_revert","zscore_mr"],
            period_days=int(d.get("period_days") or 730),
            interval=(d.get("interval") or "1d"),
        ))
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/research/ticker_corr", methods=["POST"])
@require_auth
def api_research_ticker_corr():
    try:
        from engine.strategy.research_ext import ticker_correlation_matrix
        d = request.get_json(force=True, silent=True) or {}
        return jsonify(ticker_correlation_matrix(
            tickers=[t.upper() for t in (d.get("tickers") or [])],
            period_days=int(d.get("period_days") or 365),
            interval=(d.get("interval") or "1d"),
        ))
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/research/sensitivity", methods=["POST"])
@require_auth
def api_research_sensitivity():
    try:
        from engine.strategy.research_ext import param_sensitivity
        d = request.get_json(force=True, silent=True) or {}
        return jsonify(param_sensitivity(
            ticker=(d.get("ticker") or "").upper(),
            strategy=d.get("strategy") or "sma_cross",
            base_params=d.get("base_params") or {},
            param_name=d.get("param_name") or "",
            pct_range=float(d.get("pct_range") or 0.5),
            n_points=int(d.get("n_points") or 11),
            period_days=int(d.get("period_days") or 730),
            interval=(d.get("interval") or "1d"),
        ))
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/portfolio/optimize", methods=["POST"])
@require_auth
def api_portfolio_optimize():
    """Tier2 #8: Markowitz/HRP/BL/EW 방법별 최적 비중 + 백테스트 비교."""
    try:
        from engine.strategy.portfolio_optimizer import optimize_portfolio
        d = request.get_json(force=True, silent=True) or {}
        return jsonify(optimize_portfolio(
            tickers=[t.upper() for t in (d.get("tickers") or [])],
            period_days=int(d.get("period_days") or 365),
            interval=(d.get("interval") or "1d"),
            methods=d.get("methods") or ["mv", "hrp", "bl", "ew"],
            views=d.get("views"),
            init_cash=float(d.get("init_cash") or 10000),
        ))
    except Exception as e:
        import traceback
        return jsonify({"ok": False, "error": str(e),
                        "trace": traceback.format_exc()[-400:]}), 500


@app.route("/api/research/walk_forward", methods=["POST"])
@require_auth
def api_research_walk_forward():
    """Walk-Forward 일반화 — 모든 전략에 적용 가능."""
    try:
        from engine.strategy.walk_forward import walk_forward_strategy
        d = request.get_json(force=True, silent=True) or {}
        return jsonify(walk_forward_strategy(
            ticker=(d.get("ticker") or "").upper(),
            strategy=(d.get("strategy") or "sma_cross"),
            grid=d.get("grid") or {},
            period_days=int(d.get("period_days") or 730),
            interval=(d.get("interval") or "1d"),
            n_folds=int(d.get("n_folds") or 3),
            train_ratio=float(d.get("train_ratio") or 0.6),
        ))
    except Exception as e:
        import traceback
        return jsonify({"ok": False, "error": str(e),
                        "trace": traceback.format_exc()[-400:]}), 500


@app.route("/api/research/factor_attribution", methods=["POST"])
@require_auth
def api_research_factor_attribution():
    """Tier2 #5: Fama-French 팩터 귀속 — 알파 vs 팩터 노출 분해."""
    try:
        from engine.strategy.factor_attribution import factor_attribute_strategy
        d = request.get_json(force=True, silent=True) or {}
        return jsonify(factor_attribute_strategy(
            ticker=(d.get("ticker") or "").upper(),
            strategy=(d.get("strategy") or "sma_cross"),
            params=d.get("params") or {},
            period_days=int(d.get("period_days") or 730),
            interval=(d.get("interval") or "1d"),
            factor_model=(d.get("factor_model") or "3F").upper(),
        ))
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/research/shap", methods=["POST"])
@require_auth
def api_research_shap():
    try:
        from engine.strategy.research_ext import feature_importance_shap
        d = request.get_json(force=True, silent=True) or {}
        return jsonify(feature_importance_shap(
            ticker=(d.get("ticker") or "").upper(),
            period_days=int(d.get("period_days") or 730),
            interval=(d.get("interval") or "1d"),
            horizon=int(d.get("horizon") or 5),
        ))
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ── P4: AUTO 통합 분석 ────────────────────────────────────────
@app.route("/api/auto/run", methods=["POST"])
@require_auth
def api_auto_run():
    """
    1버튼 통합 분석: ANALYZE + Active + BATCH + MULTI + OF/PEAD + WFA
    body: {ticker, period_days?, interval?, watchlist_tickers?,
           run_wfa?, run_of_pead?, run_analyze?}
    응답: 통합 dict (steps, recommendation, score, grade)
    """
    try:
        from engine.strategy.auto_analyze import auto_analyze
        d = request.get_json(force=True, silent=True) or {}
        uid = (g.user or {}).get("id") if getattr(g, "user", None) else None
        result = auto_analyze(
            ticker=(d.get("ticker") or "").upper(),
            user_id=uid,
            period_days=int(d.get("period_days") or 730),
            interval=(d.get("interval") or "1d"),
            watchlist_tickers=d.get("watchlist_tickers") or [],
            run_wfa=bool(d.get("run_wfa", True)),
            run_of_pead=bool(d.get("run_of_pead", True)),
            run_analyze=bool(d.get("run_analyze", True)),
            run_ml=bool(d.get("run_ml", True)),
            run_kronos=bool(d.get("run_kronos", True)),
            kronos_model=(d.get("kronos_model") or "kronos_small"),
        )
        return jsonify(result)
    except Exception as e:
        import traceback
        return jsonify({"ok": False, "error": str(e),
                        "trace": traceback.format_exc()[-600:]}), 500


@app.route("/api/auto/report", methods=["POST"])
@require_auth
def api_auto_report():
    """
    auto/run 결과 dict 또는 ticker만 받아 → 통합 HTML 리포트 다운로드.
    body: {ticker?, result?}  (result가 없으면 새로 auto_analyze 실행)
    """
    try:
        from engine.strategy.auto_analyze import auto_analyze, build_html_report
        d = request.get_json(force=True, silent=True) or {}
        result = d.get("result")
        if not result:
            uid = (g.user or {}).get("id") if getattr(g, "user", None) else None
            result = auto_analyze(
                ticker=(d.get("ticker") or "").upper(),
                user_id=uid,
                period_days=int(d.get("period_days") or 730),
                interval=(d.get("interval") or "1d"),
                watchlist_tickers=d.get("watchlist_tickers") or [],
            )
        html = build_html_report(result)
        from flask import Response
        resp = Response(html, mimetype="text/html; charset=utf-8")
        tk = (result or {}).get("ticker", "AUTO")
        resp.headers["Content-Disposition"] = (
            f'attachment; filename="{tk}_auto_report.html"')
        return resp
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ── Orderflow + PEAD 엔진 API ────────────────────────────────────
@app.route("/api/of_pead/run", methods=["POST"])
@require_auth
def api_of_pead_run():
    """3개 전략 (또는 선택한 하나) 동시 백테스트 + 차트 base64."""
    try:
        from engine.orderflow_pead.main import build_bundle
        from engine.orderflow_pead import (
            OrderflowDeltaStrategy, PEADStrategy,
            Hybrid_OF_PEAD_Strategy, run_backtest,
            plot_equity, plot_signals, plot_delta_bubbles,
            plot_pead_events,
        )
        d = request.get_json(force=True, silent=True) or {}
        ticker = (d.get("ticker") or "AAPL").upper()
        period = int(d.get("period_days") or 730)
        interval = (d.get("interval") or "1d")
        which = (d.get("which") or "all").lower()  # all|of|pead|hybrid

        bundle = build_bundle(ticker, period_days=period, interval=interval)
        close = bundle["ohlcv"]["close"]
        out = {"ticker": ticker, "period_days": period, "interval": interval,
               "strategies": {}}

        def _run_one(label, strat, with_charts=True):
            res = run_backtest(strat, bundle)
            if with_charts:
                # equity 시리즈 복원
                eq_idx = [pd.Timestamp(p["d"]) for p in res["equity_curve"]]
                eq_vals = [p["v"] for p in res["equity_curve"]]
                eq = pd.Series(eq_vals, index=eq_idx)
                sig = strat.generate_signals(bundle)
                charts = {
                    "equity": plot_equity(eq, f"{strat.name} Equity"),
                    "signals": plot_signals(close,
                                             sig["entries"], sig["exits"],
                                             f"{strat.name} Signals"),
                }
                if "delta" in bundle["orderflow"].columns:
                    charts["delta_bubbles"] = plot_delta_bubbles(
                        close, bundle["orderflow"]["delta"],
                        f"{strat.name} Δ")
                if not bundle["earnings"].empty:
                    charts["pead_events"] = plot_pead_events(
                        close, bundle["earnings"],
                        drift_days=getattr(strat, "params", {})
                            .get("drift_days", 30),
                        title=f"{strat.name} Events")
                res["charts"] = charts
            out["strategies"][label] = res

        if which in ("all", "of"):
            avg_d = float(bundle["orderflow"]["delta"].abs().mean())
            _run_one("orderflow",
                     OrderflowDeltaStrategy(
                         delta_threshold=max(avg_d * 1.2, 100)))
        if which in ("all", "pead"):
            _run_one("pead", PEADStrategy(sue_top_pct=0.2, drift_days=30))
        if which in ("all", "hybrid"):
            _run_one("hybrid", Hybrid_OF_PEAD_Strategy())
        return jsonify(out)
    except Exception as e:
        import traceback
        return jsonify({"ok": False, "error": str(e),
                        "trace": traceback.format_exc()[-800:]}), 500


@app.route("/api/of_pead/optimize", methods=["POST"])
@require_auth
def api_of_pead_optimize():
    """Grid/WFA 최적화 — UI에서 surface plot용 데이터 + 결과 반환."""
    try:
        from engine.orderflow_pead.main import build_bundle
        from engine.orderflow_pead import (
            OrderflowDeltaStrategy, PEADStrategy,
            grid_search, walk_forward_optimization,
            plot_param_surface,
        )
        d = request.get_json(force=True, silent=True) or {}
        ticker = (d.get("ticker") or "AAPL").upper()
        period = int(d.get("period_days") or 730)
        which = (d.get("which") or "of").lower()
        bundle = build_bundle(ticker, period_days=period)
        if which == "pead":
            grid = {"sue_top_pct": [0.1, 0.2, 0.3],
                    "drift_days":  [10, 20, 30, 60]}
            gs = grid_search(PEADStrategy, grid, bundle, top_n=10)
            surf = plot_param_surface(
                gs["all"], "sue_top_pct", "drift_days", "sharpe",
                title="PEAD Sharpe Surface")
            wfa = walk_forward_optimization(
                PEADStrategy,
                {"sue_top_pct": [0.15, 0.25],
                 "drift_days":  [20, 40]},
                bundle, n_folds=3)
        else:
            grid = {"delta_threshold":    [200, 500, 1000, 2000],
                    "divergence_lookback":[5, 10, 20],
                    "divergence_z":       [1.0, 1.5, 2.0],
                    "max_hold_bars":      [10, 30, 60]}
            gs = grid_search(OrderflowDeltaStrategy, grid, bundle, top_n=10)
            surf = plot_param_surface(
                gs["all"], "delta_threshold", "max_hold_bars", "sharpe",
                title="OF Δ Sharpe Surface")
            wfa = walk_forward_optimization(
                OrderflowDeltaStrategy,
                {"delta_threshold": [300, 700, 1500],
                 "max_hold_bars":   [20, 40]},
                bundle, n_folds=3)
        return jsonify({
            "ok": True, "ticker": ticker, "which": which,
            "grid_top": gs["top"], "n_combos": gs.get("n_combos", 0),
            "surface_plotly": surf, "wfa": wfa,
        })
    except Exception as e:
        import traceback
        return jsonify({"ok": False, "error": str(e),
                        "trace": traceback.format_exc()[-800:]}), 500


@app.route("/api/of_pead/research", methods=["POST"])
@require_auth
def api_of_pead_research():
    """리서치 — 시그널 품질 + drift + clustering."""
    try:
        from engine.orderflow_pead.main import build_bundle
        from engine.orderflow_pead import (
            OrderflowDeltaStrategy,
            signal_quality, drift_persistence, of_imbalance_clusters,
        )
        d = request.get_json(force=True, silent=True) or {}
        ticker = (d.get("ticker") or "AAPL").upper()
        period = int(d.get("period_days") or 730)
        bundle = build_bundle(ticker, period_days=period)
        strat = OrderflowDeltaStrategy()
        sig = strat.generate_signals(bundle)
        return jsonify({
            "ok": True, "ticker": ticker,
            "signal_quality": signal_quality(
                bundle["ohlcv"]["close"], sig["entries"]),
            "drift_persistence": drift_persistence(
                bundle["ohlcv"]["close"], bundle["earnings"]),
            "imbalance_clusters": of_imbalance_clusters(
                bundle["orderflow"], n_clusters=4),
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/cloud/cf/teardown", methods=["POST"])
@require_admin
def api_cf_teardown():
    """tunnel + DNS + 로컬 파일 전부 삭제."""
    from engine.cloud.named_tunnel import teardown
    return jsonify(teardown())


# ── API: Awareness Layer (속보 자산 알림) ───────────────────────
@app.route("/api/awareness/summary")
def api_awareness_summary():
    """상단 스트립 배지용 — 자산별 alert 카운트 + top priority."""
    try:
        from engine.awareness.alert_engine import get_alert_summary
        return jsonify(get_alert_summary())
    except Exception as e:
        return jsonify({"error": str(e), "alerts": {}}), 500


@app.route("/api/awareness/asset")
def api_awareness_asset():
    """특정 자산의 alert 상세 리스트 (drawer 표시용)."""
    try:
        from engine.awareness.alert_engine import get_asset_alerts
        asset = (request.args.get("asset") or "").strip()
        limit = int(request.args.get("limit") or 15)
        if not asset:
            return jsonify({"error": "asset 누락"}), 400
        return jsonify(get_asset_alerts(asset, limit=limit))
    except Exception as e:
        return jsonify({"error": str(e), "items": []}), 500


@app.route("/api/awareness/all")
def api_awareness_all():
    """전체 알림 시간순 (속보 위젯용). 자산 무관."""
    try:
        from engine.awareness.alert_engine import get_all_alerts
        limit = int(request.args.get("limit") or 60)
        hi = request.args.get("high_impact_only") in ("1", "true")
        return jsonify(get_all_alerts(limit=limit, only_high_impact=hi))
    except Exception as e:
        return jsonify({"error": str(e), "items": []}), 500


@app.route("/api/awareness/history")
def api_awareness_history():
    """high-impact 알림 영구 히스토리 (최근 30일 기본)."""
    try:
        from engine.awareness.history import list_history, stats
        days = int(request.args.get("days") or 30)
        limit = int(request.args.get("limit") or 100)
        only_high = request.args.get("only_high") in ("1", "true")
        asset = (request.args.get("asset") or "").strip() or None
        items = list_history(limit=limit, days=days,
                             only_high=only_high, asset=asset)
        return jsonify({
            "items": items,
            "count": len(items),
            "stats": stats(),
        })
    except Exception as e:
        return jsonify({"error": str(e), "items": []}), 500


@app.route("/api/awareness/refresh", methods=["POST"])
def api_awareness_refresh():
    """수동 갱신 트리거 (백그라운드 폴링과 별도)."""
    try:
        from engine.awareness.alert_engine import refresh_once
        return jsonify(refresh_once())
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/news/countries")
def api_news_countries():
    """뉴스 패널 국가 탭 메뉴."""
    from engine.data.news_feeds import available_countries
    return jsonify({"countries": available_countries()})


@app.route("/api/news/by_country")
def api_news_by_country():
    """국가별 뉴스 — KR/US/JP/CN/EU."""
    country = (request.args.get("country") or "US").strip().upper()
    from engine.data.news_feeds import fetch_country_news
    r = fetch_country_news(country, limit_per_source=8, total_limit=20)
    return jsonify({
        "live": bool(r["items"]),
        "country": r["country"],
        "country_label": r["country_label"],
        "items": r["items"],
        "sources": r["sources"],
        "live_sources": r["live_sources"],
    })


@app.route("/api/news")
def api_news():
    ticker = (request.args.get("ticker") or "").strip()
    news: List[Dict[str, str]] = []
    yf = _get_yf()
    if yf and ticker:
        try:
            for n in (yf.Ticker(ticker).news or [])[:10]:
                c = n.get("content", n)
                title = c.get("title") or n.get("title")
                link = (c.get("clickThroughUrl") or {}).get("url") \
                    or n.get("link") or ""
                if title:
                    news.append({"title": title, "link": link,
                                 "pub": "", "src": "Yahoo"})
        except Exception:
            pass
    if len(news) < 6:
        feeds = [
            "https://feeds.finance.yahoo.com/rss/2.0/headline"
            "?s=^GSPC&region=US&lang=en-US",
            "https://feeds.finance.yahoo.com/rss/2.0/headline"
            "?s=%s&region=US&lang=en-US" % (ticker or "AAPL"),
        ]
        for f in feeds:
            for it in _fetch_rss(f, 10):
                news.append({**it, "src": "Yahoo RSS"})
    # 중복 제거
    seen, uniq = set(), []
    for n in news:
        k = n["title"][:60]
        if k in seen:
            continue
        seen.add(k)
        uniq.append(n)
    live = bool(uniq)
    if not uniq:  # 오프라인 데모 뉴스
        uniq = [{"title": "[데모] 인터넷 연결 시 실시간 속보가 표시됩니다",
                 "link": "", "pub": "", "src": "DEMO"}]
    return jsonify({"live": live, "items": uniq[:15]})


# ── API: 종목 분석 / 포트폴리오 (엔진 연동) ──────────────────────
_ANALYZE_JOBS: Dict[str, Dict[str, Any]] = {}


def _run_analyze_job(job_id: str, ticker: str, user_id=None):
    try:
        from main import analyze
        offline = not bool(_get_yf())
        use_synth = offline or ticker.upper() in ("DEMO", "TEST")
        res = analyze(
            ticker if not use_synth else "DEMO",
            start="1990-01-01",
            use_synthetic=use_synth,
            ml_model="rf", regime_method="kmeans",
            out_dir=_REPORTS,
        )
        inst = res.get("institutional", {})
        sc = inst.get("scorecard", {})
        nv = inst.get("narratives", {})
        meta = inst.get("meta", {})
        explanation = inst.get("explanation", {})
        precision = inst.get("precision", {})
        html_path = res.get("report_paths", {}).get("html", "")
        report_url = ""
        archive_url = ""
        if html_path and os.path.exists(html_path):
            report_url = "/report/" + os.path.basename(html_path)
            # C9: 타임스탬프 아카이브 — 영구 이력 보존
            try:
                import shutil
                base = os.path.basename(html_path)  # 예: AAPL_report.html
                name_root, ext = os.path.splitext(base)
                ts_str = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
                arch_name = f"{name_root}_{ts_str}{ext}"
                arch_path = os.path.join(_REPORTS, arch_name)
                shutil.copy2(html_path, arch_path)
                archive_url = "/report/" + arch_name
            except Exception:
                pass
        tfs = {}
        for k, v in res.get("timeframes", {}).items():
            tfs[k] = {
                "signal": v.get("signal"),
                "score": v.get("score"),
                "ret": v.get("momentum", {}).get("cum_return_pct"),
            }
        # meta_verdict: signal_engine 판정 요약 (XAI 레이어)
        meta_verdict = {}
        if meta:
            vd = meta.get("verdict", {})
            rs = meta.get("resolved", {})
            meta_verdict = {
                "signal":         vd.get("signal"),
                "score":          vd.get("score"),
                "grade":          vd.get("grade"),
                "verdict":        vd.get("verdict"),
                "n_vetoes":       vd.get("n_vetoes", 0),
                "conflict_ratio": vd.get("conflict_ratio", 0.0),
                "n_evidence":     meta.get("n_evidence", 0),
                "stance":         rs.get("stance"),
                "trace_text":     explanation.get("trace_text", ""),
                "headline":       explanation.get("headline", ""),
                "risk_grade":     explanation.get("risk_chain", {}).get("risk_grade"),
                "risk_summary":   explanation.get("risk_chain", {}).get("summary", ""),
                "dsr":            (precision.get("dsr") or {}).get("dsr"),
                "psr":            (precision.get("psr") or {}).get("psr"),
                "robust":         precision.get("robust", True),
                "robustness_flags": precision.get("robustness_flags", []),
            }
        job_result = {
            "status": "done",
            "ticker": ticker,
            "live": not use_synth,
            "overall_signal": res.get("overall_signal"),
            "overall_score": res.get("overall_score"),
            "grade": sc.get("overall_grade"),
            "grade_score": sc.get("overall_score"),
            "verdict": sc.get("verdict"),
            "axes": sc.get("pillars", {}),
            "timeframes": tfs,
            "narratives": {k: v for k, v in nv.items() if v},
            "report_url": report_url,
            "report_archive_url": archive_url,
            "meta_verdict": meta_verdict,
        }
        _ANALYZE_JOBS[job_id] = job_result
        # C9: 분석 이력 영구 저장 (실패해도 무시)
        try:
            from engine.analyze_history import save_analysis
            # 아카이브 URL을 우선 저장 (덮어쓰기 안 됨)
            persist = dict(job_result)
            if archive_url:
                persist["report_url"] = archive_url
            save_analysis(user_id, ticker, persist)
        except Exception as _e:
            print("[analyze_history] save failed:", _e)
    except Exception as e:  # pragma: no cover
        _ANALYZE_JOBS[job_id] = {"status": "error", "error": str(e)}


@app.route("/api/analyze", methods=["POST"])
def api_analyze():
    data = request.get_json(force=True, silent=True) or {}
    ticker = (data.get("ticker") or "DEMO").strip()
    job_id = "job_%d" % int(time.time() * 1000)
    _ANALYZE_JOBS[job_id] = {"status": "running", "ticker": ticker}
    # C9: 사용자 id 전달 (이력에 기록)
    uid = (g.user or {}).get("id") if getattr(g, "user", None) else None
    threading.Thread(target=_run_analyze_job,
                     args=(job_id, ticker, uid), daemon=True).start()
    return jsonify({"job_id": job_id, "status": "running"})


# C9: 분석 이력 조회 API
@app.route("/api/analyze/history")
@require_auth
def api_analyze_history():
    from engine.analyze_history import list_history, history_stats
    ticker = (request.args.get("ticker") or "").strip().upper() or None
    limit = int(request.args.get("limit") or 30)
    limit = max(1, min(100, limit))
    mine_only = request.args.get("mine") == "1"
    user_id = g.user["id"] if mine_only else None
    return jsonify({
        "items": list_history(ticker=ticker, user_id=user_id, limit=limit),
        "stats": history_stats(ticker=ticker),
        "ticker": ticker,
    })


@app.route("/api/analyze/history/<int:hid>")
@require_auth
def api_analyze_history_detail(hid):
    from engine.analyze_history import get_history_detail
    d = get_history_detail(hid)
    if not d:
        return jsonify({"error": "이력 없음"}), 404
    return jsonify(d)


@app.route("/api/analyze/history/<int:hid>", methods=["DELETE"])
@require_auth
def api_analyze_history_delete(hid):
    from engine.analyze_history import delete_history
    is_admin = (g.user.get("role") == "admin")
    r = delete_history(hid, g.user["id"], is_admin=is_admin)
    if not r.get("ok"):
        return jsonify(r), 403
    return jsonify(r)


@app.route("/api/analyze/<job_id>")
def api_analyze_status(job_id):
    job = _ANALYZE_JOBS.get(job_id)
    if not job:
        abort(404)
    return jsonify(job)


@app.route("/api/news/sentiment")
def api_news_sentiment():
    title = (request.args.get("title") or "").strip()
    body  = (request.args.get("body")  or "").strip()
    url   = (request.args.get("url")   or "").strip()
    if not title:
        return jsonify({"error": "title required"}), 400
    try:
        from engine.data.news_summary import analyze_news_full
        return jsonify(analyze_news_full(title, url=url, body=body))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/analyst")
def api_analyst():
    ticker = (request.args.get("ticker") or "").strip().upper()
    if not ticker:
        return jsonify({"error": "ticker required"}), 400
    try:
        from engine.data.analyst import get_analyst_targets
        data = get_analyst_targets(ticker)
        return jsonify(data)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/datasources")
def api_datasources():
    """가용 소스 + 키 마스킹 상태(키 값은 절대 노출 안 함)."""
    try:
        from engine.data.keyconfig import masked_status
        from engine.data.sources import available_sources
        return jsonify({
            "keys": masked_status(),
            "available": available_sources(),
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/datasources/test", methods=["POST"])
def api_test_key():
    """provider별 실 호출 검증 — 키 유효성 즉시 확인."""
    import time, requests
    from engine.data.keyconfig import get_key
    data = request.get_json(force=True, silent=True) or {}
    provider = (data.get("provider") or "").strip().lower()
    if provider not in ("finnhub", "alphavantage", "fmp", "deepl",
                        "brave", "anthropic"):
        return jsonify({"ok": False, "error": "알 수 없는 provider"}), 400
    k = get_key(provider)
    if not k:
        return jsonify({"ok": False, "error": "키 미설정"})
    t0 = time.time()
    try:
        if provider == "finnhub":
            r = requests.get("https://finnhub.io/api/v1/quote",
                             params={"symbol": "AAPL", "token": k},
                             timeout=8)
            ok = r.status_code == 200 and r.json().get("c", 0) > 0
        elif provider == "alphavantage":
            r = requests.get("https://www.alphavantage.co/query",
                             params={"function": "GLOBAL_QUOTE",
                                     "symbol": "AAPL", "apikey": k},
                             timeout=10)
            j = r.json()
            ok = bool(j.get("Global Quote") and
                      j["Global Quote"].get("05. price"))
        elif provider == "fmp":
            r = requests.get(
                "https://financialmodelingprep.com/stable/quote",
                params={"symbol": "AAPL", "apikey": k}, timeout=8)
            ok = r.status_code == 200 and isinstance(r.json(), list) \
                and len(r.json()) > 0
        elif provider == "deepl":
            url = ("https://api-free.deepl.com" if k.endswith(":fx")
                   else "https://api.deepl.com") + "/v2/usage"
            r = requests.get(url,
                             headers={"Authorization": f"DeepL-Auth-Key {k}"},
                             timeout=8)
            ok = r.status_code == 200
        elif provider == "brave":
            r = requests.get(
                "https://api.search.brave.com/res/v1/web/search",
                params={"q": "test", "count": 1},
                headers={"X-Subscription-Token": k,
                         "Accept": "application/json"}, timeout=8)
            ok = r.status_code == 200
        elif provider == "anthropic":
            r = requests.post(
                "https://api.anthropic.com/v1/messages",
                headers={"x-api-key": k,
                         "anthropic-version": "2023-06-01",
                         "content-type": "application/json"},
                json={"model": "claude-sonnet-4-5",
                      "max_tokens": 5,
                      "messages": [{"role": "user", "content": "hi"}]},
                timeout=15)
            ok = r.status_code == 200
        elapsed = round((time.time() - t0) * 1000)
        return jsonify({
            "ok": ok, "status": r.status_code,
            "elapsed_ms": elapsed,
            "detail": "" if ok else r.text[:200],
        })
    except Exception as e:
        return jsonify({"ok": False,
                        "error": f"{type(e).__name__}: {e}",
                        "elapsed_ms": round((time.time() - t0) * 1000)})


@app.route("/api/datasources/key", methods=["POST"])
def api_set_key():
    """프로그램 설정창 전용 키 저장(로컬 파일에만 기록)."""
    try:
        from engine.data.keyconfig import set_key, masked_status
        data = request.get_json(force=True, silent=True) or {}
        provider = (data.get("provider") or "").strip().lower()
        key = (data.get("key") or "").strip()
        if provider not in ("finnhub", "alphavantage", "fmp", "deepl",
                            "brave", "anthropic"):
            return jsonify({"ok": False,
                            "error": "알 수 없는 provider"}), 400
        if not key or len(key) < 6:
            return jsonify({"ok": False,
                            "error": "키가 너무 짧습니다"}), 400
        ok = set_key(provider, key)
        return jsonify({"ok": ok, "keys": masked_status()})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# 분석 결과 캐시 — 같은 title은 재호출 안 함 (메모리만, 서버 재시작 시 초기화)
_LLM_NEWS_CACHE: Dict[str, Dict[str, Any]] = {}
_LLM_NEWS_CACHE_MAX = 200


@app.route("/api/news/llm_followup", methods=["POST"])
def api_news_llm_followup():
    """
    뉴스 분석 결과에 대한 후속 질문 (로컬 LLM, 무료).

    body: {
        message: 사용자 질문,
        history: [{role, content}, ...],  최근 N턴
        analysis: 위에서 받은 analysis dict (event_type/key_points/
                  affected_assets/rationale_kr 등),
        title: 원본 뉴스 제목 (선택),
    }
    응답은 저장하지 않음 — 모달 닫으면 사라짐 (가벼움).
    """
    try:
        from engine.llm.client import generate, LLMError
        from engine.llm.ollama_setup import (
            is_ollama_running, is_model_installed,
        )
        from engine.llm.text_utils import polish_korean
        data = request.get_json(force=True, silent=True) or {}
        message = (data.get("message") or "").strip()
        history = data.get("history") or []
        analysis = data.get("analysis") or {}
        title = (data.get("title") or "").strip()
        model = (data.get("model") or "deepseek-r1:7b").strip()
        if not message:
            return jsonify({"ok": False, "error": "message 필요"}), 400
        if len(message) > 2000:
            return jsonify({"ok": False,
                            "error": "메시지가 너무 깁니다."}), 400

        if not is_ollama_running():
            return jsonify({"ok": False,
                            "error": "Ollama 서비스가 실행되지 않음"}), 503
        if not is_model_installed(model):
            return jsonify({"ok": False,
                            "error": f"모델 미설치: {model}"}), 503

        # 시스템 프롬프트 — 분석 컨텍스트 + 한국어 강제
        sys_lines = [
            "당신은 위 뉴스 분석 결과에 대한 후속 질문을 받는 시니어 "
            "분석가입니다. 분석 결과를 근거로 한국어로 간결하게 답하세요.",
            "",
            f"## 분석 대상 뉴스" + (f"\n{title}" if title else ""),
        ]
        if analysis.get("event_type"):
            sys_lines.append(f"\n## 분류: {analysis['event_type']}")
        if analysis.get("key_points"):
            sys_lines.append("\n## 분석가 관점 핵심 포인트")
            for p in analysis["key_points"][:5]:
                sys_lines.append(f"  - {p}")
        if analysis.get("affected_assets"):
            sys_lines.append("\n## 영향 받는 자산")
            for a in analysis["affected_assets"][:5]:
                sys_lines.append(
                    f"  - {a.get('ticker')}: {a.get('direction')} "
                    f"(magnitude {a.get('magnitude', 0):.2f}, "
                    f"{a.get('horizon', '')})")
        if analysis.get("consensus_view"):
            sys_lines.append(f"\n## 컨센서스\n{analysis['consensus_view']}")
        if analysis.get("risks"):
            sys_lines.append("\n## 리스크 요인")
            for r in analysis["risks"][:3]:
                sys_lines.append(f"  - {r}")
        if analysis.get("rationale_kr"):
            sys_lines.append(f"\n## 종합 결론\n{analysis['rationale_kr']}")
        sys_lines.append(
            "\n## 답변 규칙\n"
            "- 한국어로만 답변. 영어 단어 직접 사용 금지(ticker 예외).\n"
            "- 2-4문장 이내. 일반론 금지, 위 컨텍스트 근거.\n"
            "- 정보 부족하면 솔직히 '제공된 분석에 해당 정보 없음'.")
        system_prompt = "\n".join(sys_lines)

        # 대화 히스토리 → 단일 prompt로 합침 (Ollama /generate는 단일 prompt)
        conv_lines = []
        for m in history[-10:]:
            role = m.get("role")
            content = (m.get("content") or "").strip()
            if not content:
                continue
            label = "사용자" if role == "user" else "분석가"
            conv_lines.append(f"{label}: {content}")
        conv_lines.append(f"사용자: {message}")
        conv_lines.append("분석가:")

        prompt = "\n\n".join(conv_lines)

        text = generate(
            prompt, model=model, system=system_prompt,
            temperature=0.2, max_tokens=600, timeout=120,
            strip_think=True,
        )
        text = polish_korean(text)

        return jsonify({
            "ok": True,
            "reply": text,
            "model": model,
        })
    except LLMError as e:
        return jsonify({"ok": False, "error": str(e)}), 502
    except Exception as e:
        return jsonify({"ok": False,
                        "error": f"{type(e).__name__}: {e}"}), 500


@app.route("/api/news/llm_analyze", methods=["POST"])
def api_news_llm_analyze():
    """뉴스 한 건을 로컬 LLM으로 심층 분석.

    요청 body: {title, body, source}
    응답: {ok, analysis: {...}, evidence: [...], cached: bool}
    """
    try:
        from engine.llm.news_reasoner import (
            analyze_news_deep, to_evidence_list,
        )
        from engine.llm.ollama_setup import (
            is_ollama_running, is_model_installed,
        )
        data = request.get_json(force=True, silent=True) or {}
        title = (data.get("title") or "").strip()
        body = (data.get("body") or "").strip()
        source = (data.get("source") or "").strip()
        ticker_hint = (data.get("ticker") or "").strip().upper()
        model = (data.get("model") or "deepseek-r1:7b").strip()
        if not title:
            return jsonify({"ok": False, "error": "title 누락"}), 400

        if not is_ollama_running():
            return jsonify({"ok": False,
                            "error": "Ollama 서비스가 실행되지 않음"}), 503
        if not is_model_installed(model):
            return jsonify({"ok": False,
                            "error": f"모델 미설치: {model}"}), 503

        # 캐시 (title 전체 hash + ticker_hint — 충돌 방지)
        import hashlib
        th = hashlib.md5(title.encode("utf-8")).hexdigest()[:12]
        cache_key = f"{th}|{ticker_hint}"
        if cache_key in _LLM_NEWS_CACHE:
            cached = _LLM_NEWS_CACHE[cache_key]
            return jsonify({**cached, "cached": True})

        analysis = analyze_news_deep(title, body=body, source=source,
                                     ticker_hint=ticker_hint,
                                     model=model, timeout=300)
        evidence = to_evidence_list(analysis)
        result = {"ok": analysis.get("ok", False),
                  "analysis": analysis, "evidence": evidence,
                  "cached": False}
        if analysis.get("ok"):
            _LLM_NEWS_CACHE[cache_key] = result
            # LRU 흉내: 너무 커지면 오래된 것부터 잘라냄
            if len(_LLM_NEWS_CACHE) > _LLM_NEWS_CACHE_MAX:
                drop = list(_LLM_NEWS_CACHE.keys())[:50]
                for k in drop:
                    _LLM_NEWS_CACHE.pop(k, None)
        return jsonify(result)
    except Exception as e:
        return jsonify({"ok": False,
                        "error": f"{type(e).__name__}: {e}"}), 500


@app.route("/api/llm/status")
def api_llm_status():
    """로컬 LLM 통합 상태 — 하드웨어 + Ollama + 모델 + 진행 중인 작업."""
    try:
        from engine.llm.hardware import detect_hardware, recommend_model
        from engine.llm.ollama_setup import full_status
        hw = detect_hardware()
        rec = recommend_model(hw)
        st = full_status()
        return jsonify({
            "hardware": hw,
            "recommendation": rec,
            "ollama": st,
        })
    except Exception as e:
        return jsonify({"error": f"{type(e).__name__}: {e}"}), 500


@app.route("/api/llm/install_ollama", methods=["POST"])
def api_llm_install_ollama():
    """OllamaSetup.exe 다운로드+설치를 백그라운드로 시작."""
    try:
        from engine.llm.ollama_setup import install_ollama_windows_async
        return jsonify(install_ollama_windows_async())
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/llm/pull_model", methods=["POST"])
def api_llm_pull_model():
    """모델 다운로드(ollama pull)를 백그라운드로 시작."""
    try:
        from engine.llm.ollama_setup import pull_model_async
        data = request.get_json(force=True, silent=True) or {}
        model = (data.get("model") or "").strip()
        if not model:
            return jsonify({"ok": False,
                            "error": "model 파라미터 누락"}), 400
        return jsonify(pull_model_async(model))
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/llm/auto_setup", methods=["POST"])
def api_llm_auto_setup():
    """원클릭: Ollama 설치 → 권장 모델 다운로드까지 자동.

    이미 단계가 끝났으면 다음 단계로 점프.
    """
    try:
        from engine.llm.ollama_setup import (
            is_ollama_installed, is_ollama_running,
            install_ollama_windows_async, pull_model_async,
            is_model_installed,
        )
        from engine.llm.hardware import recommend_model
        data = request.get_json(force=True, silent=True) or {}
        model = (data.get("model") or "").strip()
        if not model:
            rec = recommend_model()
            model = rec["primary"]["id"]
        # 1) Ollama 설치
        if not is_ollama_installed():
            install_ollama_windows_async()
            return jsonify({"ok": True, "stage": "installing_ollama",
                            "model": model,
                            "message": "Ollama 설치 진행 중 — "
                                       "완료 후 자동으로 모델 다운로드 시작"})
        # 2) 설치됐지만 서비스가 안 뜸
        if not is_ollama_running():
            return jsonify({"ok": False, "stage": "ollama_not_running",
                            "model": model,
                            "message": "Ollama 설치됐으나 서비스 미실행 — "
                                       "Ollama 앱을 한 번 실행해주세요."})
        # 3) 모델 다운로드
        if not is_model_installed(model):
            pull_model_async(model)
            return jsonify({"ok": True, "stage": "pulling_model",
                            "model": model,
                            "message": f"{model} 다운로드 진행 중"})
        # 4) 모두 완료
        return jsonify({"ok": True, "stage": "ready", "model": model,
                        "message": "준비 완료"})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/health")
def api_health():
    return jsonify({"ok": True,
                    "yfinance": bool(_get_yf()),
                    "ts": dt.datetime.now().isoformat()})


def main(host: str = "0.0.0.0", port: int = 8765, debug: bool = False):
    print("=" * 60)
    print("  QUANT TERMINAL  서버 시작")
    print("  로컬 :  http://127.0.0.1:%d" % port)
    print("  폰   :  같은 와이파이에서 http://<이 PC의 IP>:%d" % port)
    print("  야후 :  %s" % ("연결됨" if _get_yf() else "오프라인(합성)"))
    # awareness polling 백그라운드 시작 (GDELT + 국가별 RSS)
    try:
        from engine.awareness.alert_engine import start_polling
        if start_polling():
            print("  속보 :  awareness polling 시작 (5분 간격)")
    except Exception as e:
        print(f"  속보 :  비활성 ({type(e).__name__})")
    print("=" * 60)
    app.run(host=host, port=port, debug=debug, threaded=True)


if __name__ == "__main__":
    main()
