# 변경 이력

이 프로젝트는 [시맨틱 버저닝](https://semver.org/lang/ko/)을 따릅니다.
버전 단일 소스는 [`version.py`](version.py) 입니다.

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
