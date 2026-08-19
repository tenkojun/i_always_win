# -*- coding: utf-8 -*-
"""
Plutus — 분석 엔진 패키지
================================
주요 진입점을 **지연 임포트**로 노출한다.

무거운 임포트를 모듈 최상단에서 하면
``from engine.paths import DATA_DIR`` 같은 가벼운 사용조차
scikit-learn 전체를 끌고 오고, 선택적 의존성이 하나라도
빠지면 패키지 전체가 임포트 불가가 된다.
PEP 562 ``__getattr__`` 로 실제 접근 시점까지 미룬다.

v2.15.0 에서 구엔진(``engine.analysis`` / ``engine.institutional`` 등)을
제거했다. 그 지연 export 들도 함께 걷어냈다 — 실제 분석 경로는
``engine.jiqtx`` 이고, 거기는 자체 진입점(``jiqtx.analyze``)을 쓴다.
"""
from __future__ import annotations

import importlib
from typing import Any

try:
    from version import __version__  # 저장소 루트의 단일 버전 소스
except Exception:  # pragma: no cover - 얼린 환경 등
    __version__ = "0.0.0"

# 공개 이름 → (모듈 경로, 모듈 내 이름)
_LAZY: dict[str, tuple[str, str]] = {
    "load_ticker":     ("engine.data.loader", "load_ticker"),
    "synthetic_ohlcv": ("engine.data.loader", "synthetic_ohlcv"),
}

__all__ = ["__version__", *_LAZY]


def __getattr__(name: str) -> Any:
    try:
        mod_path, attr = _LAZY[name]
    except KeyError:
        raise AttributeError(f"module 'engine' has no attribute {name!r}") from None
    value = getattr(importlib.import_module(mod_path), attr)
    globals()[name] = value  # 두 번째부터는 캐시된 값
    return value


def __dir__() -> list[str]:
    return sorted(__all__)
