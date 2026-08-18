# 데이터 소스 전략 (engine/data/)

## 소스 우선순위
```
FMP → Alpha Vantage → Finnhub → Yahoo → Stooq
```

## 키 관리
- 파일: `~/.jiqt/keys.json` (chmod 0600)
- 모듈: `engine/data/keyconfig.py`
- 환경변수 우선: FINNHUB_KEY / ALPHAVANTAGE_KEY / FMP_KEY
- **키를 코드/로그/채팅에 절대 기록 금지**
- 마스킹 표시: `••••1234` (끝 4자리만)

## 소스별 특성
| 소스 | 키 | 한도 | 강점 |
|------|-----|------|------|
| Yahoo | X | - | 1962~ 시세, 기본 펀더멘털 |
| Stooq | X | 무제한 | 30년 시세 백필 (CSV) |
| Finnhub | O | 분당 60 | 미국 실시간 + 뉴스 |
| Alpha Vantage | O | 분당 5·일 25 | 50+ 기술지표 |
| FMP | O | 일 250 | 재무제표·SEC·밸류에이션 |

## OHLCV 표준 계약
```python
# engine/data/sources/base.py normalize()
df.columns = ['open','high','low','close','volume']  # 소문자
df.index = pd.DatetimeIndex(df.index, utc=True)      # UTC
```

## 교차 검증 (reconcile.py)
- 2+ 소스 최근 종가 비교
- `< 1%` 편차: high confidence
- `1~5%`: medium (경고)
- `> 5%`: 경보 + 출처 명시

## 데이터 한계 (리포트에 명시)
- 무료 시세: ~15분 지연 (화면 "LIVE 15M" 표기)
- 한국 종목: Yahoo (005930.KS 형식) 주력
- 실시간 틱: 무료 소스 없음
- 역사 펀더멘털: 소급 구매 불가

## 로컬 캐시 (분석 이력)
- 위치: `data/snapshots/<ticker>/<date>.json`
- PSR/DSR/팩터/국면 스냅샷 누적
- 자체 트랙레코드: 예측 시그널 vs 실제 결과 저장
