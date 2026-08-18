# 백엔드 터미널 — I ALWAYS WIN Backend Agent

## 역할
engine/ + webapp/server.py + main.py 전담

## 담당 파일
```
main.py
webapp/server.py
engine/
├── data/         (데이터 레이어)
├── backtest/     (백테스팅)
├── risk/         (리스크 메트릭)
├── ml/           (머신러닝)
├── institutional/ (기관급 분석)
├── signal_engine/ (메타 의사결정)
├── explain/      (XAI)
└── report/       (리포트 생성)
```

## 필수 로드 문서
- `.claude/docs/api.md` (엔드포인트/계약)
- `.claude/docs/data.md` (데이터 소스)
- `.claude/docs/aladdin.md` (기관 방법론)

## 핵심 규칙
- OHLCV 소문자 + DatetimeIndex UTC 계약 절대 유지
- 새 분석 모듈은 반드시 signal_engine evidence로 등록
- 모든 키 처리는 keyconfig.py 통해서만
- 무키 폴백 필수 (키 없이도 Yahoo+Stooq로 동작)
- 분석 완료 후 스모크 테스트 실행

## 스모크 테스트 명령
```python
from main import analyze
res = analyze('DEMO', use_synthetic=True, ml_model='rf')
assert res['meta_decision']['verdict'] in ['SELL','BUY','HOLD','MIXED']
print('BE smoke ✅', res['meta_decision'])
```

## 작업 시작 체크리스트
- [ ] `.claude/docs/build_status.md` 확인 (완료 단계 파악)
- [ ] 수정 대상 파일 Read 후 Edit (Write 남용 금지)
- [ ] 계약 파괴 여부 확인 (OHLCV/DatetimeIndex)
- [ ] 테스트 후 결과 보고
