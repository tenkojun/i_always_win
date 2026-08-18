"""
사용자 보유 종목 저장소
========================
auth.db의 portfolio_holdings 테이블 CRUD.
"""
from __future__ import annotations

import datetime as dt
from typing import Any, Dict, List, Optional

from ..auth.store import _LOCK, _conn


def _now() -> str:
    return dt.datetime.utcnow().isoformat()


def add_holding(user_id: int, ticker: str, quantity: float,
                avg_cost: float, currency: str = "USD",
                note: str = "") -> Dict[str, Any]:
    ticker = (ticker or "").strip().upper()
    if not ticker:
        return {"ok": False, "error": "ticker 누락"}
    try:
        quantity = float(quantity)
        avg_cost = float(avg_cost)
    except (ValueError, TypeError):
        return {"ok": False, "error": "quantity/avg_cost는 숫자"}
    if quantity <= 0 or avg_cost <= 0:
        return {"ok": False, "error": "quantity/avg_cost는 양수"}
    with _LOCK:
        c = _conn()
        c.execute(
            "INSERT INTO portfolio_holdings "
            "(user_id, ticker, quantity, avg_cost, currency, note, "
            " created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (user_id, ticker, quantity, avg_cost,
             currency[:8], (note or "")[:200], _now(), _now()))
        c.commit()
        rid = c.execute("SELECT last_insert_rowid()").fetchone()[0]
        return {"ok": True, "id": rid}


def update_holding(user_id: int, holding_id: int,
                   quantity: Optional[float] = None,
                   avg_cost: Optional[float] = None,
                   note: Optional[str] = None) -> Dict[str, Any]:
    with _LOCK:
        c = _conn()
        row = c.execute(
            "SELECT id FROM portfolio_holdings WHERE id=? AND user_id=?",
            (holding_id, user_id)).fetchone()
        if not row:
            return {"ok": False, "error": "권한 없음 또는 없는 항목"}
        sets, params = [], []
        if quantity is not None:
            try:
                q = float(quantity)
                if q <= 0:
                    return {"ok": False, "error": "quantity는 양수"}
                sets.append("quantity=?"); params.append(q)
            except ValueError:
                return {"ok": False, "error": "quantity 형식 오류"}
        if avg_cost is not None:
            try:
                a = float(avg_cost)
                if a <= 0:
                    return {"ok": False, "error": "avg_cost는 양수"}
                sets.append("avg_cost=?"); params.append(a)
            except ValueError:
                return {"ok": False, "error": "avg_cost 형식 오류"}
        if note is not None:
            sets.append("note=?"); params.append((note or "")[:200])
        if not sets:
            return {"ok": False, "error": "변경 사항 없음"}
        sets.append("updated_at=?"); params.append(_now())
        params.extend([holding_id, user_id])
        c.execute(
            f"UPDATE portfolio_holdings SET {', '.join(sets)} "
            f"WHERE id=? AND user_id=?", params)
        c.commit()
        return {"ok": True}


def delete_holding(user_id: int, holding_id: int) -> Dict[str, Any]:
    with _LOCK:
        c = _conn()
        c.execute(
            "DELETE FROM portfolio_holdings WHERE id=? AND user_id=?",
            (holding_id, user_id))
        c.commit()
        return {"ok": True}


def list_holdings(user_id: int) -> List[Dict[str, Any]]:
    with _LOCK:
        rows = _conn().execute(
            "SELECT * FROM portfolio_holdings WHERE user_id=? "
            "ORDER BY ticker ASC", (user_id,)).fetchall()
        return [dict(r) for r in rows]
