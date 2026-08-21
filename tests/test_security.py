# -*- coding: utf-8 -*-
"""
접근 제어 — 로그인 없이 뚫리는 라우트가 없는가.

v4.0.0 감사에서 나온 것들이다.
- 관리자 프록시 3개에 인증이 없어, 서버가 0.0.0.0 에 붙어 있는 한
  같은 와이파이의 누구나 주인의 관리자 권한을 빌려 쓸 수 있었다.
- 보고서가 무인증으로 열려 파일명만 알면 남의 분석을 받아 갔다.

여기서는 **네트워크 없이** 라우트 보호만 본다. 세션이 없으면 미들웨어가
중앙 서버에 묻기 전에 401 을 돌려주므로, 중앙이 죽어 있어도 돈다.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SERVER = (ROOT / "webapp" / "server.py").read_text(encoding="utf-8")

# 로그인 없이 열려도 되는 것들. 여기 없는데 무인증이면 테스트가 잡는다.
#
# 판단 기준: **읽기 전용이고, 로그인 화면이 이미 그걸 쓰고 있는가.**
# 로그인 배경에 차트가 돌기 때문에 시세 조회는 열려 있어야 한다.
# 반대로 키를 쓰거나, 설치를 트리거하거나, 소유자 토큰을 대리 전송하는
# 것은 전부 막았다(v4.0.1).
PUBLIC_OK = {
    "/", "/static/<path:fn>", "/docs/<path:fn>", "/qr_login",
    "/api/health",
    # 인증 자체를 하는 입구
    "/api/auth/login", "/api/auth/register", "/api/auth/logout",
    "/api/auth/me",
    "/api/auth/remote/status", "/api/auth/remote/configure",
    "/api/auth/remote/register", "/api/auth/remote/login",
    "/api/auth/remote/logout",
    # 로그인 화면이 쓰는 읽기 전용 시세·뉴스
    "/api/overview", "/api/heatmap/treemap", "/api/quotes", "/api/quote",
    "/api/symbol_info", "/api/calendar", "/api/search", "/api/chart",
    "/api/stream", "/api/app/info",
    "/api/awareness/summary", "/api/awareness/asset",
    "/api/awareness/all", "/api/awareness/history",
    "/api/news", "/api/news/countries", "/api/news/by_country",
    "/api/news/sentiment", "/api/analyst",
}

# 반드시 관리자만
ADMIN_ONLY = {
    "/api/auth/remote/admin/users",
    "/api/auth/remote/admin/approve",
    "/api/auth/remote/admin/reject",
    "/api/quota/tier",
}

# LAN 노출 토글도 로그인해야만 만질 수 있어야 한다
SENSITIVE = {"/api/network/lan", "/api/datasources/key", "/api/llm/auto_setup"}


def _routes():
    """(경로, 데코레이터들, 함수명) 목록."""
    out = []
    for m in re.finditer(
            r'@app\.route\(\s*("[^"]+")([^)]*)\)\s*\n'
            r'((?:@[\w\.]+(?:\([^)]*\))?\s*\n)*)\s*def\s+(\w+)', SERVER):
        out.append((m.group(1).strip('"'), m.group(3), m.group(4)))
    return out


def test_routes_were_actually_found():
    """정규식이 깨지면 모든 검사가 조용히 통과한다 — 그걸 막는다."""
    rs = _routes()
    assert len(rs) > 100, f"라우트를 {len(rs)}개밖에 못 찾았다"


def test_admin_routes_require_admin():
    """
    관리자 라우트에 @require_admin 이 붙어 있는가.

    중앙 서버가 requireAdmin 을 검사하지만, 그 대상은 '호출한 사람'이
    아니라 '이 PC 의 주인'이다. 로컬에서 먼저 막아야 한다.
    """
    found = {p: d for p, d, _ in _routes()}
    for path in ADMIN_ONLY:
        assert path in found, f"{path} 라우트가 사라졌다"
        assert "require_admin" in found[path] or "require_auth" in found[path], \
            f"{path} 에 인증 데코레이터가 없다"
        assert "require_admin" in found[path] or \
            'role") != "admin"' in SERVER, \
            f"{path} 가 관리자만 통과시키는지 확인 필요"


def test_report_serving_requires_login():
    """생성된 보고서는 로그인해야 볼 수 있어야 한다."""
    found = {p: d for p, d, _ in _routes()}
    assert "require_auth" in found.get("/report/<path:fn>", ""), \
        "/report 가 다시 무인증으로 열렸다"


def test_no_new_unprotected_routes():
    """
    새로 만든 라우트가 인증 없이 열려 있지 않은가.

    라우트가 128개다. 하나 추가할 때 데코레이터를 빠뜨리기 쉽고,
    빠뜨려도 화면은 멀쩡히 돌아서 눈치채기 어렵다.
    """
    bad = []
    for path, decos, fn in _routes():
        if path in PUBLIC_OK:
            continue
        if any(k in decos for k in ("require_auth", "require_admin",
                                    "require_tier")):
            continue
        bad.append(f"{path}  ({fn})")
    assert not bad, (
        "인증 없이 열린 라우트가 있다. 공개해도 되면 PUBLIC_OK 에 "
        "추가하고, 아니면 데코레이터를 붙일 것:\n  " + "\n  ".join(bad))


def test_sensitive_routes_are_locked():
    """
    특히 위험한 것들은 개별로도 확인한다.

    /api/datasources/key 는 인증 없이 API 키를 덮어썼고,
    /api/llm/auto_setup 은 인증 없이 Ollama 설치를 트리거했다
    (다운로드 + 실행). /api/network/lan 은 노출 범위를 바꾼다.
    이 셋은 목록 검사에 묻히면 안 돼서 이름을 박아 둔다.
    """
    found = {p: d for p, d, _ in _routes()}
    for path in SENSITIVE:
        assert path in found, f"{path} 라우트가 사라졌다"
        assert "require_auth" in found[path] or "require_admin" in found[path],             f"{path} 가 다시 무인증으로 열렸다"


def test_public_list_has_no_stale_entries():
    """PUBLIC_OK 에 이미 없어진 라우트가 남아 목록을 무의미하게 만들지 않게."""
    paths = {p for p, _, _ in _routes()}
    stale = [p for p in PUBLIC_OK if p not in paths and p != "/api/health"]
    assert not stale, f"PUBLIC_OK 에 없는 라우트가 남아 있다: {stale}"


# ── 중앙 인증 Worker ─────────────────────────────────────────
WORKER = ROOT / "auth-worker" / "src" / "index.js"


@pytest.mark.skipif(not WORKER.exists(), reason="worker 소스 없음")
def test_worker_admin_handlers_check_admin():
    """Worker 의 /admin 핸들러가 전부 requireAdmin 을 부르는가."""
    js = WORKER.read_text(encoding="utf-8")
    for fn in ("handleAdminUsers", "handleAdminApprove", "handleAdminReject",
               "handleAdminSetTier"):
        m = re.search(rf'async function {fn}\(.*?\n\}}', js, re.S)
        assert m, f"{fn} 를 못 찾았다"
        assert "requireAdmin" in m.group(0), f"{fn} 에 권한 검사가 없다"


@pytest.mark.skipif(not WORKER.exists(), reason="worker 소스 없음")
def test_worker_validates_tier_value():
    """등급 값을 검증하는가 — 임의 문자열이 DB 에 들어가면 안 된다."""
    js = WORKER.read_text(encoding="utf-8")
    assert "TIERS.includes(tier)" in js, "등급 화이트리스트 검사가 없다"
    assert re.search(r"TIERS\s*=\s*\['free',\s*'premium',\s*'platinum'\]", js)


@pytest.mark.skipif(not WORKER.exists(), reason="worker 소스 없음")
def test_worker_password_iterations_within_platform_limit():
    """
    Workers 의 PBKDF2 는 10만 회가 상한이다. 넘기면 NotSupportedError 로
    요청 자체가 죽는데, 로컬 dev 는 이 제한을 강제하지 않아 배포 후에야
    터진다.
    """
    js = WORKER.read_text(encoding="utf-8")
    m = re.search(r'PBKDF2_ITERATIONS\s*=\s*(\d+)', js)
    assert m, "PBKDF2_ITERATIONS 를 못 찾았다"
    assert int(m.group(1)) <= 100000, "10만 회를 넘으면 배포본에서 죽는다"


# ── 네트워크 노출 ────────────────────────────────────────────
LAUNCHER = (ROOT / "run_desktop.py").read_text(encoding="utf-8")


def test_binds_localhost_by_default():
    """
    기본은 127.0.0.1 이어야 한다.

    전에는 늘 0.0.0.0 이라, 앱을 켜는 것만으로 같은 망의 모든 기기에
    열렸다. 카페·PC방·회사 망에서는 의도한 적 없는 노출이다.
    Jupyter·TensorBoard 도 같은 이유로 기본이 localhost 다.
    """
    assert "def lan_allowed()" in LAUNCHER, "LAN 스위치가 사라졌다"
    assert '"0.0.0.0" if lan_allowed() else "127.0.0.1"' in LAUNCHER,         "무조건 0.0.0.0 에 붙는 상태로 되돌아갔다"


def test_no_external_script_sources():
    """
    화면이 외부에서 스크립트를 끌어오지 않는가.

    CDN 이 오염되면 임의의 JS 가 이 앱의 출처로 실행된다 — 세션이 붙은
    요청을 마음대로 보낼 수 있다. jsdelivr 은 파일을 동적으로 재압축해서
    SRI 로도 못 막는다(그쪽 파일 주석의 경고).
    """
    html = (ROOT / "webapp" / "static" / "index.html").read_text(encoding="utf-8")
    ext = re.findall(r'<script[^>]+src="(https?://[^"]+)"', html)
    assert not ext, f"외부 스크립트가 다시 들어왔다: {ext}"
    for f in ("lightweight-charts-4.1.3.js", "qrcode-generator-1.4.4.js"):
        assert (ROOT / "webapp" / "static" / "vendor" / f).exists(),             f"들여온 라이브러리가 없다: {f}"


def test_security_headers_are_set():
    """CSP·nosniff·frame-ancestors 등이 응답에 붙는가."""
    assert "@app.after_request" in SERVER
    for h in ("Content-Security-Policy", "X-Content-Type-Options",
              "X-Frame-Options", "Referrer-Policy", "Permissions-Policy"):
        assert h in SERVER, f"{h} 헤더가 사라졌다"
    # CSP 가 실질적인가 — 아무 출처나 허용하면 없는 것과 같다
    assert "object-src 'none'" in SERVER
    assert "base-uri 'self'" in SERVER
    assert "frame-ancestors 'self'" in SERVER
    assert "*" not in re.search(r'_CSP = "; "\.join\(\[(.*?)\]\)',
                                SERVER, re.S).group(1).replace("*/", "")
