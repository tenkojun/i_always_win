"""
단/중/장 종목 분석기
====================
티커별 OHLCV 데이터를 받아 **단기 / 중기 / 장기** 세 가지 시간축에서
- 추세 / 모멘텀
- 변동성
- 리스크 (Sharpe, Sortino, MDD, VaR, CVaR)
- 오더플로우 시그널
- 머신러닝 단기 예측
- 시장 국면(Regime)
- 합성 점수 & BUY/HOLD/SELL 추천
를 각각 산출한다.

기본 기간 정의 (영업일 기준)
-----------------------------
- 단기 (Short-Term)  : 최근 60일   (≈ 3개월)
- 중기 (Mid-Term)    : 최근 252일  (≈ 1년)
- 장기 (Long-Term)   : 최근 1260일 (≈ 5년) — 데이터가 부족하면 전체

각 기간별 산출 항목 설명
------------------------
- trend.sma_slope         : 종가의 이동평균 기울기 (%)
- trend.above_sma         : 종가가 SMA 위에 있는 비율 (0~1)
- trend.ma_cross          : 단기 MA - 장기 MA (양수=골든크로스)
- momentum.return         : 누적 수익률 (구간 전체)
- momentum.rsi_last       : 마지막 RSI(14)
- momentum.roc            : Rate of Change
- volatility.realized     : 연 환산 실현 변동성
- volatility.regime       : 'low'/'normal'/'high'
- risk.sharpe / sortino / calmar / mdd / var / cvar
- ml.prob_up              : 다음 N일 가격 상승 확률 (RF)
- regime.dominant         : 가장 비중 큰 클러스터 라벨
- score                   : 0~100 종합 점수
- signal                  : BUY / HOLD / SELL
"""
from __future__ import annotations
from typing import Any, Dict, Optional
import warnings
warnings.filterwarnings("ignore")

import numpy as np
import pandas as pd

from ..risk.sharpe import (
    sharpe_ratio, sortino_ratio, calmar_ratio, cagr, volatility,
    ulcer_index,
)
from ..risk.drawdown import max_drawdown, drawdown_duration
from ..risk.var_cvar import all_var_metrics
from ..risk.montecarlo import monte_carlo

from ..orderflow.cvd import cvd
from ..orderflow.vpin import vpin
from ..orderflow.microstructure import volume_imbalance

from ..ml.features import make_features
from ..ml.models import Trainer
from ..ml.regime import RegimeDetector

from ..volatility.garch import realized_vol


# ------------------------------------------------------------------ #
#  기본 타임프레임 정의
# ------------------------------------------------------------------ #
DEFAULT_TIMEFRAMES = {
    "단기": {"lookback": 60,   "ma_fast": 5,  "ma_slow": 20,  "ml_horizon": 5},
    "중기": {"lookback": 252,  "ma_fast": 20, "ma_slow": 60,  "ml_horizon": 20},
    "장기": {"lookback": 1260, "ma_fast": 50, "ma_slow": 200, "ml_horizon": 60},
}


# ------------------------------------------------------------------ #
#  유틸리티
# ------------------------------------------------------------------ #
def _safe(fn, *a, default=None, **kw):
    """예외 발생 시 default 반환."""
    try:
        return fn(*a, **kw)
    except Exception:
        return default


def _rsi(close: pd.Series, n: int = 14) -> pd.Series:
    """RSI(14) 표준 구현."""
    diff = close.diff()
    up = diff.clip(lower=0).rolling(n).mean()
    dn = (-diff.clip(upper=0)).rolling(n).mean()
    rs = up / dn.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


# ------------------------------------------------------------------ #
#  개별 분석 블록
# ------------------------------------------------------------------ #
def _trend_block(df: pd.DataFrame, ma_fast: int, ma_slow: int) -> Dict[str, float]:
    """추세 강도 측정."""
    close = df["close"]
    sma_f = close.rolling(ma_fast).mean()
    sma_s = close.rolling(ma_slow).mean()

    # 기울기 : 마지막 N봉간 SMA 변화율 (연 환산 %)
    sma_slope = (sma_s.iloc[-1] / sma_s.iloc[-min(ma_slow, len(sma_s))] - 1) * 100
    above_sma = float((close > sma_s).mean())
    ma_cross = float(sma_f.iloc[-1] - sma_s.iloc[-1])
    trend_direction = "상승" if ma_cross > 0 else "하락"

    return {
        "sma_slope_pct":   float(sma_slope),
        "above_sma_ratio": above_sma,
        "ma_cross_diff":   ma_cross,
        "trend_direction": trend_direction,
    }


def _momentum_block(df: pd.DataFrame) -> Dict[str, float]:
    """모멘텀 지표."""
    close = df["close"]
    n = len(close)
    cum_ret = float(close.iloc[-1] / close.iloc[0] - 1) * 100

    rsi = _rsi(close, 14)
    rsi_last = float(rsi.iloc[-1]) if not np.isnan(rsi.iloc[-1]) else 50.0

    # ROC : 최근 / 기준 가격 비율
    roc = float(close.iloc[-1] / close.iloc[max(0, n - 20)] - 1) * 100

    if rsi_last > 70:
        rsi_state = "과매수"
    elif rsi_last < 30:
        rsi_state = "과매도"
    else:
        rsi_state = "중립"

    return {
        "cum_return_pct": cum_ret,
        "rsi_last":       rsi_last,
        "rsi_state":      rsi_state,
        "roc_20_pct":     roc,
    }


def _volatility_block(returns: pd.Series) -> Dict[str, Any]:
    """변동성 분석."""
    ann_vol = volatility(returns)
    rv20 = realized_vol(returns, window=min(20, len(returns) - 1))
    rv_last = float(rv20.dropna().iloc[-1]) if len(rv20.dropna()) else ann_vol

    if ann_vol < 0.15:
        regime = "낮음"
    elif ann_vol < 0.30:
        regime = "보통"
    else:
        regime = "높음"

    return {
        "annual_vol":    float(ann_vol),
        "realized_20d":  rv_last,
        "vol_regime":    regime,
    }


def _risk_block(equity_like: pd.Series, returns: pd.Series) -> Dict[str, float]:
    """리스크 메트릭 묶음."""
    out = {
        "sharpe":          _safe(sharpe_ratio, returns, default=0.0),
        "sortino":         _safe(sortino_ratio, returns, default=0.0),
        "calmar":          _safe(calmar_ratio, equity_like, default=0.0),
        "cagr":            _safe(cagr, equity_like, default=0.0),
        "max_drawdown":    _safe(max_drawdown, equity_like, default=0.0),
        "dd_duration":     _safe(drawdown_duration, equity_like, default=0),
        "ulcer_index":     _safe(ulcer_index, equity_like, default=0.0),
    }
    out.update(_safe(all_var_metrics, returns, default={}) or {})
    return out


def _orderflow_block(df: pd.DataFrame) -> Dict[str, float]:
    """오더플로우 / 미시구조."""
    cvd_s  = _safe(cvd, df, default=pd.Series(dtype=float))
    vpin_s = _safe(vpin, df, default=pd.Series(dtype=float))
    vimb_s = _safe(volume_imbalance, df, default=pd.Series(dtype=float))

    cvd_trend = 0.0
    if len(cvd_s) > 20:
        cvd_trend = float(cvd_s.iloc[-1] - cvd_s.iloc[-20])

    return {
        "cvd_last":         float(cvd_s.iloc[-1]) if len(cvd_s) else 0.0,
        "cvd_20d_change":   cvd_trend,
        "vpin_mean":        float(vpin_s.mean()) if len(vpin_s) else 0.0,
        "vol_imbalance":    float(vimb_s.iloc[-1]) if len(vimb_s) else 0.0,
    }


def _ml_block(df: pd.DataFrame, horizon: int,
              model_type: str = "rf") -> Dict[str, Any]:
    """짧은 모델 학습 + 다음 N일 상승 확률 예측."""
    try:
        feats = make_features(df, target_horizon=horizon)
        if len(feats) < 100:
            return {"prob_up": None, "note": "데이터 부족"}
        split = int(len(feats) * 0.8)
        trainer = Trainer(model_type=model_type, task="classification",
                          epochs=8, seq_len=20)
        trainer.fit(feats.iloc[:split])
        metrics = trainer.evaluate(feats.iloc[split:])
        pred = trainer.predict(feats.iloc[split:])
        pred_clean = pred[~np.isnan(pred)] if hasattr(pred, "__iter__") else pred
        prob_up = float(pred_clean[-1]) if len(pred_clean) else None
        return {
            "model":       model_type,
            "horizon_d":   horizon,
            "accuracy":    metrics.get("accuracy"),
            "prob_up":     prob_up,
            "n_test":      metrics.get("n"),
        }
    except Exception as e:
        return {"prob_up": None, "note": f"실패: {e}"}


def _regime_block(returns: pd.Series, method: str = "kmeans") -> Dict[str, Any]:
    """시장 국면(클러스터) 분석."""
    if len(returns) < 60:
        return {"dominant": None, "note": "데이터 부족"}
    try:
        labels = RegimeDetector(method, n_states=3).fit_predict(returns)
        counts = labels.value_counts().to_dict()
        dom = int(labels.iloc[-1]) if len(labels) else None
        # 각 국면별 평균 수익률
        df = pd.concat([returns.rename("r"), labels], axis=1).dropna()
        stats = df.groupby("regime")["r"].agg(["mean", "std", "count"]).to_dict()
        return {
            "method":       method,
            "current":      dom,
            "n_states":     int(labels.nunique()),
            "distribution": counts,
            "mean_by_regime": stats.get("mean", {}),
        }
    except Exception as e:
        return {"dominant": None, "note": f"실패: {e}"}


# ------------------------------------------------------------------ #
#  점수 / 시그널 산출
# ------------------------------------------------------------------ #
def _compute_score(trend, momentum, vol_block, risk, ml, orderflow) -> Dict[str, Any]:
    """
    종합 점수 (0~100) + BUY/HOLD/SELL 시그널을 만든다.

    가중치
    ------
    추세 25 | 모멘텀 20 | 리스크-수익 25 | ML 확률 15 | 오더플로우 10 | 변동성 페널티 5
    """
    score = 50.0  # 중립 시작
    reasons = []

    # 1. 추세 (25)
    if trend["ma_cross_diff"] > 0:
        score += 12; reasons.append("골든크로스(단기MA>장기MA)")
    else:
        score -= 12; reasons.append("데드크로스(단기MA<장기MA)")
    if trend["sma_slope_pct"] > 0:
        score += 6
    else:
        score -= 6
    if trend["above_sma_ratio"] > 0.55:
        score += 7
    elif trend["above_sma_ratio"] < 0.45:
        score -= 7

    # 2. 모멘텀 (20)
    if 30 < momentum["rsi_last"] < 70:
        score += 4
    elif momentum["rsi_last"] >= 70:
        score -= 8; reasons.append("RSI 과매수")
    else:
        score += 8; reasons.append("RSI 과매도 → 반등 가능")
    if momentum["cum_return_pct"] > 0:
        score += 8
    else:
        score -= 8

    # 3. 리스크-수익 (25)
    if risk.get("sharpe", 0) > 1:
        score += 15; reasons.append(f"샤프 {risk['sharpe']:.2f} (우수)")
    elif risk.get("sharpe", 0) > 0:
        score += 7
    else:
        score -= 10; reasons.append(f"샤프 {risk.get('sharpe', 0):.2f} (저조)")
    if abs(risk.get("max_drawdown", 0)) > 0.3:
        score -= 10; reasons.append(f"MDD {risk['max_drawdown']*100:.1f}% 큼")

    # 4. ML 확률 (15)
    p = ml.get("prob_up")
    if p is not None:
        if p > 0.6:
            score += 12; reasons.append(f"ML 상승확률 {p*100:.1f}%")
        elif p > 0.5:
            score += 5
        elif p < 0.4:
            score -= 12; reasons.append(f"ML 상승확률 {p*100:.1f}% (낮음)")
        else:
            score -= 5

    # 5. 오더플로우 (10)
    if orderflow["cvd_20d_change"] > 0:
        score += 5; reasons.append("CVD 상승 (매수 우위)")
    else:
        score -= 5; reasons.append("CVD 하락 (매도 우위)")
    if orderflow["vol_imbalance"] > 0.1:
        score += 3
    elif orderflow["vol_imbalance"] < -0.1:
        score -= 3

    # 6. 변동성 페널티 (5)
    if vol_block["vol_regime"] == "높음":
        score -= 5; reasons.append("변동성 높음 - 주의")

    score = float(round(max(0, min(100, score)), 1))
    if score >= 65:
        signal, color = "BUY",  "🟢"
    elif score >= 45:
        signal, color = "HOLD", "🟡"
    else:
        signal, color = "SELL", "🔴"

    return {"score": score, "signal": signal, "color": color,
            "reasons": reasons[:6]}


# ------------------------------------------------------------------ #
#  단일 타임프레임 분석
# ------------------------------------------------------------------ #
def analyze_one_timeframe(df_full: pd.DataFrame,
                          name: str,
                          config: Dict,
                          ml_model: str = "rf",
                          regime_method: str = "kmeans") -> Dict[str, Any]:
    """
    하나의 시간축(단기 또는 중기 또는 장기)에 대해 전 분석을 수행한다.

    Parameters
    ----------
    df_full       : 전체 OHLCV (잘라내기 전)
    name          : '단기' / '중기' / '장기'
    config        : DEFAULT_TIMEFRAMES 의 한 항목
    ml_model      : 'rf' | 'xgb' | 'lstm' | 'gru' | 'transformer'
    regime_method : 'kmeans' | 'hmm' | 'gmm'
    """
    lookback = min(config["lookback"], len(df_full))
    df = df_full.iloc[-lookback:].copy()
    close = df["close"]
    returns = close.pct_change().fillna(0.0)
    equity_like = (1 + returns).cumprod() * 100  # 정규화된 누적가

    trend     = _trend_block(df, config["ma_fast"], config["ma_slow"])
    momentum  = _momentum_block(df)
    vol_block = _volatility_block(returns)
    risk      = _risk_block(equity_like, returns)
    orderflow = _orderflow_block(df)
    ml        = _ml_block(df, config["ml_horizon"], ml_model)
    regime    = _regime_block(returns, regime_method)
    summary   = _compute_score(trend, momentum, vol_block, risk, ml, orderflow)

    return {
        "name":           name,
        "lookback_days":  lookback,
        "period_start":   str(df.index[0].date()),
        "period_end":     str(df.index[-1].date()),
        "first_price":    float(close.iloc[0]),
        "last_price":     float(close.iloc[-1]),
        "trend":          trend,
        "momentum":       momentum,
        "volatility":     vol_block,
        "risk":           risk,
        "orderflow":      orderflow,
        "ml":             ml,
        "regime":         regime,
        **summary,
    }


# ------------------------------------------------------------------ #
#  단/중/장 통합 분석
# ------------------------------------------------------------------ #
def analyze_ticker(df: pd.DataFrame,
                   ticker: str = "TICKER",
                   timeframes: Optional[Dict] = None,
                   ml_model: str = "rf",
                   regime_method: str = "kmeans") -> Dict[str, Any]:
    """
    종목 하나에 대해 단/중/장 세 가지 시간축 분석을 모두 실행한다.

    Returns
    -------
    dict
        {
          'ticker': str,
          'timeframes': { '단기': {...}, '중기': {...}, '장기': {...} },
          'overall_signal': str,         # 3개 점수를 가중 평균한 종합 시그널
          'overall_score':  float,
        }
    """
    timeframes = timeframes or DEFAULT_TIMEFRAMES
    out = {"ticker": ticker, "timeframes": {}}

    for name, cfg in timeframes.items():
        if len(df) < cfg["ma_slow"] * 2:
            out["timeframes"][name] = {
                "name": name,
                "note": f"데이터 부족 ({len(df)}일 < 필요 {cfg['ma_slow']*2}일)",
                "score": 50.0, "signal": "HOLD",
            }
            continue
        out["timeframes"][name] = analyze_one_timeframe(
            df, name, cfg, ml_model=ml_model, regime_method=regime_method,
        )

    # 가중평균: 단기 30 % / 중기 40 % / 장기 30 %
    weights = {"단기": 0.3, "중기": 0.4, "장기": 0.3}
    weighted = sum(
        out["timeframes"][k].get("score", 50.0) * w
        for k, w in weights.items()
    )
    out["overall_score"] = float(round(weighted, 1))
    if weighted >= 65:
        out["overall_signal"] = "BUY"
        out["overall_color"]  = "🟢"
    elif weighted >= 45:
        out["overall_signal"] = "HOLD"
        out["overall_color"]  = "🟡"
    else:
        out["overall_signal"] = "SELL"
        out["overall_color"]  = "🔴"

    return out
