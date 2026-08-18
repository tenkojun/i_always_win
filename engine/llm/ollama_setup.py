"""
Ollama 설치·관리
=================
- is_ollama_installed() : `ollama --version`
- is_ollama_running()   : http://localhost:11434/api/version ping
- list_installed_models(): /api/tags
- install_ollama_windows(): OllamaSetup.exe 자동 다운로드+실행
- pull_model(name)       : /api/pull NDJSON 스트림 → 진행률 콜백
- pull_status            : 전역 상태 dict (서버에서 폴링)

설치 진행 상태는 메모리 dict로 관리 — 서버 재시작 시 초기화됨.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import threading
import time
from typing import Any, Callable, Dict, List, Optional

import requests

OLLAMA_HOST = "http://localhost:11434"
OLLAMA_WIN_INSTALLER = "https://ollama.com/download/OllamaSetup.exe"

# 전역 작업 상태 (서버가 폴링)
_TASKS: Dict[str, Dict[str, Any]] = {
    "install_ollama": {"status": "idle", "progress": 0, "message": ""},
    "pull_model":     {"status": "idle", "progress": 0, "message": "",
                       "model": "", "size_total": 0, "size_done": 0},
}
_LOCK = threading.Lock()


def _set_task(task: str, **kv) -> None:
    with _LOCK:
        _TASKS[task].update(kv)


def get_task_status(task: str) -> Dict[str, Any]:
    with _LOCK:
        return dict(_TASKS.get(task, {}))


def get_all_task_status() -> Dict[str, Dict[str, Any]]:
    with _LOCK:
        return {k: dict(v) for k, v in _TASKS.items()}


# ── 상태 확인 ─────────────────────────────────────────────────────
def is_ollama_running(timeout: float = 1.5) -> bool:
    """Ollama 서비스(11434 포트) 응답 여부 — 가장 신뢰할 수 있는 신호."""
    try:
        r = requests.get(f"{OLLAMA_HOST}/api/version", timeout=timeout)
        return r.status_code == 200
    except Exception:
        return False


def is_ollama_installed() -> bool:
    """
    설치 여부 판단 — API 응답이 우선(ground truth).

    PATH에 'ollama'가 없더라도 서비스가 응답하면 설치된 것으로 간주한다.
    이유: Python 프로세스가 Ollama 설치 전에 시작된 경우, 환경변수 PATH가
    캐싱되어 새 설치를 못 봄. 하지만 서비스가 11434에 응답하면 실질적으로
    설치 완료 상태다.
    """
    if is_ollama_running():
        return True
    return shutil.which("ollama") is not None


def ollama_version() -> Optional[str]:
    try:
        r = requests.get(f"{OLLAMA_HOST}/api/version", timeout=1.5)
        if r.status_code == 200:
            return (r.json() or {}).get("version", "")
    except Exception:
        pass
    return None


def list_installed_models() -> List[Dict[str, Any]]:
    """현재 ollama에 설치된 모델 목록."""
    try:
        r = requests.get(f"{OLLAMA_HOST}/api/tags", timeout=3)
        if r.status_code == 200:
            return (r.json() or {}).get("models", [])
    except Exception:
        pass
    return []


def is_model_installed(model: str) -> bool:
    """지정 모델이 이미 설치되어 있는지(tag 일치)."""
    target = model.lower()
    for m in list_installed_models():
        name = (m.get("name") or "").lower()
        if name == target or name.startswith(target + ":"):
            return True
    return False


# ── Ollama 설치 (Windows) ────────────────────────────────────────
def install_ollama_windows_async() -> Dict[str, Any]:
    """
    OllamaSetup.exe를 다운로드 후 silent 설치 시도.
    백그라운드 스레드로 실행 — 즉시 status 반환.
    """
    state = get_task_status("install_ollama")
    if state.get("status") == "running":
        return {"ok": False, "message": "이미 진행 중"}
    if is_ollama_installed():
        _set_task("install_ollama", status="done", progress=100,
                  message="이미 설치됨")
        return {"ok": True, "message": "이미 설치됨"}

    _set_task("install_ollama", status="running", progress=0,
              message="설치 파일 다운로드 시작…")
    t = threading.Thread(target=_install_ollama_worker, daemon=True)
    t.start()
    return {"ok": True, "message": "백그라운드 설치 시작"}


def _install_ollama_worker() -> None:
    import os
    import tempfile
    try:
        tmp = tempfile.gettempdir()
        path = os.path.join(tmp, "OllamaSetup.exe")
        # 스트리밍 다운로드 + 진행률
        with requests.get(OLLAMA_WIN_INSTALLER, stream=True,
                          timeout=60) as r:
            r.raise_for_status()
            total = int(r.headers.get("Content-Length") or 0)
            done = 0
            with open(path, "wb") as f:
                for chunk in r.iter_content(chunk_size=128 * 1024):
                    if not chunk:
                        continue
                    f.write(chunk)
                    done += len(chunk)
                    if total:
                        pct = int(done * 60 / total)  # 0~60%
                        _set_task("install_ollama", progress=pct,
                                  message=f"다운로드 {done//1048576}MB"
                                          f"/{total//1048576}MB")
        _set_task("install_ollama", progress=65,
                  message="설치 실행 중 (UAC 창이 뜨면 승인하세요)…")
        # /S = silent, 일부 빌드는 미지원 — 실패 시 GUI로 떨어짐
        try:
            subprocess.run([path, "/S"], timeout=180, check=False)
        except Exception:
            # silent 실패 시 GUI 실행 (사용자가 클릭)
            subprocess.Popen([path])
        _set_task("install_ollama", progress=90,
                  message="설치 후 서비스 부팅 대기…")
        # 최대 30초 ollama가 PATH에 잡힐 때까지 대기
        for _ in range(30):
            if is_ollama_installed():
                break
            time.sleep(1)
        if is_ollama_installed():
            _set_task("install_ollama", status="done", progress=100,
                      message="Ollama 설치 완료")
        else:
            _set_task("install_ollama", status="error", progress=0,
                      message="설치 후에도 PATH에 잡히지 않음 — "
                              "새 셸을 열고 'ollama' 명령으로 확인하세요.")
    except Exception as e:
        _set_task("install_ollama", status="error", progress=0,
                  message=f"설치 실패: {type(e).__name__}: {e}")


# ── 모델 다운로드 (pull) ─────────────────────────────────────────
def pull_model_async(model: str) -> Dict[str, Any]:
    """모델 pull을 백그라운드로 시작 — 즉시 status 반환."""
    state = get_task_status("pull_model")
    if state.get("status") == "running":
        return {"ok": False, "message": f"이미 진행 중: {state.get('model')}"}
    if not is_ollama_running():
        return {"ok": False, "message": "Ollama 서비스가 실행되지 않음"}
    if is_model_installed(model):
        _set_task("pull_model", status="done", progress=100, model=model,
                  message="이미 설치됨")
        return {"ok": True, "message": "이미 설치됨"}

    _set_task("pull_model", status="running", progress=0, model=model,
              size_total=0, size_done=0,
              message=f"{model} 다운로드 시작…")
    t = threading.Thread(target=_pull_model_worker, args=(model,),
                         daemon=True)
    t.start()
    return {"ok": True, "message": "백그라운드 다운로드 시작"}


def _pull_model_worker(model: str) -> None:
    """Ollama /api/pull은 NDJSON 스트림으로 진행률을 흘려보낸다."""
    try:
        with requests.post(f"{OLLAMA_HOST}/api/pull",
                           json={"name": model, "stream": True},
                           stream=True, timeout=None) as r:
            r.raise_for_status()
            for line in r.iter_lines():
                if not line:
                    continue
                try:
                    ev = json.loads(line.decode("utf-8"))
                except Exception:
                    continue
                # ev: {status, digest, total, completed}
                total = ev.get("total") or 0
                done = ev.get("completed") or 0
                status = ev.get("status", "")
                if total and done:
                    pct = max(0, min(99, int(done * 100 / total)))
                    _set_task("pull_model", progress=pct,
                              size_total=total, size_done=done,
                              message=f"{status}: "
                                      f"{done//1048576}MB/{total//1048576}MB")
                elif status:
                    _set_task("pull_model", message=status)
                if status == "success":
                    break
        if is_model_installed(model):
            _set_task("pull_model", status="done", progress=100,
                      message="다운로드 완료")
        else:
            _set_task("pull_model", status="error",
                      message="다운로드 후 모델이 등록되지 않음")
    except Exception as e:
        _set_task("pull_model", status="error",
                  message=f"다운로드 실패: {type(e).__name__}: {e}")


# ── 통합 상태 ─────────────────────────────────────────────────────
def full_status() -> Dict[str, Any]:
    """프론트엔드 한 번에 보여줄 통합 상태.

    running을 먼저 체크해 ground truth로 사용. installed는 running OR PATH.
    """
    running = is_ollama_running()
    installed = running or (shutil.which("ollama") is not None)
    models = list_installed_models() if running else []
    return {
        "installed": installed,
        "running": running,
        "version": ollama_version() if running else None,
        "models": [{"name": m.get("name"),
                    "size_gb": round((m.get("size") or 0) / (1024 ** 3), 2)}
                   for m in models],
        "tasks": get_all_task_status(),
    }
