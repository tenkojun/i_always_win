# -*- coding: utf-8 -*-
"""
로고 하나로 아이콘 세트 만들기
==============================
실행:  python tools/make_icons.py

입력:  webapp/static/plutus.png        원본 (건드리지 않는다)
출력:  webapp/static/plutus_mark.png   투명 배경 마크 (부팅 화면용)
       webapp/static/favicon.png       256px 파비콘 (엠블럼만)
       assets/app.ico                  16~256 멀티 사이즈

원본은 절대 덮어쓰지 않는다
---------------------------
이전 버전은 변환 결과를 `plutus.png` 에 그대로 써서 **원본을 파괴**했다.
게다가 알파를 int16 으로 계산하는 바람에 `(lum-14)*255` 가 오버플로해
가장 밝은 선이 음수로 감싸며 0(완전 투명)이 됐다 — 로고 본체가 사라진
채로 저장됐고, 원본이 없어 복구가 불가능했다.

그래서 지금은
  - 산출물을 **다른 파일명**으로만 쓴다
  - 알파 계산을 float 로 한다
  - 저장 전에 "불투명 픽셀이 실제로 있는지" 검사하고, 비면 중단한다
"""
from __future__ import annotations

import os
import sys

SRC = os.path.join("webapp", "static", "plutus.png")
MARK = os.path.join("webapp", "static", "plutus_mark.png")
FAVICON = os.path.join("webapp", "static", "favicon.png")
ICO = os.path.join("assets", "app.ico")
ICO_SIZES = [(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]


def _to_alpha(img):
    """
    검은 배경을 투명으로. 밝기를 그대로 알파로 쓴다.

    **float 로 계산한다.** 정수 타입으로 하면 중간식이 오버플로해
    밝은 픽셀이 감싸 버린다(실제로 겪었다).
    """
    import numpy as np
    a = np.array(img.convert("RGBA")).astype(np.float32)
    lum = a[..., :3].max(axis=2)
    lo, hi = 12.0, 220.0
    alpha = np.clip((lum - lo) * (255.0 / (hi - lo)), 0.0, 255.0)

    out = np.zeros(a.shape, dtype=np.uint8)
    out[..., 0] = out[..., 1] = out[..., 2] = 255   # 흑백 라인아트 → 흰색
    out[..., 3] = alpha.astype(np.uint8)

    from PIL import Image
    res = Image.fromarray(out, "RGBA")
    bbox = res.getbbox()
    return res.crop(bbox) if bbox else res


def _crop_emblem(img):
    """
    엠블럼(원형 문양)만 잘라 낸다 — 아이콘은 16px 까지 줄어드는데
    워드마크가 든 전체 락업을 그대로 쓰면 글자가 얼룩으로 뭉갠다.

    엠블럼과 글자 사이의 **빈 띠**를 경계로 삼는다. 좌표를 상수로 박으면
    로고를 바꿀 때마다 어긋나므로 매번 이미지에서 찾는다.
    """
    import numpy as np
    g = np.array(img.convert("RGBA"))
    vis = g[..., 3] if g.shape[2] == 4 else g[..., :3].max(axis=2)
    rowmean = vis.mean(axis=1)
    if rowmean.max() <= 0:
        return img

    thr = rowmean.max() * 0.06
    ys = np.where(rowmean > thr)[0]
    if len(ys) == 0:
        return img
    y0, y1 = int(ys[0]), int(ys[-1])

    cut, run = None, None
    for y in range(y0, y1 + 1):
        if rowmean[y] <= thr:
            run = y if run is None else run
        else:
            if run is not None and y - run > 12:
                cut = run
                break
            run = None
    if cut is None:
        cut = y1

    side = cut - y0
    if side < 16:
        return img
    w = img.size[0]
    left = max(0, w // 2 - side // 2)
    return img.crop((left, y0, min(w, left + side), y0 + side))


def main() -> int:
    if not os.path.isdir("webapp"):
        print("[!] 저장소 루트에서 실행하세요")
        return 1
    if not os.path.exists(SRC):
        print(f"[!] {SRC} 가 없습니다.")
        print("    로고 원본을 그 경로에 저장한 뒤 다시 실행하세요.")
        return 1
    try:
        from PIL import Image
        import numpy as np
    except ImportError:
        print("[!] Pillow / numpy 가 필요합니다:  pip install Pillow numpy")
        return 1

    img = Image.open(SRC)
    print(f"원본 {SRC}  {img.size[0]}x{img.size[1]} · {img.mode}")

    mark = _to_alpha(img)
    op = (np.array(mark)[..., 3] > 16).mean()
    # 변환이 망가지면 거의 전부 투명해진다. 그 상태로 저장하면 화면에서
    # 아무것도 안 보이는 걸 뒤늦게 발견하게 되므로 여기서 막는다.
    if op < 0.005:
        print(f"[!] 불투명 픽셀이 {op*100:.2f}% 뿐입니다 — 변환이 잘못됐습니다.")
        print("    원본이 검은 배경 + 밝은 선 형태인지 확인하세요. 저장 중단.")
        return 2

    mark.save(MARK, "PNG", optimize=True)
    print(f"  {MARK}  {mark.size[0]}x{mark.size[1]}  "
          f"{os.path.getsize(MARK):,} bytes  (불투명 {op*100:.1f}%)")

    em = _crop_emblem(mark)
    print(f"  엠블럼 크롭 → {em.size[0]}x{em.size[1]}")

    os.makedirs(os.path.dirname(ICO), exist_ok=True)
    em.resize((256, 256), Image.LANCZOS).save(FAVICON, "PNG", optimize=True)
    print(f"  {FAVICON}  {os.path.getsize(FAVICON):,} bytes")
    em.convert("RGBA").save(ICO, "ICO", sizes=ICO_SIZES)
    print(f"  {ICO}  {os.path.getsize(ICO):,} bytes  ({len(ICO_SIZES)}개 해상도)")

    print(f"\n원본 {SRC} 는 그대로 두었습니다.")
    print("EXE 아이콘 반영은 재빌드가 필요합니다:  pyinstaller app.spec --noconfirm")
    return 0


if __name__ == "__main__":
    sys.exit(main())
