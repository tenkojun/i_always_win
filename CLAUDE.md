# Plutus — 기관급 퀀트 분석 터미널

## 프로젝트
- 목표: BlackRock Aladdin 동급+ 퀀트 분석 플랫폼
- 개발자: Tenko jun - 정준화 / 저장소: https://github.com/tenkojun/i_always_win
- 버전 단일 소스: `version.py` — **업데이트마다 올린다**(현재 2.14.0)
- 실행: `python run_desktop.py` → http://127.0.0.1:8765
- EXE: `pyinstaller app.spec --noconfirm` → `dist\Plutus\` (약 138MB)

## 기술 스택
- **Backend**: Python 3.12.10 (pyenv-win), Flask (포트 8765)
- **Desktop**: pywebview + PyInstaller (`console=False`)
- **Frontend**: Vanilla JS + CSS 단일 파일 `webapp/static/index.html`
- **Data**: yfinance + Stooq (무키) / Finnhub · AlphaVantage · FMP (키)
- **Quant**: numpy · pandas · scipy · scikit-learn
- **Auth**: Cloudflare Workers + D1 (`auth-worker/`) — 무료 등급

> `arch` · `statsmodels` · `hmmlearn` · `torch` · `xgboost` 는 **설치돼 있지 않다.**
> 이들을 import 하는 구엔진 모듈(`engine/volatility/garch.py`, `engine/ml/*`,
> `engine/risk/*`)은 전부 try/except 폴백을 갖고 있어 죽지 않고 성능만 낮아진다.
> 실제 분석 경로인 `engine/jiqtx/` 는 GJR-GARCH-t MLE · HAR-RV · Jump Model 을
> **scipy 로 직접 구현**한다. 새 코드에서 저 라이브러리에 의존하지 말 것.

## 디렉토리
```
e/
├── version.py           # 이름·버전·개발자 단일 소스
├── run_desktop.py       # 런처 (pywebview 창)
├── main.py              # 구 분석 오케스트레이터 (328줄, 레거시)
├── app.spec             # PyInstaller — hiddenimports 에 엔진 모듈 전부 명시
├── auth-worker/         # Cloudflare Worker 인증 서버 (EXE 에 포함 안 함)
├── webapp/
│   ├── server.py        # Flask API (120 라우트)
│   └── static/index.html   # 메인 UI 단일 파일
└── engine/
    ├── paths.py         # 런타임 경로 단일 결정 (.data/)
    ├── console.py       # stdout/stderr UTF-8 강제
    ├── jiqtx/           # ★ 정밀 분석 엔진 (32 모듈 / 14,790줄) — 실제 분석 경로
    ├── data/            # 다중소스 데이터 레이어 + keyconfig
    ├── auth/ auth_remote/  # 세션 · 중앙 인증 클라이언트
    ├── cloud/           # 터널 (외부 접근)
    ├── institutional/ risk/ factor/ volatility/ ml/  # 레거시 (폴백 상태)
    ├── signal_engine/ explain/ portfolio/ awareness/ llm/
    └── report/          # HTML+JSON 리포트
```

## 핵심 규칙
- 단계별 진행 → 완료 후 보고 → 확인 후 다음 단계
- 파일 용량 무제한 (최고 품질 우선)
- 모든 UI/리포트 한글
- **API 키는 코드/로그/커밋에 절대 기록 금지** — `keyconfig.py` 경유, 설정 화면에서 입력
- 새 모듈은 무키 폴백 필수
- OHLCV 소문자 컬럼 + DatetimeIndex 계약 유지
- 런타임 산출물은 전부 `.data/` 아래 (앱 폴더 밖에 상태를 두지 않는다)
- 기능 변경 후에는 `version.py` 올리고 CHANGELOG 쓰고 커밋·푸시

## 분석 엔진 계약 (engine/jiqtx)
- 진입점 `jiqtx.analyze(ticker, cfg=...)` → `Analysis` 데이터클래스
- 보고서 `jiqtx.render_html(a, theme=...)` / `save_html(a, path, theme=...)`
- **외부 리소스 0개**의 자기완결 HTML — 차트는 인라인 SVG, 테마는 생성 시점에 주입
- 섹션 레지스트리 37개, 각 섹션이 스스로 `applies()` 를 판정한다.
  없는 데이터를 빈칸으로 채우지 말고 **섹션 자체를 내릴 것**
- 단일 종합 점수를 만들지 않는다 — 방향 / 리스크 예산 / 모델 신뢰도 3축 분리
- 단/중/장 지평(`horizons.py`)도 합치지 않는다. 어긋나는 지점을 드러내는 게 목적
- 거시 보드(`macro_board.py`)는 `|t| < 2` 면 중립. 유의하지 않은 베타로 서사 금지
- 로그수익률 변수와 수준 변수를 섞지 말 것 (섞으면 기여도가 자릿수로 튄다)

## 인증
- **중앙 인증 전용** — 오프라인/로컬 계정은 v2.x 에서 제거됨
- 기본 서버: `version.py` 의 `DEFAULT_AUTH_SERVER`
- "누구인가"(중앙)와 "이 브라우저가 로그인했는가"(쿠키 세션)를 분리한다.
  PC 단위 세션으로 만들면 터널로 들어온 아무나 소유자가 된다
- Workers 의 PBKDF2 반복은 **100,000 상한** (`wrangler dev --local` 은 강제 안 함)

## 보조 문서 (필요 시 로드)
| 주제 | 파일 | 로드 조건 |
|------|------|-----------|
| API 엔드포인트 | `.claude/docs/api.md` | server.py 작업 시 |
| 프론트엔드 | `.claude/docs/frontend.md` | UI/CSS/JS 작업 시 |
| 데이터 소스 | `.claude/docs/data.md` | 데이터/provider 작업 시 |
| 배포/EXE | `.claude/docs/deploy.md` | 빌드/패키징 작업 시 |
| 빌드 현황 | `.claude/docs/build_status.md` | 진행상황 확인 시 |
| 알라딘 벤치마크 | `.claude/docs/aladdin.md` | 기관 방법론 작업 시 |

## 멀티 터미널 전략
작업 요청 시 자동 라우팅:
- `[BE]` 태그 or 서버/엔진/분석 관련 → **백엔드 터미널** (`.claude/agents/backend.md`)
- `[FE]` 태그 or UI/차트/CSS 관련 → **프론트엔드 터미널** (`.claude/agents/frontend.md`)
- `[DEPLOY]` 태그 or 빌드/패키징/문서 관련 → **배포 터미널** (`.claude/agents/deploy.md`)
- 복합 작업 → 메인 터미널이 분리 후 병렬 처리
