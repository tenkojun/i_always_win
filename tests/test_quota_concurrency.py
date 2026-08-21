# -*- coding: utf-8 -*-
"""
쿼터 — 동시에 두드려도 한도를 넘지 못하는가.

한도 판정은 "확인 후 증가" 두 단계다. 그 사이에 다른 요청이 끼면 둘 다
통과한다. 버튼을 연타하거나 탭을 여러 개 열면 실제로 그렇게 된다.

CLAUDE.md 가 기록한 실제 사고 둘을 여기서 막는다.
- 확인과 증가가 한 트랜잭션이 아니면 동시 클릭으로 넘길 수 있다
- `user_id` 가 NULL 이면 SQLite 가 PK 중복을 허용해 카운트가 누적되지
  않는다 — 한도가 통째로 우회됐다
"""
from __future__ import annotations

import threading

import pytest

from engine.auth.quota import (FREE_DAILY_REPORTS, consume, quota_status,
                               set_tier)


def _fresh_uid(counter=[700000]):
    counter[0] += 1
    return counter[0]


def test_concurrent_requests_cannot_exceed_the_cap():
    """
    스레드 40개를 같은 순간에 출발시켜 한도를 넘기려 해 본다.
    BEGIN IMMEDIATE 가 빠지면 여기서 3건보다 많이 통과한다.
    """
    uid = _fresh_uid()
    n = 40
    granted = []
    lock = threading.Lock()
    barrier = threading.Barrier(n)

    def worker():
        barrier.wait()          # 최대한 동시에
        ok = bool(consume(uid, "report").get("ok"))
        with lock:
            granted.append(ok)

    ts = [threading.Thread(target=worker) for _ in range(n)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()

    n_ok = sum(granted)
    assert n_ok <= FREE_DAILY_REPORTS, (
        f"한도 {FREE_DAILY_REPORTS} 인데 {n_ok}건이 통과했다 — 경쟁 상태")
    assert quota_status(uid, "report")["used"] == n_ok, \
        "허용 건수와 DB 기록이 다르다"


def test_quota_counts_up_to_the_cap_then_stops():
    """순차 호출에서도 정확히 한도까지만 허용해야 한다."""
    uid = _fresh_uid()
    oks = [bool(consume(uid, "report").get("ok"))
           for _ in range(FREE_DAILY_REPORTS + 3)]
    assert sum(oks) == FREE_DAILY_REPORTS
    assert oks[:FREE_DAILY_REPORTS] == [True] * FREE_DAILY_REPORTS, \
        "앞쪽에서 이미 막혔다"
    assert not any(oks[FREE_DAILY_REPORTS:]), "한도를 넘겨 통과시켰다"


def test_missing_user_id_is_refused():
    """
    user_id 가 없으면 진행하지 않는다.

    NULL 키로 쓰면 SQLite 가 PK 중복을 허용해 카운트가 누적되지 않는다.
    실제로 무료 3회 제한이 통째로 우회됐다(실측 10/10 통과).
    """
    r = consume(None, "report")
    assert not r.get("ok"), "NULL 사용자로 통과했다"
    assert r.get("remaining") == 0


def test_platinum_is_unlimited(monkeypatch):
    """플래티넘은 한도가 없어야 한다 — 등급 기능이 실제로 작동하는가."""
    import engine.auth.quota as Q
    uid = _fresh_uid()
    set_tier(uid, "platinum")
    monkeypatch.setattr(Q, "_tier_from_session",
                        lambda u: "platinum" if u == uid else None)
    oks = [bool(consume(uid, "report").get("ok"))
           for _ in range(FREE_DAILY_REPORTS * 4)]
    assert all(oks), f"플래티넘인데 {oks.count(False)}건이 막혔다"


def test_quota_status_is_self_consistent():
    """used + remaining 이 limit 을 넘으면 화면에 이상한 숫자가 나간다."""
    uid = _fresh_uid()
    consume(uid, "report")
    st = quota_status(uid, "report")
    if st.get("limit") is not None and st.get("remaining") is not None:
        assert st["used"] + st["remaining"] <= st["limit"], st
        assert st["used"] >= 0 and st["remaining"] >= 0


def test_tests_do_not_touch_the_real_database():
    """
    격리가 실제로 걸렸는지 확인한다.

    처음에는 환경변수만 세팅해 두고 격리됐다고 믿었는데 아무 효과가
    없었다 — engine/paths.py 가 그 변수를 읽지 않고, AUTH_DB 는 import
    시점에 확정된다. 그래서 쿼터 테스트가 실제 .data/auth.db 에 행을
    쓰고 있었다. 조용히 실패하는 격리는 없는 것만 못하다.
    """
    from pathlib import Path
    import engine.auth.quota as Q
    real = Path(__file__).resolve().parent.parent / ".data" / "auth.db"
    assert Path(Q.AUTH_DB) != real, (
        f"테스트가 실제 DB 를 쓰고 있다: {Q.AUTH_DB}")
    assert "plutus_test_" in str(Q.AUTH_DB),         f"임시 폴더가 아니다: {Q.AUTH_DB}"
