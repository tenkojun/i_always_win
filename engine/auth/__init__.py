"""
Plutus 인증/계정 시스템
=====================
SQLite 기반. 가입 → 어드민 승인 → 활성. 세션 쿠키. PBKDF2 해시.

데이터 파일: .data/auth.db (사용자 PC에만 저장, chmod 0600)

스키마
------
users     : id, username, password_hash, salt, status, role,
            main_pc_id, claude_used, claude_quota_date,
            created_at, approved_at, approved_by
sessions  : token, user_id, created_at, expires_at, device_label

계정은 이 DB 에 없다 — 신원은 중앙 서버(Cloudflare Workers + D1)가
정한다. 여기 남는 것은 커뮤니티·분석 이력·보유 종목 같은 이 PC 의
데이터와, Claude 사용 쿼터처럼 로컬에서 세는 값뿐이다.
"""
from .store import (
    init_db, create_user, get_user_by_name, get_user_by_id,
    list_pending_users, list_all_users, approve_user, reject_user,
    create_session, get_session, delete_session,
    check_claude_quota, consume_claude_quota, reset_claude_quota,
    set_main_pc, get_main_pc, ADMIN_USERNAME,
)
from .security import hash_password, verify_password, gen_token

__all__ = [
    "init_db", "create_user", "get_user_by_name", "get_user_by_id",
    "list_pending_users", "list_all_users", "approve_user",
    "reject_user", "create_session", "get_session", "delete_session",
    "check_claude_quota", "consume_claude_quota", "reset_claude_quota",
    "set_main_pc", "get_main_pc",
    "hash_password", "verify_password", "gen_token",
    "ADMIN_USERNAME",
]
