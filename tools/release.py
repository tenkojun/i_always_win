# -*- coding: utf-8 -*-
"""
릴리스 발행 (현재 버전)
=======================
실행:  python tools/release.py [--build] [--dry-run]

`version.py` 의 현재 버전으로 태그를 만들고, CHANGELOG 해당 절을
노트로 붙여 GitHub 릴리스를 발행한다. `--build` 를 주면 EXE 부터 굽는다.

    python tools/release.py --build      # 빌드 → 포장 → 태그 → 발행
    python tools/release.py              # 이미 dist/ 가 있을 때

안전장치
--------
- **`.data/` 를 포장 전에 지운다.** 한 번이라도 실행한 dist 에는
  API 키와 계정 DB가 들어 있다. 공개 자산에 그게 섞이면 끝이다.
- 포장 후 zip 을 **업데이터의 검증 로직에 통과시켜 본다.** 릴리스를
  올린 뒤에 형식 문제를 발견하면 받아 가는 중에 고쳐야 한다.
- CHANGELOG 에 해당 버전 절이 없으면 **중단한다.** 노트 없는 릴리스는
  나중에 아무도 무슨 변경인지 모른다.
"""
from __future__ import annotations

import io
import os
import re
import shutil
import subprocess
import sys

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

GH_CANDIDATES = [
    r"C:\Program Files\GitHub CLI\gh.exe",
    r"C:\Program Files (x86)\GitHub CLI\gh.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Programs\GitHub CLI\gh.exe"),
    "gh",
]
ZIP = os.path.join("dist", "Plutus-win-x64.zip")
APP = os.path.join("dist", "Plutus")


def _gh() -> str:
    for c in GH_CANDIDATES:
        if c == "gh":
            try:
                subprocess.run([c, "--version"], capture_output=True, check=True)
                return c
            except Exception:
                continue
        if os.path.exists(c):
            return c
    raise SystemExit("[!] gh CLI 를 찾지 못했습니다.  winget install GitHub.cli")


def sh(*a: str, check: bool = True) -> str:
    r = subprocess.run(a, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if check and r.returncode != 0:
        raise SystemExit(f"[!] 실패: {' '.join(a[:3])}\n{r.stderr.strip()[:400]}")
    return (r.stdout or "").strip()


def current_version() -> str:
    s = io.open("version.py", encoding="utf-8").read()
    return re.search(r'__version__\s*=\s*"([^"]+)"', s).group(1)


def notes_for(ver: str) -> str:
    s = io.open("CHANGELOG.md", encoding="utf-8").read()
    m = re.search(r"^## \[" + re.escape(ver) + r"\][^\n]*\n(.*?)(?=^## \[|\Z)",
                  s, re.S | re.M)
    if not m:
        return ""
    return re.sub(r"\n---\s*$", "", m.group(1).strip()).strip()


def build() -> None:
    print("빌드 중…")
    if os.path.exists("version_info.txt"):
        os.remove("version_info.txt")
    sh(sys.executable, os.path.join("tools", "make_version_info.py"))
    for d in ("build", "dist"):
        shutil.rmtree(d, ignore_errors=True)
    sh(sys.executable, "-m", "PyInstaller", "app.spec", "--noconfirm")


def package() -> None:
    if not os.path.isdir(APP):
        raise SystemExit(f"[!] {APP} 가 없습니다. --build 를 주거나 먼저 빌드하세요.")
    # 실행 흔적(.data)에는 키와 계정 DB가 들어 있다 — 공개 자산에 넣으면 안 된다
    stray = os.path.join(APP, ".data")
    if os.path.exists(stray):
        shutil.rmtree(stray, ignore_errors=True)
        print("  .data 제거 (키·계정 보호)")
    # 진단 도구를 exe 옆에 같이 넣는다. 받는 PC 에는 Python 이 없으니
    # PowerShell 판만 넣는다 — 문제 생겼을 때 이게 유일한 손전등이다.
    src = os.path.join("tools", "diagnose.ps1")
    if os.path.exists(src):
        shutil.copy2(src, os.path.join(APP, "진단.ps1"))
        print("  진단.ps1 동봉")
    if os.path.exists(ZIP):
        os.remove(ZIP)
    print("포장 중…")
    sh("powershell", "-NoProfile", "-Command",
       f"Compress-Archive -Path '{APP}' -DestinationPath '{ZIP}' -Force")
    print(f"  {ZIP}  {os.path.getsize(ZIP):,} bytes")


def verify() -> None:
    """업데이터가 실제로 받아들이는 형태인지 확인한다."""
    sys.path.insert(0, os.getcwd())
    import tempfile
    from pathlib import Path
    import engine.updater as U
    tmp = Path(tempfile.mkdtemp(prefix="plutus_rel_"))
    U.UPDATE_DIR, U.STAGE_DIR = tmp / "u", tmp / "u" / "staged"
    r = U.verify_and_stage(Path(ZIP))
    shutil.rmtree(tmp, ignore_errors=True)
    if not r.get("ok"):
        raise SystemExit(f"[!] 업데이터 검증 실패: {r.get('error')}")
    if r.get("stripped_data"):
        raise SystemExit("[!] zip 에 .data 가 들어 있었습니다. 포장을 다시 하세요.")
    print(f"  업데이터 검증 통과 · 파일 {r['files']:,}개")


def main() -> int:
    if not os.path.exists("version.py"):
        print("[!] 저장소 루트에서 실행하세요")
        return 1
    dry = "--dry-run" in sys.argv
    ver = current_version()
    tag = f"v{ver}"
    note = notes_for(ver)
    if not note:
        raise SystemExit(f"[!] CHANGELOG 에 [{ver}] 절이 없습니다. 먼저 작성하세요.")

    gh = _gh()
    if tag in sh(gh, "release", "list", "--limit", "200", check=False):
        raise SystemExit(f"[!] {tag} 릴리스가 이미 있습니다.")

    dirty = sh("git", "status", "--porcelain")
    if dirty:
        print("[!] 커밋되지 않은 변경이 있습니다:")
        print("   ", dirty.split("\n")[0][:70], "…")
        if not dry:
            raise SystemExit("    먼저 커밋하세요.")

    print(f"{tag} · 노트 {len(note)}자")
    if "--build" in sys.argv:
        build()
    package()
    verify()

    if dry:
        print("\n[DRY RUN] 여기까지. 실제 발행은 --dry-run 없이.")
        return 0

    sh("git", "tag", "-a", tag, "-m", f"Plutus {tag}")
    sh("git", "push", "origin", tag)
    nf = os.path.join("dist", "_notes.md")
    io.open(nf, "w", encoding="utf-8").write(note)
    sh(gh, "release", "create", tag, ZIP,
       "--title", tag, "--notes-file", nf, "--latest", "--verify-tag")
    os.remove(nf)
    print(f"\n발행 완료 → https://github.com/tenkojun/i_always_win/releases/tag/{tag}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
