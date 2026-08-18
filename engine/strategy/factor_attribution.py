"""
factor_attribution.py — Fama-French 팩터 귀속 (Tier 2 #5)
============================================================
전략 알파를 시장β/SMB/HML/MOM/QMJ 등 팩터로 분해.

기관 트레이더의 핵심 질문:
  "이 전략의 Sharpe 1.5 중 진짜 알파는 얼마인가?"
  → 시장 베타 0.8, 모멘텀 노출 0.3 → 실제 알파는 작을 수 있음

수식:
  R_strategy - R_f = α + β_mkt × MKT + β_smb × SMB + β_hml × HML + ε
  - α는 OLS 회귀 절편 = factor에 설명되지 않는 진짜 알파
  - t-stat으로 α의 통계적 유의성 검증
"""
from __future__ import annotations

import math
import numpy as np
import pandas as pd
from typing import Any, Dict, List, Optional


_BG_PAPER = "#05070a"; _BG_PLOT = "#0a0f16"
_TXT = "#cfe6ec";      _TXT_DIM = "#5d7480"
_GRID = "#16202e";     _CYAN = "#3df0ff"
_UP = "#ff5a52";       _DOWN = "#4d9cff";  _AMBER = "#ffb44c"


def _layout(title: str, height: int = 360):
    return {
        "title": {"text": title, "font": {"color": _CYAN, "size": 12},
                  "x": 0.02, "xanchor": "left"},
        "paper_bgcolor": _BG_PAPER, "plot_bgcolor": _BG_PLOT,
        "font": {"color": _TXT, "size": 10,
                 "family": "JetBrains Mono, monospace"},
        "xaxis": {"gridcolor": _GRID, "color": _TXT_DIM},
        "yaxis": {"gridcolor": _GRID, "color": _TXT_DIM},
        "margin": {"l": 60, "r": 15, "t": 35, "b": 40},
        "height": height,
    }


def _safe(v, d=4):
    try:
        f = float(v)
        if np.isnan(f) or np.isinf(f):
            return None
        return round(f, d)
    except Exception:
        return None


def factor_attribute_strategy(ticker: str, strategy: str,
                              params: Optional[Dict[str, Any]] = None,
                              period_days: int = 730,
                              interval: str = "1d",
                              factor_model: str = "3F"
                              ) -> Dict[str, Any]:
    """전략 백테스트 → 일별 수익률 → FF 팩터 OLS 회귀 → 알파/베타/t-stat.

    factor_model: '3F' (MKT/SMB/HML) | '5F' (+RMW/CMA)
    """
    try:
        from .vbt_runner import run_backtest
        from ..factor.fama_french import synthetic_ff_factors
    except Exception as e:
        return {"ok": False, "error": f"의존성 부족: {e}"}

    # 1) 전략 백테스트 → equity → daily returns
    r = run_backtest(ticker, strategy, params=params,
                      period_days=period_days, interval=interval)
    if not r.get("ok"):
        return {"ok": False, "error": r.get("error", "백테스트 실패")}
    eq = r.get("equity_curve") or []
    if len(eq) < 30:
        return {"ok": False, "error": "equity 부족 (n<30) — 회귀 불가"}
    idx = pd.DatetimeIndex([pd.Timestamp(p["d"]) for p in eq])
    vals = np.array([float(p["v"]) for p in eq])
    eq_ser = pd.Series(vals, index=idx)
    strat_ret = eq_ser.pct_change().dropna()
    # 2) FF 팩터 생성 (실제 데이터 없으면 합성)
    ff = synthetic_ff_factors(strat_ret.index, model=factor_model)
    # excess return = R - R_f
    excess = strat_ret - ff["RF"]
    # 정렬
    df = pd.concat([excess.rename("excess"), ff.drop(columns=["RF"])],
                    axis=1).dropna()
    if len(df) < 20:
        return {"ok": False, "error": "정렬 후 데이터 부족"}

    # 3) OLS 회귀 (statsmodels)
    try:
        import statsmodels.api as sm
        X = sm.add_constant(df.drop(columns=["excess"]))
        y = df["excess"].values
        model = sm.OLS(y, X).fit()
        # 결과 추출
        coeffs = model.params           # 0번째 = α (절편)
        tvals = model.tvalues
        pvals = model.pvalues
        r_squared = float(model.rsquared)
        # 알파 (daily) → annualized
        alpha_daily = float(coeffs.iloc[0])
        annual_factor = {"1d": 252, "1h": 252*6.5, "5m": 252*78}.get(interval, 252)
        alpha_annual = alpha_daily * annual_factor
        # 통계 유의성: |t| > 2 면 95% 유의
        alpha_tstat = float(tvals.iloc[0])
        alpha_pval = float(pvals.iloc[0])
        alpha_significant = abs(alpha_tstat) > 2.0
        # 팩터 베타
        factor_names = [c for c in df.columns if c != "excess"]
        betas = []
        for fn in factor_names:
            betas.append({
                "factor": fn,
                "beta":   _safe(coeffs[fn], 4),
                "tstat":  _safe(tvals[fn], 3),
                "pval":   _safe(pvals[fn], 4),
                "significant": bool(abs(tvals[fn]) > 2.0),
            })
        # 4) 알파 vs 팩터 기여도 분해 (annualized %)
        total_excess_annual = float(df["excess"].mean()) * annual_factor
        factor_contribution = {}
        for fn in factor_names:
            f_mean = float(df[fn].mean()) * annual_factor
            factor_contribution[fn] = _safe(
                float(coeffs[fn]) * f_mean / max(abs(total_excess_annual), 1e-9) * 100, 1)

        # 5) Plotly 베타 막대 + 알파 강조
        bar_x = ["α (알파)"] + factor_names
        bar_y = [_safe(alpha_annual * 100, 2)] + [
            _safe(float(coeffs[fn]) * 100, 2) for fn in factor_names]
        bar_t = [_safe(alpha_tstat, 2)] + [_safe(tvals[fn], 2) for fn in factor_names]
        colors = [
            (_UP if alpha_significant and alpha_annual > 0
             else (_DOWN if alpha_significant and alpha_annual < 0 else _TXT_DIM))
        ] + [
            (_CYAN if abs(tvals[fn]) > 2 else _TXT_DIM)
            for fn in factor_names
        ]
        plotly_betas = {
            "data": [{
                "type": "bar", "x": bar_x, "y": bar_y,
                "text": [f"{v:+.2f}%<br>t={t}" if v is not None else "—"
                          for v, t in zip(bar_y, bar_t)],
                "textposition": "outside",
                "marker": {"color": colors,
                            "line": {"color": _GRID, "width": 0.5}},
                "hovertemplate": "%{x}<br>coef %{y:.4f}<extra></extra>",
            }],
            "layout": {
                **_layout(f"{ticker} · {strategy} · FF{factor_model} 회귀 "
                          f"(R²={r_squared:.3f})", height=360),
                "yaxis": {"gridcolor": _GRID, "color": _TXT_DIM,
                           "title": {"text": "계수 (%)",
                                     "font": {"color": _TXT}}},
            },
        }

        return {
            "ok": True,
            "ticker": ticker, "strategy": strategy,
            "factor_model": factor_model,
            "n_obs": int(len(df)),
            "r_squared": _safe(r_squared, 4),
            "alpha": {
                "daily":       _safe(alpha_daily, 6),
                "annualized":  _safe(alpha_annual, 4),
                "annualized_pct": _safe(alpha_annual * 100, 2),
                "tstat":       _safe(alpha_tstat, 3),
                "pvalue":      _safe(alpha_pval, 4),
                "significant_95": alpha_significant,
                "interpretation": _interpret_alpha(
                    alpha_annual, alpha_significant),
            },
            "betas": betas,
            "factor_contribution_pct": factor_contribution,
            "raw_metrics": r.get("metrics"),   # 원래 전략 메트릭
            "plotly_betas": plotly_betas,
        }
    except Exception as e:
        import traceback
        return {"ok": False,
                "error": f"OLS 실패: {type(e).__name__}: {e}",
                "trace": traceback.format_exc()[-300:]}


def _interpret_alpha(alpha_annual: float, significant: bool) -> str:
    """알파 결과를 사람말로 해석."""
    pct = alpha_annual * 100
    if not significant:
        if abs(pct) < 2:
            return f"통계적 유의 없음 (α={pct:+.2f}%, |t|<2). 시장/팩터 노출로 모두 설명됨"
        else:
            return f"α={pct:+.2f}%이지만 통계 유의 안 됨 — 우연일 가능성"
    if pct > 10:
        return f"⚡ 매우 강한 알파 (+{pct:.2f}% 연환산, t>2). 진짜 alpha 가능성 높음"
    elif pct > 3:
        return f"✓ 유의한 양의 알파 (+{pct:.2f}% 연환산, t>2)"
    elif pct < -10:
        return f"⚠ 매우 강한 음의 알파 ({pct:.2f}% 연환산) — 전략이 시장보다 나쁨"
    elif pct < -3:
        return f"⚠ 유의한 음의 알파 ({pct:.2f}% 연환산) — 비추천"
    else:
        return f"미미한 알파 ({pct:+.2f}% 연환산)"
