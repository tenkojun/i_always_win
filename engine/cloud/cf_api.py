"""
Cloudflare API 클라이언트 (C11 — 정식 Tunnel 자동화)
====================================================
사용자가 API Token만 발급하면 모든 작업 (계정 조회/Zone/Tunnel 생성/
DNS 라우트/자격증명 생성)을 자동 수행.

필요 권한 (Cloudflare Custom Token 생성 시):
  - Account > Cloudflare Tunnel > Edit
  - Zone > DNS > Edit
  - (옵션) Account > Account Settings > Read
"""
from __future__ import annotations

import json
import secrets
import base64
from typing import Any, Dict, List, Optional, Tuple

import requests

_BASE = "https://api.cloudflare.com/client/v4"


# ── 공통 HTTP 헬퍼 ────────────────────────────────────────────
def _headers(token: str) -> Dict[str, str]:
    return {"Authorization": f"Bearer {token}",
            "Content-Type": "application/json"}


def _get(token: str, path: str, params: Dict = None) -> Dict[str, Any]:
    r = requests.get(_BASE + path, headers=_headers(token),
                     params=params or {}, timeout=15)
    return r.json()


def _post(token: str, path: str, body: Dict = None) -> Dict[str, Any]:
    r = requests.post(_BASE + path, headers=_headers(token),
                      json=body or {}, timeout=20)
    return r.json()


def _put(token: str, path: str, body: Dict = None) -> Dict[str, Any]:
    r = requests.put(_BASE + path, headers=_headers(token),
                     json=body or {}, timeout=20)
    return r.json()


def _delete(token: str, path: str) -> Dict[str, Any]:
    r = requests.delete(_BASE + path, headers=_headers(token),
                        timeout=15)
    return r.json()


# ── 토큰 검증 + 계정/Zone 조회 ─────────────────────────────────
def verify_token(token: str) -> Dict[str, Any]:
    """토큰 유효성 + 권한 확인. 성공 시 {ok, status, accounts}."""
    if not token or len(token) < 20:
        return {"ok": False, "error": "토큰 형식 이상"}
    r = _get(token, "/user/tokens/verify")
    if not r.get("success"):
        msg = r.get("errors", [{}])[0].get("message", "검증 실패")
        return {"ok": False, "error": msg}
    return {"ok": True, "status": r.get("result", {}).get("status")}


def list_accounts(token: str) -> List[Dict[str, Any]]:
    r = _get(token, "/accounts")
    if not r.get("success"):
        return []
    return [{"id": a["id"], "name": a["name"]}
            for a in r.get("result", [])]


def list_zones(token: str, account_id: str = None
               ) -> List[Dict[str, Any]]:
    params = {"per_page": 50}
    if account_id:
        params["account.id"] = account_id
    r = _get(token, "/zones", params=params)
    if not r.get("success"):
        return []
    return [{"id": z["id"], "name": z["name"], "status": z["status"]}
            for z in r.get("result", [])]


# ── Tunnel CRUD ────────────────────────────────────────────────
def list_tunnels(token: str, account_id: str
                 ) -> List[Dict[str, Any]]:
    r = _get(token,
             f"/accounts/{account_id}/cfd_tunnel",
             params={"is_deleted": "false"})
    if not r.get("success"):
        return []
    return [{"id": t["id"], "name": t["name"],
             "created_at": t.get("created_at", ""),
             "status": t.get("status", "")}
            for t in r.get("result", [])]


def create_tunnel(token: str, account_id: str, name: str
                  ) -> Dict[str, Any]:
    """새 tunnel 생성. tunnel_secret은 cloudflared 인증용."""
    # 32바이트 random secret (base64)
    secret = base64.b64encode(secrets.token_bytes(32)).decode()
    body = {
        "name": name,
        "tunnel_secret": secret,
        "config_src": "local",   # 로컬 config.yml 사용
    }
    r = _post(token,
              f"/accounts/{account_id}/cfd_tunnel",
              body=body)
    if not r.get("success"):
        msg = r.get("errors", [{}])[0].get("message", "tunnel 생성 실패")
        return {"ok": False, "error": msg, "raw": r}
    t = r.get("result", {})
    return {"ok": True,
            "id": t["id"],
            "name": t["name"],
            "secret": secret,
            "account_id": account_id}


def delete_tunnel(token: str, account_id: str, tunnel_id: str
                  ) -> Dict[str, Any]:
    r = _delete(token,
                f"/accounts/{account_id}/cfd_tunnel/{tunnel_id}")
    if r.get("success"):
        return {"ok": True}
    return {"ok": False,
            "error": r.get("errors", [{}])[0].get("message", "삭제 실패")}


# ── DNS 라우트 (CNAME) ─────────────────────────────────────────
def route_dns_cname(token: str, zone_id: str,
                    hostname: str, tunnel_id: str,
                    proxied: bool = True) -> Dict[str, Any]:
    """
    hostname → <tunnel_id>.cfargotunnel.com 으로 CNAME 추가.
    이미 같은 이름의 레코드가 있으면 PUT으로 갱신.
    """
    target = f"{tunnel_id}.cfargotunnel.com"
    # 기존 레코드 조회
    existing = _get(token,
                    f"/zones/{zone_id}/dns_records",
                    params={"name": hostname, "type": "CNAME"})
    rec_list = existing.get("result", []) if existing.get("success") else []
    body = {"type": "CNAME", "name": hostname, "content": target,
            "proxied": proxied, "ttl": 1}
    if rec_list:
        # 갱신
        rec_id = rec_list[0]["id"]
        r = _put(token,
                 f"/zones/{zone_id}/dns_records/{rec_id}",
                 body=body)
    else:
        r = _post(token,
                  f"/zones/{zone_id}/dns_records",
                  body=body)
    if not r.get("success"):
        return {"ok": False,
                "error": r.get("errors", [{}])[0].get("message",
                                                       "DNS 설정 실패")}
    return {"ok": True, "record_id": r["result"]["id"],
            "target": target}


def delete_dns_record(token: str, zone_id: str, record_id: str
                      ) -> Dict[str, Any]:
    r = _delete(token,
                f"/zones/{zone_id}/dns_records/{record_id}")
    if r.get("success"):
        return {"ok": True}
    return {"ok": False}


# ── credentials.json 생성 (cloudflared용) ─────────────────────
def credentials_json(account_id: str, tunnel_id: str,
                     secret_b64: str) -> Dict[str, Any]:
    """
    cloudflared가 요구하는 자격증명 JSON 포맷.
    파일로 저장 후 config.yml에서 참조.
    """
    return {
        "AccountTag": account_id,
        "TunnelID": tunnel_id,
        "TunnelSecret": secret_b64,
    }
