# -*- coding: utf-8 -*-
"""
외부 접근 감시자 (Tunnel Supervisor)
====================================
Quick Tunnel 은 기동 자체는 잘 된다. 쓸모없게 만드는 건 그 다음이다.

  · cloudflared 가 죽어도 아무도 모른다 → 폰에서 갑자기 안 열린다
  · 재시작할 때마다 URL 이 바뀐다 → 어제 북마크한 주소는 죽은 주소다
  · 앱이 비정상 종료하면 cloudflared 만 남아 떠돈다

그래서 감시자를 둔다. 하는 일은 셋뿐이다.

  1. 살아 있는지 본다. 죽었으면 지수 백오프로 다시 띄운다.
  2. URL 이 바뀌면 중앙 인증 서버에 곧바로 다시 등록한다.
     그래야 ``/go/<username>`` 이 **항상 살아 있는 주소**를 가리킨다.
     사용자는 바뀌는 주소 대신 이 고정 주소 하나만 기억하면 된다.
  3. 자기가 띄운 cloudflared 의 PID 를 남겨, 다음 실행 때 유령 프로세스를
     정리한다.
"""
from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from typing import Any, Dict, Optional

from engine.paths import DATA_DIR
from . import tunnel

# ── 상태 ───────────────────────────────────────────────────────
_PID_FILE = DATA_DIR / "cloudflared.pid"
_STATE_FILE = DATA_DIR / "tunnel_state.json"

_CHECK_INTERVAL = 20.0        # 살아 있는지 확인 주기(초)
_BACKOFF_START = 5.0          # 재시작 대기 시작
_BACKOFF_MAX = 300.0          # 재시작 대기 상한 (5분)

_LOCK = threading.RLock()
_THREAD: Optional[threading.Thread] = None
_STOP = threading.Event()

_SUP: Dict[str, Any] = {
    "enabled": False,
    "port": 8765,
    "restarts": 0,
    "last_restart_at": None,
    "last_error": "",
    "published_url": "",      # 중앙 서버에 등록해 둔 URL
    "publish_error": "",
    "next_retry_in": 0,
}


# ── 유령 프로세스 정리 ─────────────────────────────────────────
def _write_pid(pid: int) -> None:
    try:
        _PID_FILE.write_text(str(pid), encoding="utf-8")
    except Exception:
        pass


def _clear_pid() -> None:
    try:
        _PID_FILE.unlink(missing_ok=True)
    except Exception:
        pass


def reap_orphan() -> Dict[str, Any]:
    """
    지난 실행에서 남은 cloudflared 를 정리한다.
    앱이 강제 종료되면 atexit 훅이 돌지 않아 터널만 살아남는다.
    """
    if not _PID_FILE.exists():
        return {"reaped": False}
    try:
        pid = int(_PID_FILE.read_text(encoding="utf-8").strip())
    except Exception:
        _clear_pid()
        return {"reaped": False}

    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(pid), "/F"],
                           capture_output=True, timeout=10,
                           creationflags=getattr(subprocess,
                                                 "CREATE_NO_WINDOW", 0))
        else:
            os.kill(pid, 15)
    except Exception:
        pass
    _clear_pid()
    return {"reaped": True, "pid": pid}


# ── 중앙 서버에 URL 게시 ───────────────────────────────────────
def publish_url(url: str) -> Dict[str, Any]:
    """
    현재 터널 URL 을 중앙 인증 서버의 내 계정에 등록한다.
    중앙 서버가 설정돼 있지 않거나 로그인 상태가 아니면 조용히 건너뛴다
    — 외부 접근 자체는 URL 만으로도 되니까 실패해도 막지 않는다.
    """
    if not url:
        return {"ok": False, "error": "URL 없음"}
    try:
        from engine import auth_remote
        if not auth_remote.is_configured():
            return {"ok": False, "error": "중앙 서버 미설정", "skipped": True}
        if not auth_remote.load_session().get("token"):
            return {"ok": False, "error": "중앙 서버 미로그인", "skipped": True}
        from engine.cloud.pc_id import get_pc_label
        label = ""
        try:
            label = get_pc_label()
        except Exception:
            pass
        r = auth_remote.register_pc(public_url=url, pc_label=label)
        with _LOCK:
            if r.get("ok"):
                _SUP["published_url"] = url
                _SUP["publish_error"] = ""
            else:
                _SUP["publish_error"] = str(r.get("error") or "등록 실패")
        return r
    except Exception as e:
        with _LOCK:
            _SUP["publish_error"] = f"{type(e).__name__}: {e}"
        return {"ok": False, "error": str(e)}


def _save_state() -> None:
    try:
        st = tunnel.status()
        _STATE_FILE.write_text(json.dumps({
            "url": st.get("url", ""),
            "running": st.get("running", False),
            "published_url": _SUP["published_url"],
            "saved_at": time.time(),
        }, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def last_known() -> Dict[str, Any]:
    """지난 실행에서 마지막으로 알려진 터널 상태 (UI 안내용)."""
    try:
        return json.loads(_STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


# ── 감시 루프 ──────────────────────────────────────────────────
def _loop(port: int) -> None:
    backoff = _BACKOFF_START
    last_url = ""

    while not _STOP.is_set():
        st = tunnel.status()

        if st.get("running") and st.get("url"):
            backoff = _BACKOFF_START           # 정상이면 백오프 초기화
            with _LOCK:
                _SUP["next_retry_in"] = 0
            if st["url"] != last_url:
                last_url = st["url"]
                _remember_pid()
                publish_url(last_url)          # 주소가 바뀌면 즉시 재등록
                _save_state()

        elif st.get("running") and not st.get("url"):
            pass                               # URL 발급 대기 중 — 기다린다

        else:
            # 죽었다. 백오프 후 재시작.
            with _LOCK:
                _SUP["last_error"] = st.get("error") or "프로세스 종료"
                _SUP["next_retry_in"] = int(backoff)
            if _STOP.wait(backoff):
                break
            r = tunnel.start_quick(local_port=port)
            with _LOCK:
                _SUP["restarts"] += 1
                _SUP["last_restart_at"] = time.time()
                if not r.get("ok"):
                    _SUP["last_error"] = r.get("message") or "재시작 실패"
            last_url = ""
            backoff = min(backoff * 2, _BACKOFF_MAX)

        if _STOP.wait(_CHECK_INTERVAL):
            break

    with _LOCK:
        _SUP["enabled"] = False


def _remember_pid() -> None:
    try:
        proc = getattr(tunnel, "_PROC", None)
        if proc is not None and proc.poll() is None:
            _write_pid(proc.pid)
    except Exception:
        pass


# ── 공개 API ───────────────────────────────────────────────────
def start(port: int = 8765) -> Dict[str, Any]:
    """터널을 켜고 감시를 시작한다. 이미 돌고 있으면 그대로 둔다."""
    global _THREAD
    with _LOCK:
        if _SUP["enabled"] and _THREAD and _THREAD.is_alive():
            return {"ok": True, "message": "이미 감시 중",
                    "url": tunnel.status().get("url", "")}
        _SUP["enabled"] = True
        _SUP["port"] = port
        _SUP["last_error"] = ""

    reap_orphan()
    r = tunnel.start_quick(local_port=port)
    _STOP.clear()
    _THREAD = threading.Thread(target=_loop, args=(port,), daemon=True)
    _THREAD.start()
    return {"ok": True, "message": r.get("message", ""), "supervised": True}


def stop() -> Dict[str, Any]:
    """감시를 끄고 터널을 내린다. 중앙 서버 등록도 해제한다."""
    with _LOCK:
        _SUP["enabled"] = False
    _STOP.set()
    tunnel.stop_quick()
    _clear_pid()
    try:
        from engine import auth_remote
        if auth_remote.is_configured() and \
                auth_remote.load_session().get("token"):
            auth_remote.pc_unregister()
    except Exception:
        pass
    with _LOCK:
        _SUP["published_url"] = ""
    _save_state()
    return {"ok": True, "message": "외부 접근을 껐습니다"}


def status() -> Dict[str, Any]:
    """터널 상태 + 감시 상태를 한 번에."""
    st = tunnel.status()
    with _LOCK:
        st["supervisor"] = {
            "enabled": _SUP["enabled"],
            "restarts": _SUP["restarts"],
            "last_restart_at": _SUP["last_restart_at"],
            "last_error": _SUP["last_error"],
            "published_url": _SUP["published_url"],
            "publish_error": _SUP["publish_error"],
            "next_retry_in": _SUP["next_retry_in"],
        }
    return st
