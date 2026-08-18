"""
auto_analyze.py — P4: 통합 AUTO 분석 오케스트레이터
============================================================
1버튼으로 다음을 자동 실행 후 통합 dict + HTML 리포트 반환:

  1) ANALYZE          (기관급 분석)        — 실패해도 진행
  2) Active 백테스트   (사용자 활성 전략들)
  3) BATCH grid       (벡터화 전략 전종)
  4) MULTI            (관심종목 cross-sectional)
  5) OF+PEAD          (orderflow + 이벤트)
  6) Walk-Forward     (강건성)
  7) 최종 추천         (top 후보 + 신뢰도)

각 단계는 try/except로 격리 — 일부 실패해도 다른 결과는 보존.
스레드 풀로 병렬 실행. 전체 60~120초 소요 가능.
"""
from __future__ import annotations

import json
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional


# ════════════════════════════════════════════════════════════
#  개별 단계 실행기 (모두 dict 반환, 실패 시 error 키)
# ════════════════════════════════════════════════════════════
def _step_active_backtest(ticker: str, user_id: Optional[int],
                          period_days: int, interval: str) -> Dict[str, Any]:
    """사용자 활성 전략 백테스트."""
    if not user_id:
        return {"ok": False, "skipped": True, "reason": "비로그인"}
    try:
        from .active_store import list_active
        from .vbt_runner import run_backtest
        actives = list_active(user_id, ticker=ticker)
        if not actives:
            return {"ok": True, "count": 0, "results": [],
                    "note": "활성 전략 없음"}
        results = []
        for a in actives[:5]:  # 안전 — 최대 5개
            try:
                r = run_backtest(
                    ticker=ticker,
                    strategy=a.get("strategy"),
                    params=a.get("params") or {},
                    period_days=period_days, interval=interval,
                )
                if r.get("ok"):
                    m = r["metrics"]
                    results.append({
                        "id":           a.get("id"),
                        "strategy":     a.get("strategy"),
                        "params":       a.get("params"),
                        "total_return": m.get("total_return"),
                        "sharpe":       m.get("sharpe"),
                        "max_drawdown": m.get("max_drawdown"),
                        "n_trades":     m.get("n_trades"),
                        "psr":          m.get("psr"),
                    })
            except Exception:
                pass
        return {"ok": True, "count": len(results), "results": results}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _step_batch(ticker: str, period_days: int, interval: str) -> Dict[str, Any]:
    """모든 벡터화 전략 batch_grid."""
    try:
        from .vbt_vectorized import batch_grid, VECTORIZABLE, _PARAM_NAMES
        strategies = list(VECTORIZABLE)
        # 기본 grid — 각 전략의 기본 파라미터로 가벼운 탐색
        grids = {}
        # batch_grid는 빈 grid면 엔진 내장 기본값 사용
        r = batch_grid(
            strategies=strategies, grids=grids, ticker=ticker,
            period_days=period_days, interval=interval, top_n_per=3,
        )
        return r
    except Exception as e:
        return {"ok": False, "error": str(e),
                "trace": traceback.format_exc()[-400:]}


def _step_multi(ticker: str, tickers: List[str],
                period_days: int, interval: str) -> Dict[str, Any]:
    """관심종목 cross-sectional. tickers 비어 있으면 메인 ticker만."""
    try:
        from .multi_asset import multi_asset_backtest, MULTI_ASSET_SUPPORTED
        # ticker가 tickers에 없으면 추가
        tks = [t.upper() for t in (tickers or [])]
        if ticker.upper() not in tks:
            tks = [ticker.upper()] + tks
        tks = tks[:10]   # 안전 cap
        # 다중자산 지원 전략 7~9개 중 핵심 4개만 (속도)
        strategies = [s for s in
                      ["sma_cross", "rsi_mr", "macd", "bb_mean"]
                      if s in MULTI_ASSET_SUPPORTED]
        if not strategies:
            strategies = (MULTI_ASSET_SUPPORTED or ["sma_cross"])[:4]
        r = multi_asset_backtest(
            tickers=tks, strategies=strategies,
            period_days=period_days, interval=interval,
        )
        return r
    except Exception as e:
        return {"ok": False, "error": str(e),
                "trace": traceback.format_exc()[-400:]}


def _step_of_pead(ticker: str, period_days: int,
                  interval: str) -> Dict[str, Any]:
    """OF + PEAD + Hybrid 3전략 (차트 dict 포함)."""
    try:
        from ..orderflow_pead.main import build_bundle
        from ..orderflow_pead import (
            OrderflowDeltaStrategy, PEADStrategy,
            Hybrid_OF_PEAD_Strategy, run_backtest as of_run,
        )
        try:
            bundle = build_bundle(ticker, period_days=period_days,
                                   interval=interval)
        except Exception as e:
            return {"ok": False,
                    "error": f"bundle 빌드 실패: {type(e).__name__}: {e}"}

        out: Dict[str, Any] = {"ok": True, "strategies": {}}
        # delta 임계
        try:
            of_df = bundle.get("orderflow")
            if of_df is None or "delta" not in of_df.columns or of_df.empty:
                avg_d = 100.0
            else:
                avg_d = float(of_df["delta"].abs().mean())
                if not (avg_d > 0):
                    avg_d = 100.0
        except Exception:
            avg_d = 100.0

        # PEAD는 earnings 데이터가 있을 때만 실행
        ev_df = bundle.get("earnings")
        has_earnings = ev_df is not None and not ev_df.empty

        candidates = [
            ("orderflow", OrderflowDeltaStrategy(
                delta_threshold=max(avg_d * 1.2, 100))),
        ]
        if has_earnings:
            candidates.append(("pead", PEADStrategy(sue_top_pct=0.2, drift_days=30)))
            candidates.append(("hybrid", Hybrid_OF_PEAD_Strategy()))
        else:
            out["strategies"]["pead"] = {"ok": False,
                "error": "earnings 데이터 없음 (소형주/ETF 등)"}
            out["strategies"]["hybrid"] = {"ok": False,
                "error": "earnings 데이터 없음 — hybrid skip"}

        for label, strat in candidates:
            try:
                res = of_run(strat, bundle)
                m = res.get("metrics", {})
                out["strategies"][label] = {
                    "ok": True,
                    "total_return": m.get("total_return"),
                    "sharpe":       m.get("sharpe"),
                    "sortino":      m.get("sortino"),
                    "max_drawdown": m.get("max_drawdown"),
                    "win_rate":     m.get("win_rate"),
                    "n_trades":     m.get("n_trades"),
                }
            except Exception as e:
                out["strategies"][label] = {
                    "ok": False,
                    "error": f"{type(e).__name__}: {e}",
                }
        return out
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def _step_wfa(ticker: str, period_days: int) -> Dict[str, Any]:
    """Walk-Forward 강건성 (OF 전략 기준 간소화).
    AUTO 안에서는 가벼운 grid (2×2)만 — 무거운 grid는 OF/PEAD 탭에서.
    """
    try:
        from ..orderflow_pead.main import build_bundle
        from ..orderflow_pead import (
            OrderflowDeltaStrategy, walk_forward_optimization,
        )
        # 기간 cap — WFA는 무거우므로 720일 이내로 클램프
        capped = min(period_days, 720)
        bundle = build_bundle(ticker, period_days=capped)
        wfa = walk_forward_optimization(
            OrderflowDeltaStrategy,
            {"delta_threshold": [500, 1500],   # 3→2
             "max_hold_bars":   [20, 40]},
            bundle, n_folds=2,                  # 3→2
        )
        return {"ok": True, "wfa": wfa}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def _step_ml(ticker: str, period_days: int, interval: str) -> Dict[str, Any]:
    """ML 시그널 백테스트 — RF 분류 (속도 우선, 신경망은 너무 느림).
    수동 ML 탭은 더 강력한 모델 선택 가능."""
    try:
        from .ml_predict import ml_signal_backtest
        # RF 분류 — 빠르고 안정적 (LSTM은 AUTO에는 부담)
        r = ml_signal_backtest(
            ticker=ticker, model_type="rf", task="classification",
            period_days=period_days, interval=interval,
            horizon=5, epochs=20,
            buy_thresh=0.55, sell_thresh=0.45,
        )
        if not r.get("ok"):
            return {"ok": False, "error": r.get("error", "ml 실패")}
        m = r.get("metrics", {})
        return {
            "ok": True,
            "model":        r.get("model_type"),
            "task":         r.get("task"),
            "device":       r.get("device"),
            "total_return": m.get("total_return"),
            "alpha":        m.get("alpha"),
            "sharpe":       m.get("sharpe"),
            "max_drawdown": m.get("max_drawdown"),
            "n_trades":     m.get("n_trades"),
        }
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def _step_kronos(ticker: str, period_days: int, interval: str,
                  model_key: str = "kronos_small") -> Dict[str, Any]:
    """🌌 Kronos foundation model 예측 — 미래 N봉 방향 + 수익률.

    Strategy가 아니라 forecaster — candidates에는 안 들어감.
    대신 recommendation의 신뢰도 점수에 반영.
    """
    try:
        from .kronos_predictor import (kronos_predict,
                                         check_model_downloaded)
        # 모델 미설치 시 skip
        chk = check_model_downloaded(model_key)
        if not chk.get("downloaded"):
            return {"ok": False, "skipped": True,
                    "reason": f"{model_key} 미다운로드 — HUB ▸ 🌌 Kronos 탭에서 설치"}
        from .vbt_runner import _fetch_ohlcv
        # period_days가 짧으면 자동 보강 (lookback 256 + 여유 = 일봉 기준 ~400일 권장)
        eff_days = max(period_days, 500)
        df = _fetch_ohlcv(ticker, period_days=eff_days, interval=interval)
        # 봉수가 lookback 미만이면 lookback 자동 축소 (분봉/짧은 기간 대응)
        n = 0 if df is None else len(df)
        eff_lookback = min(256, max(64, n - 15))   # 최소 64
        if df is None or n < eff_lookback + 10:
            return {"ok": False,
                    "error": f"데이터 부족 (n={n} < {eff_lookback+10})"}
        # 5스텝 예측 (CPU에서 0.3~3초)
        r = kronos_predict(df, model_key=model_key,
                            lookback=eff_lookback, pred_len=5,
                            sample_count=1)
        if not r.get("ok"):
            return {"ok": False, "error": r.get("error") or "예측 실패"}
        return {
            "ok":                  True,
            "model":               r.get("model"),
            "device":              r.get("device"),
            "direction":           r.get("direction"),
            "predicted_return":    r.get("predicted_return"),
            "last_close":          r.get("last_close"),
            "predicted_end_close": r.get("predicted_end_close"),
            "pred_len":            r.get("pred_len"),
            "elapsed_sec":         r.get("elapsed_sec"),
        }
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def _step_analyze(ticker: str) -> Dict[str, Any]:
    """기관급 ANALYZE (signal_engine + explain)."""
    try:
        # signal_engine는 보통 analyze 라우트에서 비동기 실행 — 여기서는 동기 실행
        from engine.signal_engine import analyze as _sig_analyze
        r = _sig_analyze(ticker)
        # 큰 dict일 수 있으므로 핵심만 추출
        return {
            "ok": True,
            "verdict":     r.get("verdict") if isinstance(r, dict) else None,
            "confidence":  r.get("confidence") if isinstance(r, dict) else None,
            "summary":     (r.get("summary") if isinstance(r, dict) else
                            str(r)[:500]),
            "evidence_n":  (len(r.get("evidence") or [])
                            if isinstance(r, dict) else 0),
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ════════════════════════════════════════════════════════════
#  추천 엔진 — 모든 결과 종합 → top 후보 + 신뢰도
# ════════════════════════════════════════════════════════════
def _build_recommendation(steps: Dict[str, Any]) -> Dict[str, Any]:
    """모든 단계 결과를 종합해 top 3 전략 추천 + 신뢰도 계산."""
    candidates: List[Dict[str, Any]] = []

    # 1) BATCH leaderboard
    batch = steps.get("batch") or {}
    if batch.get("ok"):
        for it in (batch.get("leaderboard") or [])[:5]:
            candidates.append({
                "source": "BATCH",
                "strategy":     it.get("strategy"),
                "params":       it.get("params"),
                "sharpe":       it.get("sharpe", 0),
                "total_return": it.get("total_return", 0),
                "max_drawdown": it.get("max_drawdown", 0),
                "n_trades":     it.get("n_trades", 0),
            })

    # 2) Active 백테스트
    active = steps.get("active") or {}
    if active.get("ok"):
        for it in (active.get("results") or []):
            candidates.append({
                "source": "ACTIVE",
                "strategy":     it.get("strategy"),
                "params":       it.get("params"),
                "sharpe":       it.get("sharpe", 0),
                "total_return": it.get("total_return", 0),
                "max_drawdown": it.get("max_drawdown", 0),
                "n_trades":     it.get("n_trades", 0),
                "psr":          it.get("psr"),
            })

    # 3) OF+PEAD
    ofp = steps.get("of_pead") or {}
    for label, m in (ofp.get("strategies") or {}).items():
        if isinstance(m, dict) and m.get("ok"):
            candidates.append({
                "source": "OF+PEAD",
                "strategy":     label,
                "params":       {},
                "sharpe":       m.get("sharpe", 0) or 0,
                "total_return": m.get("total_return", 0) or 0,
                "max_drawdown": m.get("max_drawdown", 0) or 0,
                "n_trades":     m.get("n_trades", 0) or 0,
            })

    # 4) ML signal — RF 분류 백테스트 (D#10)
    ml = steps.get("ml") or {}
    if ml.get("ok"):
        candidates.append({
            "source": "ML",
            "strategy":     f"ml_{ml.get('model') or 'rf'}",
            "params":       {"task": ml.get("task"),
                              "device": ml.get("device")},
            "sharpe":       ml.get("sharpe", 0) or 0,
            "total_return": ml.get("total_return", 0) or 0,
            "max_drawdown": ml.get("max_drawdown", 0) or 0,
            "n_trades":     ml.get("n_trades", 0) or 0,
        })

    # 정렬 — Sharpe 우선, 동률 시 alpha (total_return)
    candidates.sort(
        key=lambda x: (x.get("sharpe") or 0, x.get("total_return") or 0),
        reverse=True,
    )
    top = candidates[:3]

    # 신뢰도 — Sharpe + WFA OOS + 후보 수 + ANALYZE 합산
    score = 0.0
    rationale: List[str] = []
    if top:
        best_sh = float(top[0].get("sharpe") or 0)
        if best_sh >= 1.5:
            score += 35; rationale.append(f"최고 Sharpe {best_sh:.2f} (≥1.5 우수)")
        elif best_sh >= 1.0:
            score += 25; rationale.append(f"최고 Sharpe {best_sh:.2f} (≥1.0 양호)")
        elif best_sh >= 0.5:
            score += 15; rationale.append(f"최고 Sharpe {best_sh:.2f} (≥0.5 보통)")
        else:
            rationale.append(f"최고 Sharpe {best_sh:.2f} (낮음)")

    wfa = (steps.get("wfa") or {}).get("wfa") or {}
    oos = wfa.get("avg_oos_sharpe")
    if oos is not None:
        try:
            oos_v = float(oos)
            if oos_v >= 1.0:
                score += 25; rationale.append(f"WFA OOS Sharpe {oos_v:.2f} (강건)")
            elif oos_v >= 0.5:
                score += 15; rationale.append(f"WFA OOS Sharpe {oos_v:.2f} (보통)")
            else:
                rationale.append(f"WFA OOS Sharpe {oos_v:.2f} (취약)")
        except Exception:
            pass

    n_can = len(candidates)
    if n_can >= 10:
        score += 15; rationale.append(f"후보 {n_can}개 (다양성 충분)")
    elif n_can >= 5:
        score += 10; rationale.append(f"후보 {n_can}개")
    else:
        rationale.append(f"후보 {n_can}개 (부족)")

    az = steps.get("analyze") or {}
    if az.get("ok"):
        score += 10
        v = az.get("verdict") or "—"
        c = az.get("confidence")
        rationale.append(f"ANALYZE verdict={v}" +
                          (f" conf={c}" if c is not None else ""))

    # MULTI 양호한 종목 확인
    multi = steps.get("multi") or {}
    if multi.get("ok"):
        n_ok = sum(1 for t, v in (multi.get("results") or {}).items()
                    if isinstance(v, dict) and v.get("ok"))
        if n_ok >= 3:
            score += 10; rationale.append(f"MULTI {n_ok}종목 검증 통과")

    # ML 시그널 추가 검증 (D#10)
    ml_step = steps.get("ml") or {}
    if ml_step.get("ok"):
        ml_alpha = ml_step.get("alpha")
        ml_dev = ml_step.get("device", "?")
        if ml_alpha is not None and ml_alpha > 0:
            score += 8
            rationale.append(f"ML 알파 +{ml_alpha*100:.1f}% ({ml_dev})")

    # 🌌 Kronos foundation model (9단계 — forecast 방향 + Top 전략 합의)
    kronos = steps.get("kronos") or {}
    if kronos.get("ok"):
        pr = float(kronos.get("predicted_return") or 0)
        abs_pr = abs(pr)
        if abs_pr >= 0.03:
            score += 12
            rationale.append(f"🌌 Kronos 예측 {pr*100:+.1f}% (강한 시그널)")
        elif abs_pr >= 0.01:
            score += 6
            rationale.append(f"🌌 Kronos 예측 {pr*100:+.1f}%")
        else:
            rationale.append(f"🌌 Kronos 예측 {pr*100:+.1f}% (횡보)")
        # Top 전략과 방향 합의 보너스
        if top:
            kronos_bull = pr > 0
            bull_n = sum(1 for t in top
                          if (t.get("total_return") or 0) > 0)
            bear_n = sum(1 for t in top
                          if (t.get("total_return") or 0) < 0)
            if kronos_bull and bull_n >= 2:
                score += 8
                rationale.append("🤝 Kronos + Top 전략 합의 (상승 다수)")
            elif (not kronos_bull) and bear_n >= 2:
                score += 8
                rationale.append("🤝 Kronos + Top 전략 합의 (하락 다수)")
    elif kronos.get("skipped"):
        rationale.append(f"🌌 Kronos 미설치 (점수 반영 없음)")

    score = min(score, 100.0)
    grade = ("A" if score >= 80 else
             "B" if score >= 60 else
             "C" if score >= 40 else
             "D" if score >= 20 else "F")
    return {
        "top":        top,
        "score":      round(score, 1),
        "grade":      grade,
        "rationale":  rationale,
        "n_candidates": n_can,
    }


# ════════════════════════════════════════════════════════════
#  통합 실행 (병렬)
# ════════════════════════════════════════════════════════════
def auto_analyze(ticker: str,
                 user_id: Optional[int] = None,
                 period_days: int = 730,
                 interval: str = "1d",
                 watchlist_tickers: Optional[List[str]] = None,
                 run_wfa: bool = True,
                 run_of_pead: bool = True,
                 run_analyze: bool = True,
                 run_ml: bool = True,
                 run_kronos: bool = True,
                 kronos_model: str = "kronos_small",
                 ) -> Dict[str, Any]:
    """모든 단계 병렬 실행 후 통합 결과 반환."""
    t0 = time.time()
    ticker = (ticker or "").upper().strip()
    if not ticker:
        return {"ok": False, "error": "ticker 비어 있음"}
    steps: Dict[str, Any] = {}

    # 병렬 작업 정의
    jobs: Dict[str, Any] = {
        "active": (lambda: _step_active_backtest(
            ticker, user_id, period_days, interval)),
        "batch":  (lambda: _step_batch(ticker, period_days, interval)),
        "multi":  (lambda: _step_multi(ticker, watchlist_tickers or [],
                                        period_days, interval)),
    }
    if run_of_pead:
        jobs["of_pead"] = (lambda: _step_of_pead(ticker, period_days, interval))
    if run_wfa:
        jobs["wfa"]     = (lambda: _step_wfa(ticker, period_days))
    if run_analyze:
        jobs["analyze"] = (lambda: _step_analyze(ticker))
    if run_ml:
        jobs["ml"]      = (lambda: _step_ml(ticker, period_days, interval))
    if run_kronos:
        jobs["kronos"]  = (lambda: _step_kronos(ticker, period_days,
                                                 interval, kronos_model))

    # ThreadPool — 최대 7작업 동시 (9단계 = 3+4 = 활성+병렬)
    # 단계별 timeout 60s, 전체 wall-clock cap 220s
    PER_STEP_TIMEOUT = 60
    OVERALL_DEADLINE = t0 + 220.0
    with ThreadPoolExecutor(max_workers=min(7, len(jobs))) as ex:
        future_map = {ex.submit(fn): name for name, fn in jobs.items()}
        for fut in as_completed(future_map):
            name = future_map[fut]
            remaining = max(1.0, OVERALL_DEADLINE - time.time())
            wait_t = min(PER_STEP_TIMEOUT, remaining)
            try:
                steps[name] = fut.result(timeout=wait_t)
                if not isinstance(steps[name], dict):
                    steps[name] = {"ok": False,
                                   "error": f"unexpected return type {type(steps[name]).__name__}"}
            except Exception as e:
                steps[name] = {"ok": False,
                               "error": f"timeout/실패: {type(e).__name__}: {e}",
                               "step_wait_sec": round(wait_t, 1)}
            # 전체 deadline 초과 시 나머지 future 강제 종료 표시
            if time.time() >= OVERALL_DEADLINE:
                for f2, n2 in future_map.items():
                    if n2 not in steps:
                        steps[n2] = {"ok": False,
                                     "error": "전체 wall-clock 220s 초과로 강제 중단"}
                        f2.cancel()
                break

    # 추천 종합
    recommendation = _build_recommendation(steps)

    return {
        "ok":             True,
        "ticker":         ticker,
        "period_days":    period_days,
        "interval":       interval,
        "elapsed_sec":    round(time.time() - t0, 1),
        "steps":          steps,
        "recommendation": recommendation,
    }


# ════════════════════════════════════════════════════════════
#  HTML 리포트 — 한 페이지 통합 보고
# ════════════════════════════════════════════════════════════
def build_html_report(result: Dict[str, Any]) -> str:
    """auto_analyze 결과 → 단일 HTML 문자열 (다운로드/공유용)."""
    if not result or not result.get("ok"):
        return f"<html><body><h1>분석 실패</h1><pre>{result}</pre></body></html>"
    rec = result.get("recommendation") or {}
    steps = result.get("steps") or {}
    ticker = result.get("ticker")
    elapsed = result.get("elapsed_sec", 0)

    grade = rec.get("grade", "—")
    score = rec.get("score", 0)
    grade_color = {"A": "#3df0ff", "B": "#65c1cf", "C": "#ffb44c",
                   "D": "#ff9551", "F": "#ff5a52"}.get(grade, "#cfe6ec")

    def _pct(v):
        if v is None: return "—"
        try: return f"{float(v)*100:+.2f}%"
        except: return str(v)

    def _num(v, d=2):
        if v is None: return "—"
        try: return f"{float(v):.{d}f}"
        except: return str(v)

    top_rows = ""
    for i, t in enumerate(rec.get("top") or [], 1):
        top_rows += (
            f"<tr><td>{i}</td><td><b>{t.get('strategy','—')}</b></td>"
            f"<td>{t.get('source')}</td>"
            f"<td style='text-align:right;color:#3df0ff'>{_num(t.get('sharpe'))}</td>"
            f"<td style='text-align:right'>{_pct(t.get('total_return'))}</td>"
            f"<td style='text-align:right;color:#ff9551'>{_pct(t.get('max_drawdown'))}</td>"
            f"<td style='text-align:right'>{t.get('n_trades','—')}</td>"
            f"<td><code style='font-size:10px;color:#5d7480'>"
            f"{json.dumps(t.get('params') or {}, ensure_ascii=False)}</code></td></tr>"
        )

    rationale_html = "<ul>" + "".join(
        f"<li>{r}</li>" for r in rec.get("rationale") or []) + "</ul>"

    # 단계별 요약
    step_summary = ""
    for name, s in steps.items():
        if not isinstance(s, dict):
            continue
        if s.get("ok"):
            mini = ""
            if name == "batch":
                mini = f"전략 {s.get('n_vectorized',0)}개 벡터화, leaderboard {len(s.get('leaderboard') or [])}건"
            elif name == "multi":
                n_ok = sum(1 for v in (s.get('results') or {}).values()
                           if isinstance(v, dict) and v.get('ok'))
                mini = f"{n_ok}종목 검증"
            elif name == "of_pead":
                ok_str = sum(1 for v in (s.get('strategies') or {}).values()
                             if isinstance(v, dict) and v.get('ok'))
                mini = f"{ok_str}/3 전략 OK"
            elif name == "wfa":
                w = s.get('wfa') or {}
                mini = f"avg OOS Sharpe = {w.get('avg_oos_sharpe', '—')}"
            elif name == "active":
                mini = f"{s.get('count', 0)}개 활성 전략"
            elif name == "analyze":
                mini = f"verdict={s.get('verdict','—')}"
            elif name == "kronos":
                pr = s.get("predicted_return")
                pr_str = f"{pr*100:+.2f}%" if pr is not None else "—"
                mini = (f"{s.get('direction','—')} {pr_str} · "
                         f"{s.get('model','—')} ({s.get('device','—')})")
            step_summary += (
                f"<div class='step ok'><b>✓ {name.upper()}</b> {mini}</div>")
        elif s.get("skipped"):
            step_summary += (
                f"<div class='step skip'>○ {name.upper()} 건너뜀: "
                f"{s.get('reason','—')}</div>")
        else:
            step_summary += (
                f"<div class='step fail'>✗ {name.upper()} 실패: "
                f"{(s.get('error') or '—')[:120]}</div>")

    raw_json = json.dumps(result, ensure_ascii=False, indent=2, default=str)

    return f"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8">
<title>{ticker} · AUTO 분석 리포트</title>
<style>
  body{{background:#05070a;color:#cfe6ec;font-family:'IBM Plex Sans KR',sans-serif;
       margin:0;padding:24px;max-width:1100px;margin:0 auto}}
  h1{{font-family:'JetBrains Mono',monospace;color:#3df0ff;letter-spacing:2px;
      border-bottom:1px solid #16202e;padding-bottom:10px}}
  h2{{color:#3df0ff;border-left:3px solid #3df0ff;padding-left:10px;margin-top:32px}}
  table{{width:100%;border-collapse:collapse;margin-top:8px;
         font-family:'JetBrains Mono',monospace;font-size:12px}}
  th{{background:#0a0f16;color:#3df0ff;padding:8px;text-align:left;
      border-bottom:1px solid #16202e;font-size:10px;letter-spacing:1px}}
  td{{padding:7px 8px;border-bottom:1px dashed #16202e}}
  .grade{{display:inline-block;font-size:48px;font-weight:900;
          color:{grade_color};margin-right:14px;font-family:'JetBrains Mono',monospace}}
  .score{{font-family:'JetBrains Mono',monospace;font-size:20px;color:#cfe6ec}}
  .step{{padding:6px 12px;margin:4px 0;border-radius:2px;font-family:'JetBrains Mono',monospace;font-size:12px}}
  .step.ok  {{background:rgba(61,240,255,.08);border-left:2px solid #3df0ff}}
  .step.skip{{background:rgba(255,180,76,.06);border-left:2px solid #ffb44c}}
  .step.fail{{background:rgba(255,90,82,.06);border-left:2px solid #ff5a52;color:#ff9b95}}
  pre{{background:#0a0f16;padding:12px;border:1px solid #16202e;
       max-height:400px;overflow:auto;font-size:10.5px;border-radius:2px}}
  .meta{{color:#5d7480;font-size:11px;font-family:'JetBrains Mono',monospace;margin-top:6px}}
  ul{{font-size:13px;line-height:1.7}}
</style></head><body>
<h1>{ticker} · AUTO 통합 분석</h1>
<div class="meta">기간 {result.get('period_days')}일 · 봉 {result.get('interval')} · 소요 {elapsed}초</div>

<h2>종합 등급</h2>
<div><span class="grade">{grade}</span><span class="score">{score} / 100</span></div>
{rationale_html}

<h2>추천 Top 3 전략</h2>
<table>
  <thead><tr><th>#</th><th>전략</th><th>출처</th><th style='text-align:right'>Sharpe</th>
    <th style='text-align:right'>수익률</th><th style='text-align:right'>MDD</th>
    <th style='text-align:right'>거래수</th><th>파라미터</th></tr></thead>
  <tbody>{top_rows or '<tr><td colspan=8>후보 없음</td></tr>'}</tbody>
</table>

<h2>실행 단계 요약</h2>
{step_summary or '<div class="step fail">실행된 단계 없음</div>'}

<h2>전체 결과 (raw)</h2>
<pre>{raw_json}</pre>

<div class="meta" style="margin-top:30px;border-top:1px solid #16202e;padding-top:12px">
  Generated by JIQT AUTO Analyzer · {time.strftime('%Y-%m-%d %H:%M:%S')}
</div>
</body></html>
"""
