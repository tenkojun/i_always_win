"""
Flask 인증 미들웨어
====================
세션 쿠키(jiqt_session) → users 행을 g.user에 attach.
보호된 엔드포인트는 @require_auth / @require_admin 데코레이터.

쿠키 속성: HttpOnly, SameSite=Lax, Path=/
HTTPS 환경(클라우드 tunnel)에서는 Secure 플래그도 자동 추가.
"""
from __future__ import annotations

from functools import wraps
from typing import Any, Callable, Optional

from flask import g, jsonify, request

from .store import get_session, get_user_by_id

COOKIE_NAME = "jiqt_session"
COOKIE_MAX_AGE = 60 * 60 * 24 * 30   # 30일


def _is_secure_request() -> bool:
    """HTTPS 또는 Cloudflare/proxy 헤더로 추정."""
    if request.is_secure:
        return True
    if request.headers.get("X-Forwarded-Proto", "").lower() == "https":
        return True
    return False


def set_session_cookie(response, token: str) -> None:
    # HTTPS(Tunnel) 환경에선 SameSite=None + Secure로 cross-site 허용.
    # 그래야 trycloudflare.com에서 fetch가 쿠키 전송 가능.
    is_https = _is_secure_request()
    response.set_cookie(
        COOKIE_NAME, token,
        max_age=COOKIE_MAX_AGE,
        httponly=True,
        secure=is_https,
        samesite=("None" if is_https else "Lax"),
        path="/",
    )


def clear_session_cookie(response) -> None:
    response.delete_cookie(COOKIE_NAME, path="/")


def attach_user() -> None:
    """before_request 훅 — 쿠키→g.user."""
    g.user = None
    g.session = None
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return
    sess = get_session(token)
    if not sess:
        return
    user = get_user_by_id(sess["user_id"])
    if not user or user.get("status") != "active":
        return
    g.user = user
    g.session = sess


def require_auth(fn: Callable) -> Callable:
    """로그인 + 활성 사용자만 통과."""
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not getattr(g, "user", None):
            return jsonify({"error": "로그인이 필요합니다.",
                            "code": "AUTH_REQUIRED"}), 401
        return fn(*args, **kwargs)
    return wrapper


def require_admin(fn: Callable) -> Callable:
    """어드민 권한 필요."""
    @wraps(fn)
    def wrapper(*args, **kwargs):
        u = getattr(g, "user", None)
        if not u:
            return jsonify({"error": "로그인이 필요합니다.",
                            "code": "AUTH_REQUIRED"}), 401
        if u.get("role") != "admin":
            return jsonify({"error": "어드민 권한이 필요합니다.",
                            "code": "FORBIDDEN"}), 403
        return fn(*args, **kwargs)
    return wrapper
