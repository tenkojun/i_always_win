# I ALWAYS WIN — 기관급 퀀트 분석 터미널

## 프로젝트
- 목표: BlackRock Aladdin 동급+ 퀀트 분석 플랫폼
- 실행: `python run_desktop.py` → http://127.0.0.1:8765
- EXE: `pyinstaller app.spec --noconfirm` → dist\QuantTerminal\

## 기술 스택
- **Backend**: Python 3.13, Flask (port 8765)
- **Frontend**: Vanilla JS + CSS (eDEX-UI 비주얼 + 토스 UX)
- **Data**: yfinance + Stooq (무키) / Finnhub + AlphaVantage + FMP (키)
- **ML**: scikit-learn, xgboost, torch (LSTM/GRU/Transformer)
- **Quant**: scipy, statsmodels, arch, hmmlearn
- **Deploy**: PyInstaller (Windows EXE), 폰은 LAN 접속

## 디렉토리
```
e/
├── main.py              # 분석 오케스트레이터 (13단계)
├── run_desktop.py       # 런처
├── app.spec             # PyInstaller
├── webapp/
│   ├── server.py        # Flask API
│   └── templates/index.html  # 메인 UI
└── engine/
    ├── data/            # 다중소스 데이터 레이어
    ├── backtest/        # 백테스팅
    ├── risk/            # 리스크 메트릭
    ├── ml/              # 머신러닝 모델
    ├── institutional/   # PSR/DSR/CDaR/스트레스/스코어카드
    ├── signal_engine/   # 메타 의사결정 (거부권/충돌해소)
    ├── explain/         # XAI 인과추적
    └── report/          # HTML+JSON 리포트
```

## 핵심 규칙
- 단계별 진행 → 완료 후 보고 → 확인 후 다음 단계
- 파일 용량 무제한 (최고 품질 우선)
- 모든 UI/리포트 한글
- API 키는 코드/로그에 절대 기록 금지 (keyconfig.py 통해서만)
- 새 모듈은 무키 폴백 필수
- OHLCV 소문자 컬럼 + DatetimeIndex 계약 유지

## 보조 문서 (필요 시 로드)
| 주제 | 파일 | 로드 조건 |
|------|------|-----------|
| API 엔드포인트 | `.claude/docs/api.md` | server.py 작업 시 |
| 프론트엔드 | `.claude/docs/frontend.md` | UI/CSS/JS 작업 시 |
| 데이터 소스 | `.claude/docs/data.md` | 데이터/provider 작업 시 |
| 배포/EXE | `.claude/docs/deploy.md` | 빌드/패키징 작업 시 |
| 빌드 현황 | `.claude/docs/build_status.md` | 진행상황 확인 시 |
| 백로그 | `.claude/docs/backlog.md` | 다음 기능 작업 시 |
| 알라딘 벤치마크 | `.claude/docs/aladdin.md` | 기관 방법론 작업 시 |

## 멀티 터미널 전략
작업 요청 시 자동 라우팅:
- `[BE]` 태그 or 서버/엔진/분석 관련 → **백엔드 터미널** (`.claude/agents/backend.md`)
- `[FE]` 태그 or UI/차트/CSS 관련 → **프론트엔드 터미널** (`.claude/agents/frontend.md`)
- `[DEPLOY]` 태그 or 빌드/패키징/문서 관련 → **배포 터미널** (`.claude/agents/deploy.md`)
- 복합 작업 → 메인 터미널이 분리 후 병렬 처리
