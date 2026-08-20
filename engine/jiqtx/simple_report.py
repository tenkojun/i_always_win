# -*- coding: utf-8 -*-
"""
간단 리서치 — 처음 보는 사람을 위한 보고서
==========================================
전문 보고서(dynamic_report)와 **같은 분석 결과**를 쓰되, 보여 주는
방식만 바꾼다. 숫자를 줄이는 게 아니라 **읽는 순서를 바꾼다.**

쉽게 만든다고 정직함을 버리지 않는다
------------------------------------
초보자용이라고 불확실한 걸 확실하게 말하면 그게 제일 위험하다.
엔진이 기권(ABSTAIN)했으면 "예측할 수 없습니다" 라고 **쉬운 말로**
쓴다. 감추거나 그럴듯한 숫자로 채우지 않는다.

전문 보고서와 다른 점
---------------------
- 지표 이름 대신 **질문**으로 절을 연다 ("얼마나 위험한가?")
- 모든 숫자에 **체감 환산**을 붙인다 (변동성 32% → "100만원이 하루에
  ±2만원쯤 움직인다")
- 표보다 **막대·게이지**를 앞세운다
- 한 화면에 하나의 메시지만 둔다

여기서 다루지 않는 것
---------------------
팩터 회귀 계수, 옵션 RND, 커버리지 검정, 패널 반대신문 같은 것은
전문 보고서에만 있다. 그런 게 필요해지면 그때 전문 보고서를 보면 된다.
간단 리서치 끝에 그 링크를 둔다.
"""
from __future__ import annotations

import html
import math
from typing import Any, Dict, List, Optional

import numpy as np

from . import charts as ch
from .report_theme import DEFAULT_THEME, themed_css
from .glossary import build_css as _tip_css


def E(s: Any) -> str:
    return html.escape(str(s if s is not None else ""))


def _f(x, fmt="{:.1%}", dash="—"):
    try:
        v = float(x)
        if not np.isfinite(v):
            return dash
        return fmt.format(v)
    except (TypeError, ValueError):
        return dash


def _won(pct: float, base: int = 1_000_000) -> str:
    """비율을 '100만원 기준 몇 원' 으로 바꾼다 — 체감이 안 되면 숫자는 무의미하다."""
    try:
        v = abs(float(pct)) * base
        if not np.isfinite(v):
            return "—"
        if v >= 10000:
            return f"약 {v/10000:,.1f}만원"
        return f"약 {v:,.0f}원"
    except (TypeError, ValueError):
        return "—"


# ══════════════════════════════════════════════════════════════
#  조각들
# ══════════════════════════════════════════════════════════════

def _hero(a) -> str:
    v = a.verdict
    grade = getattr(v, "grade", "—")
    KO = {"BUY": "매수 검토", "HOLD": "관망", "NO_TRADE": "진입 보류",
          "AVOID": "회피", "SELL": "매도 검토"}
    tone = {"BUY": "up", "SELL": "down", "NO_TRADE": "warn",
            "AVOID": "down"}.get(grade, "neutral")
    conf = getattr(v, "model_confidence", None)
    vetoes = getattr(v, "vetoes", []) or []

    # 한 줄로 무슨 뜻인지
    means = {
        "BUY": "조건이 갖춰졌다고 봅니다. 다만 아래 위험 크기를 먼저 확인하세요.",
        "HOLD": "지금 새로 들어갈 근거가 뚜렷하지 않습니다. 이미 갖고 있다면 유지.",
        "NO_TRADE": "지금은 들어가지 않는 게 낫습니다. "
                    "<b>약세 전망이 아니라</b>, 판단 근거가 부족하거나 "
                    "위험 한도에 걸렸다는 뜻입니다.",
        "AVOID": "피하는 것을 권합니다.",
        "SELL": "줄이는 것을 검토하세요.",
    }.get(grade, "")

    cls = a.classification
    kind = E(getattr(getattr(cls, "spec", None), "label_ko", "") or "—")

    veto_html = ""
    if vetoes:
        items = "".join(f"<li>{E(x)}</li>" for x in vetoes[:4])
        veto_html = (f'<div class="veto"><b>진입을 막은 조건</b><ul>{items}</ul>'
                     f'</div>')

    return f'''
<section class="hero {tone}">
  <div class="tick">{E(a.ticker)}</div>
  <div class="grade">{E(KO.get(grade, grade))}</div>
  <div class="means">{means}</div>
  <div class="meta">{kind} · 분석일 {E(a.asof)}</div>
  {veto_html}
</section>'''


def _q_what(a) -> str:
    """이 종목은 어떤 종목인가."""
    cls = a.classification
    spec = getattr(cls, "spec", None)
    eq = getattr(a, "equity", None)
    arche = getattr(eq, "archetype_ko", None) or getattr(eq, "archetype", None)
    conf = getattr(cls, "confidence", None)

    rows = [("자산 종류", getattr(spec, "label_ko", "—")),
            ("분류 확신도", _f(conf, "{:.0%}"))]
    if arche:
        rows.append(("성격", str(arche)))
    sector = getattr(cls, "sector", None)
    if sector:
        rows.append(("섹터", str(sector)))

    note = ("자산 종류에 따라 <b>봐야 할 것이 다릅니다.</b> 금은 실질금리와 "
            "달러가, 국채는 금리와 커브가, 개별주는 시장·변동성·신용이 "
            "핵심입니다. 이 보고서는 이 종목에 맞는 것만 골라 보여 줍니다.")
    return _card("이건 어떤 종목인가요?", _kv(rows), note)


def _q_risk(a) -> str:
    """얼마나 위험한가 — 체감 가능한 숫자로."""
    p = a.perf or {}
    vol = p.get("vol_ann")
    mdd = p.get("max_drawdown")
    dd = getattr(a, "drawdown", None)
    under = getattr(dd, "longest_underwater_days", None)

    daily = (float(vol) / math.sqrt(252)) if vol and np.isfinite(vol) else None

    body = []
    if daily:
        body.append(_big(f"±{daily*100:.1f}%",
                         "보통 하루 움직임",
                         f"100만원이면 하루에 {_won(daily)} 정도 흔들립니다."))
    if mdd and np.isfinite(mdd):
        body.append(_big(f"{mdd*100:.0f}%", "과거 최대 하락",
                         f"고점에서 여기까지 떨어진 적이 있습니다. "
                         f"100만원이면 {_won(mdd)} 손실."))
    if under:
        yrs = under / 252.0
        body.append(_big(f"{int(under)}일", "가장 오래 물린 기간",
                         f"약 {yrs:.1f}년 동안 원금을 회복하지 못한 구간이 "
                         f"있었습니다."))

    # 낙폭 곡선 — 얼마나 오래 물렸는지는 숫자 하나로 안 보인다
    curve = ""
    px = getattr(a, "prices", None)
    if px is not None and len(px) > 20:
        arr = np.asarray(px, dtype=float)
        arr = arr[np.isfinite(arr)]
        curve = ch.drawdown_curve(arr[-2520:], title="원금 대비 하락 (과거)")

    note = ("변동성은 <b>방향이 아니라 폭</b>입니다. 크다고 나쁜 게 아니라 "
            "그만큼 흔들린다는 뜻이고, 그 흔들림을 견딜 수 있는 금액만 "
            "넣어야 합니다.")
    return _card("얼마나 위험한가요?",
                 f'<div class="bigs">{"".join(body)}</div>{curve}', note)


def _q_now(a) -> str:
    """지금 흐름 — 단/중/장."""
    hz = getattr(a, "horizons", None)
    if hz is None or not getattr(hz, "stats", None):
        return ""
    labs = [s.label_ko for s in hz.stats]
    ret = [float(s.cum_return) if s.cum_return == s.cum_return else None
           for s in hz.stats]
    chart = ch.horizon_compare(labs, {"수익률": ret},
                               title="기간별 수익률", fmt="pct", h=210)

    rows = []
    for s in hz.stats:
        arrow = "▲" if (s.cum_return or 0) > 0 else "▼"
        rows.append((f"{s.label_ko} ({s.days}일)",
                     f"{arrow} {_f(s.cum_return)} · 추세 {s.trend}"))

    dis = getattr(hz, "disagreements", []) or []
    warn = ""
    if dis:
        warn = ('<div class="warn"><b>기간마다 방향이 다릅니다.</b><br>'
                + "<br>".join(E(d) for d in dis[:2])
                + '<br><br>이건 오류가 아니라 정보입니다. '
                  '<b>얼마나 들고 있을 것인지</b>를 먼저 정하지 않으면 '
                  '어느 쪽 숫자를 봐야 할지 정할 수 없습니다.</div>')

    note = ("짧은 기간의 수익률은 <b>운의 비중이 큽니다.</b> 3개월 +20% 는 "
            "실력의 증거가 되기 어렵습니다.")
    return _card("지금 흐름은 어떤가요?", chart + _kv(rows) + warn, note)


def _q_driver(a) -> str:
    """무엇이 이 종목을 움직이는가 — 유의한 것만."""
    mb = getattr(a, "macro_board", None)
    if mb is None or not getattr(mb, "rows", None):
        return ""
    sig = [r for r in mb.rows
           if r.tstat is not None and abs(float(r.tstat)) >= 2.0]
    if not sig:
        return _card("무엇이 이 종목을 움직이나요?",
                     '<div class="warn">통계적으로 뚜렷하게 연결된 거시 '
                     '변수를 찾지 못했습니다. 이 종목은 큰 흐름보다 '
                     '개별 요인에 더 움직인다는 뜻일 수 있습니다.</div>',
                     "확실하지 않은 연결을 억지로 말하지 않습니다.")

    sig = sorted(sig, key=lambda r: -abs(float(r.contribution or 0)))[:5]
    chart = ch.tornado([r.label_ko for r in sig],
                       [float(r.contribution) * 100 for r in sig],
                       title="최근 3개월 영향 (클수록 큰 영향)", unit="%")
    rows = [(r.label_ko,
             f"{r.impact} · 최근값 {r.latest_str}") for r in sig]
    note = ("여기 없는 변수는 <b>연결이 뚜렷하지 않아 뺀 것</b>입니다. "
            "통계적으로 구별되지 않는 관계로 이야기를 만들면 그럴듯하지만 "
            "틀립니다.")
    return _card("무엇이 이 종목을 움직이나요?", chart + _kv(rows), note)


def _q_size(a) -> str:
    """얼마나 사야 하나."""
    sz = getattr(a, "sizing", None)
    if sz is None:
        return ""
    w = getattr(sz, "final_weight", None)
    if w is None:
        w = getattr(sz, "weight", None)
    binding = (getattr(sz, "binding_constraint_ko", None)
               or getattr(sz, "binding_constraint", None) or "—")

    body = _big(_f(w, "{:.1%}"), "권장 비중",
                f"전체 투자금이 1,000만원이면 {_won(w, 10_000_000)} 정도.")
    note = (f"이 비중을 정한 <b>가장 강한 제약</b>은 “{E(binding)}” 입니다. "
            "여러 기준(낙폭 한도·변동성 목표·유동성·자산군 상한)을 모두 "
            "계산해 <b>가장 보수적인 값</b>을 택합니다.")
    return _card("얼마나 사야 하나요?", f'<div class="bigs">{body}</div>', note)


def _q_wrong(a) -> str:
    """무엇을 보면 틀린 걸 아는가."""
    # a.kill 은 KillCriterion 리스트다 (trigger / metric / threshold /
    # current / breached / action). 예전엔 .criteria 를 찾아서 늘 비었고,
    # 절 자체가 통째로 사라졌다 — 데이터는 있는데 화면에 없었다.
    items = getattr(a, "kill", None) or []
    if not items:
        return ""
    lis = []
    for k in items[:6]:
        trig = getattr(k, "trigger", None) or str(k)
        cur = getattr(k, "current", None)
        thr = getattr(k, "threshold", None)
        act = getattr(k, "action", None)
        fired = bool(getattr(k, "breached", False))
        detail = []
        if thr is not None:
            detail.append(f"기준 {thr}")
        if cur is not None:
            detail.append(f"현재 {cur}")
        sub = (f'<div class="ksub">{E(" · ".join(detail))}'
               + (f' → {E(act)}' if act else '') + '</div>') if detail or act else ""
        lis.append(f'<li class="{"fired" if fired else ""}">{E(trig)}'
                   + (' <b>← 이미 발생</b>' if fired else '')
                   + sub + '</li>')
    note = ("이 조건들은 <b>분석 시점에 미리</b> 정한 것입니다. "
            "일이 벌어진 뒤에 만든 기준은 아무것도 반증하지 못합니다.")
    return _card("무엇을 보면 판단이 틀렸다는 걸 아나요?",
                 f"<ul class='kill'>{''.join(lis)}</ul>", note)


def _q_unknown(a) -> str:
    """모르는 것 — 이 절이 이 보고서에서 제일 중요하다."""
    v = a.verdict
    disabled = getattr(v, "disabled_modules", []) or []
    warns = list(getattr(a, "warnings", []) or [])[:4]

    lis = []
    if disabled:
        lis.append("<li><b>방향 예측을 하지 않았습니다.</b> 모델이 "
                   "검정을 통과하지 못해 출력을 취소했습니다. "
                   "낮은 점수를 준 게 아니라 <b>아무 값도 내지 않은</b> "
                   "것입니다.</li>")
    lis += [f"<li>{E(w)}</li>" for w in warns]
    lis.append("<li>과거 데이터에 <b>상장폐지된 종목이 없습니다.</b> "
               "살아남은 것만 보고 있다는 뜻이라, 종목을 고르는 능력은 "
               "원리적으로 검증할 수 없습니다.</li>")
    lis.append("<li>펀더멘털은 <b>지금 시점의 값</b>입니다. 과거 어느 "
               "시점에 무엇을 알 수 있었는지는 재현되지 않습니다.</li>")

    note = ("이 목록은 겸손이 아니라 <b>사용 설명서</b>입니다. "
            "모르는 것을 아는 척하지 않는 것이 이 엔진의 원칙입니다.")
    return _card("이 분석이 모르는 것", f"<ul>{''.join(lis)}</ul>", note,
                 tone="unknown")


# ── 조립 부품 ────────────────────────────────────────────────

def _card(title: str, body: str, note: str = "", tone: str = "") -> str:
    n = f'<div class="note">{note}</div>' if note else ""
    return (f'<section class="q {tone}"><h2>{E(title)}</h2>'
            f'<div class="body">{body}</div>{n}</section>')


def _kv(rows) -> str:
    tr = "".join(f'<tr><td class="k">{E(k)}</td><td class="v">{v}</td></tr>'
                 for k, v in rows)
    return f'<table class="kv">{tr}</table>'


def _big(num: str, label: str, sub: str = "") -> str:
    s = f'<div class="sub">{sub}</div>' if sub else ""
    return (f'<div class="big"><div class="n">{E(num)}</div>'
            f'<div class="l">{E(label)}</div>{s}</div>')


CSS = """
*{box-sizing:border-box}
body{margin:0;background:#0b0d11;color:#e6e8ec;line-height:1.75;
 font-family:'Pretendard',-apple-system,'Segoe UI',system-ui,sans-serif;
 font-size:16px}
.wrap{max-width:820px;margin:0 auto;padding:26px 18px 80px}

.hero{border:1px solid #252a34;border-radius:14px;padding:26px 24px;
 margin-bottom:22px;background:linear-gradient(135deg,#1a2030,#171a21)}
.hero .tick{font-size:13px;letter-spacing:2px;color:#8b93a3}
.hero .grade{font-size:34px;font-weight:800;margin:6px 0 10px;
 letter-spacing:-.5px}
.hero.up .grade{color:#2ec27e}.hero.down .grade{color:#e0455f}
.hero.warn .grade{color:#e8a33d}.hero.neutral .grade{color:#7ba6ff}
.hero .means{font-size:15px;color:#cfd4de}
.hero .meta{margin-top:12px;font-size:12.5px;color:#8b93a3}
.hero .veto{margin-top:14px;padding:12px 14px;border-radius:9px;
 background:#1d1519;border:1px solid #e0455f55;font-size:13.5px}
.hero .veto ul{margin:6px 0 0;padding-left:18px}

.q{border:1px solid #252a34;border-radius:13px;padding:22px 22px 18px;
 margin-bottom:16px;background:#12151b}
.q h2{margin:0 0 16px;font-size:19px;color:#7ba6ff;letter-spacing:-.3px}
.q.unknown{border-color:#e8a33d55;background:#15140f}
.q.unknown h2{color:#e8a33d}
.q .body{font-size:14.5px}
.note{margin-top:15px;padding:11px 14px;border-left:3px solid #5b8def;
 background:#151922;border-radius:0 7px 7px 0;font-size:13px;
 color:#b9c0cc;line-height:1.7}
.q.unknown .note{border-left-color:#e8a33d}

.bigs{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));
 gap:12px;margin-bottom:8px}
.big{background:#171a21;border:1px solid #252a34;border-radius:10px;
 padding:16px 15px}
.big .n{font-size:29px;font-weight:800;letter-spacing:-1px;color:#e6e8ec}
.big .l{font-size:12.5px;color:#8b93a3;margin-top:2px}
.big .sub{font-size:12.5px;color:#cfd4de;margin-top:8px;line-height:1.6}

table.kv{width:100%;border-collapse:collapse;margin:12px 0 0}
table.kv td{padding:9px 8px;border-bottom:1px solid #1e222b;
 vertical-align:top}
table.kv td.k{color:#8b93a3;width:42%;font-size:13.5px}
table.kv td.v{font-size:14px}

ul{margin:4px 0;padding-left:20px}
li{margin:7px 0}
li.fired{color:#e8a33d}
ul.kill li{margin:11px 0}
.ksub{font-size:12.5px;color:#8b93a3;margin-top:3px}
.warn{margin-top:14px;padding:13px 15px;border-radius:9px;
 background:#1d1a15;border:1px solid #e8a33d55;font-size:13.5px;
 line-height:1.7}

.foot{margin-top:30px;padding:20px 22px;border:1px solid #252a34;
 border-radius:12px;background:#12151b;font-size:13.5px;color:#b9c0cc}
.foot a{color:#7ba6ff;text-decoration:none}
.foot a:hover{text-decoration:underline}
.foot b{color:#e6e8ec}

@media(max-width:620px){
  body{font-size:15px}
  .wrap{padding:16px 12px 60px}
  .hero .grade{font-size:27px}
  .big .n{font-size:25px}
}
@media print{
  body{background:#fff;color:#000}
  .q,.hero,.big{border-color:#ccc;background:#fff}
}
"""


def render_simple(a, theme: str = DEFAULT_THEME,
                  full_report_url: str = "") -> str:
    """간단 리서치 HTML. 전문 보고서와 같은 Analysis 를 쓴다."""
    parts = [
        _hero(a),
        _q_what(a),
        _q_risk(a),
        _q_now(a),
        _q_driver(a),
        _q_size(a),
        _q_wrong(a),
        _q_unknown(a),
    ]
    link = ""
    if full_report_url:
        link = (f'<p style="margin-top:10px">팩터 회귀, 옵션 내재분포, '
                f'커버리지 검정, 전문가 패널 심의 같은 상세는 '
                f'<a href="{E(full_report_url)}">전문 보고서</a>에 있습니다.</p>')

    foot = f'''
<div class="foot">
  <b>이 보고서는 매수·매도 권유가 아닙니다.</b> 과거 데이터로 계산한
  통계적 성질을 정리한 것이고, 미래를 맞히기 위한 것이 아닙니다.
  투자 판단과 그 결과는 본인에게 있습니다.
  {link}
</div>'''

    body = "".join(p for p in parts if p)
    css = themed_css(CSS + _tip_css(), theme)
    return (f'<!doctype html><html lang="ko"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>{E(a.ticker)} 간단 리서치 — Plutus</title>'
            f'<style>{css}</style></head><body>'
            f'<div class="wrap">{body}{foot}</div></body></html>')


def save_simple(a, path: str, theme: str = DEFAULT_THEME,
                full_report_url: str = "") -> str:
    import io
    html_s = render_simple(a, theme=theme, full_report_url=full_report_url)
    with io.open(path, "w", encoding="utf-8") as f:
        f.write(html_s)
    return path
