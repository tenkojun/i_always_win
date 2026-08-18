"""
종목 분석 이력 (C9) — max 이력 영구 저장
========================================
모든 analyze 결과를 SQLite에 누적 저장. 시간순/티커별 조회 지원.
DB는 auth.db 공유 (.data/auth.db).
"""
from __future__ import annotations

import datetime as dt
import json
from typing import Any, Dict, List, Optional

from engine.auth.store import _conn, _LOCK


_SCHEMA = """
CREATE TABLE IF NOT EXISTS analysis_history (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER,
  ticker TEXT NOT NULL,
  ts TEXT NOT NULL,
  signal TEXT,
  score REAL,
  grade TEXT,
  verdict TEXT,
  headline TEXT,
  risk_grade TEXT,
  psr REAL,
  dsr REAL,
  report_url TEXT,
  meta_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_ah_ticker_ts
  ON analysis_history(ticker, ts DESC);
CREATE INDEX IF NOT EXISTS idx_ah_user_ts
  ON analysis_history(user_id, ts DESC);
"""


def _now() -> str:
    return dt.datetime.utcnow().isoformat()


def init_history_db() -> None:
    with _LOCK:
        _conn().executescript(_SCHEMA)
        _conn().commit()


def save_analysis(user_id: Optional[int], ticker: str,
                  result: Dict[str, Any]) -> Dict[str, Any]:
    """analyze 결과 dict를 한 행으로 저장. 가벼운 메타만 컬럼, 나머지는 JSON."""
    if not ticker:
        return {"ok": False, "error": "ticker 누락"}
    mv = result.get("meta_verdict", {}) or {}
    # 가벼운 메타 (검색/필터용)
    row = {
        "user_id": user_id,
        "ticker": ticker.upper(),
        "ts": _now(),
        "signal": result.get("overall_signal") or mv.get("signal"),
        "score": result.get("overall_score") or mv.get("score"),
        "grade": result.get("grade") or mv.get("grade"),
        "verdict": result.get("verdict") or mv.get("verdict"),
        "headline": mv.get("headline", ""),
        "risk_grade": mv.get("risk_grade", ""),
        "psr": mv.get("psr"),
        "dsr": mv.get("dsr"),
        "report_url": result.get("report_url", ""),
        "meta_json": json.dumps(result, ensure_ascii=False, default=str),
    }
    with _LOCK:
        c = _conn()
        c.execute(
            "INSERT INTO analysis_history "
            "(user_id, ticker, ts, signal, score, grade, verdict, "
            " headline, risk_grade, psr, dsr, report_url, meta_json) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (row["user_id"], row["ticker"], row["ts"], row["signal"],
             row["score"], row["grade"], row["verdict"],
             row["headline"], row["risk_grade"], row["psr"], row["dsr"],
             row["report_url"], row["meta_json"]))
        c.commit()
        return {"ok": True}


def list_history(ticker: Optional[str] = None,
                 user_id: Optional[int] = None,
                 limit: int = 30,
                 offset: int = 0) -> List[Dict[str, Any]]:
    """이력 조회. ticker나 user_id 둘 다 None이면 전체 최신."""
    sql = ("SELECT id, user_id, ticker, ts, signal, score, grade, "
           "verdict, headline, risk_grade, psr, dsr, report_url "
           "FROM analysis_history")
    where, params = [], []
    if ticker:
        where.append("ticker = ?")
        params.append(ticker.upper())
    if user_id is not None:
        where.append("user_id = ?")
        params.append(user_id)
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY ts DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])
    with _LOCK:
        rows = _conn().execute(sql, params).fetchall()
        return [dict(r) for r in rows]


def get_history_detail(hid: int) -> Optional[Dict[str, Any]]:
    """단일 이력 + full meta JSON."""
    with _LOCK:
        row = _conn().execute(
            "SELECT * FROM analysis_history WHERE id=?",
            (hid,)).fetchone()
        if not row:
            return None
        d = dict(row)
        if d.get("meta_json"):
            try:
                d["meta"] = json.loads(d["meta_json"])
            except Exception:
                d["meta"] = None
        d.pop("meta_json", None)
        return d


def delete_history(hid: int, user_id: int,
                   is_admin: bool = False) -> Dict[str, Any]:
    with _LOCK:
        c = _conn()
        row = c.execute(
            "SELECT user_id FROM analysis_history WHERE id=?",
            (hid,)).fetchone()
        if not row:
            return {"ok": False, "error": "이력 없음"}
        if not is_admin and row["user_id"] != user_id:
            return {"ok": False, "error": "권한 없음"}
        c.execute("DELETE FROM analysis_history WHERE id=?", (hid,))
        c.commit()
        return {"ok": True}


def history_stats(ticker: Optional[str] = None) -> Dict[str, Any]:
    """종목/전체 이력 요약 통계."""
    where = ""
    params = []
    if ticker:
        where = " WHERE ticker = ?"
        params.append(ticker.upper())
    with _LOCK:
        c = _conn()
        total = c.execute(
            f"SELECT COUNT(*) AS n FROM analysis_history{where}",
            params).fetchone()
        latest = c.execute(
            f"SELECT MAX(ts) AS t FROM analysis_history{where}",
            params).fetchone()
        return {
            "total": int(total["n"]) if total else 0,
            "latest_ts": latest["t"] if latest else None,
        }
