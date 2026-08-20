# -*- coding: utf-8 -*-
"""
자동 업데이트
=============
GitHub Releases 를 보고 새 버전이 있으면 알리고, 사용자가 동의하면
받아서 교체한다.

절대 원칙 — 사용자 데이터는 건드리지 않는다
-------------------------------------------
``.data/`` 에는 API 키 · 계정 DB · 위젯 배치 · 보고서 보관함 · 분석
이력이 들어 있다. 이 폴더는 exe 바로 옆에 있으므로, 앱 폴더를 통째로
갈아 끼우는 방식이면 설정이 날아간다.

그래서 교체 대상을 **프로그램 파일만** 으로 한정한다.

    Plutus/
    ├── Plutus.exe      ← 교체
    ├── _internal/      ← 교체
    └── .data/          ← 손대지 않음 (이름조차 스크립트에 안 넣는다)

실행 중인 exe 는 덮어쓸 수 없다
-------------------------------
윈도우는 실행 중인 파일을 잠근다. 그래서 앱 안에서 직접 바꿀 수 없고,
**앱이 종료된 뒤에** 동작하는 외부 스크립트가 필요하다. 순서는

    1. 새 버전을 받아 .data/update/staged/ 에 풀어 둔다
    2. 교체 스크립트를 쓰고 detached 로 띄운다
    3. 앱이 종료된다
    4. 스크립트가 PID 소멸을 기다렸다가 기존 파일을 .old 로 밀어 두고
       새 파일을 넣은 뒤 앱을 다시 띄운다
    5. 새 앱이 정상 기동하면 .old 를 지운다. 실패하면 되돌린다

소스 체크아웃으로 돌 때
-----------------------
`git pull` 이 정답이고, 자동으로 돌리지 않는다. 로컬 수정본을 덮어쓸 수
있어서 사용자가 직접 판단해야 한다. 확인 결과만 알리고 명령을 안내한다.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.request import Request, urlopen

from engine.paths import APP_ROOT, DATA_DIR

try:
    from version import REPO_URL, __version__ as CURRENT
except Exception:                                    # pragma: no cover
    REPO_URL, CURRENT = "", "0.0.0"

UPDATE_DIR = DATA_DIR / "update"
STAGE_DIR = UPDATE_DIR / "staged"
_UA = {"User-Agent": "Plutus-Updater", "Accept": "application/vnd.github+json"}


# ── 버전 비교 ────────────────────────────────────────────────

def _parse(v: str) -> Tuple[int, ...]:
    """'v3.1.0' / '3.1' → (3,1,0). 숫자가 아닌 꼬리표는 버린다."""
    nums = re.findall(r"\d+", str(v or ""))
    out = [int(n) for n in nums[:3]]
    while len(out) < 3:
        out.append(0)
    return tuple(out)


def is_newer(latest: str, current: str) -> bool:
    return _parse(latest) > _parse(current)


def _repo_slug() -> str:
    m = re.search(r"github\.com/([^/]+/[^/.]+)", REPO_URL or "")
    return m.group(1) if m else ""


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


# ── 확인 ─────────────────────────────────────────────────────

def check(timeout: float = 10.0) -> Dict[str, Any]:
    """
    최신 릴리스를 조회한다. **예외를 밖으로 내보내지 않는다** —
    업데이트 확인 실패가 앱 실행을 막을 이유가 없다.
    """
    slug = _repo_slug()
    if not slug:
        return {"ok": False, "error": "저장소 주소를 알 수 없습니다.",
                "current": CURRENT}
    url = f"https://api.github.com/repos/{slug}/releases/latest"
    try:
        with urlopen(Request(url, headers=_UA), timeout=timeout) as r:
            rel = json.loads(r.read().decode("utf-8"))
    except Exception as e:
        msg = f"{type(e).__name__}"
        # 릴리스가 하나도 없으면 404 다 — 오류가 아니라 '아직 없음'
        if "404" in str(e):
            return {"ok": True, "newer": False, "current": CURRENT,
                    "latest": None, "note": "게시된 릴리스가 없습니다."}
        return {"ok": False, "error": f"확인 실패: {msg}", "current": CURRENT}

    tag = str(rel.get("tag_name") or "")
    assets = rel.get("assets") or []
    # 윈도우 배포본 — 이름에 win 이 들어간 zip 을 우선한다
    asset = None
    for a in assets:
        n = (a.get("name") or "").lower()
        if n.endswith(".zip") and ("win" in n or "plutus" in n):
            asset = a
            break
    if asset is None:
        for a in assets:
            if (a.get("name") or "").lower().endswith(".zip"):
                asset = a
                break

    return {
        "ok": True,
        "current": CURRENT,
        "latest": tag or None,
        "newer": bool(tag) and is_newer(tag, CURRENT),
        "name": rel.get("name") or tag,
        "notes": (rel.get("body") or "")[:4000],
        "published": (rel.get("published_at") or "")[:10],
        "html_url": rel.get("html_url") or "",
        "asset": ({"name": asset.get("name"),
                   "size": asset.get("size"),
                   "url": asset.get("browser_download_url")}
                  if asset else None),
        "frozen": is_frozen(),
    }


# ── 내려받기 · 검증 ──────────────────────────────────────────

def download(url: str, size_hint: int = 0,
             progress=None, timeout: float = 60.0) -> Path:
    """릴리스 자산을 .data/update/ 로 받는다. 경로를 돌려준다."""
    UPDATE_DIR.mkdir(parents=True, exist_ok=True)
    dest = UPDATE_DIR / "download.zip"
    if dest.exists():
        dest.unlink()
    got = 0
    with urlopen(Request(url, headers={"User-Agent": "Plutus-Updater"}),
                 timeout=timeout) as r, open(dest, "wb") as f:
        total = int(r.headers.get("Content-Length") or size_hint or 0)
        while True:
            chunk = r.read(262144)
            if not chunk:
                break
            f.write(chunk)
            got += len(chunk)
            if progress and total:
                progress(got, total)
    return dest


def verify_and_stage(zip_path: Path) -> Dict[str, Any]:
    """
    zip 을 열어 **실행 파일이 실제로 들어 있는지** 확인한 뒤 푼다.

    검증 없이 교체하면, 받다 만 파일이나 엉뚱한 zip 으로 앱을 망가뜨린다.
    """
    # 임의의 최소 크기(예전엔 1024B)로 자르면 진짜 원인을 가린다 —
    # 내용이 잘못된 zip 도 "비어 있습니다" 로 보고돼 엉뚱한 곳을 고치게
    # 된다. 존재/0바이트만 보고, 나머지는 구조로 판정한다.
    if not zip_path.exists():
        return {"ok": False, "error": "내려받은 파일이 없습니다."}
    if zip_path.stat().st_size == 0:
        return {"ok": False, "error": "내려받은 파일이 비어 있습니다(0바이트)."}
    try:
        with zipfile.ZipFile(zip_path) as z:
            bad = z.testzip()
            if bad:
                return {"ok": False, "error": f"압축이 손상됐습니다: {bad}"}
            names = z.namelist()
            exe = [n for n in names if n.lower().endswith("plutus.exe")]
            if not exe:
                return {"ok": False,
                        "error": "zip 안에 Plutus.exe 가 없습니다."}
            if STAGE_DIR.exists():
                shutil.rmtree(STAGE_DIR, ignore_errors=True)
            STAGE_DIR.mkdir(parents=True, exist_ok=True)
            z.extractall(STAGE_DIR)
    except zipfile.BadZipFile:
        return {"ok": False, "error": "zip 형식이 아닙니다."}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}

    root = _find_payload_root(STAGE_DIR)
    if root is None:
        return {"ok": False, "error": "압축 안에서 프로그램 폴더를 찾지 못했습니다."}

    # 두 번째 방어선 — 배포본에 .data 가 섞여 들어왔으면 **여기서 지운다.**
    # 교체 스크립트도 건너뛰게 돼 있지만, 애초에 스테이징에 남겨 두지
    # 않는 편이 안전하다. 사용자 키·계정이 걸린 문제라 두 겹으로 막는다.
    stray = root / ".data"
    dropped = False
    if stray.exists():
        shutil.rmtree(stray, ignore_errors=True)
        dropped = True

    return {"ok": True, "root": str(root),
            "stripped_data": dropped,
            "files": sum(1 for _ in root.rglob("*") if _.is_file())}


def _find_payload_root(base: Path) -> Optional[Path]:
    """Plutus.exe 가 있는 디렉터리를 찾는다 (zip 이 한 겹 더 싸도 대응)."""
    for p in base.rglob("Plutus.exe"):
        return p.parent
    return None


# ── 교체 ─────────────────────────────────────────────────────

_PS_TEMPLATE = r"""
# Plutus 업데이트 적용 스크립트 (자동 생성)
# .data\ 는 이 스크립트 어디에도 등장하지 않는다 — 손대지 않기 위해서다.
$ErrorActionPreference = 'Stop'
$app  = '{APP}'
$new  = '{NEW}'
$exe  = Join-Path $app 'Plutus.exe'
$procId = {PID}
$log  = '{LOG}'

function L($m) {{ "$(Get-Date -f 'HH:mm:ss')  $m" | Out-File -Append -Encoding utf8 $log }}

L "대기: PID $procId 종료"
for ($i = 0; $i -lt 60; $i++) {{
    if (-not (Get-Process -Id $procId -ErrorAction SilentlyContinue)) {{ break }}
    Start-Sleep -Milliseconds 500
}}
Start-Sleep -Milliseconds 800

$bakExe = "$exe.old"
$intr   = Join-Path $app '_internal'
$bakInt = "$intr.old"

try {{
    L "백업"
    if (Test-Path $bakExe) {{ Remove-Item -Force $bakExe }}
    if (Test-Path $bakInt) {{ Remove-Item -Recurse -Force $bakInt }}
    if (Test-Path $exe)  {{ Rename-Item $exe  'Plutus.exe.old' }}
    if (Test-Path $intr) {{ Rename-Item $intr '_internal.old' }}

    L "새 파일 복사 (.data 는 제외)"
    # `Copy-Item $new\*` 는 점으로 시작하는 폴더도 포함한다. 릴리스 zip 에
    # 실수로 .data 가 섞여 들어오면 사용자의 키·계정 DB를 덮어쓴다.
    # 그래서 최상위 항목을 하나씩 돌며 .data 를 명시적으로 건너뛴다.
    Get-ChildItem -Force -LiteralPath $new | ForEach-Object {{
        if ($_.Name -eq '.data') {{ L "  건너뜀: .data"; return }}
        Copy-Item -LiteralPath $_.FullName -Destination $app -Recurse -Force
    }}

    L "기동"
    Start-Process -FilePath $exe -WorkingDirectory $app
    Start-Sleep -Seconds 6
    if (Get-Process -Name 'Plutus' -ErrorAction SilentlyContinue) {{
        L "성공 — 백업 삭제"
        if (Test-Path $bakExe) {{ Remove-Item -Force $bakExe }}
        if (Test-Path $bakInt) {{ Remove-Item -Recurse -Force $bakInt }}
    }} else {{
        throw "새 버전이 기동하지 않음"
    }}
}} catch {{
    L "실패: $_  → 되돌림"
    if (Test-Path $exe)    {{ Remove-Item -Force -ErrorAction SilentlyContinue $exe }}
    if (Test-Path $intr)   {{ Remove-Item -Recurse -Force -ErrorAction SilentlyContinue $intr }}
    if (Test-Path $bakExe) {{ Rename-Item $bakExe 'Plutus.exe' }}
    if (Test-Path $bakInt) {{ Rename-Item $bakInt '_internal' }}
    Start-Process -FilePath $exe -WorkingDirectory $app
}}
"""


def apply_staged() -> Dict[str, Any]:
    """
    교체 스크립트를 만들어 detached 로 띄운다. 호출 직후 앱은 종료해야
    한다 — 실행 중인 exe 는 윈도우가 잠그기 때문이다.
    """
    if not is_frozen():
        return {"ok": False,
                "error": "소스 실행 중에는 자동 교체를 하지 않습니다. "
                         "`git pull` 로 갱신하세요."}
    root = _find_payload_root(STAGE_DIR)
    if root is None:
        return {"ok": False, "error": "준비된 업데이트가 없습니다."}

    UPDATE_DIR.mkdir(parents=True, exist_ok=True)
    ps = UPDATE_DIR / "apply.ps1"
    script = _PS_TEMPLATE.format(APP=str(APP_ROOT), NEW=str(root),
                                 PID=os.getpid(),
                                 LOG=str(UPDATE_DIR / "apply.log"))
    # **BOM 이 있어야 한다.** Windows PowerShell 5.1 은 BOM 없는 .ps1 을
    # 시스템 코드페이지(한국어 윈도우면 cp949)로 읽는다. UTF-8 로 저장한
    # 한글 문자열이 깨지면서 따옴표가 닫히지 않아 **파서 오류**가 나고,
    # 스크립트가 통째로 실행되지 않는다 — 로그조차 남지 않아 원인을
    # 찾기 어렵다. 실제로 그렇게 실패했다.
    ps.write_text(script, encoding="utf-8-sig")

    flags = 0
    if hasattr(subprocess, "CREATE_NO_WINDOW"):
        flags |= subprocess.CREATE_NO_WINDOW
    if hasattr(subprocess, "DETACHED_PROCESS"):
        flags |= subprocess.DETACHED_PROCESS
    try:
        subprocess.Popen(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
             "-File", str(ps)],
            creationflags=flags, close_fds=True)
    except Exception as e:
        return {"ok": False, "error": f"교체 스크립트 실행 실패: {e}"}
    return {"ok": True, "script": str(ps),
            "note": "앱이 종료된 뒤 교체가 진행되고 자동으로 다시 켜집니다."}


def cleanup() -> None:
    """받아 둔 임시 파일 정리 — 실패해도 무시한다."""
    for p in (UPDATE_DIR / "download.zip", STAGE_DIR):
        try:
            if p.is_dir():
                shutil.rmtree(p, ignore_errors=True)
            elif p.exists():
                p.unlink()
        except Exception:
            pass
