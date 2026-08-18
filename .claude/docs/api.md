# API 엔드포인트 (webapp/server.py)

## REST Endpoints

| Method | Path | 설명 |
|--------|------|------|
| GET | `/` | 메인 UI (index.html) |
| GET | `/api/health` | 헬스체크 |
| GET | `/api/app/info` | 앱 이름·버전·개발자·저장소 (`version.py` 기반) |
| GET | `/api/overview` | 시장 개요 10종목 (20초 캐시) |
| GET | `/api/chart/<ticker>?period=1y` | OHLCV 캔들 데이터 |
| POST | `/api/analyze` | 분석 잡 생성 → job_id 반환 |
| GET | `/api/analyze/<job_id>` | 분석 결과 폴링 |
| GET | `/api/analyze/history` | 분석 이력 (ticker/limit/mine) |
| **POST** | **`/api/jiqtx/analyze`** | **정밀 분석 시작 → job_id** |
| **GET** | **`/api/jiqtx/analyze/<job_id>`** | **정밀 분석 상태 + 리포트 URL** |
| GET | `/api/news` | 뉴스 피드 (60초 캐시) |
| GET | `/api/datasources` | 데이터 소스 상태 (키 마스킹) |
| POST | `/api/datasources/key` | API 키 저장 |
| GET/POST | `/api/portfolio` | 보유 종목 조회/저장 |

### 외부 접근 (감시자 기반)

| Method | Path | 설명 |
|--------|------|------|
| GET | `/api/cloud/status` | 터널 + 감시자 상태 (`supervisor` 블록 포함) |
| POST | `/api/cloud/start_quick` | 외부 접근 켜기 (감시 시작) |
| POST | `/api/cloud/stop` | 끄기 (터널 종료 + 중앙 등록 해제) |
| POST | `/api/cloud/restart` | 강제 재시작 |
| POST | `/api/cloud/publish` | 현재 주소를 중앙 서버에 재등록 |
| GET | `/api/cloud/healthcheck` | 외부 접근 실제 도달 여부 진단 |

### 중앙 인증 (Cloudflare Worker 프록시)

| Method | Path | 설명 |
|--------|------|------|
| GET | `/api/auth/remote/status` | 서버 설정 + 세션 상태 |
| POST | `/api/auth/remote/configure` | 서버 URL 저장 + 헬스체크 |
| POST | `/api/auth/remote/register` · `/login` · `/logout` | 계정 |
| POST | `/api/auth/remote/logout_all` | 모든 기기 로그아웃 |
| POST | `/api/auth/remote/change_password` | 비밀번호 변경 |
| GET | `/api/auth/remote/sessions` | 내 활성 세션 |
| POST | `/api/auth/remote/pc/register` · `/pc/unregister` | 내 PC 주소 |
| GET | `/api/auth/remote/pc/status` | 등록 상태 + `/go/` 주소 |

> **제거됨 (v2.2.0)** — `/api/strategy/*`, `/api/paper/*`, `/api/research/*`,
> `/api/vbt/*`, `/api/kronos/*`, `/api/ml/*`, `/api/of_pead/*`,
> `/api/micro/*`, `/api/tearsheet/*`, `/api/auto/*`, `/strategy` (총 65개)

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
