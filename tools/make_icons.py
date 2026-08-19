# -*- coding: utf-8 -*-
"""
로고 하나로 아이콘 세트 만들기
==============================
실행:  python tools/make_icons.py

입력:  webapp/static/plutus.png   (정사각 권장, 512px 이상)
출력:  webapp/static/favicon.png  (256px)
       assets/app.ico             (16·32·48·64·128·256 멀티 사이즈)

왜 스크립트인가
---------------
아이콘을 손으로 리사이즈해 넣으면 로고를 바꿀 때마다 세 곳이 어긋난다.
원본 하나만 갈아 끼우고 이걸 돌리면 끝나게 둔다.

투명 배경 처리
--------------
원본이 검은 배경이면 그대로 쓴다. 알파가 있으면 유지한다. 작은
크기에서 디테일이 뭉개지므로 LANCZOS 로 줄인다.
"""
from __future__ import annotations

import os
import sys

SRC = os.path.join("webapp", "static", "plutus.png")
FAVICON = os.path.join("webapp", "static", "favicon.png")
ICO = os.path.join("assets", "app.ico")
ICO_SIZES = [(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]


def main() -> int:
    if not os.path.isdir("webapp"):
        print("[!] 저장소 루트에서 실행하세요")
        return 1
    if not os.path.exists(SRC):
        print(f"[!] {SRC} 가 없습니다.")
        print("    로고 이미지를 그 경로에 저장한 뒤 다시 실행하세요.")
        return 1

    try:
        from PIL import Image
    except ImportError:
        print("[!] Pillow 가 필요합니다:  pip install Pillow")
        return 1

    img = Image.open(SRC)
    print(f"원본 {img.size[0]}x{img.size[1]} · 모드 {img.mode}")
    if img.mode not in ("RGBA", "RGB"):
        img = img.convert("RGBA")

    # 정사각이 아니면 가운데를 기준으로 잘라 낸다 — 아이콘은 정사각이라
    # 그냥 늘리면 얼굴이 찌그러진다.
    w, h = img.size
    if w != h:
        side = min(w, h)
        left, top = (w - side) // 2, (h - side) // 2
        img = img.crop((left, top, left + side, top + side))
        print(f"  정사각 크롭 → {side}x{side}")

    os.makedirs(os.path.dirname(FAVICON), exist_ok=True)
    os.makedirs(os.path.dirname(ICO), exist_ok=True)

    fav = img.resize((256, 256), Image.LANCZOS)
    fav.save(FAVICON, "PNG", optimize=True)
    print(f"  {FAVICON}  {os.path.getsize(FAVICON):,} bytes")

    # ICO 는 여러 해상도를 한 파일에 담는다. 작업표시줄·탐색기가 상황에
    # 맞는 크기를 골라 쓴다 — 256 하나만 넣으면 작게 표시될 때 뭉갠다.
    ico_src = img.convert("RGBA")
    ico_src.save(ICO, "ICO", sizes=ICO_SIZES)
    print(f"  {ICO}  {os.path.getsize(ICO):,} bytes  "
          f"({len(ICO_SIZES)}개 해상도)")

    print("\n완료. EXE 아이콘을 반영하려면 재빌드가 필요합니다:")
    print("  pyinstaller app.spec --noconfirm")
    return 0


if __name__ == "__main__":
    sys.exit(main())
