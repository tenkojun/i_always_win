# 빌드 현황 (2026-05-20 기준)

## 완료 단계 ✅

| 단계 | 내용 |
|------|------|
| 0 | 기반 엔진: 백테스트, Risk, Orderflow, Factor, ML, Portfolio, Report |
| 0.5 | 기관급 종목 분석: 단/중/장 MC, 팩터분해, 스트레스, 스코어카드, 한글내러티브 |
| 1 | 정밀도 보정: PSR/DSR/CDaR, Purged+Embargo CV, 비대칭 회복 |
| 2 | 웹앱 UI: Flask, eDEX+토스, 상단시세, 차트, 분석패널, 뉴스 |
| 3 | 다중소스 데이터: Yahoo+Stooq+Finnhub+AV+FMP, 교차검증, 키관리 UI |
| 4 | signal_engine: evidence_registry, confidence_engine, conflict_resolver, verdict_mapper |
| 5 | XAI + 정형리포팅: verdict_trace, factor_attribution, risk_chain, HTML 섹션 |
| A1 | 버그수정: 스코어카드 pillars 0점 → .score 추출로 수정 |
| 키UI | ⚙ 설정창: Finnhub/AV/FMP 키 입력, 마스킹, 소스상태 표시 |

## 다음 단계 (미시작)

### 3단계: CSCV/PBO + Tail Risk
- CSCV (Bailey-López de Prado-Zhu 2015)로 PBO 산출
- Block Bootstrap + GARCH + Jump Diffusion + Regime-Switching MC
- 1만회 백테스트 기본값
- GBM 단독 → 비대칭·점프 모델 대체

### 백로그 A우선
- [A2] 타임프레임 슬라이딩 (전체기간→뷰 전환)
- [A3] 차트 자동 스케일 (종목 전환 시 y축 fit)
- [A4] 분석 시 전체 데이터 수집

### 백로그 B
- [B1] 애널리스트 목표주가
- [B2] 뉴스 감성 + 한글화
- [B3] 전문 용어 툴팁
- [B4] 유튜브 방송 플레이어

### 백로그 C (후기)
- [C3] vectorbt 이식 (전략 분석 탭, 10,000회 백테스트)
- [C4] 포트폴리오 시스템 (다종목, 영구저장)
- [C5] 원격 접속 (보안 설계 필요)

## API 키 현황
- Finnhub ✅ / Alpha Vantage ✅ / FMP ✅ (모두 발급 완료)
- 입력 위치: 프로그램 ⚙ 설정창
