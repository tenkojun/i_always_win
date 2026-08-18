# 배포 터미널 — I ALWAYS WIN Deploy Agent

## 역할
빌드 / 패키징 / 문서화 / 환경 관리 전담

## 담당 파일
```
app.spec              (PyInstaller 스펙)
build_windows_exe.bat (EXE 빌드 배치)
run_desktop.py        (런처)
requirements.txt      (의존성)
CLAUDE.md             (메인 문서)
.claude/docs/         (보조 문서)
QUICKSTART_TERMINAL.md
README.md
```

## 필수 로드 문서
- `.claude/docs/deploy.md` (빌드 절차/트러블슈팅)

## EXE 빌드 절차
```bat
cd C:\Users\jun\Desktop\e
pip install pyinstaller pywebview
pyinstaller app.spec --noconfirm
:: 결과: dist\QuantTerminal\QuantTerminal.exe
```

## requirements.txt 관리 원칙
- torch/tensorflow: 선택적 (ML 모델 rf 기본, torch 없어도 동작)
- 필수 코어: numpy pandas scipy matplotlib scikit-learn
- 필수 퀀트: yfinance statsmodels arch hmmlearn
- 필수 웹: flask jinja2 requests

## app.spec 주의사항
- `excludes`: torch, tensorflow (EXE 용량 절감)
- `datas`: NotoSansKR-Engine.ttf 반드시 포함
- `name`: QuantTerminal (폴더명 유지)

## 패키지 생성 (ZIP)
```python
import zipfile, os
with zipfile.ZipFile('engine_kr.zip', 'w', zipfile.ZIP_DEFLATED) as z:
    for root, dirs, files in os.walk('.'):
        # __pycache__, .git, dist, build 제외
        for f in files:
            z.write(os.path.join(root, f))
```

## 문서화 규칙
- CLAUDE.md: 최소화 (항상 로드)
- .claude/docs/: 보조 문서 (필요 시 로드)
- 마크다운 헤딩/리스트 중심, 불필요한 설명 제거
- 코드 블록으로 명령어/구조 표현

## 작업 시작 체크리스트
- [ ] requirements.txt 최신 상태 확인
- [ ] app.spec datas 리스트 확인 (TTF 포함)
- [ ] dist/ build/ __pycache__ 정리 후 빌드
- [ ] EXE 실행 테스트 (더블클릭 → 브라우저 오픈)
- [ ] ZIP 패키지 용량 확인 (불필요 파일 제외)
