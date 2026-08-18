"""
리서치 모듈 — 시그널 품질, drift 지속성, imbalance 클러스터링
==================================================================
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from typing import Any, Dict


def signal_quality(close: pd.Series,
                   entries: pd.Series,
                   lookahead: int = 5) -> Dict[str, Any]:
    """
    시그널이 발동한 시점 t에서 t+lookahead 봉 후 수익이 양수면 TP.
    precision = TP / (TP + FP)
    recall    = TP / (실제 상승 봉 수 — N봉 후 양수 수익)
    """
    entries = entries.fillna(False)
    fwd = close.shift(-lookahead) / close - 1.0
    pred = entries
    actual = (fwd > 0)
    tp = int((pred & actual).sum())
    fp = int((pred & ~actual).sum())
    fn = int((~pred & actual).sum())
    prec = tp / max(tp + fp, 1)
    rec  = tp / max(tp + fn, 1)
    f1   = 2 * prec * rec / max(prec + rec, 1e-9)
    return {
        "n_signals": int(pred.sum()),
        "lookahead_bars": lookahead,
        "precision": round(prec, 4),
        "recall":    round(rec, 4),
        "f1":        round(f1, 4),
        "tp": tp, "fp": fp, "fn": fn,
        "mean_fwd_return_at_signal": round(
            float(fwd[pred].mean()) if pred.sum() else 0.0, 5),
    }


def drift_persistence(close: pd.Series,
                      events: pd.DataFrame,
                      windows=(5, 10, 20, 30, 60)) -> Dict[str, Any]:
    """
    PEAD drift 지속성 — 이벤트 후 각 window의 평균 수익률.
    SUE 양/음 분리 통계.
    """
    if events is None or events.empty:
        return {}
    rows_pos, rows_neg = [], []
    for _, ev in events.iterrows():
        d0 = pd.Timestamp(ev["event_date"])
        sue = ev.get("sue", 0)
        # ev 직후 종가 기준
        post = close[close.index >= d0]
        if len(post) < max(windows):
            continue
        rec = {}
        for w in windows:
            rec[f"w{w}"] = float(post.iloc[min(w, len(post)-1)] / post.iloc[0] - 1)
        if sue >= 0:
            rows_pos.append(rec)
        else:
            rows_neg.append(rec)
    out = {"n_pos_events": len(rows_pos), "n_neg_events": len(rows_neg)}
    if rows_pos:
        df = pd.DataFrame(rows_pos)
        out["pos_mean"] = {k: round(float(df[k].mean()), 5) for k in df.columns}
    if rows_neg:
        df = pd.DataFrame(rows_neg)
        out["neg_mean"] = {k: round(float(df[k].mean()), 5) for k in df.columns}
    return out


def of_imbalance_clusters(orderflow: pd.DataFrame,
                          n_clusters: int = 4) -> Dict[str, Any]:
    """
    봉별 imbalance를 KMeans로 군집화 — 시장 상태 파악용.
    """
    try:
        from sklearn.cluster import KMeans
    except Exception:
        return {"error": "sklearn 없음"}
    if "delta" not in orderflow.columns:
        return {"error": "delta 컬럼 필요"}
    feat = pd.DataFrame({
        "delta_z":  (orderflow["delta"] - orderflow["delta"].mean()) /
                    (orderflow["delta"].std() + 1e-9),
        "vol_z":    (orderflow.get("ask_vol", orderflow["delta"]).fillna(0)
                     + orderflow.get("bid_vol", orderflow["delta"]).fillna(0)),
    }).fillna(0)
    # vol_z 표준화
    vz = feat["vol_z"]
    feat["vol_z"] = (vz - vz.mean()) / (vz.std() + 1e-9)
    km = KMeans(n_clusters=n_clusters, n_init=10, random_state=42)
    labels = km.fit_predict(feat.values)
    out = {"n_clusters": n_clusters, "counts": {}}
    for i in range(n_clusters):
        mask = labels == i
        out["counts"][f"c{i}"] = int(mask.sum())
        out[f"center_c{i}"] = {
            "delta_z": round(float(km.cluster_centers_[i, 0]), 3),
            "vol_z":   round(float(km.cluster_centers_[i, 1]), 3),
        }
    return out
