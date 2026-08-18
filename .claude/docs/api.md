# API 엔드포인트 (webapp/server.py)

## REST Endpoints

| Method | Path | 설명 |
|--------|------|------|
| GET | `/` | 메인 UI (index.html) |
| GET | `/api/overview` | 시장 개요 10종목 (20초 캐시) |
| GET | `/api/chart/<ticker>?period=1y` | OHLCV 캔들 데이터 |
| POST | `/api/analyze` | 분석 잡 생성 → job_id 반환 |
| GET | `/api/analyze/<job_id>` | 분석 결과 폴링 |
| GET | `/api/news` | 뉴스 피드 (60초 캐시) |
| GET | `/api/datasources` | 데이터 소스 상태 (키 마스킹) |
| POST | `/api/keys` | API 키 저장 |
| GET | `/api/portfolio` | 포트폴리오 목록 |
| POST | `/api/portfolio` | 포트폴리오 저장 |

## analyze() 반환 구조
```python
{
  "overall_signal": "SELL|BUY|HOLD|...",
  "overall_score": float,  # 0~100
  "timeframes": {
    "short": {"signal": str, "score": float, "days": 60},
    "medium": {"signal": str, "score": float, "days": 252},
    "long": {"signal": str, "score": float, "days": 1260}
  },
  "institutional": {
    "scorecard": {
      "overall_grade": "A+~D",
      "overall_score": float,
      "verdict": str,
      "pillars": {name: {"score": float, "grade": str, "comment": str}}
    },
    "narratives": {module_name: str},  # 12개 한글 분석글
    "precision": {"PSR": float, "DSR": float, "MinTRL": int, "asymmetric_recovery": dict},
    "stress": {"scenarios": {name: {"loss_pct": float, "recovery_days": int}}}
  },
  "meta_decision": {
    "verdict": str, "score": float, "grade": str,
    "vetoes": [str], "conflicts": [str], "trace": str
  },
  "explanation": {
    "headline": str, "drivers": [str], "detractors": [str],
    "vetoes": [str], "factor_attribution": dict, "risk_chain": [str]
  },
  "report_paths": {"html": str, "json": str}
}
```

## 서버 설정
- 포트: 8765
- 비동기 분석: ThreadPoolExecutor (job dict로 상태 관리)
- 캐시: 딕셔너리 기반 (production은 Redis 권장)
- CORS: 모바일 접속 허용

## 데이터 계약
- OHLCV: 컬럼 소문자 (open/high/low/close/volume)
- 인덱스: DatetimeIndex UTC
- 소스 우선순위: FMP > AV > Finnhub > Yahoo > Stooq
