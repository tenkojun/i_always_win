"""
SQLite 저장소 — 사용자/세션/Claude 쿼터/메인 PC
=================================================
모든 DB I/O는 이 모듈을 통해서만 — 다른 모듈은 ORM이나 SQL을 직접 쓰지 말 것.

위치: .data/auth.db  (사용자 홈, chmod 0600 시도)
"""
from __future__ import annotations

import datetime as dt
import os
import sqlite3
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from .security import hash_password, gen_token

# ── 초기 어드민 시드 (첫 실행 시 자동 생성) ────────────────────
# 비밀번호를 코드에 박아 두면 저장소를 읽은 사람이 그대로 들어온다.
# 첫 실행 때 무작위로 만들어 .data/ADMIN_PASSWORD.txt 에 1회 기록하고,
# 콘솔에도 한 번 찍는다. 사용자가 확인 후 그 파일을 지우면 된다.
# 환경변수 IAW_ADMIN_PASSWORD 가 있으면 그 값을 쓴다.
ADMIN_USERNAME = os.environ.get("IAW_ADMIN_USERNAME", "JUNHWA")

# ── DB 파일 경로 ─────────────────────────────────────────────────
from engine.paths import DATA_DIR as _DB_DIR, AUTH_DB as _DB_PATH

# SQLite는 멀티스레드에서 단일 connection 공유 시 안전하지 않음 → Lock
_LOCK = threading.RLock()
_CONN: Optional[sqlite3.Connection] = None

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  username TEXT UNIQUE NOT NULL COLLATE NOCASE,
  password_hash TEXT NOT NULL,
  salt TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  role TEXT NOT NULL DEFAULT 'user',
  main_pc_id TEXT,
  main_pc_label TEXT,
  main_pc_last_seen TEXT,
  claude_used INTEGER NOT NULL DEFAULT 0,
  claude_quota_date TEXT,
  created_at TEXT NOT NULL,
  approved_at TEXT,
  approved_by INTEGER
);

CREATE TABLE IF NOT EXISTS sessions (
  token TEXT PRIMARY KEY,
  user_id INTEGER NOT NULL,
  created_at TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  device_label TEXT,
  FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_users_status ON users(status);

CREATE TABLE IF NOT EXISTS portfolio_holdings (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL,
  ticker TEXT NOT NULL,
  quantity REAL NOT NULL,
  avg_cost REAL NOT NULL,
  currency TEXT NOT NULL DEFAULT 'USD',
  note TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_holdings_user ON portfolio_holdings(user_id);
CREATE INDEX IF NOT EXISTS idx_holdings_ticker ON portfolio_holdings(ticker);

CREATE TABLE IF NOT EXISTS active_strategies (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL,
  ticker TEXT NOT NULL,
  strategy TEXT NOT NULL,
  params_json TEXT NOT NULL,
  expected_alpha REAL,
  expected_sharpe REAL,
  last_signal_ts TEXT,
  last_signal_kind TEXT,
  created_at TEXT NOT NULL,
  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
  UNIQUE(user_id, ticker, strategy)
);

CREATE INDEX IF NOT EXISTS idx_active_user
  ON active_strategies(user_id, ticker);

-- C 그룹 #11: 사용자 즐겨찾기 전략 컬렉션
CREATE TABLE IF NOT EXISTS strategy_collection (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL,
  name TEXT NOT NULL,
  ticker TEXT,
  strategy TEXT NOT NULL,
  params_json TEXT NOT NULL,
  notes TEXT,
  source TEXT,
  metrics_json TEXT,
  created_at TEXT NOT NULL,
  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_collection_user
  ON strategy_collection(user_id, created_at DESC);

-- 사용자별 UI 환경 영구 저장 (위젯 배치 / 테마 / 폰트 등)
-- prefs_json은 자유 dict, key별 namespace로 사용
CREATE TABLE IF NOT EXISTS user_prefs (
  user_id    INTEGER PRIMARY KEY,
  prefs_json TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);
"""


def _now() -> str:
    return dt.datetime.utcnow().isoformat()


def _today() -> str:
    return dt.date.today().isoformat()


def _conn() -> sqlite3.Connection:
    global _CONN
    if _CONN is None:
        _DB_DIR.mkdir(parents=True, exist_ok=True)
        _CONN = sqlite3.connect(str(_DB_PATH), check_same_thread=False,
                                 timeout=15.0)
        _CONN.row_factory = sqlite3.Row
        # PRAGMA들은 best-effort — 실패해도 connection 자체는 사용
        for pragma in (
            "PRAGMA journal_mode=WAL",
            "PRAGMA busy_timeout=10000",
            "PRAGMA foreign_keys=ON",
            "PRAGMA synchronous=NORMAL",
        ):
            try:
                _CONN.execute(pragma)
            except Exception:
                pass
        # 권한 0600 시도 (Windows에서는 무시됨)
        try:
            os.chmod(_DB_PATH, 0o600)
        except Exception:
            pass
    return _CONN


def _migrate_v2(c: sqlite3.Connection) -> None:
    """C6+닉네임 마이그레이션 — login_count/last_login_at/nickname 추가."""
    cols = {r["name"] for r in
            c.execute("PRAGMA table_info(users)").fetchall()}
    if "login_count" not in cols:
        c.execute("ALTER TABLE users ADD COLUMN "
                  "login_count INTEGER NOT NULL DEFAULT 0")
    if "last_login_at" not in cols:
        c.execute("ALTER TABLE users ADD COLUMN last_login_at TEXT")
    if "nickname" not in cols:
        c.execute("ALTER TABLE users ADD COLUMN nickname TEXT")
    c.commit()


def init_db() -> Dict[str, Any]:
    """스키마 생성 + 초기 어드민 seed. 반복 호출 안전."""
    with _LOCK:
        c = _conn()
        c.executescript(_SCHEMA)
        c.commit()
        _migrate_v2(c)
        # 어드민 seed
        cur = c.execute("SELECT id, status FROM users WHERE username=?",
                        (ADMIN_USERNAME,))
        row = cur.fetchone()
        if row is None:
            pw = _initial_admin_password()
            h, s = hash_password(pw)
            c.execute(
                "INSERT INTO users (username, password_hash, salt, "
                "status, role, created_at, approved_at) "
                "VALUES (?, ?, ?, 'active', 'admin', ?, ?)",
                (ADMIN_USERNAME, h, s, _now(), _now()))
            c.commit()
            _announce_admin_password(pw)
            return {"admin_seeded": True}
        elif row["status"] != "active":
            # 어드민이 어쩌다 비활성화돼있으면 강제 활성화
            c.execute("UPDATE users SET status='active', role='admin' "
                      "WHERE id=?", (row["id"],))
            c.commit()
            return {"admin_reactivated": True}
        return {"admin_seeded": False}


# ── 초기 어드민 비밀번호 ──────────────────────────────────────────
def _initial_admin_password() -> str:
    """환경변수가 있으면 그 값, 없으면 무작위 16자."""
    env = os.environ.get("IAW_ADMIN_PASSWORD", "").strip()
    if env:
        return env
    import secrets
    import string
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(16))


def _announce_admin_password(pw: str) -> None:
    """
    딱 한 번, 사람이 읽을 수 있게 남긴다.
    환경변수로 직접 지정한 경우에는 굳이 파일로 흘리지 않는다.
    """
    if os.environ.get("IAW_ADMIN_PASSWORD", "").strip():
        return
    from engine.paths import DATA_DIR
    path = DATA_DIR / "ADMIN_PASSWORD.txt"
    try:
        path.write_text(
            "I ALWAYS WIN — 초기 어드민 계정\n"
            "================================\n"
            f"아이디   : {ADMIN_USERNAME}\n"
            f"비밀번호 : {pw}\n\n"
            "첫 로그인 후 설정에서 비밀번호를 바꾸고 이 파일을 지우세요.\n",
            encoding="utf-8")
        try:
            os.chmod(path, 0o600)
        except Exception:
            pass
    except Exception:
        pass
    print("=" * 60)
    print("  초기 어드민 계정이 생성되었습니다")
    print(f"    아이디   : {ADMIN_USERNAME}")
    print(f"    비밀번호 : {pw}")
    print(f"    (사본: {path})")
    print("  첫 로그인 후 반드시 변경하세요.")
    print("=" * 60)


# ── 사용자 CRUD ───────────────────────────────────────────────────
def create_user(username: str, password: str,
                role: str = "user",
                status: str = "pending",
                nickname: str = "") -> Dict[str, Any]:
    """신규 사용자 생성. 중복이면 None 반환."""
    username = (username or "").strip()
    nickname = (nickname or "").strip()[:40]
    if not username or len(username) < 3:
        return {"ok": False, "error": "username은 최소 3자입니다."}
    if not password or len(password) < 6:
        return {"ok": False, "error": "패스워드는 최소 6자입니다."}
    if nickname and len(nickname) < 2:
        return {"ok": False, "error": "닉네임은 최소 2자입니다."}
    try:
        h, s = hash_password(password)
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    with _LOCK:
        c = _conn()
        try:
            c.execute(
                "INSERT INTO users (username, password_hash, salt, "
                "status, role, created_at, nickname) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (username, h, s, status, role, _now(),
                 nickname or username))
            c.commit()
            row = c.execute("SELECT * FROM users WHERE username=?",
                            (username,)).fetchone()
            return {"ok": True, "user": _row_to_dict(row)}
        except sqlite3.IntegrityError:
            return {"ok": False, "error": "이미 존재하는 username입니다."}


def get_user_by_name(username: str) -> Optional[Dict[str, Any]]:
    with _LOCK:
        row = _conn().execute(
            "SELECT * FROM users WHERE username=? COLLATE NOCASE",
            (username,)).fetchone()
        return _row_to_dict(row) if row else None


def get_user_by_id(user_id: int) -> Optional[Dict[str, Any]]:
    with _LOCK:
        row = _conn().execute("SELECT * FROM users WHERE id=?",
                              (user_id,)).fetchone()
        return _row_to_dict(row) if row else None


def list_pending_users() -> List[Dict[str, Any]]:
    with _LOCK:
        rows = _conn().execute(
            "SELECT * FROM users WHERE status='pending' "
            "ORDER BY created_at ASC").fetchall()
        return [_row_to_dict(r) for r in rows]


def list_all_users() -> List[Dict[str, Any]]:
    with _LOCK:
        rows = _conn().execute(
            "SELECT * FROM users ORDER BY created_at DESC").fetchall()
        return [_row_to_dict(r) for r in rows]


def approve_user(user_id: int,
                 approver_id: int) -> Dict[str, Any]:
    with _LOCK:
        c = _conn()
        r = c.execute("SELECT id, status FROM users WHERE id=?",
                      (user_id,)).fetchone()
        if not r:
            return {"ok": False, "error": "사용자 없음"}
        c.execute("UPDATE users SET status='active', approved_at=?, "
                  "approved_by=? WHERE id=?",
                  (_now(), approver_id, user_id))
        c.commit()
        return {"ok": True}


def reject_user(user_id: int) -> Dict[str, Any]:
    with _LOCK:
        c = _conn()
        c.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
        c.execute("UPDATE users SET status='rejected' WHERE id=?",
                  (user_id,))
        c.commit()
        return {"ok": True}


# ── 세션 CRUD ─────────────────────────────────────────────────────
def create_session(user_id: int, device_label: str = "",
                   ttl_days: int = 30) -> str:
    token = gen_token(32)
    exp = (dt.datetime.utcnow()
           + dt.timedelta(days=ttl_days)).isoformat()
    with _LOCK:
        c = _conn()
        c.execute(
            "INSERT INTO sessions (token, user_id, created_at, "
            "expires_at, device_label) VALUES (?, ?, ?, ?, ?)",
            (token, user_id, _now(), exp, device_label[:80]))
        # C6: 로그인 통계 갱신
        c.execute(
            "UPDATE users SET login_count = COALESCE(login_count,0) + 1, "
            "last_login_at = ? WHERE id = ?",
            (_now(), user_id))
        c.commit()
    return token


def get_session(token: str) -> Optional[Dict[str, Any]]:
    if not token:
        return None
    with _LOCK:
        row = _conn().execute(
            "SELECT s.token, s.user_id, s.expires_at, s.device_label, "
            "u.username, u.status, u.role "
            "FROM sessions s JOIN users u ON u.id = s.user_id "
            "WHERE s.token=?", (token,)).fetchone()
        if not row:
            return None
        # 만료 체크
        try:
            exp = dt.datetime.fromisoformat(row["expires_at"])
            if exp < dt.datetime.utcnow():
                delete_session(token)
                return None
        except Exception:
            return None
        return dict(row)


def delete_session(token: str) -> None:
    with _LOCK:
        _conn().execute("DELETE FROM sessions WHERE token=?", (token,))
        _conn().commit()


# ── Claude 쿼터 ───────────────────────────────────────────────────
def reset_claude_quota_if_needed(user_id: int) -> None:
    """자정 지나면 자동 리셋."""
    with _LOCK:
        c = _conn()
        row = c.execute("SELECT claude_quota_date FROM users WHERE id=?",
                        (user_id,)).fetchone()
        if not row:
            return
        if row["claude_quota_date"] != _today():
            c.execute(
                "UPDATE users SET claude_used=0, claude_quota_date=? "
                "WHERE id=?", (_today(), user_id))
            c.commit()


def check_claude_quota(user_id: int,
                       limit: int = 10) -> Dict[str, Any]:
    """잔여 횟수 조회 (소비 안 함). {used, remaining, limit, date}."""
    reset_claude_quota_if_needed(user_id)
    with _LOCK:
        row = _conn().execute(
            "SELECT claude_used FROM users WHERE id=?",
            (user_id,)).fetchone()
        used = (row["claude_used"] if row else 0) or 0
        return {"used": used, "remaining": max(0, limit - used),
                "limit": limit, "date": _today()}


def consume_claude_quota(user_id: int,
                         limit: int = 10) -> Dict[str, Any]:
    """1회 소비 시도. {ok, remaining, ...}"""
    reset_claude_quota_if_needed(user_id)
    with _LOCK:
        c = _conn()
        row = c.execute("SELECT claude_used FROM users WHERE id=?",
                        (user_id,)).fetchone()
        used = (row["claude_used"] if row else 0) or 0
        if used >= limit:
            return {"ok": False, "used": used, "remaining": 0,
                    "limit": limit, "error":
                    f"오늘 사용량 한도({limit}회) 도달 — 자정에 리셋"}
        c.execute("UPDATE users SET claude_used=claude_used+1, "
                  "claude_quota_date=? WHERE id=?",
                  (_today(), user_id))
        c.commit()
        return {"ok": True, "used": used + 1,
                "remaining": limit - used - 1, "limit": limit}


def reset_claude_quota(user_id: int) -> None:
    """어드민 수동 리셋."""
    with _LOCK:
        _conn().execute(
            "UPDATE users SET claude_used=0, claude_quota_date=? "
            "WHERE id=?", (_today(), user_id))
        _conn().commit()


# ── 메인 PC 매핑 ─────────────────────────────────────────────────
def set_main_pc(user_id: int, pc_id: str,
                pc_label: str = "") -> None:
    """이 사용자의 메인 PC를 지정. 기존 매핑은 덮어쓴다."""
    with _LOCK:
        _conn().execute(
            "UPDATE users SET main_pc_id=?, main_pc_label=?, "
            "main_pc_last_seen=? WHERE id=?",
            (pc_id, pc_label[:80], _now(), user_id))
        _conn().commit()


def get_main_pc(user_id: int) -> Optional[Dict[str, Any]]:
    with _LOCK:
        row = _conn().execute(
            "SELECT main_pc_id, main_pc_label, main_pc_last_seen "
            "FROM users WHERE id=?", (user_id,)).fetchone()
        if not row or not row["main_pc_id"]:
            return None
        return {"pc_id": row["main_pc_id"],
                "label": row["main_pc_label"] or "",
                "last_seen": row["main_pc_last_seen"]}


# ── 유틸 ──────────────────────────────────────────────────────────
def _row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    if row is None:
        return None
    d = dict(row)
    # 민감 필드 제거 (응답 직렬화 시 사고 방지)
    d.pop("password_hash", None)
    d.pop("salt", None)
    return d
