# ==============================================================================
# [10/25] ml.py — 트리플배리어 · 모델 경합 · 기권 판정
# ==============================================================================

"""
jiqtx.ml — 방향 예측 모듈. 핵심 기능은 '예측'이 아니라 '기권(abstain)'이다.

원본 리포트의 결정적 실패
-------------------------
RF, in-sample 정확도 100%, 워크포워드 OOS 정확도 50%, 과적합 갭 49%p.
상승확률 신뢰구간이 50%를 포함.
그런데도 prob_up = 0.66 을 출력하고 점수에 반영했다.

OOS 50%는 '약한 신호'가 아니라 '신호 없음'이다.
올바른 출력은 감점된 점수가 아니라 **출력 없음**이다.

파이프라인
----------
1. 트리플 배리어 라벨링 (배리어를 σ_t로 스케일 + 거래비용 내장)
2. 표본 가중: average uniqueness (중첩 라벨 보정)
3. Purged K-Fold + Embargo 로 OOS 확률 생성
4. isotonic 보정 → Brier / Murphy 분해
5. CPCV 로 PBO 산출
6. 게이트: resolution ≈ 0  또는  PBO ≥ 0.5  또는  과적합 갭 > 15%p → ABSTAIN
"""

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd


try:
    from sklearn.ensemble import RandomForestClassifier, HistGradientBoostingClassifier
    from sklearn.linear_model import LogisticRegression
    from sklearn.preprocessing import StandardScaler
    _HAS_SK = True
except Exception:                                     # pragma: no cover
    _HAS_SK = False

# ── 패키지 내부 의존 ──────────────────────────────────────────
from .statcore import (
    calibrate_isotonic,
    deflated_sharpe,
    murphy_decomposition,
    pbo_cscv,
    purged_kfold_indices,
    reliability_table,
    sharpe_ratio,
)



# ---------------------------------------------------------------- 라벨링

@dataclass
class TripleBarrierLabels:
    # +1 / -1. 0 은 사실상 안 나온다 — 수직배리어(시간 만료)에 걸려도
    # 0 으로 두지 않고 **비용 차감 후 수익률의 부호**로 라벨하기 때문이다.
    # (López de Prado 가 제시한 두 변형 중 이진 쪽. 0 은 수익률이 정확히
    #  0 일 때뿐이라 측도 0 이다.)
    #
    # 그래서 이 문제는 실질적으로 **이진 분류**다. 비용을 배리어와 수익률
    # 양쪽에 넣으므로 "비용 빼면 손해였을 움직임" 이 -1 로 간다.
    label: np.ndarray
    touch_idx: np.ndarray        # 배리어 도달 시점
    ret: np.ndarray              # 실현 수익
    uniqueness: np.ndarray       # 평균 유일성 가중치
    pt_mult: float
    sl_mult: float
    horizon: int
    cost: float
    n_labeled: int


def triple_barrier(close: np.ndarray, sigma: np.ndarray, horizon: int = 21,
                   pt_mult: float = 2.0, sl_mult: float = 2.0,
                   cost: float = 0.0) -> TripleBarrierLabels:
    """
    López de Prado 트리플 배리어.
    배리어를 국소 변동성 σ_t 로 스케일하고, 거래비용을 배리어에 내장한다.
    고정 horizon 부호 라벨과 달리 '경로'를 반영한다.
    """
    c = np.asarray(close, float)
    s = np.asarray(sigma, float)
    n = len(c)
    lab = np.zeros(n, dtype=float)
    touch = np.full(n, -1, dtype=int)
    ret = np.full(n, np.nan)

    for t in range(n - 1):
        if not np.isfinite(s[t]) or s[t] <= 0:
            continue
        up = c[t] * math.exp(pt_mult * s[t] + cost)
        dn = c[t] * math.exp(-sl_mult * s[t] - cost)
        end = min(t + horizon, n - 1)
        hit = end
        val = 0.0
        for u in range(t + 1, end + 1):
            if c[u] >= up:
                hit, val = u, 1.0
                break
            if c[u] <= dn:
                hit, val = u, -1.0
                break
        if val == 0.0:
            r = math.log(c[end] / c[t]) - cost
            val = 1.0 if r > 0 else (-1.0 if r < 0 else 0.0)
        lab[t] = val
        touch[t] = hit
        ret[t] = math.log(c[hit] / c[t]) - cost

    # average uniqueness: 라벨 구간 중첩 보정
    conc = np.zeros(n)
    for t in range(n):
        if touch[t] > t:
            conc[t:touch[t] + 1] += 1.0
    uniq = np.ones(n)
    for t in range(n):
        if touch[t] > t:
            seg = conc[t:touch[t] + 1]
            seg = seg[seg > 0]
            uniq[t] = float(np.mean(1.0 / seg)) if len(seg) else 1.0
    return TripleBarrierLabels(lab, touch, ret, uniq, pt_mult, sl_mult,
                               horizon, cost, int(np.sum(lab != 0)))


# ---------------------------------------------------------------- 피처

def build_features(df: pd.DataFrame, macro: Optional[pd.DataFrame] = None,
                   ann: int = 252) -> pd.DataFrame:
    """가격 기반 피처 + (있으면) 매크로 변화. 전부 t 시점에 관측 가능한 것만."""
    c = df["Close"].astype(float)
    h = df["High"].astype(float)
    l = df["Low"].astype(float)
    v = df["Volume"].astype(float).replace(0, np.nan)
    r = np.log(c).diff()

    X = pd.DataFrame(index=df.index)
    for w in (5, 21, 63, 126):
        X[f"ret_{w}"] = np.log(c).diff(w)
        X[f"vol_{w}"] = r.rolling(w).std() * math.sqrt(ann)
    X["vol_ratio"] = X["vol_21"] / X["vol_126"]
    X["mom_accel"] = X["ret_21"] - X["ret_63"] / 3.0
    X["dist_sma50"] = c / c.rolling(50).mean() - 1.0
    X["dist_sma200"] = c / c.rolling(200).mean() - 1.0
    X["sma_slope"] = c.rolling(50).mean().pct_change(20)
    # RSI(14)
    d = c.diff()
    up = d.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
    X["rsi14"] = 100 - 100 / (1 + up / dn.replace(0, np.nan))
    X["hl_range"] = (h - l) / c
    X["gap"] = (df["Open"].astype(float) - c.shift(1)) / c.shift(1)
    X["vol_zscore"] = ((v - v.rolling(63).mean()) / v.rolling(63).std())
    X["skew_63"] = r.rolling(63).skew()
    X["kurt_63"] = r.rolling(63).kurt()
    X["drawdown"] = c / c.cummax() - 1.0
    X["updays_21"] = (r > 0).rolling(21).mean()

    if macro is not None and len(macro):
        for col in macro.columns:
            s = macro[col].reindex(df.index).ffill()
            X[f"m_{col}_d5"] = s.diff(5)
            X[f"m_{col}_d21"] = s.diff(21)
    return X


# ---------------------------------------------------------------- 평가

@dataclass
class MLResult:
    verdict: str                       # "SIGNAL" | "ABSTAIN"
    reasons: List[str]
    prob_up_now: float                 # 보정된 확률 (기권이면 nan)
    prob_ci: Tuple[float, float]
    oos_accuracy: float
    in_sample_accuracy: float
    overfit_gap: float
    brier: float
    brier_skill: float
    resolution: float
    reliability: float
    pbo: float
    pbo_informative: bool
    strategy_sharpe: float
    strategy_dsr: float
    n_paths: int
    n_labeled: int
    base_rate: float
    reliability_tbl: pd.DataFrame
    feature_importance: pd.Series
    model_name: str
    linear_benchmark_acc: float
    beats_linear: bool
    n_trials_used: int


def _abstain(reasons: List[str], n_labeled: int = 0, base: float = np.nan,
             model: str = "") -> "MLResult":
    """게이트 실패 시 표준 기권 결과."""
    return MLResult(
        verdict="ABSTAIN", reasons=reasons, prob_up_now=np.nan,
        prob_ci=(np.nan, np.nan), oos_accuracy=np.nan,
        in_sample_accuracy=np.nan, overfit_gap=np.nan, brier=np.nan,
        brier_skill=np.nan, resolution=np.nan, reliability=np.nan,
        pbo=np.nan, pbo_informative=False, strategy_sharpe=np.nan,
        strategy_dsr=np.nan, n_paths=0, n_labeled=int(n_labeled), base_rate=base,
        reliability_tbl=pd.DataFrame(), feature_importance=pd.Series(dtype=float),
        model_name=model, linear_benchmark_acc=np.nan, beats_linear=False,
        n_trials_used=0)


def _make_model(kind: str, seed: int = 0):
    if kind == "rf":
        return RandomForestClassifier(
            n_estimators=300, max_depth=5, min_samples_leaf=40,
            max_features="sqrt", random_state=seed, n_jobs=-1)
    if kind == "gbm":
        return HistGradientBoostingClassifier(
            max_depth=3, max_iter=200, learning_rate=0.05,
            min_samples_leaf=40, l2_regularization=1.0, random_state=seed)
    return LogisticRegression(max_iter=2000, C=0.5)


def evaluate_direction(X: pd.DataFrame, labels: TripleBarrierLabels,
                       n_splits: int = 6, embargo_frac: float = 0.01,
                       model_kind: str = "gbm", seed: int = 0,
                       gates: Optional[Dict[str, float]] = None,
                       run_pbo: bool = True) -> MLResult:
    """
    Purged CV로 OOS 확률을 만들고, 보정 후 Murphy 분해로 예측력을 판정한다.
    선형 벤치마크를 이기지 못하면 복잡한 모델은 폐기한다.
    """
    # PBO는 '전략 선택'의 과적합을 재는 지표이지 '신호 존재'를 재지 않는다.
    # 따라서 소프트 게이트(불확실성 확대)와 하드 게이트(차단)를 분리한다.
    g = {"pbo_soft": 0.50, "pbo_hard": 0.75, "overfit_gap_max": 0.15,
         "resolution_min": 1e-4, "brier_skill_min": 0.0, "dsr_min": 0.90}
    if gates:
        g.update(gates)

    reasons: List[str] = []
    if not _HAS_SK:
        return _abstain(["scikit-learn 미설치"], model=model_kind)

    y_raw = labels.label
    mask = (y_raw != 0) & np.isfinite(X.values).all(axis=1)
    idx = np.where(mask)[0]
    if len(idx) < 300:
        return _abstain([f"유효 라벨 {len(idx)}개 < 300"], len(idx),
                        model=model_kind)

    Xv = X.values[idx]
    yv = (y_raw[idx] > 0).astype(int)
    wv = labels.uniqueness[idx]
    base = float(yv.mean())

    splits = purged_kfold_indices(len(idx), n_splits, labels.horizon, embargo_frac)
    if len(splits) < 3:
        return _abstain(["유효 Purged CV 분할 부족"], len(idx), base, model_kind)

    # ---- 모델 경합: 단순 모델을 이기지 못하면 복잡한 모델은 폐기한다
    candidates = ["logit", model_kind] if model_kind != "logit" else ["logit"]
    scaler = StandardScaler()
    fitted: Dict[str, Dict] = {}

    for kind in candidates:
        p_oos = np.full(len(idx), np.nan)
        acc_in: List[float] = []
        imps_k: List[np.ndarray] = []
        for tr, te in splits:
            s_ = scaler.fit(Xv[tr])
            Xtr_s, Xte_s = s_.transform(Xv[tr]), s_.transform(Xv[te])
            m = _make_model(kind, seed)
            try:
                m.fit(Xtr_s, yv[tr], sample_weight=wv[tr])
            except TypeError:
                m.fit(Xtr_s, yv[tr])
            p_oos[te] = m.predict_proba(Xte_s)[:, 1]
            acc_in.append(float(np.mean(m.predict(Xtr_s) == yv[tr])))
            if hasattr(m, "feature_importances_"):
                imps_k.append(np.asarray(m.feature_importances_, float))
            elif hasattr(m, "coef_"):
                imps_k.append(np.abs(np.ravel(m.coef_)))
        okk = np.isfinite(p_oos)
        cut_k = int(okk.sum() * 0.6)
        pk, yk = p_oos[okk], yv[okk]
        pcal = np.concatenate([pk[:cut_k],
                               calibrate_isotonic(pk[:cut_k], yk[:cut_k], pk[cut_k:])])
        mp = murphy_decomposition(pcal[cut_k:], yk[cut_k:])
        fitted[kind] = {
            "p_oos": p_oos, "ok": okk, "cut": cut_k, "p_cal": pcal,
            "acc_oos": float(np.mean((pk > 0.5).astype(int) == yk)),
            "acc_in": float(np.mean(acc_in)),
            "murphy": mp, "imps": imps_k,
        }

    # 승자 선택: 보정 후 Brier skill 기준
    winner = max(fitted, key=lambda k: (fitted[k]["murphy"]["skill"]
                                        if np.isfinite(fitted[k]["murphy"]["skill"])
                                        else -9e9))
    W = fitted[winner]
    lin = fitted["logit"]
    oos_p, ok, cut = W["p_oos"], W["ok"], W["cut"]
    p_ok, y_ok = oos_p[ok], yv[ok]
    p_cal = W["p_cal"]
    oos_acc, is_acc = W["acc_oos"], W["acc_in"]
    lin_acc = lin["acc_oos"]
    gap = is_acc - oos_acc
    murphy = W["murphy"]
    rel_tbl = reliability_table(p_cal[cut:], y_ok[cut:])
    imps = W["imps"]
    model_kind = winner
    if winner == "logit" and len(candidates) > 1:
        reasons_note = "복잡한 모델이 선형을 이기지 못해 로지스틱을 채택"
    else:
        reasons_note = ""

    # PBO: 모델/하이퍼파라미터 변형을 전략 집합으로 삼아 산출
    pbo, n_paths = np.nan, 0
    pbo_informative = True
    spread_t = np.nan
    if run_pbo and ok.sum() > 400:
        try:
            # PBO는 '전략 선택 절차'의 과적합을 측정한다.
            # 변형이 서로 사실상 동일하면 순위가 무작위가 되어 PBO≈0.5가
            # 기계적으로 나온다. 따라서 (a) 진짜로 다른 변형을 쓰고
            # (b) 변형 간 성과 산포가 유의한지 먼저 검사한다.
            rng_v = np.random.default_rng(seed)
            nf = Xv.shape[1]
            subsets = [rng_v.choice(nf, size=max(4, nf // 2), replace=False)
                       for _ in range(6)]
            variants = []
            grid = []
            for sub in subsets:
                grid.append(("sub", sub))
            if winner == "logit":
                grid += [("logit", c_) for c_ in (0.05, 0.5, 5.0, 50.0)]
            else:
                grid += [("gbm", (d, 40, 0.05)) for d in (2, 4, 6)]
            for kind_, cfg in grid:
                cols = np.arange(nf)
                if kind_ == "sub":
                    cols = cfg
                    mm = _make_model(winner, seed)
                elif kind_ == "logit":
                    mm = LogisticRegression(max_iter=2000, C=cfg)
                else:
                    depth, leaf, lr = cfg
                    mm = HistGradientBoostingClassifier(
                        max_depth=depth, min_samples_leaf=leaf, learning_rate=lr,
                        max_iter=150, l2_regularization=1.0, random_state=seed)
                if True:
                    pv = np.full(len(idx), np.nan)
                    for tr, te in splits:
                        s_ = scaler.fit(Xv[tr][:, cols])
                        mm.fit(s_.transform(Xv[tr][:, cols]), yv[tr])
                        pv[te] = mm.predict_proba(
                            s_.transform(Xv[te][:, cols]))[:, 1]
                    sg = np.where(pv > 0.5, 1.0, -1.0)
                    variants.append(sg * np.nan_to_num(labels.ret[idx]))
            M = np.column_stack(variants)
            M = M[np.isfinite(M).all(axis=1)]
            # 변형 간 성과 산포 유의성
            mu_v = M.mean(axis=0)
            se_v = M.std(axis=0, ddof=1) / math.sqrt(len(M))
            spread_t = float((mu_v.max() - mu_v.min()) /
                             max(np.median(se_v), 1e-12))
            pbo_informative = bool(spread_t > 2.0)
            res = pbo_cscv(M, n_groups=10)
            pbo = res["pbo"]
            n_paths = int(res["n_combinations"])
        except Exception as e:
            reasons.append(f"PBO 산출 실패: {e}")

    if imps:
        imp = pd.Series(np.mean(imps, axis=0), index=X.columns)
    else:
        # HistGB 등 내장 중요도가 없는 모델: OOS 확률과의 상관으로 대체
        vals = []
        for j in range(Xv.shape[1]):
            col = Xv[ok, j]
            m_ = np.isfinite(col) & np.isfinite(oos_p[ok])
            vals.append(abs(float(np.corrcoef(col[m_], oos_p[ok][m_])[0, 1]))
                        if m_.sum() > 50 else 0.0)
        imp = pd.Series(np.nan_to_num(vals), index=X.columns)
    imp = imp.sort_values(ascending=False)

    # ---- 게이트 판정
    if not np.isfinite(murphy["resolution"]) or murphy["resolution"] < g["resolution_min"]:
        reasons.append(f"Murphy resolution {murphy['resolution']:.5f} ≈ 0 "
                       f"→ 기저율 이상의 정보 없음")
    if np.isfinite(murphy["skill"]) and murphy["skill"] <= g["brier_skill_min"]:
        reasons.append(f"Brier skill {murphy['skill']:+.3f} ≤ 0 "
                       f"→ 상수 예측만도 못함")
    if np.isfinite(gap) and gap > g["overfit_gap_max"]:
        reasons.append(f"과적합 갭 {gap:.1%} > {g['overfit_gap_max']:.0%} "
                       f"(in-sample {is_acc:.1%} vs OOS {oos_acc:.1%})")
    ci_inflate = 0.0
    if np.isfinite(pbo):
        if pbo >= g["pbo_hard"]:
            reasons.append(f"PBO {pbo:.1%} ≥ {g['pbo_hard']:.0%} → 선택 절차가 "
                           f"구조적으로 과적합 (변형 산포 t={spread_t:.1f})")
        elif pbo >= g["pbo_soft"]:
            ci_inflate = 0.10 * (pbo - g["pbo_soft"]) / 0.25
            reasons.append(f"PBO {pbo:.1%} — 변형 선택이 불안정하나 신호 존재를 "
                           f"부정하지는 않음. 확률 신뢰구간을 ±{ci_inflate:.2f} "
                           f"확대 적용 (게이트 실패 아님)")

    # 전략 수익 기반 DSR (실제 시도 횟수를 반영)
    strat_r = np.where(p_cal > 0.5, 1.0, -1.0) * np.nan_to_num(labels.ret[idx][ok])
    strat_sr = sharpe_ratio(strat_r, ann=252 / max(labels.horizon, 1))
    n_trials = len(candidates) * max(len(splits), 1)
    dsr_d = deflated_sharpe(strat_sr, len(strat_r),
                            float(pd.Series(strat_r).skew()),
                            float(pd.Series(strat_r).kurtosis() + 3),
                            n_trials=n_trials,
                            ann=int(252 / max(labels.horizon, 1)))
    strat_dsr = dsr_d["dsr"]
    if np.isfinite(strat_dsr) and strat_dsr < g["dsr_min"]:
        reasons.append(f"전략 DSR {strat_dsr:.1%} < {g['dsr_min']:.0%} "
                       f"(시행 {n_trials}회 보정, 임계샤프 "
                       f"{dsr_d['sr_threshold_ann']:.2f}) → 다중검정 보정 후 유의성 부족")
    beats_linear = bool(winner != "logit")
    if reasons_note:
        reasons.append(reasons_note + " (게이트 실패 아님)")

    hard_fail = [x for x in reasons if "게이트 실패 아님" not in x]
    verdict = "ABSTAIN" if hard_fail else "SIGNAL"

    # 현재 시점 확률 (통과했을 때만)
    p_now, ci = np.nan, (np.nan, np.nan)
    if verdict == "SIGNAL":
        s = scaler.fit(Xv)
        m = _make_model(winner, seed)
        try:
            m.fit(s.transform(Xv), yv, sample_weight=wv)
        except TypeError:
            m.fit(s.transform(Xv), yv)
        last = X.iloc[[-1]].values
        if np.isfinite(last).all():
            raw = float(m.predict_proba(s.transform(last))[0, 1])
            p_now = float(calibrate_isotonic(p_ok[:cut], y_ok[:cut],
                                             np.array([raw]))[0])
            se = math.sqrt(max(p_now * (1 - p_now), 1e-6) / max(ok.sum(), 1))
            half = 1.96 * se + 0.5 * abs(gap) + ci_inflate
            ci = (max(p_now - half, 0.0), min(p_now + half, 1.0))
            if ci[0] <= 0.5 <= ci[1]:
                verdict = "ABSTAIN"
                reasons.append(f"확률 신뢰구간 [{ci[0]:.2f}, {ci[1]:.2f}] 가 "
                               f"0.5를 포함 → 방향 우위 미확인")
                p_now = np.nan

    return MLResult(
        verdict=verdict, reasons=reasons, prob_up_now=p_now, prob_ci=ci,
        oos_accuracy=oos_acc, in_sample_accuracy=is_acc, overfit_gap=gap,
        brier=murphy["brier"], brier_skill=murphy["skill"],
        resolution=murphy["resolution"], reliability=murphy["reliability"],
        pbo=pbo, pbo_informative=bool(pbo_informative),
        strategy_sharpe=strat_sr, strategy_dsr=strat_dsr,
        n_paths=n_paths, n_labeled=int(len(idx)), base_rate=base,
        reliability_tbl=rel_tbl, feature_importance=imp,
        model_name=model_kind, linear_benchmark_acc=lin_acc,
        beats_linear=beats_linear, n_trials_used=n_trials,
    )
