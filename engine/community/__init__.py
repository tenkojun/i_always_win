"""
주식 커뮤니티 — 공지/글/댓글 (C4)
==================================
- posts: 어드민 공지(pinned) 또는 사용자 글
- comments: 글에 대한 댓글
- views: 상세 조회 시 +1

스키마는 auth.db(~/.jiqt/auth.db)에 함께 저장 (단일 DB).
"""
from .store import (
    init_community_db,
    list_posts, get_post, create_post, delete_post,
    list_comments, create_comment, delete_comment,
)

__all__ = [
    "init_community_db",
    "list_posts", "get_post", "create_post", "delete_post",
    "list_comments", "create_comment", "delete_comment",
]
