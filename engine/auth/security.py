"""
패스워드 해시 + 토큰 생성
==========================
의존성 없이 Python 표준 라이브러리만 사용 (bcrypt 미설치 환경에서도 동작).

PBKDF2-HMAC-SHA256 with 240,000 iterations + 16-byte salt.
세션 토큰은 secrets.token_urlsafe(32) — 256-bit entropy.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
from typing import Tuple

_ITERATIONS = 240_000
_HASH_ALGO = "sha256"
_DIGEST_LEN = 32  # 256-bit


def _pbkdf2(password: str, salt: bytes) -> bytes:
    return hashlib.pbkdf2_hmac(
        _HASH_ALGO, password.encode("utf-8"), salt,
        _ITERATIONS, dklen=_DIGEST_LEN)


def hash_password(password: str) -> Tuple[str, str]:
    """
    PBKDF2 해시. (hex_hash, hex_salt) 반환.
    DB에 둘 다 저장.
    """
    if not password or len(password) < 4:
        raise ValueError("패스워드가 너무 짧습니다 (최소 4자).")
    salt = secrets.token_bytes(16)
    digest = _pbkdf2(password, salt)
    return digest.hex(), salt.hex()


def verify_password(password: str, hex_hash: str,
                    hex_salt: str) -> bool:
    """저장된 해시/솔트로 패스워드 검증. timing-safe 비교."""
    if not password or not hex_hash or not hex_salt:
        return False
    try:
        salt = bytes.fromhex(hex_salt)
        expected = bytes.fromhex(hex_hash)
        actual = _pbkdf2(password, salt)
        return hmac.compare_digest(expected, actual)
    except (ValueError, TypeError):
        return False


def gen_token(n_bytes: int = 32) -> str:
    """URL-safe 세션 토큰."""
    return secrets.token_urlsafe(n_bytes)
