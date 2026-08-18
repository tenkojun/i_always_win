# -*- coding: utf-8 -*-
"""
로컬 사용자 id → 중앙 사용자 id 이전 (1회)
==========================================
예전에는 계정이 이 PC 의 SQLite 에 있었고, 커뮤니티 글·분석 이력·보유
종목이 **그 로컬 id** 를 가리켰다. 계정을 중앙 서버로 옮기면서 같은
사람의 id 가 달라졌다(예: 로컬 JUNHWA=1, 중앙 JUNHWA=2).

그대로 두면 내가 쓴 글과 내 분석 이력이 남의 것처럼 보이거나 아예
보이지 않는다. 그래서 **username 을 기준으로** 한 번만 다시 매핑한다.

안전장치
--------
- 중앙 서버에서 사용자 목록을 받아올 수 있을 때만 수행한다.
- 이미 수행했으면(플래그 파일) 다시 하지 않는다.
- 충돌(옮길 자리에 이미 다른 사람 id 가 있음)이 나면 그 사용자는 건너뛴다.
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any, Dict, List, Tuple

from engine.paths import DATA_DIR

_FLAG = DATA_DIR / ".user_ids_migrated"

# (테이블, user_id 컬럼) — 이 DB 안에서 사용자를 가리키는 곳
_TARGETS: List[Tuple[str, str]] = [
    ("analysis_history", "user_id"),
    ("community_posts", "user_id"),
    ("community_comments", "user_id"),
    ("portfolio_holdings", "user_id"),
    ("user_prefs", "user_id"),
]


def _table_exists(c: sqlite3.Connection, name: str) -> bool:
    r = c.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (name,)).fetchone()
    return r is not None


def migrate(central_users: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    central_users: 중앙 서버 ``/admin/users`` 응답의 사용자 목록.
    """
    if _FLAG.exists():
        return {"skipped": "이미 이전됨"}
    if not central_users:
        return {"skipped": "중앙 사용자 목록 없음"}

    from .store import _conn, _LOCK

    central_by_name = {
        str(u.get("username", "")).lower(): u.get("id")
        for u in central_users if u.get("username") and u.get("id")
    }
    if not central_by_name:
        return {"skipped": "매핑할 사용자 없음"}

    moved: Dict[str, Any] = {}
    with _LOCK:
        c = _conn()
        if not _table_exists(c, "users"):
            _FLAG.write_text("no local users table", encoding="utf-8")
            return {"skipped": "로컬 users 테이블 없음"}

        local = c.execute("SELECT id, username FROM users").fetchall()
        # 로컬 id → 중앙 id (이름이 같은 경우만)
        mapping = {}
        for row in local:
            name = str(row["username"] or "").lower()
            new_id = central_by_name.get(name)
            if new_id and int(new_id) != int(row["id"]):
                mapping[int(row["id"])] = int(new_id)

        if not mapping:
            _FLAG.write_text("nothing to remap", encoding="utf-8")
            return {"remapped": {}, "note": "옮길 대상 없음"}

        for table, col in _TARGETS:
            if not _table_exists(c, table):
                continue
            n = 0
            for old_id, new_id in mapping.items():
                cur = c.execute(
                    f"UPDATE {table} SET {col}=? WHERE {col}=?",
                    (new_id, old_id))
                n += cur.rowcount or 0
            if n:
                moved[table] = n
        c.commit()

    try:
        _FLAG.write_text(json.dumps(
            {"mapping": mapping, "rows": moved}, ensure_ascii=False),
            encoding="utf-8")
    except Exception:
        pass
    return {"remapped": mapping, "rows": moved}


def migrate_if_possible() -> Dict[str, Any]:
    """중앙 서버에 닿을 수 있으면 이전을 시도한다. 실패는 조용히 넘긴다."""
    if _FLAG.exists():
        return {"skipped": "이미 이전됨"}
    try:
        from engine import auth_remote
        if not auth_remote.load_session().get("token"):
            return {"skipped": "중앙 로그인 전"}
        r = auth_remote.admin_users()
        users = r.get("users") or []
        if not users:
            return {"skipped": "어드민 권한 없음 또는 목록 비어 있음"}
        return migrate(users)
    except Exception as e:
        return {"skipped": f"{type(e).__name__}: {e}"}
