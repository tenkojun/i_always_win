"""
Kronos 자동 설치 관리자 — 인앱 다운로드
============================================================
사용자가 git/pip 명령 직접 안 쓰고 버튼 하나로 설치.

3단계:
  1. pip install transformers huggingface_hub einops (Python subprocess)
  2. Kronos repo zip 다운로드 + 압축 해제 (git 없어도 OK)
  3. HuggingFace 모델 가중치 다운로드 (huggingface_hub.snapshot_download)

진행률 추적:
  - 메모리 dict (전역 상태) — UI가 polling
  - 각 단계: pending → running → done / failed
"""
from __future__ import annotations

import io
import os
import subprocess
import sys
import threading
import time
import urllib.request
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional


# ── 경로 ──────────────────────────────────────────────────────
_HOME = Path.home() / ".jiqt"
_HOME.mkdir(parents=True, exist_ok=True)
KRONOS_DIR = _HOME / "kronos"
KRONOS_DIR.mkdir(parents=True, exist_ok=True)
KRONOS_REPO_PATH = KRONOS_DIR / "Kronos"

# Kronos GitHub zip (검증: shiyu-coder/Kronos · 기본 브랜치 master)
# HEAD = 기본 브랜치 자동 추적 (main이든 master든)
KRONOS_ZIP_URL = "https://github.com/shiyu-coder/Kronos/archive/HEAD.zip"
_KRONOS_ZIP_FALLBACKS = [
    "https://github.com/shiyu-coder/Kronos/archive/refs/heads/master.zip",
    "https://github.com/shiyu-coder/Kronos/archive/refs/heads/main.zip",
]


# ── 상태 추적 (전역) ─────────────────────────────────────────
_STATE: Dict[str, Any] = {
    "running": False,
    "current_step": None,         # 'packages' | 'repo' | 'model_xxx'
    "steps": {
        "packages": {"status": "pending", "msg": ""},
        "repo":     {"status": "pending", "msg": ""},
    },
    "models": {},                  # model_key → {status, msg, size_mb}
    "log": [],                     # 최근 메시지
    "started_at": None,
    "finished_at": None,
}
_STATE_LOCK = threading.Lock()
_INSTALL_THREAD: Optional[threading.Thread] = None


def _log(msg: str):
    with _STATE_LOCK:
        _STATE["log"].append({"ts": time.time(), "msg": msg})
        _STATE["log"] = _STATE["log"][-50:]


def _set_step(step: str, status: str, msg: str = ""):
    with _STATE_LOCK:
        _STATE["current_step"] = step
        if step in _STATE["steps"]:
            _STATE["steps"][step] = {"status": status, "msg": msg}
    _log(f"[{step}] {status} {msg}")


def _set_model(model_key: str, status: str, msg: str = "",
                size_mb: float = 0):
    with _STATE_LOCK:
        _STATE["models"][model_key] = {
            "status": status, "msg": msg, "size_mb": size_mb}
    _log(f"[{model_key}] {status} {msg}")


def get_status() -> Dict[str, Any]:
    """현재 설치 상태 (UI polling용)."""
    with _STATE_LOCK:
        return dict(_STATE)


def is_running() -> bool:
    with _STATE_LOCK:
        return bool(_STATE.get("running"))


# ════════════════════════════════════════════════════════════
#  단계 1: pip install
# ════════════════════════════════════════════════════════════
_REQUIRED_PACKAGES = ["transformers", "huggingface_hub", "einops"]


def check_packages_installed() -> Dict[str, bool]:
    """현재 설치 상태."""
    out = {}
    for p in _REQUIRED_PACKAGES:
        try:
            __import__(p)
            out[p] = True
        except ImportError:
            out[p] = False
    return out


def install_packages_blocking() -> Dict[str, Any]:
    """pip install — 차단 호출 (백그라운드 thread 내부에서 사용)."""
    _set_step("packages", "running", "pip install 시작...")
    missing = [p for p, ok in check_packages_installed().items() if not ok]
    if not missing:
        _set_step("packages", "done", "이미 설치됨")
        return {"ok": True, "skipped": True}
    cmd = [sys.executable, "-m", "pip", "install",
           "--upgrade", "--quiet"] + missing
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                                timeout=600)
        if proc.returncode != 0:
            err = (proc.stderr or proc.stdout or "")[-500:]
            _set_step("packages", "failed", f"pip 실패: {err}")
            return {"ok": False, "error": err}
        _set_step("packages", "done",
                   f"{len(missing)}개 패키지 설치 완료")
        return {"ok": True, "installed": missing}
    except subprocess.TimeoutExpired:
        _set_step("packages", "failed", "타임아웃 (10분)")
        return {"ok": False, "error": "타임아웃"}
    except Exception as e:
        _set_step("packages", "failed", str(e))
        return {"ok": False, "error": str(e)}


# ════════════════════════════════════════════════════════════
#  단계 2: Kronos repo (GitHub zip 다운로드)
# ════════════════════════════════════════════════════════════
def check_repo_installed() -> bool:
    """Kronos model.py 존재 여부."""
    return (KRONOS_REPO_PATH / "model" / "__init__.py").exists() or \
           (KRONOS_REPO_PATH / "model.py").exists() or \
           any(KRONOS_REPO_PATH.glob("model*.py")) if KRONOS_REPO_PATH.exists() else False


def _download_zip(url: str) -> bytes:
    """단일 URL에서 zip 다운로드 — 실패 시 예외."""
    req = urllib.request.Request(url, headers={"User-Agent": "JIQT/1.0"})
    with urllib.request.urlopen(req, timeout=180) as resp:
        code = resp.getcode()
        if code != 200:
            raise IOError(f"HTTP {code}")
        return resp.read()


def install_repo_blocking() -> Dict[str, Any]:
    """GitHub zip 다운로드 + 압축 해제. git 명령 불요."""
    _set_step("repo", "running", "Kronos repo 다운로드...")
    if check_repo_installed():
        _set_step("repo", "done", "이미 설치됨")
        return {"ok": True, "skipped": True}
    urls = [KRONOS_ZIP_URL] + _KRONOS_ZIP_FALLBACKS
    data = None
    last_err = ""
    for url in urls:
        try:
            _set_step("repo", "running", f"시도: {url}")
            _log(f"repo zip 시도: {url}")
            data = _download_zip(url)
            _log(f"다운로드 성공: {url} ({len(data)/1e6:.1f}MB)")
            break
        except Exception as e:
            last_err = f"{type(e).__name__}: {e}"
            _log(f"실패({url}): {last_err}")
    if data is None:
        _set_step("repo", "failed", f"모든 URL 실패 — {last_err}")
        return {"ok": False, "error": last_err}
    try:
        size_mb = len(data) / 1e6
        _set_step("repo", "running",
                   f"압축 해제 중... ({size_mb:.1f}MB)")
        # 압축 해제 (in-memory)
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            # Kronos-main/ 안에 다 있음 → KRONOS_REPO_PATH로 풀기
            tmp_dir = KRONOS_DIR / "_tmp_unzip"
            if tmp_dir.exists():
                import shutil
                shutil.rmtree(tmp_dir, ignore_errors=True)
            tmp_dir.mkdir(parents=True, exist_ok=True)
            zf.extractall(tmp_dir)
            # Kronos-main/ → KRONOS_REPO_PATH로 이동
            sub_dirs = list(tmp_dir.iterdir())
            if sub_dirs:
                src = sub_dirs[0]
                if KRONOS_REPO_PATH.exists():
                    import shutil
                    shutil.rmtree(KRONOS_REPO_PATH, ignore_errors=True)
                src.rename(KRONOS_REPO_PATH)
            import shutil
            shutil.rmtree(tmp_dir, ignore_errors=True)
        _set_step("repo", "done",
                   f"Kronos repo 설치 완료 ({KRONOS_REPO_PATH})")
        # PYTHONPATH 추가 (현재 프로세스)
        if str(KRONOS_REPO_PATH) not in sys.path:
            sys.path.insert(0, str(KRONOS_REPO_PATH))
        return {"ok": True, "path": str(KRONOS_REPO_PATH)}
    except Exception as e:
        import traceback
        _set_step("repo", "failed",
                   f"{type(e).__name__}: {str(e)[:200]}")
        return {"ok": False, "error": str(e),
                "trace": traceback.format_exc()[-300:]}


# ════════════════════════════════════════════════════════════
#  단계 3: HuggingFace 모델 다운로드
# ════════════════════════════════════════════════════════════
# HF 모델 로컬 저장 경로 — Windows symlink 권한 회피용
HF_LOCAL_DIR = KRONOS_DIR / "hf_models"
HF_LOCAL_DIR.mkdir(parents=True, exist_ok=True)


def _hf_local_path(repo_id: str) -> Path:
    """HF repo_id → 로컬 경로 (NeoQuasar/Kronos-small → NeoQuasar__Kronos-small)."""
    return HF_LOCAL_DIR / repo_id.replace("/", "__")


def _safe_snapshot(repo_id: str) -> str:
    """Windows symlink 회피 — local_dir + symlinks=False (버전 호환 처리).

    HF Hub 버전별:
      - v0.17~v0.22: local_dir_use_symlinks=False (명시 필요)
      - v0.23+:      local_dir 지정 시 자동 복사 모드
      - v1.0+:       local_dir_use_symlinks 인자 제거됨 → 자동
    """
    import inspect
    from huggingface_hub import snapshot_download
    local = _hf_local_path(repo_id)
    local.mkdir(parents=True, exist_ok=True)
    kwargs = {"repo_id": repo_id, "local_dir": str(local)}
    sig = inspect.signature(snapshot_download)
    if "local_dir_use_symlinks" in sig.parameters:
        kwargs["local_dir_use_symlinks"] = False
    return snapshot_download(**kwargs)


def download_model_blocking(model_key: str) -> Dict[str, Any]:
    """HF snapshot_download — 모델 + 토크나이저 가중치 받기."""
    from .kronos_predictor import KRONOS_MODELS
    if model_key not in KRONOS_MODELS:
        return {"ok": False, "error": f"unknown model {model_key}"}
    meta = KRONOS_MODELS[model_key]
    _set_model(model_key, "running", "HF 다운로드 시작...")
    try:
        from huggingface_hub import snapshot_download  # noqa
    except ImportError:
        _set_model(model_key, "failed",
                    "huggingface_hub 미설치 — 단계 1 먼저")
        return {"ok": False, "error": "huggingface_hub missing"}
    # 심볼릭 링크 경고 억제 (Windows 권한 부족 시)
    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
    try:
        # 모델
        _log(f"HF 모델 시도: {meta['model_id']} → {_hf_local_path(meta['model_id'])}")
        _set_model(model_key, "running",
                    f"모델 받는 중: {meta['model_id']}")
        model_path = _safe_snapshot(meta["model_id"])
        # 토크나이저
        _log(f"HF 토크나이저 시도: {meta['tokenizer_id']}")
        _set_model(model_key, "running",
                    f"토크나이저 받는 중: {meta['tokenizer_id']}")
        tok_path = _safe_snapshot(meta["tokenizer_id"])
        # 크기 계산
        size_b = 0
        for p in Path(model_path).rglob("*"):
            if p.is_file():
                size_b += p.stat().st_size
        size_mb = round(size_b / 1e6, 1)
        _set_model(model_key, "done",
                    f"완료 ({size_mb}MB · {meta['params']})",
                    size_mb=size_mb)
        return {"ok": True, "model_path": model_path,
                "tokenizer_path": tok_path, "size_mb": size_mb}
    except Exception as e:
        import traceback
        err_short = str(e)[:300]
        _set_model(model_key, "failed",
                    f"HF repo={meta['model_id']} · {type(e).__name__}: {err_short}")
        _log(f"[실패] {meta['model_id']}: {err_short}")
        return {"ok": False, "error": str(e),
                "trace": traceback.format_exc()[-400:]}


# ════════════════════════════════════════════════════════════
#  통합 설치 (백그라운드 thread)
# ════════════════════════════════════════════════════════════
def install_all_async(models: Optional[List[str]] = None) -> Dict[str, Any]:
    """1+2+3 단계 전부 백그라운드 thread로.

    models: 다운로드할 Kronos 모델 키 리스트.
      None이면 ['kronos_small'] (가장 균형)
    """
    global _INSTALL_THREAD
    if is_running():
        return {"ok": False, "error": "이미 설치 진행 중"}
    if models is None:
        models = ["kronos_small"]
    with _STATE_LOCK:
        _STATE["running"] = True
        _STATE["started_at"] = time.time()
        _STATE["finished_at"] = None
        # 초기화
        _STATE["steps"] = {
            "packages": {"status": "pending", "msg": ""},
            "repo":     {"status": "pending", "msg": ""},
        }
        _STATE["models"] = {m: {"status": "pending", "msg": ""}
                            for m in models}
        _STATE["log"] = []

    def _worker():
        try:
            # 1) pip
            r1 = install_packages_blocking()
            if not r1.get("ok"):
                with _STATE_LOCK:
                    _STATE["running"] = False
                    _STATE["finished_at"] = time.time()
                return
            # 2) repo
            r2 = install_repo_blocking()
            if not r2.get("ok"):
                with _STATE_LOCK:
                    _STATE["running"] = False
                    _STATE["finished_at"] = time.time()
                return
            # 3) 모델들 순차
            for m in models:
                download_model_blocking(m)
            with _STATE_LOCK:
                _STATE["running"] = False
                _STATE["finished_at"] = time.time()
            _log("✓ 전체 설치 완료")
        except Exception as e:
            _log(f"치명적 오류: {e}")
            with _STATE_LOCK:
                _STATE["running"] = False
                _STATE["finished_at"] = time.time()

    _INSTALL_THREAD = threading.Thread(
        target=_worker, name="kronos-installer", daemon=True)
    _INSTALL_THREAD.start()
    return {"ok": True, "started": True, "models": models}


def install_single_model_async(model_key: str) -> Dict[str, Any]:
    """단일 모델만 추가 다운로드 (이미 packages/repo 설치된 후)."""
    if is_running():
        return {"ok": False, "error": "이미 설치 진행 중"}
    from .kronos_predictor import KRONOS_MODELS
    if model_key not in KRONOS_MODELS:
        return {"ok": False, "error": "unknown model"}
    with _STATE_LOCK:
        _STATE["running"] = True
        _STATE["models"][model_key] = {"status": "pending", "msg": ""}

    def _worker():
        try:
            download_model_blocking(model_key)
        finally:
            with _STATE_LOCK:
                _STATE["running"] = False
                _STATE["finished_at"] = time.time()

    threading.Thread(target=_worker, daemon=True).start()
    return {"ok": True, "started": True}
