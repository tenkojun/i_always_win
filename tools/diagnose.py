# -*- coding: utf-8 -*-
"""
문제 진단 정보 모으기
=====================
실행:  python tools/diagnose.py
또는:  Plutus 폴더에 두고  python diagnose.py

다른 PC 에서 안 될 때 무엇이 다른지 한 번에 본다. 결과를 그대로
복사해 붙이면 원인을 좁힐 수 있다.

**개인정보는 담지 않는다** — API 키 값, 비밀번호, 세션 토큰은 존재
여부만 보고 값은 절대 출력하지 않는다. 경로에 든 사용자 이름은 마스킹.
"""
from __future__ import annotations

import io
import json
import os
import platform
import re
import socket
import subprocess
import sys
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

USER = os.environ.get("USERNAME") or os.path.basename(os.path.expanduser("~"))


def mask(t: str) -> str:
    """경로 등에서 사용자 이름을 가린다."""
    if not t:
        return t
    return t.replace(USER, "<USER>") if USER else t


def hr(t: str) -> None:
    print("\n" + "─" * 62)
    print("  " + t)
    print("─" * 62)


def run(*a, timeout: int = 20) -> str:
    try:
        r = subprocess.run(a, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout)
        return (r.stdout or r.stderr or "").strip()
    except Exception as e:
        return f"(실행 실패: {type(e).__name__})"


def app_root() -> Path:
    here = Path(__file__).resolve().parent
    # tools/ 안이면 한 단계 위, Plutus 폴더 안이면 그대로
    return here.parent if here.name == "tools" else here


def main() -> int:
    root = app_root()

    hr("시스템")
    print(f"  OS         {platform.platform()}")
    print(f"  빌드       {platform.version()}")
    print(f"  아키텍처   {platform.machine()}")
    print(f"  Python     {platform.python_version()} "
          f"({'frozen' if getattr(sys,'frozen',False) else 'source'})")
    print(f"  로케일     {run('powershell','-NoProfile','-Command','(Get-Culture).Name')}")
    cp = run("powershell", "-NoProfile", "-Command", "(chcp)")
    print(f"  코드페이지 {cp[:60]}")

    hr("WebView2 런타임  ← 창이 안 뜨는 가장 흔한 원인")
    GUID = "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
    v = ""
    for hive in (r"HKLM:\SOFTWARE\WOW6432Node", r"HKLM:\SOFTWARE",
                 r"HKCU:\SOFTWARE"):
        key = hive + r"\Microsoft\EdgeUpdate\Clients" + "\\" + GUID
        out = run("powershell", "-NoProfile", "-Command",
                  "if(Test-Path '%s'){(Get-ItemProperty '%s').pv}" % (key, key))
        if out and "실행 실패" not in out:
            v = out.strip()
            break
    ok = bool(v)
    print(f"  설치됨     {'예 · ' + v if ok else '★ 아니오'}")
    if not ok:
        print("  → pywebview 는 Edge WebView2 위에서 창을 그린다. 없으면")
        print("     창이 비거나 즉시 닫힌다. 아래에서 설치:")
        print("     https://developer.microsoft.com/microsoft-edge/webview2/")

    hr("앱 폴더")
    print(f"  경로       {mask(str(root))}")
    for n in ("Plutus.exe", "_internal", ".data"):
        p = root / n
        print(f"  {n:<12} {'있음' if p.exists() else '★없음'}")
    data = root / ".data"
    if data.exists():
        try:
            print(f"  .data 항목 {len(list(data.iterdir()))}개")
        except Exception:
            pass
        w = os.access(data, os.W_OK)
        print(f"  쓰기 가능  {'예' if w else '★아니오 (권한 문제)'}")

    hr("포트 8765")
    s = socket.socket()
    s.settimeout(2)
    try:
        s.connect(("127.0.0.1", 8765))
        print("  응답함 — Plutus 가 이미 실행 중이거나 다른 앱이 점유")
    except Exception:
        print("  응답 없음 (앱이 꺼져 있으면 정상)")
    finally:
        s.close()

    hr("네트워크")
    for host, why in (("api.github.com", "업데이트 확인"),
                      ("iaw-auth.tenkojun.workers.dev", "로그인"),
                      ("query1.finance.yahoo.com", "시세")):
        try:
            socket.create_connection((host, 443), timeout=5).close()
            print(f"  {host:<38} 연결 OK   ({why})")
        except Exception as e:
            print(f"  {host:<38} ★실패 {type(e).__name__}  ({why})")

    hr("설정 상태 (값은 출력하지 않음)")
    keys = data / "keys.json"
    if keys.exists():
        try:
            k = json.loads(keys.read_text(encoding="utf-8"))
            print("  API 키     " + ", ".join(
                f"{n}={'설정됨' if k.get(n) else '없음'}" for n in sorted(k)))
        except Exception:
            print("  API 키     (읽기 실패)")
    else:
        print("  API 키     파일 없음 (무키로도 전 기능 동작)")
    for n in ("auth.db", "pc_id", "shortcuts_asked"):
        print(f"  {n:<16} {'있음' if (data / n).exists() else '없음'}")

    hr("로그 — 오류만 (마지막 40건)")
    log = data / "logs" / "app.log"
    if not log.exists():
        print("  ★ 로그 파일이 없습니다. 앱이 기동조차 못 했을 수 있습니다.")
    else:
        txt = io.open(log, encoding="utf-8", errors="replace").read()
        pat = re.compile(r"traceback|error|exception|failed|실패|"
                         r'" 5\d\d ', re.I)
        hits = [l for l in txt.split("\n") if pat.search(l)]
        if not hits:
            print("  오류 없음")
        for l in hits[-40:]:
            print("  " + mask(l.strip())[:200])
        print(f"\n  (로그 전체 {len(txt.splitlines()):,}줄 · "
              f"{mask(str(log))})")

    hr("붙여넣어 주세요")
    print("  위 내용을 그대로 복사해 전달하면 원인을 좁힐 수 있습니다.")
    print("  화면 증상(빈 창 / 즉시 종료 / 로그인 실패 / 글자 깨짐 등)도")
    print("  한 줄 적어 주시면 더 빠릅니다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
