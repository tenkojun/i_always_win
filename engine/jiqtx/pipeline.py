# ==============================================================================
# [23/25] pipeline.py — 엔드투엔드 오케스트레이션
# ==============================================================================

"""
jiqtx.pipeline — 엔드투엔드 분석 파이프라인.

실행 순서
---------
0  데이터 수집 + 무결성 (Data Steward)         → 실패 시 HALT
1  자산군 분류 (통계 지문)                      → 팩터 prior 결정
2  유동성·거래비용 (EDGE/Amihud/임팩트)         → 실패 시 SIZE_ZERO
3  변동성 (GJR-GARCH-t, HAR)
4  레짐 (Statistical Jump Model)
5  팩터 모델 + 시변 베타 + 델타 패널            → 미스매칭 시 알파 해석 차단
6  ML 방향 예측 (트리플배리어 + Purged CV)      → 게이트 실패 시 ABSTAIN
7  시뮬레이션 (FHS-EVT + 드리프트 사후분포)
8  리스크 (VaR/ES + 커버리지 검정 + 스트레스)
9  사이징 (낙폭제약 켈리)
10 옵션 표면 + RND (가능한 경우)
11 게이트 → 에이전트 → 결정론적 판정
"""

import math
import time
import warnings
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

# ── 패키지 내부 의존 ──────────────────────────────────────────
from .config import GATES, RUN, RunConfig
from . import agents as ag
from . import data as dta
from . import equity as eqm
from . import factors as fct
from . import micro as mic
from . import ml as mlm
from . import options as opt
from . import panel as pnl
from . import regime as rgm
from . import risk as rsk
from . import simulate as sim_mod
from . import taxonomy as tax
from . import thesis as ths
from . import trade as trd
from . import vol as volm
from .horizons import analyze_horizons
from .macro_board import build_macro_board




@dataclass
class Analysis:
    ticker: str
    asof: str
    classification: Any
    integrity: Any
    liquidity: Any
    perf: Dict[str, float]
    vol_profile: Any
    regime: Any
    factor_model: Any
    delta_panel: pd.DataFrame
    tvb: Dict[str, Any]
    ml: Any
    sim: Any
    var: Any
    drawdown: Any
    stress_table: pd.DataFrame
    stress_summary: Dict[str, Any]
    sizing: Any
    capacity: pd.DataFrame
    option_surface: Any
    rnd: Any
    model_vs_market: Dict[str, Any]
    gates: Any
    agent_views: List[Any]
    verdict: Any
    timings: Dict[str, float]
    warnings: List[str]
    prices: Any = None
    returns: Any = None
    index: Any = None
    macro: Any = None            # FRED 원본 (거시 대시보드가 쓴다)
    horizons: Any = None         # 단/중/장 다지평 패널
    macro_board: Any = None      # 자산군 맞춤 거시 대시보드
    equity: Any = None
    scenarios: Any = None
    scenario_ev: Any = None
    kill: Any = None
    monitor: Any = None
    catalysts: Any = None
    trade: Any = None
    hedge: Any = None
    attribution: Any = None
    factor_panel: Any = None
    panel: Any = None


# ── 컬럼 규약 ────────────────────────────────────────────────
# 이 저장소에는 가격 데이터 경로가 둘이고 규약이 서로 다르다.
#
#   engine/data/loader.py  →  소문자 (open/high/low/close/volume)
#   engine/jiqtx/data.py   →  대문자 (Open/High/Low/Close/Volume)
#
# 운영에서는 jiqtx 가 자기 수집 경로를 쓰므로 드러나지 않았다. 그런데
# analyze() 는 df 주입을 공식적으로 지원하고(백테스트·오프라인 검증),
# 그 경로로 소문자 프레임을 넣으면 무결성 검사에서 KeyError 로 죽었다.
# CLAUDE.md 는 "소문자 계약" 이라고만 적혀 있어서 문서를 따르면 오히려
# 깨졌다.
#
# 어느 쪽 규약이 옳은지 다투는 대신 **경계에서 받아 준다.** 내부는
# 대문자로 통일한 채로 두고, 들어오는 것만 맞춰 준다.
_OHLCV_CANON = {"open": "Open", "high": "High", "low": "Low",
                "close": "Close", "volume": "Volume",
                "adj close": "Adj Close", "adjclose": "Adj Close"}


def _normalize_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    """소문자/대문자 어느 쪽으로 와도 내부 규약(대문자)으로 맞춘다."""
    if df is None or not hasattr(df, "columns"):
        return df
    ren = {}
    for c in df.columns:
        key = str(c).strip().lower()
        canon = _OHLCV_CANON.get(key)
        if canon and canon not in df.columns:
            ren[c] = canon
    return df.rename(columns=ren) if ren else df


def analyze(ticker: str, df: Optional[pd.DataFrame] = None,
            meta: Optional[Dict] = None,
            macro: Optional[pd.DataFrame] = None,
            proxies: Optional[Dict[str, pd.Series]] = None,
            cfg: RunConfig = RUN,
            aum_usd: float = 10_000_000.0,
            with_options: bool = True,
            with_ml: bool = True,
            calib_weights: Optional[Dict[str, float]] = None,
            fast: bool = False,
            verbose: bool = True) -> Analysis:
    """
    df/meta/macro/proxies 를 주지 않으면 Yahoo Finance + FRED에서 직접 수집한다.
    (오프라인 테스트에서는 합성 데이터를 주입할 수 있다.)
    """
    t0 = time.time()
    T: Dict[str, float] = {}
    warns: List[str] = []

    def tick(k):
        T[k] = time.time() - t0

    def log(msg):
        if verbose:
            print(f"  [{time.time()-t0:5.1f}s] {msg}", flush=True)

    # --- 0. 데이터
    if df is None:
        df, meta = dta.load_prices(ticker, years=cfg.lookback_years)
    meta = meta or {}
    df = _normalize_ohlcv(df).sort_index()
    integ = dta.check_integrity(ticker, df, GATES.max_missing_ratio)
    log(f"데이터 {integ.n_rows}행 · 무결성 {'통과' if integ.passed else '실패'}")
    tick("data")

    if macro is None:
        try:
            macro = dta.load_fred(years=cfg.lookback_years + 2)
            gpr = dta.load_gpr()
            if gpr is not None:
                macro = macro.join(gpr.rename("gpr"), how="outer").sort_index()
        except Exception as e:
            warns.append(f"매크로 수집 실패: {e}")
            macro = pd.DataFrame()
    if proxies is None:
        try:
            proxies = dta.load_proxies(years=cfg.lookback_years)
        except Exception as e:
            warns.append(f"프록시 수집 실패: {e}")
            proxies = {}
    df, macro_a, proxies_a = dta.align_all(df, macro, proxies)
    tick("macro")

    close = df["Close"].astype(float).values
    r = np.diff(np.log(close))
    r_full = np.concatenate([[np.nan], r])
    idx = pd.DatetimeIndex(df.index)

    # --- 1. 분류
    cls = tax.classify(ticker, df, meta, proxies_a)
    spec = cls.spec
    ann = spec.ann_factor
    log(f"분류 → {spec.label_ko} (신뢰도 {cls.confidence:.0%}, 연율화 √{ann})")
    tick("classify")

    # --- 2. 유동성
    liq = mic.liquidity_profile(df, spec.max_spread_bps, GATES.min_adv_usd,
                                GATES.max_zero_return_ratio, aum_usd=aum_usd)
    log(f"유동성 → EDGE {liq.spread_bps:.0f}bp · "
        f"{'거래가능' if liq.tradable else '거래불가: ' + liq.reason}")
    tick("liquidity")

    perf = rsk.performance_summary(r, close, ann=ann, rf_ann=cfg.risk_free)

    # --- 3. 변동성
    vp = volm.vol_profile(r, ann=ann)
    log(f"변동성 → GARCH {vp.garch.ann_vol_current:.1%} "
        f"(장기 {vp.garch.ann_vol_longrun:.1%}, ν={vp.garch.nu:.1f}"
        f"{', 경계' if vp.garch.at_boundary else ''})")
    tick("vol")

    # --- 4. 레짐
    macro_for_regime = macro_a[[c for c in ("real_yield_10y", "broad_dollar",
                                            "hy_oas", "vix")
                                if c in macro_a.columns]] if len(macro_a) else None
    try:
        rg = rgm.detect_regimes(r_full[1:], n_states=3, ann=ann,
                                jump_penalty=(20.0 if fast else None),
                                macro=(macro_for_regime.iloc[1:].reset_index(drop=True)
                                       if macro_for_regime is not None else None),
                                seed=cfg.seed)
        log(f"레짐 → '{rg.labels[rg.current_state]}' "
            f"(λ={rg.jump_penalty:.0f}, 전환 {rg.n_switches}회)")
    except Exception as e:
        rg = None
        warns.append(f"레짐 식별 실패: {e}")
    tick("regime")

    # --- 5. 팩터
    F = fct.build_factor_panel(idx, macro_a, proxies_a, spec.factor_prior)
    F = F.dropna(axis=1, how="all")
    fm = fct.fit_factor_model(r_full, F, spec.r2_band, ann=ann) if len(F.columns) \
        else None
    if fm:
        log(f"팩터 → R²={fm.r2:.1%} "
            f"{'⚠ 미스매칭' if fm.mismatch else '적합'} · "
            f"선택 {fm.used_factors}")
    dp = fct.factor_delta_panel(r_full, F, spec.stress, idx, fast=fast) \
        if len(F.columns) else pd.DataFrame()
    tvb = {}
    for f in (fm.used_factors[:4] if fm else []):
        if f in F.columns:
            tvb[f] = fct.time_varying_beta(r_full, F[f].values, idx, f)
    tick("factors")

    # --- 5b. 주식 성격 프로파일링
    eqp = None
    if cls.asset_class in ("EQUITY_LARGE", "EQUITY_SMALL", "REIT"):
        try:
            sigma_t = volm.ewma_vol(r_full[1:], 0.94)
            eqp = eqm.profile_equity(
                ticker, df, meta, r_full, F,
                np.concatenate([[np.nan], sigma_t]),
                proxies_a, perf.get("vol_ann", np.nan), ann=ann,
                earnings_df=None if df is not None and meta else None)
            log(f"성격 → {eqp.archetype_ko} (신뢰도 {eqp.archetype_confidence:.0%}) "
                f"· 활성섹션 {len(eqp.active_sections)}개")
        except Exception as e:
            warns.append(f"주식 프로파일링 실패: {e}")
    tick("equity")

    # --- 6. ML
    mlr = None
    if with_ml:
        sigma_t = volm.ewma_vol(r_full[1:], 0.94)
        cost = float(liq.spread_used) if np.isfinite(liq.spread_used) else 0.0005
        labels = mlm.triple_barrier(close[1:], sigma_t,
                                    horizon=cfg.horizons[1],
                                    pt_mult=2.0, sl_mult=2.0, cost=cost)
        X = mlm.build_features(df.iloc[1:], macro_a.iloc[1:] if len(macro_a) else None)
        try:
            mlr = mlm.evaluate_direction(
                X, labels, n_splits=6, embargo_frac=cfg.embargo_frac,
                model_kind="gbm", seed=cfg.seed,
                gates={"overfit_gap_max": GATES.overfit_gap_max,
                       "resolution_min": GATES.resolution_min,
                       "brier_skill_min": GATES.brier_skill_min,
                       "dsr_min": GATES.dsr_min})
            log(f"ML → {mlr.verdict} (승자 {mlr.model_name}, OOS "
                f"{mlr.oos_accuracy:.1%}, 갭 {mlr.overfit_gap:+.1%}, "
                f"DSR {mlr.strategy_dsr:.0%})")
        except Exception as e:
            warns.append(f"ML 실패: {e}")
    tick("ml")

    # --- 7. 시뮬레이션
    lev = cls.fingerprint.leverage_detected
    sim = sim_mod.simulate_fhs(
        close, r, vp.garch, horizon=cfg.sim_horizon_days,
        n_sims=cfg.n_sims, ann=ann,
        prior_mean_ann=0.03, shrink=cfg.drift_shrink,
        seed=cfg.seed, leverage=lev)
    log(f"시뮬 → P(up) {sim.prob_up:.1%} (원본식 GBM "
        f"{sim.prob_up_naive_gbm:.1%}) · 드리프트 SE {sim.drift.se_ann:.1%}")
    tick("simulate")

    # --- 8. 리스크
    v = rsk.var_es(r, vp.garch.z, vp.garch.sigma[-1], alpha=0.05)
    ddp = rsk.drawdown_profile(close)
    # 복합 시나리오는 부분(다변량) 베타로 계산해야 한다 — 단변량을 더하면
    # 같은 시장 충격을 여러 번 세서 손실이 크게 부풀려진다.
    st_tbl, st_sum = rsk.stress_test(dp, spec.stress,
                                     limit=GATES.stress_loss_limit,
                                     partial_betas=getattr(fm, "coefs", None))
    log(f"리스크 → VaR95 {v.var_fhs_evt:.2%}(채택 {v.preferred}) · "
        f"MDD {ddp.max_drawdown:.1%} · 스트레스 최악 "
        f"{st_sum.get('worst_pnl', float('nan')):.1%}")
    tick("risk")

    # --- 9. 사이징
    sizing = rsk.position_size(
        mu_post_ann=sim.drift.mu_post_ann, se_post_ann=sim.drift.se_post_ann,
        sigma_ann=vp.garch.ann_vol_current if np.isfinite(vp.garch.ann_vol_current)
        else perf.get("vol_ann", 0.2),
        vol_target=cfg.vol_target, class_cap=spec.max_weight,
        stress_worst=st_sum.get("worst_pnl", np.nan), stress_budget=0.08,
        adv_usd=liq.adv_usd, aum_usd=aum_usd,
        kelly_cap=cfg.kelly_cap, z_resid=vp.garch.z, ann=ann,
        kelly_paths=1200 if fast else 4000)
    cap = mic.capacity_curve(liq.adv_usd if np.isfinite(liq.adv_usd) else 1e6,
                             perf.get("vol_ann", 0.2) / math.sqrt(ann),
                             gross_edge_ann=0.04,
                             spread=liq.spread_used if np.isfinite(liq.spread_used)
                             else 0.0005)
    tick("sizing")

    # --- 10. 옵션
    osurf = rnd = None
    mvm: Dict[str, Any] = {}
    if with_options:
        try:
            osurf = opt.option_surface(ticker, float(close[-1]),
                                       vp.realized_21d_ann, r=cfg.risk_free)
            if osurf:
                log(f"옵션 → 1M IV {osurf.atm_iv_1m:.1%}, 25ΔRR "
                    f"{osurf.rr25_1m:+.1%}, IV−RV {osurf.iv_rv_spread:+.1%}")
                rnd = opt.risk_neutral_density(ticker, float(close[-1]),
                                               target_days=90, r=cfg.risk_free)
                if rnd:
                    mvm = opt.compare_model_vs_market(sim.terminal, rnd,
                                                      float(close[-1]))
        except Exception as e:
            warns.append(f"옵션 분석 생략: {e}")
    tick("options")

    # --- 10b. 논지·트레이드·헤지·귀인 (해지펀드 메모 계층)
    scen = ev_sc = kill = mon = cat = tp = hp = attrib = None
    tick("options")

    # --- 11. 게이트 → 에이전트 → 판정
    gb = ag.run_gates(integ, liq, cls, fm, mlr, st_sum, cfg)
    ctx = {"classification": cls, "integrity": integ, "liquidity": liq,
           "factor_model": fm, "delta_panel": dp, "ml": mlr, "regime": rg,
           "sim": sim, "var": v, "drawdown": ddp, "stress_summary": st_sum,
           "option_surface": osurf}
    ctx["red_team"] = ag.red_team_challenges(ctx)
    views = ag.assemble_agents(ctx)
    verdict = ag.adjudicate(views, gb, sizing, calib_weights)

    # 판정 이후에 논지 계층을 구성한다 (판정이 방향·사이즈를 정하므로)
    tmp = Analysis(
        ticker=ticker, asof=str(idx[-1].date()), classification=cls,
        integrity=integ, liquidity=liq, perf=perf, vol_profile=vp, regime=rg,
        factor_model=fm, delta_panel=dp, tvb=tvb, ml=mlr, sim=sim, var=v,
        drawdown=ddp, stress_table=st_tbl, stress_summary=st_sum,
        sizing=sizing, capacity=cap, option_surface=osurf, rnd=rnd,
        model_vs_market=mvm, gates=gb, agent_views=views, verdict=verdict,
        timings=T, warnings=warns, prices=close, returns=r_full, index=idx,
        equity=eqp, factor_panel=F)
    try:
        alpha_use, alpha_note = (
            ths.shrink_alpha(fm.alpha_ann, fm.alpha_t, cfg.drift_shrink)
            if fm and fm.interpretation_allowed else (0.0, "팩터 모델 무효 → 알파 0"))
        warns.append(f"시나리오 알파 처리: {alpha_note}")
        scen = ths.build_scenarios(
            dp, F, float(close[-1]), horizon=63,
            resid_drift_ann=alpha_use, ann=ann)
        if scen and sim is not None:
            scen = ths.attach_model_probs(scen, sim.terminal, float(close[-1]))
            ev_sc = ths.expected_value(scen)
        kill = ths.kill_criteria(tmp)
        mon = ths.monitoring_plan(tmp, scen or [])
        cat = ths.catalysts(tmp)
        tp = trd.build_trade(tmp, scen, horizon_days=63)
        hp = trd.build_hedge(tmp)
        if fm is not None and len(F.columns):
            attrib = trd.return_attribution(r_full, F, fm.coefs)
        tmp.scenarios, tmp.trade, tmp.hedge = scen, tp, hp
        tmp.scenario_ev, tmp.kill = ev_sc, kill
        tmp.attribution = attrib
        log(f"논지 → 시나리오 {len(scen or [])}개 · 킬조건 "
            f"{sum(1 for k in (kill or []) if k.breached)}/{len(kill or [])} 발동 "
            f"· 트레이드 '{tp.verdict if tp else '—'}' · 헤지 "
            f"'{hp.verdict if hp else '—'}'")
    except Exception as e:
        warns.append(f"논지 계층 실패: {e}")
    tick("thesis")

    # --- 12. 전문가 패널 심의 (인격별 소견 + 반대신문)
    panel = None
    try:
        panel = pnl.convene(tmp)
        log(f"패널 → 전문가 {len(panel.experts)}명 · 반대신문 "
            f"{len(panel.challenges)}건 · 미해결 {len(panel.open_issues)}건 "
            f"· 거부권 {len(panel.blocks)}건")
    except Exception as e:
        warns.append(f"전문가 패널 실패: {e}")
    tick("panel")
    log(f"판정 → {verdict.grade} · 확률 "
        f"{verdict.direction_prob if verdict.direction_prob else float('nan'):.1%} "
        f"· 사이즈 {verdict.risk_budget_weight:.1%} · 신뢰도 {verdict.model_confidence}")
    tick("adjudicate")

    # ── 단/중/장 다지평 (점수를 합치지 않고 불일치를 드러낸다) ──
    hz = None
    try:
        mkt_px = None
        if proxies:
            # pandas Series 는 `or` 로 고를 수 없다(진리값 모호). 명시적으로.
            for _k in ("SPY", "mkt_excess"):
                _v = proxies.get(_k)
                if _v is not None and len(_v):
                    mkt_px = _v
                    break
        hz = analyze_horizons(pd.Series(close, index=idx), ann=ann, mkt=mkt_px)
        if hz and hz.disagreements:
            log(f"지평 → {hz.summary}")
    except Exception as e:
        warns.append(f"다지평 분석 실패: {e}")
    tick("horizons")

    out = Analysis(
        ticker=ticker, asof=str(idx[-1].date()), classification=cls,
        integrity=integ, liquidity=liq, perf=perf, vol_profile=vp, regime=rg,
        factor_model=fm, delta_panel=dp, tvb=tvb, ml=mlr, sim=sim, var=v,
        drawdown=ddp, stress_table=st_tbl, stress_summary=st_sum,
        sizing=sizing, capacity=cap, option_surface=osurf, rnd=rnd,
        model_vs_market=mvm, gates=gb, agent_views=views, verdict=verdict,
        timings=T, warnings=warns,
        prices=close, returns=r_full, index=idx, equity=eqp,
        scenarios=scen, scenario_ev=ev_sc, kill=kill, monitor=mon,
        catalysts=cat, trade=tp, hedge=hp, attribution=attrib,
        factor_panel=F, panel=panel, macro=macro, horizons=hz)

    # 거시 대시보드 — 완성된 Analysis 를 받아야 델타 패널을 쓸 수 있다
    try:
        out.macro_board = build_macro_board(out)
        if out.macro_board:
            log(f"거시 → 변수 {len(out.macro_board.rows)}개 · "
                f"전술 '{out.macro_board.tactical}'")
    except Exception as e:
        warns.append(f"거시 대시보드 실패: {e}")
    tick("macro_board")

    return out
