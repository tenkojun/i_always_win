# -*- coding: utf-8 -*-
"""
요청 인증 미들웨어
==================
역할이 둘로 나뉜다.

  · **누구인가**  → 중앙 서버(Cloudflare Workers + D1)가 판단한다.
    아이디·비밀번호·승인 여부는 전부 거기 있다. 이 PC 에는 계정이 없다.
  · **이 브라우저가 로그인했는가** → 쿠키로 판단한다.

둘을 합치면 안 된다. 이 웹서버는 외부 접근(터널)으로 열릴 수 있어서,
"이 PC 에 로그인된 사람"을 곧 요청자의 신원으로 삼으면 터널 주소를 아는
사람이 전부 주인 계정으로 들어온다. 그래서 브라우저마다 별도 쿠키를
발급하고(``engine.auth.session_store``), 그 쿠키가 가리키는 중앙 토큰을
중앙 서버에 확인한다.

네트워크가 끊겼을 때
--------------------
중앙 서버가 **명시적으로 거부**하면 즉시 세션을 버린다.
반면 그냥 **닿지 않는 것**(와이파이 끊김, 일시 장애)은 거부가 아니다.
이때는 마지막으로 확인된 신원을 유예 기간(기본 24시간) 동안 인정한다.
분석 한 번 돌리는 중에 와이파이가 끊겼다고 앱이 잠기면 곤란하다.
"""
from __future__ import annotations

import threading
import time
from functools import wraps
from typing import Any, Callable, Dict, Optional

from flask import g, jsonify, request

from . import session_store

COOKIE_NAME = "iaw_session"
COOKIE_MAX_AGE = 30 * 24 * 60 * 60      # 30일

_VERIFY_TTL = 60.0                      # 확인 결과 재사용 시간
_OFFLINE_GRACE = 24 * 60 * 60.0         # 서버에 닿지 않을 때 인정 기간

_LOCK = threading.RLock()
# 중앙 토큰별 확인 결과. {central_token: {user, checked_at, verified_at}}
_VERIFIED: Dict[str, Dict[str, Any]] = {}


# ── 쿠키 ──────────────────────────────────────────────────────
def _is_secure_request() -> bool:
    """HTTPS 또는 프록시(cloudflared) 헤더로 추정."""
    if request.is_secure:
        return True
    return request.headers.get("X-Forwarded-Proto", "").lower() == "https"


def set_session_cookie(response, token: str) -> None:
    # 터널(HTTPS) 환경에선 SameSite=None + Secure 여야 교차 사이트 요청에
    # 쿠키가 실린다.
    https = _is_secure_request()
    response.set_cookie(
        COOKIE_NAME, token,
        max_age=COOKIE_MAX_AGE,
        httponly=True,
        secure=https,
        samesite=("None" if https else "Lax"),
        path="/",
    )


def clear_session_cookie(response) -> None:
    response.delete_cookie(COOKIE_NAME, path="/")


# ── 중앙 확인 ─────────────────────────────────────────────────
def invalidate(central_token: Optional[str] = None) -> None:
    """로그인/로그아웃 직후처럼 즉시 다시 확인해야 할 때."""
    with _LOCK:
        if central_token:
            _VERIFIED.pop(central_token, None)
        else:
            _VERIFIED.clear()


def _verify(central_token: str) -> Optional[Dict[str, Any]]:
    """중앙 서버에 토큰 주인을 묻는다. 결과는 짧게 캐시."""
    now = time.time()
    with _LOCK:
        rec = _VERIFIED.get(central_token)
        if rec and (now - rec["checked_at"]) < _VERIFY_TTL:
            return rec["user"]

    from engine import auth_remote
    try:
        r = auth_remote.me_with_token(central_token)
    except Exception as e:
        r = {"authenticated": False, "error": str(e)}

    if r.get("authenticated"):
        user = dict(r.get("user") or {})
        with _LOCK:
            _VERIFIED[central_token] = {
                "user": user, "checked_at": now, "verified_at": now,
            }
        return user

    # 닿지 않은 것뿐이면 유예 기간 동안 마지막 신원을 인정한다.
    if r.get("error"):
        with _LOCK:
            rec = _VERIFIED.get(central_token)
            if rec:
                rec["checked_at"] = now
                if (now - rec["verified_at"]) < _OFFLINE_GRACE:
                    return rec["user"]
        return None

    # 서버가 '아니다' 라고 답했다 — 즉시 버린다.
    with _LOCK:
        _VERIFIED.pop(central_token, None)
    return None


# ── Flask 훅 ──────────────────────────────────────────────────
def attach_user() -> None:
    """before_request — 쿠키 → 중앙 토큰 → 사용자."""
    g.user = None
    g.session = None

    browser_token = request.cookies.get(COOKIE_NAME)
    if not browser_token:
        return
    central = session_store.central_token(browser_token)
    if not central:
        return

    user = _verify(central)
    if not user:
        return
    if user.get("status") and user.get("status") != "active":
        return

    g.user = user
    g.session = {"user_id": user.get("id"),
                 "browser_token": browser_token,
                 "central_token": central}


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
