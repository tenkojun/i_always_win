# ==============================================================================
# [20/25] portfolio_report.py — 책 레벨 HTML 리포트
# ==============================================================================

"""
jiqtx.portfolio_report — 책(book) 레벨 HTML 리포트.

개별 종목 리포트가 "이 종목은 무엇인가"라면,
이 리포트는 "이 포지션들을 합치면 무엇을 들고 있는 것인가"에 답한다.
"""

import html
from typing import Any, Dict, List

import numpy as np
import pandas as pd

# ── 패키지 내부 의존 ──────────────────────────────────────────
from .dynamic_report import CSS, E, JS, _f, df_table, kv_table, note
from . import charts as ch




def render_portfolio(P, title: str = "포트폴리오") -> str:
    r, nt, ac = P.risk, P.netting, P.allocation
    fails = P.limits[~P.limits["충족"]] if len(P.limits) else pd.DataFrame()

    S: List[str] = []

    # ---- 요약 카드
    S.append('<div class="cards">')
    S.append(f'<div class="card"><div class="cl">책 변동성 (연)</div>'
             f'<div class="cv">{_f(r.vol_ann,"{:.1%}")}</div>'
             f'<div class="cs">VaR95 {_f(P.var95,"{:.2%}")} · '
             f'ES95 {_f(P.es95,"{:.2%}")}</div></div>')
    S.append(f'<div class="card"><div class="cl">유효 베팅 수</div>'
             f'<div class="cv">{r.effective_bets:.2f}</div>'
             f'<div class="cs">종목 {len(r.tickers)}개 · 분산비율 '
             f'{r.diversification_ratio:.2f}</div></div>')
    S.append(f'<div class="card {"down" if r.concentration_flag else ""}">'
             f'<div class="cl">최대 위험 집중</div>'
             f'<div class="cv">{_f(r.max_pct_contribution,"{:.0%}")}</div>'
             f'<div class="cs">평균 상관 {r.avg_corr:.2f}</div></div>')
    S.append(f'<div class="card {"down" if len(fails) else ""}">'
             f'<div class="cl">한도 위반</div>'
             f'<div class="cv">{len(fails)}/{len(P.limits)}</div>'
             f'<div class="cs">가중치 출처: {E(P.weight_source)}</div></div>')
    S.append('</div>')

    if len(fails):
        S.append(note("위반된 한도: " +
                      ", ".join(f"{E(x)}" for x in fails["한도"]), "bad"))

    # ---- 위험 분해
    S.append('<details class="sec" id="risk" open><summary>'
             '<span class="icon">◑</span><h2>위험 분해 — 비중이 아니라 기여</h2>'
             '</summary><div class="body">')
    S.append(note("비중과 위험기여는 다릅니다. 동일 비중이어도 변동성과 상관에 "
                  "따라 어떤 포지션은 책 위험의 대부분을 차지합니다. "
                  "PM이 보는 것은 비중이 아니라 <b>한계기여위험</b>입니다."))
    S.append(ch.hbar(r.tickers, list(r.pct_contribution), "위험 기여 비중",
                     threshold=0.40))
    S.append(df_table(pd.DataFrame({
        "종목": r.tickers, "비중": r.weights,
        "단독 변동성": r.standalone_vol,
        "한계기여위험": r.marginal, "위험기여": r.contribution,
        "위험기여 비중": r.pct_contribution}), nd=4))
    S.append(kv_table([
        ("책 변동성 (연)", _f(r.vol_ann)),
        ("분산비율", f"{r.diversification_ratio:.2f}",
         "가중평균 개별변동성 / 책 변동성. 1에 가까우면 분산 효과 없음"),
        ("유효 베팅 수", f"{r.effective_bets:.2f}",
         "위험기여 엔트로피 기반. 종목 수보다 훨씬 작으면 실제로는 소수 베팅"),
        ("평균 상관", f"{r.avg_corr:.2f}"),
        ("VaR 95% (일간)", _f(P.var95, "{:.2%}")),
        ("ES 95% (일간)", _f(P.es95, "{:.2%}")),
    ]))
    if r.concentration_flag:
        S.append(note(f"단일 종목이 책 위험의 {r.max_pct_contribution:.0%}를 "
                      f"차지합니다. 종목 수가 많아도 실질적으로는 "
                      f"<b>{r.effective_bets:.1f}개 베팅</b>입니다.", "warn"))
    S.append('</div></details>')

    # ---- 상관
    S.append('<details class="sec" id="corr"><summary>'
             '<span class="icon">⊞</span><h2>상관 구조</h2></summary>'
             '<div class="body">')
    S.append(ch.heatmap(r.corr_matrix))
    S.append(note("정상 국면 상관입니다. 개별 종목 리포트의 <b>하방 꼬리 상관"
                  "</b>(λ_L)과 반드시 함께 보십시오 — 분산 효과는 위기에 "
                  "사라집니다."))
    S.append('</div></details>')

    # ---- 팩터 넷팅
    S.append('<details class="sec" id="netting" open><summary>'
             '<span class="icon">⇄</span><h2>팩터 넷팅 — 상쇄되는가</h2>'
             '</summary><div class="body">')
    S.append(note("개별로는 큰 노출이 책 전체에서 상쇄될 수 있고, 그 반대도 "
                  "가능합니다. <b>넷팅비율 = |순노출| / 총노출</b>이며, "
                  "1에 가까우면 전혀 상쇄되지 않는다는 뜻입니다."))
    if len(nt.table):
        S.append(df_table(nt.table.reset_index(), nd=3))
        mat = nt.gross_by_factor[nt.gross_by_factor > 0.02]
        if len(mat):
            S.append(ch.hbar(list(mat.index),
                             [float(nt.net_by_factor[i]) for i in mat.index],
                             "팩터별 책 순노출 (β)", fmt="{:+.3f}"))
    if nt.note:
        S.append(note(E(nt.note), "warn"))
    S.append('</div></details>')

    # ---- 배분 경합
    S.append('<details class="sec" id="alloc" open><summary>'
             '<span class="icon">⚖</span><h2>배분 규칙 경합 — 워크포워드 + MCS</h2>'
             '</summary><div class="body">')
    S.append(note("각 리밸런싱 시점에서 <b>과거 데이터만으로</b> 가중치를 만들고 "
                  "다음 구간 실현 수익으로 평가합니다. 회전비용도 차감합니다. "
                  "그 뒤 Hansen MCS로 통계적으로 구별되지 않는 규칙 집합을 "
                  "남깁니다."))
    if len(ac.table):
        S.append(df_table(ac.table, nd=4))
        S.append(ch.hbar(list(ac.table["규칙"]), list(ac.table["샤프"]),
                         "워크포워드 샤프 (비용 차감 후)", fmt="{:.2f}"))
    S.append(kv_table([
        ("리밸런싱 횟수", f"{ac.n_rebalances}회"),
        ("MCS 생존 규칙", E(", ".join(ac.mcs_survivors))),
        ("채택", f"<b>{E(ac.winner)}</b>"),
        ("1/N 초과 입증", "예" if ac.beats_1n else "<b>아니오</b>"),
    ]))
    S.append(note(E(ac.note), "info" if ac.beats_1n else "warn"))
    if not ac.beats_1n:
        S.append(note("HRP·최소분산이 표본 내에서 더 높은 샤프를 보여도, "
                      "MCS가 1/N을 제외하지 못하면 그 차이는 <b>통계적으로 "
                      "구별되지 않습니다</b>. 이 경우 더 단순한 규칙을 쓰는 것이 "
                      "옳습니다 — 복잡한 규칙은 추정오차를 더 많이 먹습니다.",
                      "warn"))
    S.append('</div></details>')

    # ---- 책 스트레스
    S.append('<details class="sec" id="stress" open><summary>'
             '<span class="icon">⛨</span><h2>책 레벨 스트레스</h2></summary>'
             '<div class="body">')
    if len(P.stress_table):
        S.append(df_table(P.stress_table, nd=4))
        S.append(ch.hbar(list(P.stress_table["충격"].head(8)),
                         list(P.stress_table["보수적"].head(8)),
                         "충격별 책 손익 (보수적 채택)"))
    ss = P.stress_summary
    S.append(f'<div class="hi">최악: {E(ss.get("worst_scenario","—"))} → '
             f'<b>{_f(ss.get("worst"))}</b> (한도 '
             f'{_f(ss.get("limit"),"{:.0%}")})</div>')
    S.append(note(E(ss.get("note", "")), "warn"))
    S.append('</div></details>')

    # ---- 한도
    S.append('<details class="sec" id="limits" open><summary>'
             '<span class="icon">▣</span><h2>한도 점검</h2></summary>'
             '<div class="body">')
    lt = P.limits.copy()
    lt["충족"] = lt["충족"].map(lambda x: "충족" if x else "위반")
    S.append(df_table(lt, highlight=lambda r_: "" if r_["충족"] == "충족" else "bad"))
    S.append('</div></details>')

    # ---- 구성 종목
    S.append('<details class="sec" id="names"><summary>'
             '<span class="icon">≡</span><h2>구성 종목</h2></summary>'
             '<div class="body">')
    S.append('<p class="muted">각 종목의 상세 분석은 개별 리포트를 '
             '참조하십시오.</p>')
    S.append('</div></details>')

    if P.warnings:
        S.append(note("<ul class='rl'>" +
                      "".join(f"<li>⚠ {E(w)}</li>" for w in P.warnings) +
                      "</ul>", "warn"))

    toc = "".join(
        f'<a href="#{i}">{t}</a>' for i, t in (
            ("risk", "◑ 위험 분해"), ("corr", "⊞ 상관"),
            ("netting", "⇄ 팩터 넷팅"), ("alloc", "⚖ 배분 경합"),
            ("stress", "⛨ 스트레스"), ("limits", "▣ 한도"),
            ("names", "≡ 구성")))

    return f"""<!DOCTYPE html><html lang="ko"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{E(title)} — Plutus 포트폴리오</title><style>{CSS}</style></head><body>
<div class="wrap">
<header><h1>{E(title)} · 책 레벨 분석</h1>
<div class="sub">{len(r.tickers)}개 포지션 · {E(P.weight_source)} ·
책 변동성 {_f(r.vol_ann,"{:.1%}")}</div></header>
<div class="ctl"><button onclick="expandAll()">전체 펼치기</button>
<button onclick="collapseAll()">전체 접기</button></div>
<div class="toc">{toc}</div>
{"".join(S)}
<footer><p>개별 종목 분석은 각 종목 리포트를 참조. 본 산출물은 방법론 검증용
정보 제공이며 투자 자문이 아닙니다.</p></footer>
</div><script>{JS}</script></body></html>"""


def save_portfolio(P, path: str, title: str = "포트폴리오") -> str:
    with open(path, "w", encoding="utf-8") as f:
        f.write(render_portfolio(P, title))
    return path
