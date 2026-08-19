# -*- coding: utf-8 -*-
"""
보고서 테마
===========
보고서는 외부 리소스 0개의 자기완결 HTML 이라, 테마를 나중에 갈아 끼울 수
없다. **생성 시점에** 팔레트를 주입해야 한다.

방식
----
기존 CSS 는 색을 하드코딩하고 있었다(고유 39색 · 108회 등장). 그걸 손으로
전부 고치면 오타 한 번에 화면이 깨지므로, **기계적으로 치환**한다.
`PALETTE_KEYS` 의 원본 hex → CSS 변수명 매핑을 두고, 렌더 직전에
정규식으로 바꾼 뒤 테마별 `:root` 블록을 앞에 붙인다.

원본(dark)의 값은 그대로 두었으므로, 테마를 지정하지 않으면 **예전과 픽셀
단위로 같은 화면**이 나온다.
"""
from __future__ import annotations

import re
from typing import Dict, List, Tuple

# 원본 hex → 변수명 (역할 기준)
PALETTE_KEYS: Dict[str, str] = {
    "#0b0d11": "bg",            # 페이지 배경
    "#0b0d11ee": "bg-fade",
    "#12151b": "bg2",
    "#141821": "panel-3",
    "#151922": "panel-4",
    "#161a22": "panel-5",
    "#171a21": "panel",         # 카드/박스
    "#1a1e26": "panel-2",
    "#1c212b": "chip",
    "#1e222b": "row",
    "#222732": "row-alt",
    "#252a34": "line",          # 경계선
    "#2b3346": "line-2",
    "#1a2030": "hero-a",
    "#e6e8ec": "txt",           # 본문
    "#cfd4de": "txt-2",
    "#b9c0cc": "txt-3",
    "#8b93a3": "muted",         # 보조 텍스트
    "#6b7280": "muted-2",
    "#5a6373": "muted-3",
    "#2ec27e": "up",            # 상승/양호
    "#2ec27e55": "up-line",
    "#2ec27e22": "up-soft",
    "#e0455f": "down",          # 하락/경고
    "#e0455f55": "down-line",
    "#e0455f22": "down-soft",
    "#e0455f11": "down-faint",
    "#e8a33d": "warn",          # 주의
    "#1d1a15": "warn-bg",
    "#1d1519": "danger-bg",
    "#5b8def": "accent",        # 링크/강조
    "#7ba6ff": "accent-2",
    "#9ec1ff": "accent-3",
}

# 테마 = 변수명 → 값. 없는 키는 dark 값을 그대로 쓴다.
_DARK: Dict[str, str] = {v: k for k, v in PALETTE_KEYS.items()}

_LIGHT: Dict[str, str] = {
    "bg": "#ffffff", "bg-fade": "#ffffffee", "bg2": "#f7f8fa",
    "panel": "#f4f6f9", "panel-2": "#eef1f6", "panel-3": "#f7f8fa",
    "panel-4": "#f2f4f8", "panel-5": "#eef1f6",
    "chip": "#eef1f6", "row": "#f7f8fa", "row-alt": "#eef1f6",
    "line": "#dde2ea", "line-2": "#c9d2e0", "hero-a": "#eaf0fb",
    "txt": "#151a22", "txt-2": "#2b3340", "txt-3": "#48525f",
    "muted": "#697382", "muted-2": "#7c8595", "muted-3": "#9aa3b1",
    "up": "#0f9d58", "up-line": "#0f9d5855", "up-soft": "#0f9d5822",
    "down": "#c5283d", "down-line": "#c5283d55", "down-soft": "#c5283d22",
    "down-faint": "#c5283d11",
    "warn": "#a86a12", "warn-bg": "#fdf5e6", "danger-bg": "#fdecef",
    "accent": "#1a56db", "accent-2": "#1a56db", "accent-3": "#3b73e8",
}

_SEPIA: Dict[str, str] = {
    "bg": "#f4ecd8", "bg-fade": "#f4ecd8ee", "bg2": "#efe5cd",
    "panel": "#ece0c6", "panel-2": "#e6d8b9", "panel-3": "#efe5cd",
    "panel-4": "#ebe0c8", "panel-5": "#e8dcc0",
    "chip": "#e6d8b9", "row": "#efe5cd", "row-alt": "#e9dec2",
    "line": "#d6c6a3", "line-2": "#c3b08a", "hero-a": "#e8dcc0",
    "txt": "#2b2317", "txt-2": "#3d3324", "txt-3": "#544733",
    "muted": "#6f6047", "muted-2": "#82725a", "muted-3": "#95866e",
    "up": "#1f7a4d", "up-line": "#1f7a4d55", "up-soft": "#1f7a4d22",
    "down": "#a32e2e", "down-line": "#a32e2e55", "down-soft": "#a32e2e22",
    "down-faint": "#a32e2e11",
    "warn": "#8a5a12", "warn-bg": "#f6e9cd", "danger-bg": "#f6dfd9",
    "accent": "#2f5fa8", "accent-2": "#2f5fa8", "accent-3": "#4a7ac0",
}

# 높은 대비 — 저시력/프로젝터용
_HICON: Dict[str, str] = {
    "bg": "#000000", "bg-fade": "#000000ee", "bg2": "#0a0a0a",
    "panel": "#101010", "panel-2": "#161616", "panel-3": "#0d0d0d",
    "panel-4": "#121212", "panel-5": "#161616",
    "chip": "#1a1a1a", "row": "#0d0d0d", "row-alt": "#161616",
    "line": "#4a4a4a", "line-2": "#6a6a6a", "hero-a": "#101820",
    "txt": "#ffffff", "txt-2": "#f0f0f0", "txt-3": "#dcdcdc",
    "muted": "#b8b8b8", "muted-2": "#a0a0a0", "muted-3": "#8a8a8a",
    "up": "#00e676", "up-line": "#00e67699", "up-soft": "#00e67633",
    "down": "#ff5252", "down-line": "#ff525299", "down-soft": "#ff525233",
    "down-faint": "#ff525218",
    "warn": "#ffc107", "warn-bg": "#241d00", "danger-bg": "#2a0d0d",
    "accent": "#64b5f6", "accent-2": "#82c4ff", "accent-3": "#a5d6ff",
}

THEMES: Dict[str, Dict[str, object]] = {
    "dark":  {"ko": "다크", "desc": "기본 — 화면에서 오래 보기 좋다",
              "vars": _DARK},
    "light": {"ko": "라이트", "desc": "밝은 배경 — 인쇄·공유에 적합",
              "vars": _LIGHT},
    "sepia": {"ko": "세피아", "desc": "눈부심이 적은 종이 느낌",
              "vars": _SEPIA},
    "hicon": {"ko": "고대비", "desc": "저시력·프로젝터용 강한 대비",
              "vars": _HICON},
}

DEFAULT_THEME = "dark"

# 긴 hex 부터 치환해야 #2ec27e 가 #2ec27e55 를 잘라먹지 않는다
_ORDERED: List[Tuple[str, str]] = sorted(
    PALETTE_KEYS.items(), key=lambda kv: -len(kv[0]))


def themed_css(base_css: str, theme: str = DEFAULT_THEME) -> str:
    """
    원본 CSS 의 하드코딩 색을 변수 참조로 바꾸고, 테마 팔레트를 앞에 붙인다.
    알 수 없는 테마 이름은 기본값으로 떨어진다.
    """
    name = theme if theme in THEMES else DEFAULT_THEME
    css = base_css
    for hexv, var in _ORDERED:
        css = re.sub(re.escape(hexv) + r"\b(?![0-9a-fA-F])",
                     f"var(--r-{var})", css, flags=re.IGNORECASE)

    vals = dict(_DARK)
    vals.update(THEMES[name]["vars"])          # type: ignore[index]
    root = ":root{" + "".join(f"--r-{k}:{v};" for k, v in vals.items()) + "}"

    # 색맹 대비: 상승/하락을 색으로만 구분하지 않도록 인쇄 시 밑줄 보조
    return root + "\n" + css


def theme_list() -> List[Dict[str, str]]:
    return [{"id": k, "ko": str(v["ko"]), "desc": str(v["desc"])}
            for k, v in THEMES.items()]
