"""
원격 인증 HTTP 클라이언트
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

import requests


from engine.paths import DATA_DIR as _DATA_HOME, ensure_dirs as _ensure_dirs
_ensure_dirs()
_CFG_FILE = _DATA_HOME / "auth_remote.json"
_SESSION_FILE = _DATA_HOME / "session.json"


class RemoteAuthError(Exception):
    pass


# ── 설정 ──────────────────────────────────────────────────────
def get_config() -> Dict[str, Any]:
    if not _CFG_FILE.exists():
        return {}
    try:
        return json.loads(_CFG_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def configure(server_url: str) -> Dict[str, Any]:
    """중앙 서버 URL 저장 (예: https://iaw-auth.xxx.workers.dev)."""
    url = (server_url or "").rstrip("/")
    if not url.startswith("http"):
        return {"ok": False, "error": "http(s):// 로 시작해야 함"}
    # 헬스체크
    try:
        r = requests.get(url + "/health", timeout=8)
        d = r.json()
        if not d.get("ok"):
            return {"ok": False, "error": "헬스체크 실패"}
    except Exception as e:
        return {"ok": False, "error": f"연결 실패: {e}"}
    _CFG_FILE.write_text(
        json.dumps({"server_url": url}, indent=2),
        encoding="utf-8")
    try: os.chmod(_CFG_FILE, 0o600)
    except Exception: pass
    return {"ok": True, "server_url": url, "service": d.get("service")}


def is_configured() -> bool:
    return bool(get_config().get("server_url"))


def _url(path: str) -> str:
    c = get_config()
    base = c.get("server_url", "")
    if not base:
        raise RemoteAuthError("중앙 인증 서버가 설정되지 않았습니다")
    return base + path


# ── 세션 캐시 ─────────────────────────────────────────────────
def _save_session(token: str, user: Dict[str, Any]) -> None:
    _SESSION_FILE.write_text(
        json.dumps({"token": token, "user": user}, indent=2),
        encoding="utf-8")
    try: os.chmod(_SESSION_FILE, 0o600)
    except Exception: pass


def load_session() -> Dict[str, Any]:
    if not _SESSION_FILE.exists():
        return {}
    try:
        return json.loads(_SESSION_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def clear_session() -> None:
    try: _SESSION_FILE.unlink(missing_ok=True)
    except Exception: pass


def _headers(with_auth: bool = True) -> Dict[str, str]:
    h = {"Content-Type": "application/json"}
    if with_auth:
        tok = load_session().get("token")
        if tok:
            h["Authorization"] = f"Bearer {tok}"
    return h


# ── 인증 API ─────────────────────────────────────────────────
def register(username: str, password: str,
             nickname: str = "") -> Dict[str, Any]:
    r = requests.post(_url("/auth/register"),
                      json={"username": username,
                            "password": password,
                            "nickname": nickname},
                      headers=_headers(with_auth=False), timeout=15)
    return r.json()


def login(username: str, password: str) -> Dict[str, Any]:
    r = requests.post(_url("/auth/login"),
                      json={"username": username,
                            "password": password},
                      headers=_headers(with_auth=False), timeout=15)
    d = r.json()
    if d.get("ok") and d.get("token"):
        _save_session(d["token"], d.get("user", {}))
    return d


def logout() -> Dict[str, Any]:
    try:
        requests.post(_url("/auth/logout"),
                      headers=_headers(), timeout=8)
    except Exception:
        pass
    clear_session()
    return {"ok": True}


def me() -> Dict[str, Any]:
    """현재 세션 검증 + 최신 사용자 정보."""
    if not load_session().get("token"):
        return {"authenticated": False}
    try:
        r = requests.get(_url("/auth/me"),
                         headers=_headers(), timeout=8)
        return r.json()
    except Exception:
        return {"authenticated": False, "error": "네트워크"}


# ── 어드민 ──────────────────────────────────────────────────
def admin_users() -> Dict[str, Any]:
    r = requests.get(_url("/admin/users"),
                     headers=_headers(), timeout=15)
    return r.json()


def admin_approve(user_id: int) -> Dict[str, Any]:
    r = requests.post(_url("/admin/approve"),
                      json={"user_id": user_id},
                      headers=_headers(), timeout=10)
    return r.json()


def admin_reject(user_id: int) -> Dict[str, Any]:
    r = requests.post(_url("/admin/reject"),
                      json={"user_id": user_id},
                      headers=_headers(), timeout=10)
    return r.json()


# ── A6: 본인 메인 PC URL 등록 ─────────────────────────────────
def register_pc(public_url: str, pc_label: str = "") -> Dict[str, Any]:
    r = requests.post(_url("/pc/register"),
                      json={"public_url": public_url,
                            "pc_label": pc_label},
                      headers=_headers(), timeout=10)
    return r.json()


# ── 세션 · 비밀번호 ──────────────────────────────────────────
def logout_all() -> Dict[str, Any]:
    """이 계정의 모든 기기 세션을 끊는다(현재 기기 포함)."""
    try:
        r = requests.post(_url("/auth/logout_all"),
                          headers=_headers(), timeout=10)
        d = r.json()
    except Exception as e:
        return {"ok": False, "error": f"네트워크: {e}"}
    clear_session()
    return d


def change_password(old_password: str, new_password: str) -> Dict[str, Any]:
    """
    비밀번호 변경. 성공하면 서버가 **다른 기기 세션을 모두 끊는다**
    (이 기기 세션은 유지).
    """
    try:
        r = requests.post(_url("/auth/change_password"),
                          json={"old_password": old_password,
                                "new_password": new_password},
                          headers=_headers(), timeout=15)
        return r.json()
    except Exception as e:
        return {"ok": False, "error": f"네트워크: {e}"}


def sessions() -> Dict[str, Any]:
    """내 활성 세션 목록 (기기 라벨 · 발급/만료 시각)."""
    try:
        r = requests.get(_url("/auth/sessions"),
                         headers=_headers(), timeout=10)
        return r.json()
    except Exception as e:
        return {"ok": False, "error": f"네트워크: {e}"}


# ── 메인 PC 등록 상태 ────────────────────────────────────────
def pc_status() -> Dict[str, Any]:
    """내 PC 가 중앙에 등록돼 있는지 + /go/<id> 주소."""
    try:
        r = requests.get(_url("/pc/status"),
                         headers=_headers(), timeout=10)
        return r.json()
    except Exception as e:
        return {"ok": False, "error": f"네트워크: {e}"}


def pc_unregister() -> Dict[str, Any]:
    """등록 해제 — 외부 접근을 끌 때 함께 호출한다."""
    try:
        r = requests.post(_url("/pc/unregister"),
                          headers=_headers(), timeout=10)
        return r.json()
    except Exception as e:
        return {"ok": False, "error": f"네트워크: {e}"}
