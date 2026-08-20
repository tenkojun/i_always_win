# -*- coding: utf-8 -*-
"""
과거 버전 릴리스 소급 발행
==========================
실행:  python tools/backfill_releases.py [--dry-run]

`version.py` 가 바뀐 커밋을 전부 찾아 그 지점에 태그를 만들고,
CHANGELOG 해당 절을 릴리스 노트로 붙여 GitHub 릴리스를 만든다.

바이너리를 붙이지 않는 이유
---------------------------
과거 버전의 EXE 를 만들려면 커밋마다 체크아웃해 다시 빌드해야 한다.
26개 × 4~6분 = 두 시간이 넘고, 정작 받아 갈 사람이 없다. 자동 업데이트는
**최신 릴리스만** 본다.

그래서 과거 버전은 GitHub 이 자동으로 붙여 주는 소스 아카이브만 두고,
바이너리는 최신 릴리스부터 첨부한다. 필요하면 나중에 특정 버전만
골라 빌드해 올리면 된다.

--latest=false 를 반드시 준다
-----------------------------
릴리스를 나중에 만들면 GitHub 이 그걸 'Latest' 로 올려 버릴 수 있다.
그러면 앱의 업데이트 확인이 **옛 버전을 최신이라고 인식**한다.
과거 릴리스는 전부 latest 가 아니라고 못 박는다.
"""
from __future__ import annotations

import io
import os
import re
import subprocess
import sys

# 윈도우 기본 콘솔은 cp949 라 '—' 같은 문자에서 죽는다.
# 이 프로젝트가 이미 겪은 문제라 engine/console.py 가 있지만,
# 이 도구는 engine 을 안 쓰므로 여기서 직접 처리한다.
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
    raise SystemExit("[!] gh CLI 를 찾지 못했습니다.")


def sh(*args: str, check: bool = False) -> str:
    r = subprocess.run(args, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if check and r.returncode != 0:
        raise RuntimeError(f"{' '.join(args[:2])} 실패: {r.stderr.strip()[:200]}")
    return (r.stdout or "").strip()


def versions() -> list[tuple[str, str, str]]:
    """(버전, 커밋, 날짜) — 오래된 순."""
    out = []
    log = sh("git", "log", "--format=%H|%ad", "--date=short",
             "-G^__version__", "--", "version.py")
    for line in [l for l in log.split("\n") if l]:
        sha, date = line.split("|", 1)
        blob = sh("git", "show", f"{sha}:version.py")
        m = re.search(r'__version__\s*=\s*"([^"]+)"', blob)
        if m:
            out.append((m.group(1), sha, date))
    out.reverse()
    return out


def notes_for(ver: str) -> str:
    """CHANGELOG 에서 해당 버전 절만 잘라 낸다."""
    try:
        s = io.open("CHANGELOG.md", encoding="utf-8").read()
    except Exception:
        return ""
    m = re.search(r"^## \[" + re.escape(ver) + r"\][^\n]*\n(.*?)(?=^## \[|\Z)",
                  s, re.S | re.M)
    if not m:
        return ""
    body = m.group(1).strip()
    body = re.sub(r"\n---\s*$", "", body).strip()
    return body


def main() -> int:
    dry = "--dry-run" in sys.argv
    if not os.path.exists("version.py"):
        print("[!] 저장소 루트에서 실행하세요")
        return 1
    gh = _gh()

    existing = set()
    for line in sh(gh, "release", "list", "--limit", "200").split("\n"):
        if line.strip():
            existing.add(line.split("\t")[0].strip())
    tags = set(sh("git", "tag", "-l").split("\n"))

    vs = versions()
    print(f"버전 {len(vs)}개 · 기존 릴리스 {len(existing)}개"
          f"{'  [DRY RUN]' if dry else ''}\n")

    made = skipped = failed = 0
    for ver, sha, date in vs:
        tag = f"v{ver}"
        if tag in existing:
            print(f"  {tag:<10} 이미 있음 — 건너뜀")
            skipped += 1
            continue

        note = notes_for(ver) or f"v{ver} ({date})"
        if dry:
            print(f"  {tag:<10} 생성 예정  {sha[:9]}  노트 {len(note)}자")
            made += 1
            continue

        try:
            if tag not in tags:
                sh("git", "tag", "-a", tag, sha, "-m", f"Plutus {tag}", check=True)
                sh("git", "push", "origin", tag, check=True)

            nf = os.path.join("dist", f"_notes_{ver}.md")
            os.makedirs("dist", exist_ok=True)
            io.open(nf, "w", encoding="utf-8").write(note)
            # --latest=false 가 핵심 — 안 주면 옛 버전이 Latest 가 된다
            sh(gh, "release", "create", tag,
               "--title", tag, "--notes-file", nf,
               "--latest=false", "--verify-tag", check=True)
            os.remove(nf)
            print(f"  {tag:<10} 발행  {sha[:9]}  노트 {len(note)}자")
            made += 1
        except Exception as e:
            print(f"  {tag:<10} 실패 — {e}")
            failed += 1

    print(f"\n발행 {made} · 건너뜀 {skipped} · 실패 {failed}")
    if not dry:
        print("\n확인:  gh release list --limit 40")
    return 0 if not failed else 2


if __name__ == "__main__":
    sys.exit(main())
