"""
prefs.py — 사용자별 UI 환경 영구 저장
===========================================
위젯 배치 / 테마 / 폰트 / 사운드 설정 등을 서버 SQLite에 저장.
localStorage 대체 — 프로그램 재설치, 다른 PC에서도 유지.
"""
from __future__ import annotations

import datetime as dt
import json
from typing import Any, Dict, Optional

from .store import _LOCK, _conn, init_db as _auth_init

_auth_init()


def _now() -> str:
    return dt.datetime.utcnow().isoformat()


def get_prefs(user_id: int) -> Dict[str, Any]:
    """사용자 prefs 전체 dict 반환. 없으면 빈 dict."""
    with _LOCK:
        row = _conn().execute(
            "SELECT prefs_json FROM user_prefs WHERE user_id=?",
            (user_id,)).fetchone()
        if not row:
            return {}
        try:
            return json.loads(row["prefs_json"])
        except Exception:
            return {}


def save_prefs(user_id: int, prefs: Dict[str, Any]) -> Dict[str, Any]:
    """prefs 전체 덮어쓰기 (UPSERT)."""
    if not isinstance(prefs, dict):
        return {"ok": False, "error": "prefs는 dict여야 함"}
    js = json.dumps(prefs, ensure_ascii=False)
    if len(js) > 1_000_000:  # 1MB cap
        return {"ok": False, "error": "prefs 너무 큼 (1MB 초과)"}
    now = _now()
    with _LOCK:
        c = _conn()
        c.execute("""
            INSERT INTO user_prefs (user_id, prefs_json, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
              prefs_json = excluded.prefs_json,
              updated_at = excluded.updated_at
        """, (user_id, js, now))
        c.commit()
    return {"ok": True, "size": len(js)}


def patch_prefs(user_id: int, patch: Dict[str, Any]) -> Dict[str, Any]:
    """기존 prefs에 patch dict 머지 (key 단위 부분 수정)."""
    if not isinstance(patch, dict):
        return {"ok": False, "error": "patch는 dict여야 함"}
    cur = get_prefs(user_id)
    cur.update(patch)
    return save_prefs(user_id, cur)


def delete_pref_key(user_id: int, key: str) -> Dict[str, Any]:
    cur = get_prefs(user_id)
    if key in cur:
        cur.pop(key)
        return save_prefs(user_id, cur)
    return {"ok": True, "deleted": False}
