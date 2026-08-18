"""
================================================================
  리스크 체인  (Risk Chain)
================================================================
JIQT v4 explain 레이어 — 축 B의 3단계.

"왜 위험한가"를 단계별 체인으로. 기관 리포트(BarraOne 요인
리스크, Aladdin Green Package)가 보여주는 리스크 분해를
일반 사용자도 읽을 수 있는 인과 체인으로 변환.
"""
from __future__ import annotations

from typing import Any, Dict, List


def build_risk_chain(precision: Dict[str, Any],
                     stress: Dict[str, Any],
                     factor: Dict[str, Any]) -> Dict[str, Any]:
    """
    정밀도/스트레스/팩터 → 리스크 인과 체인.

    Returns
    -------
    {"chain": [{level, text}], "risk_grade": "상/중/하", "summary": 한 줄}
    level: "info" | "caution" | "warning"
    """
    chain: List[Dict[str, str]] = []
    warn = 0

    # 1) 통계 신뢰도
    psr = (precision or {}).get("psr", {}).get("psr")
    dsr = (precision or {}).get("dsr", {}).get("dsr")
    trl = (precision or {}).get("min_trl", {})
    if dsr is not None and dsr == dsr:
        if dsr < 0.90:
            warn += 1
            chain.append({"level": "warning", "text": (
                f"다중검정 보정 샤프(DSR) {dsr*100:.0f}% < 90% — "
                f"여러 시도 중 우연히 좋아 보였을 위험. 성과의 "
                f"통계적 우위가 약함.")})
        else:
            chain.append({"level": "info", "text": (
                f"DSR {dsr*100:.0f}% — 다중검정 보정 후에도 우위 유지(양호).")})
    if trl and not trl.get("enough", True):
        warn += 1
        chain.append({"level": "warning", "text": (
            f"표본이 샤프 신뢰 최소길이 미달 "
            f"(보유 {trl.get('have','?')}일 < 필요 "
            f"{int(trl.get('min_trl',0))}일) — 현재 위험조정 "
            f"수익률은 통계적으로 확정 불가.")})

    # 2) 국면 의존성
    reg = (precision or {}).get("regime", {})
    worst = reg.get("worst_regime_sharpe")
    if worst is not None and worst < -0.3:
        warn += 1
        chain.append({"level": "warning", "text": (
            f"하락국면 샤프 {worst:.2f} — 상승장에서만 좋아 보이는 구조. "
            f"시장이 꺾이면 베타 노출이 그대로 손실로 전이될 위험.")})

    # 3) 시나리오 내성
    worst_loss = abs((stress or {}).get("worst_loss_pct", 0.0))
    if worst_loss > 0:
        lvl = ("warning" if worst_loss >= 50 else
               "caution" if worst_loss >= 30 else "info")
        if lvl != "info":
            warn += 1 if lvl == "warning" else 0
        chain.append({"level": lvl, "text": (
            f"최악 시나리오에서 약 {worst_loss:.0f}% 손실 가정 — "
            + ("위기 동반 시 회복 장기화 가능."
               if worst_loss >= 50 else "관리 가능 범위이나 주의."))})
    cdar = (stress or {}).get("cdar_pct")
    tuw = (stress or {}).get("time_under_water_days")
    if cdar is not None:
        chain.append({"level": "caution", "text": (
            f"조건부 낙폭(CDaR) {cdar:.0f}% · 원금 회복까지 최장 "
            f"약 {tuw or '?'}영업일 수중 — 단순 최대낙폭보다 "
            f"꼬리 위험을 보수적으로 본 값.")})

    # 4) 분산 가능성(팩터)
    sys_pct = (factor or {}).get("systematic_pct")
    if sys_pct is not None:
        if sys_pct > 70:
            chain.append({"level": "caution", "text": (
                f"위험의 {sys_pct:.0f}%가 시장(체계적) 요인 — "
                f"개별 종목처럼 보여도 사실상 시장 베타. 분산 효과가 제한적.")})
        else:
            chain.append({"level": "info", "text": (
                f"체계적 위험 {sys_pct:.0f}% — 시장 의존도 보통.")})

    if warn >= 3:
        grade, summary = "상", ("다수의 통계·시나리오 경고가 겹침 "
                                "— 위험 대비 보상 불확실, 보수적 접근 필수.")
    elif warn >= 1:
        grade, summary = "중", ("일부 위험 신호 존재 — 분할·소액 접근과 시나리오 점검 권고.")
    else:
        grade, summary = "하", ("주요 위험 지표가 기준을 통과 — 다만 시장 위험은 상존.")

    return {"chain": chain, "risk_grade": grade, "summary": summary}
