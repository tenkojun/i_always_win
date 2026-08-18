"""
kronos_auto.py — 🌌 Kronos foundation model 자동매매
============================================================
사용자가 토글 ON → 지정 종목들에 대해 주기적으로 Kronos 예측
→ 예측 수익률이 임계 돌파 시 자동 매수/매도 (Paper Trading / KIS 모의).

작동 흐름:
  1. enabled 사용자별로 poll_sec마다 (일봉 기준 1시간~24시간 권장)
  2. tickers 리스트 각 종목 → kronos_predict (5스텝 forecast)
  3. predicted_return > buy_thresh → 매수 (미보유 시)
  4. predicted_return < sell_thresh → 매도 (보유 시)
  5. 모든 주문에 source=kronos_auto:{model} 명시
  6. risk_limits 통과 검증

auto_trader.py (컬렉션 전략 자동매매)와는 별도 — 일봉 기준 더 긴 주기.
"""
from __future__ import annotations

import datetime as dt
import json
import threading
import time
from typing import Any, Dict, List, Optional

from ..auth.store import _LOCK as _AUTH_LOCK, _conn as _auth_conn, init_db


_SCHEMA = """
CREATE TABLE IF NOT EXISTS kronos_auto_state (
  user_id           INTEGER PRIMARY KEY,
  enabled           INTEGER NOT NULL DEFAULT 0,
  poll_sec          INTEGER NOT NULL DEFAULT 3600,
  tickers_json      TEXT,                          -- ['AAPL', '005930.KS', ...]
  model_key         TEXT NOT NULL DEFAULT 'kronos_small',
  buy_thresh        REAL NOT NULL DEFAULT 0.02,
  sell_thresh       REAL NOT NULL DEFAULT -0.02,
  position_size_pct REAL NOT NULL DEFAULT 0.10,
  pred_len          INTEGER NOT NULL DEFAULT 5,
  last_run          TEXT,
  log_json          TEXT,
  updated_at        TEXT NOT NULL
);
"""


def _init_db():
    init_db()
    with _AUTH_LOCK:
        _auth_conn().executescript(_SCHEMA)
        _auth_conn().commit()


_init_db()


def _now():
    return dt.datetime.utcnow().isoformat()


# ════════════════════════════════════════════════════════════
#  상태 CRUD
# ════════════════════════════════════════════════════════════
def get_state(user_id: int) -> Dict[str, Any]:
    with _AUTH_LOCK:
        row = _auth_conn().execute(
            "SELECT * FROM kronos_auto_state WHERE user_id=?",
            (user_id,)).fetchone()
    if not row:
        return {
            "enabled":           False,
            "poll_sec":          3600,
            "tickers":           [],
            "model_key":         "kronos_small",
            "buy_thresh":        0.02,
            "sell_thresh":       -0.02,
            "position_size_pct": 0.10,
            "pred_len":          5,
            "last_run":          None,
            "log":               [],
        }
    d = dict(row)
    try: d["tickers"] = json.loads(d.pop("tickers_json") or "[]")
    except Exception: d["tickers"] = []
    try: d["log"] = json.loads(d.pop("log_json") or "[]")
    except Exception: d["log"] = []
    d["enabled"] = bool(d.get("enabled"))
    return d


def save_state(user_id: int, *,
                enabled: bool,
                tickers: List[str],
                model_key: str = "kronos_small",
                buy_thresh: float = 0.02,
                sell_thresh: float = -0.02,
                position_size_pct: float = 0.10,
                poll_sec: int = 3600,
                pred_len: int = 5) -> Dict[str, Any]:
    # 안전 검증
    tickers = [t.upper().strip() for t in (tickers or []) if t.strip()][:30]
    poll_sec = max(60, int(poll_sec))                  # 최소 1분
    pred_len = max(1, min(60, int(pred_len)))
    position_size_pct = max(0.001, min(1.0,
                                        float(position_size_pct)))
    # buy_thresh > sell_thresh 강제
    if buy_thresh <= sell_thresh:
        return {"ok": False,
                "error": "buy_thresh > sell_thresh 이어야 합니다"}
    with _AUTH_LOCK:
        _auth_conn().execute("""
            INSERT INTO kronos_auto_state
              (user_id, enabled, poll_sec, tickers_json, model_key,
               buy_thresh, sell_thresh, position_size_pct, pred_len, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
              enabled=excluded.enabled,
              poll_sec=excluded.poll_sec,
              tickers_json=excluded.tickers_json,
              model_key=excluded.model_key,
              buy_thresh=excluded.buy_thresh,
              sell_thresh=excluded.sell_thresh,
              position_size_pct=excluded.position_size_pct,
              pred_len=excluded.pred_len,
              updated_at=excluded.updated_at
        """, (user_id, 1 if enabled else 0, poll_sec,
              json.dumps(tickers), model_key,
              float(buy_thresh), float(sell_thresh),
              float(position_size_pct), pred_len, _now()))
        _auth_conn().commit()
    return {"ok": True, **get_state(user_id)}


def _append_log(user_id: int, entry: Dict[str, Any]):
    s = get_state(user_id)
    log = s.get("log", []) + [{**entry, "ts": _now()}]
    log = log[-100:]
    with _AUTH_LOCK:
        _auth_conn().execute(
            "UPDATE kronos_auto_state SET log_json=?, last_run=?, updated_at=? "
            "WHERE user_id=?",
            (json.dumps(log, ensure_ascii=False, default=str),
             _now(), _now(), user_id))
        _auth_conn().commit()


# ════════════════════════════════════════════════════════════
#  1회 실행 (사용자별)
# ════════════════════════════════════════════════════════════
def run_once(user_id: int, force: bool = False) -> Dict[str, Any]:
    """주기적으로 호출되는 메인 루프 — 모든 ticker에 대해 예측 + 주문 결정.

    force=True 이면 enabled 무시하고 1회 실행 (테스트).
    """
    s = get_state(user_id)
    if not s.get("enabled") and not force:
        return {"ok": False, "skipped": True, "reason": "비활성"}
    tickers = s.get("tickers") or []
    if not tickers:
        return {"ok": True, "n_targets": 0, "msg": "지정 종목 없음"}

    # lazy imports — Kronos 미설치 시에도 모듈 로드 가능하게
    try:
        from ..strategy.kronos_predictor import (kronos_predict,
                                                   check_model_downloaded)
        from ..strategy.vbt_runner import _fetch_ohlcv
        from .paper_trading import get_paper_engine
    except ImportError as e:
        return {"ok": False, "error": f"의존 모듈 로드 실패: {e}"}

    model_key = s.get("model_key") or "kronos_small"
    chk = check_model_downloaded(model_key)
    if not chk.get("downloaded"):
        return {"ok": False,
                "error": f"모델 {model_key} 미다운로드 — HUB ▸ 🌌 Kronos 탭에서 설치"}

    buy_thresh = float(s.get("buy_thresh") or 0.02)
    sell_thresh = float(s.get("sell_thresh") or -0.02)
    pos_pct = float(s.get("position_size_pct") or 0.10)
    pred_len = int(s.get("pred_len") or 5)

    eng = get_paper_engine(user_id)
    actions: List[Dict[str, Any]] = []
    for tk in tickers:
        try:
            df = _fetch_ohlcv(tk, period_days=500, interval="1d")
            n = 0 if df is None else len(df)
            if n < 80:
                actions.append({"ticker": tk, "action": "skip",
                                 "reason": f"데이터 부족 (n={n})"})
                continue
            eff_lookback = min(256, max(64, n - 15))
            r = kronos_predict(df, model_key=model_key,
                                lookback=eff_lookback,
                                pred_len=pred_len, sample_count=1)
            if not r.get("ok"):
                actions.append({"ticker": tk, "action": "error",
                                 "error": r.get("error")})
                continue
            pr = float(r.get("predicted_return") or 0)
            direction = r.get("direction", "")
            # 현재 포지션 확인
            state = eng.get_state()
            pos = next((p for p in state.get("positions", [])
                         if p["ticker"] == tk), None)
            holding = pos is not None and pos.get("qty", 0) > 0

            if pr < sell_thresh and holding:
                # 매도
                qty = int(pos["qty"])
                res = eng.place_order(tk, "sell", qty,
                                       source=f"kronos_auto:{model_key}",
                                       note=f"예측 {pr*100:.2f}% < {sell_thresh*100:.1f}%")
                actions.append({"ticker": tk, "action": "sell",
                                 "qty": qty, "pred_return": pr,
                                 "direction": direction,
                                 "result": res})
            elif pr > buy_thresh and not holding:
                # 매수
                cash = state.get("cash", 0)
                price = r.get("last_close") or 0
                if price > 0 and cash > 0:
                    qty = int((cash * pos_pct) / price)
                    if qty >= 1:
                        res = eng.place_order(
                            tk, "buy", qty,
                            source=f"kronos_auto:{model_key}",
                            note=f"예측 {pr*100:+.2f}% > {buy_thresh*100:.1f}%")
                        actions.append({"ticker": tk, "action": "buy",
                                         "qty": qty, "pred_return": pr,
                                         "direction": direction,
                                         "result": res})
                    else:
                        actions.append({"ticker": tk, "action": "skip",
                                         "reason": "qty<1 (자금 부족)",
                                         "pred_return": pr})
                else:
                    actions.append({"ticker": tk, "action": "skip",
                                     "reason": "현금/가격 부족",
                                     "pred_return": pr})
            else:
                actions.append({"ticker": tk, "action": "hold",
                                 "pred_return": pr,
                                 "direction": direction,
                                 "holding": holding})
        except Exception as e:
            actions.append({"ticker": tk, "action": "error",
                             "error": f"{type(e).__name__}: {str(e)[:120]}"})

    summary = {
        "n_targets": len(tickers),
        "n_buy":  sum(1 for a in actions if a["action"] == "buy"),
        "n_sell": sum(1 for a in actions if a["action"] == "sell"),
        "n_hold": sum(1 for a in actions if a["action"] == "hold"),
        "n_skip": sum(1 for a in actions if a["action"] == "skip"),
        "n_err":  sum(1 for a in actions if a["action"] == "error"),
        "model":  model_key,
    }
    _append_log(user_id, {"summary": summary, "actions": actions[:30]})
    return {"ok": True, "summary": summary, "actions": actions}


# ════════════════════════════════════════════════════════════
#  스케줄러 — 백그라운드 thread로 모든 enabled 사용자 폴링
# ════════════════════════════════════════════════════════════
_SCHED_THREAD: Optional[threading.Thread] = None
_SCHED_STOP = threading.Event()
_LAST_RUN_BY_USER: Dict[int, float] = {}


def _list_enabled_users() -> List[Dict[str, Any]]:
    with _AUTH_LOCK:
        rows = _auth_conn().execute(
            "SELECT user_id, poll_sec FROM kronos_auto_state WHERE enabled=1"
        ).fetchall()
    return [dict(r) for r in rows]


def _scheduler_loop():
    while not _SCHED_STOP.is_set():
        try:
            users = _list_enabled_users()
            now = time.time()
            for u in users:
                uid = u["user_id"]
                poll_sec = int(u.get("poll_sec") or 3600)
                last = _LAST_RUN_BY_USER.get(uid, 0)
                if now - last >= poll_sec:
                    try:
                        run_once(uid)
                    except Exception:
                        pass
                    _LAST_RUN_BY_USER[uid] = time.time()
        except Exception:
            pass
        # 30초마다 체크 (poll_sec 정밀도)
        _SCHED_STOP.wait(30)


def start_scheduler():
    """앱 부팅 시 1번 호출."""
    global _SCHED_THREAD
    if _SCHED_THREAD and _SCHED_THREAD.is_alive():
        return False
    _SCHED_STOP.clear()
    _SCHED_THREAD = threading.Thread(
        target=_scheduler_loop, name="kronos-auto-scheduler",
        daemon=True)
    _SCHED_THREAD.start()
    return True


def stop_scheduler():
    _SCHED_STOP.set()
