# ==============================================================================
# [25/25] report.py — 마크다운 렌더러
# ==============================================================================

"""
jiqtx.report — 분석 결과를 마크다운 리포트로 렌더링.

원본 리포트와의 출력 차이
-------------------------
원본: 단일 점수 1개 (52.9점) + 감점 사유
Plutus: 3축 분리 — 방향확률 / 리스크예산 / 모델신뢰도
        그리고 '기권'이 정상 출력이다.
"""

import math
from typing import Optional

import numpy as np
import pandas as pd


def _f(x, fmt="{:.2%}", na="—"):
    try:
        if x is None or (isinstance(x, float) and not np.isfinite(x)):
            return na
        return fmt.format(x)
    except Exception:
        return na


def _md_table(df: pd.DataFrame, cols=None, rename=None, floatfmt=3) -> str:
    if df is None or len(df) == 0:
        return "_(데이터 없음)_\n"
    d = df[cols].copy() if cols else df.copy()
    if rename:
        d = d.rename(columns=rename)
    d = d.round(floatfmt)
    head = "| " + " | ".join(str(c) for c in d.columns) + " |"
    sep = "|" + "---|" * len(d.columns)
    rows = ["| " + " | ".join("" if pd.isna(v) else str(v) for v in row) + " |"
            for row in d.values]
    return "\n".join([head, sep] + rows) + "\n"


def render(a) -> str:
    """Analysis → 마크다운 문자열."""
    v = a.verdict
    cls = a.classification
    fp = cls.fingerprint
    spec = cls.spec
    L: list = []
    add = L.append

    # ============================================== 헤더
    add(f"# {a.ticker} — Plutus 정밀 분석 리포트\n")
    add(f"**기준일** {a.asof} · **자산군** {spec.label_ko} "
        f"(신뢰도 {cls.confidence:.0%}) · **연율화** √{spec.ann_factor}\n")

    grade_desc = {
        "BUY": "매수", "ACCUMULATE": "분할 매집", "HOLD": "보유/관망",
        "REDUCE": "비중 축소", "AVOID": "신규 진입 불가", "ABSTAIN": "판단 보류"}
    add("\n## 최종 판정 — 3축 분리\n")
    add("> 단일 점수는 산출하지 않습니다. 서로 다른 성질의 정보를 하나의 숫자로 "
        "합치면 정보가 파괴되기 때문입니다.\n")
    add("| 축 | 값 | 의미 |")
    add("|---|---|---|")
    add(f"| **방향 등급** | **{v.grade}** ({grade_desc.get(v.grade,'')}) | "
        f"확률 {_f(v.direction_prob, '{:.1%}')} · "
        f"CI [{_f(v.direction_ci[0],'{:.1%}')}, {_f(v.direction_ci[1],'{:.1%}')}] |")
    add(f"| **리스크 예산** | **{_f(v.risk_budget_weight,'{:.1%}')}** | "
        f"구속 제약: {a.sizing.binding_constraint if a.sizing else '—'} |")
    add(f"| **모델 신뢰도** | **{v.model_confidence}** | "
        f"무효화 모듈 {len(v.disabled_modules)}개"
        f"{': ' + ', '.join(v.disabled_modules) if v.disabled_modules else ''} |")
    add("")
    for x in v.rationale:
        add(f"- {x}")
    if v.vetoes:
        add(f"\n> ⛔ **거부권 발동**: {', '.join(v.vetoes)}")
    add("")

    # ============================================== 게이트
    add("\n## 1. 하드 게이트 — 게이트 실패는 감점이 아니라 무효화\n")
    add("> 원본 리포트는 DSR 84%·과적합 갭 49%p를 **알면서도** 점수를 냈습니다. "
        "OOS 50%는 약한 신호가 아니라 신호 없음이고, 올바른 출력은 감점된 점수가 "
        "아니라 **출력 없음**입니다.\n")
    add(_md_table(a.gates.table()))

    # ============================================== 자산 특성
    add("\n## 2. 자산 특성 지문 — 이 종목은 어떤 렌즈로 봐야 하는가\n")
    add("| 항목 | 값 | 판정 근거 |")
    add("|---|---|---|")
    add(f"| 자산군 | {spec.label_ko} | {'; '.join(cls.evidence[:2]) or '—'} |")
    add(f"| quoteType / 섹터 | {cls.quote_type or '—'} / {cls.sector or '—'} | |")
    add(f"| 연율화 기준 | √{spec.ann_factor} | "
        f"{'주말 거래 관측 → 24/7 시장' if fp.trades_weekends else '영업일 기준'} |")
    add(f"| 연변동성 | {_f(fp.ann_vol)} | 왜도 {fp.skew:+.2f}, "
        f"첨도 {fp.kurtosis:.1f} |")
    add(f"| 1차 자기상관 | {fp.autocorr1:+.3f} | "
        f"{'⚠ 평활화 의심 → 샤프 과대' if fp.smoothing_suspected else '정상'} |")
    add(f"| 최적 프록시 | {fp.best_proxy or '—'} | "
        f"β={fp.best_beta:+.2f}, R²={_f(fp.best_r2,'{:.1%}')} |")
    add(f"| 레버리지 탐지 | "
        f"{f'{fp.leverage_detected:+.0f}x' if fp.leverage_detected else '없음'} | "
        f"{'경로의존 — 변동성 드래그 반영' if fp.leverage_detected else ''} |")
    add(f"| 이력 | {fp.n_obs}영업일 (약 {fp.n_obs/252:.1f}년) | "
        f"요구 {spec.min_history_days}일 |")
    if spec.notes:
        add(f"\n> **{spec.label_ko} 고유 주의사항**: {spec.notes}\n")
    if cls.warnings:
        add("\n**분류 경고**")
        for w in cls.warnings:
            add(f"- ⚠ {w}")
    add("")

    # ============================================== 유동성
    liq = a.liquidity
    add("\n## 3. 유동성·거래비용 — VPIN 프록시를 대체하는 정당한 지표\n")
    add("> 일봉 OHLCV로 만든 VPIN/CVD 프록시는 체결방향을 모르므로 정보 함량이 "
        "사실상 0입니다. EDGE(Ardia-Guidotti-Kroencke, JFE 2024)는 OHLC 전부를 "
        "최적 결합해 거래가 희소해도 편향되지 않습니다.\n")
    add("| 지표 | 값 |")
    add("|---|---|")
    add(f"| **EDGE 유효스프레드** | {_f(liq.spread_bps,'{:.1f}')} bp |")
    add(f"| 교차검증 (CS / CHL / Roll) | {_f(liq.spread_cs*1e4,'{:.0f}')} / "
        f"{_f(liq.spread_chl*1e4,'{:.0f}')} / {_f(liq.spread_roll*1e4,'{:.0f}')} bp |")
    add(f"| 추정량 간 산포 | {_f(liq.spread_dispersion*1e4,'{:.0f}')} bp "
        f"← 측정 불확실성 |")
    add(f"| Amihud 비유동성 | {_f(liq.amihud,'{:.4f}')} |")
    add(f"| Kyle-Obizhaeva | {_f(liq.kyle_obizhaeva,'{:.5f}')} |")
    add(f"| 무거래일 비율 | {_f(liq.zero_ret_ratio,'{:.1%}')} |")
    add(f"| ADV (중앙값) | ${liq.adv_usd:,.0f} |" if np.isfinite(liq.adv_usd)
        else "| ADV | — |")
    add(f"| 1% AUM 청산 소요 | {_f(liq.days_to_liquidate_1pct_aum,'{:.1f}')} 일 |")
    add(f"\n**용량(capacity) 곡선** — 알파가 소멸하는 AUM 규모\n")
    add(_md_table(a.capacity,
                  rename={"aum_usd": "AUM($)", "participation": "ADV대비 참여율",
                          "roundtrip_cost": "왕복비용", "net_alpha_ann": "순알파(연)"},
                  floatfmt=4))
    add("_임팩트는 제곱근 법칙 G ≈ Y·σ_d·√(Q/ADV) 기준. 선형 모델은 대형 주문 "
        "비용을 극심하게 과소평가합니다._\n")

    # ============================================== 성과·리스크
    p = a.perf
    add("\n## 4. 성과·리스크 요약\n")
    add("| 지표 | 값 | | 지표 | 값 |")
    add("|---|---|---|---|---|")
    add(f"| CAGR | {_f(p.get('cagr'))} | | 최대낙폭 | {_f(p.get('max_drawdown'))} |")
    add(f"| 연변동성 | {_f(p.get('vol_ann'))} | | CDaR 95% | {_f(p.get('cdar_95'))} |")
    add(f"| 샤프 | {_f(p.get('sharpe'),'{:.2f}')} | | Ulcer | "
        f"{_f(p.get('ulcer'),'{:.1f}')} |")
    add(f"| 소르티노 | {_f(p.get('sortino'),'{:.2f}')} | | 최장 수중기간 | "
        f"{p.get('longest_underwater_days','—')} 일 |")
    add(f"| 칼마 | {_f(p.get('calmar'),'{:.2f}')} | | 승률 | "
        f"{_f(p.get('hit_rate'),'{:.1%}')} |")
    add(f"\n> {a.drawdown.recovery_note}\n")

    # 샤프 유의성
    sr = p.get("sharpe")
    if sr and np.isfinite(sr) and abs(sr) > 1e-6:
        yrs_needed = (3.0 / abs(sr)) ** 2
        add(f"> **샤프 유의성**: 샤프 {sr:.2f}를 Harvey-Liu-Zhu 팩터 허들 "
            f"(t > 3.0)로 입증하려면 **{yrs_needed:.1f}년**의 표본이 필요합니다. "
            f"현재 표본은 {p.get('n_obs',0)/252:.1f}년 → "
            f"t ≈ {abs(sr)*math.sqrt(p.get('n_obs',0)/252):.2f}.\n")

    # ============================================== 변동성
    vp = a.vol_profile
    g = vp.garch
    add("\n## 5. 조건부 변동성 — 단일 표준편차가 아니라 동학\n")
    add("| 항목 | 값 |")
    add("|---|---|")
    add(f"| GJR-GARCH(1,1)-t 현재 변동성 | {_f(g.ann_vol_current)} |")
    add(f"| 장기 평균 변동성 | {_f(g.ann_vol_longrun)}"
        f"{' ⚠ IGARCH 근방(지속성≈1)' if g.at_boundary else ''} |")
    add(f"| 최근 21일 실현 | {_f(vp.realized_21d_ann)} |")
    add(f"| HAR 예측 (Corsi 2009) | {_f(vp.har.forecast_ann)} "
        f"(R²={_f(vp.har.r2,'{:.2f}')}) |")
    add(f"| 지속성 α+γ/2+β | {_f(g.persistence,'{:.3f}')} "
        f"(반감기 {_f(g.halflife_days,'{:.0f}')}일) |")
    add(f"| 레버리지 효과 γ | {_f(g.gamma,'{:.3f}')} "
        f"← 하락 충격이 변동성을 더 키우는 정도 |")
    add(f"| t 자유도 ν | {_f(g.nu,'{:.1f}')} ← 작을수록 팻테일 |")
    add(f"| 변동성의 변동성 | {_f(vp.vol_of_vol)} |")
    add(f"| 현재 변동성 백분위 | {_f(vp.vol_percentile,'{:.0%}')} |")
    us = vp.unsmoothing
    if us.get("applied"):
        add(f"\n> ⚠ **평활화 보정**: 언스무딩 시 변동성이 "
            f"{us['vol_inflation']:.2f}배 확대. 보고된 샤프는 그만큼 과대평가입니다.\n")

    # ============================================== 레짐
    if a.regime is not None:
        rg = a.regime
        add("\n## 6. 시장 국면 — 번호가 아니라 이름\n")
        add("> K-means의 라벨 0·1·2는 강약 순서를 뜻하지 않아 경제적 해석이 "
            "불가능합니다. Statistical Jump Model은 전환마다 점프 페널티를 부과해 "
            "지속성을 강제하고, 각 국면에 경제적 이름을 붙입니다.\n")
        cols = [c for c in ["label", "share", "mean_ann", "vol_ann", "sharpe",
                            "hit_rate", "worst_day"] if c in rg.stats.columns]
        cols += [c for c in rg.stats.columns if c.startswith("macro_")]
        add(_md_table(rg.stats, cols,
                      {"label": "국면", "share": "비중", "mean_ann": "연수익",
                       "vol_ann": "연변동성", "sharpe": "샤프",
                       "hit_rate": "승률", "worst_day": "최악일"}))
        add(f"\n**현재 국면**: `{rg.labels[rg.current_state]}` "
            f"(확률 {max(rg.current_probs.values()):.0%}, "
            f"기대 지속 {rg.expected_duration[rg.current_state]:.0f}영업일)\n")
        add(f"_점프 페널티 λ={rg.jump_penalty:.0f}, 관측 구간 전환 "
            f"{rg.n_switches}회_\n")

    # ============================================== 팩터
    fm = a.factor_model
    add("\n## 7. 팩터 모델 — 자산군에 맞는 렌즈인가\n")
    if fm is None:
        add("_팩터 데이터 없음._\n")
    else:
        add(f"**선택된 팩터**: {', '.join(fm.used_factors) or '없음'} "
            f"(Elastic-Net 선택 → OLS/Newey-West 재추정)\n")
        add("| 팩터 | 계수 | t (HAC) |")
        add("|---|---|---|")
        for k, c in sorted(fm.coefs.items(), key=lambda kv: -abs(kv[1])):
            add(f"| {k} | {c:+.4f} | {fm.tstats.get(k, float('nan')):+.2f} |")
        add(f"\n| 항목 | 값 |")
        add("|---|---|")
        add(f"| R² | **{_f(fm.r2,'{:.1%}')}** |")
        add(f"| 자산군 기대밴드 | {fm.r2_band[0]:.0%} ~ {fm.r2_band[1]:.0%} |")
        add(f"| 연환산 알파 | {_f(fm.alpha_ann)} (t={_f(fm.alpha_t,'{:.2f}')}) |")
        add(f"| 알파 해석 허용 | {'예' if fm.interpretation_allowed else '**아니오**'} |")
        add(f"\n> {fm.mismatch_note}\n")
        if fm.mismatch:
            add("> 이것이 원본 리포트의 핵심 오류 지점입니다. GLD에 주식형 FF "
                "회귀를 돌려 R²=2%가 나왔고, 잔차 98%를 '종목 고유위험'으로 "
                "해석했습니다. 실제로는 **누락 변수**이며, R²가 2%인 회귀의 "
                "알파(+10.3%)는 해석 불가능한 잔차 평균입니다.\n")

    # ============================================== 델타 패널
    add("\n## 8. 델타 패널 — 헤지펀드가 실제로 보는 수치\n")
    add("> '샤프 0.96' 같은 요약통계가 아니라 **\"무엇이 X만큼 움직이면 얼마를 "
        "잃는가\"**. 정적 베타가 아니라 시변 베타를 쓰고, 하방 분위 베타를 "
        "나란히 표시합니다.\n")
    if len(a.delta_panel):
        d = a.delta_panel.copy()
        d["Δ(표준충격)"] = d["delta_pct"].map(lambda x: f"{x:+.2%}")
        d["Δ(하방베타)"] = d["delta_pct_downside"].map(lambda x: f"{x:+.2%}")
        d["β_now"] = d["beta_now"].round(3)
        d["β 안정성(CV)"] = d["beta_stability_cv"].round(2)
        d["하방β"] = d["downside_beta"].round(3)
        d["t"] = d["t_stat"].round(1)
        d["상관(전체→하방꼬리)"] = d.apply(
            lambda r_: f"{r_['corr_all']:+.2f} → {r_['corr_lower_tail']:+.2f}", axis=1)
        d["λ_L"] = d["lambda_lower"].round(2)
        d["R²(현재)"] = d["r2_now"].map(lambda x: f"{x:.1%}" if np.isfinite(x) else "—")
        add(_md_table(d, ["shock_label", "Δ(표준충격)", "Δ(하방베타)", "β_now",
                          "β 안정성(CV)", "하방β", "t", "상관(전체→하방꼬리)",
                          "λ_L", "R²(현재)", "note"],
                      {"shock_label": "충격", "note": "경보"}))
        add("\n**읽는 법**")
        add("- `Δ(하방베타)`가 `Δ(표준충격)`보다 크면 **하방에서 노출이 확대**되는 "
            "비대칭 자산입니다. 정적 베타 스트레스는 이걸 놓칩니다.")
        add("- `상관(전체→하방꼬리)`: 정상 국면 상관과 극단 국면 상관은 다른 "
            "숫자입니다. 분산투자 효과가 위기에 사라지는지 여기서 보입니다.")
        add("- `β 안정성(CV)` > 0.8 이면 그 베타를 헤지 비율로 쓰면 안 됩니다.")
        add("- `R²(현재)` 붕괴는 버그가 아니라 **구조 변화 신호**입니다. "
            "금-10년TIPS R²가 2005–2021 약 84%에서 2022년 이후 한 자릿수로 "
            "무너진 것이 대표 사례입니다.\n")
    else:
        add("_델타 패널 산출 불가 (팩터 데이터 부족)._\n")

    # 시변 베타 상세
    if a.tvb:
        add("\n**시변 베타 상세**\n")
        add("| 팩터 | 현재 β | 평균 β | 표준편차 | CV | 현재 R² | 경보 |")
        add("|---|---|---|---|---|---|---|")
        for k, t in a.tvb.items():
            add(f"| {k} | {t.beta_now:+.3f} | {t.beta_mean:+.3f} | "
                f"{t.beta_std:.3f} | {_f(t.stability_cv,'{:.2f}')} | "
                f"{_f(t.r2_now,'{:.1%}')} | {t.collapse_note or '—'} |")
        add("")

    # ============================================== ML
    add("\n## 9. 방향 예측 — 핵심 기능은 예측이 아니라 기권\n")
    if a.ml is None:
        add("_ML 모듈 미실행._\n")
    else:
        m = a.ml
        badge = "✅ SIGNAL" if m.verdict == "SIGNAL" else "⛔ ABSTAIN"
        add(f"### 판정: {badge}\n")
        add("| 진단 | 값 | 임계 |")
        add("|---|---|---|")
        add(f"| 승자 모델 | {m.model_name} | 선형 벤치마크와 경합 |")
        add(f"| OOS 정확도 | {_f(m.oos_accuracy,'{:.1%}')} | 기저율 "
            f"{_f(m.base_rate,'{:.1%}')} |")
        add(f"| In-sample 정확도 | {_f(m.in_sample_accuracy,'{:.1%}')} | |")
        add(f"| **과적합 갭** | **{_f(m.overfit_gap,'{:+.1%}')}** | < 15%p |")
        add(f"| Brier score | {_f(m.brier,'{:.4f}')} | |")
        add(f"| **Murphy Resolution** | **{_f(m.resolution,'{:.5f}')}** | > 0 |")
        add(f"| Brier skill | {_f(m.brier_skill,'{:+.4f}')} | > 0 |")
        add(f"| Reliability | {_f(m.reliability,'{:.5f}')} | 작을수록 좋음 |")
        add(f"| PBO | {_f(m.pbo,'{:.1%}')} | 소프트 50% / 하드 75% |")
        add(f"| 전략 샤프 | {_f(m.strategy_sharpe,'{:.2f}')} | |")
        add(f"| **전략 DSR** | **{_f(m.strategy_dsr,'{:.1%}')}** | > 90% |")
        add(f"| 유효 라벨 수 | {m.n_labeled} | > 300 |")
        add(f"| 시행 횟수 (로깅) | {m.n_trials_used} | DSR 보정에 사용 |")
        add("")
        add("> **Murphy 분해**: Brier = Reliability − Resolution + Uncertainty. "
            "Resolution ≈ 0 이면 모델이 기저율 이상의 정보를 담고 있지 않다는 "
            "**정량적 증명**입니다. 이 한 숫자가 '상승확률 66%'류 출력의 유효성을 "
            "판정합니다.\n")
        if m.reasons:
            add("**판정 사유**")
            for x in m.reasons:
                add(f"- {x}")
            add("")
        if m.verdict == "SIGNAL":
            add(f"### ★ 보정된 상승확률: **{m.prob_up_now:.1%}** "
                f"(CI [{m.prob_ci[0]:.1%}, {m.prob_ci[1]:.1%}])\n")
            if len(m.reliability_tbl):
                add("**신뢰도 다이어그램 (예측 vs 실현)**\n")
                add(_md_table(m.reliability_tbl,
                              rename={"bin": "확률구간", "n": "n",
                                      "pred_mean": "예측평균", "obs_freq": "실현빈도"}))
        else:
            add("> 게이트를 통과하지 못했으므로 **확률을 출력하지 않습니다.** "
                "감점된 점수를 내는 것은 없는 정보를 있는 것처럼 만드는 일입니다.\n")
        if len(m.feature_importance):
            top = m.feature_importance.head(8)
            add(f"\n_상위 피처: {', '.join(top.index)}_\n")

    # ============================================== 시뮬레이션
    s = a.sim
    add("\n## 10. 분포 시뮬레이션 — GBM 폐기\n")
    add(f"**엔진**: {s.engine} · {s.n_sims:,}회\n")
    add("### 10.1 드리프트가 왜 문제인가\n")
    add("| 항목 | 값 |")
    add("|---|---|")
    add(f"| 표본 드리프트 μ̂ | {_f(s.drift.mu_hat_ann,'{:+.1%}')} |")
    add(f"| 표준오차 SE(μ̂)=σ/√T | {_f(s.drift.se_ann,'{:.1%}')} |")
    add(f"| 95% 신뢰구간 | [{_f(s.drift.ci95[0],'{:+.1%}')}, "
        f"{_f(s.drift.ci95[1],'{:+.1%}')}] |")
    add(f"| 사후 드리프트 (축소 {s.drift.shrink:.0%}) | "
        f"{_f(s.drift.mu_post_ann,'{:+.1%}')} |")
    add(f"\n> {s.drift.note}\n")
    add("### 10.2 상승확률: 가정을 풀면 무엇이 남는가\n")
    add("| 방식 | 1년 상승확률 |")
    add("|---|---|")
    add(f"| 드리프트 고정 + 정규 (원본 리포트 방식) | "
        f"{_f(s.prob_up_naive_gbm,'{:.1%}')} |")
    add(f"| **FHS-GARCH + EVT + 파라미터 불확실성** | "
        f"**{_f(s.prob_up,'{:.1%}')}** |")
    add(f"\n_불확실성 분해: 전체 로그분산 중 파라미터 무지 기여 "
        f"{_f(s.uncertainty_decomposition['param_share'],'{:.0%}')}, "
        f"시장 변동성 기여 "
        f"{_f(s.uncertainty_decomposition['market_share'],'{:.0%}')}_\n")
    add("### 10.3 분포 요약\n")
    add("| 항목 | 값 |")
    add("|---|---|")
    add(f"| 중앙값 | {s.median_price:,.2f} |")
    add(f"| 90% 시뮬레이션 구간 | {s.q05:,.2f} ~ {s.q95:,.2f} |")
    add(f"| VaR 95% (종착수익) | {_f(s.var95_pct)} |")
    add(f"| CVaR 95% | {_f(s.cvar95_pct)} |")
    add(f"| P(경로 중 −20% 낙폭) | {_f(s.prob_dd_20,'{:.1%}')} |")
    add(f"| 경로 최대낙폭 중앙값 | {_f(s.max_dd_median)} |")
    if s.tail.ok:
        add(f"| GPD 꼬리 형상 ξ (하방/상방) | {s.tail.xi_lo:+.3f} / "
            f"{s.tail.xi_hi:+.3f} |")
    add(f"\n_주의: 90% 구간은 통계적 신뢰구간이나 목표주가가 아니라 모델 가정 "
        f"하의 시뮬레이션 분위입니다. VaR은 최대손실이 아니라 하위 5% 경계값입니다._\n")
    if s.notes:
        for n in s.notes:
            add(f"- {n}")
        add("")

    # ============================================== 리스크
    vr = a.var
    add("\n## 11. VaR / ES — 커버리지 검정을 통과했는가\n")
    add("| 모델 | VaR 95% | ES 95% | 실현 위반율 | Kupiec p | 독립성 p | 조건부 p |")
    add("|---|---|---|---|---|---|---|")
    for name, key, vv, ee in (("정규", "normal", vr.var_normal, np.nan),
                              ("히스토리컬", "historical", vr.var_historical,
                               vr.es_historical),
                              ("FHS-EVT", "fhs_evt", vr.var_fhs_evt, vr.es_fhs_evt)):
        b = vr.backtest.get(key, {})
        add(f"| {name} | {_f(vv)} | {_f(ee)} | {_f(b.get('hit_rate'),'{:.1%}')} | "
            f"{_f(b.get('kupiec_p'),'{:.3f}')} | "
            f"{_f(b.get('independence_p'),'{:.3f}')} | "
            f"{_f(b.get('cc_p'),'{:.3f}')} |")
    add(f"\n**채택 모델**: `{vr.preferred}` — {vr.note}\n")

    add("\n## 12. 스트레스 — 주식 베타 곱셈이 아니라 자산군 고유 충격\n")
    add("> 원본 리포트는 '주식베타 0.20 × 지수충격'으로 스트레스를 만들었습니다. "
        "금에 주식 베타를 곱하는 것은 의미가 없습니다. 이 엔진은 자산군별 "
        "리스크팩터에 직접 충격을 가합니다.\n")
    if len(a.stress_table):
        add(_md_table(a.stress_table,
                      ["scenario", "shocks", "pnl_static", "pnl_downside",
                       "pnl_conservative"],
                      {"scenario": "시나리오", "shocks": "충격",
                       "pnl_static": "정적β 손익", "pnl_downside": "하방β 손익",
                       "pnl_conservative": "보수적 채택"}))
    add(f"\n**최악 시나리오**: {a.stress_summary.get('worst_scenario','—')} → "
        f"{_f(a.stress_summary.get('worst_pnl'))} "
        f"(한도 {_f(a.stress_summary.get('limit'),'{:.0%}')})\n")
    add(f"_{a.stress_summary.get('note','')}_\n")

    # ============================================== 사이징
    sz = a.sizing
    add("\n## 13. 포지션 사이징 — 켈리 200%가 나오는 이유\n")
    if sz:
        add("| 방식 | 비중 |")
        add("|---|---|")
        add(f"| 단순 켈리 μ/σ² | {_f(sz.kelly_naive,'{:.0%}')} |")
        add(f"| **낙폭제약 켈리** | **{_f(sz.kelly_uncertainty_adjusted,'{:.0%}')}** |")
        add(f"| 변동성 타깃 | {_f(sz.vol_target_weight,'{:.0%}')} |")
        add(f"| 스트레스 예산 한도 | {_f(sz.stress_cap,'{:.0%}')} |")
        add(f"| 유동성 한도 | {_f(sz.liquidity_cap,'{:.0%}')} |")
        add(f"| 자산군 상한 | {_f(sz.class_cap,'{:.0%}')} |")
        add(f"| **최종 비중** | **{_f(sz.final_weight,'{:.1%}')}** |")
        add(f"\n**구속 제약**: {sz.binding_constraint}\n")
        add(f"> {sz.note}\n")
        add("> 켈리 공식 자체가 아니라 **μ를 안다고 가정한 것**이 문제입니다. "
            "성장 최적 켈리는 수학적으로 옳아도 운용 불가능한 낙폭을 동반합니다. "
            "낙폭 제약이 없으면 어떤 켈리 값도 실무 권고로 쓸 수 없습니다.\n")

    # ============================================== 옵션
    if a.option_surface is not None:
        o = a.option_surface
        add("\n## 14. 옵션 표면 — 시장이 말하는 것\n")
        add("| 지표 | 값 | 해석 |")
        add("|---|---|---|")
        add(f"| 1M ATM IV | {_f(o.atm_iv_1m)} | |")
        add(f"| 기간구조 기울기 (3M−1M) | {_f(o.term_slope,'{:+.1%}')} | "
            f"{'백워데이션 → 단기 스트레스' if o.backwardation else '정상'} |")
        add(f"| 25Δ Risk Reversal | {_f(o.rr25_1m,'{:+.1%}')} | "
            f"{'하방 공포 프리미엄' if (o.rr25_1m or 0) > 0 else '상방 선호'} |")
        add(f"| **IV − RV 스프레드** | {_f(o.iv_rv_spread,'{:+.1%}')} | "
            f"분산위험프리미엄 프록시 |")
        add(f"| Put/Call OI 비율 | {_f(o.put_call_oi_ratio,'{:.2f}')} | |")
        add(f"\n_{o.note}_\n")
    if a.rnd is not None:
        rn = a.rnd
        add(f"\n### 14.1 시장 함축 확률분포 (Breeden-Litzenberger, "
            f"{rn.tenor_days:.0f}일)\n")
        add("| 분위 | 시장 RND |")
        add("|---|---|")
        for lbl, val in (("5%", rn.q05), ("25%", rn.q25), ("50%", rn.q50),
                         ("75%", rn.q75), ("95%", rn.q95)):
            add(f"| {lbl} | {val:,.2f} |")
        add(f"\n무차익 조건 충족: {'예' if rn.arbitrage_ok else '**아니오 — 주의**'} · "
            f"함축 왜도 {rn.implied_skew:+.2f}\n")
        if a.model_vs_market:
            mv = a.model_vs_market
            add(f"\n### 14.2 우리 모델 vs 시장 — 차이가 곧 논지\n")
            add(f"| | 모델 | 시장(위험중립) | 차이 |")
            add(f"|---|---|---|---|")
            add(f"| 상승확률 | {_f(mv['model_prob_up'],'{:.1%}')} | "
                f"{_f(mv['market_prob_up_rn'],'{:.1%}')} | "
                f"{_f(mv['prob_gap'],'{:+.1%}')} |")
            add(f"| 중앙값 | {mv['model_median']:,.2f} | "
                f"{mv['market_median']:,.2f} | "
                f"{_f(mv['q50_diff_pct'],'{:+.1%}')} |")
            add(f"\n**{mv['verdict']}**\n")
            add("_위험중립 확률에는 리스크 프리미엄이 포함돼 있어 실세계 확률과 "
                "동일하지 않습니다. 차이의 방향과 크기를 논지로 삼는 것이 올바른 "
                "용법입니다._\n")

    # ============================================== 에이전트
    add("\n## 15. 에이전트 심의 — 정보 비대칭 설계\n")
    add("> LLM 멀티에이전트 토론은 자동으로 정확도를 올리지 않습니다. 동조 효과, "
        "다수의 폭정, 그리고 **동일 입력을 받으면 토론이 마팅게일이 되어 개선이 "
        "없다**는 이론적 결과가 있습니다. 따라서 각 에이전트에 **서로 다른 데이터 "
        "슬라이스**를 주고, 거부권은 통계 에이전트에만 부여하며, 최종 판정은 "
        "LLM이 아니라 결정론적 규칙 엔진이 합니다.\n")
    add("| 에이전트 | 역할 | 데이터 범위 (비대칭) | 입장 | 확률 | 거부권 |")
    add("|---|---|---|---|---|---|")
    for v_ in a.agent_views:
        add(f"| {v_.agent} | {v_.role} | {v_.data_scope} | {v_.stance} | "
            f"{_f(v_.prob_up,'{:.1%}')} | {'⛔' if v_.veto else '—'} |")
    add("")
    for v_ in a.agent_views:
        if v_.evidence:
            add(f"**{v_.agent}**")
            for e in v_.evidence[:4]:
                add(f"- {e}")
            add("")

    if len(a.verdict.pooled_detail):
        add("### 15.1 캘리브레이션 가중 로그오즈 풀링\n")
        add(_md_table(a.verdict.pooled_detail,
                      ["에이전트", "입장", "확률", "가중치", "로그오즈"]))
        add(f"\n에이전트 간 확률 산포: **{_f(a.verdict.dispersion,'{:.3f}')}** "
            f"— 산포가 크면 통합 확률을 0.5로 축소합니다(평균내지 않음).\n")

    # ============================================== 레드팀
    rt = next((v_ for v_ in a.agent_views if v_.agent.startswith("A11")), None)
    if rt:
        add("\n## 16. 레드팀 — 사전등록된 반증 프로토콜\n")
        add("> 레드팀은 **최소 3개의 구체적 반대 증거 제출이 의무**입니다. "
            "제출 실패는 시스템 오류로 로깅됩니다.\n")
        for e in rt.evidence:
            add(f"- {e}")
        add("")

    # ============================================== 한계
    add("\n## 17. 이 분석이 말할 수 없는 것\n")
    add("| 한계 | 내용 |")
    add("|---|---|")
    add("| 생존편향 | Yahoo Finance에는 상장폐지 종목이 없습니다. 종목선택 전략은 "
        "원리적으로 검증 불가합니다. |")
    add("| 인트라데이 | 일봉만 사용하므로 진짜 실현변동성·오더플로우는 계산 "
        "불가합니다. 프록시임을 명시했습니다. |")
    add("| 펀더멘털 PIT | 재무 데이터는 리스테이트먼트가 반영된 값이라 "
        "point-in-time이 아닙니다. |")
    add("| 옵션 히스토리 | 스냅샷만 제공되어 백테스트가 불가합니다. 오늘부터 "
        "축적해야 합니다. |")
    add("| 체결 가정 | 신호 생성 종가가 아니라 다음 거래일 시가/VWAP 체결을 "
        "가정해야 합니다. 이 선택이 수익성을 뒤집을 수 있습니다. |")
    add("| 세금·환율 | 반영되지 않았습니다. |")
    if a.warnings:
        add("\n**실행 경고**")
        for w in a.warnings:
            add(f"- {w}")
    add("")

    add("\n---\n")
    add(f"_소요시간 {max(a.timings.values()):.1f}초 · "
        f"{' · '.join(f'{k} {v:.1f}s' for k, v in list(a.timings.items())[-5:])}_\n")
    add(f"\n> {a.verdict.disclaimer}\n")
    return "\n".join(L)


def save(a, path: str) -> str:
    txt = render(a)
    with open(path, "w", encoding="utf-8") as f:
        f.write(txt)
    return path
