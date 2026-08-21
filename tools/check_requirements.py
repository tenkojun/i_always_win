# -*- coding: utf-8 -*-
"""
requirements.txt 가 실제 코드와 맞는가
======================================
실행:  python tools/check_requirements.py

왜 필요한가
-----------
목록이 실제와 어긋나면 **새로 받은 사람이 문서대로 해도 실패한다.**
실제로 그랬다 — requirements.txt 가 torch · xgboost · statsmodels · arch ·
hmmlearn · matplotlib 을 요구했는데 이들을 import 하는 파일은 0개였다.
게다가 arch/hmmlearn 은 윈도우에서 컴파일러 없이 설치가 자주 깨져서,
README 의 "30초 시작" 이 첫 줄부터 막혔다.

두 방향을 다 본다.
  (1) 목록에 있는데 아무도 안 쓰는 것   → 헛되이 받게 만든다
  (2) 코드가 쓰는데 목록에 없는 것       → 설치해도 실행이 안 된다
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ROOT = Path(__file__).resolve().parent.parent
SCAN = ["engine", "webapp", "tools"]
ROOT_FILES = ["run_desktop.py", "version.py"]

# 배포판에 들어 있는 것 — 목록에 없어도 된다
STDLIB = set(sys.stdlib_module_names)

# import 이름 ≠ 패키지 이름
ALIAS = {
    "sklearn": "scikit-learn",
    "webview": "pywebview",
    "PIL": "pillow",
    "yaml": "pyyaml",
    "dateutil": "python-dateutil",
}

# 이 프로젝트의 자체 모듈
LOCAL = {"engine", "webapp", "version", "tools"}

# 선택적 의존 — 없어도 폴백이 돌아야 하는 것들
OPTIONAL = {"pyinstaller", "pytest", "pytest-timeout"}

def dev_listed() -> set:
    """requirements-dev.txt 에 있는 것 — tools/ 전용 의존은 여기 있으면 된다."""
    f = ROOT / "requirements-dev.txt"
    if not f.exists():
        return set()
    out = set()
    for line in f.read_text(encoding="utf-8").splitlines():
        line = line.split("#")[0].strip()
        if not line or line.startswith("-"):
            continue
        m = re.match(r"([A-Za-z0-9._-]+)", line)
        if m:
            out.add(m.group(1).lower())
    return out


def imported_names() -> dict[str, set[str]]:
    """최상위 import 이름 → 그걸 쓰는 파일들."""
    out: dict[str, set[str]] = {}
    files = [p for d in SCAN for p in (ROOT / d).rglob("*.py")]
    files += [ROOT / f for f in ROOT_FILES if (ROOT / f).exists()]
    for p in files:
        if "__pycache__" in str(p):
            continue
        try:
            tree = ast.parse(p.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    out.setdefault(a.name.split(".")[0], set()).add(p.name)
            elif isinstance(node, ast.ImportFrom):
                if node.level:          # 상대 import 는 자기 패키지다
                    continue
                if node.module:
                    out.setdefault(node.module.split(".")[0], set()).add(p.name)
    return out


def listed() -> set[str]:
    txt = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    names = set()
    for line in txt.split("\n"):
        line = line.split("#")[0].strip()
        if not line or line.startswith("-"):
            continue
        m = re.match(r"([A-Za-z0-9._-]+)", line)
        if m:
            names.add(m.group(1).lower())
    return names


def main() -> int:
    used = imported_names()
    have = listed()
    dev = dev_listed()
    canon = {ALIAS.get(k, k).lower() for k in used}

    # (1) 목록에 있는데 안 쓰는 것
    unused = sorted(p for p in have
                    if p not in canon and p not in OPTIONAL)

    # (2) 쓰는데 목록에 없는 것
    missing = []
    for name, files in sorted(used.items()):
        if name in STDLIB or name in LOCAL or name.startswith("_"):
            continue
        pkg = ALIAS.get(name, name).lower()
        if pkg in have or pkg in OPTIONAL or pkg in dev:
            continue
        missing.append((pkg, name, sorted(files)[:3]))

    print(f"requirements.txt · 항목 {len(have)}개 · "
          f"코드가 쓰는 외부 패키지 {len(canon)}종")

    bad = False
    if unused:
        bad = True
        print("\n[!] 목록에 있는데 아무도 import 하지 않습니다:")
        for p in unused:
            print(f"      {p}")
        print("    → 지우거나, 정말 필요하면 주석으로 이유를 남기세요.")
    if missing:
        bad = True
        print("\n[!] 코드가 쓰는데 목록에 없습니다:")
        for pkg, name, files in missing:
            print(f"      {pkg:<22} (import {name}) — {', '.join(files)}")
        print("    → 설치해도 실행이 안 됩니다.")

    if not bad:
        print("\n목록과 코드가 일치합니다.")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
