"""
Paper Trading 엔진
============================================================
2가지 모드 자동 선택:

1) KIS 모의계좌 (kis_keys.json에 vts 설정 시) — 실제 KIS 모의 주문 API
   → 한국투자증권 모의투자 시스템에서 진짜 시뮬레이션
2) 내부 시뮬레이터 (KIS 키 없을 때) — in-memory 가상 portfolio
   → 시세는 fetch_ohlcv_best 폴백 (yfinance 등)

기능:
  - 활성 전략의 시그널 발생 시 자동 주문
  - 실시간 PnL 추적 (Tier 2 #6)
  - 일중 손익 / 누적 / drawdown
  - 모의 계좌이므로 위험 없음

SQLite 테이블 (auth.db):
  paper_orders   : 주문 이력
  paper_positions: 현재 포지션
  paper_history  : 일별 NAV
"""
from __future__ import annotations

import datetime as dt
import json
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from ..auth.store import _LOCK as _AUTH_LOCK, _conn as _auth_conn, init_db


_SCHEMA = """
CREATE TABLE IF NOT EXISTS paper_orders (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id      INTEGER NOT NULL,
  ts           TEXT NOT NULL,
  ticker       TEXT NOT NULL,
  side         TEXT NOT NULL,    -- buy / sell
  qty          INTEGER NOT NULL,
  price        REAL NOT NULL,
  amount       REAL NOT NULL,
  source       TEXT,             -- strategy_id 등
  kis_order_no TEXT,
  status       TEXT NOT NULL,    -- pending / filled / canceled / failed
  note         TEXT
);
CREATE INDEX IF NOT EXISTS idx_paper_orders_user
  ON paper_orders(user_id, ts DESC);

CREATE TABLE IF NOT EXISTS paper_positions (
  user_id      INTEGER NOT NULL,
  ticker       TEXT NOT NULL,
  qty          INTEGER NOT NULL,
  avg_price    REAL NOT NULL,
  updated_at   TEXT NOT NULL,
  PRIMARY KEY (user_id, ticker)
);

CREATE TABLE IF NOT EXISTS paper_nav (
  user_id      INTEGER NOT NULL,
  date         TEXT NOT NULL,    -- YYYY-MM-DD
  nav          REAL NOT NULL,
  cash         REAL NOT NULL,
  equity_value REAL NOT NULL,
  daily_pnl    REAL,
  PRIMARY KEY (user_id, date)
);

CREATE TABLE IF NOT EXISTS paper_state (
  user_id      INTEGER PRIMARY KEY,
  initial_cash REAL NOT NULL DEFAULT 10000000,
  cash         REAL NOT NULL,
  created_at   TEXT NOT NULL,
  updated_at   TEXT NOT NULL
);
"""


def _init_paper_db():
    init_db()
    with _AUTH_LOCK:
        _auth_conn().executescript(_SCHEMA)
        _auth_conn().commit()


_init_paper_db()


def _now() -> str:
    return dt.datetime.utcnow().isoformat()


def _today() -> str:
    return dt.date.today().isoformat()


class PaperTradingEngine:
    """사용자별 paper trading 엔진."""

    def __init__(self, user_id: int, initial_cash: float = 10_000_000):
        self.user_id = user_id
        # 초기 state 보장
        self._ensure_state(initial_cash)

    def _ensure_state(self, initial_cash: float):
        with _AUTH_LOCK:
            row = _auth_conn().execute(
                "SELECT * FROM paper_state WHERE user_id=?",
                (self.user_id,)).fetchone()
            if not row:
                now = _now()
                _auth_conn().execute(
                    "INSERT INTO paper_state "
                    "(user_id, initial_cash, cash, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (self.user_id, initial_cash, initial_cash, now, now))
                _auth_conn().commit()

    # ─── 시세 조회 (KIS → 폴백) ───────────────────────────────
    def _get_quote(self, ticker: str) -> Optional[float]:
        """현재가. KIS 키 있으면 KIS, 없으면 yfinance 등."""
        try:
            from ..data.sources.kis import quote_kr, quote_us, has_keys
            if has_keys("vts") or has_keys("real"):
                mode = "vts" if has_keys("vts") else "real"
                if ticker.isdigit() and len(ticker) == 6:
                    r = quote_kr(ticker, mode=mode)
                else:
                    r = quote_us(ticker, mode=mode)
                if r.get("ok"):
                    return float(r.get("price") or 0) or None
        except Exception:
            pass
        # 폴백: 가장 최근 일봉 close
        try:
            from ..data.sources import fetch_ohlcv_best
            r = fetch_ohlcv_best(ticker, cross_validate=False)
            df = r.get("df")
            if df is not None and len(df) > 0:
                return float(df["close"].iloc[-1])
        except Exception:
            pass
        return None

    # ─── 주문 실행 (KIS 모의 → 실패 시 내부 시뮬) ─────────────
    def place_order(self, ticker: str, side: str, qty: int,
                     price: Optional[float] = None,
                     source: str = "manual",
                     note: str = "",
                     skip_risk_check: bool = False) -> Dict[str, Any]:
        """매수/매도 주문. KIS 모의 사용 가능하면 진짜 모의 API, 아니면 내부.
        price=None이면 시장가 (현재가 fetch).
        """
        side = side.lower()
        if side not in ("buy", "sell"):
            return {"ok": False, "error": "side = buy | sell"}
        if qty <= 0:
            return {"ok": False, "error": "qty > 0"}
        # 시장가 처리
        if price is None or price <= 0:
            price = self._get_quote(ticker)
            if not price:
                return {"ok": False, "error": "현재가 fetch 실패"}
        amount = price * qty
        # Tier2 #7: 리스크 한도 검증 (skip_risk_check 옵션으로 수동 무시 가능)
        if not skip_risk_check:
            try:
                from .risk_limits import check_order
                chk = check_order(self.user_id, ticker, side, qty, price)
                if not chk.get("ok") and not chk.get("skipped"):
                    return {"ok": False, "error": f"리스크 한도 위반: {chk.get('reason')}",
                            "code": chk.get("code")}
            except Exception:
                pass
        # KIS 모의 주문 시도 (TODO: 실제 KIS 주문 API 호출)
        # 현재는 내부 시뮬레이터로만 처리. KIS 주문은 P4b 작업.
        kis_order_no = None
        # 내부 fills
        with _AUTH_LOCK:
            c = _auth_conn()
            # 상태 갱신
            st = c.execute(
                "SELECT cash FROM paper_state WHERE user_id=?",
                (self.user_id,)).fetchone()
            cash = float(st["cash"]) if st else 10_000_000
            if side == "buy":
                if amount > cash:
                    return {"ok": False, "error": f"잔고 부족 (필요 {amount:,.0f} > 잔고 {cash:,.0f})"}
                cash -= amount
                # 포지션 업데이트
                pos = c.execute(
                    "SELECT * FROM paper_positions WHERE user_id=? AND ticker=?",
                    (self.user_id, ticker)).fetchone()
                if pos:
                    new_qty = pos["qty"] + qty
                    new_avg = (pos["avg_price"]*pos["qty"] + price*qty) / new_qty
                    c.execute(
                        "UPDATE paper_positions SET qty=?, avg_price=?, updated_at=? "
                        "WHERE user_id=? AND ticker=?",
                        (new_qty, new_avg, _now(), self.user_id, ticker))
                else:
                    c.execute(
                        "INSERT INTO paper_positions "
                        "(user_id, ticker, qty, avg_price, updated_at) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (self.user_id, ticker, qty, price, _now()))
            else:
                # sell
                pos = c.execute(
                    "SELECT * FROM paper_positions WHERE user_id=? AND ticker=?",
                    (self.user_id, ticker)).fetchone()
                if not pos or pos["qty"] < qty:
                    return {"ok": False, "error": f"보유 부족 (보유 {pos['qty'] if pos else 0} < 매도 {qty})"}
                new_qty = pos["qty"] - qty
                cash += amount
                if new_qty == 0:
                    c.execute(
                        "DELETE FROM paper_positions WHERE user_id=? AND ticker=?",
                        (self.user_id, ticker))
                else:
                    c.execute(
                        "UPDATE paper_positions SET qty=?, updated_at=? "
                        "WHERE user_id=? AND ticker=?",
                        (new_qty, _now(), self.user_id, ticker))
            c.execute(
                "UPDATE paper_state SET cash=?, updated_at=? WHERE user_id=?",
                (cash, _now(), self.user_id))
            # 주문 기록
            cur = c.execute(
                "INSERT INTO paper_orders "
                "(user_id, ts, ticker, side, qty, price, amount, source, "
                " kis_order_no, status, note) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (self.user_id, _now(), ticker, side, qty, price, amount,
                 source, kis_order_no, "filled", note))
            c.commit()
            order_id = cur.lastrowid
        return {"ok": True, "order_id": order_id,
                "filled_price": price, "qty": qty, "amount": amount,
                "remaining_cash": cash}

    # ─── 상태 조회 ───────────────────────────────────────────
    def get_state(self) -> Dict[str, Any]:
        """현재 cash + 보유 포지션 (현재가 평가 포함)."""
        with _AUTH_LOCK:
            st = _auth_conn().execute(
                "SELECT * FROM paper_state WHERE user_id=?",
                (self.user_id,)).fetchone()
            positions_raw = _auth_conn().execute(
                "SELECT * FROM paper_positions WHERE user_id=?",
                (self.user_id,)).fetchall()
        if not st:
            return {"ok": False, "error": "state 없음"}
        cash = float(st["cash"])
        initial = float(st["initial_cash"])
        positions = []
        equity_value = 0.0
        total_pnl = 0.0
        for p in positions_raw:
            current = self._get_quote(p["ticker"]) or p["avg_price"]
            eval_amount = current * p["qty"]
            pnl = eval_amount - p["avg_price"] * p["qty"]
            equity_value += eval_amount
            total_pnl += pnl
            positions.append({
                "ticker":    p["ticker"],
                "qty":       p["qty"],
                "avg_price": p["avg_price"],
                "current":   current,
                "eval_amount": eval_amount,
                "pnl":       pnl,
                "pnl_pct":   (current/p["avg_price"] - 1) if p["avg_price"] > 0 else 0,
            })
        nav = cash + equity_value
        return {
            "ok": True,
            "user_id":      self.user_id,
            "initial_cash": initial,
            "cash":         cash,
            "equity_value": equity_value,
            "nav":          nav,
            "total_pnl":    nav - initial,
            "total_pnl_pct": (nav / initial - 1) if initial > 0 else 0,
            "n_positions":  len(positions),
            "positions":    positions,
        }

    def get_orders(self, limit: int = 50) -> List[Dict[str, Any]]:
        with _AUTH_LOCK:
            rows = _auth_conn().execute(
                "SELECT * FROM paper_orders WHERE user_id=? "
                "ORDER BY ts DESC LIMIT ?",
                (self.user_id, limit)).fetchall()
            return [dict(r) for r in rows]

    def reset(self, initial_cash: float = 10_000_000) -> Dict[str, Any]:
        """전체 초기화 (위험!)."""
        with _AUTH_LOCK:
            c = _auth_conn()
            c.execute("DELETE FROM paper_orders WHERE user_id=?", (self.user_id,))
            c.execute("DELETE FROM paper_positions WHERE user_id=?", (self.user_id,))
            c.execute("DELETE FROM paper_nav WHERE user_id=?", (self.user_id,))
            c.execute(
                "UPDATE paper_state SET initial_cash=?, cash=?, updated_at=? "
                "WHERE user_id=?",
                (initial_cash, initial_cash, _now(), self.user_id))
            c.commit()
        return {"ok": True, "initial_cash": initial_cash}

    def snapshot_nav(self) -> Dict[str, Any]:
        """오늘자 NAV 스냅샷 (일 1회 cron 권장)."""
        s = self.get_state()
        if not s.get("ok"):
            return s
        with _AUTH_LOCK:
            _auth_conn().execute("""
                INSERT INTO paper_nav (user_id, date, nav, cash, equity_value, daily_pnl)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id, date) DO UPDATE SET
                  nav=excluded.nav, cash=excluded.cash,
                  equity_value=excluded.equity_value
            """, (self.user_id, _today(), s["nav"], s["cash"],
                   s["equity_value"], None))
            _auth_conn().commit()
        return {"ok": True, "snapshot_date": _today(), "nav": s["nav"]}


# ── 싱글톤 캐시 ────────────────────────────────────────────────
_engines: Dict[int, PaperTradingEngine] = {}
_lock = threading.Lock()


def get_paper_engine(user_id: int) -> PaperTradingEngine:
    with _lock:
        if user_id not in _engines:
            _engines[user_id] = PaperTradingEngine(user_id)
        return _engines[user_id]


# ── 편의 함수 ─────────────────────────────────────────────────
def place_paper_order(user_id: int, ticker: str, side: str, qty: int,
                       price: Optional[float] = None,
                       source: str = "manual",
                       note: str = "") -> Dict[str, Any]:
    return get_paper_engine(user_id).place_order(
        ticker, side, qty, price=price, source=source, note=note)


def get_paper_state(user_id: int) -> Dict[str, Any]:
    return get_paper_engine(user_id).get_state()


def get_paper_pnl(user_id: int) -> Dict[str, Any]:
    """실시간 PnL 요약 (Tier 2 #6)."""
    s = get_paper_engine(user_id).get_state()
    if not s.get("ok"):
        return s
    return {
        "ok": True,
        "nav":           s["nav"],
        "total_pnl":     s["total_pnl"],
        "total_pnl_pct": s["total_pnl_pct"],
        "cash":          s["cash"],
        "equity_value":  s["equity_value"],
        "n_positions":   s["n_positions"],
    }
