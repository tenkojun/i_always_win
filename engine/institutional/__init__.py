"""
기관급 종목 분석 패키지 (Institutional Analytics)
==================================================
블랙록(BlackRock) Aladdin 류 기관 분석 기법을 단일 종목에
적용하기 위한 모듈 묶음.

구성
----
- mc_projection   : 단/중/장 미래 주가 몬테카를로 (경로 + 분포 통계)
- factor_risk     : Aladdin식 팩터 위험 분해 (체계적 vs 고유 위험)
- stress_test     : 시나리오/스트레스 테스트 (금리·폭락 가정)
- wealth_projection: 몬테카를로 부(富) 예측 (목표 달성 확률)
- risk_budget     : 자산배분·리스크 버짓 (변동성 타깃·켈리)
- scorecard       : 기관 스코어카드 (포트폴리오 카드 형태 종합 평점)
- narrative       : 모듈별 한글 분석 글 자동 생성기
"""
from .mc_projection import project_price_paths, mc_by_timeframe
from .factor_risk import factor_risk_decomposition
from .stress_test import stress_test, DEFAULT_SCENARIOS
from .wealth_projection import wealth_projection
from .risk_budget import risk_budget
from .scorecard import build_scorecard
from . import narrative
from .tail_risk import compute_tail_risk
from .cscv import run_cscv_from_returns
from .precision import precision_diagnostics

__all__ = [
    "project_price_paths", "mc_by_timeframe",
    "factor_risk_decomposition",
    "stress_test", "DEFAULT_SCENARIOS",
    "wealth_projection",
    "risk_budget",
    "build_scorecard",
    "narrative",
    "compute_tail_risk",
    "run_cscv_from_returns",
    "precision_diagnostics",
]
