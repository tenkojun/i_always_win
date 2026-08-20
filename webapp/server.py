"""
================================================================
  Plutus  ―  웹 서버 (Flask)
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

# ── 콘솔 인코딩 안전장치 ─────────────────────────────────────────
# 한국어 윈도우 콘솔(cp949)에 이모지를 print 하면 그 줄에서
# UnicodeEncodeError 가 나고 작업 전체가 죽는다. 먼저 막는다.
from engine.console import make_console_safe

make_console_safe()

# ── 런타임 데이터 경로 준비 ──────────────────────────────────────
# 모든 상태(키·인증 DB·채팅·바이너리)는 앱 폴더 안 .data/ 한 곳에 모은다.
# 예전 ~/.jiqt 에 남아 있던 내용은 첫 실행 시 한 번만 옮겨 온다.
from engine.paths import ensure_dirs as _ensure_dirs, migrate_legacy as _migrate

_ensure_dirs()
_moved = _migrate()
if _moved:
    print("[paths] 이전 설치본에서 이전 완료:", ", ".join(_moved))

# 지난 실행이 비정상 종료했으면 cloudflared 만 살아남아 떠돈다. 먼저 정리.
try:
    from engine.cloud.supervisor import reap_orphan as _reap
    _r = _reap()
    if _r.get("reaped"):
        print("[cloud] 남아 있던 cloudflared 정리 (pid %s)" % _r.get("pid"))
except Exception:
    pass

app = Flask(__name__, static_folder=None)

# ── 인증 시스템 초기화 (어드민 seed 포함) ────────────────────────
from engine.auth import init_db as _auth_init
from engine.auth.middleware import (
    attach_user, require_auth, require_admin,
    set_session_cookie, clear_session_cookie, COOKIE_NAME,
)

# 계정 테이블은 더 이상 쓰지 않지만, 같은 DB 에 커뮤니티·분석 이력이
# 들어 있어 스키마 초기화는 계속 필요하다.
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
    from version import DEFAULT_AUTH_SERVER
    return jsonify({
        "configured": is_configured(),
        "server_url": cfg.get("server_url", ""),
        # 앱에 내장된 기본 서버를 쓰는 중인지. 사용자가 주소를 몰라도
        # 바로 로그인되도록 기본값이 들어 있다.
        "is_default": bool(cfg.get("is_default")),
        "default_server": DEFAULT_AUTH_SERVER,
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

@app.route("/api/auth/remote/logout_all", methods=["POST"])
def api_auth_remote_logout_all():
    """모든 기기 세션 종료 — 비밀번호 유출이 의심될 때."""
    from engine.auth_remote import logout_all
    return jsonify(logout_all())


@app.route("/api/auth/remote/change_password", methods=["POST"])
def api_auth_remote_change_password():
    from engine.auth_remote import change_password
    data = request.get_json(force=True, silent=True) or {}
    return jsonify(change_password(
        old_password=data.get("old_password") or "",
        new_password=data.get("new_password") or ""))


@app.route("/api/auth/remote/sessions")
def api_auth_remote_sessions():
    from engine.auth_remote import sessions
    return jsonify(sessions())


@app.route("/api/auth/remote/pc/status")
def api_auth_remote_pc_status():
    from engine.auth_remote import pc_status
    return jsonify(pc_status())


@app.route("/api/auth/remote/pc/unregister", methods=["POST"])
def api_auth_remote_pc_unregister():
    from engine.auth_remote import pc_unregister
    return jsonify(pc_unregister())


# Flask 종료 시 cloudflared 프로세스도 정리
import atexit as _atexit
@_atexit.register
def _cleanup_tunnel():
    try:
        from engine.cloud.supervisor import stop as _sup_stop
        _sup_stop()
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


@app.route("/static/<path:fn>")
def static_files(fn):
    return send_from_directory(_STATIC, fn)


@app.route("/report/<path:fn>")
def report_files(fn):
    return send_from_directory(_REPORTS, fn)


_REPORT_THEME_FILE = "report_theme.txt"


def _report_theme() -> str:
    """
    저장된 보고서 테마. 자기완결 HTML 이라 생성 시점에 결정해야 한다.
    (이미 만든 보고서의 테마는 바뀌지 않는다 — 다시 생성해야 한다.)
    """
    from engine.jiqtx.report_theme import DEFAULT_THEME, THEMES
    from engine.paths import DATA_DIR
    try:
        v = (DATA_DIR / _REPORT_THEME_FILE).read_text(encoding="utf-8").strip()
        return v if v in THEMES else DEFAULT_THEME
    except Exception:
        return DEFAULT_THEME


@app.route("/api/reports/theme", methods=["GET", "POST"])
@require_auth
def api_report_theme():
    from engine.jiqtx.report_theme import THEMES, theme_list
    from engine.paths import DATA_DIR
    if request.method == "GET":
        return jsonify({"ok": True, "theme": _report_theme(),
                        "themes": theme_list()})
    d = request.get_json(force=True, silent=True) or {}
    t = (d.get("theme") or "").strip()
    if t not in THEMES:
        return jsonify({"ok": False, "error": "알 수 없는 테마"}), 400
    try:
        (DATA_DIR / _REPORT_THEME_FILE).write_text(t, encoding="utf-8")
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    return jsonify({"ok": True, "theme": t,
                    "note": "이미 만든 보고서는 바뀌지 않습니다 — 다시 생성하세요."})


@app.route("/api/reports")
@require_auth
def api_reports_list():
    """
    생성된 보고서 목록. 최신순.

    파일명 규칙: <TICKER>_precision.html (최신) 과
    <TICKER>_precision_<타임스탬프>.html (아카이브).
    """
    import re as _re
    items = []
    try:
        for fn in os.listdir(_REPORTS):
            if not fn.lower().endswith(".html"):
                continue
            p = os.path.join(_REPORTS, fn)
            if not os.path.isfile(p):
                continue
            st = os.stat(p)
            m = _re.match(r"^([A-Za-z0-9._-]+?)_(precision|report)"
                          r"(?:_(\d{8}_\d{6}))?\.html$", fn)
            ticker = (m.group(1).replace("_", ".") if m else
                      os.path.splitext(fn)[0])
            items.append({
                "file": fn,
                "ticker": ticker.upper(),
                "kind": ("정밀" if (m and m.group(2) == "precision")
                         else "분석"),
                "archived": bool(m and m.group(3)),
                "size": st.st_size,
                "mtime": dt.datetime.fromtimestamp(st.st_mtime).isoformat(),
                "url": "/report/" + fn,
                "download": "/api/reports/download/" + fn,
            })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    items.sort(key=lambda x: x["mtime"], reverse=True)
    return jsonify({"ok": True, "items": items, "count": len(items)})


def _safe_report_path(fn: str):
    """
    경로 탈출 차단. 사용자가 준 이름을 그대로 join 하면
    ../../ 로 저장소 바깥 파일을 내려받을 수 있다.
    """
    name = os.path.basename(fn or "")
    if not name.lower().endswith(".html"):
        return None
    p = os.path.realpath(os.path.join(_REPORTS, name))
    if os.path.commonpath([p, os.path.realpath(_REPORTS)]) !=             os.path.realpath(_REPORTS):
        return None
    return p if os.path.isfile(p) else None


@app.route("/api/reports/download/<path:fn>")
@require_auth
def api_reports_download(fn):
    p = _safe_report_path(fn)
    if not p:
        abort(404)
    return send_from_directory(_REPORTS, os.path.basename(p),
                               as_attachment=True)


@app.route("/api/reports/delete", methods=["POST"])
@require_auth
def api_reports_delete():
    d = request.get_json(force=True, silent=True) or {}
    p = _safe_report_path(d.get("file") or "")
    if not p:
        return jsonify({"ok": False, "error": "파일 없음"}), 404
    try:
        os.remove(p)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    return jsonify({"ok": True})


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

    # 종목마다 시세 + 시총을 **직렬로** 왕복하던 코드였다. 1종목에 약
    # 1.9초(quote 1.2s + marketcap 0.6s)라 23종목이면 43초 — 프론트가
    # 기다리다 끊긴다. 네트워크 대기가 대부분이라 스레드로 겹치면 된다.
    def _one(entry):
        sector, sym, name = entry
        try:
            q = _quote_one(sym)
            cap = _ticker_marketcap(yf, sym) if yf else 0.0
        except Exception:
            q, cap = {}, 0.0
        # cap이 0이면 (오프라인 또는 fast_info 실패) 가격 기반 가중치
        if cap <= 0:
            cap = max(1.0, float(q.get("price") or 1.0)) * 1e9
        return sector, {
            "ticker": sym,
            "name": name,
            "price": q.get("price"),
            "pct": q.get("pct") or 0.0,
            "cap": cap,
            "live": q.get("live", False),
        }

    from concurrent.futures import ThreadPoolExecutor
    sectors: Dict[str, Dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=12) as ex:
        for sector, item in ex.map(_one, HEATMAP_UNIVERSE):
            sectors.setdefault(sector, {"name": sector, "cap": 0.0,
                                        "items": []})
            sectors[sector]["cap"] += item["cap"]
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
# ══════════════════════════════════════════════════════════════
#  인증 — 신원은 전부 중앙 서버(Cloudflare Workers + D1)가 정한다
#
#  예전에는 이 PC 의 SQLite 에 계정을 두고 쿠키로 로그인했다. 그러면
#  PC 마다 계정이 따로 놀고, 내 PC 가 꺼져 있으면 아무도 가입·로그인을
#  할 수 없다. 로컬 계정 시스템은 제거했고, 아래 경로들은 모두 중앙
#  서버를 호출하는 얇은 껍데기다. (프론트가 쓰던 경로는 그대로 둔다.)
# ══════════════════════════════════════════════════════════════
def _auth_invalidate(central_token=None):
    """로그인/로그아웃 직후 캐시된 신원을 버린다."""
    try:
        from engine.auth.middleware import invalidate
        invalidate(central_token)
    except Exception:
        pass


@app.route("/api/auth/register", methods=["POST"])
def api_auth_register():
    """가입 신청 — 중앙 서버에서 status=pending 으로 생성된다."""
    from engine.auth_remote import register
    d = request.get_json(force=True, silent=True) or {}
    r = register(username=(d.get("username") or "").strip(),
                 password=d.get("password") or "",
                 nickname=(d.get("nickname") or "").strip())
    return jsonify(r), (200 if r.get("ok") else 400)


@app.route("/api/auth/login", methods=["POST"])
def api_auth_login():
    """
    중앙 서버로 자격을 확인하고, **이 브라우저 전용** 세션 쿠키를 발급한다.

    중앙 토큰 자체는 브라우저로 내려보내지 않는다 — 서버가 보관하고
    쿠키는 그것을 가리키기만 한다.
    """
    from engine.auth_remote import login_raw
    from engine.auth import session_store
    d = request.get_json(force=True, silent=True) or {}
    username = (d.get("username") or "").strip()
    password = d.get("password") or ""
    if not username or not password:
        return jsonify({"ok": False, "error": "username/password 필요"}), 400

    r = login_raw(username, password)
    if not r.get("ok"):
        # 중앙 서버가 준 사유(승인 대기·잠금 등)를 그대로 전달한다
        code = 429 if r.get("locked_minutes") else 401
        if "대기" in str(r.get("error", "")):
            code = 403
        return jsonify(r), code

    central = r.get("token") or ""
    user = r.get("user", {}) or {}
    device = (request.headers.get("User-Agent") or "")[:80]
    browser_token = session_store.create(central, user, device=device)
    _auth_invalidate()

    # 이 PC 가 중앙 서버에 자기 주소를 등록할 때 쓰는 세션도 갱신해 둔다
    # (외부 접근 감시자가 /pc/register 를 호출한다).
    try:
        from engine.auth_remote.client import _save_session
        _save_session(central, user)
    except Exception:
        pass

    # 예전 로컬 계정 시절의 글·이력이 새 중앙 id 를 가리키도록 1회 이전.
    # 어드민 토큰이 있어야 사용자 목록을 받을 수 있어 로그인 직후에 시도한다.
    try:
        from engine.auth.migrate_ids import migrate_if_possible
        _m = migrate_if_possible()
        if _m.get("remapped"):
            print("[auth] 사용자 id 이전:", _m)
    except Exception:
        pass

    resp = make_response(jsonify({"ok": True, "user": user}))
    set_session_cookie(resp, browser_token)
    return resp


@app.route("/api/auth/logout", methods=["POST"])
def api_auth_logout():
    """이 브라우저만 로그아웃. 다른 기기 세션은 건드리지 않는다."""
    from engine.auth import session_store
    from engine.auth_remote import logout_token
    browser_token = request.cookies.get(COOKIE_NAME) or ""
    central = session_store.central_token(browser_token)
    session_store.drop(browser_token)
    if central:
        try:
            logout_token(central)
        except Exception:
            pass
    _auth_invalidate(central)
    resp = make_response(jsonify({"ok": True}))
    clear_session_cookie(resp)
    return resp


# ── QR 일회용 로그인 (폰에서 스캔) ──────────────────────────────
@app.route("/api/auth/qr_token", methods=["POST"])
@require_auth
def api_auth_qr_token():
    """현재 로그인된 브라우저의 세션을 폰으로 옮기는 일회용 토큰."""
    from engine.auth.qr_token import issue
    return jsonify(issue(g.user["id"]))


@app.route("/qr_login")
def qr_login():
    """
    QR 자동 로그인 — 일회용 토큰을 이 기기의 세션 쿠키로 바꾼다.

    QR 을 만든 브라우저의 중앙 토큰을 그대로 물려받는다. 중앙 서버에
    다시 로그인할 필요가 없다(비밀번호를 폰에 입력하지 않아도 된다).
    """
    from flask import redirect
    from engine.auth.qr_token import consume
    from engine.auth import session_store

    token = (request.args.get("token") or "").strip()
    if not token:
        return redirect("/?qr_error=no_token")
    user_id = consume(token)
    if not user_id:
        return redirect("/?qr_error=invalid_or_expired")

    # 그 사용자의 살아 있는 브라우저 세션에서 중앙 토큰을 빌려 온다
    central = None
    user = {}
    try:
        from engine.auth_remote import load_session
        sess = load_session()
        if sess.get("token") and (sess.get("user") or {}).get("id") == user_id:
            central = sess["token"]
            user = sess.get("user") or {}
    except Exception:
        pass
    if not central:
        return redirect("/?qr_error=no_central_session")

    device = (request.headers.get("User-Agent") or "")[:80]
    browser_token = session_store.create(central, user, device=device)
    resp = make_response(redirect("/"))
    set_session_cookie(resp, browser_token)
    return resp


@app.route("/api/auth/me")
def api_auth_me():
    if not getattr(g, "user", None):
        return jsonify({"authenticated": False})
    u = g.user
    # Claude 사용 쿼터는 이 PC 기준으로 센다(엔진이 로컬에서 돈다)
    quota = {}
    try:
        from engine.auth import check_claude_quota
        quota = check_claude_quota(u.get("id"), limit=10)
    except Exception:
        pass
    main_pc = {}
    try:
        from engine.auth_remote import pc_status
        st = pc_status()
        if st.get("ok"):
            main_pc = st.get("pc") or {}
    except Exception:
        pass
    return jsonify({
        "authenticated": True,
        "user": {
            "id": u.get("id"),
            "username": u.get("username"),
            "nickname": u.get("nickname") or u.get("username"),
            "role": u.get("role", "user"),
            "status": u.get("status", "active"),
        },
        "claude_quota": quota,
        "main_pc": main_pc,
    })


# ── 어드민 — 중앙 서버의 사용자 관리 ────────────────────────────
@app.route("/api/admin/users")
@require_admin
def api_admin_users():
    from engine.auth_remote import admin_users
    return jsonify(admin_users())


@app.route("/api/admin/pending")
@require_admin
def api_admin_pending():
    from engine.auth_remote import admin_users
    r = admin_users()
    users = [u for u in (r.get("users") or [])
             if u.get("status") == "pending"]
    return jsonify({"users": users})


@app.route("/api/admin/approve", methods=["POST"])
@require_admin
def api_admin_approve():
    from engine.auth_remote import admin_approve
    d = request.get_json(force=True, silent=True) or {}
    uid = int(d.get("user_id") or 0)
    if not uid:
        return jsonify({"ok": False, "error": "user_id 필요"}), 400
    return jsonify(admin_approve(uid))


@app.route("/api/admin/reject", methods=["POST"])
@require_admin
def api_admin_reject():
    from engine.auth_remote import admin_reject
    d = request.get_json(force=True, silent=True) or {}
    uid = int(d.get("user_id") or 0)
    if not uid:
        return jsonify({"ok": False, "error": "user_id 필요"}), 400
    if uid == g.user.get("id"):
        return jsonify({"ok": False,
                        "error": "본인 계정은 거부할 수 없습니다."}), 400
    return jsonify(admin_reject(uid))


@app.route("/api/admin/reset_quota", methods=["POST"])
@require_admin
def api_admin_reset_quota():
    """
    Claude 사용 쿼터 초기화.

    쿼터는 중앙 서버가 아니라 **이 PC** 가 센다(LLM 호출이 여기서 난다).
    그래서 계정이 중앙으로 옮겨간 뒤에도 이 엔드포인트는 로컬이다.
    """
    from engine.auth import reset_claude_quota
    d = request.get_json(force=True, silent=True) or {}
    uid = int(d.get("user_id") or 0)
    if not uid:
        return jsonify({"ok": False, "error": "user_id 필요"}), 400
    reset_claude_quota(uid)
    return jsonify({"ok": True})


@app.route("/api/admin/stats")
@require_admin
def api_admin_stats():
    """중앙 서버 사용자 목록을 요약해서 돌려준다."""
    from engine.auth_remote import admin_users
    users = (admin_users() or {}).get("users") or []
    def cnt(st):
        return sum(1 for u in users if u.get("status") == st)
    detail = [{
        "id": u.get("id"),
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
    } for u in users]
    return jsonify({
        "summary": {
            "total_users": len(users),
            "active_users": cnt("active"),
            "pending_users": cnt("pending"),
            "rejected_users": cnt("rejected"),
        },
        "users": detail,
        "top_login": sorted(detail, key=lambda x: x["login_count"],
                            reverse=True)[:5],
        "top_claude_today": sorted(detail, key=lambda x: x["claude_used"],
                                   reverse=True)[:5],
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
def _app_port() -> int:
    try:
        return int(os.environ.get("IAW_PORT", "8765"))
    except ValueError:
        return 8765


@app.route("/api/cloud/status")
@require_auth
def api_cloud_status():
    from engine.cloud.supervisor import status, last_known
    from engine.cloud.pc_id import get_pc_id, get_pc_label
    s = status()
    s["pc_id"] = get_pc_id()
    s["pc_label"] = get_pc_label()
    s["last_known"] = last_known()
    return jsonify(s)


@app.route("/api/cloud/install", methods=["POST"])
@require_auth
def api_cloud_install():
    from engine.cloud.tunnel import install_async
    return jsonify(install_async())


@app.route("/api/cloud/start_quick", methods=["POST"])
@require_auth
def api_cloud_start_quick():
    """
    외부 접근 켜기 — 터널을 띄우고 **감시자에 맡긴다**.

    감시자가 죽은 터널을 되살리고, 주소가 바뀌면 중앙 서버에 다시
    등록한다. 그래야 /go/<username> 이 항상 살아 있는 주소를 가리킨다.
    """
    from engine.cloud.supervisor import start as sup_start
    from engine.cloud.pc_id import get_pc_id, get_pc_label
    from engine.auth import set_main_pc
    r = sup_start(port=_app_port())
    try:
        set_main_pc(g.user["id"], get_pc_id(), get_pc_label())
    except Exception:
        pass
    return jsonify(r)


@app.route("/api/cloud/stop", methods=["POST"])
@require_auth
def api_cloud_stop():
    from engine.cloud.supervisor import stop as sup_stop
    return jsonify(sup_stop())


@app.route("/api/cloud/healthcheck")
@require_auth
def api_cloud_healthcheck():
    """Tunnel URL이 외부에서 접근 가능한지 검증."""
    from engine.cloud.tunnel import health_check
    return jsonify(health_check())


@app.route("/api/cloud/restart", methods=["POST"])
@require_auth
def api_cloud_restart():
    """터널 강제 재시작 — 감시자가 붙어 있으면 주소 재등록까지 이어진다."""
    from engine.cloud.tunnel import restart_quick
    from engine.cloud.supervisor import publish_url, status as sup_status
    r = restart_quick(local_port=_app_port())
    return jsonify(r)


@app.route("/api/cloud/publish", methods=["POST"])
@require_auth
def api_cloud_publish():
    """현재 터널 주소를 중앙 서버에 수동으로 다시 등록."""
    from engine.cloud.supervisor import publish_url
    from engine.cloud.tunnel import status as t_status
    return jsonify(publish_url(t_status().get("url", "")))


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
        tunnel_name=(data.get("tunnel_name") or "iaw-tunnel").strip(),
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


# ── 시장 수급 스캐너 ──────────────────────────────────────
@app.route("/api/market/flow")
@require_auth
def api_market_flow():
    """
    당일 수급 보드. 세 질문에 답한다 —
    어제 대비 자금이 어디로 이동했나 / 지금 어디를 집중 매수·매도하나 /
    오늘 어느 섹터가 강세인가.

    계산이 수 초 걸리므로 엔진 쪽에서 3분 캐시를 둔다. 15분 지연
    데이터라 그보다 자주 돌 이유가 없다.
    """
    from engine.data.market_flow import build_board
    market = (request.args.get("market") or "US").upper()
    try:
        top = max(5, min(int(request.args.get("top") or 30), 60))
    except ValueError:
        top = 30
    fresh = request.args.get("fresh") == "1"
    board = build_board(market=market, top=top, use_cache=not fresh)
    return jsonify({"ok": not board.error, **board.to_dict()})


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


# ══════════════════════════════════════════════════════════════
#  정밀 분석 (jiqtx) — 게이트·패널·판정까지 도는 무거운 파이프라인
# ══════════════════════════════════════════════════════════════
_JX_JOBS: Dict[str, Dict[str, Any]] = {}


def _jx_summary(a) -> Dict[str, Any]:
    """
    Analysis 객체에서 화면에 쓸 값만 추린다.

    필드명은 추측하지 않고 각 dataclass 정의를 확인해 맞췄다.
    Verdict 는 grade / direction_prob / direction_ci / model_confidence /
    risk_budget_weight / vetoes / disabled_modules / rationale 을 갖는다.
    """
    def g(obj, name, default=None):
        return getattr(obj, name, default) if obj is not None else default

    def num(x):
        """numpy 스칼라·NaN 을 JSON 이 먹을 수 있게."""
        try:
            import math as _m
            if x is None:
                return None
            f = float(x)
            return None if _m.isnan(f) or _m.isinf(f) else f
        except Exception:
            return None

    v    = g(a, "verdict")
    cls  = g(a, "classification")
    liq  = g(a, "liquidity")
    ml   = g(a, "ml")
    var  = g(a, "var")
    dd   = g(a, "drawdown")
    sz   = g(a, "sizing")
    vp   = g(a, "vol_profile")
    reg  = g(a, "regime")
    perf = g(a, "perf", {}) or {}

    # 레짐 이름 — labels[current_state]
    regime_label = None
    try:
        labels = g(reg, "labels") or []
        cur = g(reg, "current_state")
        if labels and cur is not None:
            regime_label = labels[int(cur)]
    except Exception:
        pass

    # 변동성 — GARCH 현재 연율화, 없으면 21일 실현
    vol_ann = num(g(g(vp, "garch"), "ann_vol_current"))         or num(g(vp, "realized_21d_ann"))

    # VaR — 엔진이 채택한 방법의 값을 쓴다
    var95 = None
    pref = g(var, "preferred")
    if pref:
        var95 = num(g(var, "var_" + str(pref)))
    if var95 is None:
        var95 = num(g(var, "var_historical"))

    ci = g(v, "direction_ci") or (None, None)

    return {
        # 판정
        "grade": g(v, "grade"),
        "direction_prob": num(g(v, "direction_prob")),
        "direction_ci": [num(ci[0]), num(ci[1])],
        "model_confidence": g(v, "model_confidence"),
        "risk_budget_weight": num(g(v, "risk_budget_weight")),
        "vetoes": [str(x) for x in (g(v, "vetoes", []) or [])],
        "disabled_modules": [str(x) for x in (g(v, "disabled_modules", []) or [])],
        "rationale": [str(x) for x in (g(v, "rationale", []) or [])][:8],

        # 성격 · 거래 가능성
        "asset_class": g(cls, "asset_class"),
        "class_confidence": num(g(cls, "confidence")),
        "tradable": bool(g(liq, "tradable", False)),
        "liq_reason": g(liq, "reason", ""),
        "spread_bps": num(g(liq, "spread_bps")),

        # 시장 상태
        "vol_ann": vol_ann,
        "regime_label": regime_label,

        # ML (기권이면 그대로 드러낸다)
        "ml_verdict": g(ml, "verdict"),
        "ml_oos": num(g(ml, "oos_accuracy")),
        "ml_prob_up": num(g(ml, "prob_up_now")),
        "ml_reasons": [str(x) for x in (g(ml, "reasons", []) or [])][:4],

        # 리스크 · 사이징
        "var95": var95,
        "var_method": pref,
        "mdd": num(g(dd, "max_drawdown")),
        "size_pct": num(g(sz, "final_weight")),
        "size_binding": g(sz, "binding_constraint"),

        # 성과
        "cagr": num(perf.get("cagr")),
        "sharpe": num(perf.get("sharpe")),

        "warnings": [str(x) for x in (g(a, "warnings", []) or [])][:6],
    }


def _run_jiqtx_job(job_id: str, ticker: str, fast: bool, user_id=None):
    """
    정밀 분석 1회 실행 → 자기완결 HTML 리포트 저장 + 이력 기록.

    실패해도 예외를 밖으로 내보내지 않는다. 작업 상태에 담아
    프론트가 사유를 그대로 보여 주게 한다.
    """
    t0 = time.time()
    try:
        from engine import jiqtx
        from dataclasses import replace as _replace

        cfg = jiqtx.RUN
        if fast:
            try:
                cfg = _replace(cfg, n_sims=2000, fast=True)
            except TypeError:
                cfg = _replace(cfg, n_sims=2000)

        a = jiqtx.analyze(ticker, cfg=cfg)

        safe_tk = ticker.replace("/", "_").replace(".", "_")
        base = "%s_precision.html" % safe_tk
        path = os.path.join(_REPORTS, base)
        jiqtx.save_html(a, path, theme=_report_theme())
        report_url = "/report/" + base

        # 같은 분석으로 **간단 리서치**도 함께 만든다. 다시 계산하지 않고
        # 보여 주는 방식만 바꾸는 것이라 비용이 거의 없다.
        simple_url = ""
        try:
            from engine.jiqtx.simple_report import save_simple
            sbase = "%s_simple.html" % safe_tk
            save_simple(a, os.path.join(_REPORTS, sbase),
                        theme=_report_theme(), full_report_url=report_url)
            simple_url = "/report/" + sbase
        except Exception as e:
            print("[simple_report] 생성 실패:", e)

        # 영구 이력용 타임스탬프 사본
        archive_url = ""
        try:
            import shutil
            root, ext = os.path.splitext(base)
            arch = "%s_%s%s" % (root,
                                dt.datetime.now().strftime("%Y%m%d_%H%M%S"), ext)
            shutil.copy2(path, os.path.join(_REPORTS, arch))
            archive_url = "/report/" + arch
        except Exception:
            pass

        summary = _jx_summary(a)
        result = {
            "status": "done",
            "ticker": ticker,
            "asof": getattr(a, "asof", ""),
            "elapsed": round(time.time() - t0, 1),
            "report_url": report_url,
            "simple_url": simple_url,
            "archive_url": archive_url,
            **summary,
        }
        _JX_JOBS[job_id] = result

        # 분석 이력에 남긴다 — 예전 analyze 가 하던 일을 이어받는다
        try:
            from engine.analyze_history import save_analysis
            save_analysis(user_id, ticker, {
                "overall_signal": summary.get("grade"),
                "overall_score": (summary.get("direction_prob") or 0) * 100,
                "grade": summary.get("grade"),
                "verdict": summary.get("grade"),
                "report_url": report_url,
                "simple_url": simple_url,
                "meta_verdict": {
                    "signal": summary.get("grade"),
                    "headline": "; ".join(summary.get("rationale", [])[:2]),
                    "risk_grade": summary.get("model_confidence"),
                },
                "precision": summary,
            })
        except Exception as e:
            print("[jiqtx] 이력 저장 실패:", e)

    except Exception as e:
        import traceback
        _JX_JOBS[job_id] = {
            "status": "error",
            "ticker": ticker,
            "elapsed": round(time.time() - t0, 1),
            "error": "%s: %s" % (type(e).__name__, e),
            "trace": traceback.format_exc()[-2000:],
        }


@app.route("/api/jiqtx/analyze", methods=["POST"])
@require_auth
def api_jiqtx_analyze():
    d = request.get_json(force=True, silent=True) or {}
    ticker = (d.get("ticker") or "").strip().upper()
    if not ticker:
        return jsonify({"ok": False, "error": "ticker 필요"}), 400
    fast = bool(d.get("fast", True))

    # 한도는 **서버에서** 센다. 프론트만 막으면 이 라우트를 직접 때리면
    # 그만이다. 확인과 증가를 한 트랜잭션에서 해서 동시 클릭으로 한도를
    # 넘기지 못하게 한다.
    from engine.auth.quota import consume
    q = consume((g.user or {}).get("id"), "report")
    if not q.get("ok"):
        return jsonify({"ok": False, "error": q.get("error"),
                        "quota": q}), 429

    job_id = "jx_%d" % int(time.time() * 1000)
    _JX_JOBS[job_id] = {"status": "running", "ticker": ticker}
    threading.Thread(
        target=_run_jiqtx_job,
        args=(job_id, ticker, fast, (g.user or {}).get("id")),
        daemon=True).start()
    return jsonify({"ok": True, "job_id": job_id, "status": "running",
                    "quota": q})


# ── 바로가기 (바탕화면 · 시작 메뉴) ────────────────────────
@app.route("/api/shortcuts/status")
@require_auth
def api_shortcuts_status():
    from engine.shortcuts import status
    return jsonify({"ok": True, **status()})


@app.route("/api/shortcuts/create", methods=["POST"])
@require_auth
def api_shortcuts_create():
    """
    바탕화면 · 시작 메뉴 바로가기 생성.
    시작 메뉴 쪽이 있어야 **윈도우 검색에 뜬다.**
    """
    from engine.shortcuts import create
    d = request.get_json(force=True, silent=True) or {}
    r = create(desktop=bool(d.get("desktop", True)),
               start_menu=bool(d.get("start_menu", True)))
    return jsonify(r), (200 if r.get("ok") else 400)


@app.route("/api/shortcuts/dismiss", methods=["POST"])
@require_auth
def api_shortcuts_dismiss():
    """'다음에' — 다시 묻지 않게 표시만 남긴다."""
    from engine.shortcuts import mark_asked
    mark_asked()
    return jsonify({"ok": True})


# ── 자동 업데이트 ─────────────────────────────────────────
_UPD: Dict[str, Any] = {"state": "idle", "pct": 0, "msg": "", "error": ""}


@app.route("/api/update/check")
@require_auth
def api_update_check():
    """새 버전이 있는지 조회. 실패해도 200 으로 사유를 담아 돌려준다."""
    from engine.updater import check
    return jsonify(check())


@app.route("/api/update/start", methods=["POST"])
@require_auth
def api_update_start():
    """
    내려받기 + 검증 + 압축 해제까지. **교체는 하지 않는다** —
    사용자가 한 번 더 확인한 뒤 /api/update/apply 를 부른다.
    """
    from engine.updater import check, download, verify_and_stage
    # **관리자만.** 이 앱은 터널로 여러 명이 한 인스턴스에 붙는
    # 구조라, @require_auth 만 걸면 아무 사용자나 서버를 통째로
    # 재시작시킬 수 있다. 확인(check)은 누구나 해도 되지만 받기와
    # 교체는 소유자만 한다.
    if (g.user or {}).get("role") != "admin":
        return jsonify({"ok": False,
                        "error": "업데이트는 관리자만 실행할 수 있습니다."}), 403

    if _UPD["state"] in ("downloading", "staging"):
        return jsonify({"ok": False, "error": "이미 진행 중입니다."}), 409

    info = check()
    if not info.get("ok") or not info.get("newer"):
        return jsonify({"ok": False, "error": "받을 새 버전이 없습니다."}), 400
    asset = info.get("asset")
    if not asset or not asset.get("url"):
        return jsonify({"ok": False,
                        "error": "릴리스에 윈도우 배포본(zip)이 없습니다."}), 400

    def _run():
        try:
            _UPD.update(state="downloading", pct=0, msg="내려받는 중", error="")

            def prog(got, total):
                _UPD["pct"] = int(got * 100 / max(total, 1))

            zp = download(asset["url"], asset.get("size") or 0, progress=prog)
            _UPD.update(state="staging", pct=100, msg="검증 · 압축 해제")
            r = verify_and_stage(zp)
            if not r.get("ok"):
                _UPD.update(state="error", error=r.get("error") or "검증 실패")
                return
            _UPD.update(state="ready", msg=f"준비 완료 · 파일 {r['files']}개")
        except Exception as e:
            _UPD.update(state="error", error=f"{type(e).__name__}: {e}")

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"ok": True, "state": "downloading",
                    "asset": asset, "latest": info.get("latest")})


@app.route("/api/update/status")
@require_auth
def api_update_status():
    return jsonify({"ok": True, **_UPD})


@app.route("/api/update/apply", methods=["POST"])
@require_auth
def api_update_apply():
    """
    교체 스크립트를 띄우고 앱을 내린다. 실행 중인 exe 는 윈도우가
    잠그므로 앱이 살아 있는 동안에는 바꿀 수 없다.
    """
    from engine.updater import apply_staged
    # **관리자만.** 이 앱은 터널로 여러 명이 한 인스턴스에 붙는
    # 구조라, @require_auth 만 걸면 아무 사용자나 서버를 통째로
    # 재시작시킬 수 있다. 확인(check)은 누구나 해도 되지만 받기와
    # 교체는 소유자만 한다.
    if (g.user or {}).get("role") != "admin":
        return jsonify({"ok": False,
                        "error": "업데이트는 관리자만 실행할 수 있습니다."}), 403
    if _UPD.get("state") != "ready":
        return jsonify({"ok": False,
                        "error": "준비된 업데이트가 없습니다."}), 400
    r = apply_staged()
    if not r.get("ok"):
        return jsonify(r), 400

    # 응답이 브라우저에 도착한 뒤에 내려간다
    def _bye():
        time.sleep(1.5)
        os._exit(0)
    threading.Thread(target=_bye, daemon=True).start()
    return jsonify(r)


# ── 회원 등급 · 보고서 한도 ────────────────────────────────
@app.route("/api/quota")
@require_auth
def api_quota():
    from engine.auth.quota import quota_status, features, TIER_KO, TIERS
    return jsonify({"ok": True,
                    **quota_status(g.user["id"], "report"),
                    "features": features(g.user["id"]),
                    "tiers": [{"id": t, "ko": TIER_KO[t]} for t in TIERS]})


@app.route("/api/quota/tier", methods=["POST"])
@require_auth
def api_quota_set_tier():
    """
    등급 변경. **관리자만** — 본인이 스스로 프리미엄으로 올릴 수 있으면
    한도가 있으나 마나다.
    """
    from engine.auth.quota import set_tier, quota_status
    if (g.user or {}).get("role") != "admin":
        return jsonify({"ok": False, "error": "관리자만 변경할 수 있습니다."}), 403
    d = request.get_json(force=True, silent=True) or {}
    uid = d.get("user_id") or g.user["id"]
    r = set_tier(int(uid), (d.get("tier") or "free"))
    if not r.get("ok"):
        return jsonify(r), 400
    return jsonify({"ok": True, **quota_status(int(uid), "report")})


@app.route("/api/jiqtx/analyze/<job_id>")
@require_auth
def api_jiqtx_status(job_id):
    job = _JX_JOBS.get(job_id)
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


@app.route("/api/app/info")
def api_app_info():
    """앱 이름·버전·개발자 — 설정 화면 '정보' 패널이 그대로 뿌린다."""
    import platform
    from version import build_info
    info = build_info()
    info["python"] = platform.python_version()
    return jsonify(info)


def main(host: str = "0.0.0.0", port: int = 8765, debug: bool = False):
    print("=" * 60)
    from version import APP_NAME, __version__ as _v
    print("  %s  v%s  서버 시작" % (APP_NAME, _v))
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
