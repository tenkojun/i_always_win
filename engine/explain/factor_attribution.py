"""
================================================================
  요인 기여 분해  (Factor Attribution)
================================================================
explain 레이어 — 축 B의 1단계.

Bloomberg PORT·BarraOne 의 요인 기여 리포트처럼, 최종 판정이
**어떤 범주·어떤 증거에서 얼마나 왔는지**를 부호·기여율로 분해.

기관 리포트가 보여주는 것:
  - 범주별(추세/모멘텀/리스크/ML/팩터/시나리오/통계) 기여
  - 각 범주 내 상위 기여 증거
  - 강세/약세 진영 비중
  - 거부권이 무엇을 얼마나 깎았는지(이 앱의 차별점)
"""
from __future__ import annotations

from typing import Any, Dict, List


_CAT_ORDER = ["통계", "시나리오", "리스크", "팩터",
              "추세", "ML", "모멘텀", "오더플로우", "기타"]


def attribute(decide_result: Dict[str, Any]) -> Dict[str, Any]:
    """
    decide() 결과 → 기관급 기여 분해.

    Returns
    -------
    {
      "by_category": [
         {category, signed, abs_share_pct, n, direction,
          top_evidence:[{source,signed,rationale}]}, ...],
      "camps": {bull_pct, bear_pct},
      "veto_impact": [{source, rationale, target}],
      "headline": 한 줄 요약,
    }
    """
    evs: List[Dict[str, Any]] = decide_result.get("evidence", []) or []
    resolved = decide_result.get("resolved", {}) or {}
    verdict = decide_result.get("verdict", {}) or {}

    cat_acc: Dict[str, Dict[str, Any]] = {}
    bull_w = bear_w = 0.0
    for e in evs:
        if e.get("veto"):
            continue
        d = int(e.get("direction", 0))
        mag = float(e.get("magnitude", 0.0))
        conf = float(e.get("confidence", 0.0))
        signed = d * mag * conf
        cat = e.get("category", "기타")
        slot = cat_acc.setdefault(cat, {
            "signed": 0.0, "abs": 0.0, "n": 0, "items": []})
        slot["signed"] += signed
        slot["abs"] += abs(signed)
        slot["n"] += 1
        slot["items"].append({
            "source": e.get("source", ""),
            "signed": round(signed, 4),
            "rationale": e.get("rationale", ""),
        })
        if signed > 0:
            bull_w += signed
        elif signed < 0:
            bear_w += -signed

    total_abs = sum(s["abs"] for s in cat_acc.values()) or 1.0
    by_cat: List[Dict[str, Any]] = []
    for cat in _CAT_ORDER:
        if cat not in cat_acc:
            continue
        s = cat_acc[cat]
        s["items"].sort(key=lambda x: -abs(x["signed"]))
        by_cat.append({
            "category": cat,
            "signed": round(s["signed"], 4),
            "abs_share_pct": round(s["abs"] / total_abs * 100, 1),
            "n": s["n"],
            "direction": ("강세" if s["signed"] > 0.02 else
                          "약세" if s["signed"] < -0.02 else "중립"),
            "top_evidence": s["items"][:3],
        })

    tot_camp = bull_w + bear_w
    camps = {
        "bull_pct": round(bull_w / tot_camp * 100, 1) if tot_camp else 0,
        "bear_pct": round(bear_w / tot_camp * 100, 1) if tot_camp else 0,
    }

    veto_impact = [{
        "source": v.get("source", ""),
        "rationale": v.get("rationale", ""),
        "target": ("강세 신호 차단" if v.get("target") == 1
                   else "약세 신호 차단" if v.get("target") == -1
                   else "신호 차단"),
    } for v in resolved.get("vetoed", [])]

    top = by_cat[0] if by_cat else None
    if top:
        headline = (f"판정의 최대 동인은 '{top['category']}' "
                    f"({top['direction']}, 기여 {top['abs_share_pct']}%)")
        if veto_impact:
            headline += f" · 거부권 {len(veto_impact)}건이 강세를 제한"
    else:
        headline = "기여 분해할 증거가 부족합니다."

    return {
        "by_category": by_cat,
        "camps": camps,
        "veto_impact": veto_impact,
        "headline": headline,
        "signal": verdict.get("signal", ""),
        "score": verdict.get("score"),
        "grade": verdict.get("grade", ""),
    }
