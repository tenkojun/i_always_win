@echo off
chcp 65001 >nul
REM ============================================================
REM  I ALWAYS WIN  -  Windows .exe 빌드 (원클릭)
REM  사용법: 이 파일을 더블클릭하거나 cmd 에서 실행
REM ============================================================
cd /d "%~dp0"

echo [1/4] 의존성 설치...
python -m pip install -r requirements.txt || goto :error
python -m pip install pyinstaller pillow || goto :error

echo.
echo [2/4] 버전 리소스 생성...
python tools\make_version_info.py || goto :error

echo.
echo [3/4] .exe 빌드 (수 분 소요)...
python -m PyInstaller app.spec --noconfirm || goto :error

echo.
echo [4/4] 배포본 정리...
REM 빌드 후 한 번이라도 실행했다면 dist 안에 .data 가 생긴다.
REM 거기엔 API 키와 인증 DB 가 들어 있으므로 배포 전에 반드시 지운다.
if exist "dist\IAlwaysWin\.data" (
  echo   - dist 안의 .data 제거 ^(API 키/인증 DB 포함^)
  rmdir /s /q "dist\IAlwaysWin\.data"
)

echo.
echo 완료!
echo   결과물 : dist\IAlwaysWin\IAlwaysWin.exe
echo   로그   : 실행 후 문제가 있으면 .data\logs\app.log 를 확인하세요.
echo.
echo 더블클릭하면 콘솔 창 없이 앱 창만 열립니다.
echo.
echo [주의] 이 폴더를 남에게 전달하기 전에 IAlwaysWin\.data 폴더가
echo        없는지 확인하세요. API 키와 계정 DB 가 들어 있습니다.
pause
exit /b 0

:error
echo.
echo *** 빌드 실패 *** 위 메시지를 확인하세요.
pause
exit /b 1
