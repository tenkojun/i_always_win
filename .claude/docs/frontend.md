# 프론트엔드 (webapp/templates/index.html)

## UI 디자인 원칙
- **비주얼**: eDEX-UI (시안 글로우, 코너 브래킷, 스캔라인, 모노 폰트)
- **UX**: 토스 (깔끔한 카드, 색상 체계, 직관적 검색)
- **색상**: 배경 #0a0f1a, 강조 #00d4ff (시안), 경고 #ff6b35, 성공 #00ff88
- **폰트**: 'Share Tech Mono' (영문), 'NotoSansKR' (한글, 번들 TTF)

## 레이아웃 구조
```
┌─────────────────────────────────────────────┐
│  JIQT 상단바  [시장 시세 스트립]  [⚙설정]    │
├──────────────────┬──────────────────────────┤
│                  │  [종목 분석 패널]          │
│  [종목 차트]      │  - 스코어카드             │
│  [검색 / 기간]   │  - VERDICT TRACE          │
│                  │  - 드라이버/디트랙터       │
│                  ├──────────────────────────┤
│                  │  [뉴스 피드]  [TV 플레이어]│
└──────────────────┴──────────────────────────┘
```

## 핵심 JS 함수
- `loadOverview()` — 시장 시세 스트립 (20초 갱신)
- `loadChart(ticker, period)` — 차트 렌더 (Plotly/Lightweight Charts)
- `startAnalysis(ticker)` — 분석 실행 → 폴링
- `renderAnalysis(data)` — 분석 결과 렌더링
- `openSettings()` — API 키 설정 모달
- `saveKeys()` — 키 저장 → /api/keys POST

## 차트 스펙
- 라이브러리: CDN (Plotly 또는 Lightweight Charts)
- 캔들스틱 + 거래량 서브플롯
- 기간 버튼: 5D / 1M / 6M / 1Y / 5Y / MAX
- 종목 전환 시 y축 자동 fit (autorange: true)
- 한국주 (₩) / 미국주 ($) 통화 자동 표기

## 분석 패널 렌더링
```javascript
// pillars 렌더 (A1 버그 수정 완료 — .score 추출)
Object.entries(pillars).forEach(([k, ax]) => {
  const score = ax.score || 0;  // dict에서 .score 추출
  const grade = ax.grade || '';
  const comment = ax.comment || '';
  // 막대 + 등급 색상 + 코멘트 표시
});
```

## 모바일 대응
- 미디어 쿼리: 768px 이하 → 단일 컬럼
- 터치 제스처: 차트 핀치 줌
- 폰 접속: http://<PC-IP>:8765 (같은 와이파이)

## 한글 폰트 설정
```python
# engine/report/plotter.py 자동 실행
# 1순위: assets/NotoSansKR-Engine.ttf (번들, 항상 성공)
# matplotlib axes.unicode_minus = False
```

## 설정 모달 (⚙)
- Finnhub / Alpha Vantage / FMP 키 입력 (password 타입)
- 가용 소스 상태: ● (켜짐) / ○ (꺼짐)
- 저장 시 마스킹 표시 (••••1234)
