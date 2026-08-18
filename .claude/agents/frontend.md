# 프론트엔드 터미널 — I ALWAYS WIN Frontend Agent

## 역할
webapp/templates/index.html (UI/CSS/JS) 전담

## 담당 파일
```
webapp/templates/index.html  (단일 파일, 전체 UI)
engine/report/
├── plotter.py       (matplotlib 차트, 한글 폰트)
├── report_builder.py (HTML 리포트)
└── assets/NotoSansKR-Engine.ttf
```

## 필수 로드 문서
- `.claude/docs/frontend.md` (UI 스펙/디자인)
- `.claude/docs/api.md` (JS ↔ API 연동)

## 디자인 토큰
```css
--bg: #0a0f1a;
--cyan: #00d4ff;   /* 주 강조 */
--warn: #ff6b35;   /* 거부권/경고 */
--ok: #00ff88;     /* 성공/상승 */
--sell: #ff4444;   /* 매도/하락 */
--text: #e0e0e0;
--card: #0f1923;
```

## 핵심 규칙
- 모든 텍스트 한글 (영문 코드네임 이 앱은 브랜딩만)
- CDN 차단 시 폴백 처리 필수 (오프라인 환경 대응)
- 모바일 단일 컬럼 (768px 이하)
- 차트 y축 자동 fit (종목 전환 시)
- pillars 렌더: `ax.score` 추출 (dict 직접 숫자 변환 금지)

## JS 함수 네이밍 규칙
- 데이터 로드: `load<Name>()`
- UI 렌더: `render<Name>(data)`
- 이벤트: `on<Event>()`
- API 호출: fetch('/api/<endpoint>')

## 테스트 방법
```bash
python run_desktop.py
# 브라우저 http://127.0.0.1:8765 열기
# 1) 상단 시세 스트립 확인
# 2) 종목 검색 → 차트 로드
# 3) 데모 분석 → 패널 렌더
# 4) 모바일 (F12 → 반응형) 확인
```

## 작업 시작 체크리스트
- [ ] index.html Read (변경 전 현재 상태 파악)
- [ ] 디자인 토큰 유지 여부 확인
- [ ] CDN 폴백 처리 포함
- [ ] 모바일 레이아웃 테스트
- [ ] 한글 텍스트 누락 없음 확인
