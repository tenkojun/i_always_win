"""
QR 일회용 로그인 토큰
=======================
PC에서 인증된 사용자가 QR 발급 → 핸드폰이 그 URL을 열면 즉시 세션 생성.

설계 원칙
---------
- **메모리에만 보관** (서버 재시작 시 무효 — 5분 TTL이므로 OK)
- **1회 사용 후 소멸**
- **5분 TTL**
- **발급 시점 user_id에 묶임** — 다른 계정 도용 불가

흐름:
  1. PC: POST /api/auth/qr_token  → {token, expires_in}
  2. QR 코드: <tunnel-url>/qr_login?token=<token>
  3. 핸드폰: 위 URL 접속 → 서버가 토큰 검증 → 세션 쿠키 발급 → / 로 redirect
"""
from __future__ import annotations

import datetime as dt
import secrets
import threading
from typing import Any, Dict, Optional

_LOCK = threading.Lock()
_TOKENS: Dict[str, Dict[str, Any]] = {}
_TTL_SECONDS = 300  # 5분


def _now() -> dt.datetime:
    return dt.datetime.utcnow()


def issue(user_id: int) -> Dict[str, Any]:
    """일회용 QR 토큰 발급. 동일 user의 기존 미사용 토큰은 자동 무효."""
    token = secrets.token_urlsafe(24)  # 32자 URL-safe
    exp = _now() + dt.timedelta(seconds=_TTL_SECONDS)
    with _LOCK:
        # 동일 user의 미사용 기존 토큰 제거 (한 번에 하나만 유효)
        stale = [k for k, v in _TOKENS.items()
                 if v.get("user_id") == user_id]
        for k in stale:
            _TOKENS.pop(k, None)
        # 만료 토큰 청소 (가벼움)
        _prune_expired()
        _TOKENS[token] = {
            "user_id": user_id,
            "created_at": _now().isoformat(),
            "expires_at": exp.isoformat(),
            "used": False,
        }
    return {
        "token": token,
        "expires_in": _TTL_SECONDS,
        "expires_at": exp.isoformat(),
    }


def consume(token: str) -> Optional[int]:
    """
    토큰 검증 + 즉시 소멸. 성공 시 user_id 반환, 실패 시 None.
    """
    if not token or len(token) < 20:
        return None
    with _LOCK:
        rec = _TOKENS.pop(token, None)
        if not rec:
            return None
        if rec.get("used"):
            return None
        try:
            exp = dt.datetime.fromisoformat(rec["expires_at"])
            if _now() > exp:
                return None
        except Exception:
            return None
        return rec.get("user_id")


def _prune_expired() -> None:
    """만료 토큰 제거 (LOCK 안에서 호출)."""
    now = _now()
    dead = []
    for tok, rec in _TOKENS.items():
        try:
            exp = dt.datetime.fromisoformat(rec["expires_at"])
            if now > exp:
                dead.append(tok)
        except Exception:
            dead.append(tok)
    for t in dead:
        _TOKENS.pop(t, None)
