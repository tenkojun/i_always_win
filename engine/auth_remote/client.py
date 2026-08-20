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
# 저장된 설정이 없으면 앱에 내장된 기본 서버를 쓴다.
# 배포본을 받은 사람이 주소를 몰라도 바로 로그인할 수 있어야 하고,
# 자기 서버를 쓰고 싶으면 설정에서 덮어쓰면 된다.
def _default_server() -> str:
    try:
        from version import DEFAULT_AUTH_SERVER
        return (DEFAULT_AUTH_SERVER or "").rstrip("/")
    except Exception:
        return ""


def _saved_config() -> Dict[str, Any]:
    """파일에 실제로 저장된 값만. 기본값은 섞지 않는다."""
    if not _CFG_FILE.exists():
        return {}
    try:
        return json.loads(_CFG_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def get_config() -> Dict[str, Any]:
    """
    실제로 쓰이는 설정. 저장된 값이 없으면 기본 서버로 채운다.
    ``is_default`` 로 어느 쪽인지 구분할 수 있다.
    """
    saved = _saved_config()
    url = (saved.get("server_url") or "").rstrip("/")
    d = _default_server()
    if url:
        # 저장값이 기본값과 같으면 굳이 '커스텀'이라 부르지 않는다
        return {"server_url": url, "is_default": (url == d)}
    return {"server_url": d, "is_default": True} if d else {}


def using_default() -> bool:
    return bool(get_config().get("is_default"))


def configure(server_url: str) -> Dict[str, Any]:
    """
    중앙 서버 URL 저장. 빈 값을 주면 저장분을 지우고 기본 서버로 되돌린다.
    """
    url = (server_url or "").strip().rstrip("/")

    if not url:
        try:
            _CFG_FILE.unlink(missing_ok=True)
        except Exception:
            pass
        cfg = get_config()
        return {"ok": True, "server_url": cfg.get("server_url", ""),
                "is_default": True, "message": "기본 서버로 되돌렸습니다"}

    if not url.startswith("http"):
        return {"ok": False, "error": "http(s):// 로 시작해야 함"}
    # 헬스체크 — 살아 있는 서버만 저장한다
    try:
        r = requests.get(url + "/health", timeout=8)
        d = r.json()
        if not d.get("ok"):
            return {"ok": False, "error": "헬스체크 실패"}
    except Exception as e:
        return {"ok": False, "error": f"연결 실패: {e}"}

    if url == _default_server():
        # 기본값과 같으면 굳이 파일로 고정하지 않는다
        try:
            _CFG_FILE.unlink(missing_ok=True)
        except Exception:
            pass
        return {"ok": True, "server_url": url, "is_default": True,
                "service": d.get("service")}

    _CFG_FILE.write_text(
        json.dumps({"server_url": url}, indent=2),
        encoding="utf-8")
    try: os.chmod(_CFG_FILE, 0o600)
    except Exception: pass
    return {"ok": True, "server_url": url, "is_default": False,
            "service": d.get("service")}


def is_configured() -> bool:
    """기본 서버가 내장돼 있으므로 보통 항상 True."""
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


def admin_set_tier(user_id: int, tier: str) -> Dict[str, Any]:
    """
    회원 등급 변경. **중앙 서버가 판정한다.**

    등급은 v4.0.0 부터 중앙 D1 에만 있다. 로컬 `.data/auth.db` 에 두던
    시절에는 (1) 다른 PC 로 옮기면 등급이 사라지고 (2) 그 파일을 직접
    고치면 누구나 플래티넘이 됐다. 서버가 모르는 값이라 막을 수 없었다.
    """
    r = requests.post(_url("/admin/set_tier"),
                      json={"user_id": int(user_id), "tier": tier},
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


# ── 임의 토큰으로 확인 (브라우저별 세션 검증용) ──────────────
def me_with_token(token: str) -> Dict[str, Any]:
    """
    특정 세션 토큰의 주인을 중앙 서버에 묻는다.

    이 PC 에 저장된 세션이 아니라 **브라우저마다 다른 토큰**을 검증해야
    하므로 별도 함수가 필요하다. 네트워크 실패는 ``error`` 로 구분해서
    돌려준다 — '거부'와 '닿지 않음'은 다르게 취급해야 한다.
    """
    if not token:
        return {"authenticated": False}
    try:
        r = requests.get(_url("/auth/me"), timeout=8,
                         headers={"Content-Type": "application/json",
                                  "Authorization": f"Bearer {token}"})
        return r.json()
    except Exception as e:
        return {"authenticated": False, "error": f"네트워크: {e}"}


def login_raw(username: str, password: str) -> Dict[str, Any]:
    """
    로그인하되 이 PC 의 세션 파일을 건드리지 않는다.
    브라우저별 세션을 만들 때 쓴다(토큰은 호출자가 보관).
    """
    try:
        r = requests.post(_url("/auth/login"),
                          json={"username": username, "password": password},
                          headers=_headers(with_auth=False), timeout=15)
        return r.json()
    except Exception as e:
        return {"ok": False, "error": f"네트워크: {e}"}


def logout_token(token: str) -> Dict[str, Any]:
    """특정 토큰만 중앙 서버에서 폐기."""
    if not token:
        return {"ok": True}
    try:
        r = requests.post(_url("/auth/logout"), timeout=8,
                          headers={"Content-Type": "application/json",
                                   "Authorization": f"Bearer {token}"})
        return r.json()
    except Exception as e:
        return {"ok": False, "error": f"네트워크: {e}"}
