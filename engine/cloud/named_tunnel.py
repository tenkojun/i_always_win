"""
정식 (Named) Tunnel 관리 — C11
================================
Quick Tunnel(임의 URL)과 달리 사용자 도메인 + 영구 URL 제공.

저장 위치 (~/.jiqt/):
  - cloud_named.json       : 설정 (token / account_id / tunnel_id / hostname)
  - cf_tunnel_<id>.json    : cloudflared 자격증명 (절대 노출 금지)
  - cf_tunnel_<id>.yml     : cloudflared config (ingress 규칙)
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

from . import cf_api
from . import tunnel as _quick_tunnel  # find_cloudflared, _bin_local 등 재사용


_JIQT_HOME = Path.home() / ".jiqt"
_JIQT_HOME.mkdir(exist_ok=True)
_CONF_FILE = _JIQT_HOME / "cloud_named.json"

_LOCK = threading.RLock()
_PROC: Optional[subprocess.Popen] = None
_STATE: Dict[str, Any] = {
    "running": False, "started_at": None,
    "hostname": "", "tunnel_id": "", "log": [],
    "error": "",
}


# ── 설정 파일 IO ───────────────────────────────────────────────
def load_config() -> Dict[str, Any]:
    if not _CONF_FILE.exists():
        return {}
    try:
        return json.loads(_CONF_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_config(d: Dict[str, Any]) -> None:
    # 토큰 보호 — chmod 0600
    _CONF_FILE.write_text(json.dumps(d, indent=2, ensure_ascii=False),
                          encoding="utf-8")
    try:
        os.chmod(_CONF_FILE, 0o600)
    except Exception:
        pass


def _creds_path(tunnel_id: str) -> Path:
    return _JIQT_HOME / f"cf_tunnel_{tunnel_id}.json"


def _cfg_path(tunnel_id: str) -> Path:
    return _JIQT_HOME / f"cf_tunnel_{tunnel_id}.yml"


# ── 전체 셋업 (토큰 → tunnel → DNS → 로컬 파일 저장) ──────────
def setup(token: str, account_id: str, zone_id: str,
          hostname: str, tunnel_name: str,
          local_port: int = 8765,
          existing_tunnel_id: str = None
          ) -> Dict[str, Any]:
    """
    전체 셋업 한 번에:
      1) tunnel 생성 (또는 기존 사용)
      2) 자격증명 + config 로컬 저장
      3) DNS CNAME 라우트
      4) cloud_named.json 갱신
    """
    if not all([token, account_id, zone_id, hostname]):
        return {"ok": False, "error": "필수 파라미터 누락"}

    # 1) tunnel 생성/조회
    if existing_tunnel_id:
        # 기존 tunnel 사용 — secret을 새로 받을 수 없으니 자격증명 새로 발급 불가
        # → 새 tunnel 생성 권장
        return {"ok": False,
                "error": "기존 tunnel 재사용은 자격증명 필요 — 새 이름으로 생성하세요"}

    r = cf_api.create_tunnel(token, account_id, tunnel_name)
    if not r.get("ok"):
        return r
    tunnel_id = r["id"]
    secret = r["secret"]

    # 2) 자격증명 + config 저장
    creds = cf_api.credentials_json(account_id, tunnel_id, secret)
    cp = _creds_path(tunnel_id)
    cp.write_text(json.dumps(creds, indent=2), encoding="utf-8")
    try:
        os.chmod(cp, 0o600)
    except Exception:
        pass

    yml = _build_config_yaml(tunnel_id, str(cp), hostname, local_port)
    yp = _cfg_path(tunnel_id)
    yp.write_text(yml, encoding="utf-8")

    # 3) DNS 라우트
    dns = cf_api.route_dns_cname(token, zone_id, hostname, tunnel_id)
    if not dns.get("ok"):
        # rollback
        cf_api.delete_tunnel(token, account_id, tunnel_id)
        try: cp.unlink(); yp.unlink()
        except Exception: pass
        return {"ok": False,
                "error": f"DNS 설정 실패: {dns.get('error')}"}

    # 4) 설정 저장
    conf = load_config()
    conf.update({
        "token": token,
        "account_id": account_id,
        "zone_id": zone_id,
        "tunnel_id": tunnel_id,
        "tunnel_name": tunnel_name,
        "hostname": hostname,
        "local_port": local_port,
        "dns_record_id": dns.get("record_id"),
        "creds_path": str(cp),
        "config_path": str(yp),
    })
    save_config(conf)
    return {"ok": True,
            "tunnel_id": tunnel_id,
            "hostname": hostname,
            "url": f"https://{hostname}",
            "creds_path": str(cp),
            "config_path": str(yp)}


def _build_config_yaml(tunnel_id: str, creds_file: str,
                       hostname: str, local_port: int) -> str:
    """간단한 cloudflared config.yml — 단일 hostname → localhost 포트."""
    # YAML 직접 작성 (의존성 회피)
    creds_yaml = creds_file.replace("\\", "/")
    return (
        f"tunnel: {tunnel_id}\n"
        f"credentials-file: {creds_yaml}\n"
        f"\n"
        f"ingress:\n"
        f"  - hostname: {hostname}\n"
        f"    service: http://localhost:{local_port}\n"
        f"  - service: http_status:404\n"
    )


# ── 시작/중지 ──────────────────────────────────────────────────
def start_named() -> Dict[str, Any]:
    global _PROC
    with _LOCK:
        if _PROC and _PROC.poll() is None:
            return {"ok": True, "running": True,
                    "note": "이미 실행 중"}
        conf = load_config()
        tunnel_id = conf.get("tunnel_id")
        cfg_path = conf.get("config_path")
        if not tunnel_id or not cfg_path or not os.path.exists(cfg_path):
            return {"ok": False,
                    "error": "셋업 정보가 없습니다 — 먼저 setup 실행"}
        bin_path = _quick_tunnel.find_cloudflared()
        if not bin_path:
            return {"ok": False,
                    "error": "cloudflared 바이너리 없음 — Quick Tunnel 먼저 설치"}
        # Quick Tunnel 실행 중이면 정지
        try:
            _quick_tunnel.stop_quick()
        except Exception:
            pass
        # named tunnel 실행
        try:
            _PROC = subprocess.Popen(
                [bin_path, "tunnel", "--config", cfg_path,
                 "run", tunnel_id],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1,
                creationflags=(subprocess.CREATE_NO_WINDOW
                               if sys.platform.startswith("win")
                               else 0),
            )
            _STATE["running"] = True
            _STATE["started_at"] = int(time.time())
            _STATE["hostname"] = conf.get("hostname", "")
            _STATE["tunnel_id"] = tunnel_id
            _STATE["error"] = ""
            _STATE["log"].clear()
            threading.Thread(target=_drain_log, daemon=True).start()
            return {"ok": True, "running": True,
                    "url": f"https://{conf.get('hostname','')}"}
        except Exception as e:
            _STATE["error"] = str(e)
            return {"ok": False, "error": str(e)}


def _drain_log():
    global _PROC
    if _PROC is None:
        return
    for line in _PROC.stdout:
        with _LOCK:
            _STATE["log"].append(line.rstrip())
            if len(_STATE["log"]) > 80:
                _STATE["log"] = _STATE["log"][-80:]
    with _LOCK:
        _STATE["running"] = False


def stop_named() -> Dict[str, Any]:
    global _PROC
    with _LOCK:
        if _PROC and _PROC.poll() is None:
            try:
                _PROC.terminate()
                try: _PROC.wait(timeout=4)
                except Exception: _PROC.kill()
            except Exception:
                pass
        _PROC = None
        _STATE["running"] = False
        return {"ok": True}


def status() -> Dict[str, Any]:
    with _LOCK:
        running = bool(_PROC and _PROC.poll() is None)
        _STATE["running"] = running
        conf = load_config()
        return {
            "running": running,
            "configured": bool(conf.get("tunnel_id")),
            "hostname": conf.get("hostname", ""),
            "tunnel_id": conf.get("tunnel_id", ""),
            "tunnel_name": conf.get("tunnel_name", ""),
            "url": (f"https://{conf.get('hostname')}"
                    if conf.get("hostname") else ""),
            "started_at": _STATE["started_at"] if running else None,
            "log_tail": list(_STATE["log"][-15:]),
            "error": _STATE["error"],
        }


# ── 완전 제거 (tunnel + DNS + 로컬 파일) ───────────────────────
def teardown() -> Dict[str, Any]:
    stop_named()
    conf = load_config()
    token = conf.get("token")
    account_id = conf.get("account_id")
    zone_id = conf.get("zone_id")
    tunnel_id = conf.get("tunnel_id")
    record_id = conf.get("dns_record_id")
    errors = []
    # DNS 삭제
    if token and zone_id and record_id:
        r = cf_api.delete_dns_record(token, zone_id, record_id)
        if not r.get("ok"):
            errors.append("DNS 삭제 실패")
    # Tunnel 삭제
    if token and account_id and tunnel_id:
        r = cf_api.delete_tunnel(token, account_id, tunnel_id)
        if not r.get("ok"):
            errors.append("Tunnel 삭제 실패")
    # 로컬 파일 삭제
    try:
        cp = conf.get("creds_path")
        if cp and os.path.exists(cp): os.remove(cp)
        yp = conf.get("config_path")
        if yp and os.path.exists(yp): os.remove(yp)
    except Exception:
        pass
    # 설정 파일 비움 (token은 보존 옵션 — 일단 전체 삭제)
    try:
        _CONF_FILE.unlink(missing_ok=True)
    except Exception:
        pass
    return {"ok": True, "errors": errors}
