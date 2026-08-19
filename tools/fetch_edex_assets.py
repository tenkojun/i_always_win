# -*- coding: utf-8 -*-
"""
eDEX-UI 자산 받아오기
=====================
실행:  python tools/fetch_edex_assets.py

받는 것
-------
- 사운드 13종            → webapp/static/audio/*.wav
- 원본 부팅 로그          → webapp/static/audio/boot_log.txt
- GPL-3.0 전문           → LICENSE

라이선스 (중요)
---------------
eDEX-UI 는 **GPL-3.0** 이다. 이 자산을 넣고 배포하는 순간 결합물 전체가
GPL-3.0 이 되며 소스를 공개할 의무가 생긴다. 그래서 이 스크립트는
LICENSE 도 함께 받아 놓는다. 출처 표기는 NOTICE.md 에 있다.

  원저작물   https://github.com/GitSquared/edex-ui  (GitSquared)
  사운드     IceWolf 작곡 (v2.1.x 이상)

자산을 쓰지 않기로 하면 이 스크립트를 돌리지 말고
`webapp/static/audio/` 를 지우면 된다. 앱은 사운드가 없으면 조용히
Web Audio 합성음으로 폴백한다.
"""
from __future__ import annotations

import os
import sys
import urllib.request

BASE = "https://raw.githubusercontent.com/GitSquared/edex-ui/master"
AUDIO_DIR = os.path.join("webapp", "static", "audio")

# 파일명 → 대략적인 기대 크기(바이트). 0 이면 검사 생략.
SOUNDS = {
    "stdout.wav": 7644,        # 로그 한 줄 출력음 — '티리릭'
    "stdin.wav": 18738,
    "theme.wav": 530350,       # 부팅 테마
    "keyboard.wav": 218234,
    "granted.wav": 106228,     # 로그인 성공
    "denied.wav": 106228,      # 로그인 실패
    "panels.wav": 95184,
    "expand.wav": 227902,
    "scan.wav": 582516,
    "alarm.wav": 106226,
    "error.wav": 159146,
    "info.wav": 106242,
    "folder.wav": 16460,
}


def _get(url: str, dest: str) -> int:
    req = urllib.request.Request(url, headers={"User-Agent": "iaw-fetch"})
    with urllib.request.urlopen(req, timeout=60) as r:
        data = r.read()
    os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
    with open(dest, "wb") as f:
        f.write(data)
    return len(data)


def main() -> int:
    if not os.path.isdir("webapp"):
        print("[!] 저장소 루트에서 실행하세요 (webapp/ 이 보이는 곳)")
        return 1

    print("eDEX-UI 자산 내려받기 — GPL-3.0")
    print("  원저작물: https://github.com/GitSquared/edex-ui")
    print("  사운드  : IceWolf\n")

    ok = bad = 0
    for name, expect in SOUNDS.items():
        dest = os.path.join(AUDIO_DIR, name)
        try:
            n = _get(f"{BASE}/src/assets/audio/{name}", dest)
        except Exception as e:
            print(f"  {name:<14} 실패 — {type(e).__name__}: {e}")
            bad += 1
            continue
        # 크기가 크게 어긋나면 리다이렉트/에러 페이지를 받은 것이다
        warn = ""
        if expect and abs(n - expect) > max(2048, expect * 0.1):
            warn = f"  ← 기대 {expect:,} 와 다름. 확인 필요"
        print(f"  {name:<14} {n:>9,} bytes{warn}")
        ok += 1

    for url, dest, label in (
        (f"{BASE}/src/assets/misc/boot_log.txt",
         os.path.join(AUDIO_DIR, "boot_log.txt"), "원본 부팅 로그"),
        ("https://www.gnu.org/licenses/gpl-3.0.txt", "LICENSE", "GPL-3.0 전문"),
    ):
        try:
            n = _get(url, dest)
            print(f"  {os.path.basename(dest):<14} {n:>9,} bytes  ({label})")
            ok += 1
        except Exception as e:
            print(f"  {label} 실패 — {type(e).__name__}: {e}")
            bad += 1

    print(f"\n완료 — 성공 {ok} · 실패 {bad}")
    if bad:
        print("실패한 항목은 네트워크 확인 후 다시 실행하세요.")
    else:
        print("이제 앱을 새로고침하면 eDEX 사운드가 적용됩니다.")
    return 0 if not bad else 2


if __name__ == "__main__":
    sys.exit(main())
