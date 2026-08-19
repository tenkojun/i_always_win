# ==============================================================================
# [18/25] dynamic_report.py — 동적 섹션 레지스트리 35개 · 자기완결 HTML
# ==============================================================================

"""
jiqtx.dynamic_report — 동적 섹션 조립 + 자기완결 HTML 리포트.

동적이란 무엇인가
-----------------
고정 템플릿이 아니라 **섹션 레지스트리**다. 각 섹션은
  - applies(analysis) -> bool   : 이 자산에 이 섹션이 의미가 있는가
  - priority                    : 이 자산에서 얼마나 중요한가
  - render(analysis) -> html
를 가진다. 리포트는 `applies`가 True인 섹션만, `priority` 순으로 조립된다.

따라서
  · 금 ETF     → 실질금리 레짐 섹션이 최상단, 어닝 섹션 없음
  · 고성장 적자주 → 현금소진·희석·금리듀레이션 섹션, PER 섹션 없음
  · 배당주      → 배당 커버리지·금리 민감도 섹션
  · 바이오텍    → 점프·꼬리 섹션이 최상단, 평균 통계는 경고와 함께 강등
  · 레버리지 ETP → 변동성 드래그 분해 섹션
  · 국채 ETF    → 듀레이션/KRD 섹션
가 자동으로 달라진다.
"""

import html
import math
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

# ── 패키지 내부 의존 ──────────────────────────────────────────
from . import charts as ch
from .glossary import TERMS as _TERMS
from .glossary import build_css as _tip_css
from .glossary import build_js as _tip_js
from .report_theme import DEFAULT_THEME, themed_css




# ================================================================ 유틸

def _f(x, fmt="{:.2%}", na="—"):
    try:
        if x is None or (isinstance(x, float) and not np.isfinite(x)):
            return na
        return fmt.format(x)
    except Exception:
        return na


def E(s) -> str:
    return html.escape(str(s))


def kv_table(rows: List[tuple], cls="kv") -> str:
    body = "".join(
        f'<tr><td class="k">{E(k)}</td><td class="v">{v}</td>'
        f'<td class="n">{E(n) if n else ""}</td></tr>'
        for k, v, *rest in [(r + ("",))[:3] if len(r) == 2 else r for r in rows]
        for n in [rest[0] if rest else ""]
    )
    return f'<table class="{cls}">{body}</table>'


def df_table(df: pd.DataFrame, cols=None, rename=None, nd=3,
             highlight: Optional[Callable[[pd.Series], str]] = None) -> str:
    if df is None or len(df) == 0:
        return '<p class="muted">데이터 없음</p>'
    d = df[cols].copy() if cols else df.copy()
    if rename:
        d = d.rename(columns=rename)
    head = "".join(f"<th>{E(c)}</th>" for c in d.columns)
    rows = []
    for _, row in d.iterrows():
        cls = highlight(row) if highlight else ""
        tds = []
        for v in row.values:
            if isinstance(v, float):
                tds.append(f"<td>{'' if not np.isfinite(v) else f'{v:,.{nd}f}'}</td>")
            else:
                tds.append(f"<td>{E(v)}</td>")
        rows.append(f'<tr class="{cls}">' + "".join(tds) + "</tr>")
    return (f'<div class="tw"><table class="dt"><thead><tr>{head}</tr></thead>'
            f'<tbody>{"".join(rows)}</tbody></table></div>')


def note(text, kind="info") -> str:
    return f'<div class="note {kind}">{text}</div>'


# ================================================================ 섹션 정의

@dataclass
class Section:
    sid: str
    title: str
    icon: str
    applies: Callable[[Any], bool]
    render: Callable[[Any], str]
    priority: int = 50          # 낮을수록 위
    open_default: bool = False
    tag: str = ""               # 배지
    part: str = ""              # 정보구조 그룹 (자동 배정)


# 보고서 정보구조 — 34개 섹션을 6부로 묶는다.
PARTS: List[Tuple[str, str, str]] = [
    ("I",   "판정",        "이 자산이 무엇이고 지금 어떤 결론인가"),
    ("II",  "투자 논지",    "시나리오 · 트레이드 · 헤지 · 반증 조건"),
    ("III", "전문가 심의",  "직능별 소견과 반대신문"),
    ("IV",  "종목 진단",    "성격에 맞춘 개별 분석"),
    ("V",   "리스크",       "손실 측정과 사이징"),
    ("VI",  "운영·한계",    "모니터링과 이 분석이 못 하는 것"),
]

SECTION_PART: Dict[str, str] = {
    "exec": "I", "verdict": "I", "character": "I", "gates": "I",
    "horizons": "I", "macro": "II",
    "thesis": "II", "trade": "II", "hedge": "II", "kill": "II", "attrib": "II",
    "panel": "III", "agents": "III", "red": "III",
    "fundamentals": "IV", "runway": "IV", "crowding": "IV", "jump": "IV",
    "style": "IV", "peer": "IV", "rate": "IV", "duration": "IV", "drag": "IV",
    "earnings": "IV", "liquidity": "IV", "perf": "IV", "vol": "IV",
    "regime": "IV", "factor": "IV", "delta": "IV", "ml": "IV", "sim": "IV",
    "options": "IV",
    "risk": "V", "sizing": "V",
    "monitor": "VI", "limits": "VI",
}


REGISTRY: List[Section] = []


def section(sid, title, icon="", priority=50, open_default=False, tag=""):
    def deco(fn):
        def _applies(a):
            try:
                return bool(fn.applies(a))
            except Exception:
                return True
        REGISTRY.append(Section(sid, title, icon,
                                getattr(fn, "applies", lambda a: True),
                                fn, priority, open_default, tag))
        return fn
    return deco


def _has_equity(a) -> bool:
    return getattr(a, "equity", None) is not None


def _eq_section(a, name) -> bool:
    eq = getattr(a, "equity", None)
    return eq is not None and name in eq.active_sections


# ---------------------------------------------------------------- 1. 판정

def r_verdict(a) -> str:
    v = a.verdict
    gmap = {"BUY": ("매수", "up"), "ACCUMULATE": ("분할 매집", "up"),
            "HOLD": ("보유/관망", "neu"), "REDUCE": ("비중 축소", "down"),
            "AVOID": ("신규 진입 불가", "down"), "ABSTAIN": ("판단 보류", "neu")}
    lab, cl = gmap.get(v.grade, (v.grade, "neu"))
    g = [
        f'<div class="cards">',
        f'<div class="card {cl}"><div class="cl">방향 등급</div>'
        f'<div class="cv">{E(v.grade)}</div><div class="cs">{E(lab)}</div>'
        f'<div class="cs2">확률 {_f(v.direction_prob,"{:.1%}")} · '
        f'CI [{_f(v.direction_ci[0],"{:.1%}")}, {_f(v.direction_ci[1],"{:.1%}")}]</div></div>',
        f'<div class="card"><div class="cl">리스크 예산</div>'
        f'<div class="cv">{_f(v.risk_budget_weight,"{:.1%}")}</div>'
        f'<div class="cs">구속: {E(a.sizing.binding_constraint if a.sizing else "—")}</div></div>',
        f'<div class="card"><div class="cl">모델 신뢰도</div>'
        f'<div class="cv">{E(v.model_confidence)}</div>'
        f'<div class="cs">무효화 모듈 {len(v.disabled_modules)}개</div></div>',
        f'</div>',
    ]
    g.append(note("단일 점수는 산출하지 않습니다. 서로 다른 성질의 정보를 하나의 "
                  "숫자로 합치면 정보가 파괴됩니다."))
    if v.vetoes:
        g.append(note(f"⛔ <b>거부권 발동</b>: {E(', '.join(v.vetoes))}", "bad"))
    g.append("<ul class='rl'>" +
             "".join(f"<li>{E(x)}</li>" for x in v.rationale) + "</ul>")
    return "".join(g)


r_verdict.applies = lambda a: True
REGISTRY.append(Section("verdict", "최종 판정 — 3축 분리", "◆",
                        r_verdict.applies, r_verdict, 1, True))


# ---------------------------------------------------------------- 2. 종목 성격

def r_character(a) -> str:
    cls = a.classification
    fp = cls.fingerprint
    sp = cls.spec
    out = []
    eq = getattr(a, "equity", None)
    if eq is not None:
        out.append(
            f'<div class="hero"><div class="hero-t">{E(eq.archetype_ko)}</div>'
            f'<div class="hero-s">{E(eq.archetype_desc)}</div>'
            f'<div class="hero-b">아키타입 신뢰도 '
            f'{eq.archetype_confidence:.0%} · 코드 {E(eq.archetype)}</div></div>')
        if eq.archetype_evidence:
            out.append("<div class='chips'>" + "".join(
                f"<span class='chip'>{E(x)}</span>"
                for x in eq.archetype_evidence[:6]) + "</div>")
        out.append('<div class="two">')
        out.append("<div><h4>밸류에이션 앵커</h4><ul>" + "".join(
            f"<li>{E(x)}</li>" for x in eq.valuation_anchors) + "</ul></div>")
        out.append("<div><h4>이 성격에서 특히 볼 것</h4><ul>" + "".join(
            f"<li>{E(x)}</li>" for x in eq.watch_items) + "</ul></div>")
        out.append("</div>")
        out.append(note(f"이 아키타입 판정에 따라 아래 섹션 구성이 달라집니다 "
                        f"→ 활성 섹션: <code>{E(', '.join(eq.active_sections))}</code>"))

    rows = [
        ("자산군", E(sp.label_ko), f"신뢰도 {cls.confidence:.0%}"),
        ("quoteType / 섹터", f"{E(cls.quote_type or '—')} / {E(cls.sector or '—')}", ""),
        ("연율화 기준", f"√{sp.ann_factor}",
         "주말 거래 관측 → 24/7 시장" if fp.trades_weekends else "영업일 기준"),
        ("연변동성", _f(fp.ann_vol), f"왜도 {fp.skew:+.2f} · 첨도 {fp.kurtosis:.1f}"),
        ("1차 자기상관", f"{fp.autocorr1:+.3f}",
         "⚠ 평활화 의심 → 샤프 과대" if fp.smoothing_suspected else "정상"),
        ("최적 프록시", E(fp.best_proxy or "—"),
         f"β={fp.best_beta:+.2f} · R²={_f(fp.best_r2,'{:.1%}')}"),
        ("레버리지 탐지",
         f"{fp.leverage_detected:+.0f}x" if fp.leverage_detected else "없음",
         "경로의존 — 변동성 드래그 반영" if fp.leverage_detected else ""),
        ("이력", f"{fp.n_obs}영업일 (약 {fp.n_obs/252:.1f}년)",
         f"요구 {sp.min_history_days}일"),
    ]
    out.append(kv_table(rows))
    if sp.notes:
        out.append(note(f"<b>{E(sp.label_ko)} 고유 주의사항</b> — {E(sp.notes)}", "warn"))
    for w in cls.warnings:
        out.append(note(f"⚠ {E(w)}", "warn"))
    return "".join(out)


r_character.applies = lambda a: True
REGISTRY.append(Section("character", "종목 성격 — 어떤 렌즈로 볼 것인가", "◈",
                        r_character.applies, r_character, 2, True))


# ---------------------------------------------------------------- 0. 요약

def r_exec(a) -> str:
    v, t, h = a.verdict, a.trade, a.hedge
    cls, eq = a.classification, getattr(a, "equity", None)
    ev = a.scenario_ev or {}
    kb = [k for k in (a.kill or []) if k.breached]

    what = (f"{E(a.ticker)}는 <b>{E(cls.spec.label_ko)}</b>"
            + (f", 성격은 <b>{E(eq.archetype_ko)}</b>" if eq and
               eq.archetype != "UNCLASSIFIED" else "")
            + f"이며, 현재 판정은 <b>{E(v.grade)}</b>"
            + (f" (통합확률 {v.direction_prob:.1%})" if v.direction_prob else "")
            + f"이고 리스크 예산 <b>{v.risk_budget_weight:.1%}</b>입니다.")

    why = []
    if len(a.delta_panel):
        top = a.delta_panel.iloc[0]
        why.append(f"손익의 최대 단일 기여 요인은 <b>{E(top['shock_label'])}</b>로, "
                   f"표준 충격 시 {top['delta_pct']:+.1%} "
                   f"(하방베타 기준 {top['delta_pct_downside']:+.1%})입니다.")
    if h is not None and np.isfinite(h.var_removed):
        why.append(f"위험의 {h.var_removed:.0%}는 팩터로 헤지 가능하고 "
                   f"나머지 {1-h.var_removed:.0%}는 고유 요인이라 "
                   f"<b>사이즈로만 통제</b>됩니다.")
    if a.ml is not None and a.ml.verdict != "SIGNAL":
        why.append("방향 예측 모듈은 게이트를 통과하지 못해 "
                   "<b>확률을 출력하지 않았습니다</b>.")
    if ev.get("ev") is not None and np.isfinite(ev.get("ev", np.nan)):
        why.append(f"시나리오 가중 기대수익은 <b>{ev['ev']:+.1%}</b>"
                   f"(상방 {ev['ev_up']:+.1%} / 하방 {ev['ev_down']:+.1%})입니다.")

    change = []
    if kb:
        change.append("현재 <b>발동된</b> 반증 조건: " +
                      ", ".join(E(k.trigger) for k in kb))
    for k in (a.kill or [])[:3]:
        if not k.breached:
            change.append(f"{E(k.trigger)} — {E(k.metric)}이 {E(k.threshold)}가 되면")

    cards = [f'<div class="cards">']
    cards.append(f'<div class="card"><div class="cl">판정</div>'
                 f'<div class="cv">{E(v.grade)}</div>'
                 f'<div class="cs">신뢰도 {E(v.model_confidence)}</div></div>')
    if t is not None:
        cards.append(f'<div class="card"><div class="cl">기대손익 (비용 후)</div>'
                     f'<div class="cv">{_f(t.expected_pnl_net,"{:+.1%}")}</div>'
                     f'<div class="cs">R:R {_f(t.rr_ratio,"{:.2f}")} · '
                     f'P(목표) {_f(t.p_target_first,"{:.0%}")}</div></div>')
    if h is not None:
        cards.append(f'<div class="card"><div class="cl">헤지 후 잔차 변동성</div>'
                     f'<div class="cv">{_f(h.residual_vol_ann,"{:.1%}")}</div>'
                     f'<div class="cs">무헤지 {_f(h.unhedged_vol_ann,"{:.1%}")}</div></div>')
    cards.append(f'<div class="card {"down" if kb else ""}">'
                 f'<div class="cl">반증 조건 발동</div>'
                 f'<div class="cv">{len(kb)}/{len(a.kill or [])}</div>'
                 f'<div class="cs">사전등록된 kill criteria</div></div>')
    cards.append("</div>")

    return ("".join(cards) +
            f'<div class="hi"><b>무엇인가</b> — {what}</div>' +
            ("<div class='hi'><b>왜 그런가</b><ul class='rl'>" +
             "".join(f"<li>{x}</li>" for x in why) + "</ul></div>" if why else "") +
            ("<div class='hi'><b>무엇이 바뀌면 생각을 바꾸는가</b><ul class='rl'>" +
             "".join(f"<li>{x}</li>" for x in change) + "</ul></div>"
             if change else "") +
            note("이 요약은 아래 섹션의 산출물에서 기계적으로 조립됩니다. "
                 "서술이 아니라 <b>계산 결과</b>입니다."))


r_exec.applies = lambda a: True
REGISTRY.append(Section("exec", "요약 — 무엇을 / 왜 / 무엇이 바뀌면", "★",
                        r_exec.applies, r_exec, 0, True))


# ---------------------------------------------------------------- 2b. 시나리오

def r_thesis(a) -> str:
    sc = a.scenarios
    ev = a.scenario_ev or {}
    out = [note("시나리오를 분위수로 자르지 않고 <b>드라이버로 정의</b>합니다. "
                "손익은 팩터 델타 × 충격으로 계산하고, 확률은 그 충격 조합의 "
                "<b>역사적 동시 발생 빈도</b>에서 추정합니다.")]
    out.append(ch.scenario_chart([s.name for s in sc],
                                 [s.ret_target for s in sc],
                                 [s.prob_empirical for s in sc],
                                 [s.prob_model for s in sc]))
    rows = []
    for s in sc:
        rows.append({"시나리오": s.name, "구분": s.kind,
                     "드라이버 조건": s.driver_desc,
                     "경험확률": s.prob_empirical, "모델확률": s.prob_model,
                     "관측횟수": s.n_historical,
                     "손익": s.ret_target, "목표가": s.price_target})
    out.append(df_table(pd.DataFrame(rows), nd=4))
    if ev:
        out.append(kv_table([
            ("시나리오 가중 기대수익", f"<b>{_f(ev.get('ev'),'{:+.2%}')}</b>"),
            ("상방 기여", _f(ev.get("ev_up"), "{:+.2%}")),
            ("하방 기여", _f(ev.get("ev_down"), "{:+.2%}")),
            ("페이오프 비율", _f(ev.get("payoff_ratio"), "{:.2f}"),
             "상방기여 / |하방기여|"),
        ]))
        if ev.get("no_downside_scenario"):
            out.append(note("드라이버 기반 시나리오 중 <b>손실이 나는 것이 "
                            "없습니다.</b> 페이오프 비율이 정의되지 않으며, "
                            "이는 좋은 신호가 아니라 <b>하방 위험의 출처가 "
                            "선택된 팩터가 아니라는 뜻</b>입니다. 하방은 "
                            "VaR/ES·낙폭·점프 섹션에서 읽어야 합니다.", "warn"))
    for s_ in sc:
        if s_.note:
            out.append(note(f"<b>{E(s_.name)}</b> — {E(s_.note)}", "warn"))
    gaps = [s for s in sc if np.isfinite(s.prob_model) and
            abs(s.prob_empirical - s.prob_model) > 0.15]
    if gaps:
        out.append(note("경험확률과 모델확률의 괴리가 큰 시나리오: " +
                        ", ".join(f"{E(s.name)} ({s.prob_empirical:.0%} vs "
                                  f"{s.prob_model:.0%})" for s in gaps) +
                        ". 드라이버 기반 빈도와 모델 분포가 다르다는 뜻이며, "
                        "둘 중 하나(또는 둘 다)가 틀렸습니다.", "warn"))
    return "".join(out)


r_thesis.applies = lambda a: bool(a.scenarios)
REGISTRY.append(Section("thesis", "투자 논지 — 드라이버 기반 시나리오", "◎",
                        r_thesis.applies, r_thesis, 3, True))


# ---------------------------------------------------------------- 2c. 트레이드

def r_trade(a) -> str:
    t = a.trade
    out = [note("\"비중 15%\"는 결론이 아닙니다. 어디서 들어가고, 어디서 "
                "<b>틀렸다고 인정하고</b>, 어디를 목표로 하며, 그 확률이 얼마이고, "
                "비용 후 기대값이 양수인지가 트레이드입니다.")]
    out.append(ch.trade_ladder(t.entry, t.stop, t.target,
                               t.p_target_first, t.p_stop_first))
    out.append(kv_table([
        ("방향", f"<b>{E(t.direction)}</b>"),
        ("진입 참고가", f"{t.entry:,.2f}", E(t.entry_note)),
        ("손절", f"{t.stop:,.2f} ({t.stop_pct:.1%})", E(t.stop_basis)),
        ("목표", f"{t.target:,.2f} ({t.target_pct:+.1%})", E(t.target_basis)),
        ("보상/위험 (R:R)", _f(t.rr_ratio, "{:.2f}")),
        ("보유 지평", f"{t.horizon_days}영업일"),
    ]))
    out.append("<h4>배리어 확률 (시뮬 경로 기반)</h4>")
    out.append(kv_table([
        ("목표 선도달", f"<b>{_f(t.p_target_first,'{:.1%}')}</b>"),
        ("손절 선도달", _f(t.p_stop_first, "{:.1%}")),
        ("지평 내 미도달", _f(t.p_neither, "{:.1%}")),
        ("기대손익 (배리어 반영)", _f(t.expected_pnl, "{:+.2%}")),
        ("왕복 거래비용", _f(t.roundtrip_cost, "{:.2%}"),
         "EDGE 스프레드 + 제곱근 임팩트"),
        ("비용 후 기대손익", f"<b>{_f(t.expected_pnl_net,'{:+.2%}')}</b>"),
        ("손익분기 승률", _f(t.breakeven_hit_rate, "{:.1%}"),
         "손절폭/(손절폭+목표폭)"),
        ("엣지 (승률 − 손익분기)", f"<b>{_f(t.edge_vs_breakeven,'{:+.1%}')}</b>"),
    ]))
    out.append("<h4>리스크 예산</h4>")
    out.append(kv_table([
        ("사이즈", _f(t.size_weight, "{:.1%}")),
        ("손절 도달 시 계좌 손실", f"<b>{_f(t.max_loss_pct,'{:.2%}')}</b>",
         "사이즈 × 손절폭"),
    ]))
    kind = ("info" if "충족" in t.verdict else
            "bad" if ("부적합" in t.verdict or "없음" in t.verdict or
                      "불가" in t.verdict) else "warn")
    out.append(note(f"<b>판정: {E(t.verdict)}</b>", kind))
    if t.notes:
        out.append("<ul class='rl'>" +
                   "".join(f"<li>{E(x)}</li>" for x in t.notes) + "</ul>")
    return "".join(out)


r_trade.applies = lambda a: a.trade is not None
REGISTRY.append(Section("trade", "트레이드 구성 — 진입 · 손절 · 목표 · 기대값", "◧",
                        r_trade.applies, r_trade, 4, True))


# ---------------------------------------------------------------- 2d. 헤지

def r_hedge(a) -> str:
    h = a.hedge
    out = [note("최소분산 헤지비율은 <b>다변량 팩터 회귀 계수와 동일</b>합니다. "
                "따라서 팩터 모델이 미스매칭이면 헤지도 함께 무효입니다. "
                "이 연결을 끊고 헤지를 논하면 안 됩니다.")]
    if np.isfinite(h.var_removed):
        out.append(f'<div class="gauges">{ch.hedge_donut(h.var_removed)}'
                   f'{ch.gauge(h.residual_vol_ann, 0, max(h.unhedged_vol_ann,0.01), "헤지 후 잔차 변동성", good_high=False)}'
                   f'{ch.gauge(h.hedge_cost_ann, 0, 0.03, "연 헤지 비용", good_high=False)}'
                   f'</div>')
    if h.legs:
        out.append(df_table(pd.DataFrame([{
            "팩터": l.factor, "헤지 수단": l.instrument,
            "헤지비율": l.hedge_ratio, "β": l.beta,
            "β안정성CV": l.stability_cv,
            "신뢰": "가능" if l.reliable else "불안정"} for l in h.legs]), nd=3))
    else:
        out.append('<p class="muted">유의한 팩터 노출이 없어 헤지 레그가 없습니다.</p>')
    out.append(kv_table([
        ("총 헤지 명목 (자산 1단위당)", _f(h.gross_hedge_notional, "{:.2f}")),
        ("제거되는 분산 비중", _f(h.var_removed, "{:.0%}")),
        ("무헤지 변동성", _f(h.unhedged_vol_ann)),
        ("헤지 후 잔차 변동성", f"<b>{_f(h.residual_vol_ann,'{:.1%}')}</b>"),
        ("연 헤지 비용 (분기 리밸런싱 가정)", _f(h.hedge_cost_ann, "{:.2%}")),
        ("헤지 후 순노출", E(h.net_exposure)),
    ]))
    out.append(note(f"<b>{E(h.verdict)}</b>", "info" if h.reliable else "warn"))
    if h.notes:
        out.append("<ul class='rl'>" +
                   "".join(f"<li>{E(x)}</li>" for x in h.notes) + "</ul>")
    return "".join(out)


r_hedge.applies = lambda a: a.hedge is not None
REGISTRY.append(Section("hedge", "헤지 설계 — 무엇을 상쇄하고 무엇이 남는가", "⊟",
                        r_hedge.applies, r_hedge, 6, True))


# ---------------------------------------------------------------- 2e. 킬

def r_kill(a) -> str:
    ks = a.kill
    rows = [{"반증 조건": k.trigger, "지표": k.metric, "임계": k.threshold,
             "현재": k.current, "상태": "발동" if k.breached else "정상",
             "발동 시 조치": k.action} for k in ks]
    n = sum(1 for k in ks if k.breached)
    return (note("사후에 만든 반증 조건은 의미가 없습니다. 분석 시점에 자동 생성해 "
                 "원장에 남기고, 이후 채점 때 함께 검증합니다.") +
            df_table(pd.DataFrame(rows),
                     highlight=lambda r: "bad" if r["상태"] == "발동" else "") +
            f'<div class="hi">발동 <b>{n}/{len(ks)}</b>건</div>' +
            (note("발동된 조건이 있습니다. 해당 모듈의 출력은 이미 신뢰 구간을 "
                  "벗어났을 수 있으며, 조치 열에 적힌 대로 처리해야 합니다.", "bad")
             if n else ""))


r_kill.applies = lambda a: bool(a.kill)
REGISTRY.append(Section("kill", "반증 조건 — 무엇이 사실이면 논지가 죽는가", "⚠",
                        r_kill.applies, r_kill, 7, True))


# ---------------------------------------------------------------- 2f. 귀인

def r_attrib(a) -> str:
    d = a.attribution
    if d is None or len(d) == 0:
        return '<p class="muted">귀인 산출 불가</p>'
    out = [note("최근 성과가 <b>종목 고유(알파)</b>인지 <b>그냥 베타</b>였는지 "
                "분해합니다. 알파 비중이 낮으면 같은 노출을 훨씬 싸게 "
                "복제할 수 있다는 뜻입니다.")]
    last = d.iloc[-1]
    beta_cols = [c for c in d.columns if c.startswith("β·")]
    out.append(ch.waterfall(
        [c.replace("β·", "") for c in beta_cols] + ["알파"],
        [float(last[c]) for c in beta_cols] + [float(last["알파(잔차)"])],
        total_label=f"총수익 ({last['기간']})",
        title=f"수익 귀인 워터폴 — {last['기간']}"))
    out.append(df_table(d, nd=4))
    if "알파 비중" in d.columns and d["알파 비중"].notna().any():
        av = float(d["알파 비중"].iloc[-1])
        if np.isfinite(av):
            if av < 0.2:
                out.append(note(f"장기 알파 비중 {av:.0%} — 성과의 대부분이 "
                                f"팩터 노출입니다. 개별 종목 리스크를 지면서 "
                                f"ETF로 얻을 수 있는 것을 얻고 있는지 "
                                f"점검하십시오.", "warn"))
            elif av > 0.6:
                out.append(note(f"장기 알파 비중 {av:.0%} — 성과의 대부분이 "
                                f"팩터로 설명되지 않습니다. 진짜 알파일 수도, "
                                f"모델에 없는 팩터일 수도 있습니다. "
                                f"팩터 R²({_f(a.factor_model.r2,'{:.0%}') if a.factor_model else '—'})와 "
                                f"함께 읽으십시오."))
    return "".join(out)


r_attrib.applies = lambda a: a.attribution is not None and len(a.attribution) > 0
REGISTRY.append(Section("attrib", "수익 귀인 — 알파인가 베타인가", "≡",
                        r_attrib.applies, r_attrib, 8, True))


# ---------------------------------------------------------------- 3. 게이트

def r_gates(a) -> str:
    t = a.gates.table()
    return (note("게이트 실패는 <b>감점이 아니라 모듈 무효화</b>입니다. OOS 50%는 "
                 "약한 신호가 아니라 신호 없음이고, 올바른 출력은 감점된 점수가 "
                 "아니라 <b>출력 없음</b>입니다.") +
            df_table(t, highlight=lambda r: "" if r["판정"] == "통과" else "bad"))


r_gates.applies = lambda a: True
REGISTRY.append(Section("gates", "하드 게이트", "▣", r_gates.applies,
                        r_gates, 5, True))


# ---------------------------------------------------------------- 4. 어닝

def r_earnings(a) -> str:
    es = a.equity.earnings
    urgent = es.days_to_next is not None and es.days_to_next <= 21
    out = []
    if urgent:
        out.append(note(f"⚠ <b>{es.days_to_next}일 뒤 어닝 발표</b> "
                        f"({E(es.next_date)}). 이벤트 리스크 구간입니다.", "bad"))
    out.append(f'<div class="gauges">'
               f'{ch.gauge(es.median_abs_move, 0, max(es.p90_abs_move*1.2, 0.05), "발표일 |수익률| 중앙값", good_high=False)}'
               f'{ch.gauge(es.p90_abs_move, 0, max(es.p90_abs_move*1.2, 0.05), "90분위 (테일 이벤트)", good_high=False)}'
               f'{ch.gauge(es.gap_share_of_var, 0, 0.5, "연 분산 중 어닝일 기여", good_high=False, threshold=0.15)}'
               f'</div>')
    rows = [
        ("관측 이벤트 수", f"{es.n_events}회", ""),
        ("다음 발표", E(es.next_date or "미상"),
         f"D-{es.days_to_next}" if es.days_to_next is not None else ""),
        ("발표 다음날 |초과수익| 평균", _f(es.mean_abs_move), ""),
        ("중앙값 / 90분위 / 최대",
         f"{_f(es.median_abs_move)} / {_f(es.p90_abs_move)} / {_f(es.max_abs_move)}", ""),
        ("평균 방향", _f(es.mean_move, "{:+.2%}"), ""),
        ("서프라이즈 양수 비율", _f(es.beat_rate, "{:.0%}"), ""),
        ("PEAD (양수 서프라이즈 t+1~20)", _f(es.pead_20d_pos, "{:+.2%}"), ""),
        ("PEAD (음수 서프라이즈 t+1~20)", _f(es.pead_20d_neg, "{:+.2%}"), ""),
        ("PEAD 스프레드", f"<b>{_f(es.pead_spread,'{:+.2%}')}</b>",
         f"t = {es.pead_tstat:+.2f}" if np.isfinite(es.pead_tstat) else ""),
        ("연 분산 중 어닝일 기여", _f(es.gap_share_of_var, "{:.0%}"), ""),
    ]
    out.append(kv_table(rows))
    out.append(note(E(es.note), "warn" if urgent or
                    es.gap_share_of_var > 0.15 else "info"))
    if es.gap_share_of_var > 0.15:
        out.append(note("분산이 소수의 발표일에 집중되어 있습니다. 평상시 변동성으로 "
                        "리스크를 재면 심각히 과소평가되며, 정규 VaR은 이 구조를 "
                        "표현하지 못합니다. 어닝 전후 사이즈를 별도로 관리해야 합니다.",
                        "warn"))
    return "".join(out)


r_earnings.applies = lambda a: _eq_section(a, "earnings_event")
REGISTRY.append(Section("earnings", "어닝 이벤트 스터디 · PEAD", "◇",
                        r_earnings.applies, r_earnings, 10, True,
                        tag="주식 전용"))


# ---------------------------------------------------------------- 5. 펀더멘털

def r_fundamentals(a) -> str:
    f = a.equity.fundamentals
    arch = a.equity.archetype
    # 아키타입별로 보여줄 항목이 다르다
    base = [("시가총액", f"${f.market_cap/1e9:,.1f}B" if np.isfinite(f.market_cap) else "—")]
    if arch in ("HYPERGROWTH_UNPROFITABLE",):
        rows = base + [
            ("EV / Sales", _f(f.ev_to_sales, "{:.1f}x")),
            ("매출성장", _f(f.revenue_growth, "{:+.1%}")),
            ("영업이익률", _f(f.operating_margin, "{:+.1%}")),
            ("Rule of 40", _f(f.rule_of_40, "{:.0f}"), "40 이상이 기준"),
            ("순이익률", _f(f.profit_margin, "{:+.1%}")),
            ("FCF", f"${f.fcf/1e6:,.0f}M" if np.isfinite(f.fcf) else "—"),
            ("순현금 / 시총", _f(f.net_cash_ratio, "{:+.1%}"), "자금조달 여력"),
            ("부채비율", _f(f.debt_to_equity, "{:.0f}%")),
        ]
        extra = note("적자 고성장주에 PER은 무의미합니다. EV/Sales와 Rule of 40, "
                     "그리고 <b>현금소진 잔여기간</b>이 실질 앵커입니다. "
                     "금리 상승 시 현금흐름 듀레이션이 길어 멀티플 축소 폭이 "
                     "가장 큰 유형입니다.", "warn")
    elif arch == "DEEP_VALUE":
        rows = base + [
            ("PER (trailing / forward)",
             f"{_f(f.trailing_pe,'{:.1f}x')} / {_f(f.forward_pe,'{:.1f}x')}"),
            ("P/B", _f(f.price_to_book, "{:.2f}x")),
            ("EV/EBITDA", _f(f.ev_to_ebitda, "{:.1f}x")),
            ("ROE", _f(f.roe, "{:+.1%}"), "P/B의 정당성 판단 기준"),
            ("영업이익률", _f(f.operating_margin, "{:+.1%}")),
            ("매출성장", _f(f.revenue_growth, "{:+.1%}"), "음수면 가치함정 경보"),
            ("부채비율", _f(f.debt_to_equity, "{:.0f}%")),
            ("유동비율", _f(f.current_ratio, "{:.2f}")),
        ]
        vt = []
        if np.isfinite(f.revenue_growth) and f.revenue_growth < 0:
            vt.append("매출 역성장")
        if np.isfinite(f.roe) and np.isfinite(f.price_to_book) and \
                f.roe < 0.06 and f.price_to_book < 1.0:
            vt.append("저ROE·저PBR 동시 — P/B가 싼 이유가 ROE일 가능성")
        if np.isfinite(f.debt_to_equity) and f.debt_to_equity > 150:
            vt.append("높은 레버리지")
        extra = note(("<b>가치 함정 점검</b>: " + ", ".join(vt)) if vt else
                     "<b>가치 함정 점검</b>: 뚜렷한 함정 신호 없음",
                     "bad" if len(vt) >= 2 else "warn" if vt else "info")
    elif arch in ("DIVIDEND_INCOME", "DEFENSIVE"):
        rows = base + [
            ("배당수익률", _f(f.dividend_yield, "{:.2%}")),
            ("Payout ratio", _f(f.payout_ratio, "{:.0%}"),
             "85% 초과 시 커버리지 취약"),
            ("FCF 수익률", _f(f.fcf_yield, "{:.2%}"), "배당 지속성의 실질 근거"),
            ("PER", _f(f.trailing_pe, "{:.1f}x")),
            ("ROE", _f(f.roe, "{:+.1%}")),
            ("부채비율", _f(f.debt_to_equity, "{:.0f}%")),
        ]
        cov = ""
        if np.isfinite(f.fcf_yield) and np.isfinite(f.dividend_yield):
            c = f.fcf_yield / max(f.dividend_yield, 1e-9)
            cov = (f"FCF가 배당의 {c:.1f}배" if c >= 1 else
                   f"⚠ FCF가 배당의 {c:.1f}배 — 커버리지 부족")
        extra = note(f"<b>배당 커버리지</b>: {cov or '산출 불가'}. 인컴주는 금리 "
                     f"상승 시 채권 대체재로서의 상대 매력이 떨어져 디레이팅됩니다. "
                     f"아래 금리 델타를 함께 보십시오.",
                     "bad" if "⚠" in cov else "info")
    elif arch == "DISTRESSED":
        rows = base + [
            ("부채비율", _f(f.debt_to_equity, "{:.0f}%")),
            ("유동비율", _f(f.current_ratio, "{:.2f}"), "1.0 미만이면 경보"),
            ("순현금 / 시총", _f(f.net_cash_ratio, "{:+.1%}")),
            ("영업이익률", _f(f.operating_margin, "{:+.1%}")),
            ("FCF", f"${f.fcf/1e6:,.0f}M" if np.isfinite(f.fcf) else "—"),
            ("P/B", _f(f.price_to_book, "{:.2f}x")),
        ]
        extra = note("부실기업의 에쿼티는 사실상 자산에 대한 콜옵션입니다. "
                     "정규분포 기반 통계보다 <b>자본구조와 만기 스케줄</b>이 "
                     "주가를 지배하며, 유상증자 희석 리스크가 상시 존재합니다.", "bad")
    else:
        rows = base + [
            ("PER (trailing / forward)",
             f"{_f(f.trailing_pe,'{:.1f}x')} / {_f(f.forward_pe,'{:.1f}x')}"),
            ("P/B", _f(f.price_to_book, "{:.2f}x")),
            ("EV/EBITDA", _f(f.ev_to_ebitda, "{:.1f}x")),
            ("ROE / ROA", f"{_f(f.roe,'{:+.1%}')} / {_f(f.roa,'{:+.1%}')}"),
            ("순이익률 / 영업이익률",
             f"{_f(f.profit_margin,'{:+.1%}')} / {_f(f.operating_margin,'{:+.1%}')}"),
            ("매출성장 / 이익성장",
             f"{_f(f.revenue_growth,'{:+.1%}')} / {_f(f.earnings_growth,'{:+.1%}')}"),
            ("FCF 수익률", _f(f.fcf_yield, "{:.2%}")),
            ("부채비율", _f(f.debt_to_equity, "{:.0f}%")),
            ("배당수익률", _f(f.dividend_yield, "{:.2%}")),
        ]
        extra = ""
    rows += [("공매도 잔고 (float 대비)", _f(f.short_pct_float, "{:.1%}"),
              "15% 초과 시 스퀴즈/크라우딩 리스크"),
             ("기관 보유 비중", _f(f.inst_ownership, "{:.0%}"))]
    return kv_table([r if len(r) == 3 else r + ("",) for r in rows]) + extra + \
        note(f"⚠ {E(f.pit_warning)}", "warn")


r_fundamentals.applies = lambda a: _eq_section(a, "fundamentals")
REGISTRY.append(Section("fundamentals", "펀더멘털 — 아키타입별 지표 선택", "▤",
                        r_fundamentals.applies, r_fundamentals, 12, True,
                        tag="주식 전용"))


# ---------------------------------------------------------------- 6. 점프

def r_jump(a) -> str:
    j = a.equity.jumps
    out = [f'<div class="gauges">'
           f'{ch.gauge(j.jump_share_of_var, 0, 0.6, "점프의 분산 기여", good_high=False, threshold=0.25)}'
           f'{ch.gauge(j.jump_rate_ann, 0, 30, "연간 점프 횟수", fmt="{:.1f}", good_high=False)}'
           f'{ch.gauge(j.continuous_vol_ann, 0, max(j.continuous_vol_ann*1.5,0.3), "점프 제거 후 변동성", good_high=False)}'
           f'</div>']
    out.append(kv_table([
        ("탐지 점프 수 (|r| > 4σₜ)", f"{j.n_jumps}회"),
        ("연간 점프 빈도", _f(j.jump_rate_ann, "{:.1f}회")),
        ("점프의 분산 기여", f"<b>{_f(j.jump_share_of_var,'{:.0%}')}</b>"),
        ("최대 상승 점프", _f(j.largest_up, "{:+.1%}")),
        ("최대 하락 점프", _f(j.largest_down, "{:+.1%}")),
        ("점프 비대칭 (하락 우위)", _f(j.jump_asymmetry, "{:+.2f}")),
        ("점프 제거 후 연변동성", _f(j.continuous_vol_ann)),
    ]))
    out.append(note(E(j.note),
                    "bad" if j.jump_share_of_var > 0.25 else
                    "warn" if j.jump_share_of_var > 0.12 else "info"))
    if j.jump_share_of_var > 0.25:
        out.append(note("이 종목은 <b>점프가 수익률을 지배</b>합니다. 샤프비율·"
                        "정규 VaR·GBM 시뮬레이션은 구조적으로 부적합하며, "
                        "포지션 사이징이 방향 예측보다 훨씬 중요합니다.", "bad"))
    return "".join(out)


r_jump.applies = lambda a: _eq_section(a, "jump") or _eq_section(a, "tail")
REGISTRY.append(Section("jump", "점프 · 꼬리 구조", "⚡", r_jump.applies,
                        r_jump, 14, True, tag="이벤트형"))


# ---------------------------------------------------------------- 7. 스타일

def r_style(a) -> str:
    st = a.equity.style
    name = {"mkt_excess": "시장", "smb": "소형(SMB)", "hml": "가치(HML)",
            "rmw": "수익성(RMW)", "cma": "보수적투자(CMA)", "umd": "모멘텀(UMD)"}
    rows = [(name.get(k, k), f"{v:+.3f}",
             f"t = {st.tstats.get(k, float('nan')):+.2f}")
            for k, v in sorted(st.loadings.items(), key=lambda kv: -abs(kv[1]))]
    out = [kv_table(rows)]
    out.append(kv_table([
        ("모델 R²", _f(st.r2, "{:.1%}")),
        ("지배 스타일", E(st.dominant)),
        ("고유변동성 (연)", _f(st.idio_vol_ann)),
        ("고유위험 비중", f"<b>{_f(st.idio_share,'{:.0%}')}</b>",
         "70% 초과면 팩터로 설명 불가 — 종목 고유 리스크가 지배"),
        ("12-1 원시 모멘텀", _f(st.raw_momentum_12_1, "{:+.1%}")),
        ("12-1 잔차 모멘텀", _f(st.residual_momentum_12_1, "{:+.1%}"),
         "Blitz-Huij-Martens — 팩터 노이즈 제거"),
    ]))
    out.append(ch.stacked_bar(["팩터 설명분", "고유(특이) 분"],
                              [max(1 - st.idio_share, 0), max(st.idio_share, 0)],
                              "분산 분해"))
    if np.isfinite(st.raw_momentum_12_1) and np.isfinite(st.residual_momentum_12_1):
        gap = st.residual_momentum_12_1 - st.raw_momentum_12_1
        if abs(gap) > 0.15:
            out.append(note(f"원시 모멘텀({st.raw_momentum_12_1:+.1%})과 잔차 "
                            f"모멘텀({st.residual_momentum_12_1:+.1%})의 괴리가 "
                            f"{gap:+.1%}입니다. 최근 성과의 상당 부분이 "
                            f"<b>종목 고유 요인이 아니라 팩터 노출</b>에서 왔거나, "
                            f"그 반대입니다.", "warn"))
    return "".join(out)


r_style.applies = lambda a: _eq_section(a, "style") or _eq_section(a, "idio")
REGISTRY.append(Section("style", "스타일 로딩 · 고유위험", "◐",
                        r_style.applies, r_style, 16, False, tag="주식 전용"))


# ---------------------------------------------------------------- 8. 피어

def r_peer(a) -> str:
    p = a.equity.peer
    return kv_table([
        ("벤치마크", E(p.benchmark)),
        ("상대수익 1년", _f(p.rel_return_1y, "{:+.1%}")),
        ("상대수익 3개월", _f(p.rel_return_3m, "{:+.1%}")),
        ("베타", _f(p.beta_to_bench, "{:.2f}")),
        ("상관계수", _f(p.corr_to_bench, "{:.2f}")),
        ("트래킹에러 (연)", _f(p.tracking_error)),
        ("정보비율", _f(p.information_ratio, "{:.2f}")),
        ("상대강도 백분위", _f(p.rel_strength_percentile, "{:.0%}"),
         "자기 이력 대비 현재 위치"),
    ])


r_peer.applies = lambda a: _eq_section(a, "peer")
REGISTRY.append(Section("peer", "벤치마크 상대 성과", "⇄", r_peer.applies,
                        r_peer, 18, False, tag="주식 전용"))


# ---------------------------------------------------------------- 8b. 런웨이

def r_runway(a) -> str:
    f = a.equity.fundamentals
    fcf, cash, mc, debt = f.fcf, np.nan, f.market_cap, np.nan
    # net_cash_ratio 로부터 순현금 역산
    if np.isfinite(f.net_cash_ratio) and np.isfinite(mc):
        netcash = f.net_cash_ratio * mc
    else:
        netcash = np.nan
    burn = -fcf if (np.isfinite(fcf) and fcf < 0) else np.nan
    runway = (netcash / burn) if np.isfinite(netcash) and np.isfinite(burn) \
        and burn > 0 else np.nan
    dil = (burn / mc) if np.isfinite(burn) and np.isfinite(mc) and mc > 0 else np.nan

    rows = [
        ("연간 현금소진 (−FCF)",
         f"${burn/1e6:,.0f}M" if np.isfinite(burn) else "흑자 — 해당 없음"),
        ("순현금", f"${netcash/1e6:,.0f}M" if np.isfinite(netcash) else "—"),
        ("순현금 / 시총", _f(f.net_cash_ratio, "{:+.1%}")),
        ("현금 잔여기간 (runway)",
         f"<b>{runway:.1f}년</b>" if np.isfinite(runway) else "—",
         "2년 미만이면 자금조달 압력"),
        ("연간 희석 압력 (소진/시총)", _f(dil, "{:.1%}"),
         "증자로 메울 경우의 최소 희석률"),
        ("매출성장", _f(f.revenue_growth, "{:+.1%}")),
        ("영업이익률", _f(f.operating_margin, "{:+.1%}")),
        ("Rule of 40", _f(f.rule_of_40, "{:.0f}")),
        ("유동비율", _f(f.current_ratio, "{:.2f}")),
    ]
    out = [kv_table([r if len(r) == 3 else r + ("",) for r in rows])]
    if np.isfinite(runway):
        if runway < 1.5:
            out.append(note(f"⚠ 현금 잔여기간 <b>{runway:.1f}년</b>. 향후 12개월 내 "
                            f"자금조달 가능성이 높고, 조달 조건이 주가에 직접 "
                            f"반영됩니다. 이 구간에서는 밸류에이션보다 "
                            f"<b>조달 창구 접근성</b>이 주가를 지배합니다.", "bad"))
        elif runway < 3:
            out.append(note(f"현금 잔여기간 {runway:.1f}년 — 자금조달 시계가 "
                            f"분석 구간 안에 들어옵니다. 금리·리스크선호 변화에 "
                            f"이중으로 민감합니다.", "warn"))
        else:
            out.append(note(f"현금 잔여기간 {runway:.1f}년 — 단기 조달 압력은 낮음."))
    out.append(note("적자 기업의 에쿼티 가치는 <b>미래 현금흐름의 현재가치</b>이므로 "
                    "듀레이션이 길고, 금리 상승 시 멀티플 축소 폭이 가장 큽니다. "
                    "아래 금리 델타 섹션을 반드시 함께 보십시오.", "warn"))
    return "".join(out)


r_runway.applies = lambda a: _eq_section(a, "runway")
REGISTRY.append(Section("runway", "현금소진 · 런웨이 · 희석", "⛽",
                        r_runway.applies, r_runway, 13, True, tag="적자/성장 전용"))


# ---------------------------------------------------------------- 8c. 크라우딩

def r_crowding(a) -> str:
    f = a.equity.fundamentals
    st = a.equity.style
    sp = f.short_pct_float
    inst = f.inst_ownership
    idio = st.idio_share if st else np.nan

    score = 0.0
    ev = []
    if np.isfinite(sp):
        if sp > 0.20:
            score += 2.0; ev.append(f"공매도 잔고 {sp:.0%} of float — 극단")
        elif sp > 0.10:
            score += 1.0; ev.append(f"공매도 잔고 {sp:.0%} — 높음")
    if np.isfinite(inst) and inst > 0.85:
        score += 1.0; ev.append(f"기관 보유 {inst:.0%} — 동시 청산 리스크")
    if np.isfinite(idio) and idio > 0.75:
        score += 0.8; ev.append(f"고유위험 비중 {idio:.0%} — 팩터 헤지 불가")
    liq = a.liquidity
    if np.isfinite(liq.days_to_liquidate_1pct_aum) and \
            liq.days_to_liquidate_1pct_aum > 3:
        score += 1.0
        ev.append(f"1% AUM 청산에 {liq.days_to_liquidate_1pct_aum:.1f}일")

    lvl = ("높음" if score >= 2.5 else "중간" if score >= 1.2 else "낮음")
    kind = "bad" if score >= 2.5 else "warn" if score >= 1.2 else "info"
    out = [f'<div class="hi">크라우딩·포지셔닝 리스크: <b>{lvl}</b> '
           f'(점수 {score:.1f})</div>',
           kv_table([
               ("공매도 잔고 (float 대비)", _f(sp, "{:.1%}"),
                "20% 초과 시 스퀴즈 리스크"),
               ("기관 보유 비중", _f(inst, "{:.0%}"), "동시 청산 취약성"),
               ("고유위험 비중", _f(idio, "{:.0%}"), "팩터 헤지 불가 정도"),
               ("1% AUM 청산 소요",
                _f(liq.days_to_liquidate_1pct_aum, "{:.1f} 일")),
               ("EDGE 스프레드", _f(liq.spread_bps, "{:.1f} bp")),
           ])]
    if ev:
        out.append("<ul class='rl'>" + "".join(f"<li>{E(x)}</li>" for x in ev) +
                   "</ul>")
    out.append(note("크라우딩은 평상시엔 보이지 않다가 청산 국면에서만 드러납니다. "
                    "공매도 잔고가 높으면 상방 스퀴즈와 하방 가속이 <b>동시에</b> "
                    "커지며, 이는 변동성이 아니라 <b>왜도</b>의 문제입니다. "
                    "포지션 사이징으로만 관리 가능합니다.", kind))
    return "".join(out)


r_crowding.applies = lambda a: _eq_section(a, "crowding")
REGISTRY.append(Section("crowding", "크라우딩 · 포지셔닝", "⧉",
                        r_crowding.applies, r_crowding, 15, False,
                        tag="투기/이벤트형"))


# ---------------------------------------------------------------- 9. 금리 민감도

def r_rate(a) -> str:
    dp = a.delta_panel
    rate_f = [f for f in ("real_yield_10y", "nominal_10y", "curve_2s10s",
                          "breakeven_10y")
              if len(dp) and f in dp["factor"].values]
    sub = dp[dp["factor"].isin(rate_f)] if rate_f else pd.DataFrame()
    out = [note("현금흐름 듀레이션이 긴 자산(고성장 적자주, 인컴주, 리츠, 장기채)은 "
                "금리 민감도가 실질적으로 <b>가장 큰 단일 리스크</b>입니다.")]
    if len(sub):
        out.append(ch.delta_bars(sub["shock_label"], sub["delta_pct"],
                                 sub["delta_pct_downside"], "금리 델타"))
        out.append(df_table(sub, ["shock_label", "beta_now", "downside_beta",
                                  "delta_pct", "delta_pct_downside", "t_stat",
                                  "r2_now"],
                            {"shock_label": "충격", "beta_now": "β(현재)",
                             "downside_beta": "하방β", "delta_pct": "Δ",
                             "delta_pct_downside": "Δ(하방)",
                             "t_stat": "t", "r2_now": "R²"}, nd=4))
    else:
        out.append('<p class="muted">금리 팩터가 이 자산의 후보군에 없거나 '
                   '데이터가 부족합니다.</p>')
    return "".join(out)


r_rate.applies = lambda a: _eq_section(a, "rate_sensitivity") or \
    a.classification.asset_class in ("BOND_GOV", "BOND_CREDIT", "BOND_HY",
                                     "BOND_TIPS", "REIT")
REGISTRY.append(Section("rate", "금리 민감도 심층", "％", r_rate.applies,
                        r_rate, 20, True))


# ---------------------------------------------------------------- 10. 듀레이션

def r_duration(a) -> str:
    dp = a.delta_panel
    row = dp[dp["factor"] == "nominal_10y"] if len(dp) else pd.DataFrame()
    if not len(row):
        row = dp[dp["factor"] == "real_yield_10y"] if len(dp) else pd.DataFrame()
    if not len(row):
        return '<p class="muted">금리 팩터 부재로 듀레이션 추정 불가.</p>'
    b = float(row.iloc[0]["beta_now"])
    dur = -b * 100.0            # β는 1%p 변화당 수익률 → 실효 듀레이션 근사
    dv01 = dur / 10000.0
    return (kv_table([
        ("금리 β (1%p 당)", f"{b:+.4f}"),
        ("실효 듀레이션 (근사)", f"{dur:.2f} 년",
         "가격 = −듀레이션 × Δ금리 관계에서 역산"),
        ("DV01 (1bp 당)", f"{dv01*100:.4f}%"),
        ("+100bp 시 손익", _f(-dur / 100, "{:+.1%}")),
        ("+200bp 시 손익 (볼록성 무시)", _f(-2 * dur / 100, "{:+.1%}")),
    ]) + note("이 듀레이션은 가격-금리 회귀에서 역산한 <b>실효(empirical) 듀레이션</b>"
              "이며, 현금흐름 기반 수정듀레이션과 다릅니다. 볼록성과 스프레드 "
              "듀레이션은 별도이며, 크레딧물의 경우 금리보다 OAS가 지배할 수 "
              "있습니다.", "warn"))


r_duration.applies = lambda a: a.classification.asset_class in (
    "BOND_GOV", "BOND_CREDIT", "BOND_HY", "BOND_TIPS")
REGISTRY.append(Section("duration", "듀레이션 · DV01", "⏱", r_duration.applies,
                        r_duration, 21, True, tag="채권 전용"))


# ---------------------------------------------------------------- 11. 드래그

def r_drag(a) -> str:
    fp = a.classification.fingerprint
    L = fp.leverage_detected or 1.0
    sig = a.vol_profile.garch.ann_vol_current
    base_sig = sig / abs(L) if abs(L) > 0 else sig
    drag = 0.5 * L * (L - 1) * base_sig ** 2
    s = a.sim
    return (note(f"레버리지 <b>{L:+.0f}x</b> ETP는 <b>일간 리밸런싱</b>되므로 "
                 f"경로의존적입니다. 기초자산이 제자리로 돌아와도 손실이 남습니다.",
                 "bad") +
            kv_table([
                ("탐지 레버리지", f"{L:+.0f}x"),
                ("ETP 연변동성", _f(sig)),
                ("기초자산 추정 변동성", _f(base_sig)),
                ("이론적 변동성 드래그 (연)", f"<b>{_f(-drag,'{:+.1%}')}</b>",
                 "½·L(L−1)·σ² — 기초자산이 횡보해도 발생"),
                ("원본식 GBM 상승확률", _f(s.prob_up_naive_gbm, "{:.1%}"),
                 "ETP 수익률에 직접 GBM 적용 (틀림)"),
                ("경로 재구성 상승확률", f"<b>{_f(s.prob_up,'{:.1%}')}</b>",
                 "기초자산 경로 → 일간 리밸런싱 재구성 (올바름)"),
                ("시뮬 경로 최대낙폭 중앙값", _f(s.max_dd_median)),
                ("P(경로 중 −20% 낙폭)", _f(s.prob_dd_20, "{:.1%}")),
            ]) +
            note("두 상승확률의 차이가 곧 변동성 드래그입니다. ETF 수익률에 직접 "
                 "GBM을 돌리면 이 손실이 통째로 사라집니다. 장기 보유를 전제한 "
                 "분석 자체가 이 자산군에는 부적절합니다.", "warn"))


r_drag.applies = lambda a: (a.classification.asset_class in
                            ("LEVERAGED", "VOL_ETP") or
                            a.classification.fingerprint.leverage_detected
                            is not None)
REGISTRY.append(Section("drag", "변동성 드래그 분해", "↯", r_drag.applies,
                        r_drag, 11, True, tag="레버리지 전용"))


# ---------------------------------------------------------------- 12. 유동성

def r_liquidity(a) -> str:
    l = a.liquidity
    out = [note("일봉 VPIN/CVD 프록시는 체결방향을 모르므로 정보 함량이 사실상 "
                "0입니다. EDGE(JFE 2024)는 OHLC 전부를 최적 결합해 거래가 희소해도 "
                "편향되지 않습니다.")]
    out.append(kv_table([
        ("EDGE 유효스프레드", f"<b>{_f(l.spread_bps,'{:.1f}')} bp</b>"),
        ("교차검증 (CS / CHL / Roll)",
         f"{_f(l.spread_cs*1e4,'{:.0f}')} / {_f(l.spread_chl*1e4,'{:.0f}')} / "
         f"{_f(l.spread_roll*1e4,'{:.0f}')} bp"),
        ("추정량 간 산포", f"{_f(l.spread_dispersion*1e4,'{:.0f}')} bp",
         "측정 불확실성"),
        ("Amihud 비유동성", _f(l.amihud, "{:.4f}")),
        ("무거래일 비율", _f(l.zero_ret_ratio, "{:.1%}")),
        ("ADV (중앙값)",
         f"${l.adv_usd:,.0f}" if np.isfinite(l.adv_usd) else "—"),
        ("1% AUM 청산 소요", _f(l.days_to_liquidate_1pct_aum, "{:.1f} 일")),
        ("거래 가능 판정", "가능" if l.tradable else f"<b>불가</b> — {E(l.reason)}"),
    ]))
    out.append(df_table(a.capacity,
                        rename={"aum_usd": "AUM($)", "participation": "참여율",
                                "roundtrip_cost": "왕복비용",
                                "net_alpha_ann": "순알파(연)"}, nd=4))
    out.append(note("임팩트는 제곱근 법칙 G ≈ Y·σ_d·√(Q/ADV) 기준. 선형 모델은 "
                    "대형 주문 비용을 극심하게 과소평가합니다."))
    return "".join(out)


r_liquidity.applies = lambda a: True
REGISTRY.append(Section("liquidity", "유동성 · 거래비용 · 용량", "◎",
                        r_liquidity.applies, r_liquidity, 30, False))


# ---------------------------------------------------------------- 13. 성과

def r_perf(a) -> str:
    p = a.perf
    sr = p.get("sharpe")
    out = []
    if sr and np.isfinite(sr) and abs(sr) > 1e-6:
        yrs = (3.0 / abs(sr)) ** 2
        have = p.get("n_obs", 0) / 252
        t = abs(sr) * math.sqrt(have)
        out.append(note(f"샤프 {sr:.2f}를 Harvey-Liu-Zhu 허들(t &gt; 3.0)로 "
                        f"입증하려면 <b>{yrs:.1f}년</b>이 필요합니다. "
                        f"현 표본은 {have:.1f}년 → t ≈ {t:.2f}.",
                        "warn" if t < 3 else "info"))
    out.append(kv_table([
        ("CAGR", _f(p.get("cagr"))), ("연변동성", _f(p.get("vol_ann"))),
        ("샤프", _f(p.get("sharpe"), "{:.2f}")),
        ("소르티노", _f(p.get("sortino"), "{:.2f}")),
        ("칼마", _f(p.get("calmar"), "{:.2f}")),
        ("최대낙폭", _f(p.get("max_drawdown"))),
        ("CDaR 95%", _f(p.get("cdar_95"))),
        ("Ulcer", _f(p.get("ulcer"), "{:.1f}")),
        ("최장 수중기간", f"{p.get('longest_underwater_days','—')} 일"),
        ("승률", _f(p.get("hit_rate"), "{:.1%}")),
        ("왜도 / 첨도",
         f"{_f(p.get('skew'),'{:.2f}')} / {_f(p.get('kurtosis'),'{:.1f}')}"),
    ]))
    out.append(note(E(a.drawdown.recovery_note)))
    return "".join(out)


r_perf.applies = lambda a: True
REGISTRY.append(Section("perf", "성과 · 낙폭", "▦", r_perf.applies, r_perf, 32))


# ---------------------------------------------------------------- 14. 변동성

def r_vol(a) -> str:
    vp, g = a.vol_profile, a.vol_profile.garch
    out = [kv_table([
        ("GJR-GARCH(1,1)-t 현재", _f(g.ann_vol_current)),
        ("장기 평균", _f(g.ann_vol_longrun),
         "⚠ IGARCH 근방(지속성≈1)" if g.at_boundary else ""),
        ("최근 21일 실현", _f(vp.realized_21d_ann)),
        ("HAR 예측 (Corsi 2009)", _f(vp.har.forecast_ann),
         f"R²={_f(vp.har.r2,'{:.2f}')}"),
        ("지속성 α+γ/2+β", _f(g.persistence, "{:.3f}"),
         f"반감기 {_f(g.halflife_days,'{:.0f}')}일"),
        ("레버리지 효과 γ", _f(g.gamma, "{:.3f}"),
         "하락 충격이 변동성을 더 키우는 정도"),
        ("t 자유도 ν", _f(g.nu, "{:.1f}"), "작을수록 팻테일"),
        ("변동성의 변동성", _f(vp.vol_of_vol)),
        ("현재 변동성 백분위", _f(vp.vol_percentile, "{:.0%}")),
    ])]
    if len(g.sigma) > 260:
        s = np.asarray(g.sigma[-756:], float) * math.sqrt(
            a.classification.spec.ann_factor)
        out.append(ch.line_chart({"조건부 변동성": s}, "GARCH 조건부 변동성 (최근 3년)",
                                 hline=float(g.ann_vol_longrun)))
    us = vp.unsmoothing
    if us.get("applied"):
        out.append(note(f"⚠ <b>평활화 보정</b>: 언스무딩 시 변동성이 "
                        f"{us['vol_inflation']:.2f}배 확대됩니다. 보고된 샤프는 "
                        f"그만큼 과대평가입니다.", "warn"))
    return "".join(out)


r_vol.applies = lambda a: True
REGISTRY.append(Section("vol", "조건부 변동성", "∿", r_vol.applies, r_vol, 34))


# ---------------------------------------------------------------- 15. 레짐

def r_regime(a) -> str:
    rg = a.regime
    cols = [c for c in ["label", "share", "mean_ann", "vol_ann", "sharpe",
                        "hit_rate", "worst_day"] if c in rg.stats.columns]
    cols += [c for c in rg.stats.columns if c.startswith("macro_")]
    out = [note("K-means 라벨 0·1·2는 강약 순서를 뜻하지 않아 경제적 해석이 "
                "불가능합니다. Statistical Jump Model은 전환마다 점프 페널티를 "
                "부과해 지속성을 강제하고, 각 국면에 <b>경제적 이름</b>을 붙입니다.")]
    if a.prices is not None and a.regime is not None:
        st = rg.states
        pxv = np.asarray(a.prices, float)[-len(st):]
        out.append(ch.regime_timeline(st, pxv, rg.labels))
    out.append(df_table(rg.stats, cols,
                        {"label": "국면", "share": "비중", "mean_ann": "연수익",
                         "vol_ann": "연변동성", "sharpe": "샤프",
                         "hit_rate": "승률", "worst_day": "최악일"}))
    out.append(f'<div class="hi">현재 국면: <b>{E(rg.labels[rg.current_state])}</b> '
               f'(확률 {max(rg.current_probs.values()):.0%}, 기대 지속 '
               f'{rg.expected_duration[rg.current_state]:.0f}영업일)</div>')
    out.append(f'<p class="muted">점프 페널티 λ={rg.jump_penalty:.0f} · '
               f'관측 구간 전환 {rg.n_switches}회</p>')
    return "".join(out)


r_regime.applies = lambda a: a.regime is not None
REGISTRY.append(Section("regime", "시장 국면", "◫", r_regime.applies, r_regime, 36))


# ---------------------------------------------------------------- 16. 팩터

def r_factor(a) -> str:
    fm = a.factor_model
    if fm is None:
        return '<p class="muted">팩터 데이터 없음</p>'
    rows = [(k, f"{c:+.4f}", f"t = {fm.tstats.get(k, float('nan')):+.2f}")
            for k, c in sorted(fm.coefs.items(), key=lambda kv: -abs(kv[1]))]
    out = [f'<p class="muted">선택 팩터: <code>{E(", ".join(fm.used_factors))}</code> '
           f'(Elastic-Net 선택 → OLS/Newey-West 재추정)</p>',
           kv_table(rows),
           kv_table([
               ("R²", f"<b>{_f(fm.r2,'{:.1%}')}</b>"),
               ("자산군 기대밴드", f"{fm.r2_band[0]:.0%} ~ {fm.r2_band[1]:.0%}"),
               ("연환산 알파", _f(fm.alpha_ann),
                f"t = {_f(fm.alpha_t,'{:.2f}')}"),
               ("알파 해석 허용",
                "예" if fm.interpretation_allowed else "<b>아니오</b>"),
           ])]
    out.append(note(E(fm.mismatch_note), "bad" if fm.mismatch else "info"))
    if fm.mismatch:
        out.append(note("이것이 원본 리포트의 핵심 오류 지점입니다. GLD에 주식형 FF "
                        "회귀를 돌려 R²=2%가 나왔고 잔차 98%를 '종목 고유위험'으로 "
                        "해석했습니다. 실제로는 <b>누락 변수</b>이며, R²가 2%인 "
                        "회귀의 알파는 해석 불가능한 잔차 평균입니다.", "bad"))
    return "".join(out)


r_factor.applies = lambda a: a.factor_model is not None
REGISTRY.append(Section("factor", "팩터 모델 적합성", "⊞", r_factor.applies,
                        r_factor, 38))


# ---------------------------------------------------------------- 17. 델타

def r_delta(a) -> str:
    d = a.delta_panel
    if not len(d):
        return '<p class="muted">델타 패널 산출 불가</p>'
    out = [note("'샤프 0.96' 같은 요약통계가 아니라 <b>무엇이 X만큼 움직이면 "
                "얼마를 잃는가</b>. 정적 베타가 아니라 시변 베타를 쓰고 하방 분위 "
                "베타를 나란히 표시합니다."),
           ch.delta_bars(d["shock_label"], d["delta_pct"],
                         d["delta_pct_downside"], "표준 충격당 손익")]
    v = d.copy()
    v["상관(전체→하방)"] = v.apply(
        lambda r: f"{r['corr_all']:+.2f} → {r['corr_lower_tail']:+.2f}", axis=1)
    out.append(df_table(v, ["shock_label", "beta_now", "beta_stability_cv",
                            "downside_beta", "delta_pct", "delta_pct_downside",
                            "t_stat", "상관(전체→하방)", "lambda_lower",
                            "r2_now", "note"],
                        {"shock_label": "충격", "beta_now": "β(현재)",
                         "beta_stability_cv": "β안정성CV",
                         "downside_beta": "하방β", "delta_pct": "Δ",
                         "delta_pct_downside": "Δ(하방)", "t_stat": "t",
                         "lambda_lower": "λ_L", "r2_now": "R²", "note": "경보"},
                        nd=3))
    out.append('<ul class="rl">'
               '<li><b>Δ(하방)</b>이 <b>Δ</b>보다 크면 하방에서 노출이 확대되는 '
               '비대칭 자산입니다. 정적 베타 스트레스는 이걸 놓칩니다.</li>'
               '<li><b>상관(전체→하방)</b>: 정상 국면과 극단 국면 상관은 다른 '
               '숫자입니다. 분산 효과가 위기에 사라지는지 여기서 보입니다.</li>'
               '<li><b>β안정성CV &gt; 0.8</b>이면 그 베타를 헤지 비율로 쓰면 '
               '안 됩니다.</li>'
               '<li><b>R² 붕괴</b>는 버그가 아니라 구조 변화 신호입니다. '
               '금-10년TIPS R²가 2005–2021 약 84%에서 2022년 이후 한 자릿수로 '
               '무너진 것이 대표 사례입니다.</li></ul>')
    if a.tvb:
        for k, t in list(a.tvb.items())[:3]:
            s = t.rolling.dropna()
            if len(s) > 60:
                out.append(ch.line_chart({f"β({k})": s.values[-756:]},
                                         f"시변 베타 — {k}", h=200,
                                         hline=float(t.beta_mean)))
                if t.collapse_note:
                    out.append(note(E(t.collapse_note), "bad"))
    return "".join(out)


r_delta.applies = lambda a: len(a.delta_panel) > 0
REGISTRY.append(Section("delta", "델타 패널 — 헤지펀드 민감도", "Δ",
                        r_delta.applies, r_delta, 40, True))


# ---------------------------------------------------------------- 18. ML

def r_ml(a) -> str:
    m = a.ml
    badge = ("<span class='pill up'>SIGNAL</span>" if m.verdict == "SIGNAL"
             else "<span class='pill down'>ABSTAIN</span>")
    out = [f'<div class="hi">판정 {badge} · 승자 모델 <code>{E(m.model_name)}</code></div>',
           f'<div class="gauges">'
           f'{ch.gauge(m.resolution, 0, max(m.resolution*2, 0.02), "Murphy Resolution", fmt="{:.5f}", threshold=1e-4)}'
           f'{ch.gauge(m.overfit_gap, 0, 0.6, "과적합 갭", good_high=False, threshold=0.15)}'
           f'{ch.gauge(m.strategy_dsr, 0, 1, "전략 DSR", threshold=0.90)}'
           f'</div>',
           kv_table([
               ("OOS 정확도", _f(m.oos_accuracy, "{:.1%}"),
                f"기저율 {_f(m.base_rate,'{:.1%}')}"),
               ("In-sample 정확도", _f(m.in_sample_accuracy, "{:.1%}")),
               ("과적합 갭", f"<b>{_f(m.overfit_gap,'{:+.1%}')}</b>", "< 15%p"),
               ("Brier score", _f(m.brier, "{:.4f}")),
               ("Murphy Resolution", f"<b>{_f(m.resolution,'{:.5f}')}</b>", "> 0"),
               ("Brier skill", _f(m.brier_skill, "{:+.4f}"), "> 0"),
               ("PBO", _f(m.pbo, "{:.1%}"), "소프트 50% / 하드 75%"),
               ("전략 샤프", _f(m.strategy_sharpe, "{:.2f}")),
               ("전략 DSR", f"<b>{_f(m.strategy_dsr,'{:.1%}')}</b>", "> 90%"),
               ("유효 라벨 / 시행횟수", f"{m.n_labeled} / {m.n_trials_used}"),
           ]),
           note("<b>Murphy 분해</b>: Brier = Reliability − Resolution + "
                "Uncertainty. Resolution ≈ 0이면 모델이 기저율 이상의 정보를 "
                "담고 있지 않다는 <b>정량적 증명</b>입니다.")]
    if m.reasons:
        out.append("<ul class='rl'>" +
                   "".join(f"<li>{E(x)}</li>" for x in m.reasons) + "</ul>")
    if m.verdict == "SIGNAL":
        out.append(f'<div class="hi">보정 상승확률 <b>{m.prob_up_now:.1%}</b> '
                   f'(CI [{m.prob_ci[0]:.1%}, {m.prob_ci[1]:.1%}])</div>')
        if len(m.reliability_tbl):
            out.append(ch.reliability_diagram(m.reliability_tbl))
    else:
        out.append(note("게이트를 통과하지 못했으므로 <b>확률을 출력하지 "
                        "않습니다.</b> 감점된 점수를 내는 것은 없는 정보를 있는 "
                        "것처럼 만드는 일입니다.", "warn"))
    if len(m.feature_importance):
        out.append(f'<p class="muted">상위 피처: '
                   f'{E(", ".join(m.feature_importance.head(8).index))}</p>')
    return "".join(out)


r_ml.applies = lambda a: a.ml is not None
REGISTRY.append(Section("ml", "방향 예측 — 핵심은 기권", "◑", r_ml.applies,
                        r_ml, 42, True))


# ---------------------------------------------------------------- 19. 시뮬

def r_sim(a) -> str:
    s = a.sim
    s0 = float(s.median_price / (1 + 0)) if False else None
    out = [f'<p class="muted">엔진: <code>{E(s.engine)}</code> · '
           f'{s.n_sims:,}회</p>',
           note("<b>드리프트가 왜 문제인가</b> — 표본 드리프트의 표준오차는 "
                "σ/√T이며, 일봉 표본에서 거의 항상 추정치 자체만큼 큽니다."),
           kv_table([
               ("표본 드리프트 μ̂", _f(s.drift.mu_hat_ann, "{:+.1%}")),
               ("표준오차 SE(μ̂)=σ/√T", _f(s.drift.se_ann, "{:.1%}")),
               ("95% 신뢰구간",
                f"[{_f(s.drift.ci95[0],'{:+.1%}')}, {_f(s.drift.ci95[1],'{:+.1%}')}]"),
               ("사후 드리프트", _f(s.drift.mu_post_ann, "{:+.1%}"),
                f"축소 {s.drift.shrink:.0%}"),
           ]),
           note(E(s.drift.note),
                "bad" if s.drift.se_ann >= abs(s.drift.mu_hat_ann) else "info"),
           kv_table([
               ("드리프트 고정 + 정규 (원본 방식)",
                _f(s.prob_up_naive_gbm, "{:.1%}")),
               ("FHS-GARCH + EVT + 파라미터 불확실성",
                f"<b>{_f(s.prob_up,'{:.1%}')}</b>"),
           ])]
    dcm = s.uncertainty_decomposition
    out.append(ch.stacked_bar(["시장 변동성", "파라미터 무지"],
                              [max(dcm.get("market_share", 0), 0),
                               max(dcm.get("param_share", 0), 0)],
                              "예측 불확실성의 출처"))
    if len(s.log_paths_quantiles):
        s0v = float(s.log_paths_quantiles["q50"].iloc[0])
        out.append(ch.fan_chart(s.log_paths_quantiles, s0v,
                                "1년 시뮬레이션 분포 (팬차트)"))
    ov_x = ov_y = None
    lab = ""
    if a.rnd is not None:
        ov_x, ov_y, lab = a.rnd.strikes, a.rnd.density, "시장 함축 분포 (RND)"
    out.append(ch.histogram(s.terminal, "종착 가격 분포", overlay_x=ov_x,
                            overlay_y=ov_y, overlay_label=lab))
    out.append(kv_table([
        ("중앙값", f"{s.median_price:,.2f}"),
        ("90% 시뮬 구간", f"{s.q05:,.2f} ~ {s.q95:,.2f}"),
        ("VaR 95% (종착수익)", _f(s.var95_pct)),
        ("CVaR 95%", _f(s.cvar95_pct)),
        ("P(경로 중 −20% 낙폭)", _f(s.prob_dd_20, "{:.1%}")),
        ("경로 최대낙폭 중앙값", _f(s.max_dd_median)),
    ]))
    out.append(note("90% 구간은 통계적 신뢰구간이나 목표주가가 아니라 모델 가정 "
                    "하의 시뮬레이션 분위입니다. VaR은 최대손실이 아니라 하위 5% "
                    "경계값입니다.", "warn"))
    return "".join(out)


r_sim.applies = lambda a: a.sim is not None
REGISTRY.append(Section("sim", "분포 시뮬레이션", "◉", r_sim.applies, r_sim, 44, True))


# ---------------------------------------------------------------- 20. 리스크

def r_risk(a) -> str:
    vr = a.var
    rows = []
    for name, key, vv, ee in (("정규", "normal", vr.var_normal, np.nan),
                              ("히스토리컬", "historical", vr.var_historical,
                               vr.es_historical),
                              ("FHS-EVT", "fhs_evt", vr.var_fhs_evt,
                               vr.es_fhs_evt)):
        b = vr.backtest.get(key, {})
        rows.append({"모델": name, "VaR95": vv, "ES95": ee,
                     "위반율": b.get("hit_rate", np.nan),
                     "Kupiec p": b.get("kupiec_p", np.nan),
                     "독립성 p": b.get("independence_p", np.nan),
                     "조건부 p": b.get("cc_p", np.nan)})
    out = [df_table(pd.DataFrame(rows), nd=4),
           f'<div class="hi">채택 모델: <code>{E(vr.preferred)}</code></div>',
           note(E(vr.note), "warn" if "⚠" in vr.note else "info")]
    out.append("<h4>스트레스 — 자산군 고유 충격</h4>")
    out.append(note("원본 리포트는 '주식베타 0.20 × 지수충격'으로 스트레스를 "
                    "만들었습니다. 금에 주식 베타를 곱하는 것은 의미가 없습니다."))
    if len(a.stress_table):
        _cols = ["scenario", "shocks", "beta_basis", "pnl_static",
                 "pnl_downside", "pnl_conservative"]
        _cols = [c for c in _cols if c in a.stress_table.columns]
        out.append(df_table(a.stress_table, _cols,
                            {"scenario": "시나리오", "shocks": "충격",
                             "beta_basis": "베타 기준",
                             "pnl_static": "정적β", "pnl_downside": "하방β",
                             "pnl_conservative": "보수적 채택"}, nd=4))
        out.append(note(
            "복합 시나리오는 <b>부분(다변량) 베타</b>로 계산합니다. "
            "델타 패널의 단변량 베타에는 그 충격에 딸려 오는 시장 움직임이 "
            "이미 포함돼 있어서, 여러 팩터를 동시에 때리는 시나리오에서 "
            "그대로 더하면 같은 충격을 여러 번 세게 됩니다. "
            "단일 팩터 시나리오는 총효과가 맞으므로 단변량을 그대로 씁니다."))
    out.append(f'<div class="hi">최악: '
               f'{E(a.stress_summary.get("worst_scenario","—"))} → '
               f'<b>{_f(a.stress_summary.get("worst_pnl"))}</b> '
               f'(한도 {_f(a.stress_summary.get("limit"),"{:.0%}")})</div>')
    return "".join(out)


r_risk.applies = lambda a: a.var is not None
REGISTRY.append(Section("risk", "VaR/ES · 스트레스", "⛨", r_risk.applies,
                        r_risk, 46, True))


# ---------------------------------------------------------------- 21. 사이징

def r_sizing(a) -> str:
    sz = a.sizing
    if sz is None:
        return '<p class="muted">사이징 미산출</p>'
    caps = [("단순 켈리 μ/σ²", sz.kelly_naive),
            ("낙폭제약 켈리", sz.kelly_uncertainty_adjusted),
            ("변동성 타깃", sz.vol_target_weight),
            ("스트레스 예산", sz.stress_cap),
            ("유동성 한도", sz.liquidity_cap),
            ("자산군 상한", sz.class_cap)]
    body = "".join(
        f'<tr class="{"bind" if n == sz.binding_constraint else ""}">'
        f'<td class="k">{E(n)}</td><td class="v">{_f(v,"{:.0%}")}</td></tr>'
        for n, v in caps)
    return (f'<table class="kv">{body}'
            f'<tr class="tot"><td class="k">최종 비중</td>'
            f'<td class="v"><b>{_f(sz.final_weight,"{:.1%}")}</b></td></tr>'
            f'</table>' +
            f'<div class="hi">구속 제약: <b>{E(sz.binding_constraint)}</b></div>' +
            note(E(sz.note), "warn") +
            note("켈리 공식 자체가 아니라 <b>μ를 안다고 가정한 것</b>이 문제입니다. "
                 "성장 최적 켈리는 수학적으로 옳아도 운용 불가능한 낙폭을 "
                 "동반합니다. 낙폭 제약이 없으면 어떤 켈리 값도 실무 권고로 쓸 "
                 "수 없습니다."))


r_sizing.applies = lambda a: a.sizing is not None
REGISTRY.append(Section("sizing", "포지션 사이징", "⚖", r_sizing.applies,
                        r_sizing, 48, True))


# ---------------------------------------------------------------- 22. 옵션

def r_options(a) -> str:
    o = a.option_surface
    out = [kv_table([
        ("1M ATM IV", _f(o.atm_iv_1m)),
        ("기간구조 기울기 (3M−1M)", _f(o.term_slope, "{:+.1%}"),
         "백워데이션 → 단기 스트레스" if o.backwardation else "정상"),
        ("25Δ Risk Reversal", _f(o.rr25_1m, "{:+.1%}"),
         "하방 공포 프리미엄" if (o.rr25_1m or 0) > 0 else "상방 선호"),
        ("IV − RV 스프레드", f"<b>{_f(o.iv_rv_spread,'{:+.1%}')}</b>",
         "분산위험프리미엄 프록시"),
        ("Put/Call OI 비율", _f(o.put_call_oi_ratio, "{:.2f}")),
    ])]
    if len(o.tenor_days) > 1:
        out.append(ch.line_chart({"ATM IV": o.atm_iv, "25Δ RR": o.rr25},
                                 "IV 기간구조", h=210))
    out.append(note(E(o.note)))
    if a.rnd is not None and a.model_vs_market:
        mv = a.model_vs_market
        out.append("<h4>우리 모델 vs 시장 — 차이가 곧 논지</h4>")
        out.append(kv_table([
            ("모델 상승확률", _f(mv["model_prob_up"], "{:.1%}")),
            ("시장(위험중립) 상승확률", _f(mv["market_prob_up_rn"], "{:.1%}")),
            ("차이", f"<b>{_f(mv['prob_gap'],'{:+.1%}')}</b>"),
            ("모델 / 시장 중앙값",
             f"{mv['model_median']:,.2f} / {mv['market_median']:,.2f}"),
        ]))
        out.append(note(f"<b>{E(mv['verdict'])}</b> — 위험중립 확률에는 리스크 "
                        f"프리미엄이 포함돼 실세계 확률과 다릅니다. 차이의 방향과 "
                        f"크기를 논지로 삼는 것이 올바른 용법입니다."))
    return "".join(out)


r_options.applies = lambda a: a.option_surface is not None
REGISTRY.append(Section("options", "옵션 표면 · RND", "◬", r_options.applies,
                        r_options, 50))


# ---------------------------------------------------------------- 22b. 전문가 패널

def r_panel(a) -> str:
    P = a.panel
    out = [note("각 전문가는 <b>직함과 선언된 편향</b>을 먼저 밝히고, 자기 "
                "체크리스트로 심사한 뒤 소견을 냅니다. 소견문은 LLM 생성이 "
                "아니라 각자의 결정 규칙에서 도출되므로 같은 입력이면 같은 "
                "소견이 나옵니다 — 감사 가능합니다.")]
    out.append(f'<div class="hi">{E(P.summary)}</div>')
    if P.blocks:
        out.append(note("⛔ <b>거부권</b>: " +
                        "; ".join(E(b) for b in P.blocks), "bad"))

    tally = " · ".join(f"{k} {v}" for k, v in P.stance_tally.items())
    out.append(kv_table([
        ("참여 전문가", f"{len(P.experts)}명"),
        ("입장 분포", E(tally)),
        ("견해 산포(표준편차)", _f(P.dissent_ratio, "{:.3f}"),
         "0.10 초과면 통합 확률을 중립으로 축소"),
        ("반대신문", f"{len(P.challenges)}건"),
        ("미해결 쟁점", f"{len(P.open_issues)}건"),
    ]))

    for e in P.experts:
        badge = {"BULL": "up", "BEAR": "down", "BLOCK": "down"}.get(e.stance, "")
        veto = ' <span class="badge">거부권</span>' if e.veto_power else ""
        out.append(
            f'<details class="ag"><summary><b>{E(e.title)}</b> '
            f'<span class="pill {badge}">{E(e.stance)}</span> '
            f'확신도 {e.conviction:.0%}'
            + (f' · 확률 {e.prob_up:.1%}' if e.prob_up is not None
               and np.isfinite(e.prob_up) else "")
            + f'{veto}</summary><div style="padding:4px 12px 12px 12px">')
        out.append(f'<p class="muted"><b>소속</b> {E(e.desk)} · '
                   f'<b>렌즈</b> {E(e.lens)}</p>')
        out.append(note(f"<b>선언된 편향</b> — {E(e.declared_bias)}<br>"
                        f"<b>열람 범위</b> — {E(e.data_scope)}"))
        out.append(f'<p>{E(e.opinion)}</p>')
        if e.checklist:
            out.append(df_table(pd.DataFrame([{
                "심사 항목": c.item, "값": c.value, "판정": c.verdict,
                "증거 위계": c.weight} for c in e.checklist]),
                highlight=lambda r_: "bad" if r_["판정"] == "FAIL" else ""))
        out.append(f'<p class="muted"><b>생각을 바꿀 조건</b> — '
                   f'{E(e.would_change_mind)}</p>')
        out.append('</div></details>')

    if P.challenges:
        out.append("<h4>반대신문</h4>")
        out.append(note("반론의 승패는 <b>증거 위계</b>로 결정됩니다: "
                        "① 데이터 무결성 → ② 체결 가능성 → ③ 표본외 통계 → "
                        "④ 표본내 통계 → ⑤ 경제적 메커니즘 → ⑥ 서사. "
                        "상위 증거가 하위 주장을 이깁니다."))
        for c in P.challenges:
            out.append(f'<div class="hi"><b>{E(c.challenger)}</b> → '
                       f'<b>{E(c.target)}</b><br>'
                       f'<span class="muted">대상 주장</span> {E(c.challenged_claim)}<br>'
                       f'<span class="muted">반론</span> {E(c.objection)}<br>'
                       f'<span class="muted">판정</span> {E(c.resolution)}</div>')
    if P.open_issues:
        out.append(note("<b>미해결 쟁점</b> — 결정적 반박에 이르지 못해 "
                        "양측 견해를 병기합니다.<ul class='rl'>" +
                        "".join(f"<li>{E(x)}</li>" for x in P.open_issues) +
                        "</ul>", "warn"))
    if P.agreed_facts:
        out.append("<h4>합의된 사실 (상위 위계 통과 항목)</h4>")
        out.append("<ul class='rl'>" +
                   "".join(f"<li>{E(x)}</li>" for x in P.agreed_facts) + "</ul>")
    return "".join(out)


r_panel.applies = lambda a: getattr(a, "panel", None) is not None
REGISTRY.append(Section("panel", "전문가 패널 — 직능별 소견과 반대신문", "⚗",
                        r_panel.applies, r_panel, 55, True))


# ---------------------------------------------------------------- 23. 에이전트

def r_agents(a) -> str:
    out = [note("LLM 멀티에이전트 토론은 자동으로 정확도를 올리지 않습니다. 동조 "
                "효과, 다수의 폭정, 그리고 <b>동일 입력을 받으면 토론이 마팅게일이 "
                "되어 개선이 없다</b>는 이론적 결과가 있습니다. 따라서 각 "
                "에이전트에 서로 다른 데이터 슬라이스를 주고, 거부권은 통계 "
                "에이전트에만 부여하며, 최종 판정은 결정론적 규칙 엔진이 합니다.")]
    rows = []
    for v in a.agent_views:
        rows.append({"에이전트": v.agent, "역할": v.role,
                     "데이터 범위 (비대칭)": v.data_scope, "입장": v.stance,
                     "확률": v.prob_up if v.prob_up is not None else np.nan,
                     "거부권": "⛔" if v.veto else ""})
    out.append(df_table(pd.DataFrame(rows), nd=3))
    for v in a.agent_views:
        if v.evidence:
            out.append(f'<details class="ag"><summary>{E(v.agent)}</summary>'
                       f'<ul>' + "".join(f"<li>{E(e)}</li>"
                                         for e in v.evidence[:5]) +
                       "</ul></details>")
    if len(a.verdict.pooled_detail):
        out.append("<h4>캘리브레이션 가중 로그오즈 풀링</h4>")
        out.append(df_table(a.verdict.pooled_detail,
                            ["에이전트", "입장", "확률", "가중치", "로그오즈"], nd=3))
        out.append(f'<p class="muted">에이전트 간 확률 산포 '
                   f'<b>{_f(a.verdict.dispersion,"{:.3f}")}</b> — 산포가 크면 '
                   f'통합 확률을 0.5로 축소합니다(평균내지 않음).</p>')
    return "".join(out)


r_agents.applies = lambda a: True
REGISTRY.append(Section("agents", "에이전트 심의", "◈", r_agents.applies,
                        r_agents, 60))


# ---------------------------------------------------------------- 24. 레드팀

def r_red(a) -> str:
    rt = next((v for v in a.agent_views if v.agent.startswith("A11")), None)
    if rt is None:
        return ""
    return (note("레드팀은 <b>최소 3개의 구체적 반대 증거 제출이 의무</b>입니다. "
                 "제출 실패는 시스템 오류로 로깅됩니다.", "warn") +
            "<ul class='rl'>" +
            "".join(f"<li>{E(x)}</li>" for x in rt.evidence) + "</ul>")


r_red.applies = lambda a: any(v.agent.startswith("A11") for v in a.agent_views)
REGISTRY.append(Section("red", "레드팀 — 사전등록된 반증", "⚑", r_red.applies,
                        r_red, 62, True))


# ---------------------------------------------------------------- 24b. 모니터링

def r_monitor(a) -> str:
    out = []
    if a.catalysts:
        out.append("<h4>촉매 캘린더</h4>")
        out.append(df_table(pd.DataFrame(a.catalysts)))
    if a.monitor:
        out.append("<h4>모니터링 플랜</h4>")
        out.append(df_table(pd.DataFrame([{
            "확인 대상": m.what, "출처": m.source, "주기": m.frequency,
            "임계/기준": m.threshold, "이유": m.why} for m in a.monitor])))
    out.append(note("모니터링 플랜이 없는 논지는 검증되지 않습니다. "
                    "각 항목은 <b>출처와 주기와 임계</b>가 지정되어 있어야 "
                    "누군가 실제로 확인할 수 있습니다."))
    return "".join(out)


r_monitor.applies = lambda a: bool(a.monitor) or bool(a.catalysts)
REGISTRY.append(Section("monitor", "촉매 캘린더 · 모니터링 플랜", "◷",
                        r_monitor.applies, r_monitor, 65, True))


# ---------------------------------------------------------------- 25. 한계

def r_limits(a) -> str:
    rows = [
        ("생존편향", "Yahoo Finance에 상장폐지 종목이 없습니다. 종목선택 전략은 "
                 "원리적으로 검증 불가합니다."),
        ("인트라데이", "일봉만 사용 → 진짜 실현변동성·오더플로우 계산 불가."),
        ("펀더멘털 PIT", "리스테이트먼트가 반영된 값이라 point-in-time이 아닙니다."),
        ("옵션 히스토리", "스냅샷만 제공 → 백테스트 불가. 오늘부터 축적해야 합니다."),
        ("체결 가정", "신호 생성 종가가 아니라 다음 거래일 시가/VWAP를 가정해야 "
                  "합니다. 이 선택이 수익성을 뒤집을 수 있습니다."),
        ("세금·환율·추적오차", "미반영."),
    ]
    out = kv_table(rows)
    if a.warnings:
        out += "<ul class='rl'>" + "".join(f"<li>⚠ {E(w)}</li>"
                                           for w in a.warnings) + "</ul>"
    eqw = getattr(a, "equity", None)
    if eqw and eqw.warnings:
        out += "<ul class='rl'>" + "".join(f"<li>⚠ {E(w)}</li>"
                                           for w in eqw.warnings) + "</ul>"
    return out


r_limits.applies = lambda a: True
REGISTRY.append(Section("limits", "이 분석이 말할 수 없는 것", "⊘",
                        r_limits.applies, r_limits, 90))


# ================================================================ 조립

CSS = """
*{box-sizing:border-box}
body{margin:0;background:#0b0d11;color:#e6e8ec;
 font-family:ui-sans-serif,-apple-system,"Segoe UI",Roboto,"Noto Sans KR",sans-serif;
 font-size:14.5px;line-height:1.62;-webkit-text-size-adjust:100%}
.wrap{max-width:980px;margin:0 auto;padding:18px 16px 80px}
header{padding:22px 0 12px;border-bottom:1px solid #252a34;margin-bottom:14px}
h1{font-size:26px;margin:0 0 6px;letter-spacing:-.4px}
h2{font-size:17px;margin:0}
h4{font-size:14px;margin:18px 0 8px;color:#cfd4de}
.sub{color:#8b93a3;font-size:13px}
.toc{display:flex;flex-wrap:wrap;gap:6px;margin:14px 0 8px}
.toc a{font-size:12px;padding:5px 10px;border-radius:99px;background:#171a21;
 color:#b9c0cc;text-decoration:none;border:1px solid #252a34}
.toc a:hover{background:#1e222b;color:#e6e8ec}
details.sec{background:#12151b;border:1px solid #222732;border-radius:11px;
 margin:9px 0;overflow:hidden}
details.sec>summary{cursor:pointer;padding:12px 15px;list-style:none;
 display:flex;align-items:center;gap:9px;user-select:none}
details.sec>summary::-webkit-details-marker{display:none}
details.sec>summary:hover{background:#161a22}
.icon{width:26px;height:26px;border-radius:7px;background:#1c212b;display:flex;
 align-items:center;justify-content:center;font-size:14px;color:#5b8def;flex:none}
.badge{margin-left:auto;font-size:10.5px;padding:3px 8px;border-radius:99px;
 background:#1c212b;color:#8b93a3;border:1px solid #252a34}
.body{padding:2px 16px 18px}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));
 gap:10px;margin:6px 0 14px}
.cards3{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin:12px 0}
@media(max-width:760px){.cards3{grid-template-columns:1fr}}
.src{margin-top:10px;font-size:11.5px;color:#8b93a3;line-height:1.6}
.card{background:#171a21;border:1px solid #252a34;border-radius:10px;padding:14px}
.card.up{border-color:#2ec27e55}.card.down{border-color:#e0455f55}
.cl{font-size:11px;color:#8b93a3}
.cv{font-size:23px;font-weight:700;margin:4px 0 2px;letter-spacing:-.5px}
.card.up .cv{color:#2ec27e}.card.down .cv{color:#e0455f}
.cs{font-size:12px;color:#cfd4de}.cs2{font-size:11px;color:#8b93a3;margin-top:4px}
.hero{background:linear-gradient(135deg,#1a2030,#171a21);border:1px solid #2b3346;
 border-radius:12px;padding:16px;margin-bottom:12px}
.hero-t{font-size:20px;font-weight:700;color:#7ba6ff}
.hero-s{font-size:13px;color:#cfd4de;margin-top:5px}
.hero-b{font-size:11px;color:#8b93a3;margin-top:8px}
.chips{display:flex;flex-wrap:wrap;gap:6px;margin:10px 0}
.chip{font-size:11px;padding:4px 9px;border-radius:99px;background:#1c212b;
 border:1px solid #2b3346;color:#b9c0cc}
.two{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-top:10px}
@media(max-width:620px){.two{grid-template-columns:1fr}}
.two ul{margin:4px 0;padding-left:18px;font-size:13px;color:#cfd4de}
table.kv{width:100%;border-collapse:collapse;margin:8px 0}
table.kv td{padding:7px 8px;border-bottom:1px solid #1e222b;vertical-align:top}
table.kv td.k{color:#8b93a3;font-size:12.5px;width:42%}
table.kv td.v{font-variant-numeric:tabular-nums;font-size:13.5px}
table.kv td.n{color:#6b7280;font-size:11px}
table.kv tr.bind{background:#1a2030}
table.kv tr.bind td.k{color:#e8a33d}
table.kv tr.tot td{border-top:1px solid #2b3346;padding-top:10px}
.tw{overflow-x:auto;-webkit-overflow-scrolling:touch;margin:8px 0}
table.dt{width:100%;border-collapse:collapse;font-size:12px;min-width:520px}
table.dt th{text-align:left;padding:7px 9px;background:#171a21;color:#8b93a3;
 font-weight:600;white-space:nowrap;border-bottom:1px solid #252a34}
table.dt td{padding:6px 9px;border-bottom:1px solid #1a1e26;
 font-variant-numeric:tabular-nums;white-space:nowrap}
table.dt tr.bad td{background:#e0455f11}
.note{background:#151922;border-left:3px solid #5b8def;padding:10px 13px;
 border-radius:0 8px 8px 0;margin:10px 0;font-size:12.8px;color:#cfd4de}
.note.warn{border-left-color:#e8a33d;background:#1d1a15}
.note.bad{border-left-color:#e0455f;background:#1d1519}
.hi{background:#171a21;border:1px solid #252a34;border-radius:8px;padding:10px 13px;
 margin:10px 0;font-size:13.5px}
.pill{font-size:11px;padding:3px 9px;border-radius:99px;font-weight:700}
.pill.up{background:#2ec27e22;color:#2ec27e}
.pill.down{background:#e0455f22;color:#e0455f}
.muted{color:#8b93a3;font-size:12.5px}
ul.rl{margin:8px 0;padding-left:20px;font-size:13px;color:#cfd4de}
ul.rl li{margin:5px 0}
code{background:#1c212b;padding:1.5px 5px;border-radius:4px;font-size:12px;
 color:#9ec1ff}
details.ag{background:#141821;border:1px solid #1e222b;border-radius:8px;
 margin:6px 0;padding:0}
details.ag>summary{padding:8px 12px;cursor:pointer;font-size:12.5px;color:#b9c0cc}
details.ag ul{margin:0;padding:0 12px 10px 30px;font-size:12px;color:#8b93a3}
.gauges{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));
 gap:8px;margin:8px 0}
footer{margin-top:30px;padding-top:16px;border-top:1px solid #252a34;
 color:#6b7280;font-size:11.5px}
.topbar{position:sticky;top:0;z-index:20;display:flex;align-items:center;gap:10px;
 padding:9px 16px;background:#0b0d11ee;backdrop-filter:blur(8px);
 border-bottom:1px solid #222732}
.tb-t{font-weight:700;font-size:14px;letter-spacing:-.2px}
.tb-g{font-size:11px;padding:3px 9px;border-radius:99px;background:#1c212b;
 border:1px solid #2b3346;color:#9ec1ff;font-weight:700}
.tb-s{font-size:11.5px;color:#8b93a3}
.tb-b{margin-left:auto;background:#171a21;border:1px solid #252a34;color:#b9c0cc;
 padding:4px 10px;border-radius:7px;font-size:11.5px;cursor:pointer}
.tb-b+.tb-b{margin-left:0}
.tb-b:hover{background:#1e222b;color:#e6e8ec}
.metrics{display:grid;grid-template-columns:repeat(auto-fit,minmax(132px,1fr));
 gap:8px;margin-top:14px}
.m{background:#12151b;border:1px solid #222732;border-radius:9px;padding:9px 11px}
.ml{font-size:10.5px;color:#8b93a3}
.mv{font-size:17px;font-weight:700;margin:2px 0;letter-spacing:-.3px}
.ms{font-size:10px;color:#6b7280}
.part{margin:26px 0 8px}
.ph{display:flex;gap:12px;align-items:flex-start;padding:0 2px 10px;
 border-bottom:1px solid #222732;margin-bottom:6px}
.pn{font-size:20px;font-weight:800;color:#2b3346;line-height:1;min-width:34px}
.pt{font-size:15px;margin:0;color:#e6e8ec;letter-spacing:-.2px}
.pd{font-size:11.5px;color:#6b7280;margin-top:3px}
.num{font-size:10.5px;color:#5a6373;font-variant-numeric:tabular-nums;
 min-width:20px;flex:none}
details.sec>summary h3{font-size:14.5px;margin:0;font-weight:600}
.tg{display:flex;flex-wrap:wrap;gap:5px;align-items:center;margin-bottom:6px}
.tgl{font-size:10.5px;color:#5a6373;font-weight:700;margin-right:4px;
 letter-spacing:.4px}
nav.toc{margin:16px 0 6px}
@media print{
 body{background:#fff;color:#111}
 .topbar,.ctl,nav.toc{display:none}
 details.sec,section.part{break-inside:avoid}
 details.sec[open]>summary{background:none}
 details.sec{border-color:#ccc;background:#fff}
 .note,.hi,.card,.m{background:#f6f7f9;color:#111;border-color:#ddd}
 table.dt th{background:#eee;color:#111}
 h1,h2,h3,.pt{color:#111}
}
.ctl{display:flex;gap:8px;margin:10px 0}
.ctl button{background:#171a21;border:1px solid #252a34;color:#b9c0cc;
 padding:6px 12px;border-radius:8px;font-size:12px;cursor:pointer}
.ctl button:hover{background:#1e222b;color:#e6e8ec}
"""

JS = """
function allSec(){return document.querySelectorAll('details.sec')}
function expandAll(){allSec().forEach(d=>d.open=true)}
function collapseAll(){allSec().forEach(d=>d.open=false)}
document.addEventListener('click',function(e){
  const a=e.target.closest('.toc a'); if(!a)return; e.preventDefault();
  const id=a.getAttribute('href').slice(1);
  const d=document.getElementById(id); if(!d)return; d.open=true;
  d.scrollIntoView({behavior:'smooth',block:'start'});
});
"""


# ================================================================ 단/중/장

def r_horizons(a) -> str:
    hz = getattr(a, "horizons", None)
    if hz is None or not hz.stats:
        return ""
    out = [note(
        "지평별 결과를 <b>평균하지 않습니다.</b> 단기 55점·중기 72점·장기 74점을 "
        "기간가중해 67점(BUY)을 만들면 '장기 구조적 상승 안의 중기 조정' 같은 "
        "정보가 통째로 사라집니다. 평균 하나만 남고, 그 평균은 어느 기간에도 "
        "해당하지 않습니다. 여기서는 지평별로 따로 계산하고 "
        "<b>서로 어긋나는 지점</b>을 찾아 드러냅니다.")]

    rows = []
    for st in hz.stats:
        rows.append({
            "지평": f"{st.label_ko} ({st.days}일)",
            "구간": f"{st.start} ~ {st.end}",
            "누적수익": st.cum_return,
            "연변동성": st.ann_vol,
            "샤프": round(float(st.sharpe), 2) if st.sharpe == st.sharpe else None,
            "최대낙폭": st.max_drawdown,
            "추세": st.trend,
            "SMA기울기": st.sma_slope_pct / 100.0,
            "교차": st.ma_cross,
            "시장β": (round(float(st.beta_mkt), 2)
                     if st.beta_mkt is not None else None),
        })
    out.append(df_table(pd.DataFrame(rows), nd=4))

    # 드리프트 신뢰도 — 짧은 지평일수록 SE 가 커진다
    drows = []
    for st in hz.stats:
        drows.append({
            "지평": st.label_ko,
            "드리프트 μ̂": st.drift_ann,
            "표준오차 σ/√T": st.drift_se,
            "t": round(float(st.drift_t), 2) if st.drift_t == st.drift_t else None,
            "판정": "의미 있음" if st.drift_meaningful else "0과 구별 불가",
        })
    out.append("<h4>드리프트 신뢰도</h4>")
    out.append(df_table(pd.DataFrame(drows), nd=4))
    out.append(note(
        "드리프트 표준오차는 σ/√T 입니다. 지평이 짧을수록 커져서, 짧은 구간의 "
        "기대수익률은 거의 언제나 0과 구별되지 않습니다. |t| < 2 인 지평의 "
        "수익률로 미래를 말하면 안 됩니다."))

    if hz.vol_term_structure:
        out.append(f'<div class="hi">변동성 기간구조 — {E(hz.vol_term_structure)}</div>')

    if hz.disagreements:
        out.append("<h4>지평 간 불일치</h4>")
        out.append("<ul>" + "".join(f"<li>{E(d)}</li>"
                                    for d in hz.disagreements) + "</ul>")
        out.append(note("불일치는 오류가 아니라 정보입니다. 어느 한 지평의 결론만 "
                        "인용하면 정반대 이야기가 나온다는 뜻이므로, 보유 기간을 "
                        "정하지 않은 채로는 결론을 낼 수 없습니다.", "warn"))
    else:
        out.append(note("지평 간 결론이 일치합니다 — 보유 기간에 크게 의존하지 "
                        "않는 상태입니다."))
    return "".join(out)


r_horizons.applies = lambda a: getattr(a, "horizons", None) is not None \
    and bool(getattr(a.horizons, "stats", None))
REGISTRY.append(Section("horizons", "단기 · 중기 · 장기", "⏱", r_horizons.applies,
                        r_horizons, priority=18, open_default=True))


# ================================================================ 거시 대시보드

def r_macro(a) -> str:
    mb = getattr(a, "macro_board", None)
    if mb is None or not mb.rows:
        return ""
    cls = a.classification
    out = [note(
        f"<b>{E(cls.spec.label_ko)}</b>에 실제로 영향을 주는 거시 변수만 "
        "골라 봅니다. 자산군마다 보는 변수가 다릅니다 — 금에는 실질금리·달러가, "
        "국채에는 명목금리·커브가, 개별주에는 시장·변동성·크레딧이 들어갑니다.")]

    rows = []
    for r in mb.rows:
        rows.append({
            "변수": r.label_ko,
            "최신값": r.latest_str,
            "기준일": r.as_of,
            "1M 변화": round(float(r.chg_1m), 3) if r.chg_1m == r.chg_1m else None,
            "3M 변화": round(float(r.chg_3m), 3) if r.chg_3m == r.chg_3m else None,
            "베타": round(float(r.beta), 4) if r.beta is not None else None,
            "t": round(float(r.tstat), 1) if r.tstat is not None else None,
            "영향": r.impact,
            "해석": r.comment,
        })
    out.append(df_table(pd.DataFrame(rows), nd=4))
    out.append(note(mb.note))

    # 판단 3종
    out.append(
        '<div class="cards3">'
        f'<div class="card"><div class="ct">전술적 거시 판단</div>'
        f'<div class="cv">{E(mb.tactical)}</div>'
        f'<div class="cs">{E(mb.tactical_detail)}</div></div>'
        f'<div class="card"><div class="ct">구조적 거시 판단</div>'
        f'<div class="cv">{E(mb.structural)}</div>'
        f'<div class="cs">{E(mb.structural_detail[:150])}</div></div>'
        f'<div class="card"><div class="ct">핵심 전환 신호</div>'
        f'<div class="cv">{E(mb.pivot)}</div>'
        f'<div class="cs">{E(mb.pivot_detail)}</div></div>'
        '</div>')

    if mb.scenarios:
        out.append("<h4>거시 시나리오 매트릭스</h4>")
        out.append(df_table(pd.DataFrame([
            {"시나리오": x["name"], "발생 조건": x["condition"],
             "방향": x["direction"], "관찰 지표": x["watch"]}
            for x in mb.scenarios])))

    if mb.sources:
        out.append(f'<div class="src">출처: {E(" · ".join(mb.sources))}</div>')
    return "".join(out)


r_macro.applies = lambda a: getattr(a, "macro_board", None) is not None \
    and bool(getattr(a.macro_board, "rows", None))
REGISTRY.append(Section("macro", "거시경제 대시보드", "🌐", r_macro.applies,
                        r_macro, priority=28, open_default=True))


def build_sections(a) -> List[Section]:
    out = []
    for s in REGISTRY:
        try:
            if s.applies(a):
                out.append(s)
        except Exception:
            continue
    return sorted(out, key=lambda s: s.priority)


def _tip_script() -> str:
    """용어 사전을 JSON 으로 넣은 툴팁 스크립트."""
    import json as _json
    payload = _json.dumps({k: list(v) for k, v in _TERMS.items()},
                          ensure_ascii=False)
    # </script> 가 문자열 안에 들어가면 스크립트가 조기 종료된다
    payload = payload.replace("</", "<\\/")
    return _tip_js().replace("__TERMS_JSON__", payload)


def render_html(a, theme: str = DEFAULT_THEME) -> str:
    secs = build_sections(a)
    for x in secs:
        x.part = SECTION_PART.get(x.sid, "IV")
    cls = a.classification
    eq = getattr(a, "equity", None)
    v = a.verdict
    subtitle = (f"{cls.spec.label_ko}"
                + (f" · {eq.archetype_ko}" if eq and
                   eq.archetype != "UNCLASSIFIED" else "")
                + f" · 기준일 {a.asof} · 연율화 √{cls.spec.ann_factor}")

    # 핵심 지표 스트립
    strip = []
    strip.append(("판정", E(v.grade), v.model_confidence))
    if v.direction_prob:
        strip.append(("통합 확률", f"{v.direction_prob:.1%}",
                      f"CI [{v.direction_ci[0]:.0%}, {v.direction_ci[1]:.0%}]"))
    strip.append(("리스크 예산", f"{v.risk_budget_weight:.1%}",
                  a.sizing.binding_constraint if a.sizing else ""))
    if a.trade is not None and np.isfinite(a.trade.expected_pnl_net):
        strip.append(("비용 후 기대손익", f"{a.trade.expected_pnl_net:+.1%}",
                      f"R:R {a.trade.rr_ratio:.2f}"))
    if a.hedge is not None and np.isfinite(a.hedge.residual_vol_ann):
        strip.append(("헤지 후 잔차 vol", f"{a.hedge.residual_vol_ann:.1%}",
                      f"제거 {a.hedge.var_removed:.0%}"))
    if a.kill:
        nb = sum(1 for k in a.kill if k.breached)
        strip.append(("반증 조건 발동", f"{nb}/{len(a.kill)}", "kill criteria"))
    strip_html = "".join(
        f'<div class="m"><div class="ml">{E(l)}</div>'
        f'<div class="mv">{val}</div><div class="ms">{E(sub)}</div></div>'
        for l, val, sub in strip)

    # 파트별 조립
    body, toc = [], []
    idx = 0
    for pn, ptitle, pdesc in PARTS:
        grp = [x for x in secs if x.part == pn]
        if not grp:
            continue
        toc.append(f'<div class="tg"><span class="tgl">{pn}. {E(ptitle)}</span>')
        body.append(f'<section class="part" id="part-{pn}">'
                    f'<div class="ph"><span class="pn">{pn}</span>'
                    f'<div><h2 class="pt">{E(ptitle)}</h2>'
                    f'<div class="pd">{E(pdesc)}</div></div></div>')
        for sec in grp:
            idx += 1
            try:
                inner = sec.render(a)
            except Exception as exc:
                inner = f'<p class="muted">섹션 렌더 실패: {E(exc)}</p>'
            tag = f'<span class="badge">{E(sec.tag)}</span>' if sec.tag else ""
            body.append(
                f'<details class="sec" id="{sec.sid}"'
                f'{" open" if sec.open_default else ""}>'
                f'<summary><span class="num">{idx:02d}</span>'
                f'<span class="icon">{sec.icon}</span>'
                f'<h3>{E(sec.title)}</h3>{tag}</summary>'
                f'<div class="body">{inner}</div></details>')
            toc.append(f'<a href="#{sec.sid}">{idx:02d} {E(sec.title)}</a>')
        body.append('</section>')
        toc.append('</div>')

    skipped = [x.title for x in REGISTRY if x not in secs]
    return f"""<!DOCTYPE html><html lang="ko"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{E(a.ticker)} — I ALWAYS WIN</title><style>{themed_css(CSS, theme)}{_tip_css()}</style></head><body>
<div class="topbar"><span class="tb-t">{E(a.ticker)}</span>
<span class="tb-g">{E(v.grade)}</span>
<span class="tb-s">{E(cls.spec.label_ko)}</span>
<button class="tb-b" onclick="collapseAll()">접기</button>
<button class="tb-b" onclick="expandAll()">펼치기</button></div>
<div class="wrap">
<header><h1>{E(a.ticker)} · I ALWAYS WIN 정밀 분석</h1>
<div class="sub">{E(subtitle)}</div>
<div class="metrics">{strip_html}</div></header>
<nav class="toc">{"".join(toc)}</nav>
{"".join(body)}
<footer>
<p>활성 섹션 {len(secs)} / 전체 {len(REGISTRY)}.
이 자산에 해당하지 않아 생략: {E(', '.join(skipped)) or '없음'}</p>
<p>{E(a.verdict.disclaimer)}</p>
</footer></div><script>{JS}
{_tip_script()}</script></body></html>"""


def save_html(a, path: str, theme: str = DEFAULT_THEME) -> str:
    with open(path, "w", encoding="utf-8") as f:
        f.write(render_html(a, theme=theme))
    return path
