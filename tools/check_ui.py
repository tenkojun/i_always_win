# -*- coding: utf-8 -*-
"""
index.html 의 인라인 JS 구문 검사
=================================
실행:  python tools/check_ui.py

왜 필요한가
-----------
UI 가 11,000줄짜리 단일 HTML 이고 JS 가 `<script>` 안에 인라인으로 있다.
그래서 편집기도 린터도 구문 오류를 잡아 주지 않는다. 오타 하나면
**스크립트 블록 전체가 파싱되지 않아** 함수가 하나도 정의되지 않고,
화면은 부팅 화면에서 멈춘다. 콘솔을 열기 전까지는 이유를 알 수 없다.

실제로 그렇게 앱을 통째로 죽인 적이 있다(주석을 닫고 본문이 남았다).
고치기 전에 이걸 먼저 돌린다.

의존성 0개 — Node 만 있으면 된다.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

TARGET = os.path.join("webapp", "static", "index.html")
# type 이 붙은 것(module/json/ld+json)은 제외하고 평범한 스크립트만 본다
SCRIPT = re.compile(
    r"<script(?![^>]*\bsrc=)(?![^>]*\btype=)[^>]*>(.*?)</script>",
    re.S | re.I)


def node() -> str | None:
    for c in ("node", r"C:\Program Files\nodejs\node.exe"):
        try:
            subprocess.run([c, "--version"], capture_output=True, timeout=20)
            return c
        except Exception:
            continue
    return None


def main() -> int:
    if not os.path.exists(TARGET):
        print(f"[!] {TARGET} 가 없습니다. 저장소 루트에서 실행하세요.")
        return 1
    exe = node()
    if not exe:
        print("[!] Node 를 찾지 못했습니다. 검사를 건너뜁니다.")
        return 0

    src = open(TARGET, encoding="utf-8").read()
    blocks = list(SCRIPT.finditer(src))
    print(f"{TARGET} · 인라인 스크립트 {len(blocks)}개")

    bad = 0
    for i, m in enumerate(blocks, 1):
        code = m.group(1)
        # 이 블록이 원본 몇 번째 줄에서 시작하는지 (오류 줄 번호 보정용)
        start = src.count("\n", 0, m.start(1)) + 1
        lines = code.count("\n")
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False,
                                         encoding="utf-8") as f:
            f.write(code)
            tmp = f.name
        try:
            r = subprocess.run([exe, "--check", tmp],
                               capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=90)
        finally:
            try:
                os.unlink(tmp)
            except Exception:
                pass

        if r.returncode == 0:
            print(f"  [{i}] OK    {lines:>5}줄  (원본 {start}줄부터)")
            continue

        bad += 1
        print(f"  [{i}] 실패  {lines:>5}줄  (원본 {start}줄부터)")
        for ln in (r.stderr or "").split("\n"):
            ln = ln.rstrip()
            if not ln or tmp in ln and ":" not in ln:
                continue
            # 임시 파일의 줄 번호를 원본 줄 번호로 바꿔 준다
            mm = re.search(re.escape(tmp) + r":(\d+)", ln)
            if mm:
                real = start + int(mm.group(1)) - 1
                ln = ln.replace(f"{tmp}:{mm.group(1)}",
                                f"{TARGET}:{real}")
            print("      " + ln[:160])

    if bad:
        print(f"\n[!] 스크립트 블록 {bad}개에 구문 오류가 있습니다.")
        print("    이 상태로 열면 함수가 하나도 정의되지 않고 부팅 화면에서 멈춥니다.")
        return 1
    print("\n구문 오류 없음")
    return 0


if __name__ == "__main__":
    sys.exit(main())
