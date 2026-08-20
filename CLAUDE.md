# Plutus — 기관급 퀀트 분석 터미널

## 프로젝트
- 목표: BlackRock Aladdin 동급+ 퀀트 분석 플랫폼
- 개발자: Tenko jun - 정준화
- 저장소: https://github.com/tenkojun/plutus *(v3.4.2 에서 `i_always_win`
  에서 리네임. 옛 주소는 GitHub 가 리다이렉트한다. 아래 "이름" 참조)*
- 버전 단일 소스: `version.py` — **업데이트마다 올린다** (현재 3.4.5)
- 실행: `python run_desktop.py` → http://127.0.0.1:8765
- EXE: `python tools/release.py --build` → `dist\Plutus\` (약 194MB)
- 설치본: 같은 명령이 `dist\Plutus-Setup-x64.exe` 도 굽는다 (Inno Setup)
- 라이선스: **MIT** — 외부 저작물 0개 (사운드는 전부 Web Audio 합성)

## 기술 스택
- **Backend**: Python 3.12.10 (pyenv-win), Flask (포트 8765, 라우트 125개)
- **Desktop**: pywebview + PyInstaller (`console=False`)
- **Frontend**: Vanilla JS + CSS 단일 파일 `webapp/static/index.html`
- **Data**: yfinance + Stooq (무키) / Finnhub · AlphaVantage · FMP · Alpaca (키)
- **Quant**: numpy · pandas · scipy · scikit-learn
- **Auth**: Cloudflare Workers + D1 (`auth-worker/`) — 무료 등급

> `arch` · `statsmodels` · `hmmlearn` · `torch` · `xgboost` · `matplotlib` ·
> `PIL` 은 **설치돼 있지 않고 쓰지도 않는다.** 이들을 import 하던 구엔진은
> v2.15.0 에서 전부 제거됐다. `engine/jiqtx/` 가 GJR-GARCH-t MLE · HAR-RV ·
> Jump Model 을 **scipy 로 직접 구현**하고, 차트는 인라인 SVG 를 **문자열로**
> 만든다. 새 코드에서 저 라이브러리에 의존하지 말 것.

## 디렉토리
```
e/
├── version.py           # 이름·버전·개발자 단일 소스
├── run_desktop.py       # 런처 (pywebview 창)
├── app.spec             # PyInstaller — hiddenimports 에 엔진 모듈 전부 명시
├── auth-worker/         # Cloudflare Worker 인증 서버 (EXE 에 포함 안 함)
├── tools/
│   ├── release.py           # 빌드 → 포장 → 검증 → 태그 → 발행 (한 줄)
│   ├── backfill_releases.py # 과거 버전 소급 발행
│   ├── make_icons.py        # 로고 1개 → 마크·파비콘·ico
│   ├── make_version_info.py # 윈도우 버전 리소스
│   └── diagnose.ps1         # 진단 (릴리스에 `진단.ps1` 로 동봉)
├── installer/
│   ├── plutus.iss       # Inno Setup — 설치 프로그램
│   └── vendor/          # WebView2 부트스트래퍼 (gitignore, 빌드 시 자동 수신)
├── webapp/
│   ├── server.py        # Flask API
│   └── static/
│       ├── index.html            # 메인 UI 단일 파일
│       ├── plutus.png            # 로고 원본 (make_icons 가 읽기만 한다)
│       ├── plutus_mark.png       # 마크 — 흰 잉크 (어두운 배경용)
│       └── plutus_mark_light.png # 마크 — 어두운 잉크 (밝은 배경용)
└── engine/
    ├── paths.py         # 런타임 경로 단일 결정 (.data/)
    ├── console.py       # stdout/stderr UTF-8 강제
    ├── updater.py       # GitHub 릴리스 감지 · 다운로드 · 교체
    ├── jiqtx/           # ★ 정밀 분석 엔진 (33 모듈 / 15,601줄) — 실제 분석 경로
    ├── data/            # 다중소스 + keyconfig + market_flow(수급 스캐너)
    ├── auth/            # 세션 · prefs · quota(등급·일일한도)
    ├── auth_remote/ cloud/ jobs/ community/ portfolio/ awareness/ llm/
```

## 핵심 규칙
- 단계별 진행 → 완료 후 보고 → 확인 후 다음 단계
- 파일 용량 무제한 (최고 품질 우선)
- 모든 UI/리포트 한글
- **API 키는 코드/로그/커밋에 절대 기록 금지** — `keyconfig.py` 경유, 설정 화면 입력
- 새 모듈은 무키 폴백 필수
- OHLCV 소문자 컬럼 + DatetimeIndex 계약 유지
- 런타임 산출물은 전부 `.data/` 아래 (앱 폴더 밖에 상태를 두지 않는다)
- 기능 변경 후 `version.py` 올리고 CHANGELOG 쓰고 커밋·푸시

## 분석 엔진 계약 (engine/jiqtx)
- 진입점 `jiqtx.analyze(ticker, cfg=...)` → `Analysis` 데이터클래스
- 전문 보고서 `jiqtx.render_html(a, theme=...)` / `save_html(...)`
- 간단 리서치 `simple_report.render_simple(a, theme=..., full_report_url=...)`
  — **같은 Analysis 를 쓰고 보여 주는 방식만 바꾼다.** 재계산 없음
- **외부 리소스 0개**의 자기완결 HTML — 차트는 인라인 SVG, 테마는 생성 시점 주입
- 섹션 레지스트리 37개, 각 섹션이 스스로 `applies()` 판정.
  없는 데이터를 빈칸으로 채우지 말고 **섹션 자체를 내릴 것**
- 단일 종합 점수를 만들지 않는다 — 방향 / 리스크 예산 / 모델 신뢰도 3축 분리
- 단/중/장 지평(`horizons.py`)도 합치지 않는다. 어긋나는 지점을 드러내는 게 목적
- 거시 보드(`macro_board.py`)는 `|t| < 2` 면 중립. 유의하지 않은 베타로 서사 금지
- 로그수익률 변수와 수준 변수를 섞지 말 것 (섞으면 기여도가 자릿수로 튄다)
- 용어집 66개 — 정의만 쓰지 말고 **왜 보는지 + 어떤 값이면 문제인지**

## 인증 · 등급
- **중앙 인증 전용** — 오프라인/로컬 계정은 v2.x 에서 제거됨
- "누구인가"(중앙)와 "이 브라우저가 로그인했는가"(쿠키 세션)를 분리한다.
  PC 단위 세션으로 만들면 터널로 들어온 아무나 소유자가 된다
- Workers 의 PBKDF2 반복은 **100,000 상한**
- 등급 3단계 (`engine/auth/quota.py`) — 무료 3회/일 · 프리미엄 무제한 ·
  플래티넘 전 기능. **한도 판정은 전부 서버에서** 한다. 확인과 증가를 한
  트랜잭션(`BEGIN IMMEDIATE`)에서 처리해 동시 클릭으로 넘길 수 없다.
  `user_id` 가 없으면 진행하지 않는다 — NULL 키로 쓰면 SQLite 가 PK 중복을
  허용해 카운트가 누적되지 않는다(실제로 한도가 통째로 우회됐다)

## 업데이트 · 릴리스
- 발행: `python tools/release.py --build` (CHANGELOG 확인 → 빌드 →
  `.data` 삭제 → 포장 → 업데이터 검증 → 태그 → `gh release create --latest`)
- 앱은 GitHub Releases 를 보고 새 버전을 알린다. 교체 대상은
  `Plutus.exe` 와 `_internal/` 뿐 — **`.data/` 는 손대지 않는다**
- 교체 스크립트(`.ps1`)는 **반드시 `utf-8-sig`(BOM)** 로 쓴다. BOM 이 없으면
  Windows PowerShell 5.1 이 cp949 로 읽어 한글이 깨지고 **파싱 자체가 실패**한다
  — 로그도 안 남아 원인을 찾기 어렵다 (v3.1.1 에서 실제로 그랬다)
- 소급 발행은 `--latest=false` 필수. 안 주면 옛 버전이 Latest 가 된다
- 릴리스 자산 2개 — `Plutus-Setup-x64.exe`(설치본) · `Plutus-win-x64.zip`(portable)

## 설치 프로그램 (installer/plutus.iss)
- **`%LOCALAPPDATA%\Programs\Plutus` 에 깐다. Program Files 가 아니다.**
  Plutus 는 실행 파일 옆 `.data\` 에 쓰는데 Program Files 는 관리자만
  쓸 수 있다 — 거기 깔면 앱이 자기 데이터를 못 쓴다. `PrivilegesRequired=lowest`
  라 **UAC 가 아예 안 뜬다** (PC방·회사 PC 에서 중요)
- WebView2 는 레지스트리 3곳(64/32비트 시스템 · 사용자)을 다 보고 없을
  때만 무인 설치. 설치 후 재확인해서 실패했으면 **말해 준다**
- 시작 메뉴 등록이 윈도우 검색의 조건이다. 바탕화면만으로는 색인 안 됨
- 제거 시 `.data\`(키·계정·보고서)를 **물어보고** 지운다. 무인 제거에서는
  남기는 쪽으로 답한다
- 부트스트래퍼는 커밋하지 않는다("외부 저작물 0개"). 빌드 때 받고
  **Authenticode 서명 검증** — Microsoft 아니면 빌드 중단
- 코드 서명 없음 → SmartScreen 경고. `추가 정보` → `실행`

## 이름
제품명은 **Plutus**(그리스 신화 부의 신)다. 저장소는 v3.4.2 에서
`i_always_win` → `plutus` 로 리네임했다(GitHub 에서 먼저 바꾸고 코드의
URL 을 따라 고쳤다). 옛 주소는 GitHub 가 웹·API 모두 리다이렉트하므로
구버전 앱의 업데이트 확인도 계속 동작한다.

아직 남은 옛 이름 둘은 **배포된 자원의 식별자**라 그대로 둔다.
바꾸면 각각 로그인이 끊기고, 기존 사용자 설정이 고아가 된다.

    https://iaw-auth.tenkojun.workers.dev   중앙 인증 Worker (URL)
    %LOCALAPPDATA%\i_always_win             폴백 데이터 폴더 (engine/paths.py)

앞의 것은 Cloudflare 에서 먼저 리네임해야 옮길 수 있고, 뒤의 것은 옮기려면
**마이그레이션 코드가 먼저** 있어야 한다 — 이름만 바꾸면 기존 PC 의 키·
계정 DB 를 앱이 못 찾는다.

## UI 주의
- 앱 테마는 `data-theme` 속성 + CSS 변수. **저장된 테마를 `<head>` 최상단
  스크립트에서 즉시 적용**한다 — 부팅 시퀀스가 그보다 위에서 돌기 때문에,
  아래쪽에서 적용하면 부팅 화면이 테마를 못 본다
- 색을 하드코딩하지 말 것. `--up` 은 **한국식으로 빨강**(상승)이라,
  서구 관례 색을 폴백으로 달면 변수가 빠지는 순간 의미가 뒤집힌다
- 보고서 툴팁은 `position:fixed` 레이어 하나를 body 직속으로 둔다.
  `.term` 안에 absolute 로 넣으면 `overflow:hidden` 조상에서 잘린다
- **마크(`plutus_mark.png`)는 흰 잉크 + 알파다. 밝은 배경에서 사라진다.**
  부팅 화면이 `var(--bg)` 를 따르게 되면서 white 테마에서 실제로 안 보였다.
  `<html>` 의 `light-bg` 클래스가 `invert(1)` 을 건다 —
  **테마 이름이 아니라 `--bg` 의 실측 밝기**로 판정한다(`refreshLightBg()`).
  custom 테마는 사용자가 `--bg` 를 아무 색으로나 정할 수 있어서, 이름
  목록을 박아 두면 그쪽으로 샌다
- 애니메이션이 `filter` 를 애니메이트하면 정적 `filter` 규칙을 **덮어쓴다.**
  그래서 `logoFocus` 의 밝은 배경용 쌍둥이 `logoFocusLight` 를 따로 둔다
- README 의 로고는 `<picture>` + `prefers-color-scheme` 으로 GitHub 의
  라이트/다크 테마에 각각 대응한다

## 보조 문서 (필요 시 로드)
| 주제 | 파일 | 로드 조건 |
|------|------|-----------|
| 릴리스 절차 | `docs/RELEASE.md` | 배포·태그 작업 시 |
| API 엔드포인트 | `.claude/docs/api.md` | server.py 작업 시 |
| 프론트엔드 | `.claude/docs/frontend.md` | UI/CSS/JS 작업 시 |
| 데이터 소스 | `.claude/docs/data.md` | 데이터/provider 작업 시 |
| 배포/EXE | `.claude/docs/deploy.md` | 빌드/패키징 작업 시 |
| 알라딘 벤치마크 | `.claude/docs/aladdin.md` | 기관 방법론 작업 시 |

## 멀티 터미널 전략
작업 요청 시 자동 라우팅:
- `[BE]` 태그 or 서버/엔진/분석 관련 → **백엔드 터미널** (`.claude/agents/backend.md`)
- `[FE]` 태그 or UI/차트/CSS 관련 → **프론트엔드 터미널** (`.claude/agents/frontend.md`)
- `[DEPLOY]` 태그 or 빌드/패키징/문서 관련 → **배포 터미널** (`.claude/agents/deploy.md`)
- 복합 작업 → 메인 터미널이 분리 후 병렬 처리
