
# ── 패키지 내부 의존 ──────────────────────────────────────────
import numpy as np
import pandas as pd

from .equity import (
    ARCHETYPES,
    StyleTilt,
    classify_archetype,
    earnings_event_study,
    extract_fundamentals,
    jump_profile,
)
from .micro import abdi_ranaldo, corwin_schultz, edge_spread, roll_spread
from .risk import kelly_with_drawdown_constraint
from .statcore import adaptive_conformal, murphy_decomposition
from .vol import fit_gjr_garch_t

# ==============================================================================
# 검증 스위트 — 추정량이 실제로 맞는지 합성 진실값에 대해 확인
# ==============================================================================

def _sim_ohlc_bounce(n_days, true_spread, daily_vol, ticks=390, seed=0):
    rng = np.random.default_rng(seed)
    sd_t = daily_vol / np.sqrt(ticks)
    O, H, L, C = (np.empty(n_days) for _ in range(4))
    logp = np.log(100.0)
    for d in range(n_days):
        eff = logp + np.cumsum(rng.normal(0, sd_t, ticks))
        tr = np.exp(eff + rng.choice([-1, 1], ticks) * true_spread / 2)
        O[d], C[d], H[d], L[d] = tr[0], tr[-1], tr.max(), tr.min()
        logp = eff[-1]
    return O, H, L, C


def run_validation():
    print("=" * 74)
    print("I ALWAYS WIN 추정량 검증")
    print("=" * 74)

    print("\n[1] 스프레드 추정량 — 진짜 스프레드 복원 정확도")
    print(f"{'true':>8} {'EDGE':>10} {'CS':>10} {'CHL':>10} {'Roll':>10}")
    for s in (0.0005, 0.001, 0.002, 0.005, 0.01):
        acc = {k: [] for k in "ECHR"}
        for k in range(6):
            O, H, L, C = _sim_ohlc_bounce(500, s, 0.018, seed=k)
            acc["E"].append(edge_spread(O, H, L, C))
            acc["C"].append(corwin_schultz(H, L))
            acc["H"].append(abdi_ranaldo(H, L, C))
            acc["R"].append(roll_spread(C))
        f = lambda x: f"{np.nanmean(x)*1e4:8.1f}bp"
        print(f"{s*1e4:6.0f}bp {f(acc['E'])} {f(acc['C'])} "
              f"{f(acc['H'])} {f(acc['R'])}")
    print("  → EDGE 만 편향 없이 복원. CS/Roll 은 저스프레드에서 심한 상향편향.")

    print("\n[2] GJR-GARCH(1,1)-t 파라미터 복원")
    for om, a_, g_, b_, nu in [(3e-6, 0.05, 0.06, 0.88, 6),
                               (2e-6, 0.03, 0.10, 0.86, 8)]:
        rng = np.random.default_rng(3)
        n = 2000; s2 = np.zeros(n); e = np.zeros(n)
        s2[0] = om / (1 - a_ - g_/2 - b_)
        for t in range(1, n):
            s2[t] = om + (a_ + g_*(e[t-1] < 0))*e[t-1]**2 + b_*s2[t-1]
            e[t] = np.sqrt(s2[t]) * rng.standard_t(nu) / np.sqrt(nu/(nu-2))
        f = fit_gjr_garch_t(e)
        print(f"  진짜 α={a_:.3f} γ={g_:.3f} β={b_:.3f} ν={nu}  →  "
              f"추정 α={f.alpha:.3f} γ={f.gamma:.3f} β={f.beta:.3f} "
              f"ν={f.nu:.1f} (지속성 {f.persistence:.3f})")

    print("\n[3] Murphy resolution — 신호/노이즈 판별력")
    rng = np.random.default_rng(0)
    p = rng.uniform(0, 1, 4000)
    a1 = murphy_decomposition(p, (rng.uniform(0, 1, 4000) < p).astype(float))
    b1 = murphy_decomposition(p, (rng.uniform(0, 1, 4000) < 0.5).astype(float))
    print(f"  정보성: resolution {a1['resolution']:.5f}, skill {a1['skill']:+.3f}")
    print(f"  무정보: resolution {b1['resolution']:.5f}, skill {b1['skill']:+.3f}")
    print(f"  → 판별비 {a1['resolution']/max(b1['resolution'],1e-9):.0f}배")

    print("\n[4] ACI conformal 커버리지 (목표 90%)")
    rng = np.random.default_rng(1)
    for name, gen in (("정규", lambda n: rng.standard_normal(n)),
                      ("t(3) 팻테일", lambda n: rng.standard_t(3, n)/np.sqrt(3)),
                      ("변동성 레짐전환", lambda n: rng.standard_normal(n) *
                       np.where(np.arange(n) < n//2, 1.0, 2.5))):
        r_ = adaptive_conformal(gen(1500), np.ones(1500), 0.90)
        print(f"  {name:14s}: 실측 {r_.empirical_coverage:.1%} "
              f"(오차 {r_.coverage_gap:+.1%})")

    print("\n[5] 낙폭제약 켈리 — 추정오차가 커지면 축소되는가")
    rng = np.random.default_rng(2)
    zt = rng.standard_t(4, 4000) / np.sqrt(2)
    for se in (0.02, 0.10, 0.25):
        k = kelly_with_drawdown_constraint(0.08, se, 0.18, z_resid=zt,
                                           dd_limit=0.25)
        print(f"  SE(μ)={se:.0%}: naive {k['f_naive']:.0%} → 성장최적 "
              f"{k['f_growth']:.0%} (95%MDD {k['mdd_at_growth']:.0%}) → "
              f"낙폭제약 {k['f_dd']:.0%} (95%MDD {k['mdd_at_dd']:.0%})")

    print("\n[6] 주식 아키타입 분류기 회귀 테스트")
    cases = [
        ("우량 복리성장주", "QUALITY_COMPOUNDER", "Technology",
         dict(marketCap=4.2e11, returnOnEquity=0.34, profitMargins=0.245,
              operatingMargins=0.31, revenueGrowth=0.14, debtToEquity=32.0,
              totalCash=5.5e10, totalDebt=1.2e10, dividendYield=0.006,
              trailingPE=27.5, priceToBook=9.1, currentRatio=2.4,
              freeCashflow=2.1e10), 0.95, 0.22, 0.0),
        ("고성장 적자기업", "HYPERGROWTH_UNPROFITABLE", "Technology",
         dict(marketCap=1.8e10, profitMargins=-0.185, operatingMargins=-0.12,
              revenueGrowth=0.46, debtToEquity=18.0, totalCash=2.4e9,
              totalDebt=3.0e8, dividendYield=0.0, priceToBook=14.2,
              currentRatio=3.8, freeCashflow=-3.2e8,
              shortPercentOfFloat=0.081), 1.8, 0.55, -0.2),
        ("딥밸류", "DEEP_VALUE", "Financial Services",
         dict(marketCap=3.1e9, trailingPE=7.8, priceToBook=0.72,
              returnOnEquity=0.05, profitMargins=0.06, operatingMargins=0.09,
              revenueGrowth=-0.03, debtToEquity=95.0, dividendYield=0.021,
              currentRatio=1.4, freeCashflow=2.4e8), 0.85, 0.26, 0.5),
        ("배당 인컴주", "DIVIDEND_INCOME", "Utilities",
         dict(marketCap=6.2e10, trailingPE=17.2, priceToBook=1.9,
              returnOnEquity=0.11, profitMargins=0.135, operatingMargins=0.22,
              revenueGrowth=0.03, debtToEquity=142.0, dividendYield=0.055,
              payoutRatio=0.88, currentRatio=0.9, freeCashflow=1.6e9),
         0.55, 0.15, 0.0),
        ("부실/턴어라운드", "DISTRESSED", "Industrials",
         dict(marketCap=4.2e8, profitMargins=-0.11, operatingMargins=-0.04,
              revenueGrowth=-0.08, debtToEquity=340.0, currentRatio=0.7,
              totalCash=6e7, totalDebt=1.4e9, dividendYield=0.0,
              priceToBook=0.6, freeCashflow=-9e7), 1.6, 0.62, 0.0),
    ]
    rng = np.random.default_rng(0)
    ok = 0
    for name, expect, sector, meta, beta, idio, hml in cases:
        f = extract_fundamentals(meta)
        st = StyleTilt({"mkt_excess": beta, "smb": 0.2, "hml": hml, "rmw": 0.1,
                        "cma": 0.0, "umd": 0.0},
                       {k: 2.0 for k in ("mkt_excess", "smb", "hml", "rmw",
                                         "cma", "umd")},
                       0.5, "테스트", 0.2, idio, 0.0, 0.0)
        r_ = rng.normal(0, 0.015, 900)
        jp = jump_profile(r_, np.full(900, 0.015))
        vol_ = float(np.std(r_, ddof=1) * np.sqrt(252)) * beta
        got, conf, ev = classify_archetype(f, st, jp, sector, vol_)
        hit = got == expect
        ok += hit
        print(f"  [{'PASS' if hit else 'FAIL'}] {name:<16} → "
              f"{ARCHETYPES[got]['ko']} (신뢰도 {conf:.0%})")
    print(f"  → {ok}/{len(cases)} 통과")

    print("\n[7] PEAD 검출력 — 주입한 드리프트를 회수하는가")

    def _pead_case(true_pead, seed_base, reps):
        got, ts = [], []
        for k in range(reps):
            rng2 = np.random.default_rng(seed_base + k)
            n_days, n_ev = 1400, 22
            idx = pd.bdate_range("2019-01-02", periods=n_days)
            bench_r = rng2.normal(0.0003, 0.010, n_days)
            r_ = 1.0 * bench_r + rng2.normal(0.0, 0.011, n_days)
            ev_pos = np.linspace(120, n_days - 40, n_ev).astype(int)
            surp = rng2.normal(0, 1.0, n_ev)
            for j, pp_ in enumerate(ev_pos):
                s_ = surp[j]
                r_[pp_+1] += np.sign(s_) * abs(rng2.normal(0.055, 0.019))
                if true_pead:
                    r_[pp_+2:pp_+22] += np.sign(s_) * true_pead / 20.0
            px = pd.Series(100 * np.exp(np.cumsum(r_)), index=idx)
            bench = pd.Series(300 * np.exp(np.cumsum(bench_r)), index=idx)
            ed = pd.DataFrame({"Surprise(%)": surp * 5.0}, index=idx[ev_pos])
            es = earnings_event_study("TEST", px, bench, ed)
            if es:
                got.append(es.pead_spread)
                ts.append(es.pead_tstat)
        g = np.array(got); t = np.array(ts)
        return g, t

    for tp in (0.08, 0.04, 0.02):
        g, t = _pead_case(tp, 10, 6)
        print(f"  참 스프레드 {2*tp:+.1%} → 추정 {g.mean():+.2%} "
              f"(±{g.std(ddof=1):.2%})  편의 {g.mean()-2*tp:+.2%}  "
              f"|t| {np.abs(t).mean():.2f}  검출률 {np.mean(np.abs(t)>1.8):.0%}")
    g, t = _pead_case(0.0, 50, 12)
    print(f"  귀무 (0%)          → 추정 {g.mean():+.2%} "
          f"(±{g.std(ddof=1):.2%})  |t| {np.abs(t).mean():.2f}  "
          f"거짓양성률 {np.mean(np.abs(t)>1.8):.0%}  "
          f"(|t|>1.8 명목 ≈7%)")

    print("\n" + "=" * 74)
