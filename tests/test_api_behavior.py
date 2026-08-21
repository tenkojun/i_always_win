# -*- coding: utf-8 -*-
"""
기능 테스트 — 소스를 읽는 대신 **실제로 호출한다.**

`test_security.py` 는 소스에서 데코레이터를 찾는다. 그건 빠르지만
"데코레이터가 붙어 있다" 를 볼 뿐 "실제로 막힌다" 를 보지 못한다.
데코레이터 순서가 틀렸거나, before_request 가 먼저 통과시켜 버리거나,
예외가 인증을 우회하는 경우를 놓친다.

여기서는 Flask 테스트 클라이언트로 진짜 요청을 보낸다. 네트워크는
타지 않는다 — 세션이 없으면 미들웨어가 중앙 서버에 묻기 전에 401 을
돌려주므로, 중앙이 죽어 있어도 돈다.
"""
from __future__ import annotations

import pytest


@pytest.fixture(scope="module")
def client():
    from webapp.server import app
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


# ── 인증이 실제로 막는가 ─────────────────────────────────────
@pytest.mark.parametrize("method,path", [
    ("GET",  "/api/auth/remote/admin/users"),
    ("POST", "/api/auth/remote/admin/approve"),
    ("POST", "/api/auth/remote/admin/reject"),
    ("POST", "/api/quota/tier"),
    ("GET",  "/api/datasources"),
    ("POST", "/api/datasources/key"),
    ("POST", "/api/llm/auto_setup"),
    ("POST", "/api/llm/install_ollama"),
    ("POST", "/api/auth/remote/change_password"),
    ("GET",  "/api/auth/remote/sessions"),
    ("GET",  "/api/network/lan"),
    ("POST", "/api/network/lan"),
    ("GET",  "/api/quota"),
    ("GET",  "/api/community/posts"),
    ("GET",  "/report/whatever.html"),
])
def test_protected_routes_reject_anonymous(client, method, path):
    """
    로그인 없이 부르면 401 이어야 한다.

    이 중 셋은 특히 위험했다 — datasources/key 는 인증 없이 API 키를
    덮어썼고, llm/auto_setup 은 인증 없이 Ollama 설치를 트리거했으며
    (다운로드+실행), admin/* 는 소유자 토큰을 대리 전송했다.
    """
    r = client.open(path, method=method, json={})
    assert r.status_code == 401, (
        f"{method} {path} 가 {r.status_code} 를 돌려줬다 (401 이어야 함)")


@pytest.mark.parametrize("path", [
    "/api/health",
    "/api/auth/me",
    "/api/auth/remote/status",
])
def test_public_routes_stay_open(client, path):
    """로그인 화면이 쓰는 것들은 막히면 안 된다 — 앱이 안 켜진다."""
    r = client.get(path)
    assert r.status_code == 200, f"{path} 가 {r.status_code}"


def test_health_shape(client):
    """업데이터·런처·진단 도구가 전부 이 응답에 의존한다."""
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.is_json, "health 가 JSON 이 아니다"


# ── 보안 헤더가 실제 응답에 붙는가 ───────────────────────────
def test_security_headers_on_real_response(client):
    r = client.get("/api/health")
    csp = r.headers.get("Content-Security-Policy", "")
    assert csp, "CSP 헤더가 없다"
    assert "default-src 'self'" in csp
    assert "object-src 'none'" in csp
    assert r.headers.get("X-Content-Type-Options") == "nosniff"
    assert r.headers.get("X-Frame-Options") == "SAMEORIGIN"
    assert r.headers.get("Referrer-Policy") == "no-referrer"


def test_csp_forbids_external_script_hosts(client):
    """
    script-src 가 외부 호스트를 허용하면 CDN 을 없앤 의미가 없다.
    """
    csp = client.get("/api/health").headers.get("Content-Security-Policy", "")
    part = [p for p in csp.split(";") if p.strip().startswith("script-src")]
    assert part, "script-src 지시어가 없다"
    src = part[0]
    assert "https://" not in src, f"외부 호스트가 허용돼 있다: {src}"
    assert "*" not in src, f"와일드카드가 있다: {src}"


def test_api_responses_are_not_cached(client):
    """인증이 걸린 응답이 중간 캐시에 남으면 안 된다."""
    r = client.get("/api/health")
    assert r.headers.get("Cache-Control") == "no-store"


# ── 오류 응답의 모양 ─────────────────────────────────────────
def test_401_is_json_not_html(client):
    """
    화면이 JSON 을 기대한다. HTML 이 오면 "알 수 없는 오류" 로만 보인다.
    """
    r = client.get("/api/quota")
    assert r.status_code == 401
    assert r.is_json, "401 이 JSON 이 아니다"
    body = r.get_json()
    assert body.get("code") == "AUTH_REQUIRED", \
        "화면이 로그인 유도를 못 한다"


def test_unknown_route_is_404_not_500(client):
    r = client.get("/api/이런건_없다")
    assert r.status_code == 404


# ── 등급 판정 ────────────────────────────────────────────────
def test_tier_values_are_whitelisted():
    """임의 문자열이 등급으로 저장되면 기능 판정이 통째로 무너진다."""
    from engine.auth.quota import TIERS, set_tier
    assert set(TIERS) == {"free", "premium", "platinum"}
    r = set_tier(999999, "god")
    assert not r.get("ok"), "알 수 없는 등급이 통과했다"


def test_features_fall_back_to_free_for_unknown_user():
    """모르는 사용자에게 기능을 열어 주면 안 된다."""
    from engine.auth.quota import features
    f = features(987654321)
    assert f["tier"] == "free"
