"""
KIS Provider — fetch_ohlcv_best 폴백 체인용 wrapper
============================================================
KIS API를 표준 Provider 인터페이스로 노출. 키 없거나 실패 시
자동으로 다음 소스(FMP/AV/Yahoo/Stooq)로 폴백.
"""
from __future__ import annotations

from typing import Optional
import pandas as pd

from .base import Provider, _normalize


class KISProvider(Provider):
    """한국투자증권 OpenAPI Provider.
    - 모의(vts) 우선 → 실패 시 실전(real)로 시도
    - 미국 주식: NASDAQ 자동 (NYSE 종목도 NASDAQ 시도, 실패 시 다음 소스)
    """
    name = "kis"
    needs_key = True
    capabilities = ("ohlcv",)

    def __init__(self, prefer_mode: str = "vts"):
        # vts (모의) 권장 — 데이터 시세는 동일
        self.prefer_mode = prefer_mode

    def is_available(self) -> bool:
        try:
            from .kis import has_keys
            return has_keys(self.prefer_mode) or has_keys(
                "real" if self.prefer_mode == "vts" else "vts")
        except Exception:
            return False

    def fetch_ohlcv(self, ticker: str, start: str = "2010-01-01",
                     end: Optional[str] = None,
                     interval: str = "1d") -> pd.DataFrame:
        """우선 prefer_mode → 실패 시 다른 모드 시도."""
        from .kis import fetch_ohlcv_kis, has_keys
        last_err = None
        # 모드 fallback 순서
        modes = [self.prefer_mode]
        other = "real" if self.prefer_mode == "vts" else "vts"
        if has_keys(other):
            modes.append(other)
        for m in modes:
            if not has_keys(m):
                continue
            try:
                df = fetch_ohlcv_kis(ticker, start=start, end=end,
                                       interval=interval, mode=m)
                return _normalize(df)
            except Exception as e:
                last_err = e
                continue
        if last_err:
            raise last_err
        raise RuntimeError("KIS 키 없음")
