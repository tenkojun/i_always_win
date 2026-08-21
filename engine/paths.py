# -*- coding: utf-8 -*-
"""
경로 단일 관리자 (Path Registry)
================================
앱이 쓰는 **모든** 런타임 경로는 여기서만 결정한다.

배경
----
예전 JIQT 는 사용자 홈의 ``~/.jiqt`` 에 인증 DB·API 키·채팅·
cloudflared 바이너리를 흩어 놓았다. 프로그램 폴더 밖에 상태가
있으면 백업·이전·삭제가 전부 반쪽이 된다.

원칙
----
- 모든 상태는 **앱 폴더 안 ``.data/``** 한 곳에 모은다.
- 앱 폴더가 쓰기 불가(예: Program Files 설치)면
  ``%LOCALAPPDATA%/i_always_win`` 로 자동 강등한다.
- 기존 ``~/.jiqt`` 가 있으면 **첫 실행 시 자동 이전**한다.
- ``.data/`` 전체는 .gitignore 대상 — 키가 저장소로 새지 않는다.
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

__all__ = [
    "APP_ROOT", "DATA_DIR", "BIN_DIR", "CHATS_DIR", "CACHE_DIR",
    "REPORTS_DIR", "KEYS_FILE", "AUTH_DB", "PC_ID_FILE",
    "ensure_dirs", "migrate_legacy",
]

_LEGACY_DIR = Path(os.path.expanduser("~")) / ".jiqt"


def _app_root() -> Path:
    """PyInstaller 로 얼렸으면 exe 옆, 아니면 저장소 루트."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def _writable(p: Path) -> bool:
    try:
        p.mkdir(parents=True, exist_ok=True)
        probe = p / ".write_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True
    except Exception:
        return False


def _resolve_data_dir() -> Path:
    # 명시적으로 지정하면 그걸 쓴다. **기본 동작은 바뀌지 않는다** —
    # 변수를 안 주면 예전과 똑같이 앱 폴더 옆 .data 다.
    #
    # 왜 넣었나: 테스트가 실제 .data/auth.db 에 쓰고 있었다. conftest 가
    # 환경변수를 세팅해 두고 격리됐다고 믿었는데 아무도 그 변수를 읽지
    # 않았다. 지정할 방법 자체가 없으면 격리는 언제나 우회로가 된다.
    # (데이터를 다른 드라이브에 두고 싶은 경우에도 쓸 수 있다.)
    override = (os.environ.get("PLUTUS_DATA_DIR")
                or os.environ.get("IAW_DATA_DIR") or "").strip()
    if override:
        p = Path(override).expanduser()
        if _writable(p):
            return p
        # 지정했는데 못 쓰면 조용히 다른 곳에 쓰지 않는다 — 어디에
        # 저장됐는지 모르는 상태가 제일 나쁘다.
        print(f"[!] PLUTUS_DATA_DIR 에 쓸 수 없습니다: {p} — 기본 위치를 씁니다.")

    primary = _app_root() / ".data"
    if _writable(primary):
        return primary
    fallback = Path(
        os.environ.get("LOCALAPPDATA")
        or os.path.expanduser("~/.local/share")
    ) / "i_always_win"
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback


APP_ROOT: Path = _app_root()
DATA_DIR: Path = _resolve_data_dir()

BIN_DIR: Path = DATA_DIR / "bin"
CHATS_DIR: Path = DATA_DIR / "chats"
CACHE_DIR: Path = DATA_DIR / "cache"
REPORTS_DIR: Path = APP_ROOT / "webapp" / "reports"

KEYS_FILE: Path = DATA_DIR / "keys.json"
AUTH_DB: Path = DATA_DIR / "auth.db"
PC_ID_FILE: Path = DATA_DIR / "pc_id"


def ensure_dirs() -> None:
    for d in (DATA_DIR, BIN_DIR, CHATS_DIR, CACHE_DIR):
        try:
            d.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass


# ── 레거시 이전 ────────────────────────────────────────────────
_MIGRATE_MAP = {
    "keys.json": "keys.json",
    "auth.db":   "auth.db",
    "pc_id":     "pc_id",
    "chats":     "chats",
    "bin":       "bin",
}
_MIGRATED_FLAG = ".migrated_from_jiqt"


def migrate_legacy() -> list[str]:
    """
    ``~/.jiqt`` 내용을 ``.data/`` 로 한 번만 옮긴다.
    원본은 지우지 않는다(사용자가 직접 확인 후 삭제하도록).
    옮긴 항목 이름 리스트를 반환.
    """
    ensure_dirs()
    flag = DATA_DIR / _MIGRATED_FLAG
    if flag.exists() or not _LEGACY_DIR.is_dir():
        return []

    moved: list[str] = []
    for src_name, dst_name in _MIGRATE_MAP.items():
        src = _LEGACY_DIR / src_name
        dst = DATA_DIR / dst_name
        if not src.exists():
            continue
        # 디렉터리는 ensure_dirs() 가 이미 빈 껍데기를 만들어 두므로
        # "존재하면 건너뛰기"가 아니라 내용을 합쳐야 한다.
        if dst.exists() and not src.is_dir():
            continue
        try:
            if src.is_dir():
                shutil.copytree(src, dst, dirs_exist_ok=True)
            else:
                shutil.copy2(src, dst)
                try:
                    os.chmod(dst, 0o600)
                except Exception:
                    pass
            moved.append(dst_name)
        except Exception:
            pass

    try:
        flag.write_text("\n".join(moved), encoding="utf-8")
    except Exception:
        pass
    return moved
