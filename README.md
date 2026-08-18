# 📊 종목 단/중/장 + 기관급 분석 엔진 (한글판 v2.0)

종목 티커 하나를 넣으면 **단기 / 중기 / 장기** 세 시간축으로 분석하고,
블랙록(BlackRock) Aladdin 류 **기관급 위험·수익 분석**을 더해
**한글 리포트(HTML/JSON)** 로 출력하는 Python 엔진입니다.

> ⚠️ 본 엔진의 모든 출력은 \*\*정보 제공 목적\*\*이며 투자 권유가 아닙니다.

\---

## 🚀 30초 사용법 (Google Colab)

```python
!pip install -r requirements.txt

from main import analyze

# 미국 종목
res = analyze('AAPL', start='2018-01-01')

# 한국 종목 (코스피=.KS / 코스닥=.KQ)
res = analyze('005930.KS')      # 삼성전자

# 인터넷 없이 데모
res = analyze('DEMO', use\_synthetic=True)

# HTML 리포트를 셀에 바로 표시
from IPython.display import HTML
HTML(open(res\['report\_paths']\['html'], encoding='utf-8').read())
```

\---

## 🏛 무엇이 나오나요?

### 1\) 단/중/장 타임프레임 분석

|기간|룩백|이동평균|ML 예측 지평|
|-|-|-|-|
|단기|60일 (≈3개월)|5/20|5일|
|중기|252일 (≈1년)|20/60|20일|
|장기|1260일 (≈5년)|50/200|60일|

각 기간별로 추세·모멘텀·변동성·리스크·오더플로우·머신러닝·시장국면을
0\~100 점수화 → **BUY / HOLD / SELL** 시그널.

### 2\) 기관급 분석 (BlackRock Aladdin식)

* **미래 주가 몬테카를로** — 단/중/장 기간별로 수천 개 미래 경로를
시뮬레이션하여 분위수 밴드(5\~95%)와 종착 가격 분포, 상승 확률,
+10%/-10% 도달 확률, VaR/CVaR 산출
* **팩터 위험 분해** — 위험을 *시장 등 공통 팩터* vs *종목 고유 요인*
으로 분해 (체계적/고유 위험 비중, 시장 베타, 알파)
* **시나리오 스트레스 테스트** — 2008 금융위기·2020 코로나·금리 인상 등
위기 가정 시 예상 손실·가격·회복 기간
* **몬테카를로 부(富) 예측** — 원금 N원 투자 시 목표 수익률
(+10%/+20%/2배) 달성 확률, 물가·예금 초과 확률
* **자산배분·리스크 버짓** — 목표 변동성 기준 권장 종목 배분 비중,
하프 켈리

### 3\) 기관 스코어카드 (포트폴리오 카드)

7개 평가 축(수익성·추세·안정성·위험효율·팩터건전성·시나리오내성·상승확률)
을 등급화하여 **A+ \~ D 종합 기관 등급**과 한 줄 의견 제시.

### 4\) 모듈별 한글 분석 글

각 모듈 결과를 비전문가도 읽을 수 있는 **기관 리서치 코멘트** 형식의
한글 문장으로 자동 작성. 예:

> "\[중기] 약 1년 후를 GBM 방식으로 3000회 시뮬레이션한 결과, 현재가
> 대비 상승할 확률은 약 58%입니다. 예상 중앙값은 12,300원, 90%
> 신뢰구간은 8,900\~17,400원으로 표준편차가 ±24%에 달해 변동성이
> 큰 편입니다. ..."

\---

## 📁 디렉토리 구조

```
engine\_kr/
├── main.py                     # analyze() 진입점
├── requirements.txt
├── colab\_quickstart.ipynb      # Colab 5단계 퀵스타트
└── engine/
    ├── data/loader.py          # yfinance + 합성 데이터
    ├── analysis/timeframe.py   # 단/중/장 분석기
    ├── institutional/          # ★ 기관급 분석 (신규)
    │   ├── mc\_projection.py    #   미래 주가 몬테카를로
    │   ├── factor\_risk.py      #   팩터 위험 분해
    │   ├── stress\_test.py      #   시나리오 스트레스 테스트
    │   ├── wealth\_projection.py#   부(富) 예측
    │   ├── risk\_budget.py      #   자산배분·리스크 버짓
    │   ├── market\_proxy.py     #   시장 프록시/팩터 패널
    │   ├── scorecard.py        #   기관 스코어카드
    │   └── narrative.py        #   한글 분석 글 생성기
    ├── risk/                   # 샤프·MDD·VaR·몬테카를로·시계열
    ├── orderflow/              # CVD·VPIN·미시구조
    ├── factor/                 # Fama-French·팩터 익스포져
    ├── ml/                     # 특징·모델(RF/XGB/LSTM/GRU/TF)·국면
    ├── volatility/             # GARCH/EGARCH
    ├── portfolio/              # 마코위츠·BL·HRP
    └── report/                 # 플로터·리포트 빌더(HTML/JSON)
```

\---

## ⚙️ 주요 파라미터

```python
analyze(
    ticker,                       # 'AAPL', '005930.KS', 'BTC-USD' ...
    start='2018-01-01',
    end=None,
    ml\_model='rf',                # rf | xgb | lstm | gru | transformer
    regime\_method='kmeans',       # kmeans | hmm | gmm
    use\_synthetic=False,          # True = 인터넷 없이 데모
    initial\_capital=10\_000\_000,   # 부 예측 원금 (기본 1,000만원)
    target\_vol=0.10,              # 리스크 버짓 목표 변동성
    out\_dir='./report\_out',
)
```

\---

## 📦 반환 값

```python
res\['timeframes']                 # {단기, 중기, 장기} 상세
res\['overall\_score'], res\['overall\_signal']
res\['institutional']\['scorecard'] # 기관 스코어카드
res\['institutional']\['narratives']# 모듈별 한글 분석 글
res\['institutional']\['mc\_tf']     # 단/중/장 몬테카를로 결과
res\['report\_paths']\['html']       # HTML 리포트 경로
```

\---

## 🔧 확장 포인트

* **타임프레임 변경**: `engine/analysis/timeframe.py` 의 `DEFAULT\_TIMEFRAMES`
* **시나리오 추가**: `engine/institutional/stress\_test.py` 의 `DEFAULT\_SCENARIOS`
* **점수 가중치**: `timeframe.py` 의 `\_compute\_score`,
`scorecard.py` 의 `pillars` weight
* **실제 FF 팩터 사용**: `factor/fama\_french.py` 의 `load\_ff\_factors(csv)`
로 Kenneth French 데이터 주입

\---

## ⚠️ 한계 / 주의

* 단기(60일)는 표본이 작아 머신러닝·국면 분석이 "데이터 부족"으로
생략될 수 있습니다(정상 동작).
* 단일 종목 도구 특성상 시장지수가 없으면 팩터 분해는 **근사 프록시**
로 추정합니다(리포트에 명시됨). 정밀 분석은 실제 지수/FF 데이터 주입 권장.
* 몬테카를로·스트레스 결과는 과거 통계 기반 **확률적 추정**이며 미래를
보장하지 않습니다.





인용한 학계 표준(López de Prado, Bailey, Barra/MSCI, Chekhlov-Uryasev) 기반.

알라딘 블랙록 모티브

