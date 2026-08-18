
# ── 패키지 내부 의존 ──────────────────────────────────────────
from dataclasses import replace
import numpy as np
import os
import pandas as pd
import time

from .config import RUN
from .dynamic_report import build_sections, save_html
from .pipeline import analyze
from .portfolio import analyze_portfolio
from .portfolio_report import save_portfolio
from .report import save

# ==============================================================================
# 오프라인 데모 — 네트워크 없이 전체 파이프라인 검증
# ==============================================================================

def _ohlc_microstructure(c_path, rng, true_spread=0.0008, ticks=390,
                         wknd=False, start="2019-01-02"):
    """
    브라운 브릿지로 일간 종가 로그수익을 정확히 보존하면서 현실적인 일중
    고저와 bid-ask bounce를 생성한다. 임의 노이즈로 High/Low를 만들면
    스프레드 추정량이 무의미하게 부풀려진다.
    """
    n = len(c_path)
    idx = (pd.date_range(start, periods=n, freq="D") if wknd
           else pd.bdate_range(start, periods=n))
    logc = np.log(c_path)
    lr = np.diff(logc, prepend=logc[0])
    intraday_vol = float(np.std(lr[np.isfinite(lr)], ddof=1))
    O = np.empty(n); H = np.empty(n); L = np.empty(n); C = np.empty(n)
    tgrid = np.arange(1, ticks + 1) / ticks
    for d in range(n):
        w = np.cumsum(rng.normal(0, intraday_vol / np.sqrt(ticks), ticks))
        bridge = w - tgrid * w[-1]
        eff = logc[d] - lr[d] + tgrid * lr[d] + bridge
        q = rng.choice([-1.0, 1.0], ticks)
        tr = np.exp(eff + q * true_spread / 2.0)
        O[d], C[d] = tr[0], tr[-1]
        H[d], L[d] = tr.max(), tr.min()
    return pd.DataFrame({"Open": O, "High": H, "Low": L, "Close": C,
                         "Volume": rng.lognormal(16.5, 0.45, n)}, index=idx)


def build_demo_world(N: int = 1700, seed: int = 42):
    """
    7개 자산을 합성한다. 자산군·성격별로 다른 렌즈가 실제로 적용되는지 확인용.
    GOLDX 에는 2022년 이후 실질금리 β 붕괴(-0.075 → -0.010)를 의도적으로 심었다.
    """
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2019-01-02", periods=N)

    ry = np.cumsum(rng.normal(0, 0.035, N)) + 0.5
    be = np.cumsum(rng.normal(0, 0.02, N)) + 2.2
    dxy_r = rng.normal(0, 0.0035, N)
    dxy = 100 * np.exp(np.cumsum(dxy_r))
    vix = np.clip(18 + np.cumsum(rng.normal(0, 0.7, N)), 9, 70)
    hy = np.clip(3.5 + np.cumsum(rng.normal(0, 0.03, N)), 2.5, 12)
    n10 = ry + be
    n2 = n10 - np.clip(np.cumsum(rng.normal(0, 0.02, N)) + 0.6, -1.5, 2.5)
    macro = pd.DataFrame({
        "real_yield_10y": ry, "breakeven_10y": be, "broad_dollar": dxy,
        "vix": vix, "hy_oas": hy, "nominal_10y": n10, "nominal_2y": n2,
        "curve_2s10s": n10 - n2,
        "wti": 70 * np.exp(np.cumsum(rng.normal(0, .02, N))),
        "gpr": np.clip(100 + np.cumsum(rng.normal(0, 3, N)), 20, 400),
    }, index=idx)

    s2 = np.zeros(N); e = np.zeros(N); s2[0] = 1.2e-4
    for t in range(1, N):
        s2[t] = 3e-6 + (0.05 + 0.07 * (e[t-1] < 0)) * e[t-1]**2 + 0.88 * s2[t-1]
        e[t] = np.sqrt(s2[t]) * rng.standard_t(6) / np.sqrt(1.5)
    mkt = 0.00035 + e
    spy = pd.Series(300 * np.exp(np.cumsum(mkt)), index=idx)

    proxies = {
        "mkt_excess": spy,
        "smb": pd.Series(180*np.exp(np.cumsum(0.6*mkt+rng.normal(0,.006,N))), index=idx),
        "hml": pd.Series(140*np.exp(np.cumsum(0.8*mkt+rng.normal(0,.005,N))), index=idx),
        "rmw": pd.Series(120*np.exp(np.cumsum(0.9*mkt+rng.normal(0,.004,N))), index=idx),
        "cma": spy,
        "umd": pd.Series(160*np.exp(np.cumsum(1.05*mkt+rng.normal(0,.005,N))), index=idx),
    }

    d_ry = np.diff(ry, prepend=ry[0])
    d_be = np.diff(be, prepend=be[0])
    beta_ry = np.where(np.arange(N) < 900, -0.075, -0.010)   # ← 구조 변화 주입
    r_gold = (beta_ry * d_ry - 0.85 * dxy_r + 0.030 * d_be
              + 0.08 * mkt + rng.normal(0.00030, 0.0072, N))
    gold = 150 * np.exp(np.cumsum(r_gold))
    eq_ = 90 * np.exp(np.cumsum(1.15 * mkt + rng.normal(0.00012, 0.0075, N)))
    lev = 50 * np.cumprod(1 + np.clip(3.0 * np.expm1(mkt), -0.9, None))

    def eq_path(beta, idio_vol, drift, jump_p=0.0, jump_sd=0.06):
        base = beta * mkt + rng.normal(drift, idio_vol, N)
        if jump_p > 0:
            base = base + (rng.random(N) < jump_p) * rng.normal(0, jump_sd, N)
        return 60 * np.exp(np.cumsum(base))

    quality = eq_path(0.95, 0.0060, 0.00040)
    hyper = eq_path(1.55, 0.0175, 0.00030)
    income = eq_path(0.62, 0.0048, 0.00012)
    biotech = eq_path(0.75, 0.0125, 0.00005, jump_p=0.012, jump_sd=0.13)

    A = {
        "GOLDX": (_ohlc_microstructure(gold, rng, 0.0006),
                  {"quoteType": "ETF", "longName": "Synthetic Gold Bullion Trust",
                   "category": "Commodities Precious Metals", "marketCap": 7e10}),
        "MEGACAP": (_ohlc_microstructure(eq_, rng, 0.0004),
                    {"quoteType": "EQUITY", "sector": "Technology",
                     "industry": "Software", "longName": "Synthetic Mega Cap Inc",
                     "marketCap": 9.5e11, "dividendYield": 0.006}),
        "LEV3X": (_ohlc_microstructure(lev, rng, 0.0018),
                  {"quoteType": "ETF", "longName": "Synthetic Daily 3X Bull Shares",
                   "category": "Trading--Leveraged Equity", "marketCap": 4e9}),
        "QUALCO": (_ohlc_microstructure(quality, rng, 0.0004),
                   {"quoteType": "EQUITY", "sector": "Technology",
                    "industry": "Software", "longName": "Synthetic Quality Co",
                    "marketCap": 4.2e11, "trailingPE": 27.5, "forwardPE": 23.0,
                    "priceToBook": 9.1, "returnOnEquity": 0.34,
                    "returnOnAssets": 0.16, "profitMargins": 0.245,
                    "operatingMargins": 0.31, "revenueGrowth": 0.14,
                    "earningsGrowth": 0.18, "debtToEquity": 32.0,
                    "currentRatio": 2.4, "freeCashflow": 2.1e10,
                    "totalCash": 5.5e10, "totalDebt": 1.2e10,
                    "dividendYield": 0.006, "payoutRatio": 0.18,
                    "heldPercentInstitutions": 0.74, "shortPercentOfFloat": 0.012,
                    "enterpriseValue": 3.9e11, "ebitda": 3.2e10,
                    "totalRevenue": 8.4e10, "beta": 1.05}),
        "HYPERG": (_ohlc_microstructure(hyper, rng, 0.0011),
                   {"quoteType": "EQUITY", "sector": "Technology",
                    "industry": "Software - Infrastructure",
                    "longName": "Synthetic Hypergrowth Inc",
                    "marketCap": 1.8e10, "priceToBook": 14.2,
                    "returnOnEquity": -0.22, "profitMargins": -0.185,
                    "operatingMargins": -0.12, "revenueGrowth": 0.46,
                    "debtToEquity": 18.0, "currentRatio": 3.8,
                    "freeCashflow": -3.2e8, "totalCash": 2.4e9,
                    "totalDebt": 3.0e8, "dividendYield": 0.0,
                    "heldPercentInstitutions": 0.55,
                    "shortPercentOfFloat": 0.081,
                    "enterpriseValue": 1.6e10, "totalRevenue": 1.1e9,
                    "beta": 1.9}),
        "INCOMEC": (_ohlc_microstructure(income, rng, 0.0005),
                    {"quoteType": "EQUITY", "sector": "Utilities",
                     "industry": "Utilities - Regulated Electric",
                     "longName": "Synthetic Income Utility Co",
                     "marketCap": 6.2e10, "trailingPE": 17.2, "forwardPE": 16.4,
                     "priceToBook": 1.9, "returnOnEquity": 0.11,
                     "profitMargins": 0.135, "operatingMargins": 0.22,
                     "revenueGrowth": 0.03, "debtToEquity": 142.0,
                     "currentRatio": 0.9, "freeCashflow": 1.6e9,
                     "totalCash": 8e8, "totalDebt": 3.1e10,
                     "dividendYield": 0.047, "payoutRatio": 0.79,
                     "heldPercentInstitutions": 0.68,
                     "shortPercentOfFloat": 0.021,
                     "enterpriseValue": 9.3e10, "ebitda": 8.4e9,
                     "totalRevenue": 2.2e10, "beta": 0.58}),
        "BIOJMP": (_ohlc_microstructure(biotech, rng, 0.0025),
                   {"quoteType": "EQUITY", "sector": "Healthcare",
                    "industry": "Biotechnology",
                    "longName": "Synthetic Clinical Biotech",
                    "marketCap": 2.1e9, "priceToBook": 4.4,
                    "returnOnEquity": -0.38, "profitMargins": -2.4,
                    "operatingMargins": -1.9, "revenueGrowth": 0.05,
                    "debtToEquity": 9.0, "currentRatio": 5.2,
                    "freeCashflow": -4.1e8, "totalCash": 9.2e8,
                    "totalDebt": 6e7, "dividendYield": 0.0,
                    "heldPercentInstitutions": 0.61,
                    "shortPercentOfFloat": 0.174,
                    "totalRevenue": 4.2e7, "beta": 1.35}),
    }
    return A, macro, proxies


def run_demo(outdir="./reports_demo", n_sims=6000):
    print("=" * 74)
    print("I ALWAYS WIN 오프라인 데모 — 자산군·성격별 렌즈 분기 확인")
    print("=" * 74)
    assets, macro, proxies = build_demo_world()
    cfg = replace(RUN, n_sims=n_sims, lookback_years=7)
    os.makedirs(outdir, exist_ok=True)
    rows, done = [], []
    for tk, (df, meta) in assets.items():
        print(f"\n{'-'*74}\n▶ {tk}\n{'-'*74}")
        t0 = time.time()
        res = analyze(tk, df=df, meta=meta, macro=macro, proxies=proxies,
                      cfg=cfg, aum_usd=2e7, with_options=False, verbose=True)
        done.append(res)
        base = os.path.join(outdir, f"JIQTX_{tk}_{res.asof}")
        save(res, base + ".md")
        save_html(res, base + ".html")
        secs = build_sections(res)
        eq = res.equity
        fm = res.factor_model
        rows.append({
            "티커": tk, "성격": (eq.archetype_ko if eq else "—"),
            "섹션": len(secs), "자산군": res.classification.spec.label_ko,
            "팩터 R²": f"{fm.r2:.1%}" if fm else "—",
            "ML": res.ml.verdict if res.ml else "—",
            "P(up) GBM→FHS":
                f"{res.sim.prob_up_naive_gbm:.0%}→{res.sim.prob_up:.0%}",
            "켈리 naive→제약":
                f"{res.sizing.kelly_naive:.0%}→"
                f"{res.sizing.kelly_uncertainty_adjusted:.0%}",
            "판정": res.verdict.grade,
            "사이즈": f"{res.verdict.risk_budget_weight:.1%}",
        })
        print(f"  → {os.path.basename(base)}.html · 섹션 {len(secs)}개 "
              f"({time.time()-t0:.0f}s)")

    print(f"\n{'-'*74}\n▶ 포트폴리오 ({len(done)}개 포지션)\n{'-'*74}")
    P = analyze_portfolio(done)
    pp = os.path.join(outdir, "JIQTX_PORTFOLIO.html")
    save_portfolio(P, pp, title="데모 북")
    r = P.risk
    print(f"  책 변동성 {r.vol_ann:.1%} · 유효베팅 {r.effective_bets:.2f}/"
          f"{len(r.tickers)} · 최대 위험집중 {r.max_pct_contribution:.0%}")
    print(f"  배분 경합: {P.allocation.winner} "
          f"(1/N 초과 입증 {P.allocation.beats_1n})")
    fails = P.limits[~P.limits["충족"]]
    if len(fails):
        print(f"  ⚠ 한도 위반: {', '.join(fails['한도'])}")
    print(f"  → {pp}")

    print(f"\n{'='*74}\n요약\n{'='*74}")
    print(pd.DataFrame(rows).to_string(index=False))
    print(f"\n리포트 폴더: {os.path.abspath(outdir)}")
