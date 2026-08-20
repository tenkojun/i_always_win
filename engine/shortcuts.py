# -*- coding: utf-8 -*-
"""
바로가기 등록 (Windows)
=======================
바탕화면 바로가기와 **시작 메뉴 등록**을 만든다.

왜 설치 프로그램이 아니라 앱 안에서 하는가
------------------------------------------
Plutus 는 압축만 풀면 도는 portable 배포다. 설치 과정이 없으니 설치
프로그램이 해 줄 일(바로가기·시작 메뉴 등록)을 아무도 안 한다. 그래서
**첫 실행 때 한 번 물어보고** 사용자가 원하면 그때 만든다.

윈도우 검색에 뜨게 하려면
-------------------------
바탕화면 바로가기만으로는 검색에 안 뜬다. 검색 색인은
`%APPDATA%\\Microsoft\\Windows\\Start Menu\\Programs` 를 본다.
그래서 **시작 메뉴 쪽이 본체**고 바탕화면은 선택이다.

.lnk 를 만드는 방법
-------------------
pywin32 같은 의존성을 새로 들이지 않는다. Windows 에 원래 있는
WScript.Shell COM 객체를 PowerShell 로 호출한다. 추가 패키지 0개.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict

from engine.paths import DATA_DIR

try:
    from version import APP_NAME, APP_TAGLINE
except Exception:                                   # pragma: no cover
    APP_NAME, APP_TAGLINE = "Plutus", ""

_ASKED_FLAG = DATA_DIR / "shortcuts_asked"


def is_windows() -> bool:
    return os.name == "nt"


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def _target() -> Path | None:
    """
    바로가기가 가리킬 실행 파일. 정할 수 없으면 None.

    소스 실행 중에는 `sys.argv[0]` 이 런처 스크립트가 아닐 수 있다
    (`python -` 이면 `-`, `-m` 이면 모듈 경로). 그런 값으로 바로가기를
    만들면 클릭해도 아무 일이 안 일어나는 죽은 링크가 생긴다.
    **모르면 만들지 않는다.**
    """
    if is_frozen():
        return Path(sys.executable).resolve()
    try:
        cand = Path(sys.argv[0]).resolve()
    except Exception:
        return None
    if cand.suffix.lower() in (".py", ".exe") and cand.exists():
        return cand
    return None


def _desktop() -> Path:
    return Path(os.path.expanduser("~")) / "Desktop"


def _start_menu() -> Path:
    base = os.environ.get("APPDATA") or os.path.expanduser("~/AppData/Roaming")
    return Path(base) / "Microsoft" / "Windows" / "Start Menu" / "Programs"


def already_asked() -> bool:
    return _ASKED_FLAG.exists()


def mark_asked() -> None:
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        _ASKED_FLAG.write_text("1", encoding="utf-8")
    except Exception:
        pass


def status() -> Dict[str, Any]:
    """지금 바로가기가 있는지. 화면이 물어볼지 말지 판단하는 근거."""
    d = _desktop() / f"{APP_NAME}.lnk"
    m = _start_menu() / f"{APP_NAME}.lnk"
    return {
        "supported": is_windows(),
        "frozen": is_frozen(),
        "asked": already_asked(),
        "desktop": d.exists(),
        "start_menu": m.exists(),
        "desktop_path": str(d),
        "start_menu_path": str(m),
        "target": (str(_target()) if _target() else None),
    }


def _make_lnk(link: Path, target: Path, icon: Path | None,
              workdir: Path, desc: str) -> None:
    """
    WScript.Shell 로 .lnk 생성. PowerShell 스크립트는 **BOM 으로 저장**한다
    — BOM 이 없으면 PowerShell 5.1 이 시스템 코드페이지로 읽어 한글 경로와
    설명이 깨진다(업데이터에서 같은 문제를 이미 겪었다).
    """
    link.parent.mkdir(parents=True, exist_ok=True)
    ico = str(icon) if icon and icon.exists() else str(target)
    ps = f"""
$ws = New-Object -ComObject WScript.Shell
$s = $ws.CreateShortcut('{link}')
$s.TargetPath = '{target}'
$s.WorkingDirectory = '{workdir}'
$s.IconLocation = '{ico}'
$s.Description = '{desc}'
$s.Save()
"""
    tmp = DATA_DIR / "_mklnk.ps1"
    tmp.write_text(ps, encoding="utf-8-sig")
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                    "-File", str(tmp)],
                   check=True, capture_output=True, timeout=30,
                   creationflags=flags)
    try:
        tmp.unlink()
    except Exception:
        pass


def create(desktop: bool = True, start_menu: bool = True) -> Dict[str, Any]:
    """
    바로가기를 만든다. 실패해도 예외를 밖으로 내보내지 않는다 —
    바로가기 하나 때문에 앱이 죽을 이유가 없다.
    """
    if not is_windows():
        return {"ok": False, "error": "Windows 에서만 지원합니다."}

    target = _target()
    if target is None or not target.exists():
        return {"ok": False,
                "error": ("실행 파일 경로를 확인할 수 없습니다. "
                          "빌드된 Plutus.exe 로 실행한 뒤 다시 시도하세요.")}

    workdir = target.parent
    icon = workdir / "_internal" / "webapp" / "static" / "favicon.png"
    ico_win = workdir / "_internal" / "assets" / "app.ico"
    use_icon = ico_win if ico_win.exists() else (icon if icon.exists() else None)
    # em-dash(—)는 WScript.Shell 을 거치며 '?' 로 깨진다(COM 이 ANSI 로
    # 내려받는 구간이 있다). 한글은 멀쩡한데 이 문자만 상한다 — 하이픈으로.
    desc = f"{APP_NAME} - {APP_TAGLINE}" if APP_TAGLINE else APP_NAME

    made, failed = [], []
    jobs = []
    if desktop:
        jobs.append(("desktop", _desktop() / f"{APP_NAME}.lnk"))
    if start_menu:
        # 시작 메뉴에 들어가야 윈도우 검색에 뜬다 — 이쪽이 본체다
        jobs.append(("start_menu", _start_menu() / f"{APP_NAME}.lnk"))

    for kind, link in jobs:
        try:
            _make_lnk(link, target, use_icon, workdir, desc)
            made.append(kind)
        except Exception as e:
            failed.append({"kind": kind, "error": f"{type(e).__name__}: {e}"})

    mark_asked()
    return {"ok": bool(made), "created": made, "failed": failed,
            "note": ("시작 메뉴에 등록했습니다. 윈도우 검색에 반영되기까지 "
                     "잠시 걸릴 수 있습니다." if "start_menu" in made else "")}


def remove() -> Dict[str, Any]:
    """만든 바로가기를 지운다."""
    gone = []
    for p in (_desktop() / f"{APP_NAME}.lnk",
              _start_menu() / f"{APP_NAME}.lnk"):
        try:
            if p.exists():
                p.unlink()
                gone.append(str(p))
        except Exception:
            pass
    return {"ok": True, "removed": gone}
