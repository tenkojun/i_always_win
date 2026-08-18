"""
커뮤니티 게시판 — SQLite store (C4)
"""
from __future__ import annotations

import datetime as dt
from typing import Any, Dict, List, Optional

# auth와 동일 DB 사용
from engine.auth.store import _conn, _LOCK


_SCHEMA = """
CREATE TABLE IF NOT EXISTS community_posts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL,
  title TEXT NOT NULL,
  body TEXT NOT NULL,
  pinned INTEGER NOT NULL DEFAULT 0,
  views INTEGER NOT NULL DEFAULT 0,
  attached_strategy_json TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_cp_created
  ON community_posts(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_cp_pinned
  ON community_posts(pinned DESC);

CREATE TABLE IF NOT EXISTS community_comments (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  post_id INTEGER NOT NULL,
  user_id INTEGER NOT NULL,
  body TEXT NOT NULL,
  created_at TEXT NOT NULL,
  FOREIGN KEY (post_id) REFERENCES community_posts(id) ON DELETE CASCADE,
  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_cc_post ON community_comments(post_id);
"""


def _now() -> str:
    return dt.datetime.utcnow().isoformat()


def init_community_db() -> None:
    """C4 테이블 생성. 반복 호출 안전. P7 컬럼 자동 마이그레이션."""
    with _LOCK:
        _conn().executescript(_SCHEMA)
        # P7: 기존 DB에 attached_strategy_json 없으면 ALTER TABLE
        try:
            cols = _conn().execute(
                "PRAGMA table_info(community_posts)").fetchall()
            names = {c["name"] if hasattr(c, "keys") else c[1] for c in cols}
            if "attached_strategy_json" not in names:
                _conn().execute(
                    "ALTER TABLE community_posts "
                    "ADD COLUMN attached_strategy_json TEXT")
        except Exception:
            pass
        _conn().commit()


def _row(r) -> Dict[str, Any]:
    if r is None:
        return None
    return dict(r)


# ── posts CRUD ───────────────────────────────────────────────
def list_posts(limit: int = 50, offset: int = 0) -> List[Dict[str, Any]]:
    """글 목록 — pinned 먼저, 그 다음 최신순. user/comment count 조인.
    P7: has_strategy 플래그도 함께 반환."""
    sql = """
    SELECT
      p.id, p.title, p.pinned, p.views, p.created_at, p.updated_at,
      u.username, u.nickname, u.role,
      (SELECT COUNT(*) FROM community_comments c WHERE c.post_id=p.id)
        AS comment_count,
      substr(p.body, 1, 120) AS preview,
      CASE WHEN p.attached_strategy_json IS NOT NULL
                AND p.attached_strategy_json != ''
           THEN 1 ELSE 0 END AS has_strategy
    FROM community_posts p
    JOIN users u ON u.id = p.user_id
    ORDER BY p.pinned DESC, p.created_at DESC
    LIMIT ? OFFSET ?
    """
    with _LOCK:
        rows = _conn().execute(sql, (limit, offset)).fetchall()
        return [_row(r) for r in rows]


def get_post(post_id: int, inc_view: bool = True
             ) -> Optional[Dict[str, Any]]:
    """글 상세 + 조회수 +1 (선택)."""
    with _LOCK:
        c = _conn()
        if inc_view:
            c.execute(
                "UPDATE community_posts SET views = views + 1 WHERE id=?",
                (post_id,))
            c.commit()
        row = c.execute(
            "SELECT p.*, u.username, u.nickname, u.role "
            "FROM community_posts p JOIN users u ON u.id = p.user_id "
            "WHERE p.id=?", (post_id,)).fetchone()
        return _row(row)


def create_post(user_id: int, title: str, body: str,
                pinned: bool = False,
                attached_strategy: Optional[Dict[str, Any]] = None
                ) -> Dict[str, Any]:
    """글 작성. pinned는 어드민만 (라우트에서 검증).
    P7: attached_strategy = {name, strategy, params, ticker?, metrics?, source?}"""
    import json as _json
    title = (title or "").strip()[:200]
    body = (body or "").strip()
    if len(title) < 2:
        return {"ok": False, "error": "제목은 최소 2자."}
    if len(body) < 2:
        return {"ok": False, "error": "본문은 최소 2자."}
    now = _now()
    att_json = None
    if attached_strategy and isinstance(attached_strategy, dict):
        # 보안: 허용 키만 추출
        allow = {"name","strategy","params","ticker","metrics","source","notes"}
        clean = {k: v for k, v in attached_strategy.items() if k in allow}
        if clean.get("strategy"):
            att_json = _json.dumps(clean, ensure_ascii=False)
    with _LOCK:
        c = _conn()
        cur = c.execute(
            "INSERT INTO community_posts "
            "(user_id, title, body, pinned, views, "
            " attached_strategy_json, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, 0, ?, ?, ?)",
            (user_id, title, body, 1 if pinned else 0,
             att_json, now, now))
        c.commit()
        return {"ok": True, "id": cur.lastrowid,
                "has_strategy": att_json is not None}


def delete_post(post_id: int, user_id: int,
                is_admin: bool = False) -> Dict[str, Any]:
    """글 삭제. 어드민은 모든 글, 사용자는 자기 글만."""
    with _LOCK:
        c = _conn()
        row = c.execute(
            "SELECT user_id FROM community_posts WHERE id=?",
            (post_id,)).fetchone()
        if not row:
            return {"ok": False, "error": "글 없음"}
        if not is_admin and row["user_id"] != user_id:
            return {"ok": False, "error": "삭제 권한 없음"}
        c.execute("DELETE FROM community_posts WHERE id=?", (post_id,))
        c.commit()
        return {"ok": True}


# ── comments CRUD ────────────────────────────────────────────
def list_comments(post_id: int) -> List[Dict[str, Any]]:
    sql = """
    SELECT
      c.id, c.body, c.created_at, c.user_id,
      u.username, u.nickname, u.role
    FROM community_comments c
    JOIN users u ON u.id = c.user_id
    WHERE c.post_id=?
    ORDER BY c.created_at ASC
    """
    with _LOCK:
        rows = _conn().execute(sql, (post_id,)).fetchall()
        return [_row(r) for r in rows]


def create_comment(post_id: int, user_id: int,
                   body: str) -> Dict[str, Any]:
    body = (body or "").strip()[:2000]
    if len(body) < 1:
        return {"ok": False, "error": "댓글 내용이 필요합니다."}
    with _LOCK:
        c = _conn()
        # post 존재 확인
        if not c.execute(
                "SELECT 1 FROM community_posts WHERE id=?",
                (post_id,)).fetchone():
            return {"ok": False, "error": "글이 존재하지 않습니다."}
        cur = c.execute(
            "INSERT INTO community_comments "
            "(post_id, user_id, body, created_at) VALUES (?, ?, ?, ?)",
            (post_id, user_id, body, _now()))
        c.commit()
        return {"ok": True, "id": cur.lastrowid}


def delete_comment(comment_id: int, user_id: int,
                   is_admin: bool = False) -> Dict[str, Any]:
    with _LOCK:
        c = _conn()
        row = c.execute(
            "SELECT user_id FROM community_comments WHERE id=?",
            (comment_id,)).fetchone()
        if not row:
            return {"ok": False, "error": "댓글 없음"}
        if not is_admin and row["user_id"] != user_id:
            return {"ok": False, "error": "삭제 권한 없음"}
        c.execute("DELETE FROM community_comments WHERE id=?",
                  (comment_id,))
        c.commit()
        return {"ok": True}
