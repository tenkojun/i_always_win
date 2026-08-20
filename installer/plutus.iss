; ============================================================
;  Plutus — 설치 프로그램 (Inno Setup 6)
; ============================================================
;
;  빌드:  python tools/release.py --build     (release.py 가 자동 호출)
;  직접:  ISCC.exe /DMyVersion=3.4.3 installer\plutus.iss
;  결과:  dist\Plutus-Setup-x64.exe
;
;  이 설치 프로그램이 존재하는 이유
;  --------------------------------
;  전에는 zip 을 풀고, WebView2 를 확인하고, 바로가기를 만들고, 시작
;  메뉴에 등록하는 걸 **사람이 PC 마다 하나씩** 했다. 낯선 PC(PC방 등)
;  일수록 빠뜨리는 게 많았다. 그 전부를 여기서 한 번에 한다.
;
;  설치 위치를 왜 Program Files 로 안 하는가
;  -----------------------------------------
;  Plutus 는 실행 파일 옆 `.data\` 에 키·계정 DB·캐시를 쓴다. Program
;  Files 는 관리자만 쓸 수 있어서, 거기 깔면 앱이 자기 데이터를 못 쓴다.
;  그래서 %LOCALAPPDATA%\Programs\Plutus 에 사용자 권한으로 깐다.
;  **관리자 권한(UAC)이 아예 필요 없다** — PC방·회사 PC 에서 중요하다.

#ifndef MyVersion
  #define MyVersion "0.0.0"
#endif

#define MyName      "Plutus"
#define MyPublisher "Tenko jun"
#define MyTagline   "기관급 퀀트 분석 터미널"
#define MyURL       "https://github.com/tenkojun/plutus"
#define MyExe       "Plutus.exe"

[Setup]
AppId={{8E3A1C42-9D57-4B06-A1F3-2C7E5B4D9081}
AppName={#MyName}
AppVersion={#MyVersion}
AppVerName={#MyName} {#MyVersion}
AppPublisher={#MyPublisher}
AppPublisherURL={#MyURL}
AppSupportURL={#MyURL}/issues
AppUpdatesURL={#MyURL}/releases
VersionInfoVersion={#MyVersion}
VersionInfoDescription={#MyName} 설치 프로그램
VersionInfoCompany={#MyPublisher}

; 사용자 영역 설치 — UAC 를 띄우지 않는다
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
DefaultDirName={localappdata}\Programs\{#MyName}
DefaultGroupName={#MyName}
DisableProgramGroupPage=yes
DisableDirPage=no
AllowNoIcons=yes

LicenseFile=..\LICENSE
OutputDir=..\dist
OutputBaseFilename=Plutus-Setup-x64
SetupIconFile=..\assets\app.ico
UninstallDisplayIcon={app}\{#MyExe}
UninstallDisplayName={#MyName} {#MyVersion}

; 194MB 를 담아야 한다. lzma2/max 가 zip 보다 한참 작다.
Compression=lzma2/max
SolidCompression=yes
LZMANumBlockThreads=4

ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0

WizardStyle=modern
ShowLanguageDialog=no
CloseApplications=yes
CloseApplicationsFilter=*.exe
RestartApplications=no

[Languages]
Name: "korean"; MessagesFile: "compiler:Default.isl"

[Messages]
korean.BeveledLabel={#MyName} — {#MyTagline}

[CustomMessages]
korean.CreateDesktopIcon=바탕화면에 바로가기 만들기
korean.LaunchApp={#MyName} 실행
korean.InstallingWebView2=Microsoft Edge WebView2 런타임 설치 중… (최초 1회, 몇 분 걸릴 수 있습니다)
korean.WebView2Failed=WebView2 런타임 설치에 실패했습니다.%n%n{#MyName} 는 설치되지만 앱 창 대신 기본 브라우저로 열립니다.%n기능은 전부 그대로 동작합니다.%n%n나중에 직접 설치하시려면:%nhttps://developer.microsoft.com/microsoft-edge/webview2/
korean.KeepData={#MyName} 의 설정과 데이터를 남길까요?%n%n여기에는 API 키, 계정 정보, 저장된 보고서가 들어 있습니다.%n%n[예] 남긴다 (다시 설치하면 그대로 이어서 씁니다)%n[아니오] 전부 지운다

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
; 앱 본체. dist\Plutus\ 를 통째로.
Source: "..\dist\{#MyName}\{#MyExe}"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\dist\{#MyName}\_internal\*"; DestDir: "{app}\_internal"; \
    Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\dist\{#MyName}\진단.ps1"; DestDir: "{app}"; Flags: ignoreversion skipifsourcedoesntexist

; WebView2 부트스트래퍼 — 런타임이 없을 때만 돌린다. 설치 후엔 남기지 않는다.
Source: "vendor\MicrosoftEdgeWebview2Setup.exe"; DestDir: "{tmp}"; \
    Flags: deleteafterinstall; Check: NeedsWebView2

[Icons]
; 시작 메뉴 — **윈도우 검색에 뜨려면 이게 있어야 한다.** 바탕화면만으로는
; 색인되지 않는다. 검색은 Start Menu\Programs 를 본다.
Name: "{autoprograms}\{#MyName}"; Filename: "{app}\{#MyExe}"; \
    WorkingDir: "{app}"; Comment: "{#MyName} - {#MyTagline}"
Name: "{autodesktop}\{#MyName}"; Filename: "{app}\{#MyExe}"; \
    WorkingDir: "{app}"; Comment: "{#MyName} - {#MyTagline}"; Tasks: desktopicon

[Run]
; 런타임이 없을 때만. //silent //install 은 부트스트래퍼의 무인 설치 인자다.
Filename: "{tmp}\MicrosoftEdgeWebview2Setup.exe"; \
    Parameters: "/silent /install"; \
    StatusMsg: "{cm:InstallingWebView2}"; \
    Check: NeedsWebView2; Flags: waituntilterminated runhidden

Filename: "{app}\{#MyExe}"; Description: "{cm:LaunchApp}"; \
    WorkingDir: "{app}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; 앱이 실행 중에 만드는 것들. .data 는 여기서 지우지 않는다 — 아래 코드가
; 사용자에게 물어본 뒤에만 지운다.
Type: filesandordirs; Name: "{app}\_internal"
Type: files; Name: "{app}\plutus-진단.txt"

[Code]

// ── WebView2 런타임 탐지 ──────────────────────────────────────
// pywebview 가 이 위에 창을 그린다. 없으면 앱 창이 안 뜬다.
// 윈도우 11 엔 기본 탑재지만 정리된 이미지·LTSC·Edge 제거 PC 엔 없다.
// 레지스트리 세 곳을 다 본다 — 시스템 설치(64/32비트 뷰)와 사용자 설치.
const
  WV2_GUID = '{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}';

function WebView2Installed: Boolean;
var
  pv: String;
  base: String;
begin
  base := 'SOFTWARE\Microsoft\EdgeUpdate\Clients\' + WV2_GUID;
  Result :=
    ((RegQueryStringValue(HKLM, 'SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\' + WV2_GUID, 'pv', pv)) and (pv <> '') and (pv <> '0.0.0.0')) or
    ((RegQueryStringValue(HKLM, base, 'pv', pv)) and (pv <> '') and (pv <> '0.0.0.0')) or
    ((RegQueryStringValue(HKCU, base, 'pv', pv)) and (pv <> '') and (pv <> '0.0.0.0'));
end;

function NeedsWebView2: Boolean;
begin
  Result := not WebView2Installed;
end;

// 설치가 끝난 뒤 다시 확인한다. 여전히 없으면 (네트워크 차단 등)
// 조용히 넘어가지 않고 말해 준다 — 앱은 브라우저 모드로 돌아간다.
procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
  begin
    if not WebView2Installed then
      MsgBox(ExpandConstant('{cm:WebView2Failed}'), mbInformation, MB_OK);

    // 설치 프로그램이 바로가기를 이미 만들었으니 앱이 첫 실행 때 또
    // 물어보지 않게 한다.
    ForceDirectories(ExpandConstant('{app}\.data'));
    SaveStringToFile(ExpandConstant('{app}\.data\shortcuts_asked'), '1', False);
  end;
end;

// ── 제거 ─────────────────────────────────────────────────────
// .data 에는 API 키와 계정 DB, 저장된 보고서가 들어 있다. 말없이
// 지우면 안 된다. 물어보고, 답한 대로만 한다.
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  dataDir: String;
begin
  if CurUninstallStep = usUninstall then
  begin
    dataDir := ExpandConstant('{app}\.data');
    if DirExists(dataDir) then
      if MsgBox(ExpandConstant('{cm:KeepData}'), mbConfirmation, MB_YESNO) = IDNO then
        DelTree(dataDir, True, True, True);
  end;
end;
