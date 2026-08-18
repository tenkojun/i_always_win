@echo off
REM ============================================================
REM  I ALWAYS WIN  -  Windows .exe 빌드 (원클릭)
REM  사용법: 이 파일을 더블클릭하거나 cmd에서 실행
REM ============================================================
echo [1/3] 의존성 설치...
pip install -r requirements.txt
pip install pyinstaller pywebview

echo [2/3] .exe 빌드 (수 분 소요)...
pyinstaller app.spec --noconfirm

echo [3/3] 완료!
echo 결과물: dist\QuantTerminal\QuantTerminal.exe
echo 더블클릭하면 대시보드가 열립니다.
pause
