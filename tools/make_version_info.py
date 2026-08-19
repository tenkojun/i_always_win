# -*- coding: utf-8 -*-
"""PyInstaller 윈도우 버전 리소스 생성 — 파일 속성에 이름/개발자가 뜨게."""
import io
import os
import sys

# 저장소 루트를 임포트 경로에 넣는다 (tools/ 에서 실행돼도 되게)
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from version import (__version__, APP_NAME, DEVELOPER, APP_TAGLINE,
                     version_tuple)

vt = version_tuple()
while len(vt) < 4:
    vt = vt + (0,)

tpl = f"""# UTF-8
# PyInstaller 가 읽는 윈도우 버전 리소스. version.py 에서 자동 생성된다.
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers={vt},
    prodvers={vt},
    mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable(
        '041204B0',
        [StringStruct('CompanyName', {DEVELOPER!r}),
         StringStruct('FileDescription', {APP_TAGLINE!r}),
         StringStruct('FileVersion', {__version__!r}),
         StringStruct('InternalName', {APP_NAME!r}),
         StringStruct('LegalCopyright', {f'© 2026 {DEVELOPER}'!r}),
         StringStruct('OriginalFilename', 'Plutus.exe'),
         StringStruct('ProductName', {APP_NAME!r}),
         StringStruct('ProductVersion', {__version__!r})])
    ]),
    VarFileInfo([VarStruct('Translation', [0x0412, 1200])])
  ]
)
"""
io.open(os.path.join(_ROOT, "version_info.txt"), "w", encoding="utf-8", newline="").write(tpl)
print("version_info.txt 생성 (v%s)" % __version__)
