# ==============================================================================
# [22/25] ledger.py — 캘리브레이션 원장 (SQLite)
# ==============================================================================

"""
jiqtx.ledger — 캘리브레이션 원장 (Post-Mortem Ledger).

왜 이것이 없으면 나머지가 무의미한가
------------------------------------
게이트·DSR·PBO·Murphy 분해는 전부 '지금 이 표본 안에서' 계산된 진단이다.
시스템이 실제로 잘 맞히는지는 **예측을 남기고 나중에 채점**해야만 알 수 있다.
원장이 없으면 이 엔진은 아무리 정교해도 정적 계산기다.

원장이 하는 일
--------------
1. 모든 예측을 타임스탬프·설정 해시와 함께 저장 (사후 수정 불가)
2. 지평(horizon)이 지나면 실현값으로 자동 채점 (Brier, 로그점수, CI 포함 여부)
3. 에이전트별 Brier skill을 누적 → **판정 엔진의 가중치로 환류**
   (자기 확신이 아니라 실적이 가중치를 정한다)
4. 자산군별·아키타입별 정확도, conformal 실측 커버리지 추적

저장소는 SQLite(표준 라이브러리)이므로 외부 의존이 없다.
"""

import hashlib
import json
import math
import os
import sqlite3
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

# 원장도 앱 폴더 안 .data/ 에 — 백업·이전이 폴더 하나로 끝나도록.
from engine.paths import DATA_DIR as _APP_DATA

DEFAULT_PATH = os.environ.get(
    "JIQTX_LEDGER", str(_APP_DATA / "jiqtx_ledger.db"))

_SCHEMA = """
CREATE TABLE IF NOT EXISTS predictions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_ts   TEXT NOT NULL,
  asof         TEXT NOT NULL,
  due_date     TEXT NOT NULL,
  ticker       TEXT NOT NULL,
  asset_class  TEXT,
  archetype    TEXT,
  horizon_days INTEGER,
  price_at     REAL,
  grade        TEXT,
  prob_up      REAL,
  ci_lo        REAL,
  ci_hi        REAL,
  size         REAL,
  confidence   TEXT,
  dispersion   REAL,
  ml_verdict   TEXT,
  sim_prob     REAL,
  gates_failed TEXT,
  disabled     TEXT,
  config_hash  TEXT,
  n_trials     INTEGER,
  notes        TEXT,
  UNIQUE(ticker, asof, horizon_days, config_hash)
);
CREATE TABLE IF NOT EXISTS agent_views (
  pred_id INTEGER NOT NULL,
  agent   TEXT NOT NULL,
  role    TEXT,
  stance  TEXT,
  prob_up REAL,
  confidence REAL,
  veto    INTEGER,
  scope   TEXT,
  PRIMARY KEY (pred_id, agent),
  FOREIGN KEY (pred_id) REFERENCES predictions(id)
);
CREATE TABLE IF NOT EXISTS outcomes (
  pred_id     INTEGER PRIMARY KEY,
  scored_ts   TEXT NOT NULL,
  price_then  REAL,
  realized_ret REAL,
  realized_up INTEGER,
  brier       REAL,
  log_score   REAL,
  in_ci       INTEGER,
  FOREIGN KEY (pred_id) REFERENCES predictions(id)
);
CREATE TABLE IF NOT EXISTS agent_scores (
  pred_id INTEGER NOT NULL,
  agent   TEXT NOT NULL,
  brier   REAL,
  log_score REAL,
  PRIMARY KEY (pred_id, agent)
);
CREATE TABLE IF NOT EXISTS registry (
  config_hash TEXT PRIMARY KEY,
  registered_ts TEXT,
  payload TEXT
);
CREATE INDEX IF NOT EXISTS ix_pred_due ON predictions(due_date);
CREATE INDEX IF NOT EXISTS ix_pred_tk  ON predictions(ticker);
"""


def config_hash(payload: Dict) -> str:
    """사전등록 해시. 설정이 바뀌면 해시가 바뀌고 시행횟수 N에 카운트된다."""
    s = json.dumps(payload, sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:16]


class Ledger:
    def __init__(self, path: str = DEFAULT_PATH):
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self.path = path
        self.con = sqlite3.connect(path)
        self.con.row_factory = sqlite3.Row
        self.con.executescript(_SCHEMA)
        self.con.commit()

    # ------------------------------------------------------------ 사전등록

    def preregister(self, payload: Dict) -> str:
        h = config_hash(payload)
        self.con.execute(
            "INSERT OR IGNORE INTO registry(config_hash, registered_ts, payload)"
            " VALUES (?,?,?)",
            (h, datetime.utcnow().isoformat(timespec="seconds"),
             json.dumps(payload, ensure_ascii=False, default=str)))
        self.con.commit()
        return h

    def n_trials(self) -> int:
        """등록된 서로 다른 설정의 개수 = DSR 보정에 쓸 시행횟수."""
        r = self.con.execute("SELECT COUNT(*) c FROM registry").fetchone()
        return int(r["c"]) if r else 1

    # ------------------------------------------------------------ 기록

    def record(self, a, horizon_days: int = 21,
               cfg_payload: Optional[Dict] = None,
               notes: str = "") -> Optional[int]:
        """Analysis 를 원장에 기록. 이미 있으면 None."""
        v = a.verdict
        eq = getattr(a, "equity", None)
        asof = pd.Timestamp(a.asof)
        due = (asof + pd.tseries.offsets.BDay(horizon_days)).date().isoformat()
        payload = cfg_payload or {
            "ticker": a.ticker, "asset_class": a.classification.asset_class,
            "horizon": horizon_days, "version": "0.2",
        }
        h = self.preregister(payload)
        gates_failed = ",".join(r.code for r in a.gates.results if not r.passed)
        try:
            cur = self.con.execute(
                "INSERT INTO predictions (created_ts, asof, due_date, ticker,"
                " asset_class, archetype, horizon_days, price_at, grade,"
                " prob_up, ci_lo, ci_hi, size, confidence, dispersion,"
                " ml_verdict, sim_prob, gates_failed, disabled, config_hash,"
                " n_trials, notes)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (datetime.utcnow().isoformat(timespec="seconds"),
                 a.asof, due, a.ticker, a.classification.asset_class,
                 (eq.archetype if eq else None), horizon_days,
                 float(a.prices[-1]) if a.prices is not None else None,
                 v.grade, v.direction_prob,
                 v.direction_ci[0] if v.direction_ci else None,
                 v.direction_ci[1] if v.direction_ci else None,
                 v.risk_budget_weight, v.model_confidence, v.dispersion,
                 (a.ml.verdict if a.ml else None),
                 (a.sim.prob_up if a.sim else None),
                 gates_failed, ",".join(v.disabled_modules), h,
                 self.n_trials(), notes))
        except sqlite3.IntegrityError:
            return None
        pid = int(cur.lastrowid)
        for av in a.agent_views:
            self.con.execute(
                "INSERT OR REPLACE INTO agent_views"
                " (pred_id, agent, role, stance, prob_up, confidence, veto, scope)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (pid, av.agent, av.role, av.stance, av.prob_up,
                 av.confidence, int(av.veto), av.data_scope))
        self.con.commit()
        return pid

    # ------------------------------------------------------------ 채점

    def pending(self, as_of: Optional[str] = None) -> pd.DataFrame:
        as_of = as_of or datetime.utcnow().date().isoformat()
        q = ("SELECT p.* FROM predictions p LEFT JOIN outcomes o"
             " ON p.id=o.pred_id WHERE o.pred_id IS NULL AND p.due_date<=?")
        return pd.read_sql_query(q, self.con, params=(as_of,))

    def score(self, price_lookup: Callable[[str, str], Optional[float]],
              as_of: Optional[str] = None) -> Dict[str, int]:
        """
        price_lookup(ticker, date_iso) -> 종가 (없으면 None)
        지평이 지난 예측을 실현값으로 채점한다.
        """
        pend = self.pending(as_of)
        n_ok = n_skip = 0
        for _, row in pend.iterrows():
            px_then = price_lookup(row["ticker"], row["due_date"])
            if px_then is None or not np.isfinite(px_then) or \
                    row["price_at"] in (None, 0) or not np.isfinite(row["price_at"]):
                n_skip += 1
                continue
            ret = float(px_then) / float(row["price_at"]) - 1.0
            up = 1 if ret > 0 else 0
            p = row["prob_up"]
            if p is None or not np.isfinite(p):
                brier = log_s = None
                in_ci = None
            else:
                p = float(np.clip(p, 1e-6, 1 - 1e-6))
                brier = (p - up) ** 2
                log_s = -(up * math.log(p) + (1 - up) * math.log(1 - p))
                # CI 정합성: 구간이 0.5를 포함하면 '방향 주장 없음' → 채점 제외(NULL).
                # 0.5를 배제한 '확신 있는 방향 주장'만 채점한다.
                # (이전 정의는 사실상 항상 참이라 정보가 없었다 — 원장이 잡아낸 결함)
                lo, hi = row["ci_lo"], row["ci_hi"]
                in_ci = None
                if lo is not None and hi is not None and \
                        np.isfinite(lo) and np.isfinite(hi):
                    if lo > 0.5:
                        in_ci = int(up == 1)
                    elif hi < 0.5:
                        in_ci = int(up == 0)
            self.con.execute(
                "INSERT OR REPLACE INTO outcomes (pred_id, scored_ts,"
                " price_then, realized_ret, realized_up, brier, log_score, in_ci)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (int(row["id"]), datetime.utcnow().isoformat(timespec="seconds"),
                 float(px_then), ret, up, brier, log_s, in_ci))
            # 에이전트별 채점
            for av in self.con.execute(
                    "SELECT agent, prob_up FROM agent_views WHERE pred_id=?",
                    (int(row["id"]),)):
                ap = av["prob_up"]
                if ap is None or not np.isfinite(ap):
                    continue
                ap = float(np.clip(ap, 1e-6, 1 - 1e-6))
                self.con.execute(
                    "INSERT OR REPLACE INTO agent_scores"
                    " (pred_id, agent, brier, log_score) VALUES (?,?,?,?)",
                    (int(row["id"]), av["agent"], (ap - up) ** 2,
                     -(up * math.log(ap) + (1 - up) * math.log(1 - ap))))
            n_ok += 1
        self.con.commit()
        return {"scored": n_ok, "skipped": n_skip,
                "pending_remaining": len(self.pending(as_of))}

    # ------------------------------------------------------------ 가중치 환류

    def agent_weights(self, min_n: int = 15, floor: float = 0.15,
                      cap: float = 2.5) -> Dict[str, float]:
        """
        에이전트별 Brier skill(기저율 대비)을 가중치로 변환.
        자기 확신이 아니라 **실적**이 가중치를 정한다.
        표본이 부족한 에이전트는 1.0(중립)을 받는다.
        """
        q = """SELECT s.agent, s.brier, o.realized_up
               FROM agent_scores s JOIN outcomes o ON s.pred_id=o.pred_id"""
        df = pd.read_sql_query(q, self.con)
        if len(df) == 0:
            return {}
        base = float(df["realized_up"].mean())
        base_brier = base * (1 - base)
        out: Dict[str, float] = {}
        for agent, g in df.groupby("agent"):
            if len(g) < min_n or base_brier <= 0:
                out[agent] = 1.0
                continue
            skill = 1.0 - float(g["brier"].mean()) / base_brier
            # skill in (-inf, 1]. 0 이하면 무가치 → floor
            w = floor + (cap - floor) * max(min(skill, 1.0), 0.0)
            out[agent] = float(w)
        return out

    # ------------------------------------------------------------ 리포트

    def calibration_report(self) -> Dict[str, pd.DataFrame]:
        q = """SELECT p.*, o.realized_ret, o.realized_up, o.brier,
                      o.log_score, o.in_ci
               FROM predictions p JOIN outcomes o ON p.id=o.pred_id"""
        d = pd.read_sql_query(q, self.con)
        res: Dict[str, pd.DataFrame] = {}
        if len(d) == 0:
            return res
        base = float(d["realized_up"].mean())
        bb = base * (1 - base)

        def agg(g):
            has_p = g["brier"].notna()
            return pd.Series({
                "n": len(g),
                "확률제출": int(has_p.sum()),
                "기저율": float(g["realized_up"].mean()),
                "평균수익": float(g["realized_ret"].mean()),
                "Brier": float(g.loc[has_p, "brier"].mean()) if has_p.any() else np.nan,
                "Brier skill": (1 - float(g.loc[has_p, "brier"].mean()) / bb)
                if has_p.any() and bb > 0 else np.nan,
                "방향적중": float(((g["prob_up"] > 0.5).astype(int) ==
                                g["realized_up"])[has_p].mean())
                if has_p.any() else np.nan,
                "확신주장수": int(g["in_ci"].notna().sum()),
                "확신주장 적중": float(g["in_ci"].mean())
                if g["in_ci"].notna().any() else np.nan,
            })

        res["overall"] = d.groupby(lambda _: "전체").apply(agg, include_groups=False) \
            if hasattr(pd.core.groupby.DataFrameGroupBy, "apply") else pd.DataFrame()
        for key, name in (("asset_class", "자산군"), ("archetype", "아키타입"),
                          ("grade", "등급"), ("confidence", "모델신뢰도"),
                          ("ml_verdict", "ML판정")):
            if key in d.columns and d[key].notna().any():
                res[name] = d.groupby(key).apply(agg, include_groups=False)

        # 에이전트별
        qa = """SELECT s.agent, s.brier, s.log_score, o.realized_up,
                       v.prob_up, v.stance
                FROM agent_scores s
                JOIN outcomes o ON s.pred_id=o.pred_id
                JOIN agent_views v ON v.pred_id=s.pred_id AND v.agent=s.agent"""
        da = pd.read_sql_query(qa, self.con)
        if len(da):
            def agg_a(g):
                return pd.Series({
                    "n": len(g),
                    "Brier": float(g["brier"].mean()),
                    "Brier skill": 1 - float(g["brier"].mean()) / bb if bb > 0 else np.nan,
                    "방향적중": float(((g["prob_up"] > 0.5).astype(int) ==
                                    g["realized_up"]).mean()),
                    "평균제출확률": float(g["prob_up"].mean()),
                })
            res["에이전트"] = da.groupby("agent").apply(agg_a, include_groups=False) \
                .sort_values("Brier skill", ascending=False)

        # 등급별 실현수익 (경제적 가치)
        res["등급별 실현수익"] = d.groupby("grade").agg(
            n=("realized_ret", "size"),
            평균수익=("realized_ret", "mean"),
            중앙값=("realized_ret", "median"),
            승률=("realized_up", "mean"),
            평균사이즈=("size", "mean")).sort_values("평균수익", ascending=False)
        return res

    def summary(self) -> Dict[str, Any]:
        p = self.con.execute("SELECT COUNT(*) c FROM predictions").fetchone()["c"]
        o = self.con.execute("SELECT COUNT(*) c FROM outcomes").fetchone()["c"]
        return {"predictions": int(p), "scored": int(o),
                "pending": int(p) - int(o), "n_trials": self.n_trials(),
                "path": self.path}

    def close(self):
        self.con.close()


# ---------------------------------------------------------------- 헬퍼

def price_lookup_from_frame(prices: Dict[str, pd.Series]
                            ) -> Callable[[str, str], Optional[float]]:
    """{ticker: 종가 시리즈} 로부터 조회 함수를 만든다(리플레이·백테스트용)."""
    def _lk(ticker: str, date_iso: str) -> Optional[float]:
        s = prices.get(ticker)
        if s is None or len(s) == 0:
            return None
        d = pd.Timestamp(date_iso)
        idx = s.index[s.index <= d]
        if len(idx) == 0:
            return None
        return float(s.loc[idx[-1]])
    return _lk


def price_lookup_yfinance(cache: Optional[Dict] = None
                          ) -> Callable[[str, str], Optional[float]]:
    """실데이터용 조회 함수."""
    cache = cache if cache is not None else {}

    def _lk(ticker: str, date_iso: str) -> Optional[float]:
        if ticker not in cache:
            try:
                import yfinance as yf
                h = yf.Ticker(ticker).history(period="3y", auto_adjust=True)
                h.index = pd.DatetimeIndex(h.index).tz_localize(None)
                cache[ticker] = h["Close"].astype(float)
            except Exception:
                cache[ticker] = pd.Series(dtype=float)
        s = cache[ticker]
        if len(s) == 0:
            return None
        d = pd.Timestamp(date_iso)
        idx = s.index[s.index <= d]
        return float(s.loc[idx[-1]]) if len(idx) else None
    return _lk
