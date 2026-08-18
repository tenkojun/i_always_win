"""
이 PC의 고유 식별자
====================
첫 실행 시 random uuid 생성 → .data/pc_id 에 영구 보관.
다음부터는 동일 ID 재사용. 사용자 계정의 main_pc_id와 매칭됨.

PC 라벨(사람이 읽는)도 같이 생성: "Ryzen-5-PC", "Mac-Studio" 등.
"""
from __future__ import annotations

import os
import platform
import secrets
import socket
from pathlib import Path

from engine.paths import PC_ID_FILE as _ID_PATH


def get_pc_id() -> str:
    """이 PC의 고유 ID. 없으면 생성."""
    _ID_PATH.parent.mkdir(parents=True, exist_ok=True)
    if _ID_PATH.exists():
        try:
            v = _ID_PATH.read_text(encoding="utf-8").strip()
            if v and len(v) >= 8:
                return v
        except Exception:
            pass
    new_id = secrets.token_hex(8)  # 16 chars
    try:
        _ID_PATH.write_text(new_id, encoding="utf-8")
        try:
            os.chmod(_ID_PATH, 0o600)
        except Exception:
            pass
    except Exception:
        pass
    return new_id


def get_pc_label() -> str:
    """사람이 읽는 PC 라벨 (hostname 기반)."""
    try:
        host = socket.gethostname() or "PC"
    except Exception:
        host = "PC"
    sysname = platform.system() or ""
    return f"{host} ({sysname})" if sysname else host
