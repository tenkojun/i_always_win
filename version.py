# -*- coding: utf-8 -*-
"""
Plutus — 버전 단일 소스(single source of truth).

모든 모듈·UI·리포트·EXE 메타데이터는 여기서만 버전을 읽는다.
릴리스마다 __version__ 을 올리고 CHANGELOG.md 에 항목을 추가한다.
"""
from __future__ import annotations

__version__ = "3.1.1"

APP_NAME = "Plutus"
APP_SLUG = "plutus"
APP_TAGLINE = "기관급 퀀트 분석 터미널"
APP_SUBTITLE = "Research & Analytics"
DEVELOPER = "Tenko jun - 정준화"
REPO_URL = "https://github.com/tenkojun/i_always_win"

# ── 배포된 인프라 식별자 — 이름을 바꿔도 그대로 둔다 ──────────
# 아래 둘은 **이미 배포돼 돌아가는 자원의 주소**다. 제품명을 Plutus 로
# 바꿨다고 여기까지 바꾸면 로그인이 끊기고(Worker URL) 원격 저장소가
# 사라진다(repo). 옮기고 싶으면 Cloudflare/GitHub 에서 먼저 리네임한 뒤
# 이 값을 고쳐야 한다.

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
