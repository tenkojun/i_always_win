# 배포 & 빌드 (Windows EXE / 모바일 LAN)

## 빠른 실행
```bat
cd C:\Users\jun\Desktop\e
python run_desktop.py
```
→ 브라우저 자동 오픈, 콘솔에 PC/폰 주소 출력

## EXE 빌드
```bat
pip install pyinstaller pillow
python tools\make_version_info.py
pyinstaller app.spec --noconfirm
```
- 결과: `dist\IAlwaysWin\IAlwaysWin.exe`
- 배포: `dist\IAlwaysWin\` 폴더 통째로 복사 (EXE 단독 불가)
- 소요: 5~15분

## app.spec 설정
- `console=False` — 콘솔 창 없음. 진단 로그는 `.data\logspp.log`
- `icon=assets/app.ico`, `version=version_info.txt`
  (`tools/make_version_info.py` 가 `version.py` 에서 자동 생성)
- excludes: tensorflow, vectorbt, numba, llvmlite, shap,
  transformers, tkinter, PyQt/PySide, jupyter
- hiddenimports 에 `engine.jiqtx.*` 25개 모듈 전부 명시
  (동적 임포트가 많아 PyInstaller 가 놓친다)
- NotoSansKR-Engine.ttf 번들 포함
- 앱 이름: IAlwaysWin

## 폰 접속
1. `python run_desktop.py` 실행
2. 콘솔에서 `폰: http://192.168.x.x:8765` 확인
3. 같은 와이파이 폰 브라우저에서 접속

## 의존성 설치
```bat
python -m pip install -r requirements.txt
```

## 자주 발생하는 문제
| 증상 | 해결 |
|------|------|
| `pip` 명령 안 됨 | `python -m pip install ...` |
| 포트 8765 충돌 | 이전 cmd 창 닫고 재실행 |
| EXE 콘솔 바로 꺼짐 | cmd에서 직접 실행해 오류 확인 |
| ModuleNotFoundError | `python -m webapp.server` 로 직접 실행 |
| 윈도우 디펜더 차단 | "추가 정보 → 실행" 또는 예외 등록 |

## Python 버전 주의
- 현재: Python 3.13.13
- torch/hmmlearn: 3.13 미리빌드 없을 수 있음 → 대체 설치:
```bat
pip install numpy pandas scipy matplotlib scikit-learn yfinance statsmodels arch hmmlearn jinja2 flask requests tqdm
```

## Colab 실행 (구버전 참고용)
```python
from main import analyze
res = analyze('005930.KS', start='2018-01-01', initial_capital=10_000_000)
from IPython.display import HTML
HTML(open(res['report_paths']['html']).read())
```
