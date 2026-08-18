"""
JIQT 인증/계정 시스템
=====================
SQLite 기반. 가입 → 어드민 승인 → 활성. 세션 쿠키. PBKDF2 해시.

데이터 파일: ~/.jiqt/auth.db (사용자 PC에만 저장, chmod 0600)

스키마
------
users     : id, username, password_hash, salt, status, role,
            main_pc_id, claude_used, claude_quota_date,
            created_at, approved_at, approved_by
sessions  : token, user_id, created_at, expires_at, device_label

초기 어드민(첫 실행 시 자동 seed): JUNHWA / WNSGHK
"""
from .store import (
    init_db, create_user, get_user_by_name, get_user_by_id,
    list_pending_users, list_all_users, approve_user, reject_user,
    create_session, get_session, delete_session,
    check_claude_quota, consume_claude_quota, reset_claude_quota,
    set_main_pc, get_main_pc, ADMIN_USERNAME, ADMIN_PASSWORD,
)
from .security import hash_password, verify_password, gen_token

__all__ = [
    "init_db", "create_user", "get_user_by_name", "get_user_by_id",
    "list_pending_users", "list_all_users", "approve_user",
    "reject_user", "create_session", "get_session", "delete_session",
    "check_claude_quota", "consume_claude_quota", "reset_claude_quota",
    "set_main_pc", "get_main_pc",
    "hash_password", "verify_password", "gen_token",
    "ADMIN_USERNAME", "ADMIN_PASSWORD",
]
