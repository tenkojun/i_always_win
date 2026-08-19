
# ── 패키지 내부 의존 ──────────────────────────────────────────
from dataclasses import replace
import argparse
import os
import sys
import time
import warnings

from ._demo import run_demo
from ._validate import run_validation
from .config import RUN
from .dynamic_report import REGISTRY, build_sections, save_html
from .pipeline import analyze
from .portfolio import analyze_portfolio
from .portfolio_report import save_portfolio
from .report import save

# ==============================================================================
# CLI
# ==============================================================================



FULL_DOCUMENTATION = r'''
# Plutus

**범자산 정밀 분석 엔진 · 통합 문서 v0.6**

Yahoo Finance 전 종목을 자산군·종목 성격별로 다른 렌즈로 분석하고,
전문가 패널 심의를 거쳐 동적 보고서를 생성하는 시스템.

> **설계 원칙 하나** — 정밀함은 지표를 더 넣는 것이 아니라
> **틀린 출력을 내지 않는 능력**이다.

```bash
pip install -r requirements.txt
python run_analysis.py GLD NVDA TLT TQQQ --portfolio
```

---

## 목차

| 부 | 내용 |
|---|---|
| **1** | [출발점 — 원본 리포트 진단](#1-출발점--원본-리포트-진단) |
| **2** | [자산군 인지 — "금이 다른 이유"의 일반화](#2-자산군-인지) |
| **3** | [주식 성격 프로파일러](#3-주식-성격-프로파일러) |
| **4** | [통계 엔진](#4-통계-엔진) |
| **5** | [투자 논지 계층](#5-투자-논지-계층) |
| **6** | [전문가 패널](#6-전문가-패널) |
| **7** | [포트폴리오 계층](#7-포트폴리오-계층) |
| **8** | [캘리브레이션 원장](#8-캘리브레이션-원장) |
| **9** | [동적 보고서](#9-동적-보고서) |
| **10** | [검증 결과](#10-검증-결과) |
| **11** | [개발 중 발견·수정한 결함](#11-개발-중-발견수정한-결함) |
| **12** | [모듈 지도 · 사용법](#12-모듈-지도--사용법) |
| **13** | [한계 — 이 시스템이 말할 수 없는 것](#13-한계) |
| **14** | [참고문헌](#14-참고문헌) |

---

## 1. 출발점 — 원본 리포트 진단

업로드된 GLD 리포트는 이미 상위권이었다. DSR·PSR·CDaR·purged/embargo CV·과적합 갭을
**보고**했고, 금에 주식형 팩터를 쓴 게 잘못이라는 것도 스스로 지적했다.

그런데 결정적 결함이 하나 있었다.

> **DSR 84%, 과적합 갭 49%p, OOS 정확도 50%를 알면서도 점수를 냈다.**

OOS 50%는 약한 신호가 아니라 **신호 없음**이다. 올바른 출력은 감점된 52.9점이 아니라
**기권**이다. 감점은 없는 정보를 있는 것처럼 만든다.

### 1.1 몬테카를로 — 가장 큰 결함

원본은 GBM으로 상승확률 87%를 냈다. 드리프트는 최근 수익률에서 추정한 +26.2%.
그런데 드리프트 표준오차는 σ/√T이다.

```
SE(drift) = 0.2835 / √1 = 28.35%p
95% 신뢰구간 = [-29.4%, +81.8%]
```

**추정치의 95% 구간이 0을 훨씬 넘어 음수까지 걸친다.** 이 불확실성을 통합하면:

| 방식 | 1년 상승확률 |
|---|---|
| 드리프트 고정 (원본 방식) | 78.3% |
| 파라미터 불확실성 적분 | 71.0% |
| 장기 앵커로 50% 축소 | 60.4% |
| 70% 축소 | 55.9% |

**"상승확률 87%"는 시장에 대한 진술이 아니라 우리가 드리프트를 안다는 가정에 대한
진술이었다.**

### 1.2 나머지 모듈

| 모듈 | 결함 | 성격 |
|---|---|---|
| 팩터 분해 | 주식형 FF 회귀, R²=2%. 잔차 98%는 "고유위험"이 아니라 **누락 변수** | 모델 오지정 |
| 스트레스 | 주식베타 0.20 × 지수충격. 금에 주식 베타를 곱함 | 시나리오 설계 오류 |
| ML | in-sample 100% / OOS 50%. 확률보정·Brier 없음 | **예측력 부재** |
| 레짐 | K-means, 라벨 0·1·2는 경제적 해석 불가 | 해석 불가 |
| 오더플로우 | 일봉 CVD/VPIN 프록시 — 체결방향 미상 | **측정 무효** |
| 켈리 | half-Kelly 200% — 추정오차 미반영 | 수학적 오용 |
| 점수 통합 | 67.0 → 52.9 → 15.1 세 값 병존 | 결정규칙 부재 |

샤프 0.96을 Harvey-Liu-Zhu 허들(t>3.0)로 입증하려면 **9.8년**이 필요하다.
1년 표본의 샤프 0.96은 t ≈ 0.96으로 0과 구별되지 않는다.

---

## 2. 자산군 인지

### 2.1 핵심 통찰: 팩터 로딩은 상수가 아니라 레짐 함수다

금 가격과 10년 TIPS 금리의 결정계수:

| 기간 | R² |
|---|---|
| 1997–2004 | 69% |
| 2005–2021 | **84%** |
| 2022–2023 | **3%** |
| 2024 이후 | **7%** |

2022년을 기점으로 관계가 소멸했다. 원인은 한계 매수자의 교체다. 3년 연속 중앙은행
순매입이 1,000톤을 넘으면서, 가격 결정 주체가 "금리에 반응하는 투자자"에서
"금리와 무관하게 준비자산을 다변화하는 공적 부문"으로 바뀌었다.

**여기서 나오는 원칙 3가지**

1. **정적 베타 금지.** 모든 로딩은 시변(Kalman/rolling)으로 추정하고 β 안정성 자체를 보고한다.
2. **R² 붕괴는 버그가 아니라 신호다.** 급락하면 "구조 변화" 경보를 울린다.
3. **한계 매수자를 팩터로 모델링한다.** 자산마다 "지금 가격을 정하는 주체"가 다르고, 시간에 따라 바뀐다.

### 2.2 3단계 분류

| 단계 | 방법 |
|---|---|
| **Level 0** | quoteType / sector / category / 이름 키워드 |
| **Level 1** | **통계 지문** — 메타데이터를 신뢰하지 않고 수익률로 검증 |
| **Level 2** | 자산군 배정 + 신뢰도 점수 |

Level 1이 핵심이다. 메타데이터는 자주 틀린다.

| 지문 | 탐지 |
|---|---|
| 주말 거래 관측 | 크립토 → **연율화 √365** (√252면 변동성 17% 과소평가) |
| 프록시 대비 β≈±2·±3 & R²>0.95 | **레버리지 ETP** → 경로의존 처리 |
| 1차 자기상관 > 0.20 | **평활화** → 언스무딩 (샤프 과대 보정) |
| 무거래일 비율 > 25% | 유동성 절벽 |

### 2.3 자산군별 팩터 사전 (`config.py`에서 편집 가능)

| 자산군 | 코어 팩터 | 기대 R² |
|---|---|---|
| 대형 개별주 | FF5 + UMD + VIX + HY OAS | 0.35–0.92 |
| 귀금속 | 10y 실질금리, 광의달러, breakeven, GPR | **0.10–0.55** |
| 에너지 원자재 | WTI, 달러, breakeven (+롤수익 분해 필수) | 0.30–0.90 |
| 국채 ETF | 명목10y, 커브, breakeven | 0.55–0.98 |
| 하이일드 | HY OAS, 주식, 금리 | 0.45–0.95 |
| 리츠 | 주식팩터 + 금리 + HY OAS | 0.40–0.92 |
| 크립토 | BTC팩터, 주식, 달러, VIX | 0.20–0.90 |
| 레버리지 ETP | 기초자산 (경로의존 별도 처리) | 0.75–0.999 |

**R²가 밴드를 벗어나면 팩터 미스매칭 알람이 뜨고 알파 해석이 차단된다.**
원본이 GLD에 주식형 회귀를 돌려 R²=2%를 얻고도 알파 +10.3%를 계산한 지점이 정확히 여기다.

---

## 3. 주식 성격 프로파일러

금이 실질금리·달러로 봐야 하듯, 개별 주식도 종목마다 봐야 할 것이 다르다.
9개 아키타입으로 분류하고 **밸류에이션 앵커 · 경고 항목 · 보고서 섹션**을 결정한다.

`QUALITY_COMPOUNDER` · `HYPERGROWTH_UNPROFITABLE` · `DEEP_VALUE` ·
`DIVIDEND_INCOME` · `CYCLICAL` · `DEFENSIVE` · `HIGH_BETA_SPECULATIVE` ·
`EVENT_DRIVEN` · `DISTRESSED`

**회귀 테스트 5/5 통과** (`validate_archetypes.py`)

### 3.1 펀더멘털 섹션도 성격별로 지표가 다르다

| 아키타입 | 표시 지표 | 표시하지 않는 것 |
|---|---|---|
| 고성장 적자 | EV/Sales, Rule of 40, 순현금/시총, 런웨이 | **PER (무의미)** |
| 딥밸류 | P/B vs ROE, EV/EBIT, **가치 함정 점검** | 성장 프리미엄 지표 |
| 배당 인컴 | 배당 커버리지(FCF/배당), payout, 금리 델타 | 성장 멀티플 |
| 부실 | 순부채, 유동비율, 희석 압력 | 이익 기반 밸류에이션 |

### 3.2 주식 전용 분석

| 기능 | 내용 |
|---|---|
| **어닝 이벤트 스터디** | 발표 다음날 초과수익 분포(중앙값·90분위·최대), 서프라이즈 부호별 **PEAD** t+1~20 누적, t값 |
| **어닝 집중도** | 연 분산 중 발표 4일 비중. 15% 초과면 "평상시 변동성으로 리스크를 재면 심각한 과소평가" 경보 |
| **점프 프로파일** | \|r\| > 4σₜ 탐지. 점프 기여 25% 초과면 "샤프·정규 VaR·GBM 전부 부적합" 판정 |
| **스타일 로딩** | FF 로딩 + 고유위험 비중 + **잔차 모멘텀**(Blitz-Huij-Martens) vs 원시 모멘텀 괴리 |
| **런웨이** | 순현금 / 연간 현금소진 → 잔여 연수 + 연간 희석 압력 |
| **크라우딩** | 공매도잔고·기관보유·고유위험·청산소요일 종합 점수 |

**펀더멘털은 point-in-time이 아니다.** 리스테이트먼트가 반영된 값이라 시계열 백테스트에
쓰면 안 되고, 현재 상태 진단으로만 쓴다. 리포트에 이 경고가 항상 함께 출력된다.

---

## 4. 통계 엔진

### 4.1 교체 대상

| 폐기 | 채택 | 근거 |
|---|---|---|
| 일봉 VPIN/CVD | **EDGE 스프레드** | Ardia·Guidotti·Kroencke, JFE 2024 |
| GBM 시뮬레이션 | **FHS-GJR-GARCH-t + GPD 꼬리 + 드리프트 사후분포** | McNeil-Frey 2000 |
| K-means 레짐 | **Statistical Jump Model** | Nystrup 2020, Shu-Yu-Mulvey 2024 |
| 고정 horizon 부호 | **Triple-barrier + 메타라벨링** | López de Prado 2018 |
| 단일 WF-CV | **CPCV → PBO 직접 산출** | Bailey et al. |
| 보정 없음 | **Brier + Murphy 분해 + conformal(ACI)** | Murphy 1973, Gibbs-Candès 2021 |
| half-Kelly | **낙폭 제약 켈리** | — |
| 주식베타 스트레스 | **자산군 고유 리스크팩터 충격** | — |

### 4.2 하드 게이트 — 실패는 감점이 아니라 무효화

| 게이트 | 조건 | 실패 시 |
|---|---|---|
| G1 데이터 무결성 | 결측 <2%, 조정 검증 통과 | **전체 중단** |
| G2 유동성 | EDGE < 임계, ADV > 임계 | **거래불가 판정** |
| G3 팩터 적합 | R²가 자산군 밴드 내 | **알파 해석 차단** |
| G4 ML 과적합 | 갭 <15%p, DSR ≥90%, PBO <75% | **확률 출력 금지** |
| G5 보정 | Brier skill > 0, Resolution > 0 | **확률 출력 금지** |
| G7 스트레스 | 최악 손실 < 한도 | **사이즈 0** |

### 4.3 PBO는 소프트 게이트다

**PBO는 전략 *선택 절차*의 과적합을 재는 지표이지 신호의 존재를 재지 않는다.**
변형들이 사실상 동일하면 순위가 무작위가 되어 PBO가 기계적으로 0.5 근방에 나온다.

- PBO ≥ 0.50 → 신뢰구간 확대 (소프트)
- PBO ≥ 0.75 → 차단 (하드)
- 하드 게이트는 **전략 DSR**로 건다

### 4.4 사이징 — 켈리 200%가 나오는 이유

문제는 켈리 공식이 아니라 **μ를 안다고 가정한 것**이다.

| 방식 | SE(μ)=2% | SE(μ)=10% | SE(μ)=25% |
|---|---|---|---|
| 단순 μ/σ² | 247% | 247% | 247% |
| 성장 최적 (불확실성·팻테일 반영) | 300% (95%MDD 63%) | 300% (66%) | 300% (76%) |
| **낙폭 제약** | **90% (24%)** | **85% (25%)** | **60% (23%)** |

성장 최적 켈리는 수학적으로 옳아도 운용 불가능한 낙폭을 동반한다.
**낙폭 제약이 없으면 어떤 켈리 값도 실무 권고로 쓸 수 없다.**

최종 비중은 6중 캡의 최솟값이고, 어떤 제약이 구속했는지 항상 명시한다.

---

## 5. 투자 논지 계층

지표를 보여주는 것과 투자 논지를 세우는 것은 다르다.

### 5.1 시나리오 — 분위수가 아니라 드라이버로 정의

| | 기존 | Plutus |
|---|---|---|
| 정의 | 분포의 10/50/90 분위 | **상위 드라이버 ±1σ/±2σ 조합** |
| 손익 | 분위 그 자체 | **팩터 델타 × 충격** |
| 확률 | 없거나 직관 | **역사적 동시 발생 빈도** + 모델 초과확률 |

- 드라이버는 상관 |r|>0.9면 **중복 제거** (같은 충격을 두 번 세는 것 방지)
- 모델 확률은 버킷 질량이 아니라 **초과확률** P(수익 ≥ 목표)
- **알파를 그대로 넣지 않는다.** t<2면 0, 유의해도 60% 축소
- **드라이버만으로 손실이 안 나면 경보** — "하방 위험의 출처가 선택된 팩터가 아니다"

### 5.2 트레이드 구성

"비중 15%"는 결론이 아니다.

| 항목 | 산출 방식 |
|---|---|
| 손절 | **2σ × 지평 변동성 + 왕복비용** (임의 % 아님) |
| 목표 | 강세 시나리오에서 도출 |
| P(목표/손절 선도달) | **시뮬 경로 배리어 통과** (4,000경로 보관) |
| 손익분기 승률 | 손절폭 / (손절폭 + 목표폭) |
| 엣지 | P(목표) − 손익분기 승률 |
| 비용 후 기대손익 | 배리어 확률 가중 − EDGE 스프레드 − 제곱근 임팩트 |
| 리스크 예산 검증 | 사이즈 × 손절폭 ≤ 계좌 2% |

### 5.3 헤지 설계

**최소분산 헤지비율 = 다변량 팩터 회귀 계수**다. 따라서 팩터 모델이 미스매칭이면
헤지도 함께 무효이며, 시스템이 이 연결을 끊지 않는다.

레그마다 헤지 수단(SPY/ES, TIP/TIPS 선물, UUP/DX, HYG/CDX HY 등)과 **β 안정성 CV**를
표시하고, CV>0.8이면 "헤지비율이 불안정해 오히려 위험을 추가할 수 있음"으로 처리한다.
제거 가능 분산이 25% 미만이면 **"헤지보다 사이즈 축소"**로 결론을 바꾼다.

### 5.4 반증 조건 (kill criteria)

분석 시점에 **자동 생성**된다. 사후에 만든 반증 조건은 의미가 없다.

| 반증 조건 | 임계 | 발동 시 조치 |
|---|---|---|
| 팩터 모델 붕괴 | R² < 밴드 하단의 55% | 델타·헤지·스트레스 전부 무효화 |
| 헤지 비율 불안정 | β 변동계수 > 0.80 | 해당 팩터 헤지 불가 |
| 구조 변화 | 롤링 R² < 과거 중앙값의 35% | 재추정 전 신규 진입 금지 |
| 드리프트 무의미 | SE(μ̂) ≥ \|μ̂\| | 기대수익 기반 사이징 금지 |
| 방향 엣지 소멸 | Murphy Resolution ≈ 0 | 확률 출력 사용 금지 |
| 거래비용 초과 | EDGE > 자산군 한도 | 사이즈 0 또는 지정가 분할 |
| 스트레스 한도 초과 | 최악 손실 > 35% | 헤지 전 진입 불가 |
| 이벤트 리스크 | 어닝까지 ≤ 14영업일 | 사이즈 축소 또는 옵션 대체 |

모니터링 플랜은 항목마다 **출처(FRED 시리즈 ID까지)·주기·임계**를 지정한다.

---

## 6. 전문가 패널

### 6.1 먼저, 경고

LLM 멀티에이전트 토론은 자동으로 정확도를 올리지 않는다.

- **동조**: 약한 모델은 토론 중 자기 편향을 극소량만 교정
- **다수의 폭정**: 정답인 소수 의견이 사회적 압력에 눌린다
- **마팅게일 정체**: 동일 입력을 받으면 라운드가 지나도 기대 정확도가 개선되지 않는다

역할만 나눈 에이전트 15개는 비싼 앵무새 15마리가 될 수 있다.

### 6.2 이를 막는 설계 5원칙

| # | 원칙 |
|---|---|
| 1 | **정보 비대칭** — 각자 다른 데이터 슬라이스. 다양성이 프롬프트가 아니라 실제 정보에서 나와야 한다 |
| 2 | **선언된 편향** — 각 전문가가 자기 편향을 먼저 밝힌다. 독자가 할인해 읽으라고 |
| 3 | **판정은 LLM이 아니다** — 최종 판정은 결정론적 규칙 엔진 |
| 4 | **거부권은 통계 전문가에만** — 데이터·체결·리스크·감사만 |
| 5 | **소견문은 결정 규칙에서 도출** — 같은 입력이면 같은 소견. 감사 가능 |

### 6.3 전문가 14명

| 전문가 | 렌즈 | 열람 범위 | 거부권 |
|---|---|---|---|
| 데이터 무결성 책임자 | 모든 결론은 입력 품질을 넘을 수 없다 | 원시 OHLCV만 | ⛔ |
| 크로스에셋 분류 전략가 | 이것이 무엇인지 정하지 않으면 어떤 지표도 무의미 | 메타 + 통계지문 (**가격 수준 미열람**) | |
| 팩터 이코노미스트 | 설명 안 되는 수익은 알파가 아니라 못 찾은 팩터 | 팩터 패널만 (**자산 가격 미열람**) | |
| 매크로 전략가 | 가격은 실질금리·달러·유동성의 함수 | 매크로 + 발표 캘린더 | |
| 체결 총괄 | 거래할 수 없으면 옳아도 소용없다 | 미시구조 지표만 | ⛔ |
| 파생 스트럭처러 | 옵션은 시장의 확률분포를 직접 보여준다 | 옵션 체인만 | |
| 계량 리서처 | 예측력은 표본외에서만 인정 | 피처 + 라벨 | |
| 레짐 사관 | 같은 자산도 국면이 바뀌면 다른 자산 | 국면 피처만 (**날짜 블라인드**) | |
| 확률모형 총괄 | 분포를 모르면 확률을 말할 수 없다 | 수익률 + GARCH 잔차 | |
| 리스크 책임자 | 질문은 "틀렸을 때 얼마를 잃나" | 꼬리 + 스트레스 | ⛔ |
| 헤지 설계자 | 무엇을 상쇄하고 무엇이 남는가 | 팩터 계수 + β 안정성 | |
| 포트폴리오 매니저 | 좋은 분석과 좋은 트레이드는 다르다 | 판정 + 시나리오 + 제약 | |
| 검증 감사관 | 발견은 검증 전까지 가설이다 | 검증 통계만 | ⛔ |
| 적대적 검토관 | 이 결론을 무효화할 세계는 무엇인가 | 타 전문가 결론 (**최종 단계만**) | |

### 6.4 반대신문과 증거 위계

각 전문가는 다른 전문가의 **구체적 주장**에 반론을 건다. 승패는 증거 위계로 결정된다.

```
1. 데이터 무결성    — 틀린 데이터 위에서는 아무 주장도 성립하지 않는다
2. 체결 가능성      — 거래할 수 없으면 옳아도 소용없다
3. 표본외 통계 검정  — DSR / PBO / Murphy / 커버리지
4. 표본내 통계      — R², t값, 샤프
5. 경제적 메커니즘   — 왜 그래야 하는지의 논리
6. 서사·정성        — 이야기
```

위계 1–3의 반론은 **인용**, 4는 **부분 인용(신뢰구간 확대)**, 5–6은 **미해결 쟁점으로
병기**된다. 미해결 쟁점을 평균으로 뭉개지 않고 남기는 것이 핵심이다.

실행 예시 (GOLDX):

```
[리스크 책임자 → 포트폴리오 매니저]
  대상 주장: R:R 0.45 구조
  반론: 손절폭이 목표폭보다 큽니다. 승률 83% 가정이 69% 아래로만
        떨어져도 기대값이 음수가 됩니다.
  판정: 부분 인용 — '표본내 통계'(위계 4). 신뢰구간을 확대합니다.

[헤지 설계자 → 리스크 책임자]
  대상 주장: 헤지를 통한 위험 통제
  반론: 제거 가능 분산이 19%에 불과합니다. 헤지로 한도를 맞추려는
        시도는 비용만 쓰고 실패합니다 — 사이즈를 줄이는 것이 유일한 수단.
  판정: 부분 인용 — 신뢰구간 확대.
```

---

## 7. 포트폴리오 계층

해지펀드는 종목 하나를 따로 보지 않는다.

### 7.1 비중이 아니라 위험기여

```
한계기여위험  MCR_i = (Σw)_i / σ_p
위험기여      CR_i  = w_i × MCR_i     (합 = σ_p)
분산비율      DR    = Σ(w_i σ_i) / σ_p
유효 베팅 수  ENB   = exp(위험기여 엔트로피)
```

데모: 7종목을 같은 비중으로 넣었는데 **유효 베팅 수는 3.85**, QUALCO 하나가 책 위험의
**37%**를 차지했다. 종목 수는 분산이 아니다. 공분산은 **Ledoit-Wolf 축소**를 쓴다.

### 7.2 팩터 넷팅

- 넷팅비율 낮음 → "개별 헤지는 이중 집행. **책 레벨에서 한 번만**"
- 넷팅비율 ≈ 1 → **"이것은 분산이 아니라 동일 베팅의 레버리지다"**

데모 북이 후자였다. 7종목 전부 롱이고 같은 팩터에 같은 방향이라 상쇄가 0이었다.

### 7.3 배분 경합 — 워크포워드 + MCS

각 리밸런싱 시점에서 **과거 데이터만으로** 가중치를 만들고 다음 구간 실현 수익으로
평가하며 **회전비용도 차감**한다.

| 규칙 | 샤프 | 최대낙폭 |
|---|---|---|
| HRP | **1.50** | −8.0% |
| 최소분산 | 1.35 | −7.4% |
| 리스크 패리티 | 1.23 | −9.6% |
| 역변동성 | 1.10 | −10.2% |
| 1/N | 0.85 | −13.4% |

HRP 샤프가 1/N의 **1.76배**다. 그런데 —

> **Hansen MCS(α=0.10)가 1/N을 제외하지 못했다 (5/5 생존).**
> 따라서 그 차이는 통계적으로 구별되지 않으며, 시스템은 **1/N을 채택**한다.

표본 내 우위를 근거로 복잡한 규칙을 쓰면 추정오차를 더 많이 먹는다.

---

## 8. 캘리브레이션 원장

**이것이 없으면 이 엔진은 정적 계산기다.** 게이트·DSR·PBO는 전부 '지금 이 표본 안'의
진단이다. 판정 엔진의 최종 출력이 맞는지는 예측을 남기고 채점해야만 안다.

```python
from engine.jiqtx import analyze, Ledger
led = Ledger()
w = led.agent_weights()                  # 실적 기반 가중치
a  = analyze("NVDA", calib_weights=w)    # 판정 엔진에 환류
led.record(a, horizon_days=21)
led.score(price_lookup)
```

SQLite(외부 의존 없음)에 예측을 **설정 해시**와 함께 저장한다. 설정이 바뀌면 해시가
바뀌고 **시행횟수 N에 카운트**되어 DSR이 실제 N을 쓴다.

`replay.py`는 과거 시점으로 데이터를 잘라내고(point-in-time) 분석기를 그대로 돌려
원장을 부트스트랩한다.

### 8.1 리플레이 98건 결과

| 등급 | n | 평균 실현수익 | Brier skill | 확신 주장 | 적중 |
|---|---|---|---|---|---|
| ACCUMULATE | 10 | +2.40% | **+0.210** | 10건 | **80%** |
| HOLD | 52 | +0.59% | −0.033 | 0건 | — |
| NO_TRADE | 36 | +2.00% | −0.028 | 0건 | — |

**98건 중 확신 있는 방향 주장은 10건뿐이고 그중 8건이 맞았다.** 나머지 88건은
"방향 주장 없음"이다. 이것이 의도된 동작이다.

### 8.2 원장이 말한 불편한 사실

첫 리플레이에서 **세 에이전트 모두 Brier skill이 음수**였다
(Simulation −0.022, Regime −0.033, Macro −0.048). 가중치 함수가 셋 다 하한 0.15로 눌렀다.

> 합성 데이터라 절대값은 의미가 없다. 하지만 **"우리 확률은 아직 검증되지 않았다"를
> 시스템이 스스로 말할 수 있다**는 사실이 원장의 존재 이유다.

---

## 9. 동적 보고서

고정 템플릿이 아니라 **섹션 레지스트리**다. 35개 섹션 각각이
`applies(analysis) → bool`을 가지며 해당하는 것만 조립된다.

### 9.1 6부 정보구조

| 부 | 내용 |
|---|---|
| **I** 판정 | 요약 · 최종 판정 · 종목 성격 · 하드 게이트 |
| **II** 투자 논지 | 시나리오 · 트레이드 · 헤지 · 반증 조건 · 수익 귀인 |
| **III** 전문가 심의 | 전문가 패널 · 에이전트 · 레드팀 |
| **IV** 종목 진단 | 성격에 맞춘 개별 분석 (자산별로 구성이 다름) |
| **V** 리스크 | VaR/ES · 스트레스 · 사이징 |
| **VI** 운영·한계 | 촉매·모니터링 · 한계 |

### 9.2 자산별 실제 분기

| 종목 | 성격 | 활성 | 이 종목에만 나오는 섹션 |
|---|---|---|---|
| 금 ETF | 귀금속 | 24 | — (주식 섹션 전부 생략) |
| 레버리지 ETP | — | 25 | `drag` 변동성 드래그 분해 |
| 대형 테크 | 우량 복리성장주 | 27 | `fundamentals` `style` `peer` |
| 유틸리티 | 배당 인컴주 | 27 | `fundamentals` `peer` `rate` |
| 적자 SaaS | 고성장 적자기업 | 29 | `runway` `crowding` `rate` |
| 임상 바이오텍 | 이벤트 드리븐 | 29 | `jump` `runway` `crowding` |

금 ETF에는 어닝·펀더멘털 섹션이 **아예 렌더되지 않는다.**
하단에 "이 자산에 해당하지 않아 생략된 섹션" 목록을 표시해 무엇이 빠졌는지 감추지 않는다.

### 9.3 UI

- **자기완결 단일 HTML** — matplotlib/plotly 없이 인라인 SVG. 오프라인에서 열림
- 스티키 상단바 (티커 · 판정 배지 · 전체 접기/펼치기)
- 핵심 지표 스트립 (판정 · 확률 · 리스크예산 · 기대손익 · 잔차vol · 반증조건)
- 부 단위 그룹 · 섹션 번호 · 그룹 목차
- 차트: 팬차트 · 델타바 · 워터폴 · 시나리오 · 트레이드 사다리 · 헤지 도넛 ·
  국면 타임라인 · 신뢰도 다이어그램 · 상관 히트맵 · 게이지
- **인쇄 CSS** 포함

---

## 10. 검증 결과

전부 재현 가능하다: `python validate_estimators.py`, `validate_archetypes.py`, `validate_pead.py`

### 10.1 EDGE 스프레드 추정량

정식 bid-ask bounce 미시구조 시뮬레이션(효율가격 랜덤워크 + 매수/매도 도착)으로 검증.

| 진짜 | **EDGE** | Corwin-Schultz | Abdi-Ranaldo | Roll |
|---|---|---|---|---|
| 5bp | **5.4bp** | 72.0bp | 25.3bp | 64.9bp |
| 10bp | **9.3bp** | 73.8bp | 25.5bp | 65.0bp |
| 20bp | **20.7bp** | 79.0bp | 30.2bp | 66.2bp |
| 50bp | **51.6bp** | 97.0bp | 56.3bp | 85.3bp |
| 100bp | **102.2bp** | 130.8bp | 103.0bp | 122.7bp |

EDGE만 편향 없이 복원한다.

### 10.2 GJR-GARCH(1,1)-t 파라미터 복원

| 진짜 | 추정 |
|---|---|
| α=0.050 γ=0.060 β=0.880 ν=6 | α=0.067 γ=0.087 β=0.844 ν=5.9 |
| α=0.030 γ=0.100 β=0.860 ν=8 | α=0.050 γ=0.129 β=0.819 ν=8.2 |

### 10.3 Murphy Resolution — 신호/노이즈 판별

| | Resolution | Brier Skill |
|---|---|---|
| 정보성 확률 | 0.08001 | +0.321 |
| 무정보 확률 | 0.00019 | −0.318 |

**판별비 411배.** `resolution ≈ 0`이 "예측력 없음"의 정량적 증명임을 확인.

### 10.4 ACI Conformal 커버리지 (목표 90%)

| 분포 | 실측 |
|---|---|
| 정규 | 89.9% |
| t(3) 팻테일 | 89.9% |
| 변동성 레짐전환 | 89.5% |

### 10.5 PEAD 검출력

| 참 스프레드 | 추정 | 편의 | 평균 \|t\| | 검출률 |
|---|---|---|---|---|
| +16.0% | +16.57% | +0.57%p | 7.77 | 100% |
| +8.0% | +8.58% | +0.58%p | 3.93 | 83% |
| +4.0% | +4.58% | +0.58%p | 2.35 | 67% |
| **0% (귀무)** | **−0.10%** | −0.10%p | 0.75 | **8%** |

편의가 표준오차(3.58%p) 대비 무시할 수준이고, 귀무 상태 거짓양성률 8%는
\|t\|>1.8 양측 명목 수준(≈7%)과 일치한다.

### 10.6 엔드투엔드 (7종목 합성)

| 티커 | 선택 팩터 | R² | P(up) GBM→FHS | 켈리 | 판정 |
|---|---|---|---|---|---|
| GOLDX | 실질금리·달러·breakeven | 18.5% | 95%→**76%** | 631%→125% | ACCUMULATE 15% |
| LEV3X | mkt | 99.7% | 61%→**38%** | 83%→40% | NO_TRADE 0% |
| QUALCO | mkt·rmw | 60.2% | 89%→70% | 457%→105% | HOLD 15% |
| BIOJMP | mkt·smb·vix | 12.0% | 57%→61% | 97%→65% | HOLD 6% |

- LEV3X의 61%→38%가 **변동성 드래그**다. 일간 리밸런싱 경로를 재구성해야만 나온다
- 합성 데이터에 심어둔 **실질금리 베타 붕괴**(−0.075 → −0.010)를 시스템이
  `real_yield_10y` 레그의 **CV 0.86**으로 탐지해 헤지 불가 판정했다

---

## 11. 개발 중 발견·수정한 결함

정직성 기록. 전부 개발 중 스스로 잡아 고친 것들이다.

| # | 결함 | 어떻게 드러났나 | 수정 |
|---|---|---|---|
| 1 | **켈리에 사후분포 "적분"이 축소를 못 만듦** | 로그효용이 μ에 선형이라 Var(μ)가 답을 안 바꿈. SE 2%→25%에서 f*가 그대로 | 예측분포에서 일간 리밸런싱 경로 시뮬 + **낙폭 제약** |
| 2 | **PBO를 하드 게이트로 씀** | 진짜 신호가 있는 데이터에서도 PBO 60%로 차단됨 | PBO는 선택 절차 지표 → **소프트 게이트**, 하드는 전략 DSR |
| 3 | **`gauge()` 포맷 문자열을 width 위치에 전달** | 7개 리포트 전부에서 ML 섹션이 조용히 렌더 실패 | 키워드 인자로 수정 |
| 4 | **펀더멘털 없는 종목을 가격만으로 단정** | MEGACAP이 "경기방어주"로 분류됨 | 펀더멘털 부족 시 임계 상향 → "미분류" |
| 5 | **`AVOID`가 약세 판단과 거래불가를 혼동** | **원장 리플레이에서 AVOID 36건의 평균 실현수익이 +2.0%** | `NO_TRADE` 등급 분리 |
| 6 | **CI 정합률 지표가 항상 1.0** | 모든 자산군에서 1.0 → 정보 없음 | 0.5를 배제한 확신 주장만 채점, 나머지는 제외 |
| 7 | **시나리오 모델확률을 버킷 질량으로 계산** | "극단 확률 32% > 강세 확률 15%" | **초과확률** P(수익 ≥ 목표)로 변경 |
| 8 | **시나리오에 알파를 그대로 반영** | 약세 시나리오가 +3.15%로 나옴 | t<2면 0, 유의해도 60% 축소 |
| 9 | **상관 1.0인 팩터를 드라이버·헤지 레그에 중복 사용** | 같은 충격을 두 번 셈 | 상관 임계로 중복 제거 |

**5번과 6번은 코드 리뷰로는 보이지 않았고, 채점을 해야만 보였다.** 원장의 존재 이유다.

---

## 12. 모듈 지도 · 사용법

### 12.1 실행

```bash
pip install -r requirements.txt        # numpy pandas scipy scikit-learn yfinance pyarrow

python run_analysis.py GLD                       # 단일 종목 → .md + .html
python run_analysis.py NVDA TLT BTC-USD --portfolio --fast
python run_analysis.py TQQQ --md-only --no-ml

python demo_offline.py                 # 네트워크 없이 7종목 + 책 리포트
python demo_replay.py                  # 워크포워드 리플레이 → 원장 부트스트랩
python validate_estimators.py          # 추정량 정확도
python validate_archetypes.py          # 아키타입 분류기 5/5
python validate_pead.py                # PEAD 검출력
```

### 12.2 모듈 (25개, 약 12,000줄)

| 파일 | 역할 |
|---|---|
| `config.py` | 자산군 명세, 팩터 prior, 게이트 임계치 |
| `data.py` | yfinance + FRED + GPR 수집, 무결성 검증 |
| `taxonomy.py` | 3단계 자산 분류, 통계 지문 |
| `micro.py` | EDGE / CS / CHL / Roll, Amihud, 제곱근 임팩트, capacity |
| `statcore.py` | PSR/DSR/MTRL, Purged CV, CPCV, PBO, Murphy, ACI, Kupiec/Christoffersen, SPA, MCS, 분위회귀, 꼬리의존성 |
| `vol.py` | EWMA, **GJR-GARCH-t MLE(직접 구현)**, HAR-RV, 언스무딩 |
| `regime.py` | **Statistical Jump Model**, 경제적 명명, 레짐별 베타 |
| `factors.py` | 팩터 라우터, ElasticNet 선택, **Kalman 시변베타**, 델타 패널, 롤수익 분해 |
| `equity.py` | **9개 주식 아키타입**, 어닝 이벤트 스터디, PEAD, 점프, 스타일, 런웨이 |
| `ml.py` | 트리플배리어, 모델 경합, **기권 판정** |
| `simulate.py` | 드리프트 사후분포, FHS, GPD 꼬리, 레버리지 경로 재구성 |
| `risk.py` | VaR/ES 3종 + 커버리지검정, 드로다운, 스트레스, **낙폭제약 켈리** |
| `options.py` | IV 기간구조, 25Δ RR/BF, VRP, **Breeden-Litzenberger RND**, 그릭스 |
| `thesis.py` | **드라이버 기반 시나리오**, 반증 조건, 모니터링, 촉매 |
| `trade.py` | **배리어 확률 트레이드 구성**, 최소분산 헤지, 수익 귀인 |
| `panel.py` | **전문가 14명 · 소견문 · 반대신문 · 증거 위계** |
| `agents.py` | 하드게이트, **결정론적 판정 엔진** |
| `portfolio.py` | 위험기여 분해, 팩터 넷팅, **배분 경합(워크포워드+MCS)**, 책 스트레스 |
| `ledger.py` | **캘리브레이션 원장 (SQLite)** — 저장·채점·가중치 환류 |
| `replay.py` | **워크포워드 리플레이** |
| `charts.py` | **의존성 없는 인라인 SVG 차트 10종** |
| `dynamic_report.py` | **동적 섹션 레지스트리 35개 + 자기완결 HTML** |
| `portfolio_report.py` | 책 레벨 HTML |
| `pipeline.py` | 엔드투엔드 오케스트레이션 |
| `report.py` | 마크다운 렌더러 |

외부 의존은 `numpy` `pandas` `scipy` `scikit-learn`뿐이다.
GARCH·점프모델·분위회귀·EVT·HRP는 전부 직접 구현했다.

---

## 13. 한계

이걸 명시하지 않으면 나머지가 무의미하다.

| 한계 | 내용 |
|---|---|
| **생존편향** | Yahoo Finance에 상장폐지 종목이 없다. **종목선택 전략은 원리적으로 검증 불가** |
| **인트라데이** | 일봉만 사용 → 진짜 실현변동성·오더플로우 계산 불가. HAR은 r² 프록시 |
| **펀더멘털 PIT** | 리스테이트먼트가 반영된 값이라 point-in-time이 아님 |
| **옵션 히스토리** | 스냅샷만 제공 → 백테스트 불가. **오늘부터 매일 축적해야 함** |
| **체결 가정** | 신호 생성 종가가 아니라 다음 거래일 시가/VWAP를 가정해야 함. 이 선택이 수익성을 뒤집을 수 있음 |
| **개발자 look-ahead** | 리플레이는 "과거에 이 코드를 돌렸다면"의 근사. 코드가 전체 기간을 보고 개발됨 → 순수 OOS 아님 |
| **합성 데이터 검증** | 모든 엔드투엔드 결과는 합성 데이터 기준. 실데이터 검증 미완료 |
| **EDGE 구현** | 공개 pseudocode 구조를 따랐고 시뮬 검증했으나, 운영 전 참조 구현(R `bidask`)과 대조 권장 |
| **적중률** | **크게 오르지 않는다.** 개선은 거짓 신호 제거·리스크 추정 정확도·사이징 규율에서 나온다 |

### "우월하다"고 말할 수 있는 조건

다음을 **전부** 만족할 때만:

- [ ] 사전등록된 OOS 기간 + **모델 학습 컷오프 이후**
- [ ] 비용·슬리피지·체결지연 반영
- [ ] MCS에서 벤치마크가 제외됨 (α = 0.10)
- [ ] PBO < 30%, DSR 유의
- [ ] 최소 2개 레짐 × 3개 자산군에서 재현
- [ ] 12개월 이상 실시간 페이퍼 트레이딩 기록

하나라도 미충족이면 결론은 **"우월성 미확인"**이다.
이 결론을 낼 수 있다는 것 자체가 시스템의 가치다.

> 이 엔진은 "더 잘 맞히는 시스템"이 아니라
> **"틀렸을 때 덜 잃고, 모를 때 모른다고 말하는 시스템"**이다.

### 다음 단계

1. **실데이터 실행** — 제 검증은 전부 합성이다. 실제 티커에서 시나리오 드라이버가
   무엇으로 뽑히는지, 헤지 레그가 거래 가능한 조합인지, 배분 경합에서 여전히 1/N이
   살아남는지 확인 필요
2. **옵션 스냅샷 축적** — 오늘 시작하면 1년 뒤 VRP/RND 백테스트 가능
3. **사전등록** — 분석 전 가설·팩터·임계치를 해시로 동결. 이후 변경은 N에 카운트
4. **원장 누적** — 실데이터 리플레이로 에이전트 skill이 여전히 음수인지 확인
5. **팩터 원본 교체** — 프록시 ETF 대신 Ken French 라이브러리

---

## 14. 참고문헌

**백테스트 과적합·다중검정**
- Bailey & López de Prado (2014). The Deflated Sharpe Ratio. *JPM* 40(5), 94–107.
- Bailey, Borwein, López de Prado & Zhu (2014). Pseudo-Mathematics and Financial Charlatanism. *Notices of the AMS* 61(5).
- Harvey, Liu & Zhu (2016). …and the Cross-Section of Expected Returns. *RFS*.
- Hansen (2005). A Test for Superior Predictive Ability. *JBES*.
- Romano & Wolf (2005). Stepwise Multiple Testing as Formalized Data Snooping. *Econometrica* 73.
- Hansen, Lunde & Nason (2011). The Model Confidence Set. *Econometrica*.
- López de Prado (2018). *Advances in Financial Machine Learning*. Wiley.

**미시구조·거래비용**
- Ardia, Guidotti & Kroencke (2024). Efficient Estimation of Bid-Ask Spreads from OHLC. *JFE* 161, 103916.
- Abdi & Ranaldo (2017). *RFS* 30, 4437–4480.
- Corwin & Schultz (2012). *Journal of Finance*.
- Amihud (2002). *Journal of Financial Markets*.
- Andersen & Bondarenko (2015). Assessing Measures of Order Flow Toxicity. *Review of Finance* 19. (VPIN 비판)
- Almgren, Thum, Hauptmann & Li (2005). Direct Estimation of Equity Market Impact. *Risk*.

**변동성·리스크·EVT**
- Corsi (2009). A Simple Approximate Long-Memory Model of Realized Volatility. *JFEC*.
- McNeil & Frey (2000). *Journal of Empirical Finance* 7, 271–300.
- Barone-Adesi, Giannopoulos & Vosper (1999). *Journal of Futures Markets*. (FHS)
- Politis & Romano (1994). The Stationary Bootstrap. *JASA*.
- Christoffersen (1998). *International Economic Review*.
- Acerbi & Székely (2014). Back-Testing Expected Shortfall. *Risk*.

**레짐**
- Bemporad et al. (2018). Fitting Jump Models. *Automatica*.
- Nystrup, Lindström & Madsen (2020). *ESWA* 150, 113307.
- Shu, Yu & Mulvey (2024). Downside Risk Reduction Using Regime-Switching Signals. *JAM*.

**불확실성 정량화**
- Gibbs & Candès (2021). Adaptive Conformal Inference Under Distribution Shift. *NeurIPS*.
- Xu & Xie (2021). Conformal Prediction Interval for Dynamic Time-Series. *ICML*.
- Murphy (1973). A New Vector Partition of the Probability Score. *J. Applied Meteorology*.

**포트폴리오**
- López de Prado (2016). Building Diversified Portfolios that Outperform OOS. *JPM*. (HRP)
- Ledoit & Wolf (2004). *J. Multivariate Analysis*.
- DeMiguel, Garlappi & Uppal (2009). Optimal Versus Naive Diversification. *RFS*. (1/N)

**자산군**
- Baur & Lucey (2010). Is Gold a Hedge or a Safe Haven? *Financial Review* 45(2), 217–229.
- Erb & Harvey (2013). The Golden Dilemma. *FAJ*.
- Gorton & Rouwenhorst (2006). *FAJ* 62, 47–68.
- Koijen, Moskowitz, Pedersen & Vrugt (2018). Carry. *JFE* 127, 197–225.
- Caldara & Iacoviello (2022). Measuring Geopolitical Risk. *AER* 112(4), 1194–1225.

**옵션**
- Breeden & Litzenberger (1978). *Journal of Business* 51(4), 621–651.
- Bakshi, Kapadia & Madan (2003). *RFS* 16(1), 101–143.

**LLM 멀티에이전트 (프레임워크 및 비판)**
- Xiao, Sun, Luo & Wang (2024). TradingAgents. arXiv:2412.20138.
- Yu et al. (2024). FinCon. *NeurIPS 37*.
- 멀티에이전트 토론의 동조·다수의 폭정·마팅게일 정체, 금융 LLM 평가의 5대 실패
  (look-ahead·survivorship·과적합·비용무시·레짐맹목) 문헌 — §6.1의 근거.

---

*본 문서와 소프트웨어는 방법론 연구·검증 목적입니다. 투자 자문이 아닙니다.*

'''



def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="python -m engine.jiqtx.cli",
        description="Plutus 범자산 정밀 분석 엔진",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="예)  python -m engine.jiqtx.cli GLD NVDA TLT --portfolio --fast\n"
               "     python -m engine.jiqtx.cli --demo\n"
               "     python -m engine.jiqtx.cli --validate\n"
               "     python -m engine.jiqtx.cli --doc > SPEC.md")
    ap.add_argument("tickers", nargs="*", help="Yahoo Finance 티커")
    ap.add_argument("--aum", type=float, default=1e7, help="운용자산(USD)")
    ap.add_argument("--years", type=int, default=8, help="조회 기간(년)")
    ap.add_argument("--sims", type=int, default=20000, help="시뮬레이션 횟수")
    ap.add_argument("--out", default="./reports", help="리포트 저장 폴더")
    ap.add_argument("--portfolio", action="store_true",
                    help="티커 2개 이상일 때 책 레벨 리포트도 생성")
    ap.add_argument("--fast", action="store_true", help="고속 모드")
    ap.add_argument("--md-only", action="store_true", help="HTML 생략")
    ap.add_argument("--no-options", action="store_true")
    ap.add_argument("--no-ml", action="store_true")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--demo", action="store_true",
                    help="네트워크 없이 합성 7종목 데모 실행")
    ap.add_argument("--validate", action="store_true",
                    help="추정량 검증 스위트 실행")
    ap.add_argument("--doc", action="store_true", help="통합 문서 출력")
    a = ap.parse_args(argv)

    warnings.filterwarnings("ignore")
    # `python -m engine.jiqtx.cli --doc | head` 처럼 파이프가 먼저 닫히는 경우 대비
    try:
        import signal
        signal.signal(signal.SIGPIPE, signal.SIG_DFL)
    except (ImportError, AttributeError, ValueError):
        pass

    if a.doc:
        try:
            sys.stdout.write(FULL_DOCUMENTATION + "\n")
        except BrokenPipeError:
            pass
        return 0
    if a.validate:
        run_validation()
        return 0
    if a.demo:
        run_demo(outdir=a.out if a.out != "./reports" else "./reports_demo")
        return 0
    if not a.tickers:
        ap.print_help()
        return 1

    cfg = replace(RUN, lookback_years=a.years, n_sims=a.sims)
    os.makedirs(a.out, exist_ok=True)
    done = []

    for tk in a.tickers:
        print(f"\n{'='*66}\n▶ {tk}\n{'='*66}")
        t0 = time.time()
        try:
            res = analyze(tk, cfg=cfg, aum_usd=a.aum,
                          with_options=not a.no_options,
                          with_ml=not a.no_ml, fast=a.fast,
                          verbose=not a.quiet)
            done.append(res)
        except Exception as exc:
            print(f"  ✗ 실패: {exc}")
            continue
        base = os.path.join(a.out, f"JIQTX_{tk.replace('/','_')}_{res.asof}")
        save(res, base + ".md")
        secs = build_sections(res)
        if not a.md_only:
            save_html(res, base + ".html")
        v, eq = res.verdict, res.equity
        print(f"\n  자산군 {res.classification.spec.label_ko}"
              + (f" · 성격 {eq.archetype_ko} ({eq.archetype_confidence:.0%})"
                 if eq else ""))
        print(f"  판정 {v.grade} | 확률 "
              f"{v.direction_prob if v.direction_prob else float('nan'):.1%} | "
              f"사이즈 {v.risk_budget_weight:.1%} | 신뢰도 {v.model_confidence}")
        if res.panel:
            print(f"  전문가 패널 {len(res.panel.experts)}명 · 반대신문 "
                  f"{len(res.panel.challenges)}건 · 거부권 "
                  f"{len(res.panel.blocks)}건")
        print(f"  동적 섹션 {len(secs)}/{len(REGISTRY)}개 활성")
        print(f"  리포트 → {base}.md"
              + ("" if a.md_only else f" / {base}.html")
              + f"  ({time.time()-t0:.0f}s)")

    if a.portfolio and len(done) >= 2:
        print(f"\n{'='*66}\n▶ 포트폴리오 ({len(done)}개 포지션)\n{'='*66}")
        try:
            P = analyze_portfolio(done)
            pp = os.path.join(a.out, "JIQTX_PORTFOLIO.html")
            save_portfolio(P, pp, title=" · ".join(x.ticker for x in done))
            r = P.risk
            print(f"  책 변동성 {r.vol_ann:.1%} | 유효베팅 "
                  f"{r.effective_bets:.2f} | 최대 위험집중 "
                  f"{r.max_pct_contribution:.0%}")
            print(f"  배분 경합 채택: {P.allocation.winner} "
                  f"(1/N 초과 입증: {P.allocation.beats_1n})")
            fails = P.limits[~P.limits["충족"]]
            if len(fails):
                print(f"  ⚠ 한도 위반 {len(fails)}건: "
                      f"{', '.join(fails['한도'])}")
            print(f"  리포트 → {pp}")
        except Exception as exc:
            print(f"  ✗ 포트폴리오 분석 실패: {exc}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
