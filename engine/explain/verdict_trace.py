"""
================================================================
  판정 추적기  (Verdict Trace) - XAI 축 B의 핵심
================================================================
"왜 이 판정인가"를 인과 사슬로 보여준다. Aladdin·Bloomberg 의
정형 리포팅·AI 코멘터리에는 없는 **거부권·충돌 가시화**가
I ALWAYS WIN 의 차별점이다.
"""
from __future__ import annotations
from typing import Any, Dict, List


def _fmt_contrib(c) -> Dict[str, Any]:
    if isinstance(c, dict):
        return c
    src, signed, rationale, category = (list(c) + [None, None, None, None])[:4]
    return {"source": src, "signed": signed,
            "rationale": rationale, "category": category}


def build_trace(meta: Dict[str, Any]) -> Dict[str, Any]:
    if not meta:
        return {"headline": "데이터 부족 — 판정 불가",
                "drivers": [], "detractors": [], "vetoes": [],
                "conflicts": [], "tree": [], "one_liner": "—",
                "signal": "-", "grade": "-", "score": 0}
    verdict = meta.get("verdict", {}) or {}
    resolved = meta.get("resolved", {}) or {}
    signal = verdict.get("signal", "-")
    grade = verdict.get("grade", "-")
    score = verdict.get("score", 0.0)

    contribs = [_fmt_contrib(c)
                for c in (resolved.get("contributions") or [])]
    drivers: List[Dict[str, Any]] = []
    detractors: List[Dict[str, Any]] = []
    for c in contribs:
        s = float(c.get("signed") or 0.0)
        if s > 0:
            drivers.append(c)
        elif s < 0:
            detractors.append(c)
    drivers.sort(key=lambda x: -abs(x.get("signed", 0)))
    detractors.sort(key=lambda x: -abs(x.get("signed", 0)))

    vetoes_raw = resolved.get("vetoed") or []
    vetoes = [{
        "source": v.get("source"),
        "target": ("강세 신호 차단" if v.get("target") == 1
                   else "약세 신호 차단" if v.get("target") == -1
                   else "신호 차단"),
        "rationale": v.get("rationale", ""),
        "confidence": v.get("confidence"),
    } for v in vetoes_raw]

    conflicts: List[str] = []
    cr = float(resolved.get("conflict_ratio") or 0.0)
    if cr >= 0.42:
        conflicts.append(
            f"증거가 팽팽히 충돌(소수 진영 비중 {cr*100:.0f}%) — "
            f"방향 단정보다 관망이 합리적")
    elif cr >= 0.30:
        conflicts.append(
            f"일부 증거 혼재(소수 진영 {cr*100:.0f}%) — 신뢰도 하향 반영됨")
    gate = float(resolved.get("regime_gate") or 1.0)
    if gate < 0.99:
        conflicts.append(
            f"국면 게이트 적용 → 신호 강도 ×{gate:.2f} (전환·불확실 국면)")

    by_cat: Dict[str, Dict[str, Any]] = {}
    for c in contribs:
        cat = c.get("category") or "기타"
        d = by_cat.setdefault(cat, {"pos": 0.0, "neg": 0.0, "items": []})
        s = float(c.get("signed") or 0)
        if s > 0:
            d["pos"] += s
        else:
            d["neg"] += -s
        d["items"].append(c)
    tree = []
    for cat, d in sorted(by_cat.items(),
                          key=lambda kv: -(kv[1]["pos"] + kv[1]["neg"])):
        net = d["pos"] - d["neg"]
        tree.append({
            "category": cat,
            "net": round(net, 3),
            "lean": ("강세" if net > 0.05 else
                     "약세" if net < -0.05 else "중립"),
            "n": len(d["items"]),
            "top": (d["items"][0].get("rationale") if d["items"] else ""),
        })

    if vetoes:
        v_short = ", ".join(v["source"].split("_")[0] for v in vetoes_raw)
        headline = (f"{signal} · {grade}({score:.0f}점) — 거부권 발동: {v_short}")
    elif drivers and not detractors:
        headline = (f"{signal} · {grade}({score:.0f}점) — 강세 증거 우세, 거부권 없음")
    elif detractors and not drivers:
        headline = (f"{signal} · {grade}({score:.0f}점) — 약세 증거 우세")
    elif cr >= 0.42:
        headline = (f"{signal} · {grade}({score:.0f}점) — 증거 충돌, 관망 권고")
    else:
        headline = f"{signal} · {grade}({score:.0f}점)"

    parts = []
    if drivers:
        parts.append(f"강세 주도: {drivers[0].get('rationale','')}")
    if vetoes:
        parts.append(f"거부권: {vetoes[0].get('rationale','')}")
    elif detractors:
        parts.append(f"약세 주도: {detractors[0].get('rationale','')}")
    if cr >= 0.42 and "충돌" not in " ".join(parts):
        parts.append("증거 혼재")
    one_liner = " · ".join(parts) if parts else "—"

    return {
        "headline": headline, "signal": signal, "grade": grade,
        "score": score, "drivers": drivers[:5],
        "detractors": detractors[:5], "vetoes": vetoes,
        "conflicts": conflicts, "tree": tree,
        "one_liner": one_liner,
    }


def render_trace_text(trace: Dict[str, Any]) -> str:
    """추적 결과를 한글 텍스트로(내러티브용)."""
    out = [f"▶ {trace.get('headline', '')}"]
    if trace.get("drivers"):
        out.append("  강세 기여:")
        for d in trace["drivers"][:3]:
            out.append(f"   + {d.get('rationale','')} "
                       f"({d.get('signed',0):+.2f})")
    if trace.get("detractors"):
        out.append("  약세 기여:")
        for d in trace["detractors"][:3]:
            out.append(f"   - {d.get('rationale','')} "
                       f"({d.get('signed',0):+.2f})")
    if trace.get("vetoes"):
        out.append("  거부권 발동:")
        for v in trace["vetoes"]:
            out.append(f"   ⚠ {v.get('rationale','')}")
    if trace.get("conflicts"):
        for c in trace["conflicts"]:
            out.append(f"  ◇ {c}")
    return "\n".join(out)
