# BlackRock Aladdin 벤치마크 & 기관 방법론

## Aladdin 9솔루션 → JIQT 대응

| Aladdin | JIQT 모듈 | 상태 |
|---------|-----------|------|
| Accounting | backtest/ P&L | ✅ |
| Aladdin Studio | JIQT_BLUEPRINT_v4.md | ✅ |
| Aladdin Data Cloud | engine/data/sources/ | ✅ |
| Whole Portfolio | portfolio/ + C4 백로그 | 부분 |
| Risk | institutional/ (PSR/DSR/CDaR) | ✅ |
| Sustainability | ESG 스코어링 | ❌ 미구현 |
| Climate | 기후 시나리오 | ❌ 미구현 |
| Aladdin Copilot | explain/ (XAI) | ✅ JIQT 차별점 |
| Private Markets | FMP 확장 가능 | 부분 |

## JIQT 차별점 (Aladdin에 없는 것)
1. **명시적 거부권(veto)**: RSI극단/DSR낮음/시나리오취약 시 BUY 자동 차단
2. **verdict_trace 인과 사슬**: 모든 판정에 "왜?" 추적 필수
3. **PSR/DSR/CSCV 통합**: 과적합 확률 구조적 격리
4. **무료 데이터 한계 투명 표기**: 신뢰도 강점

## 구현된 기관 방법론

| 방법론 | 출처 | 구현 위치 |
|--------|------|-----------|
| Purged+Embargo CV | López de Prado 2018 | ml/models.py |
| HRP | López de Prado 2016 | portfolio/hrp.py |
| PSR / DSR | Bailey & LdP 2012 | institutional/precision.py |
| CSCV / PBO | Bailey et al. 2015 | 3단계 예정 |
| Fama-French 3F/5F | Fama & French 1993/2015 | factor/fama_french.py |
| Black-Litterman | Black & Litterman 1992 | portfolio/black_litterman.py |
| CDaR | Chekhlov-Uryasev 2005 | institutional/precision.py |
| Barra 팩터 분해 | MSCI Barra | institutional/factor_risk.py |
| BAB | Frazzini & Pedersen 2014 | factor/exposure.py |
| Ledoit-Wolf 축소 | Ledoit & Wolf 2004 | portfolio/markowitz.py |

## signal_engine 메타 의사결정 구조
```
Evidence 등록 (evidence_registry.py)
    ↓
신뢰도 재보정 (confidence_engine.py)
    ↓
충돌 해소: 거부권→가중합의→국면게이트 (conflict_resolver.py)
    ↓
판정 출력 + 점수 상한 (verdict_mapper.py)
```

## 정직한 한계
- Aladdin 본질 우위: 수십 년치 독점 데이터 (채권/사모/펀더멘털)
- JIQT: 방법론·구조·투명성 동급, 데이터 깊이는 무료 소스 한계 표기
