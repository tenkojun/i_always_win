# I ALWAYS WIN — 바탕화면 바로가기 생성
# =====================================
# 사용법:  powershell -ExecutionPolicy Bypass -File tools\make_shortcut.ps1
#
# 작업 디렉터리를 exe 폴더로 지정하는 게 중요하다. 앱은 실행 파일 옆의
# .data\ 에 상태(키·세션·캐시)를 만들기 때문에, 작업 디렉터리가 엉뚱하면
# 상태가 다른 곳에 흩어진다.

$ErrorActionPreference = 'Stop'

$root     = Split-Path -Parent $PSScriptRoot
$exe      = Join-Path $root 'dist\IAlwaysWin\IAlwaysWin.exe'
$workDir  = Split-Path -Parent $exe
$icon     = Join-Path $root 'assets\app.ico'
$desktop  = [Environment]::GetFolderPath('Desktop')
$lnk      = Join-Path $desktop 'I ALWAYS WIN.lnk'

if (-not (Test-Path $exe)) {
    Write-Host "실행 파일이 없습니다: $exe" -ForegroundColor Red
    Write-Host "먼저 build_windows_exe.bat 으로 빌드하세요." -ForegroundColor Yellow
    exit 1
}

$shell = New-Object -ComObject WScript.Shell
$sc = $shell.CreateShortcut($lnk)
$sc.TargetPath       = $exe
$sc.WorkingDirectory = $workDir
$sc.Description      = 'I ALWAYS WIN — 기관급 퀀트 분석 터미널'
if (Test-Path $icon) { $sc.IconLocation = $icon }
$sc.Save()

Write-Host "바로가기 생성 완료" -ForegroundColor Green
Write-Host "  $lnk"
Write-Host "  → $exe"
