# -*- coding: utf-8 -*-
"""
회귀 테스트 — 한 번 났던 버그는 다시 나지 않게.

여기 있는 것들은 전부 **실제로 사용자에게 배포된 상태에서 발견된** 것들이다.
고친 것만으로는 부족하다. 다음 수정이 이걸 되돌려도 아무도 모르는 상태가
진짜 문제였다.
"""
from __future__ import annotations

import io
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
INDEX = io.open(ROOT / "webapp" / "static" / "index.html", encoding="utf-8").read()
LAUNCHER = io.open(ROOT / "run_desktop.py", encoding="utf-8").read()
SERVER = io.open(ROOT / "webapp" / "server.py", encoding="utf-8").read()
QUOTA = io.open(ROOT / "engine" / "auth" / "quota.py", encoding="utf-8").read()


# ── v3.4.2 · WebView2 가 없으면 아무 말 없이 죽었다 ──────────
def test_launcher_checks_webview2_before_opening_window():
    """
    창을 만들기 전에 런타임을 확인하는가.

    없으면 pywebview 가 창을 띄우다 실패하는데 console=False 라 오류가
    아무 데도 안 보인다. 사용자 눈에는 "그냥 안 켜짐" 이고 로그에도 안
    남는다 — 다른 PC 에서 실제로 이랬다.
    """
    assert "def webview2_version(" in LAUNCHER
    assert "warn_missing_webview2" in LAUNCHER
    # 레지스트리 세 곳(64/32비트 시스템 · 사용자)을 다 봐야 한다
    assert LAUNCHER.count("EdgeUpdate") >= 3, "레지스트리 확인 위치가 줄었다"
    # 확인이 create_window 보다 먼저 와야 의미가 있다
    assert LAUNCHER.index("webview2_version()") < LAUNCHER.index("create_window")


def test_launcher_never_dies_silently():
    """창 생성이 실패해도 사용자에게 이유를 알려야 한다."""
    assert "_msgbox" in LAUNCHER
    assert "_browser_mode" in LAUNCHER, "브라우저 폴백이 사라졌다"


# ── v3.4.5 · 재실행하면 "연결할 수 없습니다" ────────────────
def test_launcher_reverifies_reused_instance():
    """
    중복 실행 감지는 한 순간의 스냅샷이다.

    옛 인스턴스가 종료되는 중에 확인이 걸리면 200 을 받고, 창이 열릴
    때쯤엔 아무도 안 듣는 포트가 된다. 껐다 바로 켜면 정확히 이 창이
    열렸다 — 창을 열기 직전에 다시 확인해야 한다.
    """
    m = re.search(r'if reuse:(.*?)\n    else:', LAUNCHER, re.S)
    assert m, "reuse 분기를 못 찾았다"
    assert "_wait_for_server" in m.group(1), \
        "재사용 경로에서 서버 생존 재확인이 사라졌다"
    assert "_warn_no_server" in LAUNCHER, "죽은 주소로 창을 열게 됐다"


# ── v4.0.0 · 관리자 화면의 Invalid Date ─────────────────────
def test_no_double_z_in_date_parsing():
    """
    중앙 서버의 nowIso() 는 이미 Z 로 끝난다. +'Z' 를 또 붙이면 'ZZ' 가
    되어 Invalid Date 로 떨어진다. 로컬 Python 값은 타임존이 없어 Z 가
    필요하므로, 일괄 제거가 아니라 utcDate() 로 양쪽을 처리해야 한다.
    """
    assert "+'Z')" not in INDEX, "날짜에 Z 를 다시 덧붙이고 있다"
    assert "function utcDate(s)" in INDEX, "utcDate 헬퍼가 사라졌다"
    assert INDEX.count("utcDate(") >= 9, "utcDate 사용처가 줄었다"


def test_utc_date_handles_both_server_formats():
    """Worker(ISO+Z) 와 로컬 Python(공백 구분, 타임존 없음) 둘 다."""
    m = re.search(r'function utcDate\(s\)\{(.*?)\n\}', INDEX, re.S)
    assert m
    body = m.group(1)
    assert "replace(' ', 'T')" in body, "공백 구분 형식을 처리하지 않는다"
    assert "Z|[+-]" in body, "이미 타임존이 있는 값을 구분하지 않는다"
    assert "new Date(NaN)" in body, \
        "실패 시 null 을 주면 호출부의 .toLocaleString() 에서 터진다"


# ── v4.0.0 · 등급 드롭다운이 안 뜨던 문제 ───────────────────
def test_admin_stats_passes_tier_through():
    """
    /api/admin/stats 가 화이트리스트로 응답을 재조립한다. 여기 빠지면
    중앙에서 등급을 받아 와도 화면까지 못 간다.
    """
    m = re.search(r'def api_admin_stats\(\):(.*?)\n@app\.route', SERVER, re.S)
    assert m, "api_admin_stats 를 못 찾았다"
    assert '"tier"' in m.group(1), "등급이 다시 화이트리스트에서 빠졌다"


# ── v4.0.0 · 등급이 로컬이라 우회됐다 ───────────────────────
def test_tier_prefers_central_over_local_cache():
    """
    로컬 .data/auth.db 를 고치면 누구나 플래티넘이 됐다. 중앙 값이
    있으면 그걸 써야 하고, 로컬은 못 닿을 때 쓰는 캐시일 뿐이다.
    """
    m = re.search(r'def get_tier\(user_id: int\) -> str:(.*?)\ndef ',
                  QUOTA, re.S)
    assert m, "get_tier 를 못 찾았다"
    body = m.group(1)
    assert "_tier_from_session" in body, "중앙 값을 먼저 보지 않는다"
    assert body.index("_tier_from_session") < body.index("_cached_tier"), \
        "로컬 캐시가 중앙보다 먼저 오면 강등이 반영되지 않는다"


def test_tier_does_not_leak_across_users():
    """
    다른 사용자의 등급을 물을 때 로그인한 사람의 세션 값을 쓰면
    그 사람 등급이 남의 것으로 새어 나간다.
    """
    m = re.search(r'def _tier_from_session\(user_id: int\)(.*?)\ndef ',
                  QUOTA, re.S)
    assert m, "_tier_from_session 을 못 찾았다"
    assert 'int(u.get("id") or 0) != int(user_id)' in m.group(1), \
        "세션 주인과 조회 대상이 같은지 확인하지 않는다"


def test_tier_change_writes_to_central_first():
    """중앙이 거절하면 로컬도 건드리면 안 된다 — 둘이 어긋나면 안 된다."""
    m = re.search(r'def api_quota_set_tier\(\):(.*?)\ndef ', SERVER, re.S)
    assert m, "api_quota_set_tier 를 못 찾았다"
    body = m.group(1)
    assert "admin_set_tier" in body, "중앙에 쓰지 않는다"
    assert body.index("admin_set_tier") < body.index("set_tier(uid"), \
        "로컬을 먼저 쓰면 중앙이 거절해도 로컬만 바뀐다"
    assert "_invalidate_user_cache" in body, \
        "신원 캐시를 안 비우면 등급 변경이 60초 동안 안 보인다"


# ── v4.0.0 · 로고가 밝은 배경에서 사라졌다 ──────────────────
def test_light_background_is_detected_by_luminance_not_theme_name():
    """
    테마 이름 목록으로 판정하면 custom 테마(사용자가 --bg 를 아무 색으로나
    정할 수 있다)에서 똑같이 샌다. 실제 밝기를 재야 한다.
    """
    assert "light-bg" in INDEX
    assert "function refreshLightBg()" in INDEX
    m = re.search(r'function refreshLightBg\(\)\{(.*?)\n\}', INDEX, re.S)
    assert m and "0.299" in m.group(1), "밝기 계산이 사라졌다"


def test_logo_has_a_light_background_animation():
    """
    정적 filter 규칙만으로는 부족하다. 등장 애니메이션이 매 키프레임에서
    filter 를 덮어써서, 로고가 나타나는 1초 동안 여전히 안 보였다.
    """
    assert "logoFocusLight" in INDEX, "밝은 배경용 키프레임이 사라졌다"
    assert re.search(r'@keyframes logoFocusLight\{[^}]*invert\(1\)', INDEX, re.S)
