# -*- coding: utf-8 -*-
"""
회원 등급 · 보고서 일일 한도
============================
무료 회원은 하루 N회, 프리미엄은 무제한으로 보고서를 뽑는다.

왜 서버에서 세는가
------------------
한도를 프론트에서만 막으면 `/api/jiqtx/analyze` 를 직접 때리면 그만이다.
브라우저는 신뢰 경계 밖이므로 **카운트도 판정도 서버에서** 한다.

날짜 경계
---------
`date.today()` 는 **서버 로컬 날짜**다. 사용자가 다른 시간대에 있어도
모두 같은 경계를 쓴다. 사용자별 시간대로 자르면 시간대를 바꿔가며
한도를 초기화할 수 있어서 일부러 서버 기준으로 고정했다.

저장
----
prefs 와 같은 SQLite 를 쓴다. 별도 테이블을 만들지 않고 `usage_daily`
한 장에 (user_id, day, kind) 로 누적한다.
"""
from __future__ import annotations

import datetime as dt
import sqlite3
from typing import Any, Dict

from engine.paths import AUTH_DB

FREE_DAILY_REPORTS = 3
TIERS = ("free", "premium")


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(str(AUTH_DB), timeout=10)
    c.row_factory = sqlite3.Row
    return c


def _ensure(c: sqlite3.Connection) -> None:
    c.execute("""CREATE TABLE IF NOT EXISTS usage_daily(
        user_id INTEGER NOT NULL,
        day     TEXT    NOT NULL,
        kind    TEXT    NOT NULL,
        n       INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (user_id, day, kind))""")
    c.execute("""CREATE TABLE IF NOT EXISTS user_tier(
        user_id INTEGER PRIMARY KEY,
        tier    TEXT NOT NULL DEFAULT 'free',
        updated TEXT)""")


def _today() -> str:
    return dt.date.today().isoformat()


def get_tier(user_id: int) -> str:
    try:
        with _conn() as c:
            _ensure(c)
            r = c.execute("SELECT tier FROM user_tier WHERE user_id=?",
                          (user_id,)).fetchone()
            t = (r["tier"] if r else "free")
            return t if t in TIERS else "free"
    except Exception:
        return "free"


def set_tier(user_id: int, tier: str) -> Dict[str, Any]:
    tier = (tier or "free").lower()
    if tier not in TIERS:
        return {"ok": False, "error": "알 수 없는 등급"}
    try:
        with _conn() as c:
            _ensure(c)
            c.execute("""INSERT INTO user_tier(user_id,tier,updated)
                         VALUES(?,?,?)
                         ON CONFLICT(user_id) DO UPDATE
                         SET tier=excluded.tier, updated=excluded.updated""",
                      (user_id, tier, dt.datetime.now().isoformat(" ", "seconds")))
        return {"ok": True, "tier": tier}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def used_today(user_id: int, kind: str = "report") -> int:
    try:
        with _conn() as c:
            _ensure(c)
            r = c.execute("""SELECT n FROM usage_daily
                             WHERE user_id=? AND day=? AND kind=?""",
                          (user_id, _today(), kind)).fetchone()
            return int(r["n"]) if r else 0
    except Exception:
        return 0


def quota_status(user_id: int, kind: str = "report") -> Dict[str, Any]:
    tier = get_tier(user_id)
    used = used_today(user_id, kind)
    if tier == "premium":
        return {"tier": tier, "unlimited": True, "used": used,
                "limit": None, "remaining": None, "allowed": True}
    remaining = max(0, FREE_DAILY_REPORTS - used)
    return {"tier": tier, "unlimited": False, "used": used,
            "limit": FREE_DAILY_REPORTS, "remaining": remaining,
            "allowed": remaining > 0}


def consume(user_id: int, kind: str = "report") -> Dict[str, Any]:
    """
    한도를 확인하고 **같은 트랜잭션에서** 1 올린다.

    확인과 증가를 따로 하면 동시에 두 번 눌렀을 때 둘 다 통과한다.
    무료 3회가 4회가 되는 경로라 여기서 원자적으로 처리한다.
    """
    tier = get_tier(user_id)
    try:
        with _conn() as c:
            _ensure(c)
            c.execute("BEGIN IMMEDIATE")
            r = c.execute("""SELECT n FROM usage_daily
                             WHERE user_id=? AND day=? AND kind=?""",
                          (user_id, _today(), kind)).fetchone()
            used = int(r["n"]) if r else 0
            if tier != "premium" and used >= FREE_DAILY_REPORTS:
                return {"ok": False, "error": (
                    f"무료 등급은 하루 {FREE_DAILY_REPORTS}회까지 "
                    "보고서를 만들 수 있습니다. 자정에 초기화됩니다."),
                    **quota_status(user_id, kind)}
            c.execute("""INSERT INTO usage_daily(user_id,day,kind,n)
                         VALUES(?,?,?,1)
                         ON CONFLICT(user_id,day,kind) DO UPDATE
                         SET n = n + 1""", (user_id, _today(), kind))
    except Exception as e:
        # 계량 실패가 분석 자체를 막지는 않게 한다 — 과금이 아니라 한도다
        return {"ok": True, "warn": f"{type(e).__name__}: {e}",
                **quota_status(user_id, kind)}
    return {"ok": True, **quota_status(user_id, kind)}
