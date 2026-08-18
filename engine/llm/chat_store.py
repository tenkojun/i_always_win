"""
Claude 채팅 영구 저장
======================
대화는 PC에만 저장 (클라우드 동기화 안 함 — 사용자 결정).

파일: .data/chats/<user_id>/<chat_id>.json
각 파일:
{
  "id": "8자 hex",
  "user_id": 2,
  "title": "사용자 첫 질문에서 자동 추출",
  "created_at": "ISO",
  "updated_at": "ISO",
  "messages": [{"role": "user|assistant", "content": "...", "ts": "..."}]
}
"""
from __future__ import annotations

import datetime as dt
import json
import os
import secrets
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from engine.paths import CHATS_DIR as _BASE
_LOCK = threading.Lock()


def _user_dir(user_id: int) -> Path:
    d = _BASE / str(user_id)
    d.mkdir(parents=True, exist_ok=True)
    return d


def _now() -> str:
    return dt.datetime.utcnow().isoformat()


def _new_id() -> str:
    return secrets.token_hex(4)


def _path(user_id: int, chat_id: str) -> Path:
    return _user_dir(user_id) / f"{chat_id}.json"


def _safe_id(chat_id: str) -> Optional[str]:
    """8자 hex만 허용 (path traversal 방지)."""
    if not chat_id or len(chat_id) != 8:
        return None
    try:
        bytes.fromhex(chat_id)
        return chat_id
    except ValueError:
        return None


def create_chat(user_id: int, first_message: str = "") -> Dict[str, Any]:
    chat_id = _new_id()
    title = (first_message or "새 대화").strip()
    if len(title) > 40:
        title = title[:38] + "…"
    data = {
        "id": chat_id,
        "user_id": user_id,
        "title": title,
        "created_at": _now(),
        "updated_at": _now(),
        "messages": [],
    }
    with _LOCK:
        with open(_path(user_id, chat_id), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    return data


def load_chat(user_id: int, chat_id: str) -> Optional[Dict[str, Any]]:
    cid = _safe_id(chat_id)
    if not cid:
        return None
    p = _path(user_id, cid)
    if not p.exists():
        return None
    try:
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def append_message(user_id: int, chat_id: str, role: str,
                   content: str) -> Optional[Dict[str, Any]]:
    cid = _safe_id(chat_id)
    if not cid or role not in ("user", "assistant"):
        return None
    with _LOCK:
        data = load_chat(user_id, cid)
        if not data:
            return None
        data["messages"].append({
            "role": role, "content": content, "ts": _now(),
        })
        data["updated_at"] = _now()
        # 첫 user 메시지로 title 자동 갱신 (기본값일 때만)
        if data["title"] == "새 대화" and role == "user":
            data["title"] = (content[:38] + "…"
                             if len(content) > 40 else content)
        with open(_path(user_id, cid), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    return data


def list_chats(user_id: int, limit: int = 50) -> List[Dict[str, Any]]:
    """대화 목록 (updated_at 내림차순). content는 빠짐 — 사이드바용."""
    d = _user_dir(user_id)
    out = []
    for p in d.glob("*.json"):
        try:
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
            out.append({
                "id": data.get("id"),
                "title": data.get("title", "(제목 없음)"),
                "created_at": data.get("created_at"),
                "updated_at": data.get("updated_at"),
                "msg_count": len(data.get("messages", [])),
            })
        except Exception:
            continue
    out.sort(key=lambda x: x.get("updated_at", ""), reverse=True)
    return out[:limit]


def delete_chat(user_id: int, chat_id: str) -> bool:
    cid = _safe_id(chat_id)
    if not cid:
        return False
    p = _path(user_id, cid)
    if not p.exists():
        return False
    try:
        p.unlink()
        return True
    except Exception:
        return False
