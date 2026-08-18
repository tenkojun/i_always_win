"""
JobManager — 백그라운드 작업 큐 (Thread 기반)
============================================================
오래 걸리는 작업 (MEGA grid, AUTO, ML 학습 등)을 백그라운드 실행.
사용자는 화면 자유롭게 쓰고, 완료 시 알림 받고, 중단도 가능.

핵심 설계:
- ThreadPoolExecutor (max_workers=3 — 무거운 작업 동시성 제한)
- threading.Event 으로 cancel signal
- 메모리 dict + SQLite 영구화 (재시작 후에도 결과 조회)
- 작업 함수에 cancel_event 주입 — 협조적 cancel
- 진행률 callback 옵션 (작업 함수가 호출)
"""
from __future__ import annotations

import json
import threading
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor, Future
from typing import Any, Callable, Dict, List, Optional

# SQLite 영구화 (옵션)
from ..auth.store import _LOCK as _AUTH_LOCK, _conn as _auth_conn, init_db


# ── 작업 상태 enum ────────────────────────────────────────────
class JobStatus:
    QUEUED   = "queued"
    RUNNING  = "running"
    DONE     = "done"
    FAILED   = "failed"
    CANCELED = "canceled"


_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
  id           TEXT PRIMARY KEY,
  user_id      INTEGER,
  kind         TEXT NOT NULL,
  title        TEXT,
  status       TEXT NOT NULL,
  progress     REAL DEFAULT 0,
  message      TEXT,
  payload_json TEXT,
  result_json  TEXT,
  error        TEXT,
  created_at   TEXT NOT NULL,
  started_at   TEXT,
  finished_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_jobs_user_status
  ON jobs(user_id, status, created_at DESC);
"""


def _init_jobs_db():
    init_db()  # auth db 보장
    with _AUTH_LOCK:
        _auth_conn().executescript(_SCHEMA)
        _auth_conn().commit()


_init_jobs_db()


# ── JobContext: 작업 함수에 주입 (cancel_event + progress 콜백) ─
class JobContext:
    """작업 함수가 진행률 보고 + cancel 체크할 수 있게."""

    def __init__(self, job_id: str, cancel_event: threading.Event,
                 manager: "JobManager"):
        self.job_id = job_id
        self.cancel_event = cancel_event
        self.manager = manager
        self._last_progress = 0.0

    def is_canceled(self) -> bool:
        return self.cancel_event.is_set()

    def check_canceled(self):
        """cancel 시 CancelledError 발생 — 작업 함수에서 주기적 호출."""
        if self.cancel_event.is_set():
            raise InterruptedError("작업이 취소되었습니다")

    def progress(self, pct: float, message: str = ""):
        """0~100 진행률. 작업 함수가 호출."""
        pct = max(0.0, min(100.0, float(pct)))
        self._last_progress = pct
        self.manager._update_progress(self.job_id, pct, message)


# ── JobManager ────────────────────────────────────────────────
class JobManager:
    """싱글톤 백그라운드 작업 관리자."""

    def __init__(self, max_workers: int = 3):
        self._executor = ThreadPoolExecutor(max_workers=max_workers,
                                             thread_name_prefix="job")
        self._jobs: Dict[str, Dict[str, Any]] = {}
        self._cancel_events: Dict[str, threading.Event] = {}
        self._futures: Dict[str, Future] = {}
        self._lock = threading.RLock()
        # 등록된 작업 종류 (kind → 핸들러 함수)
        self._handlers: Dict[str, Callable[[Any, JobContext], Any]] = {}

    # ─── 핸들러 등록 ─────────────────────────────────────────
    def register(self, kind: str,
                 fn: Callable[[Any, JobContext], Any]) -> None:
        """fn signature: (payload: dict, ctx: JobContext) -> result_dict"""
        self._handlers[kind] = fn

    # ─── 제출 ────────────────────────────────────────────────
    def submit(self, kind: str, payload: Dict[str, Any],
                user_id: Optional[int] = None,
                title: Optional[str] = None) -> str:
        if kind not in self._handlers:
            raise ValueError(f"unknown job kind: {kind}")
        job_id = f"job_{int(time.time()*1000)}_{uuid.uuid4().hex[:8]}"
        now = _now()
        title = title or kind
        with self._lock:
            self._jobs[job_id] = {
                "id": job_id, "user_id": user_id, "kind": kind,
                "title": title, "status": JobStatus.QUEUED,
                "progress": 0.0, "message": "",
                "payload": payload, "result": None, "error": None,
                "created_at": now, "started_at": None, "finished_at": None,
            }
            self._cancel_events[job_id] = threading.Event()
            # DB upsert
            self._persist(job_id)
        # 큐에 제출
        fut = self._executor.submit(self._run_job, job_id)
        self._futures[job_id] = fut
        return job_id

    # ─── 실행 (내부) ─────────────────────────────────────────
    def _run_job(self, job_id: str):
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return
            handler = self._handlers.get(job["kind"])
            cancel_event = self._cancel_events[job_id]
            payload = job["payload"]
            job["status"] = JobStatus.RUNNING
            job["started_at"] = _now()
            self._persist(job_id)
        ctx = JobContext(job_id, cancel_event, self)
        try:
            if cancel_event.is_set():
                raise InterruptedError("시작 전 취소됨")
            result = handler(payload, ctx)
            with self._lock:
                if cancel_event.is_set():
                    job["status"] = JobStatus.CANCELED
                    job["error"] = "사용자가 취소함"
                else:
                    job["status"] = JobStatus.DONE
                    job["result"] = result
                    job["progress"] = 100.0
                job["finished_at"] = _now()
                self._persist(job_id)
        except InterruptedError as ie:
            with self._lock:
                job["status"] = JobStatus.CANCELED
                job["error"] = str(ie)
                job["finished_at"] = _now()
                self._persist(job_id)
        except Exception as e:
            with self._lock:
                job["status"] = JobStatus.FAILED
                job["error"] = f"{type(e).__name__}: {e}"
                job["traceback"] = traceback.format_exc()[-1500:]
                job["finished_at"] = _now()
                self._persist(job_id)

    # ─── 진행률 갱신 ─────────────────────────────────────────
    def _update_progress(self, job_id: str, pct: float, message: str = ""):
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return
            job["progress"] = pct
            if message:
                job["message"] = message
            # DB 자주 쓰지 않게 — 진행률은 메모리만 (조회 시 합치기)

    # ─── 취소 ─────────────────────────────────────────────────
    def cancel(self, job_id: str) -> Dict[str, Any]:
        with self._lock:
            evt = self._cancel_events.get(job_id)
            job = self._jobs.get(job_id)
            if not job:
                return {"ok": False, "error": "job not found"}
            if job["status"] in (JobStatus.DONE, JobStatus.FAILED,
                                  JobStatus.CANCELED):
                return {"ok": False,
                        "error": f"이미 종료된 작업 ({job['status']})"}
            if evt:
                evt.set()
            # QUEUED 상태면 즉시 cancel 마크
            if job["status"] == JobStatus.QUEUED:
                job["status"] = JobStatus.CANCELED
                job["error"] = "큐에서 취소됨"
                job["finished_at"] = _now()
                self._persist(job_id)
            return {"ok": True, "status": job["status"]}

    # ─── 조회 ─────────────────────────────────────────────────
    def get(self, job_id: str,
             user_id: Optional[int] = None) -> Optional[Dict[str, Any]]:
        with self._lock:
            job = self._jobs.get(job_id)
            if job:
                # user_id 권한 체크
                if user_id is not None and job.get("user_id") not in (None, user_id):
                    return None
                return self._to_dict(job, include_result=True)
        # 메모리에 없으면 DB에서
        return self._load_from_db(job_id, user_id)

    def list(self, user_id: Optional[int] = None,
              status_filter: Optional[str] = None,
              limit: int = 50) -> List[Dict[str, Any]]:
        with self._lock:
            items = list(self._jobs.values())
        # filter
        if user_id is not None:
            items = [j for j in items if j.get("user_id") in (None, user_id)]
        if status_filter:
            items = [j for j in items if j["status"] == status_filter]
        items.sort(key=lambda x: x.get("created_at") or "", reverse=True)
        items = items[:limit]
        return [self._to_dict(j, include_result=False) for j in items]

    def get_result(self, job_id: str,
                    user_id: Optional[int] = None) -> Optional[Dict[str, Any]]:
        j = self.get(job_id, user_id=user_id)
        if not j:
            return None
        return j.get("result")

    # ─── SQLite 영구화 ───────────────────────────────────────
    def _persist(self, job_id: str):
        job = self._jobs.get(job_id)
        if not job:
            return
        try:
            with _AUTH_LOCK:
                c = _auth_conn()
                c.execute("""
                    INSERT INTO jobs
                      (id, user_id, kind, title, status, progress, message,
                       payload_json, result_json, error,
                       created_at, started_at, finished_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                      status=excluded.status,
                      progress=excluded.progress,
                      message=excluded.message,
                      result_json=excluded.result_json,
                      error=excluded.error,
                      started_at=excluded.started_at,
                      finished_at=excluded.finished_at
                """, (
                    job["id"], job.get("user_id"), job["kind"],
                    job.get("title"), job["status"],
                    float(job.get("progress") or 0),
                    job.get("message", ""),
                    json.dumps(job.get("payload") or {}, ensure_ascii=False,
                               default=str),
                    json.dumps(job.get("result"), ensure_ascii=False,
                                default=str) if job.get("result") else None,
                    job.get("error"),
                    job.get("created_at"), job.get("started_at"),
                    job.get("finished_at"),
                ))
                c.commit()
        except Exception:
            # 영구화 실패해도 메모리에서는 계속 작동
            pass

    def _load_from_db(self, job_id: str,
                       user_id: Optional[int]) -> Optional[Dict[str, Any]]:
        try:
            with _AUTH_LOCK:
                row = _auth_conn().execute(
                    "SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
                if not row:
                    return None
                d = dict(row)
                if user_id is not None and d.get("user_id") not in (None, user_id):
                    return None
                # JSON 디코드
                try: d["result"] = json.loads(d.pop("result_json") or "null")
                except Exception: d["result"] = None
                try: d["payload"] = json.loads(d.pop("payload_json") or "{}")
                except Exception: d["payload"] = {}
                return d
        except Exception:
            return None

    def _to_dict(self, job: Dict[str, Any],
                  include_result: bool = False) -> Dict[str, Any]:
        out = {
            "id": job["id"], "user_id": job.get("user_id"),
            "kind": job["kind"], "title": job.get("title"),
            "status": job["status"],
            "progress": round(float(job.get("progress") or 0), 1),
            "message": job.get("message", ""),
            "error": job.get("error"),
            "created_at": job.get("created_at"),
            "started_at": job.get("started_at"),
            "finished_at": job.get("finished_at"),
        }
        if include_result and job.get("result") is not None:
            out["result"] = job["result"]
        return out


def _now() -> str:
    import datetime as dt
    return dt.datetime.utcnow().isoformat()


# ── 싱글톤 ────────────────────────────────────────────────────
_singleton: Optional[JobManager] = None
_singleton_lock = threading.Lock()


def get_manager() -> JobManager:
    global _singleton
    if _singleton is None:
        with _singleton_lock:
            if _singleton is None:
                _singleton = JobManager(max_workers=3)
                _register_handlers(_singleton)
    return _singleton


# ─── 편의 함수 ───────────────────────────────────────────────
def submit_job(kind: str, payload: Dict[str, Any],
                user_id: Optional[int] = None,
                title: Optional[str] = None) -> str:
    return get_manager().submit(kind, payload, user_id=user_id, title=title)


def get_job(job_id: str, user_id: Optional[int] = None) -> Optional[Dict[str, Any]]:
    return get_manager().get(job_id, user_id=user_id)


def list_jobs(user_id: Optional[int] = None,
               status_filter: Optional[str] = None,
               limit: int = 50) -> List[Dict[str, Any]]:
    return get_manager().list(user_id=user_id, status_filter=status_filter,
                                 limit=limit)


def cancel_job(job_id: str) -> Dict[str, Any]:
    return get_manager().cancel(job_id)


def get_result(job_id: str,
                user_id: Optional[int] = None) -> Optional[Dict[str, Any]]:
    return get_manager().get_result(job_id, user_id=user_id)


# ════════════════════════════════════════════════════════════
#  핸들러 등록 — 백그라운드로 돌릴 작업 종류
# ════════════════════════════════════════════════════════════
def _register_handlers(mgr: JobManager):
    """각 종류별 핸들러를 등록. payload는 dict, ctx는 JobContext."""

    def _h_auto_analyze(payload, ctx):
        from engine.strategy.auto_analyze import auto_analyze
        ctx.progress(5, "AUTO 분석 시작")
        result = auto_analyze(**payload)
        ctx.check_canceled()
        ctx.progress(100, "완료")
        return result

    def _h_mega_grid(payload, ctx):
        from engine.strategy.mega_grid import mega_grid
        ctx.progress(5, "MEGA grid 시작")
        result = mega_grid(**payload)
        ctx.progress(100, "완료")
        return result

    def _h_batch_grid(payload, ctx):
        from engine.strategy.vbt_vectorized import batch_grid
        ctx.progress(5, "BATCH grid 시작")
        result = batch_grid(**payload)
        ctx.progress(100, "완료")
        return result

    def _h_multi_strategy(payload, ctx):
        from engine.strategy.multi_strategy import combine_strategies
        ctx.progress(5, "전략 합성 시작")
        result = combine_strategies(**payload)
        ctx.progress(100, "완료")
        return result

    def _h_ml_predict(payload, ctx):
        from engine.strategy.ml_predict import train_predict_model
        ctx.progress(5, "ML 학습 시작")
        result = train_predict_model(**payload)
        ctx.progress(100, "완료")
        return result

    def _h_ml_backtest(payload, ctx):
        from engine.strategy.ml_predict import ml_signal_backtest
        ctx.progress(5, "ML 백테스트 시작")
        result = ml_signal_backtest(**payload)
        ctx.progress(100, "완료")
        return result

    def _h_of_pead_optimize(payload, ctx):
        from engine.orderflow_pead.main import build_bundle
        from engine.orderflow_pead import (
            OrderflowDeltaStrategy, PEADStrategy,
            grid_search, walk_forward_optimization,
        )
        ticker = payload.get("ticker", "AAPL")
        which = payload.get("which", "of")
        period_days = int(payload.get("period_days", 730))
        ctx.progress(10, f"{ticker} bundle 빌드")
        bundle = build_bundle(ticker, period_days=period_days)
        ctx.check_canceled()
        ctx.progress(40, "grid search")
        if which == "pead":
            grid = {"sue_top_pct": [0.1, 0.2, 0.3],
                    "drift_days":  [10, 20, 30, 60]}
            gs = grid_search(PEADStrategy, grid, bundle, top_n=10)
            ctx.progress(70, "WFA")
            wfa = walk_forward_optimization(PEADStrategy,
                {"sue_top_pct": [0.15, 0.25], "drift_days": [20, 40]},
                bundle, n_folds=3)
        else:
            grid = {"delta_threshold":    [200, 500, 1000, 2000],
                    "divergence_lookback":[5, 10, 20],
                    "divergence_z":       [1.0, 1.5, 2.0],
                    "max_hold_bars":      [10, 30, 60]}
            gs = grid_search(OrderflowDeltaStrategy, grid, bundle, top_n=10)
            ctx.progress(70, "WFA")
            wfa = walk_forward_optimization(OrderflowDeltaStrategy,
                {"delta_threshold": [300, 700, 1500], "max_hold_bars": [20, 40]},
                bundle, n_folds=3)
        ctx.progress(100, "완료")
        return {
            "ok": True, "ticker": ticker, "which": which,
            "grid_top": gs.get("top", []),
            "n_combos": gs.get("n_combos", 0),
            "wfa": wfa,
        }

    def _h_portfolio_optimize(payload, ctx):
        from engine.strategy.portfolio_optimizer import optimize_portfolio
        ctx.progress(5, "포트폴리오 최적화")
        result = optimize_portfolio(**payload)
        ctx.progress(100, "완료")
        return result

    mgr.register("auto_analyze",        _h_auto_analyze)
    mgr.register("mega_grid",           _h_mega_grid)
    mgr.register("batch_grid",          _h_batch_grid)
    mgr.register("multi_strategy",      _h_multi_strategy)
    mgr.register("ml_predict",          _h_ml_predict)
    mgr.register("ml_backtest",         _h_ml_backtest)
    mgr.register("of_pead_optimize",    _h_of_pead_optimize)
    mgr.register("portfolio_optimize",  _h_portfolio_optimize)
