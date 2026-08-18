# ==============================================================================
# [01/25] config.py — 자산군 정의 · 팩터 prior · 게이트 임계치
# ==============================================================================

"""
jiqtx.config — 자산군 정의, 팩터 사전(prior), 게이트 임계치.

설계 원칙
---------
1. 팩터는 하드코딩이 아니라 '후보군 제한(prior)'이다. 실제 선택은 통계가 한다.
2. 각 자산군에는 '기대 R² 밴드'가 있다. 밴드를 벗어나면 모델 미스매칭 알람.
   (GLD에 주식형 FF 회귀를 돌려 R²=2%가 나온 것이 정확히 이 케이스)
3. 스트레스 시나리오는 '주식 베타 × 지수충격'이 아니라 자산군 고유 리스크팩터 충격이다.
"""
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

# ---------------------------------------------------------------- 매크로 시리즈
# FRED 시리즈 ID -> 내부 팩터명. 무료·무인증 CSV 엔드포인트로 수집 가능.
FRED_SERIES: Dict[str, str] = {
    "DFII10":      "real_yield_10y",     # 10년 TIPS 실질금리
    "DFII5":       "real_yield_5y",
    "T10YIE":      "breakeven_10y",      # 10년 기대인플레이션
    "DTWEXBGS":    "broad_dollar",       # 광의 달러지수
    "DGS10":       "nominal_10y",
    "DGS2":        "nominal_2y",
    "T10Y2Y":      "curve_2s10s",
    "BAMLH0A0HYM2": "hy_oas",            # 하이일드 OAS
    "BAMLC0A0CM":  "ig_oas",
    "VIXCLS":      "vix",
    "DCOILWTICO":  "wti",
    "DEXUSEU":     "eurusd",
}

# 커브 팩터를 만들 때 쓰는 원시 금리 시리즈
CURVE_SERIES = ["DGS2", "DGS10"]

# ---------------------------------------------------------------- 자산군 명세


@dataclass(frozen=True)
class AssetClassSpec:
    """자산군 1개의 분석 사양."""
    code: str
    label_ko: str
    ann_factor: int                       # 연율화 기준일수 (크립토=365)
    factor_prior: List[str]               # 후보 팩터 (내부 팩터명)
    r2_band: Tuple[float, float]          # 기대 설명력 밴드
    stress: Dict[str, float]              # 리스크팩터 -> 표준충격(변수 단위)
    max_weight: float                     # 단일자산 상한
    min_history_days: int
    max_spread_bps: float                 # 유동성 게이트
    notes: str = ""
    path_dependent: bool = False          # 레버리지/변동성 ETP 여부
    needs_roll_model: bool = False        # 선물 롤 수익 분해 필요


# CMA 제외 — 쓸 만한 롱숏 프록시가 없다 (위 FACTOR_LEGS 주석 참조)
_EQ_CORE = ["mkt_excess", "smb", "hml", "rmw", "umd"]

ASSET_CLASSES: Dict[str, AssetClassSpec] = {

    "EQUITY_LARGE": AssetClassSpec(
        code="EQUITY_LARGE", label_ko="대형 개별주", ann_factor=252,
        factor_prior=_EQ_CORE + ["vix", "hy_oas"],
        r2_band=(0.35, 0.92),
        stress={"mkt_excess": -0.20, "vix": 20.0, "hy_oas": 3.0, "nominal_10y": 1.0},
        max_weight=0.15, min_history_days=504, max_spread_bps=40,
        notes="기대 R²가 밴드 하단 미만이면 이벤트/특수상황 플래그.",
    ),

    "EQUITY_SMALL": AssetClassSpec(
        code="EQUITY_SMALL", label_ko="중소형 개별주", ann_factor=252,
        factor_prior=_EQ_CORE + ["vix"],
        r2_band=(0.15, 0.75),
        stress={"mkt_excess": -0.30, "vix": 25.0, "hy_oas": 4.0},
        max_weight=0.06, min_history_days=504, max_spread_bps=120,
        notes="평활화 수익률 언스무딩 필수. 상폐 편향 경고 대상.",
    ),

    "ETF_EQUITY": AssetClassSpec(
        code="ETF_EQUITY", label_ko="주식형 ETF", ann_factor=252,
        factor_prior=_EQ_CORE + ["vix"],
        r2_band=(0.70, 0.99),
        stress={"mkt_excess": -0.20, "vix": 20.0},
        max_weight=0.35, min_history_days=252, max_spread_bps=20,
    ),

    "ETF_SECTOR": AssetClassSpec(
        code="ETF_SECTOR", label_ko="섹터 ETF", ann_factor=252,
        factor_prior=_EQ_CORE + ["vix", "wti", "nominal_10y"],
        r2_band=(0.60, 0.98),
        stress={"mkt_excess": -0.20, "vix": 20.0, "nominal_10y": 1.0},
        max_weight=0.20, min_history_days=252, max_spread_bps=25,
    ),

    "PRECIOUS_METAL": AssetClassSpec(
        code="PRECIOUS_METAL", label_ko="귀금속", ann_factor=252,
        factor_prior=["real_yield_10y", "broad_dollar", "breakeven_10y",
                      "vix", "gpr", "mkt_excess"],
        r2_band=(0.10, 0.55),
        stress={"real_yield_10y": 1.0, "broad_dollar": 0.05,
                "breakeven_10y": -0.50, "gpr": -1.0},
        max_weight=0.15, min_history_days=504, max_spread_bps=15,
        notes=("실질금리 β는 반드시 시변. 금-TIPS R²는 2005-2021 약 84%에서 "
               "2022년 이후 한 자릿수로 붕괴한 전례가 있음(한계 매수자 교체). "
               "은은 산업수요 비중이 커 PMI/산업생산 로딩을 추가해야 함."),
    ),

    "COMMODITY_ENERGY": AssetClassSpec(
        code="COMMODITY_ENERGY", label_ko="에너지 원자재", ann_factor=252,
        factor_prior=["wti", "broad_dollar", "breakeven_10y", "mkt_excess", "vix"],
        r2_band=(0.30, 0.90),
        stress={"wti": -0.35, "broad_dollar": 0.05, "mkt_excess": -0.20},
        max_weight=0.08, min_history_days=504, max_spread_bps=30,
        needs_roll_model=True,
        notes="ETF≠현물. 콘탱고 롤 손실을 분해하지 않으면 기초자산 프록시로 쓸 수 없음.",
    ),

    "COMMODITY_BROAD": AssetClassSpec(
        code="COMMODITY_BROAD", label_ko="광의 원자재", ann_factor=252,
        factor_prior=["wti", "broad_dollar", "breakeven_10y", "mkt_excess"],
        r2_band=(0.25, 0.85),
        stress={"wti": -0.30, "broad_dollar": 0.05},
        max_weight=0.10, min_history_days=504, max_spread_bps=35,
        needs_roll_model=True,
    ),

    "BOND_GOV": AssetClassSpec(
        code="BOND_GOV", label_ko="국채 ETF", ann_factor=252,
        factor_prior=["nominal_10y", "curve_2s10s", "breakeven_10y", "mkt_excess"],
        r2_band=(0.55, 0.98),
        stress={"nominal_10y": 2.0, "curve_2s10s": -1.0},
        max_weight=0.40, min_history_days=252, max_spread_bps=15,
        notes="DV01·KRD를 델타 패널에 반드시 포함.",
    ),

    "BOND_CREDIT": AssetClassSpec(
        code="BOND_CREDIT", label_ko="투자등급 크레딧", ann_factor=252,
        factor_prior=["nominal_10y", "ig_oas", "mkt_excess", "curve_2s10s"],
        r2_band=(0.45, 0.95),
        stress={"nominal_10y": 2.0, "ig_oas": 1.5, "mkt_excess": -0.20},
        max_weight=0.30, min_history_days=252, max_spread_bps=20,
    ),

    "BOND_HY": AssetClassSpec(
        code="BOND_HY", label_ko="하이일드", ann_factor=252,
        factor_prior=["hy_oas", "mkt_excess", "nominal_10y", "vix"],
        r2_band=(0.45, 0.95),
        stress={"hy_oas": 3.0, "mkt_excess": -0.25, "vix": 20.0},
        max_weight=0.15, min_history_days=252, max_spread_bps=25,
        notes="주식 베타 0.3~0.5 수준. 채권으로 취급하면 리스크를 과소평가함.",
    ),

    "BOND_TIPS": AssetClassSpec(
        code="BOND_TIPS", label_ko="물가연동채", ann_factor=252,
        factor_prior=["real_yield_10y", "breakeven_10y", "nominal_10y"],
        r2_band=(0.55, 0.98),
        stress={"real_yield_10y": 1.5, "breakeven_10y": -0.75},
        max_weight=0.30, min_history_days=252, max_spread_bps=20,
    ),

    "REIT": AssetClassSpec(
        code="REIT", label_ko="리츠", ann_factor=252,
        factor_prior=_EQ_CORE + ["nominal_10y", "real_yield_10y", "hy_oas"],
        r2_band=(0.40, 0.92),
        stress={"mkt_excess": -0.25, "nominal_10y": 1.5, "hy_oas": 2.0},
        max_weight=0.12, min_history_days=504, max_spread_bps=40,
        notes="서브섹터(오피스/데이터센터/물류)가 완전히 다름. 단일 REIT 팩터 금지.",
    ),

    "CRYPTO": AssetClassSpec(
        code="CRYPTO", label_ko="암호자산", ann_factor=365,
        factor_prior=["crypto_mkt", "mkt_excess", "broad_dollar", "vix"],
        r2_band=(0.20, 0.90),
        stress={"crypto_mkt": -0.45, "mkt_excess": -0.20, "vix": 25.0},
        max_weight=0.05, min_history_days=365, max_spread_bps=60,
        notes="연율화 √365. √252를 쓰면 변동성이 약 17% 과소평가됨.",
    ),

    "FX": AssetClassSpec(
        code="FX", label_ko="통화", ann_factor=252,
        factor_prior=["broad_dollar", "curve_2s10s", "vix", "nominal_2y"],
        r2_band=(0.35, 0.95),
        stress={"broad_dollar": 0.05, "vix": 20.0},
        max_weight=0.20, min_history_days=504, max_spread_bps=15,
        notes="캐리는 crash risk가 본질. 왜도·꼬리의존성 병기 필수.",
    ),

    "LEVERAGED": AssetClassSpec(
        code="LEVERAGED", label_ko="레버리지/인버스 ETP", ann_factor=252,
        factor_prior=["mkt_excess", "vix"],
        r2_band=(0.75, 0.999),
        stress={"mkt_excess": -0.20, "vix": 25.0},
        max_weight=0.03, min_history_days=252, max_spread_bps=30,
        path_dependent=True,
        notes=("일간 리밸런싱 → 경로의존. 장기 기대 로그수익 ≈ Lμ − L(L−1)σ²/2. "
               "몬테카를로는 기초자산에 돌리고 레버리지 경로를 재구성해야 함."),
    ),

    "VOL_ETP": AssetClassSpec(
        code="VOL_ETP", label_ko="변동성 ETP", ann_factor=252,
        factor_prior=["vix", "mkt_excess"],
        r2_band=(0.55, 0.98),
        stress={"vix": 25.0, "mkt_excess": -0.20},
        max_weight=0.02, min_history_days=252, max_spread_bps=50,
        path_dependent=True, needs_roll_model=True,
        notes="롤 손실이 수익률을 지배. 장기 보유 전제의 분석 자체가 부적절.",
    ),

    "INDEX": AssetClassSpec(
        code="INDEX", label_ko="지수(비거래)", ann_factor=252,
        factor_prior=_EQ_CORE,
        r2_band=(0.60, 0.999),
        stress={"mkt_excess": -0.20},
        max_weight=0.0, min_history_days=252, max_spread_bps=1e9,
        notes="거래 불가. 참조용만.",
    ),

    "MUTUALFUND": AssetClassSpec(
        code="MUTUALFUND", label_ko="뮤추얼펀드", ann_factor=252,
        factor_prior=_EQ_CORE,
        r2_band=(0.50, 0.99),
        stress={"mkt_excess": -0.20},
        max_weight=0.20, min_history_days=504, max_spread_bps=1e9,
        notes="NAV 기준 → 평활화. 샤프 과대 위험, 언스무딩 필요.",
    ),

    "UNKNOWN": AssetClassSpec(
        code="UNKNOWN", label_ko="미분류", ann_factor=252,
        factor_prior=["mkt_excess", "vix"],
        r2_band=(0.0, 1.0),
        stress={"mkt_excess": -0.20},
        max_weight=0.02, min_history_days=504, max_spread_bps=50,
        notes="분류 불확실 → 최소 사이즈만 허용.",
    ),
}

# 팩터 프록시 티커 (FRED로 못 얻는 것). 사용자가 교체 가능.
# ---------------------------------------------------------------- 팩터 다리
#
# 팩터는 **롱숏 스프레드**다. 롱온리 ETF 수익률을 그대로 팩터로 쓰면
# 전부 시장 하나가 된다. 실측하면 이렇게 나온다(8년, SPY 대비 R²):
#     cma(SPY)  100.0%   rmw(QUAL) 96.6%   hml(IWD) 85.2%
#     umd(MTUM)  76.8%   smb(IWM)  75.5%
# 설계행렬이 특이행렬에 가까워져 회귀계수는 시장 베타를 임의로 쪼갠 값이
# 되고, 그 위에 얹힌 시나리오·델타패널·스트레스·헤지비율이 전부 무의미해진다.
# (애플의 가치(HML) 베타가 +1.0 으로 나오던 원인이 이것이다.)
#
# 그래서 (롱, 숏) 다리로 정의하고 수익률 차이를 팩터로 쓴다.
# CMA(보수적 투자)는 무료로 쓸 만한 롱숏 프록시가 없어 제외한다 —
# 없는 팩터를 SPY 로 채우느니 빼는 게 낫다.
FACTOR_LEGS: Dict[str, Tuple[str, Optional[str]]] = {
    "mkt_excess": ("SPY",     None),      # 시장 (초과수익 근사)
    "smb":        ("IWM",     "SPY"),     # 소형 − 대형
    "hml":        ("IWD",     "IWF"),     # 가치 − 성장
    "rmw":        ("QUAL",    "SPY"),     # 퀄리티 − 시장
    "umd":        ("MTUM",    "SPY"),     # 모멘텀 − 시장
    "crypto_mkt": ("BTC-USD", None),
}

# 실제로 내려받아야 할 티커 목록 (중복 제거)
PROXY_UNIVERSE: List[str] = sorted(
    {t for legs in FACTOR_LEGS.values() for t in legs if t})

# load_proxies 는 {키: 티커} 를 받아 {키: 시세} 를 준다.
# 이제 키를 팩터명이 아니라 **티커**로 둔다(한 티커가 여러 팩터의 다리다).
PROXY_TICKERS: Dict[str, str] = {t: t for t in PROXY_UNIVERSE}

# ---------------------------------------------------------------- 하드 게이트


@dataclass(frozen=True)
class Gates:
    """게이트 실패는 '감점'이 아니라 '해당 모듈 출력 무효화(abstain)'다."""
    max_missing_ratio: float = 0.02
    max_zero_return_ratio: float = 0.35     # 무거래일 비율
    min_adv_usd: float = 1_000_000.0
    pbo_max: float = 0.50                   # PBO ≥ 50% → ML 폐기
    dsr_min: float = 0.90                   # DSR 신뢰확률 하한
    overfit_gap_max: float = 0.15           # in-sample − OOS 정확도
    brier_skill_min: float = 0.0            # 기후 벤치마크 대비
    resolution_min: float = 1e-4            # Murphy resolution
    conformal_target: float = 0.90
    conformal_tolerance: float = 0.07       # 실측 커버리지 허용 오차
    stress_loss_limit: float = 0.35         # 스트레스 손실 한도
    min_r2_ratio: float = 0.35              # 기대밴드 하단 대비 최소 비율


GATES = Gates()

# ---------------------------------------------------------------- 실행 파라미터


@dataclass(frozen=True)
class RunConfig:
    lookback_years: int = 8
    horizons: Tuple[int, ...] = (5, 21, 63)      # 영업일
    vol_halflife: int = 20
    cpcv_groups: int = 12
    cpcv_test_groups: int = 2
    embargo_frac: float = 0.01
    n_sims: int = 20_000
    sim_horizon_days: int = 252
    drift_shrink: float = 0.60                   # 장기 앵커로의 축소 강도
    kelly_cap: float = 0.25
    vol_target: float = 0.10
    risk_free: float = 0.04
    seed: int = 20260814
    # 시행횟수 로깅: DSR은 N에 극도로 민감하므로 반드시 기록해야 함
    n_trials_declared: int = 1


RUN = RunConfig()
