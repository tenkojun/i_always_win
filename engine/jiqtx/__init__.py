# -*- coding: utf-8 -*-
"""
jiqtx — 범자산 정밀 분석 엔진.

Yahoo Finance 전 종목을 자산군·종목 성격별로 다른 렌즈로 분석하고,
전문가 패널 심의를 거쳐 자기완결 HTML 보고서를 만든다.

  설계 원칙 하나 — 정밀함은 지표를 더 넣는 것이 아니라
                  "틀린 출력을 내지 않는 능력"이다.

파이프라인
----------
  데이터 수집 ─ 무결성 검증                      → 실패 시 HALT
     ↓
  자산군 분류 (메타데이터 + 통계 지문)            → 팩터 prior 결정
     ↓
  유동성·거래비용 (EDGE / Amihud / 제곱근임팩트)  → 실패 시 SIZE_ZERO
     ↓
  변동성 (GJR-GARCH-t MLE) ─ 레짐 (Jump Model)
     ↓
  팩터모델 (ElasticNet→OLS/HAC) + Kalman 시변베타 + 델타 패널
     ↓
  주식 성격 프로파일 (9개 아키타입)               → 보고서 섹션 구성 결정
     ↓
  ML (트리플배리어 → Purged CV → Murphy → PBO/DSR) → 실패 시 ABSTAIN
     ↓
  시뮬레이션 (FHS + GPD 꼬리 + 드리프트 사후분포)
     ↓
  리스크 (VaR/ES + 커버리지검정) ─ 스트레스 ─ 낙폭제약 켈리
     ↓
  투자 논지 (시나리오 · 트레이드 · 헤지 · 반증조건 · 귀인)
     ↓
  전문가 패널 14명 (소견 + 반대신문 + 증거위계)
     ↓
  하드게이트 → 결정론적 판정 엔진 → 동적 HTML 보고서
     ↓
  포트폴리오 (위험기여 · 팩터넷팅 · 배분경합) ─ 원장 (예측 저장 · 채점)

핵심 설계 결정
--------------
1. 게이트 실패는 감점이 아니라 **모듈 무효화(abstain)**.
   OOS 정확도 50%는 약한 신호가 아니라 신호 없음이다.
2. 팩터 로딩은 상수가 아니라 레짐 함수다. β는 시변 추정하고
   β 안정성 자체를 지표로 보고한다. R² 붕괴는 버그가 아니라 구조변화 신호.
3. 드리프트 표준오차는 σ/√T 이며 일봉 표본에서 거의 항상 추정치만큼 크다.
   따라서 GBM 상승확률은 시장이 아니라 가정에 대한 진술이다.
4. PBO 는 전략 **선택 절차**의 과적합 지표이지 신호 존재 지표가 아니다.
   → 소프트 게이트. 하드 게이트는 전략 DSR.
5. 켈리의 문제는 공식이 아니라 μ를 안다고 가정한 것.
   → 예측분포 시뮬 + **낙폭 제약**.
6. 멀티에이전트 토론은 자동으로 정확도를 올리지 않는다(동조·마팅게일 정체).
   → 정보 비대칭 + 선언된 편향 + **판정은 결정론적 규칙 엔진**.

한계 (반드시 읽을 것)
---------------------
· 생존편향: Yahoo Finance 에 상장폐지 종목 없음 → 종목선택 전략 검증 불가
· 일봉만 → 진짜 실현변동성·오더플로우 불가
· 펀더멘털은 point-in-time 아님 → 시계열 백테스트 금지, 현재 진단만
· 옵션은 스냅샷만 → 백테스트 불가
· 적중률은 크게 오르지 않는다.
  개선은 거짓신호 제거·리스크추정·사이징 규율에서 나온다.

본 소프트웨어는 방법론 연구·검증 목적이며 투자 자문이 아니다.

사용
----
    from engine.jiqtx import analyze, save_html
    a = analyze("GLD")
    save_html(a, "GLD.html")
"""
from __future__ import annotations

import importlib
from typing import Any

__all__ = [
    # 파이프라인
    "analyze", "Analysis", "RunConfig", "RUN", "GATES",
    # 리포트
    "render_html", "save_html", "build_sections",
    "render_markdown", "save_markdown",
    # 포트폴리오
    "analyze_portfolio", "render_portfolio", "save_portfolio",
    # 데이터
    "load_prices", "check_integrity",
    # 원장 · 리플레이
    "Ledger", "replay",
    # 오프라인 검증
    "run_demo", "run_validation",
]

_LAZY: dict[str, tuple[str, str]] = {
    "analyze":            ("pipeline", "analyze"),
    "Analysis":           ("pipeline", "Analysis"),
    "RunConfig":          ("config", "RunConfig"),
    "RUN":                ("config", "RUN"),
    "GATES":              ("config", "GATES"),
    "render_html":        ("dynamic_report", "render_html"),
    "save_html":          ("dynamic_report", "save_html"),
    "build_sections":     ("dynamic_report", "build_sections"),
    "render_markdown":    ("report", "render"),
    "save_markdown":      ("report", "save"),
    "analyze_portfolio":  ("portfolio", "analyze_portfolio"),
    "render_portfolio":   ("portfolio_report", "render_portfolio"),
    "save_portfolio":     ("portfolio_report", "save_portfolio"),
    "load_prices":        ("data", "load_prices"),
    "check_integrity":    ("data", "check_integrity"),
    "Ledger":             ("ledger", "Ledger"),
    "replay":             ("replay", "replay"),
    "run_demo":           ("_demo", "run_demo"),
    "run_validation":     ("_validate", "run_validation"),
}


def __getattr__(name: str) -> Any:
    """무거운 하위 모듈은 실제로 쓸 때 불러온다."""
    try:
        mod, attr = _LAZY[name]
    except KeyError:
        raise AttributeError(
            f"module 'engine.jiqtx' has no attribute {name!r}") from None
    value = getattr(importlib.import_module(f".{mod}", __name__), attr)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(__all__)
