# -*- coding: utf-8 -*-
"""
브라우저 세션 ↔ 중앙 토큰 매핑
==============================
계정은 중앙 서버(Cloudflare Workers + D1)에 있지만, **세션은 브라우저마다
따로** 있어야 한다. 이유가 있다.

이 앱의 웹서버는 외부 접근(터널)으로 열릴 수 있다. 만약 "이 PC 에
로그인된 사람"을 곧 요청자의 신원으로 삼으면, 터널 주소를 아는 사람은
누구나 주인 계정으로 들어온다. 로그인 화면이 아예 의미가 없어진다.

그래서 이렇게 나눈다.

  · **누구인가**  → 중앙 서버가 판단한다 (아이디·비밀번호·승인 여부)
  · **이 브라우저가 로그인했는가** → 이 파일이 판단한다 (쿠키)

로그인에 성공하면 중앙 토큰을 받아 여기에 보관하고, 브라우저에는 그것을
가리키는 별도의 임의 토큰을 쿠키로 준다. 중앙 토큰 자체는 브라우저로
내려보내지 않는다.
"""
from __future__ import annotations

import json
import os
import secrets
import threading
import time
from typing import Any, Dict, Optional

from engine.paths import DATA_DIR

_FILE = DATA_DIR / "browser_sessions.json"
_TTL = 30 * 24 * 60 * 60.0          # 30일
_LOCK = threading.RLock()
_CACHE: Optional[Dict[str, Any]] = None


def _load() -> Dict[str, Any]:
    global _CACHE
    if _CACHE is not None:
        return _CACHE
    try:
        _CACHE = json.loads(_FILE.read_text(encoding="utf-8"))
        if not isinstance(_CACHE, dict):
            _CACHE = {}
    except Exception:
        _CACHE = {}
    return _CACHE


def _save() -> None:
    try:
        _FILE.write_text(json.dumps(_CACHE or {}, ensure_ascii=False),
                         encoding="utf-8")
        try:
            os.chmod(_FILE, 0o600)
        except Exception:
            pass
    except Exception:
        pass


def _prune(now: float) -> None:
    d = _load()
    dead = [k for k, v in d.items()
            if not isinstance(v, dict) or v.get("expires_at", 0) < now]
    for k in dead:
        d.pop(k, None)


def create(central_token: str, user: Dict[str, Any],
           device: str = "") -> str:
    """로그인 성공 직후. 브라우저에 줄 토큰을 만들어 돌려준다."""
    now = time.time()
    browser_token = secrets.token_hex(32)
    with _LOCK:
        d = _load()
        _prune(now)
        d[browser_token] = {
            "central_token": central_token,
            "user_id": user.get("id"),
            "username": user.get("username"),
            "created_at": now,
            "expires_at": now + _TTL,
            "device": (device or "")[:80],
        }
        _save()
    return browser_token


def get(browser_token: str) -> Optional[Dict[str, Any]]:
    if not browser_token:
        return None
    now = time.time()
    with _LOCK:
        rec = _load().get(browser_token)
        if not isinstance(rec, dict):
            return None
        if rec.get("expires_at", 0) < now:
            _load().pop(browser_token, None)
            _save()
            return None
        return dict(rec)


def central_token(browser_token: str) -> Optional[str]:
    rec = get(browser_token)
    return rec.get("central_token") if rec else None


def drop(browser_token: str) -> None:
    if not browser_token:
        return
    with _LOCK:
        if _load().pop(browser_token, None) is not None:
            _save()


def drop_all() -> None:
    """모든 브라우저 세션 종료."""
    global _CACHE
    with _LOCK:
        _CACHE = {}
        _save()


def count() -> int:
    with _LOCK:
        _prune(time.time())
        return len(_load())
