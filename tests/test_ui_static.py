# -*- coding: utf-8 -*-
"""
UI 정적 검사 — 브라우저 없이 CI 에서 돈다.

UI 가 11,000줄짜리 단일 HTML 이고 JS 가 인라인이라, 편집기도 린터도
그 안을 봐 주지 않는다. 오타 하나면 스크립트 블록 전체가 파싱되지 않아
함수가 하나도 정의되지 않고 부팅 화면에서 멈춘다 — 콘솔을 열기 전까지
이유를 알 수 없다. 실제로 그렇게 앱을 죽인 적이 있다(v4.0.0 작업 중).
"""
from __future__ import annotations

import io
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
INDEX = ROOT / "webapp" / "static" / "index.html"
SRC = io.open(INDEX, encoding="utf-8").read()
LINES = SRC.split("\n")

THEMES = ["cyber", "dark", "white", "edex", "snow", "sakura", "autumn", "ocean"]
# 모든 테마가 반드시 정의해야 하는 변수. 하나라도 빠지면 그 테마에서
# :root(cyber) 값으로 흘러내려 색이 뒤섞인다.
REQUIRED_VARS = ["--bg", "--panel", "--panel2", "--line", "--cyan", "--amber",
                 "--up", "--down", "--txt", "--txt-dim", "--txt-mute",
                 "--hover", "--on-cyan", "--on-amber", "--scrim", "--scrim-soft"]


def _theme_blocks():
    """테마 이름 → 그 블록의 본문."""
    out = {}
    m = re.search(r':root\{(.*?)\n  \}', SRC, re.S)
    if m:
        out["cyber"] = m.group(1)
    for m in re.finditer(r':root\[data-theme="(\w+)"\]\{(.*?)\n  \}', SRC, re.S):
        out.setdefault(m.group(1), m.group(2))
    return out


def _body_range():
    """테마 정의 블록의 줄 범위 — 거기서는 색을 써도 정상이다."""
    s = next(i for i, l in enumerate(LINES) if ':root{' in l)
    e = next(i for i, l in enumerate(LINES) if 'data-theme="ocean"' in l) + 26
    return s, e


# ── 구문 ─────────────────────────────────────────────────────
@pytest.mark.skipif(shutil.which("node") is None, reason="Node 없음")
def test_inline_javascript_parses():
    """
    인라인 <script> 가 전부 파싱되는가.

    여기가 깨지면 앱이 부팅 화면에서 멈춘다. 가장 비싼 종류의 실수라
    가장 먼저 본다.
    """
    blocks = re.findall(
        r"<script(?![^>]*\bsrc=)(?![^>]*\btype=)[^>]*>(.*?)</script>",
        SRC, re.S | re.I)
    assert blocks, "인라인 스크립트를 하나도 못 찾았다 — 검사기가 무의미해진다"
    for i, code in enumerate(blocks, 1):
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False,
                                         encoding="utf-8") as f:
            f.write(code)
            tmp = f.name
        try:
            r = subprocess.run(["node", "--check", tmp], capture_output=True,
                               text=True, encoding="utf-8", errors="replace",
                               timeout=120)
        finally:
            Path(tmp).unlink(missing_ok=True)
        assert r.returncode == 0, (
            f"스크립트 블록 {i} 구문 오류:\n{(r.stderr or '')[:600]}")


# ── 테마 완전성 ──────────────────────────────────────────────
def test_every_theme_is_defined():
    got = set(_theme_blocks())
    missing = [t for t in THEMES if t not in got]
    assert not missing, f"테마 정의가 없다: {missing}"


@pytest.mark.parametrize("theme", THEMES)
def test_theme_defines_all_required_vars(theme):
    """
    테마마다 필수 변수를 전부 정의하는가.

    하나라도 빠지면 그 값만 :root(cyber) 에서 상속돼, 밝은 테마에
    사이버 색이 섞인다. 눈으로는 "가끔 이상한 색" 으로만 보여서
    찾기 어렵다.
    """
    body = _theme_blocks()[theme]
    missing = [v for v in REQUIRED_VARS
               if not re.search(rf'{re.escape(v)}\s*:', body)]
    assert not missing, f"{theme} 테마에 없는 변수: {missing}"


@pytest.mark.parametrize("theme", THEMES)
def test_accent_text_is_readable_on_accent_background(theme):
    """
    강조색 위의 글자가 읽히는가 (WCAG AA 4.5).

    버튼 배경이 --cyan 인데 글자를 하드코딩하면 테마가 바뀔 때 대비가
    뒤집힌다. white 에서 실제로 어두운 배경에 어두운 글자가 됐다(1.14).
    """
    body = _theme_blocks()[theme]

    def val(name):
        m = re.search(rf'{re.escape(name)}\s*:\s*(#[0-9a-fA-F]{{3,8}})', body)
        return m.group(1) if m else None

    def lum(h):
        h = h.lstrip("#")
        if len(h) == 3:
            h = "".join(c * 2 for c in h)
        n = int(h[:6], 16)
        ch = [((n >> 16) & 255), ((n >> 8) & 255), (n & 255)]
        f = [(v / 255) / 12.92 if v / 255 <= 0.03928
             else ((v / 255 + 0.055) / 1.055) ** 2.4 for v in ch]
        return 0.2126 * f[0] + 0.7152 * f[1] + 0.0722 * f[2]

    def ratio(a, b):
        la, lb = lum(a), lum(b)
        return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)

    for bg_var, fg_var in (("--cyan", "--on-cyan"), ("--amber", "--on-amber")):
        bg, fg = val(bg_var), val(fg_var)
        assert bg and fg, f"{theme}: {bg_var}/{fg_var} 를 못 찾았다"
        cr = ratio(bg, fg)
        assert cr >= 4.5, (
            f"{theme}: {fg} on {bg} 명암비 {cr:.2f} (AA 기준 4.5)")


# ── 하드코딩 색 ──────────────────────────────────────────────
def test_no_hardcoded_dark_backgrounds():
    """
    테마 정의 밖에서 어두운 배경을 박아 쓰지 않는가.

    이게 늘어나면 밝은 테마에서 검은 판이 남는다 — 어제 로그인
    입력창이 그랬다. 지금 남은 것들은 테마 미리보기 스와치처럼
    의도적인 것뿐이라, 여기서 더 늘지 않게 상한을 건다.
    """
    s, e = _body_range()
    HEX = re.compile(r'#(?:[0-9a-fA-F]{6}|[0-9a-fA-F]{3})(?![0-9a-zA-Z_-])')
    PROP = re.compile(r'(?:^|[;{])\s*(background|background-color)\s*:\s*[^;{}]*$')
    bad = []
    for i, l in enumerate(LINES):
        if s <= i <= e:
            continue
        for m in HEX.finditer(l):
            if not PROP.search(l[:m.start()]):
                continue
            h = m.group(0)[1:]
            if len(h) == 3:
                h = "".join(c * 2 for c in h)
            n = int(h, 16)
            if (0.299 * ((n >> 16) & 255) + 0.587 * ((n >> 8) & 255)
                    + 0.114 * (n & 255)) / 255 < 0.30:
                bad.append(f"{i+1}: {m.group(0)}")
    assert len(bad) <= 9, (
        f"하드코딩된 어두운 배경이 {len(bad)}건으로 늘었다 (상한 9):\n"
        + "\n".join(bad[:15]))


# ── 이스케이프 ───────────────────────────────────────────────
def test_single_escape_implementation():
    """
    이스케이프 구현이 하나인가.

    esc / _escHtml / _histEsc 로 나뉘어 있었고 그중 _histEsc 가
    작은따옴표를 빼먹은 채 40곳에서 쓰였다. 셋으로 나뉘면 그중 하나만
    약해도 그 경로로 샌다.
    """
    assert "function _histEsc(s){ return esc(s); }" in SRC, \
        "_histEsc 가 다시 자체 구현을 갖게 됐다"
    assert "function _escHtml(s){ return esc(s); }" in SRC, \
        "_escHtml 가 다시 자체 구현을 갖게 됐다"


def test_escape_covers_single_quote():
    """작은따옴표를 안 막으면 href='...' 같은 속성에서 빠져나갈 수 있다."""
    m = re.search(r'function esc\(s\)\{(.*?)\n\}', SRC, re.S)
    assert m, "esc() 정의를 못 찾았다"
    body = m.group(1)
    for ch in ("&amp;", "&lt;", "&gt;", "&quot;", "&#39;"):
        assert ch in body, f"esc() 가 {ch} 를 안 만든다"


def test_no_unescaped_server_data_in_admin_rows():
    """
    관리자 패널이 닉네임/유저명을 이스케이프하는가.

    가입할 때 넣은 태그가 관리자 브라우저에서 실행되던 자리다.
    승인 대기 목록은 관리자가 반드시 여는 화면이라 노출이 확실했다.
    """
    assert "${esc(u.nickname)}" in SRC, "닉네임이 다시 날것으로 들어간다"
    assert "${esc(u.username)}" in SRC, "유저명이 다시 날것으로 들어간다"


def test_iframe_src_is_not_string_concatenated():
    """
    iframe src 를 문자열로 조립하지 않는가.

    프로퍼티 대입에는 HTML 파싱이 없어서 속성 탈출이 성립하지 않는다.
    """
    assert not re.search(r'innerHTML\s*=\s*[`\'"]<iframe[^`\'"]*src="\$\{', SRC), \
        "iframe 을 다시 문자열로 조립하고 있다"
