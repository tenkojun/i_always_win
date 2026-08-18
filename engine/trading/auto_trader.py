"""
auto_trader.py — 컬렉션 전략 자동매매 (Paper Trading 모드)
============================================================
사용자가 토글 ON → 컬렉션의 활성 전략들이 자동으로 시그널 → 주문.

작동 흐름:
1. 컬렉션에서 active=True 표시된 전략 fetch
2. 각 전략의 최신 시그널 (run_backtest → entry_signals/exit_signals 마지막)
3. 시그널 발생 시 → check_order (risk_limits) → place_order (paper)
4. 모든 주문에 source = strategy_name 명시

폴링: 1분마다 (장중에만 / 사용자 설정)
"""
from __future__ import annotations

import datetime as dt
import json
import threading
import time
from typing import Any, Dict, List, Optional

from ..auth.store import _LOCK as _AUTH_LOCK, _conn as _auth_conn, init_db


_SCHEMA = """
CREATE TABLE IF NOT EXISTS auto_trader_state (
  user_id     INTEGER PRIMARY KEY,
  enabled     INTEGER NOT NULL DEFAULT 0,
  poll_sec    INTEGER NOT NULL DEFAULT 60,
  strategies_json TEXT,         -- 자동매매 대상 전략 IDs (JSON list)
  last_run    TEXT,
  log_json    TEXT,             -- 최근 활동 로그
  updated_at  TEXT NOT NULL
);
"""


def _init_at_db():
    init_db()
    with _AUTH_LOCK:
        _auth_conn().executescript(_SCHEMA)
        _auth_conn().commit()


_init_at_db()


def _now():
    return dt.datetime.utcnow().isoformat()


def get_state(user_id: int) -> Dict[str, Any]:
    with _AUTH_LOCK:
        row = _auth_conn().execute(
            "SELECT * FROM auto_trader_state WHERE user_id=?",
            (user_id,)).fetchone()
    if not row:
        return {"enabled": False, "poll_sec": 60, "strategies": [],
                 "last_run": None, "log": []}
    d = dict(row)
    try: d["strategies"] = json.loads(d.pop("strategies_json") or "[]")
    except Exception: d["strategies"] = []
    try: d["log"] = json.loads(d.pop("log_json") or "[]")
    except Exception: d["log"] = []
    d["enabled"] = bool(d.get("enabled"))
    return d


def save_state(user_id: int, enabled: bool,
                strategies: List[int],
                poll_sec: int = 60) -> Dict[str, Any]:
    with _AUTH_LOCK:
        _auth_conn().execute("""
            INSERT INTO auto_trader_state
              (user_id, enabled, poll_sec, strategies_json, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
              enabled=excluded.enabled,
              poll_sec=excluded.poll_sec,
              strategies_json=excluded.strategies_json,
              updated_at=excluded.updated_at
        """, (user_id, 1 if enabled else 0, int(poll_sec),
              json.dumps(strategies), _now()))
        _auth_conn().commit()
    return {"ok": True, "enabled": enabled,
            "strategies": strategies, "poll_sec": poll_sec}


def _append_log(user_id: int, entry: Dict[str, Any]):
    s = get_state(user_id)
    log = s.get("log", []) + [{**entry, "ts": _now()}]
    log = log[-100:]   # 최근 100건만
    with _AUTH_LOCK:
        _auth_conn().execute(
            "UPDATE auto_trader_state SET log_json=?, last_run=?, updated_at=? "
            "WHERE user_id=?",
            (json.dumps(log, ensure_ascii=False, default=str),
             _now(), _now(), user_id))
        _auth_conn().commit()


def run_once(user_id: int) -> Dict[str, Any]:
    """1회 실행 — 컬렉션의 활성 전략들의 최신 시그널 평가 + 주문."""
    s = get_state(user_id)
    if not s.get("enabled"):
        return {"ok": False, "skipped": True, "reason": "비활성"}
    strategy_ids = s.get("strategies", [])
    if not strategy_ids:
        return {"ok": True, "n_strategies": 0, "msg": "지정된 전략 없음"}

    from ..strategy.collection_store import list_collection
    from ..strategy.vbt_runner import run_backtest
    from .paper_trading import get_paper_engine

    items = list_collection(user_id)
    targets = [it for it in items if it.get("id") in strategy_ids]
    eng = get_paper_engine(user_id)
    actions = []
    for it in targets:
        try:
            r = run_backtest(
                ticker=it.get("ticker") or "AAPL",
                strategy=it.get("strategy"),
                params=it.get("params") or {},
                period_days=180,
                next_bar_exec=True,  # 룩어헤드 fix
            )
            if not r.get("ok"):
                actions.append({"ticker": it.get("ticker"),
                                 "strategy": it.get("strategy"),
                                 "action": "skip", "reason": r.get("error")})
                continue
            # 최신 entry/exit 시그널 확인 (마지막 5일 이내)
            entries = r.get("entry_signals") or []
            exits = r.get("exit_signals") or []
            today = dt.date.today()
            recent_entry = any(
                (today - dt.datetime.strptime(d, "%Y-%m-%d").date()).days <= 2
                for d in entries)
            recent_exit = any(
                (today - dt.datetime.strptime(d, "%Y-%m-%d").date()).days <= 2
                for d in exits)
            # 동작 결정
            tk = (it.get("ticker") or "AAPL").upper()
            if recent_exit:
                # 보유 중이면 청산
                state = eng.get_state()
                pos = next((p for p in state.get("positions", [])
                             if p["ticker"] == tk), None)
                if pos and pos["qty"] > 0:
                    res = eng.place_order(tk, "sell", pos["qty"],
                                           source=f"auto:{it.get('strategy')}")
                    actions.append({"ticker": tk,
                                     "strategy": it.get("strategy"),
                                     "action": "sell", "result": res})
                    continue
            if recent_entry:
                # 미보유 시 매수
                state = eng.get_state()
                pos = next((p for p in state.get("positions", [])
                             if p["ticker"] == tk), None)
                if not pos:
                    # 가용 현금의 10%만 사용 (단순 룰)
                    cash = state["cash"]
                    price = eng._get_quote(tk)
                    if price and cash > 0:
                        qty = int((cash * 0.10) / price)
                        if qty >= 1:
                            res = eng.place_order(
                                tk, "buy", qty,
                                source=f"auto:{it.get('strategy')}")
                            actions.append({"ticker": tk,
                                             "strategy": it.get("strategy"),
                                             "action": "buy",
                                             "qty": qty,
                                             "result": res})
                            continue
            actions.append({"ticker": tk,
                             "strategy": it.get("strategy"),
                             "action": "hold"})
        except Exception as e:
            actions.append({"ticker": it.get("ticker"),
                             "strategy": it.get("strategy"),
                             "action": "error",
                             "error": str(e)[:120]})
    # 로그 저장
    summary = {
        "n_targets": len(targets),
        "n_buy":  sum(1 for a in actions if a["action"] == "buy"),
        "n_sell": sum(1 for a in actions if a["action"] == "sell"),
        "n_hold": sum(1 for a in actions if a["action"] == "hold"),
        "n_err":  sum(1 for a in actions if a["action"] == "error"),
    }
    _append_log(user_id, {"summary": summary, "actions": actions[:30]})
    return {"ok": True, "summary": summary, "actions": actions}


# ════════════════════════════════════════════════════════════
#  스케줄러 — 백그라운드 thread로 enabled 사용자 모두 1분/5분 폴링
# ════════════════════════════════════════════════════════════
_scheduler_thread = None
_scheduler_stop = False


def _scheduler_loop():
    """모든 사용자 중 enabled인 사람의 auto_trader를 polling.
    poll_sec 단위로 run_once 호출. 1초마다 enabled 체크.
    """
    global _scheduler_stop
    last_run_by_user: Dict[int, float] = {}
    import time
    while not _scheduler_stop:
        try:
            with _AUTH_LOCK:
                rows = _auth_conn().execute(
                    "SELECT user_id, poll_sec FROM auto_trader_state "
                    "WHERE enabled=1").fetchall()
            now = time.time()
            for r in rows:
                uid = r["user_id"]
                poll_sec = int(r["poll_sec"] or 60)
                if now - last_run_by_user.get(uid, 0) >= poll_sec:
                    try:
                        run_once(uid)
                    except Exception:
                        pass
                    last_run_by_user[uid] = now
        except Exception:
            pass
        time.sleep(5)   # 5초마다 enabled 체크 (낮은 부하)


def start_scheduler() -> Dict[str, Any]:
    """전역 스케줄러 시작 (서버 부팅 시 1회 호출)."""
    global _scheduler_thread, _scheduler_stop
    if _scheduler_thread and _scheduler_thread.is_alive():
        return {"ok": True, "already_running": True}
    _scheduler_stop = False
    _scheduler_thread = threading.Thread(
        target=_scheduler_loop, name="auto-trader-scheduler", daemon=True)
    _scheduler_thread.start()
    return {"ok": True, "started": True}


def stop_scheduler() -> Dict[str, Any]:
    global _scheduler_stop
    _scheduler_stop = True
    return {"ok": True}


# 서버 부팅 시 자동 시작
try:
    start_scheduler()
except Exception:
    pass
