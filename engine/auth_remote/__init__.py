"""
원격 인증 클라이언트 — 중앙 Worker (Cloudflare D1) 호출
=====================================================
배포 시 모든 사용자가 같은 중앙 서버에 인증.
세션 토큰은 .data/session.json에 캐시.
"""
from .client import (
    configure, get_config, is_configured, using_default,
    register, login, login_raw, logout, logout_all, logout_token,
    me, me_with_token,
    load_session, clear_session,
    change_password, sessions,
    admin_users, admin_approve, admin_reject,
    register_pc, pc_status, pc_unregister,
    RemoteAuthError,
)

__all__ = [
    "configure", "get_config", "is_configured", "using_default",
    "register", "login", "login_raw", "logout", "logout_all",
    "logout_token", "me", "me_with_token",
    "load_session", "clear_session",
    "change_password", "sessions",
    "admin_users", "admin_approve", "admin_reject",
    "register_pc", "pc_status", "pc_unregister",
    "RemoteAuthError",
]
