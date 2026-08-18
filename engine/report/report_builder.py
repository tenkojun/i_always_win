"""
리포트 생성 모듈
================
종목 분석 결과(dict)를 받아
- JSON  : 기계 판독용
- HTML  : 사람이 읽는 종합 리포트 (단/중/장 비교 + 메트릭 설명 포함)
두 형태로 저장한다.
"""
from __future__ import annotations
import json
import math
from pathlib import Path
from typing import Any, Dict


# ------------------------------------------------------------------ #
# 메트릭 설명 사전 — HTML 리포트에 자동 첨부
# ------------------------------------------------------------------ #
METRIC_GLOSSARY = {
    # 추세
    "sma_slope_pct":    "장기 이동평균의 기울기 (%) — 양수=상승추세",
    "above_sma_ratio":  "구간 동안 종가가 SMA 위에 있던 비율 (0~1)",
    "ma_cross_diff":    "단기MA - 장기MA. 양수=골든크로스",
    "trend_direction":  "추세 방향 (상승/하락)",
    # 모멘텀
    "cum_return_pct":   "구간 누적 수익률 (%)",
    "rsi_last":         "RSI(14) 최종값. 70+ 과매수 / 30- 과매도",
    "rsi_state":        "RSI 해석 결과",
    "roc_20_pct":       "최근 20일 가격 변화율 (%)",
    # 변동성
    "annual_vol":       "연 환산 변동성 (수익률 표준편차 × √252)",
    "realized_20d":     "최근 20일 실현 변동성 (연환산)",
    "vol_regime":       "변동성 국면 (낮음/보통/높음)",
    # 리스크
    "sharpe":           "샤프 비율. > 1 양호 / > 2 우수",
    "sortino":          "하방 변동성만 분모로 쓰는 샤프",
    "calmar":           "CAGR / |MDD|. 손실 1단위당 수익",
    "cagr":             "연복리 환산 수익률",
    "max_drawdown":     "최대 손실폭 (음수, 예: -0.25 = 25%)",
    "dd_duration":     "최장 손실 지속 일수",
    "ulcer_index":      "손실 깊이 × 기간 지표 (작을수록 좋음)",
    "parametric_var":   "정규분포 가정 5% VaR (양수=손실 크기)",
    "historical_var":   "실제 분포 기반 5% VaR",
    "mc_var":           "몬테카를로 5% VaR",
    "cvar":             "Expected Shortfall (5% 영역의 평균 손실)",
    # 오더플로우
    "cvd_last":         "누적 거래량 델타 최종값",
    "cvd_20d_change":   "최근 20일 CVD 변화. 양수=매수 누적",
    "vpin_mean":        "VPIN 평균 (정보거래 비중, 0~1)",
    "vol_imbalance":    "거래량 불균형. +면 매수 / -면 매도 우위",
    # ML
    "model":            "사용한 ML 모델",
    "accuracy":         "분류 정확도 (홀드아웃)",
    "prob_up":          "다음 N일 가격 상승 확률 (0~1)",
    "horizon_d":        "예측 시점(영업일 후)",
    "n_test":           "검증 표본 크기",
    # 시그널
    "score":            "0~100 종합 점수",
    "signal":           "BUY (65+) / HOLD (45~65) / SELL (~45)",
    # 기관급 — 몬테카를로 예측
    "up_prob":          "현재가보다 오를 확률 (%)",
    "exp_return_pct":   "기대 수익률 (%)",
    "median_price":     "예상 중앙값 가격",
    "std_pct":          "종착 수익률 표준편차 (변동성 크기)",
    "prob_up_10":       "+10% 이상 상승 확률 (%)",
    "prob_dn_10":       "-10% 이하 하락 확률 (%)",
    "var_95_pct":       "95% 신뢰수준 최대손실 (%)",
    "cvar_95_pct":      "VaR 초과 구간의 평균 손실 (%)",
    "skew":             "분포 비대칭도 (+면 상승꼬리, -면 하락꼬리)",
    "kurtosis":         "꼬리 두께 (+면 극단값 잦음)",
    # 기관급 — 팩터 위험
    "systematic_pct":   "체계적(시장 등 공통팩터) 위험 비중 (%)",
    "specific_pct":     "종목 고유 위험 비중 (%)",
    "alpha_ann":        "팩터로 설명 안 되는 연환산 초과수익",
    "top_driver":       "위험을 가장 키우는 요인",
    # 기관급 — 부 예측
    "exp_value":        "기대 평가금액",
    "prob_loss":        "원금 손실 확률 (%)",
    "prob_beat_infl":   "물가상승률 초과 확률 (%)",
    # 기관급 — 리스크 버짓
    "suggested_weight": "변동성 타깃 기준 권장 배분 비중",
    "half_kelly":       "하프 켈리 기준 비중",
    "final_weight":     "최종 권장 배분 비중",
}


# ------------------------------------------------------------------ #
def _json_safe(o):
    """JSON 직렬화 가능 형태로 변환."""
    if isinstance(o, dict):
        return {k: _json_safe(v) for k, v in o.items()}
    if isinstance(o, list):
        return [_json_safe(x) for x in o]
    if isinstance(o, float) and (math.isnan(o) or math.isinf(o)):
        return None
    try:
        import numpy as np, pandas as pd
        if isinstance(o, (np.floating,)): return None if np.isnan(o) else float(o)
        if isinstance(o, (np.integer,)):  return int(o)
        if isinstance(o, np.ndarray):     return o.tolist()
        if isinstance(o, pd.Series):      return o.dropna().to_dict()
        if isinstance(o, pd.DataFrame):   return o.dropna(how="all").to_dict()
    except ImportError:
        pass
    return o


def _fmt(v):
    """숫자/문자열 예쁘게."""
    if isinstance(v, bool):
        return "예" if v else "아니오"
    if isinstance(v, float):
        if abs(v) > 100:
            return f"{v:,.2f}"
        return f"{v:.4f}"
    if isinstance(v, int):
        return f"{v:,}"
    return str(v)


def _table(d: Dict[str, Any], with_desc: bool = True) -> str:
    """key/value/설명 3열 테이블."""
    if not d:
        return "<p style='color:#888'>(데이터 없음)</p>"
    rows = []
    for k, v in d.items():
        if isinstance(v, dict):
            v_html = _table(v, with_desc=False)
        else:
            v_html = _fmt(v)
        desc = METRIC_GLOSSARY.get(k, "") if with_desc else ""
        desc_cell = f"<td style='color:#666;font-size:12px'>{desc}</td>" if with_desc else ""
        rows.append(f"<tr><th>{k}</th><td>{v_html}</td>{desc_cell}</tr>")
    header = ("<tr><th>항목</th><th>값</th><th>설명</th></tr>" if with_desc
              else "<tr><th>항목</th><th>값</th></tr>")
    return f"<table>{header}{''.join(rows)}</table>"


def _img(plot: Dict[str, Any]) -> str:
    if not plot or "png_b64" not in plot:
        return ""
    return f"<img src='data:image/png;base64,{plot['png_b64']}'/>"


def _signal_badge(signal: str, score: float) -> str:
    colors = {"BUY": "#4caf50", "HOLD": "#ff9800", "SELL": "#f44336"}
    bg = colors.get(signal, "#888")
    return (f"<span style='background:{bg};color:white;"
            f"padding:6px 14px;border-radius:6px;font-weight:bold'>"
            f"{signal}  {score:.1f}점</span>")


# ------------------------------------------------------------------ #
HTML_TEMPLATE = """<!DOCTYPE html>
<html><head><meta charset='utf-8'>
<title>📊 {ticker} 단/중/장 분석 리포트</title>
<style>
body  {{ font-family: -apple-system, 'Malgun Gothic', '맑은 고딕', sans-serif;
         margin: 24px; color: #222; max-width: 1200px; }}
h1    {{ border-bottom: 3px solid #1a5fb4; padding-bottom: 8px; }}
h2    {{ margin-top: 28px; color: #1a5fb4; border-left: 4px solid #1a5fb4;
         padding-left: 8px; }}
h3    {{ color: #444; }}
table {{ border-collapse: collapse; margin: 8px 0 16px 0; width: 100%; }}
th,td {{ border: 1px solid #ddd; padding: 6px 10px; font-size: 13px;
         text-align: left; }}
th    {{ background: #f0f4f8; width: 25%; }}
.col3 {{ display: flex; gap: 16px; flex-wrap: wrap; }}
.box  {{ flex: 1 1 350px; border: 1px solid #ddd; border-radius: 8px;
         padding: 12px; background: #fafafa; }}
.score-big{{ font-size: 36px; font-weight: bold; }}
img   {{ max-width: 100%; border: 1px solid #eee; margin: 8px 0;
         border-radius: 4px; }}
.reasons {{ background: #fff8e1; border-left: 4px solid #ffa726;
            padding: 10px; margin: 8px 0; }}
.note {{ color: #888; font-style: italic; }}
</style></head>
<body>

<h1>📊 {ticker} 종목 분석 리포트</h1>
<p>분석기간: {period}</p>
<p>{overall_badge} &nbsp;&nbsp; <b>종합 시그널</b></p>

<h2>🏛 기관 스코어카드 (포트폴리오 카드)</h2>
{scorecard_card}

<h2>📝 모듈별 분석 코멘트</h2>
{narrative_section}

<h2>📌 단/중/장 점수 비교</h2>
{score_chart}

<div class='col3'>
{timeframe_boxes}
</div>

<h2>🎲 미래 주가 몬테카를로 (단/중/장)</h2>
{mc_charts}

<h2>🧬 기관 리스크 분석 차트</h2>
{inst_charts}

<h2>📈 보조 차트</h2>
{charts}

<h2>📚 메트릭 용어 사전</h2>
<table>
<tr><th>항목</th><th>설명</th></tr>
{glossary_rows}
</table>

<h2>🔎 권고</h2>
<p>{recommendation}</p>
</body></html>
"""


def _timeframe_box(name: str, tf: Dict[str, Any]) -> str:
    if "note" in tf:
        return f"<div class='box'><h3>{name}</h3><p class='note'>{tf['note']}</p></div>"

    score = tf.get("score", 50)
    signal = tf.get("signal", "HOLD")
    badge = _signal_badge(signal, score)
    reasons_html = "<ul>" + "".join(
        f"<li>{r}</li>" for r in tf.get("reasons", [])
    ) + "</ul>" if tf.get("reasons") else ""

    return f"""
    <div class='box'>
        <h3>{name} ({tf.get('lookback_days', 0)}일)</h3>
        {badge}
        <p style='margin-top:8px'>{tf.get('period_start','')} ~ {tf.get('period_end','')}</p>
        <p>가격 {tf.get('first_price', 0):.2f} → {tf.get('last_price', 0):.2f}
          ({tf.get('momentum',{}).get('cum_return_pct',0):+.2f}%)</p>
        <h4>추세</h4>{_table(tf.get('trend',{}), with_desc=False)}
        <h4>모멘텀</h4>{_table(tf.get('momentum',{}), with_desc=False)}
        <h4>변동성</h4>{_table(tf.get('volatility',{}), with_desc=False)}
        <h4>리스크</h4>{_table(tf.get('risk',{}), with_desc=False)}
        <h4>오더플로우</h4>{_table(tf.get('orderflow',{}), with_desc=False)}
        <h4>ML 예측</h4>{_table(tf.get('ml',{}), with_desc=False)}
        <h4>국면</h4>{_table(tf.get('regime',{}), with_desc=False)}
        <div class='reasons'><b>판단 근거:</b>{reasons_html}</div>
    </div>
    """


def _grade_color(grade: str) -> str:
    table = {
        "A+": "#1b5e20", "A": "#2e7d32", "B+": "#558b2f",
        "B": "#9e9d24", "C": "#ef6c00", "D": "#c62828",
    }
    return table.get(grade, "#666")


def _scorecard_card(sc: Dict[str, Any]) -> str:
    """기관 스코어카드 — 포트폴리오 카드 형태."""
    if not sc or "pillars" not in sc:
        return ""
    g = sc.get("overall_grade", "C")
    s = sc.get("overall_score", 50)
    gc = _grade_color(g)

    rows = ""
    for name, p in sc["pillars"].items():
        pscore = p["score"]
        pg = p["grade"]
        bar_w = max(2, min(100, pscore))
        bar_c = ("#2e7d32" if pscore >= 65 else
                 "#ef6c00" if pscore >= 45 else "#c62828")
        rows += f"""
        <tr>
          <td style='width:130px;font-weight:600'>{name}</td>
          <td style='width:90px'>
            <div style='background:#eee;border-radius:4px;height:14px;width:140px'>
              <div style='background:{bar_c};height:14px;border-radius:4px;
                          width:{bar_w}%'></div></div>
          </td>
          <td style='width:48px;text-align:center;font-weight:700;
                     color:{_grade_color(pg)}'>{pg}</td>
          <td style='width:52px;text-align:right'>{pscore:.0f}점</td>
          <td style='color:#555;font-size:12px'>{p.get('comment','')}</td>
        </tr>"""

    return f"""
    <div style='border:2px solid {gc};border-radius:12px;padding:18px;
                margin:12px 0;background:linear-gradient(135deg,#fafcff,#eef3fb)'>
      <div style='display:flex;align-items:center;gap:20px;flex-wrap:wrap'>
        <div style='text-align:center;min-width:140px'>
          <div style='font-size:13px;color:#666'>기관 종합 등급</div>
          <div style='font-size:54px;font-weight:800;color:{gc};
                      line-height:1.1'>{g}</div>
          <div style='font-size:20px;font-weight:700'>{s:.1f}<span
               style='font-size:13px;color:#888'>/100</span></div>
        </div>
        <div style='flex:1;min-width:300px'>
          <table style='border:none'>
            <tbody style='border:none'>{rows}</tbody>
          </table>
        </div>
      </div>
      <div style='margin-top:14px;padding:10px 14px;background:#1a5fb4;
                  color:white;border-radius:8px;font-size:14px'>
        <b>📋 기관 의견:</b> {sc.get('verdict','')}
      </div>
    </div>"""


def _narrative_section(narr: Dict[str, str]) -> str:
    """모듈별 한글 분석 글 블록."""
    if not narr:
        return ""
    blocks = [
        ("🎲 몬테카를로 미래 주가 예측", "montecarlo"),
        ("🧬 팩터 위험 분해 (Aladdin식)", "factor_risk"),
        ("🌪 시나리오 스트레스 테스트", "stress"),
        ("💰 몬테카를로 부(富) 예측", "wealth"),
        ("⚖️ 자산배분 · 리스크 버짓", "risk_budget"),
        ("📈 추세", "trend"),
        ("⚡ 모멘텀", "momentum"),
        ("📉 변동성", "volatility"),
        ("🛡 리스크", "risk"),
        ("💹 오더플로우", "orderflow"),
        ("🤖 머신러닝 예측", "ml"),
        ("🔀 시장 국면", "regime"),
    ]
    html = ""
    for label, key in blocks:
        txt = narr.get(key)
        if not txt:
            continue
        html += f"""
        <div style='border-left:4px solid #1a5fb4;background:#f7f9fc;
                    padding:10px 16px;margin:10px 0;border-radius:0 8px 8px 0'>
          <div style='font-weight:700;color:#1a5fb4;margin-bottom:4px'>{label}</div>
          <div style='font-size:14px;line-height:1.7;color:#333'>{txt}</div>
        </div>"""
    return html


def build_report(results: Dict[str, Any],
                 out_dir: str = "./report_out") -> Dict[str, str]:
    """
    Parameters
    ----------
    results : analyze_ticker 의 결과 dict
        - ticker
        - timeframes: {단기: {...}, 중기: {...}, 장기: {...}}
        - overall_score / overall_signal / overall_color
        - plots: {name: plot_dict}     (옵션)
    """
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    ticker = results.get("ticker", "TICKER")
    tfs = results.get("timeframes", {})

    # 기간 표시 - 가장 긴 타임프레임 기준
    period = ""
    for n in ["장기", "중기", "단기"]:
        if n in tfs and "period_start" in tfs[n]:
            period = f"{tfs[n]['period_start']} ~ {tfs[n]['period_end']}"
            break

    # 종합 시그널
    overall_signal = results.get("overall_signal", "HOLD")
    overall_score  = results.get("overall_score", 50)
    overall_badge  = _signal_badge(overall_signal, overall_score)

    # 점수 막대 차트
    plots = results.get("plots", {})
    score_chart = _img(plots.get("score_bars", {}))

    # 기관 스코어카드 + 모듈별 분석글
    inst = results.get("institutional", {})
    scorecard_card = _scorecard_card(inst.get("scorecard", {}))
    narrative_section = _narrative_section(inst.get("narratives", {}))

    # 미래 주가 몬테카를로 차트 (단/중/장)
    mc_charts = ""
    if plots.get("mc_multi"):
        mc_charts += _img(plots["mc_multi"])
    for key, label in [("mc_dist_단기", "단기 분포"),
                        ("mc_dist_중기", "중기 분포"),
                        ("mc_dist_장기", "장기 분포")]:
        if plots.get(key):
            mc_charts += f"<h3>{label}</h3>{_img(plots[key])}"
    if not mc_charts:
        mc_charts = "<p class='note'>(몬테카를로 차트 없음)</p>"

    # 기관 리스크 차트
    inst_charts = ""
    for key, label in [("scorecard", "기관 스코어카드 레이더"),
                        ("factor_risk", "팩터 위험 분해"),
                        ("stress", "시나리오 스트레스 테스트")]:
        if plots.get(key):
            inst_charts += f"<h3>{label}</h3>{_img(plots[key])}"
    if not inst_charts:
        inst_charts = "<p class='note'>(기관 차트 없음)</p>"

    # 단/중/장 박스
    timeframe_boxes = "\n".join(
        _timeframe_box(name, tfs.get(name, {}))
        for name in ["단기", "중기", "장기"]
    )

    # 추가 차트들
    charts_html = ""
    for key, label in [
        ("price", "가격 + 이동평균"),
        ("drawdown", "낙폭"),
        ("returns_hist", "수익률 분포"),
        ("cvd", "CVD"),
        ("regime", "시장 국면"),
        ("montecarlo", "몬테카를로"),
    ]:
        if key in plots and plots[key]:
            charts_html += f"<h3>{label}</h3>{_img(plots[key])}"

    # 용어 사전
    glossary_rows = "\n".join(
        f"<tr><th>{k}</th><td>{v}</td></tr>"
        for k, v in METRIC_GLOSSARY.items()
    )

    recommendation = results.get(
        "recommendation",
        f"종합 점수 {overall_score:.1f}점 → {overall_signal}. "
        "단기·중기·장기 시그널이 일치할수록 신뢰도가 높습니다. "
        "본 분석은 정보 제공 목적이며 투자 권유가 아닙니다."
    )

    html = HTML_TEMPLATE.format(
        ticker=ticker,
        period=period,
        overall_badge=overall_badge,
        scorecard_card=scorecard_card,
        narrative_section=narrative_section,
        score_chart=score_chart,
        timeframe_boxes=timeframe_boxes,
        mc_charts=mc_charts,
        inst_charts=inst_charts,
        charts=charts_html,
        glossary_rows=glossary_rows,
        recommendation=recommendation,
    )

    html_path = out / f"{ticker}_report.html"
    json_path = out / f"{ticker}_report.json"
    html_path.write_text(html, encoding="utf-8")

    payload = {k: v for k, v in results.items() if k != "plots"}
    # 기관 분석의 대용량 배열(시뮬 경로 등)은 JSON 에서 제외 — 통계만 유지
    if "institutional" in payload:
        inst_clean = {}
        for sec, val in payload["institutional"].items():
            if sec == "mc_tf" and isinstance(val, dict):
                inst_clean["mc_tf"] = {}
                for tfn, mc in val.items():
                    if isinstance(mc, dict):
                        inst_clean["mc_tf"][tfn] = {
                            k: v for k, v in mc.items()
                            if k not in ("paths_sample", "terminal", "pctl")
                        }
                    else:
                        inst_clean["mc_tf"][tfn] = mc
            else:
                inst_clean[sec] = val
        payload = {**payload, "institutional": inst_clean}
    json_path.write_text(
        json.dumps(_json_safe(payload), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return {"html": str(html_path), "json": str(json_path),
            "score": overall_score, "signal": overall_signal}
