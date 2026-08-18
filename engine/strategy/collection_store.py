"""
collection_store.py — C 그룹 #11: 사용자 전략 컬렉션
======================================================
즐겨찾기 시스템. 분석/백테스트 결과에서 ⭐ 클릭으로 저장.
active_strategies와 분리 — 컬렉션은 "북마크" 개념, active는 "실시간 알림".

스키마 (auth.db.strategy_collection):
  id, user_id, name, ticker, strategy, params_json, notes, source,
  metrics_json (Sharpe/return/MDD 캐시), created_at
"""
from __future__ import annotations

import datetime as dt
import json
from typing import Any, Dict, List, Optional

from ..auth.store import _LOCK, _conn, init_db as _auth_init

_auth_init()


def _now() -> str:
    return dt.datetime.utcnow().isoformat()


def add_to_collection(user_id: int,
                      name: str,
                      strategy: str,
                      params: Dict[str, Any],
                      ticker: Optional[str] = None,
                      notes: Optional[str] = None,
                      source: Optional[str] = None,
                      metrics: Optional[Dict[str, Any]] = None
                      ) -> Dict[str, Any]:
    """컬렉션에 신규 추가 (중복 허용 — 같은 전략을 다른 이름으로 저장 가능)."""
    if not name or not name.strip():
        return {"ok": False, "error": "name 필요"}
    if not strategy:
        return {"ok": False, "error": "strategy 필요"}
    with _LOCK:
        c = _conn()
        try:
            cur = c.execute("""
                INSERT INTO strategy_collection
                  (user_id, name, ticker, strategy, params_json,
                   notes, source, metrics_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (user_id, name.strip(),
                  ticker.upper() if ticker else None,
                  strategy,
                  json.dumps(params or {}),
                  notes, source,
                  json.dumps(metrics or {}),
                  _now()))
            c.commit()
            return {"ok": True, "id": cur.lastrowid}
        except Exception as e:
            return {"ok": False, "error": str(e)}


def list_collection(user_id: int,
                    ticker: Optional[str] = None,
                    limit: int = 200) -> List[Dict[str, Any]]:
    with _LOCK:
        c = _conn()
        if ticker:
            rows = c.execute(
                "SELECT * FROM strategy_collection "
                "WHERE user_id=? AND ticker=? "
                "ORDER BY created_at DESC LIMIT ?",
                (user_id, ticker.upper(), limit)).fetchall()
        else:
            rows = c.execute(
                "SELECT * FROM strategy_collection WHERE user_id=? "
                "ORDER BY created_at DESC LIMIT ?",
                (user_id, limit)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            try:
                d["params"] = json.loads(d.pop("params_json"))
            except Exception:
                d["params"] = {}
            try:
                d["metrics"] = json.loads(d.pop("metrics_json") or "{}")
            except Exception:
                d["metrics"] = {}
            out.append(d)
        return out


def delete_from_collection(user_id: int, item_id: int) -> Dict[str, Any]:
    with _LOCK:
        c = _conn()
        c.execute("DELETE FROM strategy_collection "
                  "WHERE id=? AND user_id=?",
                  (item_id, user_id))
        c.commit()
        return {"ok": True}


def rename_collection_item(user_id: int, item_id: int,
                           name: str,
                           notes: Optional[str] = None) -> Dict[str, Any]:
    """이름/메모만 수정."""
    if not name or not name.strip():
        return {"ok": False, "error": "name 필요"}
    with _LOCK:
        c = _conn()
        try:
            if notes is not None:
                c.execute("UPDATE strategy_collection SET name=?, notes=? "
                          "WHERE id=? AND user_id=?",
                          (name.strip(), notes, item_id, user_id))
            else:
                c.execute("UPDATE strategy_collection SET name=? "
                          "WHERE id=? AND user_id=?",
                          (name.strip(), item_id, user_id))
            c.commit()
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}
