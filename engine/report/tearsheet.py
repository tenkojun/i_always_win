"""
tearsheet.py — Quantstats 스타일 PDF Tear Sheet (Tier 2 #10)
============================================================
백테스트 결과 또는 Paper Trading 이력으로부터 표준 월간/분기 PDF 리포트 생성.
matplotlib만 사용 (reportlab 불요) → 의존성 최소.

페이지 구성:
  1. 표지 + 핵심 KPI (수익률/Sharpe/MDD/PSR/DSR/Profit Factor)
  2. Equity curve + Drawdown
  3. Monthly returns heatmap
  4. Yearly returns + Distribution
  5. 거래 통계 (Best/Worst/Avg/PnL 분포)
  6. 데이터 신뢰성 경고 (data_caveats)
"""
from __future__ import annotations

import io
import math
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

# matplotlib agg (서버 환경)
try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.backends.backend_pdf import PdfPages
    _HAVE_MPL = True
except Exception:
    _HAVE_MPL = False


# 다크 테마 색상
_BG = "#0a0f16"
_TXT = "#cfe6ec"
_CYAN = "#3df0ff"
_UP = "#ff5a52"
_DOWN = "#4d9cff"
_AMBER = "#ffb44c"
_GRID = "#16202e"


def _setup_dark(ax):
    ax.set_facecolor(_BG)
    ax.tick_params(colors=_TXT, labelsize=8)
    ax.spines["bottom"].set_color(_GRID)
    ax.spines["left"].set_color(_GRID)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.grid(True, alpha=0.15, color=_CYAN, linewidth=0.3)


def _safe(v, d=2):
    try:
        f = float(v)
        if math.isnan(f) or math.isinf(f):
            return None
        return round(f, d)
    except Exception:
        return None


def build_strategy_tearsheet(backtest_result: Dict[str, Any]) -> Optional[bytes]:
    """run_backtest 결과 dict → PDF bytes. 실패 시 None."""
    if not _HAVE_MPL:
        return None
    if not backtest_result or not backtest_result.get("ok"):
        return None
    m = backtest_result.get("metrics") or {}
    eq_curve = backtest_result.get("equity_curve") or []
    if not eq_curve:
        return None
    # equity Series
    idx = pd.DatetimeIndex([pd.Timestamp(p["d"]) for p in eq_curve])
    vals = np.array([float(p["v"]) for p in eq_curve])
    eq_ser = pd.Series(vals, index=idx)
    rets = eq_ser.pct_change().dropna()

    buf = io.BytesIO()
    with PdfPages(buf) as pdf:
        _page_summary(pdf, backtest_result, eq_ser, rets, m)
        _page_equity_drawdown(pdf, backtest_result, eq_ser)
        _page_monthly_heatmap(pdf, backtest_result, rets)
        _page_distribution(pdf, backtest_result, rets)
        _page_caveats(pdf, backtest_result)
    return buf.getvalue()


# ─── 페이지 1: 요약 ──────────────────────────────────────────
def _page_summary(pdf, r, eq, rets, m):
    fig = plt.figure(figsize=(8.5, 11), facecolor=_BG)
    fig.patch.set_facecolor(_BG)
    # 타이틀
    fig.text(0.5, 0.95, "TEAR SHEET", color=_CYAN, fontsize=22,
              fontweight="bold", ha="center", family="monospace")
    fig.text(0.5, 0.92,
              f"{r.get('ticker','—')} · {r.get('strategy','—')}",
              color=_TXT, fontsize=13, ha="center", family="monospace")
    fig.text(0.5, 0.895,
              f"기간 {r.get('n_bars', len(eq))}봉 · {r.get('interval','1d')}봉 "
              f"· {eq.index[0].strftime('%Y-%m-%d')} → "
              f"{eq.index[-1].strftime('%Y-%m-%d')}",
              color="#88a", fontsize=9, ha="center", family="monospace")

    # KPI 카드 (2×4)
    kpis = [
        ("총 수익률",    f"{(m.get('total_return') or 0)*100:+.2f}%",
            _UP if (m.get('total_return') or 0) >= 0 else _DOWN),
        ("Buy & Hold",   f"{(m.get('buy_hold_return') or 0)*100:+.2f}%", _TXT),
        ("α (알파)",     f"{(m.get('alpha') or 0)*100:+.2f}%",
            _UP if (m.get('alpha') or 0) >= 0 else _DOWN),
        ("거래 횟수",    str(m.get('n_trades') or 0), _TXT),
        ("Sharpe",       f"{m.get('sharpe') or 0:.2f}", _CYAN),
        ("Sortino",      f"{m.get('sortino') or 0:.2f}", _CYAN),
        ("Max Drawdown", f"{(m.get('max_drawdown') or 0)*100:.2f}%", _DOWN),
        ("Win Rate",     f"{(m.get('win_rate') or 0)*100:.1f}%", _AMBER),
    ]
    for i, (lab, val, col) in enumerate(kpis):
        row, c = i // 4, i % 4
        x = 0.06 + c * 0.235
        y = 0.78 - row * 0.10
        fig.text(x, y + 0.025, lab, color="#88a", fontsize=8,
                  family="monospace")
        fig.text(x, y - 0.015, val, color=col, fontsize=16,
                  fontweight="bold", family="monospace")

    # 통계 유의도
    fig.text(0.06, 0.59, "▸ 통계 유의도", color=_CYAN, fontsize=11,
              fontweight="bold", family="monospace")
    fig.text(0.06, 0.56,
              f"PSR (Probabilistic Sharpe): {m.get('psr') or 0:.3f}",
              color=_TXT, fontsize=10, family="monospace")
    fig.text(0.06, 0.535,
              f"DSR (Deflated Sharpe):       {m.get('dsr') or 0:.3f}",
              color=_TXT, fontsize=10, family="monospace")
    # 해석
    psr = m.get('psr') or 0
    if psr >= 0.95:
        interp = "✓ 매우 신뢰 (Sharpe > 0 확률 95%+)"
    elif psr >= 0.7:
        interp = "양호 (Sharpe > 0 확률 70%+)"
    else:
        interp = "⚠ 통계 유의 부족 — 우연일 가능성"
    fig.text(0.06, 0.51, interp, color=_AMBER, fontsize=9,
              style="italic", family="monospace")

    # 파라미터
    fig.text(0.06, 0.46, "▸ 파라미터", color=_CYAN, fontsize=11,
              fontweight="bold", family="monospace")
    params = r.get('params') or {}
    for i, (k, v) in enumerate(list(params.items())[:8]):
        fig.text(0.06, 0.43 - i*0.022, f"{k} = {v}",
                  color=_TXT, fontsize=9, family="monospace")

    # equity curve mini
    ax = fig.add_axes([0.06, 0.05, 0.88, 0.18], facecolor=_BG)
    _setup_dark(ax)
    ax.plot(eq.index, eq.values, color=_CYAN, lw=1.3)
    peak = eq.cummax()
    ax.fill_between(eq.index, eq.values, peak.values,
                    where=eq < peak, alpha=0.15, color=_DOWN)
    ax.set_title("Equity Curve (initial $10,000)", color=_CYAN,
                  fontsize=10, family="monospace", loc="left")
    pdf.savefig(fig, facecolor=_BG)
    plt.close(fig)


# ─── 페이지 2: Equity + Drawdown ──────────────────────────────
def _page_equity_drawdown(pdf, r, eq):
    fig = plt.figure(figsize=(8.5, 11), facecolor=_BG)
    fig.text(0.5, 0.95, "EQUITY · DRAWDOWN", color=_CYAN, fontsize=16,
              fontweight="bold", ha="center", family="monospace")
    # Equity (위)
    ax1 = fig.add_axes([0.08, 0.55, 0.85, 0.35], facecolor=_BG)
    _setup_dark(ax1)
    peak = eq.cummax()
    ax1.plot(eq.index, eq.values, color=_CYAN, lw=1.5, label="Equity")
    ax1.plot(eq.index, peak.values, color=_TXT, lw=0.6,
              alpha=0.4, label="Peak")
    ax1.fill_between(eq.index, eq.values, peak.values,
                      where=eq < peak, alpha=0.20, color=_DOWN)
    ax1.set_title("Equity Curve", color=_CYAN, fontsize=11,
                   family="monospace", loc="left")
    ax1.legend(facecolor=_BG, edgecolor=_GRID, labelcolor=_TXT,
                fontsize=8)
    # Drawdown (아래)
    ax2 = fig.add_axes([0.08, 0.12, 0.85, 0.35], facecolor=_BG)
    _setup_dark(ax2)
    dd = (eq - peak) / peak * 100
    ax2.fill_between(dd.index, dd.values, 0, color=_DOWN, alpha=0.5)
    ax2.plot(dd.index, dd.values, color=_DOWN, lw=1.2)
    ax2.set_title("Drawdown (%)", color=_DOWN, fontsize=11,
                   family="monospace", loc="left")
    ax2.set_ylabel("%", color=_TXT, fontsize=9)
    pdf.savefig(fig, facecolor=_BG)
    plt.close(fig)


# ─── 페이지 3: 월별 히트맵 ─────────────────────────────────────
def _page_monthly_heatmap(pdf, r, rets):
    if len(rets) < 30:
        return
    monthly = rets.resample("M").apply(lambda x: (1+x).prod() - 1)
    years = sorted(set(monthly.index.year))
    if len(years) < 1:
        return
    months = ["Jan","Feb","Mar","Apr","May","Jun",
              "Jul","Aug","Sep","Oct","Nov","Dec"]
    mat = np.full((len(years), 12), np.nan)
    for ts, v in monthly.items():
        i = years.index(ts.year)
        j = ts.month - 1
        mat[i, j] = v * 100
    fig = plt.figure(figsize=(8.5, 11), facecolor=_BG)
    fig.text(0.5, 0.95, "MONTHLY RETURNS (%)", color=_CYAN, fontsize=16,
              fontweight="bold", ha="center", family="monospace")
    ax = fig.add_axes([0.10, 0.10, 0.83, 0.78], facecolor=_BG)
    vmax = max(abs(np.nanmin(mat)), abs(np.nanmax(mat)), 1)
    # 한국식: 빨강=수익, 파랑=손실
    from matplotlib.colors import LinearSegmentedColormap
    cmap = LinearSegmentedColormap.from_list("kor",
        [_DOWN, _GRID, _UP], N=256)
    im = ax.imshow(mat, aspect="auto", cmap=cmap,
                    vmin=-vmax, vmax=vmax)
    # 셀에 값 표시
    for i in range(mat.shape[0]):
        for j in range(mat.shape[1]):
            v = mat[i, j]
            if not np.isnan(v):
                ax.text(j, i, f"{v:+.1f}", ha="center", va="center",
                         fontsize=8, color="#fff", family="monospace")
    ax.set_xticks(range(12))
    ax.set_xticklabels(months, color=_TXT, fontsize=9)
    ax.set_yticks(range(len(years)))
    ax.set_yticklabels([str(y) for y in years], color=_TXT, fontsize=9)
    ax.set_title("월별 수익률 (%) — 빨강=수익, 파랑=손실 (한국식)",
                  color=_CYAN, fontsize=10, family="monospace", loc="left",
                  pad=10)
    cbar = plt.colorbar(im, ax=ax, fraction=0.04, pad=0.02)
    cbar.ax.tick_params(colors=_TXT, labelsize=8)
    pdf.savefig(fig, facecolor=_BG)
    plt.close(fig)


# ─── 페이지 4: Distribution ──────────────────────────────────
def _page_distribution(pdf, r, rets):
    if len(rets) < 10:
        return
    fig = plt.figure(figsize=(8.5, 11), facecolor=_BG)
    fig.text(0.5, 0.95, "RETURNS DISTRIBUTION", color=_CYAN,
              fontsize=16, fontweight="bold", ha="center",
              family="monospace")
    # 히스토그램
    ax1 = fig.add_axes([0.08, 0.55, 0.85, 0.35], facecolor=_BG)
    _setup_dark(ax1)
    ax1.hist(rets.values * 100, bins=40, color=_CYAN, alpha=0.7,
              edgecolor=_GRID)
    ax1.axvline(0, color=_TXT, lw=0.8, linestyle="--")
    ax1.set_title("Daily Returns Histogram (%)", color=_CYAN,
                   fontsize=11, family="monospace", loc="left")
    ax1.set_xlabel("Return (%)", color=_TXT, fontsize=9)
    # 통계 박스
    skew_v = float(rets.skew())
    kurt_v = float(rets.kurt())
    var95 = float(np.percentile(rets.values * 100, 5))
    cvar95 = float(rets[rets <= rets.quantile(0.05)].mean() * 100)
    stat_txt = (
        f"평균 (daily): {rets.mean()*100:+.3f}%\n"
        f"표준편차:      {rets.std()*100:.3f}%\n"
        f"왜도 (skew):   {skew_v:+.3f}\n"
        f"첨도 (kurt):   {kurt_v:+.3f}\n"
        f"VaR 95%:       {var95:.2f}%\n"
        f"CVaR 95%:      {cvar95:.2f}%"
    )
    fig.text(0.10, 0.42, stat_txt, color=_TXT, fontsize=10,
              family="monospace",
              bbox=dict(facecolor=_GRID, edgecolor=_CYAN, pad=10))
    # 누적 분포 (QQ-like)
    ax2 = fig.add_axes([0.08, 0.08, 0.85, 0.28], facecolor=_BG)
    _setup_dark(ax2)
    sorted_rets = np.sort(rets.values * 100)
    q = np.linspace(0, 1, len(sorted_rets))
    ax2.plot(q, sorted_rets, color=_AMBER, lw=1.2)
    ax2.axhline(0, color=_TXT, lw=0.5, alpha=0.4)
    ax2.set_title("Sorted Returns (CDF-like)", color=_CYAN,
                   fontsize=11, family="monospace", loc="left")
    ax2.set_xlabel("Percentile", color=_TXT, fontsize=9)
    ax2.set_ylabel("Daily Return (%)", color=_TXT, fontsize=9)
    pdf.savefig(fig, facecolor=_BG)
    plt.close(fig)


# ─── 페이지 5: 데이터 신뢰성 경고 ─────────────────────────────
def _page_caveats(pdf, r):
    dc = r.get("data_caveats") or {}
    warnings = dc.get("warnings") or []
    fig = plt.figure(figsize=(8.5, 11), facecolor=_BG)
    fig.text(0.5, 0.95, "DATA CAVEATS · 데이터 한계", color=_AMBER,
              fontsize=16, fontweight="bold", ha="center",
              family="monospace")
    fig.text(0.5, 0.92,
              "백테스트 결과를 실거래로 옮길 때 보수적으로 해석하세요",
              color=_TXT, fontsize=10, ha="center", family="monospace",
              style="italic")
    y = 0.85
    for w in warnings:
        sev = w.get("severity", "low")
        col = {"high": _DOWN, "medium": _AMBER, "low": _TXT}.get(sev, _TXT)
        icon = {"high": "⚠", "medium": "!", "low": "ⓘ"}.get(sev, "·")
        fig.text(0.08, y, f"{icon} {w.get('title', '')}", color=col,
                  fontsize=12, fontweight="bold", family="monospace")
        # 본문 wrap
        msg = w.get("msg", "")
        lines = []
        cur = ""
        for word in msg.split():
            if len(cur) + len(word) > 70:
                lines.append(cur); cur = word
            else:
                cur = (cur + " " + word).strip()
        if cur: lines.append(cur)
        for line in lines:
            y -= 0.025
            fig.text(0.10, y, line, color=_TXT, fontsize=9,
                      family="monospace")
        y -= 0.04
        if y < 0.10:
            break
    fig.text(0.5, 0.05,
              f"Generated by JIQT · 데이터 한계 {len(warnings)}건",
              color=_AMBER, fontsize=9, ha="center", family="monospace",
              style="italic")
    pdf.savefig(fig, facecolor=_BG)
    plt.close(fig)


# ════════════════════════════════════════════════════════════
#  Paper Trading Tear Sheet (별도)
# ════════════════════════════════════════════════════════════
def build_paper_tearsheet(user_id: int) -> Optional[bytes]:
    """Paper Trading 거래 이력 + NAV 추이 → PDF."""
    if not _HAVE_MPL:
        return None
    try:
        from ..trading.paper_trading import get_paper_engine
        from ..auth.store import _LOCK, _conn
    except Exception:
        return None
    eng = get_paper_engine(user_id)
    state = eng.get_state()
    orders = eng.get_orders(limit=200)
    if not state.get("ok"):
        return None
    # NAV 시계열 (paper_nav 테이블)
    with _LOCK:
        nav_rows = _conn().execute(
            "SELECT date, nav, cash, equity_value FROM paper_nav "
            "WHERE user_id=? ORDER BY date", (user_id,)).fetchall()
    nav_df = pd.DataFrame([dict(r) for r in nav_rows]) if nav_rows else pd.DataFrame()
    buf = io.BytesIO()
    with PdfPages(buf) as pdf:
        # 페이지 1: Paper 요약
        fig = plt.figure(figsize=(8.5, 11), facecolor=_BG)
        fig.text(0.5, 0.95, "PAPER TRADING REPORT", color=_CYAN,
                  fontsize=20, fontweight="bold", ha="center",
                  family="monospace")
        # KPI
        kpis = [
            ("초기 자본", f"{state['initial_cash']:,.0f}원", _TXT),
            ("현재 NAV",  f"{state['nav']:,.0f}원", _CYAN),
            ("총 손익",   f"{state['total_pnl']:+,.0f}원",
                _UP if state['total_pnl']>=0 else _DOWN),
            ("수익률",    f"{state['total_pnl_pct']*100:+.2f}%",
                _UP if state['total_pnl_pct']>=0 else _DOWN),
            ("현금",      f"{state['cash']:,.0f}원", _TXT),
            ("평가금액",  f"{state['equity_value']:,.0f}원", _TXT),
            ("포지션",    f"{state['n_positions']}종목", _AMBER),
            ("총 주문",   f"{len(orders)}건", _TXT),
        ]
        for i, (lab, val, col) in enumerate(kpis):
            row, c = i // 2, i % 2
            x = 0.10 + c * 0.45
            y = 0.85 - row * 0.07
            fig.text(x, y, lab, color="#88a", fontsize=9,
                      family="monospace")
            fig.text(x, y - 0.025, val, color=col, fontsize=14,
                      fontweight="bold", family="monospace")
        # NAV 차트
        if not nav_df.empty:
            ax = fig.add_axes([0.08, 0.10, 0.85, 0.35], facecolor=_BG)
            _setup_dark(ax)
            dts = pd.to_datetime(nav_df["date"])
            ax.plot(dts, nav_df["nav"], color=_CYAN, lw=1.5, label="NAV")
            ax.fill_between(dts, state["initial_cash"], nav_df["nav"],
                             where=nav_df["nav"] >= state["initial_cash"],
                             alpha=0.2, color=_UP)
            ax.fill_between(dts, state["initial_cash"], nav_df["nav"],
                             where=nav_df["nav"] < state["initial_cash"],
                             alpha=0.2, color=_DOWN)
            ax.axhline(state["initial_cash"], color=_TXT, lw=0.5,
                        linestyle="--", alpha=0.5)
            ax.set_title("NAV 추이", color=_CYAN, fontsize=11,
                          family="monospace", loc="left")
            ax.legend(facecolor=_BG, edgecolor=_GRID, labelcolor=_TXT)
        else:
            fig.text(0.5, 0.3, "NAV 스냅샷 없음 — 매일 NAV 저장 권장",
                      color=_AMBER, fontsize=11, ha="center",
                      family="monospace")
        pdf.savefig(fig, facecolor=_BG)
        plt.close(fig)

        # 페이지 2: 주문 이력 표 (최근 50건)
        if orders:
            fig = plt.figure(figsize=(8.5, 11), facecolor=_BG)
            fig.text(0.5, 0.95, "주문 이력 (최근 50)", color=_CYAN,
                      fontsize=14, fontweight="bold", ha="center",
                      family="monospace")
            y = 0.90
            fig.text(0.08, y, "시각", color=_CYAN, fontsize=9,
                      family="monospace")
            fig.text(0.22, y, "종목", color=_CYAN, fontsize=9,
                      family="monospace")
            fig.text(0.35, y, "구분", color=_CYAN, fontsize=9,
                      family="monospace")
            fig.text(0.43, y, "수량", color=_CYAN, fontsize=9,
                      family="monospace")
            fig.text(0.55, y, "가격", color=_CYAN, fontsize=9,
                      family="monospace")
            fig.text(0.70, y, "금액", color=_CYAN, fontsize=9,
                      family="monospace")
            fig.text(0.85, y, "출처", color=_CYAN, fontsize=9,
                      family="monospace")
            for o in orders[:50]:
                y -= 0.017
                if y < 0.08:
                    break
                sideCol = _UP if o["side"] == "buy" else _DOWN
                t = (o.get("ts") or "")[:16].replace("T", " ")
                fig.text(0.08, y, t, color=_TXT, fontsize=7,
                          family="monospace")
                fig.text(0.22, y, str(o.get("ticker", "")), color=_CYAN,
                          fontsize=8, family="monospace")
                fig.text(0.35, y,
                          "매수" if o["side"] == "buy" else "매도",
                          color=sideCol, fontsize=8, family="monospace")
                fig.text(0.43, y, str(o.get("qty", "")), color=_TXT,
                          fontsize=8, family="monospace")
                fig.text(0.55, y, f"{float(o.get('price',0)):,.0f}",
                          color=_TXT, fontsize=8, family="monospace")
                fig.text(0.70, y, f"{float(o.get('amount',0)):,.0f}",
                          color=_TXT, fontsize=8, family="monospace")
                fig.text(0.85, y, str(o.get("source", ""))[:14],
                          color="#88a", fontsize=7, family="monospace")
            pdf.savefig(fig, facecolor=_BG)
            plt.close(fig)
    return buf.getvalue()


# ════════════════════════════════════════════════════════════
#  🌌 Kronos 예측 정확도 Tear Sheet (P0 #2)
# ════════════════════════════════════════════════════════════
def build_kronos_tearsheet(ticker: str,
                            model_key: str = "kronos_small",
                            period_days: int = 730,
                            slide_step: int = 20,
                            lookback: int = 128,
                            pred_len: int = 5) -> Optional[bytes]:
    """Walk-forward 스타일 Kronos 예측 vs 실제 비교 보고서.

    매 slide_step 봉마다 예측 → 실제 후행 pred_len 봉과 비교.
    방향 정확도 + MAE/RMSE + 상관계수 + 시계열 시각화.
    """
    if not _HAVE_MPL:
        return None
    try:
        from ..strategy.kronos_predictor import (kronos_predict,
                                                   check_model_downloaded)
        from ..strategy.vbt_runner import _fetch_ohlcv
    except Exception:
        return None

    chk = check_model_downloaded(model_key)
    if not chk.get("downloaded"):
        return None

    df = _fetch_ohlcv(ticker, period_days=period_days, interval="1d")
    if df is None or len(df) < lookback + pred_len + 30:
        return None
    n = len(df)
    # Walk-forward — 매 slide_step 봉마다 예측, 실제 가격과 비교
    samples: List[Dict[str, Any]] = []
    for i in range(lookback, n - pred_len, slide_step):
        sub = df.iloc[:i]
        try:
            r = kronos_predict(sub, model_key=model_key,
                                lookback=lookback, pred_len=pred_len,
                                sample_count=1)
            if not r.get("ok"):
                continue
            actual_end_close = float(df.iloc[i + pred_len - 1]["close"])
            actual_start_close = float(df.iloc[i - 1]["close"])
            actual_return = (actual_end_close / actual_start_close - 1
                              if actual_start_close > 0 else 0)
            pred_return = float(r.get("predicted_return") or 0)
            pred_end_close = float(r.get("predicted_end_close") or 0)
            samples.append({
                "date":      str(df.index[i].date()),
                "i":         i,
                "actual_close":     actual_end_close,
                "predicted_close":  pred_end_close,
                "actual_return":    actual_return,
                "predicted_return": pred_return,
                "direction_match":  ((actual_return > 0) ==
                                     (pred_return > 0)),
            })
        except Exception:
            continue

    if not samples:
        return None

    s_df = pd.DataFrame(samples)
    s_df["date"] = pd.to_datetime(s_df["date"])
    s_df["error"] = (s_df["predicted_close"] - s_df["actual_close"]) / s_df["actual_close"]
    s_df["abs_error"] = s_df["error"].abs()

    # 메트릭
    n_samples = len(s_df)
    dir_acc = float(s_df["direction_match"].mean())
    mae = float(s_df["abs_error"].mean())
    rmse = float(np.sqrt((s_df["error"] ** 2).mean()))
    try:
        corr = float(s_df["actual_return"].corr(s_df["predicted_return"]))
    except Exception:
        corr = float("nan")
    # 상승 시 정확도
    up_mask = s_df["actual_return"] > 0
    down_mask = s_df["actual_return"] < 0
    up_acc = float(s_df.loc[up_mask, "direction_match"].mean()) if up_mask.any() else float("nan")
    down_acc = float(s_df.loc[down_mask, "direction_match"].mean()) if down_mask.any() else float("nan")

    buf = io.BytesIO()
    with PdfPages(buf) as pdf:
        # 페이지 1: 표지 + KPI
        fig = plt.figure(figsize=(8.5, 11), facecolor=_BG)
        fig.text(0.5, 0.92, "🌌 KRONOS FORECAST ACCURACY",
                  ha="center", color=_CYAN, fontsize=20,
                  family="monospace", weight="bold")
        fig.text(0.5, 0.88, f"{ticker} · {model_key} · "
                              f"{pd.Timestamp.now().strftime('%Y-%m-%d')}",
                  ha="center", color=_TXT, fontsize=11,
                  family="monospace")
        fig.text(0.5, 0.85, f"walk-forward · {n_samples} samples · "
                              f"lookback={lookback} pred_len={pred_len} "
                              f"slide={slide_step}",
                  ha="center", color="#88a", fontsize=9,
                  family="monospace")

        def _kpi(x, y, label, val, color):
            fig.text(x, y, label.upper(), color="#88a", fontsize=9,
                      family="monospace")
            fig.text(x, y - 0.045, val, color=color, fontsize=24,
                      family="monospace", weight="bold")

        # KPI grid (2x3)
        col1, col2 = 0.12, 0.55
        rows = [0.72, 0.58, 0.44]
        _kpi(col1, rows[0], "Direction Accuracy",
             f"{dir_acc*100:.1f}%",
             _CYAN if dir_acc >= 0.55 else _AMBER if dir_acc >= 0.50 else _UP)
        _kpi(col2, rows[0], "Samples",
             f"{n_samples}", _TXT)
        _kpi(col1, rows[1], "MAE",
             f"{mae*100:.2f}%", _AMBER)
        _kpi(col2, rows[1], "RMSE",
             f"{rmse*100:.2f}%", _AMBER)
        _kpi(col1, rows[2], "Correlation",
             f"{corr:+.3f}" if corr == corr else "—",
             _CYAN if (corr == corr and corr > 0.2) else _TXT)
        _kpi(col2, rows[2], "Up/Down Acc",
             (f"↑{up_acc*100:.0f}% ↓{down_acc*100:.0f}%"
              if up_acc==up_acc and down_acc==down_acc else "—"),
             _TXT)

        # 평가 문구
        verdict = ("강함 (>55%)" if dir_acc >= 0.55 else
                    "보통 (≥50%)" if dir_acc >= 0.50 else
                    "약함 (랜덤 수준)")
        v_color = (_CYAN if dir_acc >= 0.55 else
                    _AMBER if dir_acc >= 0.50 else _UP)
        fig.text(0.5, 0.25, f"Forecast Quality: {verdict}",
                  ha="center", color=v_color, fontsize=14,
                  family="monospace", weight="bold")
        fig.text(0.5, 0.20,
                  "랜덤(50%) 대비 5%p 이상 우위면 의미 있음. "
                  "30+ 샘플 권장.",
                  ha="center", color="#88a", fontsize=9,
                  family="monospace")
        fig.text(0.5, 0.13,
                  "⚠ Zero-shot 결과. 한국 종목은 fine-tuning 권장.",
                  ha="center", color=_AMBER, fontsize=9,
                  family="monospace")
        pdf.savefig(fig, facecolor=_BG)
        plt.close(fig)

        # 페이지 2: 시계열 (실제 vs 예측 가격)
        fig, axes = plt.subplots(2, 1, figsize=(8.5, 11), facecolor=_BG,
                                   gridspec_kw={"height_ratios": [2, 1]})
        ax1, ax2 = axes
        _setup_dark(ax1); _setup_dark(ax2)
        # 실제 close 전체
        ax1.plot(df.index, df["close"], color=_TXT, linewidth=0.7,
                  alpha=0.6, label="actual close")
        # 예측 종점 close (sample마다 점)
        ax1.scatter(s_df["date"], s_df["predicted_close"],
                     color=_CYAN, s=14, alpha=0.85, label="predicted end-close")
        # 방향 일치 표시
        match_df = s_df[s_df["direction_match"]]
        miss_df = s_df[~s_df["direction_match"]]
        ax1.scatter(match_df["date"], match_df["actual_close"],
                     color=_CYAN, marker="^", s=10, alpha=0.3)
        ax1.scatter(miss_df["date"], miss_df["actual_close"],
                     color=_UP, marker="v", s=10, alpha=0.3)
        ax1.set_title(f"{ticker} — Actual vs Predicted",
                       color=_CYAN, fontsize=11, family="monospace")
        ax1.legend(loc="upper left", facecolor=_BG, edgecolor=_GRID,
                    labelcolor=_TXT, fontsize=8)
        # 오차 시계열
        ax2.bar(s_df["date"], s_df["error"] * 100,
                 color=[_CYAN if e >= 0 else _UP for e in s_df["error"]],
                 width=2)
        ax2.axhline(0, color=_TXT, linewidth=0.5)
        ax2.set_title("Prediction Error (%)",
                       color=_CYAN, fontsize=10, family="monospace")
        ax2.set_ylabel("error (%)", color=_TXT, fontsize=8)
        fig.tight_layout()
        pdf.savefig(fig, facecolor=_BG)
        plt.close(fig)

        # 페이지 3: 산점도 + 오차 분포
        fig, axes = plt.subplots(1, 2, figsize=(11, 5.5), facecolor=_BG)
        ax1, ax2 = axes
        _setup_dark(ax1); _setup_dark(ax2)
        # 산점도 — 실제 수익률 vs 예측 수익률
        colors = [_CYAN if m else _UP for m in s_df["direction_match"]]
        ax1.scatter(s_df["predicted_return"] * 100,
                     s_df["actual_return"] * 100, c=colors, s=18, alpha=0.7)
        ax1.axhline(0, color="#88a", linewidth=0.5, alpha=0.6)
        ax1.axvline(0, color="#88a", linewidth=0.5, alpha=0.6)
        # y = x 대각선
        lim = max(abs(s_df["actual_return"].abs().max()),
                   abs(s_df["predicted_return"].abs().max())) * 100
        ax1.plot([-lim, lim], [-lim, lim], color=_AMBER,
                  linewidth=0.7, alpha=0.6, linestyle="--")
        ax1.set_xlabel("Predicted Return (%)", color=_TXT, fontsize=9)
        ax1.set_ylabel("Actual Return (%)", color=_TXT, fontsize=9)
        ax1.set_title("Predicted vs Actual", color=_CYAN,
                       fontsize=11, family="monospace")
        # 오차 히스토그램
        ax2.hist(s_df["error"] * 100, bins=20, color=_CYAN,
                  alpha=0.7, edgecolor=_BG)
        ax2.axvline(0, color=_AMBER, linewidth=1)
        ax2.axvline(s_df["error"].mean() * 100, color=_UP,
                     linewidth=1, linestyle="--")
        ax2.set_xlabel("Error (%)", color=_TXT, fontsize=9)
        ax2.set_ylabel("Frequency", color=_TXT, fontsize=9)
        ax2.set_title("Error Distribution", color=_CYAN,
                       fontsize=11, family="monospace")
        fig.tight_layout()
        pdf.savefig(fig, facecolor=_BG)
        plt.close(fig)

    return buf.getvalue()
