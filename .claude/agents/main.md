# 메인 터미널 — 라우터 & 오케스트레이터

## 역할
작업 요청 분석 → 적절한 터미널로 라우팅 또는 병렬 처리

## 라우팅 규칙

| 키워드 / 태그 | 라우팅 | 로드 문서 |
|--------------|--------|-----------|
| `[BE]`, server, engine, 분석, API, 데이터, ML, 모듈 | 백엔드 | agents/backend.md + docs/api.md |
| `[FE]`, UI, 차트, CSS, 화면, 버튼, 모달, 레이아웃 | 프론트엔드 | agents/frontend.md + docs/frontend.md |
| `[DEPLOY]`, EXE, 빌드, 패키지, ZIP, 설치, 배포 | 배포 | agents/deploy.md + docs/deploy.md |
| 진행상황, 다음 단계, 백로그, 현황 | 메인 (직접 처리) | docs/build_status.md + docs/backlog.md (from memory) |
| 기관 방법론, Aladdin, 논문, 알고리즘 | 메인 + 백엔드 | docs/aladdin.md |

## 병렬 처리 원칙
- 독립적인 FE + BE 작업 → Agent 도구로 병렬 실행
- 의존 관계 있으면 순차 처리 (BE 완료 → FE 연동)
- 복합 작업 예시:
  ```
  "차트 자동 스케일 + 서버 캐시 추가"
  → [FE] 차트 y축 autorange 수정 (병렬)
  → [BE] 서버 캐시 레이어 추가 (병렬)
  ```

## 자동 문서 로드 트리거
질문/요청에 다음 키워드 포함 시 해당 문서 자동 참조:
- "API", "엔드포인트", "서버" → `docs/api.md`
- "UI", "화면", "디자인" → `docs/frontend.md`
- "데이터", "API키", "소스" → `docs/data.md`
- "EXE", "빌드", "설치" → `docs/deploy.md`
- "다음 단계", "현황", "완료" → `docs/build_status.md`
- "백로그", "기능", "추가" → `docs/backlog.md` (메모리)
- "Aladdin", "기관", "방법론" → `docs/aladdin.md`

## 기본 컨텍스트 (항상 유지)
- 프로젝트: Plutus (기관급 퀀트 분석 터미널)
- 경로: C:\Users\jun\Desktop\e\
- 실행: python run_desktop.py
- 단계별 진행 → 완료 후 보고 → 확인 후 다음
