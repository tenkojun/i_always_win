# Plutus 진단 - Python 없이 도는 판
# =================================
# Plutus.exe 옆에 두고 오른쪽 클릭 > "PowerShell에서 실행"
# 또는:  powershell -ExecutionPolicy Bypass -File 진단.ps1
#
# 왜 PowerShell 인가: 배포본을 받은 PC 에는 Python 이 없다. 진단 도구가
# Python 을 요구하면 정작 필요한 곳에서 못 쓴다. PowerShell 은 윈도우에
# 항상 있다.
#
# 개인정보는 담지 않는다 - API 키 값·비밀번호·토큰은 존재 여부만 본다.

$ErrorActionPreference = 'Continue'
try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch {}

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
if ((Split-Path -Leaf $root) -eq 'tools') { $root = Split-Path -Parent $root }
$data = Join-Path $root '.data'
$me   = $env:USERNAME
$out  = New-Object Text.StringBuilder

function W($t) { Write-Host $t; [void]$out.AppendLine($t) }
function HR($t) {
  W ''
  W ('-' * 62)
  W ("  " + $t)
  W ('-' * 62)
}
function Mask($t) { if ($me) { return ($t -replace [regex]::Escape($me), '<USER>') } $t }

HR '시스템'
$os = Get-CimInstance Win32_OperatingSystem
W ("  OS         " + $os.Caption)
W ("  빌드       " + $os.Version + "  (" + $os.OSArchitecture + ")")
W ("  로케일     " + (Get-Culture).Name + "   시스템 ANSI CP " + (Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\Nls\CodePage').ACP)
W ("  PowerShell " + $PSVersionTable.PSVersion)
W ("  메모리     " + [math]::Round($os.TotalVisibleMemorySize / 1MB, 1) + " GB")

HR 'WebView2 런타임   <- 창이 안 뜨는 가장 흔한 원인'
$guid = '{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}'
$ver = $null
foreach ($h in @('HKLM:\SOFTWARE\WOW6432Node','HKLM:\SOFTWARE','HKCU:\SOFTWARE')) {
  $k = Join-Path $h "Microsoft\EdgeUpdate\Clients\$guid"
  if (Test-Path $k) {
    $pv = (Get-ItemProperty $k -ErrorAction SilentlyContinue).pv
    if ($pv) { $ver = $pv; break }
  }
}
if ($ver) {
  W ("  설치됨     예 - " + $ver)
} else {
  W '  설치됨     ***  아니오  ***'
  W '  -> Plutus 는 Edge WebView2 위에 화면을 그린다. 없으면 창이 비거나'
  W '     열리자마자 닫힌다. 아래에서 "Evergreen 부트스트래퍼" 를 받아 설치:'
  W '     https://developer.microsoft.com/microsoft-edge/webview2/'
}

HR '앱 폴더'
W ("  경로       " + (Mask $root))
foreach ($n in @('Plutus.exe','_internal','.data')) {
  $p = Join-Path $root $n
  W ("  {0,-12} {1}" -f $n, $(if (Test-Path $p) { '있음' } else { '*** 없음 ***' }))
}
$exe = Join-Path $root 'Plutus.exe'
if (Test-Path $exe) {
  $f = Get-Item $exe
  W ("  버전 리소스 " + $f.VersionInfo.FileVersion + " / " + $f.VersionInfo.ProductVersion)
  W ("  크기       " + [math]::Round($f.Length / 1MB, 1) + " MB")
  # 인터넷에서 받은 파일에 붙는 표식. 붙어 있으면 SmartScreen 이 막을 수 있다
  $z = Get-Item $exe -Stream Zone.Identifier -ErrorAction SilentlyContinue
  if ($z) { W '  차단 표식   *** 있음 (Zone.Identifier) - 속성 > 차단 해제 필요할 수 있음 ***' }
  else    { W '  차단 표식   없음' }
}
# 압축을 덜 풀었는지 - _internal 이 통째로 빠지면 즉시 종료된다
$int = Join-Path $root '_internal'
if (Test-Path $int) {
  $cnt = (Get-ChildItem $int -Recurse -File -ErrorAction SilentlyContinue).Count
  W ("  _internal  파일 " + $cnt + "개")
  if ($cnt -lt 500) { W '  *** 파일 수가 너무 적다. 압축이 덜 풀렸을 수 있다 ***' }
  foreach ($need in @('base_library.zip','python312.dll')) {
    if (-not (Test-Path (Join-Path $int $need))) { W ("  *** " + $need + " 없음 ***") }
  }
}
if (Test-Path $data) {
  try {
    $t = Join-Path $data '_wtest.tmp'
    'x' | Out-File $t -Encoding utf8 -ErrorAction Stop
    Remove-Item $t -Force
    W '  쓰기 가능  예'
  } catch { W '  쓰기 가능  *** 아니오 - 권한 문제 (Program Files 에 두면 이렇게 된다) ***' }
}
# 설치 위치가 문제를 부르는 곳인지
if ($root -match '^[A-Z]:\\Program Files') { W '  *** Program Files 아래다. portable 앱이라 쓰기가 막힌다. 다른 곳으로 옮기세요 ***' }
if ($root -match '\\AppData\\Local\\Temp') { W '  *** 임시 폴더에서 실행 중이다. 압축을 풀지 않고 zip 안에서 바로 연 것 ***' }

HR '포트 8765'
$busy = Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue
if ($busy) {
  foreach ($b in $busy) {
    $pr = Get-Process -Id $b.OwningProcess -ErrorAction SilentlyContinue
    W ("  점유 중 - PID " + $b.OwningProcess + " " + $(if ($pr) { $pr.ProcessName } else { '?' }))
  }
  W '  (Plutus 가 이미 떠 있으면 정상. 다른 앱이면 그게 원인이다)'
} else { W '  비어 있음 (앱이 꺼져 있으면 정상)' }

HR '네트워크'
foreach ($h in @(@('api.github.com','업데이트'), @('iaw-auth.tenkojun.workers.dev','로그인'), @('query1.finance.yahoo.com','시세'))) {
  $r = Test-NetConnection -ComputerName $h[0] -Port 443 -WarningAction SilentlyContinue -InformationLevel Quiet
  W ("  {0,-38} {1}   ({2})" -f $h[0], $(if ($r) { '연결 OK' } else { '*** 실패 ***' }), $h[1])
}
$px = (Get-ItemProperty 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings' -ErrorAction SilentlyContinue)
if ($px.ProxyEnable -eq 1) { W ("  프록시 사용 중: " + $px.ProxyServer) }

HR '설정 상태 (값은 출력하지 않음)'
if (Test-Path $data) {
  $kf = Join-Path $data 'keys.json'
  if (Test-Path $kf) {
    try {
      $k = Get-Content $kf -Raw -Encoding UTF8 | ConvertFrom-Json
      $s = ($k.PSObject.Properties | ForEach-Object { $_.Name + '=' + $(if ($_.Value) { '설정됨' } else { '없음' }) }) -join ', '
      W ("  API 키     " + $s)
    } catch { W '  API 키     (읽기 실패)' }
  } else { W '  API 키     파일 없음 (무키로도 동작)' }
  foreach ($n in @('auth.db','pc_id','shortcuts_asked')) {
    W ("  {0,-16} {1}" -f $n, $(if (Test-Path (Join-Path $data $n)) { '있음' } else { '없음' }))
  }
} else { W '  .data 없음 - 앱이 한 번도 제대로 뜨지 못했다' }

HR '로그 - 오류만 (마지막 40건)'
$logs = @()
if (Test-Path $data) { $logs = Get-ChildItem (Join-Path $data 'logs') -Filter *.log -ErrorAction SilentlyContinue }
if (-not $logs) {
  W '  *** 로그 파일이 없습니다. 앱이 기동조차 못 했을 수 있습니다. ***'
} else {
  foreach ($l in $logs) {
    W ("  [" + $l.Name + "  " + $l.Length + " bytes  " + $l.LastWriteTime + "]")
    $txt = Get-Content $l.FullName -Encoding UTF8 -ErrorAction SilentlyContinue
    $hit = $txt | Select-String -Pattern 'traceback|error|exception|failed|실패|" 5\d\d ' -CaseSensitive:$false
    if (-not $hit) { W '    오류 없음' }
    else { $hit | Select-Object -Last 40 | ForEach-Object { W ('    ' + (Mask $_.Line.Trim())) } }
    W ("    (전체 " + $txt.Count + "줄)")
  }
}

HR '윈도우 이벤트 로그 - Plutus 관련 (최근 10건)'
try {
  $ev = Get-WinEvent -FilterHashtable @{LogName='Application'; Level=1,2; StartTime=(Get-Date).AddDays(-3)} -ErrorAction SilentlyContinue |
        Where-Object { $_.Message -match 'Plutus|python312|WebView2' } | Select-Object -First 10
  if ($ev) { foreach ($e in $ev) { W ('  ' + $e.TimeCreated + '  ' + ($e.Message -split "`n")[0]) } }
  else { W '  관련 항목 없음' }
} catch { W '  (조회 실패)' }

HR '완료'
$rp = Join-Path $root 'plutus-진단.txt'
try {
  $out.ToString() | Out-File -FilePath $rp -Encoding utf8
  W ("  결과를 저장했습니다: " + (Mask $rp))
} catch { W '  (파일 저장 실패 - 위 내용을 직접 복사하세요)' }
W '  이 내용을 그대로 전달해 주세요. 화면 증상(빈 창 / 즉시 종료 /'
W '  로그인 실패 / 글자 깨짐)도 한 줄 적어 주시면 더 빠릅니다.'
W ''
if ($Host.UI.RawUI -and -not $env:PLUTUS_DIAG_NOWAIT) {
  try { Read-Host '엔터를 누르면 닫힙니다' } catch {}
}
