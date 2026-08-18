"""
ml_predict.py — D#10 ML 가격예측 + ML 시그널 백테스트
============================================================
engine.ml.Trainer (RF/XGB/LSTM/GRU/Transformer)를 활용:

  - train_predict_model      : 학습 → 미래 N봉 예측 + Plotly 차트
  - ml_signal_backtest       : ML 예측을 매수/매도 시그널로 변환 → 백테스트
  - get_device_info          : GPU/CPU + 메모리 정보 (UI 표시용)
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd


# Plotly 다크 헬퍼
_BG_PAPER = "#05070a"; _BG_PLOT = "#0a0f16"
_TXT = "#cfe6ec";      _TXT_DIM = "#5d7480"
_GRID = "#16202e";     _CYAN = "#3df0ff"
_UP = "#ff5a52";       _DOWN = "#4d9cff";  _AMBER = "#ffb44c"


def _layout(title: str, height: int = 380) -> Dict[str, Any]:
    return {
        "title": {"text": title, "font": {"color": _CYAN, "size": 12},
                  "x": 0.02, "xanchor": "left"},
        "paper_bgcolor": _BG_PAPER, "plot_bgcolor": _BG_PLOT,
        "font": {"color": _TXT, "size": 10,
                 "family": "JetBrains Mono, monospace"},
        "xaxis": {"gridcolor": _GRID, "color": _TXT_DIM},
        "yaxis": {"gridcolor": _GRID, "color": _TXT_DIM},
        "margin": {"l": 50, "r": 15, "t": 35, "b": 40},
        "height": height,
        "hovermode": "x unified",
        "hoverlabel": {"bgcolor": _BG_PLOT, "bordercolor": _CYAN,
                       "font": {"color": _TXT}},
        "legend": {"bgcolor": "rgba(0,0,0,0)",
                   "font": {"color": _TXT, "size": 9},
                   "orientation": "h", "y": 1.0, "x": 1.0,
                   "xanchor": "right", "yanchor": "bottom"},
    }


def _dt_list(idx):
    try:
        return [pd.Timestamp(x).strftime("%Y-%m-%dT%H:%M:%S") for x in idx]
    except Exception:
        return list(idx)


def _safe(v, d=4):
    try:
        f = float(v)
        if np.isnan(f) or np.isinf(f):
            return None
        return round(f, d)
    except Exception:
        return None


# ════════════════════════════════════════════════════════════
#  Device info (UI에 GPU 가용성 표시)
# ════════════════════════════════════════════════════════════
def generate_ml_signals(ohlcv: pd.DataFrame,
                         model_type: str = "rf",
                         task: str = "classification",
                         horizon: int = 5,
                         seq_len: int = 30,
                         epochs: int = 20,
                         train_pct: float = 0.7,
                         buy_thresh: float = 0.55,
                         sell_thresh: float = 0.45
                         ) -> tuple:
    """ML 학습 + 시그널 추출 (entries, exits Series). run_backtest용 wrapper.

    train_pct 만큼 학습 → 전체 구간 예측 → 임계로 매수/매도 시그널 변환.
    """
    try:
        from engine.ml.models import Trainer
        from engine.ml.features import make_features
    except Exception as e:
        raise RuntimeError(f"ML 의존성 부족: {e}")

    feats = make_features(ohlcv, target_horizon=horizon)
    if len(feats) < 60:
        # 시그널 없음 (모두 False)
        idx = ohlcv["close"].index
        empty = pd.Series(False, index=idx)
        return empty, empty

    n = len(feats)
    split = int(n * float(train_pct))
    train_df = feats.iloc[:split]

    tr = Trainer(model_type=model_type, task=task,
                  seq_len=seq_len, epochs=epochs)
    tr.fit(train_df)
    pred = tr.predict(feats)
    pred_ser = pd.Series(pred, index=feats.index)

    # 시그널 변환
    if task == "classification":
        # 확률이 buy_thresh 상향 돌파 → 매수, sell_thresh 하향 돌파 → 매도
        prev = pred_ser.shift(1).fillna(0.5)
        entries = (pred_ser > buy_thresh) & (prev <= buy_thresh)
        exits = (pred_ser < sell_thresh) & (prev >= sell_thresh)
    else:
        # 회귀: 예측 수익률 양 → 매수, 음 → 매도
        prev = pred_ser.shift(1).fillna(0)
        entries = (pred_ser > 0) & (prev <= 0)
        exits = (pred_ser < 0) & (prev >= 0)

    # ⚠ in-sample bias 제거 — 학습 구간 시그널 마스킹
    if split > 0:
        try:
            split_date = feats.index[split] if split < len(feats) else None
            if split_date is not None:
                entries.loc[entries.index < split_date] = False
                exits.loc[exits.index < split_date] = False
        except Exception:
            pass

    # ohlcv의 close 인덱스에 맞춰 reindex
    close_idx = ohlcv["close"].index
    entries = entries.reindex(close_idx).fillna(False).astype(bool)
    exits = exits.reindex(close_idx).fillna(False).astype(bool)
    # 룩어헤드 fix는 run_backtest에서 처리 (next_bar_exec)
    return entries, exits


def get_device_info() -> Dict[str, Any]:
    info = {"torch_available": False, "device": "cpu",
            "cuda_available": False, "gpu_name": None,
            "gpu_memory_gb": None, "xgb_available": False}
    try:
        import torch
        info["torch_available"] = True
        info["cuda_available"] = bool(torch.cuda.is_available())
        if info["cuda_available"]:
            info["device"] = "cuda"
            info["gpu_name"] = torch.cuda.get_device_name(0)
            try:
                info["gpu_memory_gb"] = round(
                    torch.cuda.get_device_properties(0).total_memory / 1e9, 1)
            except Exception:
                pass
    except Exception:
        pass
    try:
        import xgboost  # noqa
        info["xgb_available"] = True
    except Exception:
        pass
    return info


# ════════════════════════════════════════════════════════════
#  1) Train + Predict (학습 + 미래 예측 + Plotly 차트)
# ════════════════════════════════════════════════════════════
def train_predict_model(ticker: str,
                        model_type: str = "lstm",
                        task: str = "regression",
                        period_days: int = 730,
                        interval: str = "1d",
                        horizon: int = 5,
                        seq_len: int = 30,
                        epochs: int = 20,
                        test_pct: float = 0.2
                        ) -> Dict[str, Any]:
    """학습 → in-sample 예측 + 미래 horizon봉 예측 + Plotly 차트.

    model_type: rf | xgb | lstm | gru | transformer
    task      : classification (다음 horizon봉 +/- 확률) | regression (수익률)
    """
    t0 = time.time()
    try:
        from engine.ml.models import Trainer
        from engine.ml.features import make_features
        from .vbt_runner import _fetch_ohlcv
    except Exception as e:
        return {"ok": False, "error": f"의존성 로드 실패: {e}"}

    try:
        df = _fetch_ohlcv(ticker, period_days=period_days, interval=interval)
        if len(df) < 100:
            return {"ok": False,
                    "error": f"데이터 부족 (n={len(df)} < 100)"}
    except Exception as e:
        return {"ok": False, "error": f"데이터 fetch 실패: {e}"}

    # 피처
    try:
        feats = make_features(df, target_horizon=horizon)
        if len(feats) < 60:
            return {"ok": False,
                    "error": f"피처 부족 (n={len(feats)} < 60)"}
    except Exception as e:
        return {"ok": False, "error": f"피처 생성 실패: {e}"}

    # train/test split (시간순)
    n = len(feats)
    split = int(n * (1 - test_pct))
    train_df = feats.iloc[:split].copy()
    test_df  = feats.iloc[split:].copy()
    if len(train_df) < 30 or len(test_df) < 10:
        return {"ok": False, "error": "train/test 너무 짧음"}

    try:
        tr = Trainer(model_type=model_type, task=task,
                      seq_len=seq_len, epochs=epochs)
    except Exception as e:
        return {"ok": False, "error": f"Trainer init 실패: {e}"}

    # 학습
    t_train = time.time()
    try:
        tr.fit(train_df)
    except Exception as e:
        import traceback
        return {"ok": False,
                "error": f"학습 실패: {type(e).__name__}: {e}",
                "trace": traceback.format_exc()[-400:]}
    train_sec = round(time.time() - t_train, 2)

    # in-sample + test 예측
    try:
        full_pred = tr.predict(feats)  # 전체 구간
    except Exception as e:
        return {"ok": False, "error": f"예측 실패: {e}"}

    # holdout 메트릭
    try:
        metrics = tr.evaluate(test_df)
    except Exception:
        metrics = {}

    # Plotly 차트 — 가격 + 예측 (분류면 확률, 회귀면 수익률)
    dates = _dt_list(feats.index)
    close_vals = [float(v) for v in df["close"].loc[feats.index].values]
    pred_vals = [float(v) if not np.isnan(v) else None for v in full_pred]

    # split 기준 라벨 (학습/예측 영역 시각화용 vline)
    split_x = feats.index[split].strftime("%Y-%m-%d") if split < n else None

    traces = [
        # 가격 (왼쪽 y축)
        {"type": "scatter", "x": dates, "y": close_vals, "mode": "lines",
         "line": {"color": _TXT, "width": 1.1}, "name": "Close",
         "yaxis": "y1",
         "hovertemplate": "%{x|%Y-%m-%d}<br>$%{y:,.2f}<extra></extra>"},
        # 예측 (오른쪽 y축)
        {"type": "scatter", "x": dates, "y": pred_vals, "mode": "lines",
         "line": {"color": _CYAN, "width": 1.3,
                   "dash": "solid"}, "name": f"예측 ({task})",
         "yaxis": "y2",
         "hovertemplate": "%{x|%Y-%m-%d}<br>pred %{y:.4f}<extra></extra>"},
    ]
    # 0 line / 0.5 line
    if task == "classification":
        traces.append({"type": "scatter", "x": [dates[0], dates[-1]],
                        "y": [0.5, 0.5], "mode": "lines",
                        "line": {"color": _AMBER, "width": 0.5, "dash": "dot"},
                        "name": "임계 0.5", "yaxis": "y2",
                        "showlegend": False, "hoverinfo": "skip"})
    layout = _layout(f"{ticker} · {model_type.upper()} ({task}) · "
                      f"horizon={horizon}봉 · 학습 {train_sec}s", height=440)
    layout["yaxis"]  = {"domain": [0, 1.0], "gridcolor": _GRID,
                         "color": _TXT, "title": {"text": "Price"}}
    layout["yaxis2"] = {"overlaying": "y", "side": "right",
                         "color": _CYAN, "showgrid": False,
                         "title": {"text": "예측"}}
    if split_x:
        layout["shapes"] = [{
            "type": "line", "xref": "x", "yref": "paper",
            "x0": split_x, "x1": split_x, "y0": 0, "y1": 1,
            "line": {"color": _AMBER, "width": 1, "dash": "dash"},
        }]
        layout["annotations"] = [{
            "x": split_x, "y": 1.02, "xref": "x", "yref": "paper",
            "showarrow": False, "text": "← 학습 / 검증 →",
            "font": {"color": _AMBER, "size": 10}, "xanchor": "center",
        }]

    # 마지막 예측값 + 방향 해석
    last_pred = None
    for v in reversed(pred_vals):
        if v is not None:
            last_pred = v; break
    direction = "—"
    if last_pred is not None:
        if task == "classification":
            if last_pred > 0.55:   direction = "📈 상승 우위"
            elif last_pred < 0.45: direction = "📉 하락 우위"
            else:                   direction = "➡️ 중립"
        else:
            if last_pred > 0.01:   direction = "📈 상승 예측"
            elif last_pred < -0.01: direction = "📉 하락 예측"
            else:                   direction = "➡️ 횡보 예측"

    return {
        "ok": True,
        "ticker":     ticker,
        "model_type": model_type,
        "task":       task,
        "horizon":    horizon,
        "device":     tr.device,
        "n_train":    int(split),
        "n_test":     int(n - split),
        "train_sec":  train_sec,
        "elapsed_sec": round(time.time() - t0, 2),
        "metrics":    {k: _safe(v) for k, v in (metrics or {}).items()},
        "last_pred":  _safe(last_pred, 6),
        "direction":  direction,
        "plotly":     {"data": traces, "layout": layout},
    }


# ════════════════════════════════════════════════════════════
#  2) ML 시그널 백테스트 — 예측을 매수/매도로 변환
# ════════════════════════════════════════════════════════════
def ml_signal_backtest(ticker: str,
                       model_type: str = "lstm",
                       task: str = "classification",
                       period_days: int = 730,
                       interval: str = "1d",
                       horizon: int = 5,
                       seq_len: int = 30,
                       epochs: int = 20,
                       buy_thresh: float = 0.55,
                       sell_thresh: float = 0.45,
                       fees: float = 0.001,
                       init_cash: float = 10000.0
                       ) -> Dict[str, Any]:
    """ML 모델 예측을 시그널로 변환해 vectorbt 백테스트.

    분류: pred > buy_thresh → 매수, pred < sell_thresh → 매도
    회귀: pred > 0 → 매수, pred < 0 → 매도
    """
    t0 = time.time()
    try:
        from engine.ml.models import Trainer
        from engine.ml.features import make_features
        from .vbt_runner import _fetch_ohlcv, _vbt_freq
        from .pf_stats import extract_full_stats, monthly_returns_heatmap
        import vectorbt as vbt
    except Exception as e:
        return {"ok": False, "error": f"의존성 로드 실패: {e}"}

    try:
        df = _fetch_ohlcv(ticker, period_days=period_days, interval=interval)
        if len(df) < 100:
            return {"ok": False, "error": "데이터 부족"}
        feats = make_features(df, target_horizon=horizon)
        if len(feats) < 60:
            return {"ok": False, "error": "피처 부족"}
    except Exception as e:
        return {"ok": False, "error": f"데이터/피처 실패: {e}"}

    # 학습 (전체 - test 부분만)
    n = len(feats)
    split = int(n * 0.7)   # 70% 학습, 30% 백테스트
    train_df = feats.iloc[:split]
    try:
        tr = Trainer(model_type=model_type, task=task,
                      seq_len=seq_len, epochs=epochs)
        tr.fit(train_df)
        pred = tr.predict(feats)  # 전체 구간 예측
    except Exception as e:
        return {"ok": False,
                "error": f"학습/예측 실패: {type(e).__name__}: {e}"}

    # 시그널 변환
    close = df["close"].loc[feats.index].astype(float)
    pred_ser = pd.Series(pred, index=feats.index)
    if task == "classification":
        entries = (pred_ser > buy_thresh) & (
            pred_ser.shift(1).fillna(0.5) <= buy_thresh)
        exits = (pred_ser < sell_thresh) & (
            pred_ser.shift(1).fillna(0.5) >= sell_thresh)
    else:
        entries = (pred_ser > 0) & (pred_ser.shift(1).fillna(0) <= 0)
        exits = (pred_ser < 0) & (pred_ser.shift(1).fillna(0) >= 0)
    entries = entries.fillna(False).astype(bool)
    exits = exits.fillna(False).astype(bool)

    # ⚠⚠⚠ in-sample bias 제거 — 학습 구간 시그널 마스킹 (가장 중요!)
    # split까지는 모델이 답을 학습한 구간 → in-sample 거래는 가짜
    # OOS (split 이후)만 실거래 시뮬레이션 의미 있음
    # 단, close 인덱스 기준으로 마스킹 (feats보다 길 수 있음)
    if split > 0 and len(entries) > 0:
        try:
            split_date = feats.index[split] if split < len(feats) else None
            if split_date is not None:
                mask_pre = entries.index < split_date
                entries.loc[mask_pre] = False
                exits.loc[mask_pre] = False
        except Exception:
            # fallback: 위치 기반
            n = len(entries)
            cut = min(split, n - 1)
            entries.iloc[:cut] = False
            exits.iloc[:cut] = False

    # ⚠ 룩어헤드 bias 제거 — 시그널 t → 거래 t+1 시가
    # ML 모델이 t의 close + 과거 피처로 예측 → 시그널 발생.
    # 실제 거래는 t+1 봉에서 가능 (t의 close 시점엔 장 마감)
    entries = entries.shift(1).fillna(False).astype(bool)
    exits = exits.shift(1).fillna(False).astype(bool)

    # 백테스트 (split 이후만 실거래로 평가)
    # 단순화: 전체 구간으로 돌리되 split 표시
    try:
        pf = vbt.Portfolio.from_signals(
            close, entries, exits, fees=fees, init_cash=init_cash,
            freq=_vbt_freq(interval))
    except Exception as e:
        return {"ok": False, "error": f"vbt 실패: {e}"}

    total = float(pf.total_return())
    sh = float(pf.sharpe_ratio())
    mdd = float(pf.max_drawdown())
    bh = float(close.iloc[-1] / close.iloc[0] - 1)
    nt = int(pf.trades.count())

    eq = pf.value()
    if len(eq) > 200:
        eq = eq.iloc[::max(1, len(eq)//200)]
    equity = [{"d": d.strftime("%Y-%m-%d"), "v": float(v)}
              for d, v in eq.items()]

    stats_full = extract_full_stats(pf)
    heatmap = monthly_returns_heatmap(
        pf, title=f"{ticker} · ML({model_type}) 월별 수익률 %")

    # 시그널 마커
    ent_dates = [d.strftime("%Y-%m-%d") for d in close.index[entries.values]]
    ex_dates = [d.strftime("%Y-%m-%d") for d in close.index[exits.values]]

    return {
        "ok": True,
        "ticker": ticker,
        "model_type": model_type,
        "task": task,
        "horizon": horizon,
        "buy_thresh": buy_thresh,
        "sell_thresh": sell_thresh,
        "device": tr.device,
        "n_train": int(split),
        "n_total": int(n),
        "elapsed_sec": round(time.time() - t0, 2),
        "metrics": {
            "total_return":    _safe(total),
            "buy_hold_return": _safe(bh),
            "alpha":           _safe(total - bh),
            "sharpe":          _safe(sh, 3),
            "max_drawdown":    _safe(mdd),
            "n_trades":        nt,
        },
        "entry_signals": ent_dates,
        "exit_signals":  ex_dates,
        "equity_curve":  equity,
        "stats_full":    stats_full,
        "monthly_heatmap": heatmap,
    }
