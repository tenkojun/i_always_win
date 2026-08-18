"""
미래 주가 몬테카를로 예측 모듈
================================
과거 수익률을 학습해 **미래 주가 경로**를 수천 개 시뮬레이션하고,
- 분위수 밴드 (5 / 25 / 50 / 75 / 95%)
- 종착 가격 분포 통계 (평균·중앙값·표준편차·왜도·첨도)
- 현재가 대비 상승 확률, +10% / +20% 도달 확률
- 하락 위험 (-10% / -20% 확률, VaR / CVaR)
를 산출한다.

블랙록 등 기관은 미래 자산가치를 점(point)으로 보지 않고
**확률 분포**로 본다. 이 모듈은 그 관점을 단/중/장 기간별로 제공한다.

method (모델)
-------------
- gbm       : 기하 브라운 운동. log수익률의 μ, σ 로 정규 샘플링
              (블랙-숄즈와 동일한 가정, 해석이 쉬움)
- bootstrap : 실제 과거 수익률을 복원추출 (팻테일·비대칭 반영)

출력 dict 속성
--------------
- name              : '단기' / '중기' / '장기'
- horizon_days      : 예측 영업일 수
- start_price       : 시뮬 시작가 (현재가)
- paths_sample      : (≤200, horizon) 표본 경로 (그래프용)
- pctl              : {p5,p25,p50,p75,p95: (horizon,)} 분위수 밴드
- terminal          : (n_sim,) 종착 가격
- exp_price         : 종착 기대가격 (평균)
- median_price      : 종착 중앙값 가격
- std_pct           : 종착 수익률 표준편차 (%)
- up_prob           : 현재가보다 높을 확률 (%)
- prob_up_10        : +10% 이상 확률 (%)
- prob_up_20        : +20% 이상 확률 (%)
- prob_dn_10        : -10% 이하 확률 (%)
- prob_dn_20        : -20% 이하 확률 (%)
- exp_return_pct    : 기대수익률 (%)
- ci90_low/high     : 5% / 95% 종착 가격
- var_95_pct        : 95% 신뢰 VaR (종착 손실 %, 양수)
- cvar_95_pct       : 95% CVaR (꼬리 평균 손실 %)
- skew / kurtosis   : 종착 분포 왜도·첨도
"""
from __future__ import annotations
from typing import Any, Dict, Optional
import numpy as np
import pandas as pd


def project_price_paths(df: pd.DataFrame,
                        horizon: int,
                        name: str = "",
                        train_lookback: Optional[int] = None,
                        n_sim: int = 3000,
                        method: str = "gbm",
                        seed: int = 42) -> Dict[str, Any]:
    """
    미래 주가 경로를 시뮬레이션한다.

    Parameters
    ----------
    df             : OHLCV (close 열 필요)
    horizon        : 며칠 앞까지 예측할지 (영업일)
    name           : 라벨 ('단기' 등)
    train_lookback : 학습에 쓸 과거 일수. None 이면 horizon×4 (없으면 전체)
    n_sim          : 시뮬레이션 횟수
    method         : 'gbm' | 'bootstrap'
    """
    close = df["close"].dropna()
    if len(close) < 30:
        return {"name": name, "note": "데이터 부족"}

    if train_lookback is None:
        train_lookback = min(len(close) - 1, max(horizon * 4, 120))
    train = close.iloc[-train_lookback:]
    log_r = np.log(train / train.shift(1)).dropna().values
    if len(log_r) < 10:
        return {"name": name, "note": "데이터 부족"}

    rng = np.random.default_rng(seed)
    s0 = float(close.iloc[-1])

    if method == "bootstrap":
        idx = rng.integers(0, len(log_r), size=(n_sim, horizon))
        sim_log = log_r[idx]
    else:  # gbm
        mu = float(log_r.mean())
        sd = float(log_r.std(ddof=1))
        sim_log = rng.normal(mu, sd, size=(n_sim, horizon))

    # 누적 → 가격 경로
    paths = s0 * np.exp(np.cumsum(sim_log, axis=1))
    terminal = paths[:, -1]
    ret = terminal / s0 - 1.0

    pctl = {
        "p5":  np.percentile(paths, 5,  axis=0),
        "p25": np.percentile(paths, 25, axis=0),
        "p50": np.percentile(paths, 50, axis=0),
        "p75": np.percentile(paths, 75, axis=0),
        "p95": np.percentile(paths, 95, axis=0),
    }

    # 종착 손익 분포
    q05 = float(np.percentile(ret, 5))
    tail = ret[ret <= q05]

    # 왜도·첨도 (scipy 없이)
    rc = ret - ret.mean()
    s = rc.std()
    skew = float((rc**3).mean() / s**3) if s > 0 else 0.0
    kurt = float((rc**4).mean() / s**4 - 3.0) if s > 0 else 0.0

    sample_n = min(200, n_sim)
    sample_idx = rng.choice(n_sim, size=sample_n, replace=False)

    return {
        "name":          name,
        "horizon_days":  int(horizon),
        "method":        method,
        "start_price":   s0,
        "paths_sample":  paths[sample_idx],
        "pctl":          pctl,
        "terminal":      terminal,
        "exp_price":     float(terminal.mean()),
        "median_price":  float(np.median(terminal)),
        "std_pct":       float(ret.std() * 100),
        "up_prob":       float((ret > 0).mean() * 100),
        "prob_up_10":    float((ret >= 0.10).mean() * 100),
        "prob_up_20":    float((ret >= 0.20).mean() * 100),
        "prob_dn_10":    float((ret <= -0.10).mean() * 100),
        "prob_dn_20":    float((ret <= -0.20).mean() * 100),
        "exp_return_pct": float(ret.mean() * 100),
        "ci90_low":      float(np.percentile(terminal, 5)),
        "ci90_high":     float(np.percentile(terminal, 95)),
        "var_95_pct":    float(-q05 * 100),
        "cvar_95_pct":   float(-tail.mean() * 100) if len(tail) else 0.0,
        "skew":          skew,
        "kurtosis":      kurt,
    }


def mc_by_timeframe(df: pd.DataFrame,
                    timeframes: Dict[str, Dict],
                    method: str = "gbm",
                    n_sim: int = 3000,
                    seed: int = 42) -> Dict[str, Dict[str, Any]]:
    """
    단/중/장 각 기간에 대해 미래 주가 몬테카를로를 실행한다.

    예측 지평(horizon)은 각 타임프레임의 lookback 을 그대로 사용한다.
    - 단기  : 60영업일  (≈ 3개월 후)
    - 중기  : 252영업일 (≈ 1년 후)
    - 장기  : 1260영업일(≈ 5년 후)
    """
    out: Dict[str, Dict[str, Any]] = {}
    for name, cfg in timeframes.items():
        horizon = int(cfg.get("lookback", 252))
        out[name] = project_price_paths(
            df, horizon=horizon, name=name,
            method=method, n_sim=n_sim, seed=seed,
        )
    return out
