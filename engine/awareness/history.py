"""
Awareness 알림 히스토리 영구 저장
====================================
.data/auth.db 의 alert_history 테이블에 high-impact 알림을 영구 보관.
일반 알림은 12h TTL 메모리만 사용 (alert_engine 그대로).

저장 시점: alert_engine._ingest_articles 에서 high_impact만 추가 호출.
조회: 최근 30일, 자산 필터링, 시간순.
"""
from __future__ import annotations

import datetime as dt
import hashlib
from typing import Any, Dict, List, Optional

from ..auth.store import _LOCK, _conn

_SCHEMA = """
CREATE TABLE IF NOT EXISTS alert_history (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  title_hash TEXT UNIQUE NOT NULL,
  title TEXT NOT NULL,
  url TEXT,
  domain TEXT,
  ts TEXT,
  saved_at TEXT NOT NULL,
  priority REAL,
  matched_keywords TEXT,
  assets TEXT,
  high_impact INTEGER DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_alerts_saved ON alert_history(saved_at DESC);
CREATE INDEX IF NOT EXISTS idx_alerts_high ON alert_history(high_impact);
"""

_INITIALIZED = False


def _ensure_schema():
    global _INITIALIZED
    if _INITIALIZED:
        return
    with _LOCK:
        c = _conn()
        c.executescript(_SCHEMA)
        c.commit()
    _INITIALIZED = True


def _now() -> str:
    return dt.datetime.utcnow().isoformat()


def _title_hash(title: str) -> str:
    return hashlib.md5(title[:80].lower().encode()).hexdigest()[:16]


def save_alert(record: Dict[str, Any], assets: List[str]) -> bool:
    """단일 alert을 히스토리에 저장. 중복(같은 title hash)이면 skip."""
    _ensure_schema()
    title = (record.get("title") or "").strip()
    if not title:
        return False
    th = _title_hash(title)
    with _LOCK:
        c = _conn()
        try:
            c.execute(
                "INSERT INTO alert_history "
                "(title_hash, title, url, domain, ts, saved_at, "
                " priority, matched_keywords, assets, high_impact) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (th, title[:300], record.get("url", "")[:500],
                 record.get("domain", "")[:80], record.get("ts", ""),
                 _now(), float(record.get("priority") or 0),
                 ",".join(record.get("matched_keywords") or [])[:200],
                 ",".join(assets or [])[:200],
                 1 if record.get("high_impact") else 0))
            c.commit()
            return True
        except Exception:
            # UNIQUE 위반(이미 저장됨) 또는 기타
            return False


def list_history(limit: int = 100, days: int = 30,
                 only_high: bool = False,
                 asset: Optional[str] = None) -> List[Dict[str, Any]]:
    """최근 N일 히스토리 (시간 내림차순)."""
    _ensure_schema()
    cutoff = (dt.datetime.utcnow()
              - dt.timedelta(days=days)).isoformat()
    sql = ("SELECT id, title, url, domain, ts, saved_at, priority, "
           "matched_keywords, assets, high_impact "
           "FROM alert_history WHERE saved_at >= ? ")
    params: List[Any] = [cutoff]
    if only_high:
        sql += "AND high_impact = 1 "
    if asset:
        sql += "AND assets LIKE ? "
        params.append(f"%{asset}%")
    sql += "ORDER BY saved_at DESC LIMIT ?"
    params.append(int(limit))
    with _LOCK:
        rows = _conn().execute(sql, params).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["matched_keywords"] = (d.get("matched_keywords") or "").split(",") \
            if d.get("matched_keywords") else []
        d["assets"] = (d.get("assets") or "").split(",") \
            if d.get("assets") else []
        d["high_impact"] = bool(d.get("high_impact"))
        out.append(d)
    return out


def prune_old(days_to_keep: int = 60) -> int:
    """오래된 히스토리 정리 (반환: 삭제 행 수)."""
    _ensure_schema()
    cutoff = (dt.datetime.utcnow()
              - dt.timedelta(days=days_to_keep)).isoformat()
    with _LOCK:
        c = _conn()
        res = c.execute(
            "DELETE FROM alert_history WHERE saved_at < ?", (cutoff,))
        n = res.rowcount
        c.commit()
    return n


def stats() -> Dict[str, Any]:
    """히스토리 통계 (요약 카드용)."""
    _ensure_schema()
    with _LOCK:
        c = _conn()
        total = c.execute(
            "SELECT COUNT(*) FROM alert_history").fetchone()[0]
        high = c.execute(
            "SELECT COUNT(*) FROM alert_history WHERE high_impact=1"
        ).fetchone()[0]
        oldest = c.execute(
            "SELECT MIN(saved_at) FROM alert_history").fetchone()[0]
        newest = c.execute(
            "SELECT MAX(saved_at) FROM alert_history").fetchone()[0]
    return {"total": total, "high_impact": high,
            "oldest": oldest, "newest": newest}
