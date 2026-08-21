# -*- coding: utf-8 -*-
"""
테스트 공통 설정.

원칙
----
- **네트워크를 타지 않는다.** 시세 API 가 죽어도 테스트는 돌아야 한다.
  데이터가 필요하면 합성으로 만든다.
- **실제 `.data/` 를 건드리지 않는다.** 거기에 진짜 API 키와 계정 DB 가 있다.
- 난수는 전부 시드를 고정한다. 통계 테스트가 가끔 실패하면 아무도 안 믿게 된다.

격리가 왜 이렇게 생겼는가
-------------------------
처음에는 환경변수(`PLUTUS_DATA_DIR`)를 세팅해 두면 될 거라고 봤는데
**아무 효과가 없었다.** `engine/paths.py` 는 그 변수를 읽지 않고
`LOCALAPPDATA` 만 보며, `AUTH_DB` 는 **import 시점에 상수로 확정**된다.
그래서 쿼터 테스트가 실제 `.data/auth.db` 에 행을 쓰고 있었다.

지금은 값을 확정한 **모듈들의 속성을 직접 바꾼다.** `_conn()` 이 호출
시점에 모듈 전역을 읽으므로 import 이후에 갈아 끼워도 먹는다.
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# 실제 경로를 잘못 건드리면 바로 알 수 있게 남겨 둔다
_REAL_DATA = ROOT / ".data"


@pytest.fixture(scope="session", autouse=True)
def _isolate_data_dir():
    """
    쓰기가 임시 폴더로만 가게 한다.

    `AUTH_DB` 를 값으로 들고 있는 모듈을 전부 찾아 갈아 끼운다.
    새 모듈이 같은 방식으로 import 하면 여기 추가해야 한다 —
    빠뜨리면 조용히 실제 DB 를 건드린다.
    """
    tmp = Path(tempfile.mkdtemp(prefix="plutus_test_"))
    os.environ["PLUTUS_DATA_DIR"] = str(tmp)
    os.environ["IAW_DATA_DIR"] = str(tmp)

    import engine.paths as paths
    patched = []

    def _swap(mod, attr, value):
        if hasattr(mod, attr):
            patched.append((mod, attr, getattr(mod, attr)))
            setattr(mod, attr, value)

    _swap(paths, "DATA_DIR", tmp)
    _swap(paths, "AUTH_DB", tmp / "auth.db")

    # 값을 복사해 간 모듈들 — import 이후에도 전역 조회라 갈아 끼우면 먹는다
    import engine.auth.quota as quota
    _swap(quota, "AUTH_DB", tmp / "auth.db")
    try:
        import engine.auth.session_store as ss
        _swap(ss, "AUTH_DB", tmp / "auth.db")
    except Exception:
        pass

    # 격리가 실제로 걸렸는지 확인한다. 조용히 실패하면 의미가 없다.
    assert quota.AUTH_DB != (_REAL_DATA / "auth.db"), "격리 실패"

    try:
        yield tmp
    finally:
        for mod, attr, old in reversed(patched):
            setattr(mod, attr, old)
        shutil.rmtree(tmp, ignore_errors=True)


@pytest.fixture
def rng():
    """시드 고정 난수. 통계 테스트는 재현 가능해야 한다."""
    import numpy as np
    return np.random.default_rng(20260821)


def pytest_configure(config):
    config.addinivalue_line("markers", "slow: 오래 걸리는 테스트")
    config.addinivalue_line("markers", "net: 네트워크가 필요 (기본 제외)")
