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
PREMIUM_DAILY_REPORTS = None          # 무제한

# 등급 — 위로 갈수록 포함 관계다(플래티넘 ⊃ 프리미엄 ⊃ 무료).
TIERS = ("free", "premium", "platinum")
TIER_KO = {"free": "무료", "premium": "프리미엄", "platinum": "플래티넘"}

# 기능 플래그. **화면에서 숨기는 것과 서버에서 막는 것은 다르다** —
# 프론트는 이 값으로 UI 를 정리하고, 실제 차단은 라우트에서 한다.
FEATURES = {
    "free": {
        "reports_per_day": FREE_DAILY_REPORTS,
        "portfolio": True,       # 포트폴리오 분석
        "market_flow": True,     # 수급 스캐너
        "report_themes": False,  # 보고서 테마 선택
        "vault": False,          # 보고서 보관함
        "agent_chat": False,     # 에이전트 채팅
        "tunnel": False,         # 외부 접근(터널)
        "export": False,         # 보고서 다운로드
        "priority": False,       # 분석 큐 우선순위
    },
    "premium": {
        "reports_per_day": None,
        "portfolio": True, "market_flow": True,
        "report_themes": True, "vault": True,
        "agent_chat": True, "tunnel": False,
        "export": True, "priority": False,
    },
    # 플래티넘 = 전 기능 개방
    "platinum": {
        "reports_per_day": None,
        "portfolio": True, "market_flow": True,
        "report_themes": True, "vault": True,
        "agent_chat": True, "tunnel": True,
        "export": True, "priority": True,
    },
}


def features(user_id: int) -> Dict[str, Any]:
    """이 사용자가 쓸 수 있는 기능 표."""
    t = get_tier(user_id)
    f = dict(FEATURES.get(t, FEATURES["free"]))
    f["tier"] = t
    f["tier_ko"] = TIER_KO.get(t, t)
    return f


def can(user_id: int, feature: str) -> bool:
    """기능 하나에 대한 허용 여부. 모르는 이름은 막는다(닫힌 기본값)."""
    return bool(FEATURES.get(get_tier(user_id), FEATURES["free"])
                .get(feature, False))


def _conn() -> sqlite3.Connection:
    # timeout 은 잠금이 풀리기를 기다리는 시간이다. 기본 5초로는 서버가
    # 다른 작업으로 DB를 쥐고 있을 때 쉽게 터진다 — 그 실패가 곧 한도
    # 판정 실패로 이어지므로 넉넉히 준다.
    c = sqlite3.connect(str(AUTH_DB), timeout=20)
    c.row_factory = sqlite3.Row
    try:
        # WAL 이면 읽기와 쓰기가 서로를 막지 않아 잠금 충돌이 크게 준다.
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA busy_timeout=20000")
    except Exception:
        pass                      # 지원 안 되는 환경이면 기본 모드로 간다
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
    if FEATURES.get(tier, {}).get("reports_per_day") is None:
        return {"tier": tier, "tier_ko": TIER_KO.get(tier, tier),
                "unlimited": True, "used": used,
                "limit": None, "remaining": None, "allowed": True}
    remaining = max(0, FREE_DAILY_REPORTS - used)
    return {"tier": tier, "tier_ko": TIER_KO.get(tier, tier),
            "unlimited": False, "used": used,
            "limit": FREE_DAILY_REPORTS, "remaining": remaining,
            "allowed": remaining > 0}


def consume(user_id: int, kind: str = "report") -> Dict[str, Any]:
    """
    한도를 확인하고 **같은 트랜잭션에서** 1 올린다.

    확인과 증가를 따로 하면 동시에 두 번 눌렀을 때 둘 다 통과한다.
    무료 3회가 4회가 되는 경로라 여기서 원자적으로 처리한다.
    """
    # user_id 가 없으면 한도를 셀 방법이 없다. 예전엔 그대로 진행해서
    # NULL 키로 행을 넣었는데, SQLite 는 PRIMARY KEY 에 NULL 중복을
    # 허용하므로 **매 호출이 새 행**이 됐다 — 카운트가 누적되지 않아
    # 무료 3회 제한이 통째로 우회됐다(실측 10/10 통과).
    if user_id is None:
        return {"ok": False,
                "error": "사용자를 식별할 수 없습니다. 다시 로그인하세요.",
                "tier": "free", "tier_ko": TIER_KO["free"],
                "unlimited": False, "used": 0,
                "limit": FREE_DAILY_REPORTS, "remaining": 0, "allowed": False}

    tier = get_tier(user_id)
    try:
        with _conn() as c:
            _ensure(c)
            c.execute("BEGIN IMMEDIATE")
            r = c.execute("""SELECT n FROM usage_daily
                             WHERE user_id=? AND day=? AND kind=?""",
                          (user_id, _today(), kind)).fetchone()
            used = int(r["n"]) if r else 0
            cap = FEATURES.get(tier, {}).get("reports_per_day")
            if cap is not None and used >= cap:
                return {"ok": False, "error": (
                    f"{TIER_KO.get(tier, tier)} 등급은 하루 {cap}회까지 "
                    "보고서를 만들 수 있습니다. 자정에 초기화됩니다."),
                    **quota_status(user_id, kind)}
            c.execute("""INSERT INTO usage_daily(user_id,day,kind,n)
                         VALUES(?,?,?,1)
                         ON CONFLICT(user_id,day,kind) DO UPDATE
                         SET n = n + 1""", (user_id, _today(), kind))
    except Exception as e:
        # 예전엔 여기서 ok=True 로 통과시켰다("계량 실패가 분석을 막을
        # 이유는 없다"). 그런데 DB 잠금은 **동시 요청이 많을 때** 나는
        # 것이라, 정확히 한도를 지켜야 하는 순간에 문이 열린다.
        # 실측 — 이미 99회 쓴 무료 계정이 잠금 상황에서 그대로 통과했다.
        #
        # 그래서 이미 한도를 넘긴 게 확인되면 막고, 판단이 불가능할 때만
        # 통과시킨다. 계량 실패로 정상 사용자를 막지 않으면서 명백한
        # 초과는 잡는다.
        try:
            st = quota_status(user_id, kind)
            if not st.get("allowed", True):
                return {"ok": False,
                        "error": "일시적으로 사용량을 확인할 수 없습니다. "
                                 "이미 한도를 사용해 잠시 후 다시 시도하세요.",
                        "warn": f"{type(e).__name__}", **st}
            return {"ok": True, "warn": f"{type(e).__name__}: {e}", **st}
        except Exception:
            # 상태 조회조차 안 되면 판단 근거가 없다 — 통과시키되 알린다
            return {"ok": True, "warn": f"{type(e).__name__}: {e}",
                    "tier": tier, "tier_ko": TIER_KO.get(tier, tier),
                    "unlimited": False, "used": None,
                    "limit": FREE_DAILY_REPORTS, "remaining": None,
                    "allowed": True}
    return {"ok": True, **quota_status(user_id, kind)}
