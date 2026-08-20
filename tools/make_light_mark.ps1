# 밝은 배경용 마크 만들기
# ========================
#   powershell -ExecutionPolicy Bypass -File tools\make_light_mark.ps1
#
# 입력 : webapp/static/plutus_mark.png        (흰 잉크 — 어두운 배경용)
# 출력 : webapp/static/plutus_mark_light.png  (어두운 잉크 — 밝은 배경용)
#
# 왜 필요한가
# -----------
# 마크는 흰 잉크에 알파로만 형태를 만든다. 어두운 배경에서는 잘 보이지만
# **흰 배경에서는 통째로 사라진다** (GitHub 라이트 테마, 앱의 white/snow
# 테마). 알파는 그대로 두고 잉크 색만 어둡게 바꾼 쌍둥이를 만든다.
#
# PIL 은 이 프로젝트에 설치돼 있지 않다(그리고 EXE 에서 일부러 뺐다).
# 그래서 윈도우에 원래 있는 System.Drawing 을 쓴다. 픽셀 하나씩
# GetPixel 하면 68만 픽셀에 몇 분이 걸리므로 LockBits 로 통째로 읽는다.

$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Drawing

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
if ((Split-Path -Leaf $root) -eq 'tools') { $root = Split-Path -Parent $root }
$src = Join-Path $root 'webapp\static\plutus_mark.png'
$dst = Join-Path $root 'webapp\static\plutus_mark_light.png'

# 밝은 배경용 잉크. 앱의 어두운 배경 톤과 같은 계열로 골라서 같은 마크로
# 읽히게 한다. 순수 검정은 너무 딱딱하다.
$INK_R = 13; $INK_G = 17; $INK_B = 23

if (-not (Test-Path $src)) { throw "원본이 없습니다: $src" }

$bmp = [System.Drawing.Bitmap]::FromFile($src)
try {
  $w = $bmp.Width; $h = $bmp.Height
  $rect = New-Object System.Drawing.Rectangle 0, 0, $w, $h
  $fmt = [System.Drawing.Imaging.PixelFormat]::Format32bppArgb

  # 원본을 읽기 전용으로 잠그고 바이트로 통째로 가져온다
  $srcData = $bmp.LockBits($rect, [System.Drawing.Imaging.ImageLockMode]::ReadOnly, $fmt)
  # PS 5.1 에서 New-Object byte[] 는 생성자를 못 찾는다. ::new 를 쓴다.
  $bytes = [byte[]]::new($srcData.Stride * $h)
  [System.Runtime.InteropServices.Marshal]::Copy($srcData.Scan0, $bytes, 0, $bytes.Length)
  $bmp.UnlockBits($srcData)

  # BGRA 순서. 알파는 건드리지 않고 색만 바꾼다 — 형태와 안티에일리어싱이
  # 전부 알파에 들어 있어서, 알파를 지키면 가장자리가 그대로 살아난다.
  $opaque = 0
  for ($i = 0; $i -lt $bytes.Length; $i += 4) {
    if ($bytes[$i + 3] -gt 0) {
      $bytes[$i]     = $INK_B
      $bytes[$i + 1] = $INK_G
      $bytes[$i + 2] = $INK_R
      if ($bytes[$i + 3] -gt 32) { $opaque++ }
    }
  }

  # 빈 이미지를 저장하지 않는다 — 예전에 로고가 통째로 날아간 적이 있다
  if ($opaque -lt 100) { throw "불투명 픽셀이 $opaque 개뿐입니다. 저장하지 않습니다." }

  $out = New-Object System.Drawing.Bitmap $w, $h, $fmt
  $dstData = $out.LockBits($rect, [System.Drawing.Imaging.ImageLockMode]::WriteOnly, $fmt)
  [System.Runtime.InteropServices.Marshal]::Copy($bytes, 0, $dstData.Scan0, $bytes.Length)
  $out.UnlockBits($dstData)
  $out.Save($dst, [System.Drawing.Imaging.ImageFormat]::Png)
  $out.Dispose()

  "만들었습니다: $dst"
  "  크기       $w x $h"
  "  불투명     $opaque 픽셀"
  "  잉크       RGB($INK_R, $INK_G, $INK_B)"
} finally {
  $bmp.Dispose()
}
