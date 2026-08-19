"""
Cloudflare Tunnel 자동화
==========================
두 가지 모드 지원:

1) **Quick Tunnel** (계정 없이, 즉시 가능)
   - `cloudflared tunnel --url http://localhost:8765`
   - 매 실행마다 임시 URL 발급 (https://random-words.trycloudflare.com)
   - 단점: URL이 바뀜, 인증 없음 (URL을 아는 사람은 모두 접속 가능)
   - 장점: 5분 안에 동작 + 무료 + 계정 불필요

2) **Named Tunnel** (정식, 고정 URL)
   - Cloudflare 계정 + 도메인 필요
   - `cloudflared tunnel login` → `create` → `route dns` → `run`
   - 고정 URL, Cloudflare Access로 인증 추가 가능

이 모듈은 cloudflared 실행파일을 자동 감지/다운로드하고
백그라운드 프로세스로 Quick Tunnel을 시작/중지/URL 추출합니다.

URL 추출: cloudflared stdout에서 `https://*.trycloudflare.com` 정규식 매칭.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

# ── 경로 ─────────────────────────────────────────────────────────
from engine.paths import BIN_DIR as _BIN_DIR
_BIN_PATH_WIN = _BIN_DIR / "cloudflared.exe"
_BIN_PATH_NIX = _BIN_DIR / "cloudflared"

# ── Cloudflare 공식 릴리즈 (Windows 64bit) ─────────────────────
_DL_URL_WIN = ("https://github.com/cloudflare/cloudflared/releases/"
               "latest/download/cloudflared-windows-amd64.exe")
_DL_URL_LINUX = ("https://github.com/cloudflare/cloudflared/releases/"
                 "latest/download/cloudflared-linux-amd64")
_DL_URL_MAC = ("https://github.com/cloudflare/cloudflared/releases/"
               "latest/download/cloudflared-darwin-amd64.tgz")

# Quick Tunnel URL 패턴
_URL_RE = re.compile(r"https://[a-z0-9\-]+\.trycloudflare\.com",
                     re.IGNORECASE)

# ── 전역 상태 ────────────────────────────────────────────────────
_STATE: Dict[str, Any] = {
    "installed": False,
    "path": "",
    "running": False,
    "mode": "",          # "quick" | "named" | ""
    "url": "",
    "started_at": None,
    "stdout_log": [],    # 최근 30줄
    "error": "",
    "download": {
        "status": "idle",  # idle/downloading/done/error
        "progress": 0,
        "message": "",
    },
}
_LOCK = threading.RLock()
_PROC: Optional[subprocess.Popen] = None


def _is_windows() -> bool:
    return os.name == "nt"


def _bin_local() -> Path:
    return _BIN_PATH_WIN if _is_windows() else _BIN_PATH_NIX


def find_cloudflared() -> Optional[str]:
    """PATH에 cloudflared가 있거나, 우리가 설치한 곳에 있으면 경로 반환."""
    # 1) PATH
    p = shutil.which("cloudflared") or (
        shutil.which("cloudflared.exe") if _is_windows() else None)
    if p and os.path.isfile(p):
        return p
    # 2) .data/bin/
    local = _bin_local()
    if local.exists():
        return str(local)
    return None


def status() -> Dict[str, Any]:
    """현재 상태 스냅샷 (UI 폴링용)."""
    with _LOCK:
        path = find_cloudflared()
        _STATE["installed"] = bool(path)
        _STATE["path"] = path or ""
        # 프로세스 살아있는지 체크
        if _PROC is not None:
            ret = _PROC.poll()
            if ret is not None:
                _STATE["running"] = False
                if ret != 0 and not _STATE["error"]:
                    _STATE["error"] = f"cloudflared 종료 (exit {ret})"
        return {
            "installed": _STATE["installed"],
            "path": _STATE["path"],
            "running": _STATE["running"],
            "mode": _STATE["mode"],
            "url": _STATE["url"],
            "started_at": _STATE["started_at"],
            "stdout_tail": list(_STATE["stdout_log"][-12:]),
            "error": _STATE["error"],
            "download": dict(_STATE["download"]),
        }


# ── 다운로드 ─────────────────────────────────────────────────────
def install_async() -> Dict[str, Any]:
    """cloudflared를 .data/bin/ 에 다운로드 (백그라운드)."""
    with _LOCK:
        if _STATE["download"]["status"] == "downloading":
            return {"ok": False, "message": "이미 다운로드 진행 중"}
        if find_cloudflared():
            _STATE["download"] = {"status": "done", "progress": 100,
                                  "message": "이미 설치됨"}
            return {"ok": True, "message": "이미 설치됨"}
        _STATE["download"] = {"status": "downloading",
                              "progress": 0, "message": "다운로드 시작…"}
    t = threading.Thread(target=_install_worker, daemon=True)
    t.start()
    return {"ok": True, "message": "백그라운드 다운로드 시작"}


def _install_worker() -> None:
    import requests
    try:
        _BIN_DIR.mkdir(parents=True, exist_ok=True)
        url = (_DL_URL_WIN if _is_windows()
               else _DL_URL_LINUX)  # mac은 tgz, 일단 윈/리눅스만
        dest = _bin_local()
        with requests.get(url, stream=True, timeout=60) as r:
            r.raise_for_status()
            total = int(r.headers.get("Content-Length") or 0)
            done = 0
            with open(dest, "wb") as f:
                for chunk in r.iter_content(chunk_size=128 * 1024):
                    if not chunk:
                        continue
                    f.write(chunk)
                    done += len(chunk)
                    if total:
                        pct = int(done * 100 / total)
                        with _LOCK:
                            _STATE["download"]["progress"] = pct
                            _STATE["download"]["message"] = (
                                f"{done//1048576}MB/{total//1048576}MB")
        # 실행 권한 (Linux/Mac)
        if not _is_windows():
            try:
                os.chmod(dest, 0o755)
            except Exception:
                pass
        with _LOCK:
            _STATE["download"] = {
                "status": "done", "progress": 100,
                "message": f"설치 완료: {dest}"
            }
    except Exception as e:
        with _LOCK:
            _STATE["download"] = {
                "status": "error", "progress": 0,
                "message": f"다운로드 실패: {type(e).__name__}: {e}"
            }


# ── Quick Tunnel 시작/중지 ──────────────────────────────────────
def start_quick(local_port: int = 8765) -> Dict[str, Any]:
    """Quick Tunnel 시작 (계정 없이, 임시 URL)."""
    global _PROC
    with _LOCK:
        if _STATE["running"] and _PROC and _PROC.poll() is None:
            return {"ok": False,
                    "message": f"이미 실행 중 ({_STATE['url']})",
                    "url": _STATE["url"]}
        path = find_cloudflared()
        if not path:
            return {"ok": False,
                    "message": "cloudflared 미설치 — 먼저 설치하세요."}

        # 기존 프로세스 정리
        _force_stop()

        try:
            cmd = [path, "tunnel", "--url",
                   f"http://localhost:{local_port}",
                   "--no-autoupdate", "--metrics", "127.0.0.1:0"]
            kw: Dict[str, Any] = {
                "stdout": subprocess.PIPE,
                "stderr": subprocess.STDOUT,
                "text": True,
                "bufsize": 1,
            }
            if _is_windows():
                kw["creationflags"] = (
                    subprocess.CREATE_NO_WINDOW
                    if hasattr(subprocess, "CREATE_NO_WINDOW") else 0)
            _PROC = subprocess.Popen(cmd, **kw)
        except Exception as e:
            return {"ok": False,
                    "message": f"실행 실패: {type(e).__name__}: {e}"}

        _STATE["running"] = True
        _STATE["mode"] = "quick"
        _STATE["url"] = ""
        _STATE["started_at"] = time.time()
        _STATE["stdout_log"] = []
        _STATE["error"] = ""

    # 백그라운드로 stdout 모니터링 (URL 추출)
    t = threading.Thread(target=_monitor_stdout, daemon=True)
    t.start()
    return {"ok": True, "message": "Quick Tunnel 시작 — URL 발급 대기 중"}


def _monitor_stdout() -> None:
    """cloudflared stdout 읽으면서 URL 추출 + 로그 저장."""
    global _PROC
    if _PROC is None or _PROC.stdout is None:
        return
    try:
        for line in iter(_PROC.stdout.readline, ""):
            if not line:
                break
            line = line.rstrip()
            with _LOCK:
                _STATE["stdout_log"].append(line)
                # 최근 30줄만 유지
                if len(_STATE["stdout_log"]) > 30:
                    _STATE["stdout_log"] = _STATE["stdout_log"][-30:]
                # URL 패턴 매칭
                if not _STATE["url"]:
                    m = _URL_RE.search(line)
                    if m:
                        _STATE["url"] = m.group(0)
    except Exception:
        pass
    with _LOCK:
        if _PROC and _PROC.poll() is not None:
            _STATE["running"] = False


def stop_quick() -> Dict[str, Any]:
    """Quick Tunnel 중지."""
    with _LOCK:
        _force_stop()
        _STATE["running"] = False
        _STATE["url"] = ""
        _STATE["mode"] = ""
        return {"ok": True, "message": "중지됨"}


def health_check(local_port: int = 8765,
                 timeout: int = 8) -> Dict[str, Any]:
    """
    Quick Tunnel URL이 진짜 외부에서 접근 가능한지 검증.

    1) cloudflared 프로세스 살아있는지
    2) URL 발급됐는지
    3) URL을 외부로 GET → 200 응답 오는지 (서버 자신을 통해 검증)

    핸드폰에서 안 들어가질 때 진단용.
    """
    import requests
    state = status()
    out = {
        "process_alive": False,
        "url_set": False,
        "url": state.get("url", ""),
        "external_reachable": False,
        "response_code": None,
        "response_time_ms": None,
        "advice": "",
    }
    if not state.get("running"):
        out["advice"] = "cloudflared 프로세스가 실행 중이 아님 — "\
                        "'Quick Tunnel 시작' 다시 누르세요."
        return out
    out["process_alive"] = True
    if not state.get("url"):
        out["advice"] = "URL 미발급 — 시작 후 5-15초 더 대기 필요."
        return out
    out["url_set"] = True

    # URL을 실제 GET (외부 cloudflared를 통해 다시 자신에게 옴)
    import time
    t0 = time.time()
    try:
        r = requests.get(state["url"], timeout=timeout,
                         allow_redirects=True,
                         headers={"User-Agent": "Plutus-HealthCheck"})
        out["response_code"] = r.status_code
        out["response_time_ms"] = round((time.time() - t0) * 1000)
        out["external_reachable"] = r.status_code in (200, 302, 401)
        if not out["external_reachable"]:
            out["advice"] = (f"Tunnel은 살아있으나 HTTP {r.status_code} — "
                              "Cloudflare 자체 차단 또는 ISP 이슈 가능.")
        else:
            out["advice"] = "✓ Tunnel 외부 접근 가능. 핸드폰에서도 "\
                             "이 URL 그대로 사용 가능."
    except requests.Timeout:
        out["advice"] = (f"Tunnel URL이 {timeout}초 안에 응답 없음 — "
                          "cloudflared 죽었거나 외부 차단됨. "
                          "Tunnel 재시작 권장.")
    except Exception as e:
        out["advice"] = f"외부 접근 실패: {type(e).__name__}: {e}"
    return out


def restart_quick(local_port: int = 8765) -> Dict[str, Any]:
    """Tunnel 강제 재시작 — 죽었을 때 또는 URL 갱신 시."""
    stop_quick()
    import time
    time.sleep(0.5)
    return start_quick(local_port=local_port)


def _force_stop() -> None:
    global _PROC
    if _PROC is None:
        return
    try:
        if _PROC.poll() is None:
            _PROC.terminate()
            try:
                _PROC.wait(timeout=5)
            except subprocess.TimeoutExpired:
                _PROC.kill()
    except Exception:
        pass
    _PROC = None
