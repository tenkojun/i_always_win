"""
사용자별 활성 전략 저장 (auth.db.active_strategies).

차트 로딩 시 자동으로 마커 그리고, 60초마다 새 시그널 체크해 알림.
"""
from __future__ import annotations

import datetime as dt
import json
from typing import Any, Dict, List, Optional

from ..auth.store import _LOCK, _conn, init_db as _auth_init

# 첫 호출 시 스키마 ensure (active_strategies 테이블 포함)
_auth_init()


def _now() -> str:
    return dt.datetime.utcnow().isoformat()


def upsert_active(user_id: int, ticker: str, strategy: str,
                  params: Dict[str, Any],
                  expected_alpha: Optional[float] = None,
                  expected_sharpe: Optional[float] = None
                  ) -> Dict[str, Any]:
    """동일 (user,ticker,strategy)면 갱신."""
    with _LOCK:
        c = _conn()
        try:
            c.execute("""
                INSERT INTO active_strategies
                  (user_id, ticker, strategy, params_json,
                   expected_alpha, expected_sharpe, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id, ticker, strategy)
                DO UPDATE SET params_json=excluded.params_json,
                              expected_alpha=excluded.expected_alpha,
                              expected_sharpe=excluded.expected_sharpe
            """, (user_id, ticker.upper(), strategy,
                  json.dumps(params), expected_alpha, expected_sharpe,
                  _now()))
            c.commit()
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}


def list_active(user_id: int,
                ticker: Optional[str] = None) -> List[Dict[str, Any]]:
    with _LOCK:
        c = _conn()
        if ticker:
            rows = c.execute(
                "SELECT * FROM active_strategies "
                "WHERE user_id=? AND ticker=? "
                "ORDER BY created_at DESC",
                (user_id, ticker.upper())).fetchall()
        else:
            rows = c.execute(
                "SELECT * FROM active_strategies WHERE user_id=? "
                "ORDER BY created_at DESC", (user_id,)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            try:
                d["params"] = json.loads(d.pop("params_json"))
            except Exception:
                d["params"] = {}
            out.append(d)
        return out


def delete_active(user_id: int, strategy_id: int) -> Dict[str, Any]:
    with _LOCK:
        c = _conn()
        c.execute("DELETE FROM active_strategies WHERE id=? AND user_id=?",
                  (strategy_id, user_id))
        c.commit()
        return {"ok": True}


def mark_signal(strategy_id: int, ts: str, kind: str) -> None:
    with _LOCK:
        c = _conn()
        c.execute(
            "UPDATE active_strategies "
            "SET last_signal_ts=?, last_signal_kind=? WHERE id=?",
            (ts, kind, strategy_id))
        c.commit()
