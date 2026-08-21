# -*- coding: utf-8 -*-
"""
테스트 공통 설정.

원칙
----
- **네트워크를 타지 않는다.** 시세 API 가 죽어도 테스트는 돌아야 한다.
  데이터가 필요하면 합성으로 만든다.
- **`.data/` 를 건드리지 않는다.** 실제 키·계정 DB 가 거기 있다.
  테스트용 임시 디렉토리로 격리한다.
- 난수는 전부 시드를 고정한다. 통계 테스트가 가끔 실패하면
  아무도 안 믿게 된다.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture(scope="session", autouse=True)
def _isolate_data_dir():
    """
    실행 중 만들어지는 것들을 임시 폴더로 보낸다.

    이걸 안 하면 테스트가 개발자의 실제 `.data/auth.db` 를 건드린다.
    등급 테스트가 진짜 계정 등급을 바꿔 놓는 사고가 난다.
    """
    tmp = tempfile.mkdtemp(prefix="plutus_test_")
    os.environ["PLUTUS_DATA_DIR"] = tmp
    os.environ["IAW_DATA_DIR"] = tmp
    yield tmp


@pytest.fixture
def rng():
    """시드 고정 난수. 통계 테스트는 재현 가능해야 한다."""
    import numpy as np
    return np.random.default_rng(20260821)


def pytest_configure(config):
    config.addinivalue_line("markers", "slow: 오래 걸리는 테스트")
    config.addinivalue_line("markers", "net: 네트워크가 필요 (기본 제외)")
