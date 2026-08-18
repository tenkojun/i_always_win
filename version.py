# -*- coding: utf-8 -*-
"""
I ALWAYS WIN — 버전 단일 소스(single source of truth).

모든 모듈·UI·리포트·EXE 메타데이터는 여기서만 버전을 읽는다.
릴리스마다 __version__ 을 올리고 CHANGELOG.md 에 항목을 추가한다.
"""
from __future__ import annotations

__version__ = "2.10.0"

APP_NAME = "I ALWAYS WIN"
APP_SLUG = "i_always_win"
APP_TAGLINE = "기관급 퀀트 분석 터미널"
DEVELOPER = "Tenko jun - 정준화"
REPO_URL = "https://github.com/tenkojun/i_always_win"

# 기본 중앙 인증 서버 (Cloudflare Workers + D1).
# 별도 설정을 하지 않으면 앱은 이 서버로 로그인한다 — 배포본을 받은
# 사람이 URL 을 몰라도 바로 쓸 수 있게. 자기 서버를 쓰려면
# 설정 → 중앙 인증에서 주소를 덮어쓰면 된다.
DEFAULT_AUTH_SERVER = "https://iaw-auth.tenkojun.workers.dev"


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
        "default_auth_server": DEFAULT_AUTH_SERVER,
    }
