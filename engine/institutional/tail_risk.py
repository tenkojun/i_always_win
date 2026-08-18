"""
Tail Risk 종합 분석 (Barra/Axioma 기관급)
==========================================
4종 MC 앙상블 + CSCV/PBO + 극단값 이론(EVT) 기반 테일 리스크를
하나의 dict로 묶어 반환한다.

출력
----
ensemble_var     : 앙상블 5% VaR
ensemble_cvar    : 앙상블 CVaR
per_method       : 방법별 VaR/CVaR 비교
cscv             : CSCV/PBO 결과
evt              : 극단값 이론 (GPD 꼬리) 추정
tail_narrative   : 한글 해석
"""
from __future__ import annotations
from typing import Dict, Optional
import numpy as np
import pandas as pd

from engine.risk.montecarlo import monte_carlo_ensemble, monte_carlo
from engine.institutional.cscv import run_cscv_from_returns


# ── 극단값 이론 (GPD 피팅) ─────────────────────────────────────────────────
def _evt_gpd(losses: np.ndarray, threshold_pct: float = 0.90) -> Dict:
    """
    손실(양수) 배열에 GPD(Generalized Pareto Distribution) 피팅.
    scipy 없으면 단순 분위수 폴백.
    threshold_pct: 해당 분위 이상만 GPD 피팅 (극단값만 선택)
    """
    try:
        from scipy.stats import genpareto  # type: ignore
        u = float(np.quantile(losses, threshold_pct))
        exceedances = losses[losses > u] - u
        if len(exceedances) < 20:
            raise ValueError("exceedances too few")
        xi, loc, scale = genpareto.fit(exceedances, floc=0)
        # GPD 기반 VaR/CVaR 추정
        n = len(losses)
        n_u = len(exceedances)
        alpha = 0.01  # 1% VaR
        var_99 = u + (scale / max(xi, 1e-10)) * (
            ((n / n_u) * alpha) ** (-xi) - 1)
        cvar_99 = (var_99 + scale - xi * u) / (1 - xi) if xi < 1 else var_99 * 2
        return {
            "method": "gpd",
            "threshold": round(float(u), 6),
            "xi": round(float(xi), 4),
            "scale": round(float(scale), 6),
            "var_99": round(float(var_99), 4),
            "cvar_99": round(float(cvar_99), 4),
            "n_exceedances": int(n_u),
        }
    except Exception:
        # 폴백: 히스토리컬 분위수
        var_99 = float(-np.quantile(-losses, 0.01))
        tail = losses[losses > var_99]
        cvar_99 = float(tail.mean()) if len(tail) else var_99
        return {
            "method": "historical_fallback",
            "var_99": round(var_99, 4),
            "cvar_99": round(cvar_99, 4),
        }


# ── 한글 테일 리스크 내러티브 ─────────────────────────────────────────────
def _tail_narrative(res: Dict) -> str:
    lines = []

    # VaR/CVaR
    var5  = res.get("ensemble_var", 0)
    cvar5 = res.get("ensemble_cvar", 0)
    lines.append(
        f"앙상블 VaR(5%) {var5:.1%} — 최악 5% 시나리오에서 이 이상 손실 가능."
    )
    lines.append(
        f"CVaR(5%) {cvar5:.1%} — 극단 손실 시나리오의 평균 손실 규모."
    )

    # 방법별 비교
    pm = res.get("per_method", {})
    if len(pm) > 1:
        max_m  = max(pm, key=lambda k: pm[k].get("var", 0))
        max_v  = pm[max_m].get("var", 0)
        lines.append(
            f"방법별 VaR 편차: 최대 {max_m} ({max_v:.1%}) — "
            "모델 불확실성(Model Risk)이 크면 보수적 수치 채택 권장."
        )

    # PBO
    cscv = res.get("cscv", {})
    pbo  = cscv.get("pbo")
    if pbo is not None:
        if pbo < 0.25:
            lines.append(f"PBO {pbo:.1%} — 전략 과적합 위험 낮음.")
        elif pbo < 0.50:
            lines.append(f"PBO {pbo:.1%} — 과적합 위험 보통. IS 성과를 그대로 신뢰하지 말 것.")
        else:
            lines.append(f"⚠ PBO {pbo:.1%} — 과적합 높음. 미래 수익률 추정 신뢰도 하락.")

    # EVT
    evt = res.get("evt", {})
    xi  = evt.get("xi")
    if xi is not None:
        tail_type = "두꺼운 꼬리 (Heavy Tail)" if xi > 0.2 else \
                    "얇은 꼬리 (Light Tail)" if xi < 0 else "중간 꼬리"
        lines.append(
            f"GPD 형상모수 ξ={xi:.3f} — {tail_type}. "
            + ("극단 손실이 정규분포보다 훨씬 클 수 있음." if xi > 0.2 else
               "극단 손실이 정규분포 가정과 유사.")
        )

    return " | ".join(lines)


# ── 메인 공개 함수 ─────────────────────────────────────────────────────────
def compute_tail_risk(returns: pd.Series,
                      horizon: int = 252,
                      n_sim: int = 10_000,
                      regimes: Optional[np.ndarray] = None,
                      run_cscv: bool = True,
                      seed: int = 42) -> Dict:
    """
    Tail Risk 종합 분석.

    Parameters
    ----------
    returns   : 일별 수익률 시리즈
    horizon   : MC 예측 기간 (영업일)
    n_sim     : MC 시뮬레이션 횟수
    regimes   : 국면 레이블 배열 (선택)
    run_cscv  : CSCV/PBO 실행 여부 (느림, 기본 True)
    """
    r = returns.dropna()
    if len(r) < 30:
        return {"error": "수익률 데이터 부족 (30일 미만)"}

    # 1. 앙상블 MC
    ens = monte_carlo_ensemble(r, n_sim=n_sim, horizon=horizon,
                               seed=seed, regimes=regimes)

    # 2. EVT (손실 = 음의 수익률)
    losses = -r.values
    losses = losses[losses > 0]
    evt = _evt_gpd(losses) if len(losses) > 40 else {}

    # 3. CSCV/PBO
    cscv_res = {}
    if run_cscv:
        cscv_res = run_cscv_from_returns(r, n_groups=min(16, len(r) // 20))

    result = {
        "ensemble_var":    round(ens.get("var", 0), 4),
        "ensemble_cvar":   round(ens.get("cvar", 0), 4),
        "per_method":      ens.get("per_method", {}),
        "method_used":     ens.get("method_used", ""),
        "n_sim":           ens.get("n_sim", n_sim),
        "simulated_equity": ens.get("simulated_equity"),
        "terminal_pnl":    ens.get("terminal_pnl"),
        "ci_low":          ens.get("ci_low"),
        "ci_high":         ens.get("ci_high"),
        "median_path":     ens.get("median_path"),
        "maxdd_distribution": ens.get("maxdd_distribution"),
        "evt":             evt,
        "cscv":            cscv_res,
    }
    result["tail_narrative"] = _tail_narrative(result)
    return result
