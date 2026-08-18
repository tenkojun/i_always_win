"""
vectorbt 백테스트 러너
========================
단일 실행 (run_backtest) + 파라미터 grid search (run_grid_search).

데이터 소스는 reconcile.fetch_ohlcv_best — FMP>AV>Yahoo>Stooq.
모든 지표는 vectorbt 빌트인 사용 (MA, RSI, MACD).
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

# vectorbt는 무겁고 첫 import 시간이 길어 lazy import
_vbt = None


def _vbt_lib():
    global _vbt
    if _vbt is None:
        import vectorbt as vbt  # noqa
        _vbt = vbt
    return _vbt


# ── 전략 메타 (UI 폼 자동 생성용) ─────────────────────────────
AVAILABLE_STRATEGIES = {
    "sma_cross": {
        "label": "SMA 크로스",
        "desc": "단기 SMA가 장기 SMA를 상향 돌파 시 매수",
        "params": [
            {"name": "fast", "type": "int", "default": 10,
             "min": 3, "max": 50, "label": "단기 SMA"},
            {"name": "slow", "type": "int", "default": 50,
             "min": 10, "max": 200, "label": "장기 SMA"},
        ],
    },
    "rsi_mr": {
        "label": "RSI 평균회귀",
        "desc": "RSI 과매도 진입, 과매수 청산",
        "params": [
            {"name": "window", "type": "int", "default": 14,
             "min": 5, "max": 50, "label": "RSI 기간"},
            {"name": "low", "type": "int", "default": 30,
             "min": 10, "max": 40, "label": "매수 임계"},
            {"name": "high", "type": "int", "default": 70,
             "min": 60, "max": 90, "label": "매도 임계"},
        ],
    },
    "macd": {
        "label": "MACD 히스토그램",
        "desc": "MACD 히스토그램 부호 전환 시 매매",
        "params": [
            {"name": "fast", "type": "int", "default": 12,
             "min": 5, "max": 30, "label": "Fast EMA"},
            {"name": "slow", "type": "int", "default": 26,
             "min": 15, "max": 50, "label": "Slow EMA"},
            {"name": "signal", "type": "int", "default": 9,
             "min": 3, "max": 20, "label": "Signal EMA"},
        ],
    },
    "smc_ob": {
        "label": "SMC Order Block",
        "desc": "기관 주문 흔적(order block) 재테스트 시 진입",
        "params": [
            {"name": "swing", "type": "int", "default": 5,
             "min": 3, "max": 15, "label": "swing 검출 window"},
            {"name": "retest", "type": "int", "default": 8,
             "min": 3, "max": 30, "label": "리테스트 범위(bars)"},
        ],
    },
    "cvd_div": {
        "label": "CVD 다이버전스",
        "desc": "가격 신고가 + CVD 약화 → 약세, 반대도",
        "params": [
            {"name": "lookback", "type": "int", "default": 20,
             "min": 5, "max": 60, "label": "관찰 기간"},
        ],
    },
}

# 확장 전략 (strategies_ext) 14개 자동 등록
from .strategies_ext import STRATEGIES_EXT
for _k, _v in STRATEGIES_EXT.items():
    AVAILABLE_STRATEGIES[_k] = {
        "label": _v["label"], "desc": _v["desc"],
        "params": _v["params"],
    }

# 마이크로구조 전략 3개 자동 등록 (P0-1)
# 일봉 근사 동작 + KIS 키 있을 때 정확
from .micro_strategies import STRATEGIES_MICRO
# strategies_ext와 동일 인터페이스로 통합
for _k, _v in STRATEGIES_MICRO.items():
    AVAILABLE_STRATEGIES[_k] = {
        "label": _v["label"], "desc": _v["desc"],
        "params": _v["params"],
    }
    # vbt_vectorized가 STRATEGIES_EXT에서 자동 등록하므로 강제 추가
    STRATEGIES_EXT[_k] = _v

# Orderflow / PEAD / Hybrid — 일반 전략과 동일 인터페이스로 등록
# (별도 OF+PEAD 탭 없이 STRATEGY 백테스트에서 그대로 선택 가능)
AVAILABLE_STRATEGIES["orderflow"] = {
    "label": "Orderflow (CVD)",
    "desc": "CVD/델타 + 다이버전스 필터. 전일 고점 돌파 + 직전봉 음봉 + 델타 임계 + 매수자 미고갈 → 매수.",
    "params": [
        {"name": "delta_threshold", "type": "int", "default": 500,
         "min": 50, "max": 5000, "label": "Δ 임계"},
        {"name": "divergence_lookback", "type": "int", "default": 10,
         "min": 5, "max": 30, "label": "다이버전스 lookback"},
        {"name": "divergence_z", "type": "float", "default": 1.5,
         "min": 0.5, "max": 3.0, "label": "z 임계"},
        {"name": "max_hold_bars", "type": "int", "default": 30,
         "min": 5, "max": 120, "label": "최대 보유 봉"},
    ],
}
AVAILABLE_STRATEGIES["pead"] = {
    "label": "PEAD (어닝 drift)",
    "desc": "Post-Earnings Announcement Drift. SUE 상위 X%에서 진입, N일 후 청산. 어닝 데이터 필수.",
    "params": [
        {"name": "sue_top_pct", "type": "float", "default": 0.2,
         "min": 0.05, "max": 0.5, "label": "SUE 상위 %"},
        {"name": "drift_days", "type": "int", "default": 30,
         "min": 5, "max": 90, "label": "drift 보유일"},
        {"name": "enter_offset_days", "type": "int", "default": 1,
         "min": 0, "max": 5, "label": "진입 오프셋"},
    ],
}
AVAILABLE_STRATEGIES["hybrid_of_pead"] = {
    "label": "Hybrid OF+PEAD",
    "desc": "Orderflow LONG OR PEAD LONG. 두 시그널 합집합. 어닝 데이터 있는 종목에서 권장.",
    "params": [
        {"name": "delta_threshold", "type": "int", "default": 500,
         "min": 50, "max": 5000, "label": "OF Δ 임계"},
        {"name": "sue_top_pct", "type": "float", "default": 0.2,
         "min": 0.05, "max": 0.5, "label": "PEAD SUE 상위 %"},
        {"name": "drift_days", "type": "int", "default": 30,
         "min": 5, "max": 90, "label": "PEAD 보유일"},
    ],
}

# OF/PEAD 그룹 — run_backtest에서 특수 처리
_OF_PEAD_SET = {"orderflow", "pead", "hybrid_of_pead"}

# D#10 GPU: ML 전략 10조합 (5 모델 × 2 task)
# 일반 전략과 동일 형식 — STRATEGY 백테스트 탭에서 선택 가능
_ML_STRATEGIES = {
    "ml_rf_cls":    {"model": "rf",          "task": "classification",
                      "label": "ML · RF 분류",
                      "desc":  "RandomForest 분류 (상승 확률) — CPU 빠름"},
    "ml_rf_reg":    {"model": "rf",          "task": "regression",
                      "label": "ML · RF 회귀",
                      "desc":  "RandomForest 회귀 (수익률) — CPU 빠름"},
    "ml_xgb_cls":   {"model": "xgb",         "task": "classification",
                      "label": "ML · XGB 분류",
                      "desc":  "XGBoost 분류 — CPU 중간 (정확도 ↑)"},
    "ml_xgb_reg":   {"model": "xgb",         "task": "regression",
                      "label": "ML · XGB 회귀",
                      "desc":  "XGBoost 회귀 — CPU 중간"},
    "ml_lstm_cls":  {"model": "lstm",        "task": "classification",
                      "label": "ML · LSTM 분류",
                      "desc":  "LSTM 분류 — PyTorch GPU 가속 ⚡"},
    "ml_lstm_reg":  {"model": "lstm",        "task": "regression",
                      "label": "ML · LSTM 회귀",
                      "desc":  "LSTM 회귀 — PyTorch GPU 가속 ⚡"},
    "ml_gru_cls":   {"model": "gru",         "task": "classification",
                      "label": "ML · GRU 분류",
                      "desc":  "GRU 분류 — LSTM보다 가벼움 ⚡"},
    "ml_gru_reg":   {"model": "gru",         "task": "regression",
                      "label": "ML · GRU 회귀",
                      "desc":  "GRU 회귀 — LSTM보다 가벼움 ⚡"},
    "ml_tf_cls":    {"model": "transformer", "task": "classification",
                      "label": "ML · Transformer 분류",
                      "desc":  "Transformer Encoder 분류 — GPU 권장 ⚡"},
    "ml_tf_reg":    {"model": "transformer", "task": "regression",
                      "label": "ML · Transformer 회귀",
                      "desc":  "Transformer Encoder 회귀 — GPU 권장 ⚡"},
}

# 표준 params (모든 ML 전략 공통)
_ML_PARAMS = [
    {"name": "horizon", "type": "int", "default": 5,
     "min": 1, "max": 60, "label": "예측 horizon (봉)"},
    {"name": "seq_len", "type": "int", "default": 30,
     "min": 10, "max": 120, "label": "시퀀스 길이 (LSTM/GRU/TF)"},
    {"name": "epochs", "type": "int", "default": 20,
     "min": 5, "max": 80, "label": "학습 에폭 (신경망)"},
    {"name": "train_pct", "type": "float", "default": 0.7,
     "min": 0.5, "max": 0.9, "label": "학습 비율"},
    {"name": "buy_thresh", "type": "float", "default": 0.55,
     "min": 0.50, "max": 0.80, "label": "매수 임계 (분류)"},
    {"name": "sell_thresh", "type": "float", "default": 0.45,
     "min": 0.20, "max": 0.50, "label": "매도 임계 (분류)"},
]

for _ml_id, _ml_meta in _ML_STRATEGIES.items():
    AVAILABLE_STRATEGIES[_ml_id] = {
        "label": _ml_meta["label"],
        "desc":  _ml_meta["desc"],
        "params": _ML_PARAMS,
    }

_ML_SET = set(_ML_STRATEGIES.keys())

# Kronos foundation model 전략 (NeoQuasar K-line forecaster)
# 의존성: transformers + huggingface_hub + einops + shiyu-coder/Kronos 코드
# 키 없이도 등록 (실행 시 graceful fail)
_KRONOS_STRATEGIES = {
    "kronos_mini": {
        "label": "Kronos · mini (4M, CPU)",
        "desc":  "K-line foundation model — Zero-shot 예측. CPU 친화, 빠른 추론.",
    },
    "kronos_small": {
        "label": "Kronos · small (25M, CPU 권장)",
        "desc":  "균형 잡힌 성능 — 1~3초/예측.",
    },
    "kronos_base": {
        "label": "Kronos · base (102M, GPU 권장)",
        "desc":  "고성능 foundation model — GPU 강력 권장.",
    },
}

_KRONOS_PARAMS = [
    {"name": "lookback", "type": "int", "default": 256,
     "min": 64, "max": 512, "label": "입력 봉 수 (컨텍스트)"},
    {"name": "pred_len", "type": "int", "default": 5,
     "min": 1, "max": 30, "label": "예측 봉 수"},
    {"name": "buy_thresh", "type": "float", "default": 0.01,
     "min": 0.001, "max": 0.10, "label": "매수 임계 (예측 수익률)"},
    {"name": "sell_thresh", "type": "float", "default": -0.01,
     "min": -0.10, "max": -0.001, "label": "매도 임계"},
    {"name": "slide_step", "type": "int", "default": 5,
     "min": 1, "max": 30, "label": "예측 간격 (봉)"},
]

for _kr_id, _kr_meta in _KRONOS_STRATEGIES.items():
    AVAILABLE_STRATEGIES[_kr_id] = {
        "label": _kr_meta["label"],
        "desc":  _kr_meta["desc"],
        "params": _KRONOS_PARAMS,
    }

_KRONOS_SET = set(_KRONOS_STRATEGIES.keys())


def _tax_after(ticker: str, total_return: float, n_trades: int) -> Dict[str, Any]:
    """세후 수익률 계산.

    한국 (6자리): 양도소득세 22% (2025년부터 5천만원 이상 누적시)
    미국 (영문): 장기 15% (1년 이상 보유) / 단기 22~37% (1년 미만)
    여기는 단순화 — 한국 22%, 미국 단기 22% (보수적)
    """
    if total_return <= 0:
        return {"tax_rate": 0, "after_tax_return": total_return,
                "market": "?", "note": "손실 시 세금 0"}
    is_kr = ticker.isdigit() and len(ticker) == 6
    if is_kr:
        tax_rate = 0.22
        market = "한국"
        note = "양도세 22% (2025+ · 5천만원 초과 시)"
    else:
        # 단기로 가정 (보수적)
        tax_rate = 0.22
        market = "미국"
        note = "단기 22% 가정 (1년 미만 매매)"
    tax = total_return * tax_rate
    return {
        "tax_rate":         tax_rate,
        "tax_amount_pct":   round(tax, 4),
        "after_tax_return": round(total_return - tax, 4),
        "market":           market,
        "note":             note,
    }


def _build_data_caveats(ticker: str, strategy: str,
                         interval: str) -> Dict[str, Any]:
    """Tier1 #3+#4: 모든 백테스트에 자동 첨부되는 데이터 한계 경고.

    기관 트레이더가 알아야 할 데이터 caveat — alpha 환각 방지.
    """
    caveats = []
    # 1) Survivorship Bias (모든 백테스트 공통)
    caveats.append({
        "severity": "high",
        "code": "survivorship",
        "title": "Survivorship Bias",
        "msg": "현재 상장 종목만 백테스트 — 상폐 종목 제외됨. "
               "실제 알파는 3~5% 부풀려져 있을 수 있음 (학술 검증치).",
    })
    # 2) Corporate Actions (yfinance 기준)
    caveats.append({
        "severity": "medium",
        "code": "corp_actions",
        "title": "Corporate Actions",
        "msg": "yfinance auto_adjust=True 사용 (split/dividend 조정됨). "
               "그러나 배당 재투자 가정은 자동 적용 안 됨 — "
               "배당 수익률 높은 종목(JEPI/SCHD 등)은 5~10% 과소평가 가능.",
    })
    # 3) Point-in-Time (PEAD/orderflow 한정)
    if strategy in ("pead", "hybrid_of_pead"):
        caveats.append({
            "severity": "high",
            "code": "point_in_time",
            "title": "Point-in-Time 미보장 (PEAD)",
            "msg": "어닝 SUE는 재진술된 후 값일 수 있음 — 미래 정보 누설. "
                   "실제 거래 시 Sharpe는 더 낮아질 수 있음.",
        })
    # 4) ML overfit 경고
    if strategy.startswith("ml_"):
        caveats.append({
            "severity": "high",
            "code": "ml_overfit",
            "title": "ML 과적합 위험",
            "msg": "학습 구간 포함 차트 — in-sample Sharpe는 과대평가. "
                   "out-of-sample 30%만 실거래 시그널로 간주하세요. "
                   "Walk-Forward 검증 권장.",
        })
    # 5) 인트라데이 데이터 한계
    if interval in ("1m", "5m", "15m", "30m", "1h"):
        caveats.append({
            "severity": "medium",
            "code": "intraday_data",
            "title": "인트라데이 데이터 한계",
            "msg": f"yfinance {interval}봉 데이터 — 최근 60~730일만, "
                   "장중 빠진 봉/거래소별 불일치 가능. "
                   "실시간 데이터 ≠ 백테스트 데이터.",
        })
    # 6) Bid-Ask Spread 미반영
    caveats.append({
        "severity": "low",
        "code": "spread",
        "title": "Bid-Ask Spread 미반영",
        "msg": "mid price 기준 백테스트 — 실거래는 ask 매수/bid 매도. "
               "유동성 낮은 종목은 추가 비용 50~200bps 가능.",
    })
    return {
        "warnings": caveats,
        "summary": f"{len(caveats)}개 데이터 한계 — 백테스트 결과를 "
                    "실거래로 그대로 옮길 때 보수적으로 해석 필요",
    }


def get_strategy_meta() -> Dict[str, Any]:
    return {"strategies": AVAILABLE_STRATEGIES}


def _fetch_price(ticker: str, period_days: int = 730,
                 interval: str = "1d") -> pd.Series:
    """vectorbt용 종가 Series — reconcile 다중소스 활용. interval 지원."""
    from ..data.sources import fetch_ohlcv_best
    period_days = _clamp_period_for_interval(period_days, interval)
    end = pd.Timestamp.now().strftime("%Y-%m-%d")
    start = (pd.Timestamp.now() - pd.Timedelta(days=period_days)
             ).strftime("%Y-%m-%d")
    r = fetch_ohlcv_best(ticker, start=start, end=end,
                         interval=interval, cross_validate=False)
    df = r["df"]
    return df["close"].astype(float)


def _fetch_ohlcv(ticker: str, period_days: int = 730,
                 interval: str = "1d") -> pd.DataFrame:
    """ext 전략용 — 전체 OHLCV df 반환. interval 지원 (1m/5m/15m/1h/1d)."""
    from ..data.sources import fetch_ohlcv_best
    # yfinance 인트라데이 제한 자동 클램프
    period_days = _clamp_period_for_interval(period_days, interval)
    end = pd.Timestamp.now().strftime("%Y-%m-%d")
    start = (pd.Timestamp.now() - pd.Timedelta(days=period_days)
             ).strftime("%Y-%m-%d")
    r = fetch_ohlcv_best(ticker, start=start, end=end,
                         interval=interval, cross_validate=False)
    return r["df"]


def _clamp_period_for_interval(days: int, interval: str) -> int:
    """yfinance 인트라데이 데이터 제한.
    1m: 7일, 2m/5m/15m/30m: 60일, 60m/1h: 730일, 그 외 무제한."""
    limits = {"1m": 7, "2m": 60, "5m": 60, "15m": 60, "30m": 60,
              "60m": 730, "1h": 730}
    cap = limits.get(interval)
    if cap is None:
        return days
    return min(max(1, days), cap)


def _vbt_freq(interval: str) -> str:
    """interval → vectorbt freq 문자열."""
    return {
        "1m": "1T", "2m": "2T", "5m": "5T", "15m": "15T", "30m": "30T",
        "60m": "1H", "1h": "1H",
        "1d": "1D", "1wk": "1W", "1mo": "1M",
    }.get(interval, "1D")


# ── 전략 시그널 생성 ──────────────────────────────────────────
def _signals_sma_cross(price, fast, slow):
    vbt = _vbt_lib()
    fast_ma = vbt.MA.run(price, fast)
    slow_ma = vbt.MA.run(price, slow)
    entries = fast_ma.ma_crossed_above(slow_ma)
    exits = fast_ma.ma_crossed_below(slow_ma)
    return entries, exits


def _signals_rsi_mr(price, window, low, high):
    vbt = _vbt_lib()
    rsi = vbt.RSI.run(price, window=window)
    entries = rsi.rsi_crossed_below(low)
    exits = rsi.rsi_crossed_above(high)
    return entries, exits


def _signals_smc_ob(price, swing=5, retest=8):
    """
    SMC Order Block (causal 버전 — look-ahead bias 제거).

    원래 알고리즘은 swing 검출에 양옆 N봉을 봐 미래 정보 누설.
    수정: 시점 t에서는 t-N봉까지만 사용해 confirmed swing 검출.
    그래서 swing은 swing 봉 N개 후에야 확정됨 (지연 정상).

    1) i-2N..i-N 구간의 최저점이 confirmed swing low (i 시점에)
    2) 가격이 OB 영역으로 retest 시 매수
    3) 다음 confirmed swing high 도달 시 매도
    """
    n = len(price)
    p = price.values if hasattr(price, "values") else np.asarray(price)
    entries = np.zeros(n, dtype=bool)
    exits = np.zeros(n, dtype=bool)
    in_pos = False

    last_ob_zone = None  # (low, high, end_idx)

    for i in range(2 * swing + 1, n):
        # 시점 i에서 i-swing-1 봉이 confirmed swing인지 검사
        # (그 봉 양옆 swing개 봉을 다 봐야 — i-2*swing..i-1 사용)
        center = i - swing - 1
        if center < swing:
            continue
        window = p[center - swing:center + swing + 1]
        # window는 i-2*swing-1 .. i-1 — 미래 봉 없음
        c_val = p[center]
        # confirmed swing low
        if c_val == window.min() and c_val < p[center - 1]:
            ob_low = c_val
            ob_high = p[max(0, center - 3):center + 1].max()
            last_ob_zone = (ob_low, ob_high, i + retest)
        # confirmed swing high
        elif c_val == window.max() and c_val > p[center - 1]:
            if in_pos:
                exits[i] = True
                in_pos = False

        # 진입 체크 (현재 봉 가격 p[i]가 OB 영역에 들어오면)
        if not in_pos and last_ob_zone is not None:
            lo, hi, end = last_ob_zone
            if i <= end and lo <= p[i] <= hi:
                entries[i] = True
                in_pos = True
                last_ob_zone = None

    return pd.Series(entries, index=price.index), \
           pd.Series(exits, index=price.index)


def _signals_cvd_div(price, lookback=20):
    """
    CVD 다이버전스 — PseudoCVDDiv IndicatorFactory 사용.
    close 차분 부호 누적으로 가상 CVD를 만들고 다이버전스 감지.
    numba 설치 시 자동 JIT 가속, 없으면 numpy 폴백.
    """
    from .vbt_indicators import PseudoCVDDiv
    ind   = PseudoCVDDiv.run(price, lookback=lookback)
    entry = ind.sig_entry.squeeze()
    exit_ = ind.sig_exit.squeeze()
    # squeeze 후 Series가 아닌 경우 인덱스 복원
    if not isinstance(entry, pd.Series):
        entry = pd.Series(entry.values, index=price.index)
        exit_ = pd.Series(exit_.values, index=price.index)
    else:
        entry.index = price.index
        exit_.index = price.index
    return entry.astype(bool), exit_.astype(bool)


def _signals_macd(price, fast, slow, signal):
    vbt = _vbt_lib()
    macd = vbt.MACD.run(price, fast_window=fast, slow_window=slow,
                        signal_window=signal)
    hist = macd.hist
    # 히스토그램 양수→매수, 음수→청산
    entries = (hist > 0) & (hist.shift(1) <= 0)
    exits = (hist < 0) & (hist.shift(1) >= 0)
    return entries, exits


# ── 통합 백테스트 ─────────────────────────────────────────────
def run_backtest(ticker: str, strategy: str,
                 params: Optional[Dict[str, Any]] = None,
                 period_days: int = 730,
                 fees: float = 0.001,
                 init_cash: float = 10000.0,
                 interval: str = "1d",
                 # P5: 포지션 사이징 / 레버리지 / 손익절
                 size: Optional[float] = None,
                 size_type: str = "amount",   # amount|percent|value|targetpercent
                 leverage: float = 1.0,
                 slippage: float = 0.0001,
                 sl_stop: Optional[float] = None,  # 예: 0.05 = 5%
                 tp_stop: Optional[float] = None,
                 trail_stop: Optional[float] = None,
                 # Tier1 #2 + P1 #4/#5: 시장 임팩트 + Corwin-Schultz spread + Latency
                 auto_slippage: bool = False,
                 latency_ms: float = 0.0,    # P1 #5: 주문 지연 비용 (분봉에서 유의미)
                 # 룩어헤드 bias 제거 — 시그널 → 다음 봉 시가에 실행
                 next_bar_exec: bool = True,
                 ) -> Dict[str, Any]:
    """단일 파라미터 백테스트. interval로 인트라데이 (1m/5m/15m/1h) 지원.

    P5 옵션:
      size       — 한 트레이드 단위 (None=전액)
      size_type  — amount(주식수), percent(자본비율), value(달러), targetpercent
      leverage   — 1.0(현물) / 2.0 / 3.0 (마진)
      slippage   — 슬리피지 (0.0001 = 1bp)
      sl_stop    — Stop-loss % (0.05 = -5%)
      tp_stop    — Take-profit % (0.10 = +10%)
      trail_stop — Trailing stop %
    """
    if strategy not in AVAILABLE_STRATEGIES:
        return {"ok": False, "error": f"알 수 없는 전략: {strategy}"}
    params = params or {}
    defaults = {p["name"]: p["default"]
                for p in AVAILABLE_STRATEGIES[strategy]["params"]}
    cfg = {**defaults, **params}
    # 인트라데이는 자동 기간 클램프
    period_days = _clamp_period_for_interval(period_days, interval)
    t0 = time.time()
    try:
        vbt = _vbt_lib()
        price = _fetch_price(ticker, period_days, interval=interval)
        # 최소 bar 수는 interval과 무관 (30개 봉 = 1m면 30분, 1d면 30일)
        if len(price) < 30:
            return {"ok": False,
                    "error": f"데이터 부족 ({len(price)}봉 < 30, "
                             f"interval={interval})"}

        if strategy == "sma_cross":
            entries, exits = _signals_sma_cross(
                price, cfg["fast"], cfg["slow"])
        elif strategy == "rsi_mr":
            entries, exits = _signals_rsi_mr(
                price, cfg["window"], cfg["low"], cfg["high"])
        elif strategy == "macd":
            entries, exits = _signals_macd(
                price, cfg["fast"], cfg["slow"], cfg["signal"])
        elif strategy == "smc_ob":
            entries, exits = _signals_smc_ob(
                price, swing=cfg["swing"], retest=cfg["retest"])
        elif strategy == "cvd_div":
            entries, exits = _signals_cvd_div(
                price, lookback=cfg["lookback"])
        elif strategy in STRATEGIES_EXT:
            # 확장 전략 — OHLCV 필요
            ohlcv = _fetch_ohlcv(ticker, period_days, interval=interval)
            if len(ohlcv) < 30:
                return {"ok": False, "error": "데이터 부족"}
            fn = STRATEGIES_EXT[strategy]["fn"]
            entries, exits = fn(ohlcv, **cfg)
            # price를 ohlcv의 close로 교체 (백테스트용)
            price = ohlcv["close"].astype(float)
        elif strategy in _KRONOS_SET:
            # Kronos foundation model 전략
            ohlcv = _fetch_ohlcv(ticker, period_days, interval=interval)
            if len(ohlcv) < int(cfg.get("lookback", 256)) + 30:
                return {"ok": False,
                        "error": f"데이터 부족 (lookback {cfg.get('lookback')} 필요)"}
            try:
                from .kronos_predictor import generate_kronos_signals
                entries, exits = generate_kronos_signals(
                    ohlcv,
                    model_key=strategy,
                    lookback=int(cfg.get("lookback", 256)),
                    pred_len=int(cfg.get("pred_len", 5)),
                    buy_thresh=float(cfg.get("buy_thresh", 0.01)),
                    sell_thresh=float(cfg.get("sell_thresh", -0.01)),
                    slide_step=int(cfg.get("slide_step", 5)),
                )
                price = ohlcv["close"].astype(float)
            except Exception as e:
                import traceback
                return {"ok": False,
                        "error": f"{strategy} 실패: {type(e).__name__}: {e}",
                        "trace": traceback.format_exc()[-400:]}
        elif strategy in _ML_SET:
            # D#10 GPU: ML 전략 — generate_ml_signals 호출 (학습 + 시그널)
            ohlcv = _fetch_ohlcv(ticker, period_days, interval=interval)
            if len(ohlcv) < 100:
                return {"ok": False,
                        "error": f"ML 학습 데이터 부족 (n={len(ohlcv)} < 100)"}
            ml_meta = _ML_STRATEGIES[strategy]
            try:
                from .ml_predict import generate_ml_signals
                entries, exits = generate_ml_signals(
                    ohlcv,
                    model_type=ml_meta["model"],
                    task=ml_meta["task"],
                    horizon=int(cfg.get("horizon", 5)),
                    seq_len=int(cfg.get("seq_len", 30)),
                    epochs=int(cfg.get("epochs", 20)),
                    train_pct=float(cfg.get("train_pct", 0.7)),
                    buy_thresh=float(cfg.get("buy_thresh", 0.55)),
                    sell_thresh=float(cfg.get("sell_thresh", 0.45)),
                )
                price = ohlcv["close"].astype(float)
            except Exception as e:
                import traceback
                return {"ok": False,
                        "error": f"{strategy} 학습/시그널 실패: "
                                 f"{type(e).__name__}: {e}",
                        "trace": traceback.format_exc()[-400:]}
        elif strategy in _OF_PEAD_SET:
            # OF/PEAD/Hybrid — orderflow_pead 엔진 위임
            from ..orderflow_pead.main import build_bundle
            from ..orderflow_pead import (
                OrderflowDeltaStrategy, PEADStrategy,
                Hybrid_OF_PEAD_Strategy,
            )
            try:
                bundle = build_bundle(ticker, period_days=period_days,
                                       interval=interval)
            except Exception as e:
                return {"ok": False,
                        "error": f"OF/PEAD bundle 실패: {type(e).__name__}: {e}"}
            # PEAD/Hybrid는 earnings 필요
            ev = bundle.get("earnings")
            if strategy in ("pead", "hybrid_of_pead") and (ev is None or ev.empty):
                return {"ok": False,
                        "error": f"{strategy}: 어닝 데이터 없음 "
                                 f"(소형주/ETF/암호화폐는 미지원)"}
            try:
                if strategy == "orderflow":
                    strat = OrderflowDeltaStrategy(
                        delta_threshold=cfg.get("delta_threshold", 500),
                        divergence_lookback=cfg.get("divergence_lookback", 10),
                        divergence_z=cfg.get("divergence_z", 1.5),
                        max_hold_bars=cfg.get("max_hold_bars", 30),
                    )
                elif strategy == "pead":
                    strat = PEADStrategy(
                        sue_top_pct=cfg.get("sue_top_pct", 0.2),
                        drift_days=cfg.get("drift_days", 30),
                        enter_offset_days=cfg.get("enter_offset_days", 1),
                    )
                else:  # hybrid_of_pead
                    strat = Hybrid_OF_PEAD_Strategy(
                        of_params={"delta_threshold": cfg.get("delta_threshold", 500)},
                        pead_params={"sue_top_pct": cfg.get("sue_top_pct", 0.2),
                                     "drift_days":  cfg.get("drift_days", 30)},
                    )
                sig = strat.generate_signals(bundle)
                entries = sig["entries"]
                exits = sig["exits"]
                # price를 bundle close로 교체 — 인덱스 정합성 확보
                price = bundle["ohlcv"]["close"].astype(float)
            except Exception as e:
                import traceback
                return {"ok": False,
                        "error": f"OF/PEAD 시그널 생성 실패: {type(e).__name__}: {e}",
                        "trace": traceback.format_exc()[-400:]}
        else:
            return {"ok": False, "error": "전략 미구현"}

        # Tier1 #2 + P1 #4/#5: 자동 슬리피지 (시장임팩트 + Corwin-Schultz spread + latency)
        slippage_meta = None
        if auto_slippage:
            try:
                from ..backtest.market_impact import auto_slippage_for_backtest
                vol_ser = high_ser = low_ser = None
                try:
                    _ohlcv_for_vol = _fetch_ohlcv(ticker, period_days,
                                                    interval=interval)
                    if "volume" in _ohlcv_for_vol.columns:
                        vol_ser = _ohlcv_for_vol["volume"]
                    if "high" in _ohlcv_for_vol.columns:
                        high_ser = _ohlcv_for_vol["high"]
                    if "low" in _ohlcv_for_vol.columns:
                        low_ser = _ohlcv_for_vol["low"]
                except Exception:
                    pass
                # 봉 간격 → bars_per_day 추정
                bars_per_day = {"1m": 390, "5m": 78, "15m": 26, "1h": 7,
                                 "1d": 1}.get(interval, 1)
                # latency_ms는 함수 시그니처에 추가됨 (locals() lookup으로 옵션화)
                _lat = float(locals().get("latency_ms") or 0.0)
                slip_r = auto_slippage_for_backtest(
                    price, vol_ser, init_cash, position_size_pct=1.0,
                    high=high_ser, low=low_ser,
                    latency_ms=_lat, bars_per_day=bars_per_day)
                slippage = float(slip_r.get("slippage_pct", slippage))
                slippage_meta = slip_r
            except Exception:
                pass

        # interval에 맞는 freq (Sharpe 정확도) + P5 옵션 (size/leverage/SL/TP)
        pf_kwargs = {
            "fees": fees, "slippage": slippage,
            "init_cash": init_cash, "freq": _vbt_freq(interval),
        }
        # size / size_type
        if size is not None:
            pf_kwargs["size"] = float(size)
        _SIZE_TYPE_MAP = {
            "amount": "amount", "percent": "percent",
            "value": "value", "targetpercent": "targetpercent",
        }
        if size_type in _SIZE_TYPE_MAP and size is not None:
            try:
                pf_kwargs["size_type"] = _SIZE_TYPE_MAP[size_type]
            except Exception:
                pass
        # leverage (vbt 0.27는 short_entries/short_exits로 처리,
        # leverage 직접 옵션 없음 — size 보정으로 대체)
        if leverage and leverage > 0 and leverage != 1.0:
            # 단순 구현: 모든 size를 leverage배 (size가 percent면 의미 명확)
            if "size" in pf_kwargs:
                pf_kwargs["size"] = pf_kwargs["size"] * float(leverage)
            elif size_type == "percent":
                pf_kwargs["size"] = float(leverage) * 100.0
                pf_kwargs["size_type"] = "percent"
        # SL / TP / Trailing
        if sl_stop is not None and sl_stop > 0:
            pf_kwargs["sl_stop"] = float(sl_stop)
        if tp_stop is not None and tp_stop > 0:
            pf_kwargs["tp_stop"] = float(tp_stop)
        if trail_stop is not None and trail_stop > 0:
            pf_kwargs["sl_trail"] = True
            pf_kwargs["sl_stop"] = float(trail_stop)
        # ⚠ 룩어헤드 bias 제거: 시그널 t → 거래 t+1 시가
        # 이전: t의 close에 시그널 발생 → 같은 봉 close에 거래 (look-ahead)
        # 이후: t의 close 후 신호 → t+1 봉 시가에 거래 (현실)
        if next_bar_exec:
            try:
                # entries/exits를 한 봉 미루기
                if hasattr(entries, "shift"):
                    entries = entries.shift(1).fillna(False).astype(bool)
                if hasattr(exits, "shift"):
                    exits = exits.shift(1).fillna(False).astype(bool)
            except Exception:
                pass

        try:
            pf = vbt.Portfolio.from_signals(price, entries, exits, **pf_kwargs)
        except TypeError as _te:
            # vbt 버전별 옵션 차이 — 알 수 없는 kwarg 제거 후 재시도
            for bad in ("sl_stop","tp_stop","sl_trail","size_type"):
                pf_kwargs.pop(bad, None)
            pf = vbt.Portfolio.from_signals(price, entries, exits, **pf_kwargs)

        # 매수/매도 신호 timestamp (차트 마커용)
        def _to_bool_arr(sig):
            """vectorbt 시그널 → length-N boolean array."""
            if hasattr(sig, "values"):
                arr = np.asarray(sig.values)
            else:
                arr = np.asarray(sig)
            arr = arr.ravel() if arr.ndim > 1 else arr
            arr = np.nan_to_num(arr.astype(float), nan=0).astype(bool)
            # 길이 보정
            if len(arr) > len(price):
                arr = arr[:len(price)]
            elif len(arr) < len(price):
                pad = np.zeros(len(price) - len(arr), dtype=bool)
                arr = np.concatenate([arr, pad])
            return arr

        ent_mask = _to_bool_arr(entries)
        exit_mask = _to_bool_arr(exits)
        entry_dates = price.index[ent_mask].tolist()
        exit_dates = price.index[exit_mask].tolist()
        entry_signals = [d.strftime("%Y-%m-%d") for d in entry_dates]
        exit_signals = [d.strftime("%Y-%m-%d") for d in exit_dates]

        # 핵심 지표
        total_ret = float(pf.total_return())
        sharpe = float(pf.sharpe_ratio())
        sortino = float(pf.sortino_ratio())
        max_dd = float(pf.max_drawdown())
        win_rate = float(pf.trades.win_rate())
        n_trades = int(pf.trades.count())
        # 벤치마크 (buy&hold)
        bh_ret = float((price.iloc[-1] / price.iloc[0]) - 1)
        # equity curve 다운샘플 (최대 200점)
        eq = pf.value()
        if len(eq) > 200:
            step = len(eq) // 200
            eq = eq.iloc[::step]
        equity = [{"d": d.strftime("%Y-%m-%d"), "v": float(v)}
                  for d, v in eq.items()]

        # B10: 통계적 유의 — PSR (Probabilistic Sharpe Ratio)
        psr_val, dsr_val = 0.5, 0.5
        rets_for_qs = None
        try:
            from engine.institutional.precision import (
                probabilistic_sharpe_ratio, deflated_sharpe_ratio)
            rets = pf.returns()
            if rets is not None and len(rets) >= 10:
                rets_for_qs = rets
                rets_arr = rets.values.astype(float)
                psr_r = probabilistic_sharpe_ratio(rets_arr, sr_benchmark=0.0)
                psr_val = float(psr_r.get("psr", 0.5))
                if np.isnan(psr_val):
                    psr_val = 0.5
                # DSR — n_trials=10 (대략 19 전략 / 평균적 시도수)
                dsr_r = deflated_sharpe_ratio(rets_arr, n_trials=10)
                dsr_val = float(dsr_r.get("dsr", 0.5))
                if np.isnan(dsr_val):
                    dsr_val = 0.5
        except Exception:
            pass

        # P3: 풍부 메트릭 + 월별 히트맵 + qs_extra (모두 실패 시 기본값)
        stats_full: Dict[str, Any] = {"groups": [], "raw": {}}
        monthly_heatmap: Dict[str, Any] = {"data": [], "layout": {}}
        qs_extra: Dict[str, Any] = {}
        try:
            from .pf_stats import (extract_full_stats,
                                    monthly_returns_heatmap, qs_extra_stats)
            stats_full      = extract_full_stats(pf)
            monthly_heatmap = monthly_returns_heatmap(
                pf, title=f"{ticker} · {strategy} 월별 수익률 %")
            if rets_for_qs is not None:
                qs_extra = qs_extra_stats(rets_for_qs)
        except Exception:
            pass

        return {
            "ok": True,
            "ticker": ticker,
            "strategy": strategy,
            "params": cfg,
            "interval": interval,
            "n_bars": int(len(price)),
            "elapsed_sec": round(time.time() - t0, 2),
            "metrics": {
                "total_return": round(total_ret, 4),
                "buy_hold_return": round(bh_ret, 4),
                "alpha": round(total_ret - bh_ret, 4),
                "sharpe": round(sharpe, 3) if np.isfinite(sharpe) else 0,
                "sortino": round(sortino, 3) if np.isfinite(sortino) else 0,
                "max_drawdown": round(max_dd, 4),
                "win_rate": round(win_rate, 4)
                            if np.isfinite(win_rate) else 0,
                "n_trades": n_trades,
                # B10: 통계 유의도
                "psr": round(psr_val, 4),
                "dsr": round(dsr_val, 4),
            },
            # P3: 풍부 지표 (30+) + 월별 히트맵 + qs 추가
            "stats_full":      stats_full,
            "monthly_heatmap": monthly_heatmap,
            "qs_extra":        qs_extra,
            "equity_curve": equity,
            "data_points": len(price),
            "entry_signals": entry_signals,
            "exit_signals": exit_signals,
            # Tier1 #2: Almgren-Chriss 슬리피지 메타
            "slippage_used": slippage,
            "slippage_meta": slippage_meta,
            # Tier1 #3+#4: 데이터 신뢰성 경고 (모든 백테스트에 자동)
            "data_caveats": _build_data_caveats(ticker, strategy, interval),
            # P2-1: Tax-aware 세후 수익률
            "tax": _tax_after(ticker, total_ret, n_trades),
        }
    except Exception as e:
        return {"ok": False,
                "error": f"{type(e).__name__}: {e}",
                "elapsed_sec": round(time.time() - t0, 2)}


# ── 파라미터 grid search ─────────────────────────────────────
def run_grid_search(ticker: str, strategy: str,
                    grid: Dict[str, List[Any]],
                    period_days: int = 730,
                    fees: float = 0.001,
                    top_n: int = 10) -> Dict[str, Any]:
    """
    파라미터 grid 탐색 — Sharpe 기준 상위 N개 반환.

    grid 예: {"fast": [5, 10, 20], "slow": [30, 50, 100]}
    """
    if strategy not in AVAILABLE_STRATEGIES:
        return {"ok": False, "error": f"알 수 없는 전략: {strategy}"}
    if not grid:
        return {"ok": False, "error": "grid 비어 있음"}
    t0 = time.time()
    try:
        # 카르테시안 곱 → 각 조합 백테스트
        from itertools import product
        keys = list(grid.keys())
        combos = list(product(*[grid[k] for k in keys]))
        # 제한 제거 (사용자 요청) — 단, 5000조합 초과 시 경고만
        if len(combos) > 5000:
            return {"ok": False,
                    "error": f"조합이 비현실적 ({len(combos)} > 5000) — "
                             "각 파라미터 값 수를 줄이세요"}

        results = []
        for vals in combos:
            params = dict(zip(keys, vals))
            r = run_backtest(ticker, strategy, params=params,
                             period_days=period_days, fees=fees)
            if r.get("ok"):
                m = r["metrics"]
                results.append({
                    "params": params,
                    "sharpe": m["sharpe"],
                    "total_return": m["total_return"],
                    "max_drawdown": m["max_drawdown"],
                    "n_trades": m["n_trades"],
                })
        # Sharpe 내림차순
        results.sort(key=lambda x: -x["sharpe"])
        # 파라미터 한글 라벨
        meta = AVAILABLE_STRATEGIES.get(strategy, {})
        param_labels = {p["name"]: p.get("label", p["name"])
                          for p in meta.get("params", [])}
        return {
            "ok": True,
            "ticker": ticker,
            "strategy": strategy,
            "n_tested": len(results),
            "top_n": top_n,
            "results": results[:top_n],
            "elapsed_sec": round(time.time() - t0, 2),
            "param_labels": param_labels,
        }
    except Exception as e:
        return {"ok": False,
                "error": f"{type(e).__name__}: {e}",
                "elapsed_sec": round(time.time() - t0, 2)}
