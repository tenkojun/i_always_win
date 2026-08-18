"""
시장 프록시 / 팩터 패널 빌더
==============================
단일 종목만 분석하는 도구에는 '시장 전체' 데이터가 없다.
하지만 Aladdin식 팩터 위험 분해는 시장 팩터가 있어야 의미가 있다.

이 모듈은 다음 순서로 팩터 패널(MKT/SMB/HML/RMW/CMA/RF)을 만든다.

1. (온라인) yfinance 로 실제 시장지수를 받아 MKT 로 사용
     - .KS / .KQ  →  ^KS11 (코스피)
     - 그 외       →  ^GSPC (S&P500)
   스타일 팩터(SMB/HML/RMW/CMA)는 소규모 합성값으로 대체
   (정밀 분석은 Kenneth French 데이터 CSV 를 별도 주입 권장)

2. (오프라인/실패) 종목 수익률에서 '시장 성분 + 고유 성분' 을
   분해해 현실적인 베타(≈1)와 설명력(R²≈0.35)을 갖도록 MKT 를
   합성한다.  → 데모에서도 분해 결과가 해석 가능하도록.

⚠️ 2번은 근사(proxy)다. 리포트에 'proxy' 임을 명시한다.
"""
from __future__ import annotations
from typing import Tuple
import numpy as np
import pandas as pd


def _index_symbol(ticker: str) -> str:
    t = ticker.upper()
    if t.endswith(".KS"):
        return "^KS11"
    if t.endswith(".KQ"):
        return "^KQ11"
    return "^GSPC"


def _synthetic_style(index: pd.DatetimeIndex, seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    n = len(index)
    return pd.DataFrame({
        "SMB": rng.normal(0.0001, 0.004, n),
        "HML": rng.normal(0.0000, 0.004, n),
        "RMW": rng.normal(0.0001, 0.003, n),
        "CMA": rng.normal(0.0000, 0.003, n),
        "RF":  np.full(n, 0.03 / 252),
    }, index=index)


def build_factor_panel(stock_returns: pd.Series,
                        ticker: str = "",
                        try_real: bool = True,
                        seed: int = 7) -> Tuple[pd.DataFrame, bool]:
    """
    Returns
    -------
    (factors_df, is_real_market)
        factors_df     : MKT, SMB, HML, RMW, CMA, RF 열
        is_real_market : 실제 지수를 받았으면 True, 근사면 False
    """
    idx = stock_returns.index
    real = False
    mkt = None

    if try_real:
        try:
            import yfinance as yf
            sym = _index_symbol(ticker)
            mdf = yf.download(sym,
                              start=str(idx[0].date()),
                              end=str(idx[-1].date()),
                              auto_adjust=True, progress=False)
            if mdf is not None and not mdf.empty:
                if isinstance(mdf.columns, pd.MultiIndex):
                    mdf.columns = mdf.columns.get_level_values(0)
                mclose = mdf["Close"] if "Close" in mdf.columns else mdf.iloc[:, 0]
                mret = mclose.pct_change().dropna()
                mret.index = pd.to_datetime(mret.index)
                mkt = mret.reindex(idx).dropna()
                if len(mkt) > len(idx) * 0.5:
                    real = True
        except Exception:
            mkt = None

    if mkt is None or not real:
        # ---- 근사: 종목에서 시장성분 추출 ----
        rng = np.random.default_rng(seed)
        r = stock_returns.values
        # 시장 = 종목의 평활(공통) 성분 + 독립 잡음 (목표 베타≈1, R²≈0.35)
        common = pd.Series(r, index=idx).rolling(3, min_periods=1).mean().values
        noise = rng.normal(0, np.std(r) * 1.1, len(r))
        mkt_vals = 0.55 * common + 0.45 * noise
        mkt = pd.Series(mkt_vals, index=idx)

    style = _synthetic_style(idx, seed=seed)
    factors = pd.concat([mkt.rename("MKT"), style], axis=1).dropna()
    return factors, real
