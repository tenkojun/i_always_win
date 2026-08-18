# 변경 이력

이 프로젝트는 [시맨틱 버저닝](https://semver.org/lang/ko/)을 따릅니다.
버전 단일 소스는 [`version.py`](version.py) 입니다.

---

## [2.6.0] — 2026-08-18

**중앙 인증 강화.** 내 PC 가 꺼져 있어도 가입·승인·로그인이 살아 있도록
Cloudflare Workers + D1 (둘 다 무료 티어) 위의 인증 서버를 손봤다.

### 보안 (중요)
- **하드코딩된 기본 어드민 비밀번호 제거.** 두 군데 모두.
  - Worker: `env.ADMIN_PASSWORD || 'WNSGHK'` → 시크릿이 없으면
    어드민을 아예 만들지 않는다.
  - 로컬 SQLite: `ADMIN_PASSWORD = "WNSGHK"` 상수 삭제 →
    첫 실행 시 무작위 16자를 만들어 `.data/ADMIN_PASSWORD.txt`(0600)와
    콘솔에 1회만 표시. `IAW_ADMIN_PASSWORD` 환경변수로 지정도 가능.
  - 공개 저장소에 올리는 이상, 코드에 있는 비밀번호는 비밀번호가 아니다.
- **오픈 리다이렉트 차단.** `/go/<username>` 은 사용자가 등록한 주소로
  302 를 보낸다. 검증이 없어서 누구나 임의 주소를 등록하고 이 도메인의
  신뢰를 빌릴 수 있었다. 등록 시점과 리다이렉트 시점 **양쪽에서**
  https 여부·호스트 형태를 확인하고 사설/로컬 대역을 거부한다.
- **무차별 대입 차단.** username 과 IP 를 각각 카운트, 30분 창에서
  8회 실패 시 15분 잠금. 성공하면 카운터 삭제.
- 없는 사용자로 로그인해도 해시를 한 번 돌려 **응답 시간으로 계정 존재
  여부가 새지 않게** 했다.
- 500 응답에 예외 내용을 싣지 않는다.
- 비밀번호 규칙 강화 (6자 → 8자, 숫자만 금지), username 형식 검증.

### 추가
- `POST /auth/logout_all` — 모든 기기 세션 종료
- `POST /auth/change_password` — 변경 시 다른 기기 자동 로그아웃
- `GET /auth/sessions` — 내 활성 세션 목록 (기기·발급시각)
- `GET /pc/status` · `POST /pc/unregister`
- `migrations/0002_hardening.sql` — 레이트리밋·감사 로그 테이블
- 승인/거부/비밀번호 변경 감사 로그
- 앱 측 대응 라우트 5개 + `engine/auth_remote` 클라이언트 함수

### 변경 (무료 티어 대응)
- 어드민 시드 확인을 매 요청 → **isolate 당 1회**. 요청 수만큼 발생하던
  D1 읽기가 사라진다.
- 만료 세션 청소는 로그인 20회에 1번꼴(5%)로만 실행.
- `npm run db:init` 이 0001·0002 를 함께 적용.

### 검증
- `wrangler dev --local` + 로컬 D1 로 전 흐름 확인: 어드민 시드 ·
  가입(pending) → 승인 → 로그인 · 비어드민 admin API 403 ·
  8회 실패 후 429 잠금 · 잠금 중에도 발급된 토큰은 유효 ·
  비밀번호 변경 403/200 · 오픈 리다이렉트 4종 차단, 정상 터널 URL 1종 통과

---

## [2.5.0] — 2026-08-18

### 수정
- **테마 배경 이펙트가 거의 보이지 않던 문제.** 원인이 셋이었다.
  1. `body::after` 에 그려 놓고 `z-index:0` 이라, 불투명한 패널 뒤에
     깔려 사실상 화면에 나오지 않았다.
  2. `radial-gradient` 10개를 한 덩어리로 `translateY` 해서, 흩날림이
     아니라 무늬가 통째로 미끄러지는 것처럼 보였다.
  3. 회전·흔들림·깊이감이 전혀 없었다.
- 규칙 순서 문제도 함께 정리 — 테마별 `body::after` 가 기본 규칙보다
  **앞에** 선언돼 있어 배경 그리드까지 덮어쓰고 있었다.

### 추가
- **캔버스 파티클 엔진** (`#fx`, z-index 500 · `pointer-events:none`).
  입자마다 낙하속도·흔들림 주기·회전속도·투명도가 다르고,
  작은 입자는 느리고 흐리게 그려 깊이감을 준다.
  - 🌸 SAKURA — 베지어 곡선으로 그린 실제 꽃잎. `flutter` 값에 따라
    가로폭이 줄었다 늘어나며 앞뒤로 뒤집힌다. 잎맥까지 그린다.
  - ❄ SNOW — 크기별 눈송이, 완만한 표류
  - 🍁 AUTUMN — 구르며 떨어지는 낙엽 (잎맥 포함)
  - 🌊 OCEAN — 떠오르는 기포
- 설정 → 외관에 **배경 이펙트 세기** (끄기/약하게/기본/강하게).
  입자 수는 화면 넓이에 비례해 자동 조절 (1920×1080 기준 벚꽃 82개).
- 다른 탭으로 넘어가면 자동 정지, `prefers-reduced-motion` 존중.

---

## [2.4.0] — 2026-08-18

**JIQT-X 정밀 분석 엔진 병합.** 13,366줄 단일 파일 배포판을
원래 패키지 구조(`engine/jiqtx/`, 29개 모듈)로 되돌려 붙였다.

### 추가
- `engine/jiqtx/` — 범자산 정밀 분석 엔진
  - `statcore` PSR/DSR · Purged CV · CPCV/PBO · Murphy 분해 · ACI conformal ·
    Kupiec/Christoffersen 커버리지 · DM/SPA/Hansen MCS
  - `micro` EDGE 스프레드 · Amihud · 제곱근 임팩트 · capacity 곡선
  - `vol` GJR-GARCH-t MLE 직접 구현 · HAR-RV · 언스무딩
  - `regime` Statistical Jump Model + Viterbi · 경제적 국면 명명
  - `simulate` FHS + GPD 꼬리 + 드리프트 사후분포
  - `taxonomy` 3단계 자산 분류 (메타데이터 + 통계 지문)
  - `factors` 팩터 라우터 · Kalman 시변베타 · 델타 패널
  - `equity` 9개 주식 아키타입 · 어닝/PEAD 이벤트 스터디 · 점프 · 런웨이
  - `ml` 트리플배리어 → Purged CV → 모델 경합 → **기권(abstain) 판정**
  - `options` IV 표면 · Breeden-Litzenberger RND · 그릭스
  - `risk` VaR/ES · 스트레스 · **낙폭제약 켈리**
  - `thesis`/`trade` 시나리오 · 반증조건 · 배리어확률 트레이드 · 최소분산 헤지
  - `agents`/`panel` 하드 게이트 · 전문가 14명 · 반대신문 · **결정론적 판정 엔진**
  - `charts` 외부 의존 없는 인라인 SVG 15종
  - `dynamic_report` 종목 성격에 따라 구성이 바뀌는 자기완결 HTML (35섹션 레지스트리)
  - `portfolio` 위험기여 · 팩터 넷팅 · 배분 경합(워크포워드 + Hansen MCS) · HRP
  - `ledger` 예측 저장 → 채점 → 에이전트 가중치 환류 (SQLite)
- `POST /api/jiqtx/analyze` · `GET /api/jiqtx/analyze/<job_id>` —
  백그라운드 실행 + 폴링
- 분석 위젯에 **[◈ 정밀]** 버튼 — 판정·확신도·보고서 링크 표시

### 변경
- jiqtx 의 캐시(`~/.jiqtx_cache`)와 원장(`~/.jiqtx/ledger.db`)도
  앱 폴더 안 `.data/` 로 일원화
- `requirements.txt` 에 `pyarrow` 추가 (가격 캐시 parquet)

### 검증
- 내장 검증 스위트 통과 — EDGE 스프레드 편향 없이 복원(5bp→5.4bp),
  GJR-GARCH 파라미터 복원, Murphy 판별비 411배, ACI 커버리지 오차 ≤0.5%p,
  아키타입 분류 5/5, PEAD 검출력 회수
- 오프라인 데모로 전 파이프라인 확인 — 종목당 26개 섹션 · 210KB
  **완전 자기완결 HTML**(외부 리소스 0)

---

## [2.3.0] — 2026-08-18

`JIQT` 표기를 코드·UI·문서·빌드 메타데이터 전반에서 걷어냈다.

### 변경
- 브랜드 → **I ALWAYS WIN**, 개발자 표기 → **Tenko jun - 정준화**
- 창 제목·페이지 타이틀·콘솔 배너·PyInstaller 스펙 모두 `version.py` 참조
- 식별자 정리: `jiqt.*` → `iaw.*` (localStorage),
  `text/jiqt-*` → `text/iaw-*` (드래그앤드롭),
  `jiqt_session` → `iaw_session` (쿠키),
  `jiqt-auth` → `iaw-auth` (D1), `jiqt-tunnel` → `iaw-tunnel`
- User-Agent `JIQT/1.0` → `IAlwaysWin/2.0`
- README 전면 재작성 (설치·키·`.data/`·구조·한계)

### 추가
- `GET /api/app/info` — 앱 이름·버전·개발자·저장소·파이썬 버전
- 설정 → 정보 패널이 하드코딩(`engine_kr v2.5`) 대신 실제 값을 표시
- 리브랜딩으로 로컬 설정이 날아가지 않도록 `jiqt.*` → `iaw.*`
  localStorage 키 1회 자동 이전 (레이아웃·관심종목·테마·드로잉)

---

## [2.2.0] — 2026-08-18

전략 백테스트 계열 기능 제거 — 이 앱은 **종목 정밀 분석**에 집중한다.

### 제거
- **STRATEGY HUB 전체** (백테스트 · BATCH · MULTI · AUTO · 리서치 ·
  vbt PRO · ML 예측 · 합성 · Paper · Tick · Kronos · 컬렉션)
- **페이퍼 트레이딩** — 주문/PnL/자동매매/리스크 한도 전부
- **리서치** — 전략 상관·민감도·워크포워드·SHAP·팩터 귀인
- **Kronos** 파운데이션 모델 연동 및 설정 패널
- 커뮤니티의 전략 첨부/가져오기
- 서버 라우트 65개, 엔진 패키지 5개
  (`strategy` `trading` `orderflow_pead` `microstructure` `backtest`),
  `report/tearsheet.py`, `static/strategy.html`
- 의존성: vectorbt · numba · llvmlite · shap · transformers ·
  huggingface_hub · einops
- 남은 사용처가 없어진 Plotly CDN 스크립트

### 수정
- **닫힌 알림 서랍의 ✕ 버튼이 허공에 떠 보이던 버그.**
  `.aw-drawer` 를 `right:-460px` 로 숨겼는데 폭은 좁은 화면에서
  `100vw` 로 바뀌어, 화면폭 461~920px 구간에서 (화면폭-460px) 만큼
  서랍이 삐져나왔다. `transform:translateX(100%)` + `visibility`
  방식으로 바꿔 폭과 무관하게 완전히 숨는다.
- **상단 📜 이력 칩이 아무 동작도 하지 않던 문제.**
  `openAnalyzeHistory()` 가 호출만 되고 정의된 적이 없었다(기존 버그).
  최근 분석 50건을 보여 주는 팝업으로 구현.

### 변경
- 작업 큐(`engine/jobs`)는 인프라만 남기고 전략 핸들러 8종 제거 —
  오래 걸리는 정밀 분석을 여기에 다시 붙인다.
- `index.html` 14,799줄 → 8,980줄 · `server.py` 3,745줄 → 2,434줄

---

## [2.1.0] — 2026-08-18

### 변경
- **외부 폴더 의존 제거.** 앱이 쓰던 `~/.jiqt/` 상태(인증 DB, API 키,
  채팅, PC 식별자, cloudflared 바이너리)를 전부 앱 폴더 안
  `.data/` 한 곳으로 모았다. 백업·이전·삭제가 폴더 하나로 끝난다.

### 추가
- `engine/paths.py` — 모든 런타임 경로의 단일 결정 지점.
  앱 폴더가 쓰기 불가면 `%LOCALAPPDATA%/i_always_win` 로 자동 강등.
  기존 `~/.jiqt` 는 첫 실행 시 자동 이전(원본은 보존).

### 수정
- `engine/__init__.py` 가 임포트 즉시 scikit-learn·torch까지 끌어오던 문제 —
  PEP 562 지연 임포트로 전환. 선택적 의존성 하나가 빠져도 패키지가
  통째로 임포트 불가가 되지 않는다.

---

## [2.0.0] — 2026-08-18

`JIQT` → **I ALWAYS WIN** 리브랜딩과 함께 시작하는 새 계보.

### 추가
- `version.py` — 앱 이름·버전·개발자 표기의 단일 소스
- `.gitignore` — API 키·런타임 DB·대용량 자산·빌드 산출물 배제
- `CHANGELOG.md`

### 참고
- 이 저장소에는 **API 키가 포함되지 않습니다.** 키는 앱 실행 후 설정 화면에서 입력합니다.
