# -*- coding: utf-8 -*-
"""
I ALWAYS WIN — 버전 단일 소스(single source of truth).

모든 모듈·UI·리포트·EXE 메타데이터는 여기서만 버전을 읽는다.
릴리스마다 __version__ 을 올리고 CHANGELOG.md 에 항목을 추가한다.
"""
from __future__ import annotations

__version__ = "2.5.0"

APP_NAME = "I ALWAYS WIN"
APP_SLUG = "i_always_win"
APP_TAGLINE = "기관급 퀀트 분석 터미널"
DEVELOPER = "Tenko jun - 정준화"
REPO_URL = "https://github.com/tenkojun/i_always_win"


def version_tuple() -> tuple[int, ...]:
    """'2.0.0' -> (2, 0, 0). PyInstaller 버전 리소스용."""
    return tuple(int(p) for p in __version__.split("."))


def build_info() -> dict:
    """설정 화면 / API 가 그대로 뿌릴 수 있는 메타데이터."""
    return {
        "app": APP_NAME,
        "slug": APP_SLUG,
        "tagline": APP_TAGLINE,
        "version": __version__,
        "developer": DEVELOPER,
        "repo": REPO_URL,
    }
