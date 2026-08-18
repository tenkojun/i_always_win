"""
시각화 모듈 (Plotter)
=====================
모든 차트는 (1) matplotlib Figure 객체와 (2) base64 PNG 두 형태를
함께 반환한다. HTML 리포트에 인라인 삽입할 수 있도록.

한글 표시
---------
패키지에 한글 폰트(NotoSansKR-Engine.ttf)를 **동봉**하여 어떤 환경
(Colab·서버·오프라인)에서도 한글이 깨지지 않도록 한다.

순서:
  1) 동봉 폰트를 matplotlib 에 직접 등록 (가장 확실, 인터넷 불필요)
  2) 실패 시 시스템에 설치된 한글 폰트 탐색
  3) 그래도 없으면 시스템 한글 폰트 경로 직접 스캔
이 함수는 import 시 자동 실행되며, 필요하면 외부에서
``setup_korean_font()`` 로 다시 호출할 수도 있다.
"""
from __future__ import annotations
import base64
import io
import os
import glob
from typing import Dict, Optional
import matplotlib
import matplotlib.pyplot as plt
from matplotlib import font_manager
import numpy as np
import pandas as pd

# ------------------------------------------------------------------ #
# 한글 폰트 설정
# ------------------------------------------------------------------ #
# 패키지에 동봉된 폰트 파일 경로
_BUNDLED_FONT = os.path.join(
    os.path.dirname(__file__), "assets", "NotoSansKR-Engine.ttf"
)

# 시스템에 흔히 깔려 있는 한글 폰트 후보 경로(2차 폴백)
_SYS_FONT_GLOBS = [
    "/usr/share/fonts/truetype/nanum/Nanum*.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK*.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK*.ttc",
    "/Library/Fonts/AppleGothic.ttf",
    "C:/Windows/Fonts/malgun.ttf",
]


def setup_korean_font(verbose: bool = False) -> Optional[str]:
    """한글 폰트를 등록하고 matplotlib 기본 글꼴로 지정한다.

    Returns
    -------
    설정된 폰트 패밀리 이름(성공) 또는 None(실패).
    """
    matplotlib.rcParams["axes.unicode_minus"] = False  # 마이너스 부호 깨짐 방지

    # 1) 동봉 폰트 우선 — 인터넷/설치 불필요, 항상 성공해야 함
    if os.path.exists(_BUNDLED_FONT):
        try:
            font_manager.fontManager.addfont(_BUNDLED_FONT)
            fam = font_manager.FontProperties(
                fname=_BUNDLED_FONT).get_name()
            matplotlib.rcParams["font.family"] = fam
            if verbose:
                print(f"[font] 동봉 폰트 사용: {fam}")
            return fam
        except Exception as e:  # pragma: no cover
            if verbose:
                print(f"[font] 동봉 폰트 등록 실패: {e}")

    # 2) 이미 등록된 한글 폰트 탐색
    candidates = ["NanumGothic", "NanumBarunGothic", "Malgun Gothic",
                  "AppleGothic", "Noto Sans CJK KR", "Noto Sans KR"]
    available = {f.name for f in font_manager.fontManager.ttflist}
    for c in candidates:
        if c in available:
            matplotlib.rcParams["font.family"] = c
            if verbose:
                print(f"[font] 시스템 등록 폰트 사용: {c}")
            return c

    # 3) 시스템 폰트 파일 경로 직접 스캔 후 등록
    for pat in _SYS_FONT_GLOBS:
        for path in sorted(glob.glob(pat)):
            try:
                font_manager.fontManager.addfont(path)
                fam = font_manager.FontProperties(fname=path).get_name()
                matplotlib.rcParams["font.family"] = fam
                if verbose:
                    print(f"[font] 시스템 폰트 파일 사용: {path} -> {fam}")
                return fam
            except Exception:
                continue

    if verbose:
        print("[font] 한글 폰트를 찾지 못함 — 한글이 깨질 수 있습니다.")
    return None


# 하위 호환용 별칭
_setup_korean_font = setup_korean_font
setup_korean_font()


def _fig_to_b64(fig) -> str:
    """matplotlib Figure → base64 PNG 문자열."""
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=120, bbox_inches="tight")
    buf.seek(0)
    return base64.b64encode(buf.read()).decode("ascii")


# ------------------------------------------------------------------ #
def plot_price_with_ma(df: pd.DataFrame, ma_fast: int = 20, ma_slow: int = 60,
                       title: str = "가격 + 이동평균") -> Dict:
    """가격 + 단기/장기 이동평균."""
    close = df["close"]
    fig, ax = plt.subplots(figsize=(9, 4))
    ax.plot(close.index, close.values, lw=1.2, color="black", label="종가")
    ax.plot(close.rolling(ma_fast).mean(), lw=1, color="tab:blue",
            label=f"SMA{ma_fast}")
    ax.plot(close.rolling(ma_slow).mean(), lw=1, color="tab:red",
            label=f"SMA{ma_slow}")
    ax.set_title(title); ax.legend(); ax.grid(True, alpha=0.3)
    return {"fig": fig, "png_b64": _fig_to_b64(fig)}


def plot_returns_hist(returns: pd.Series, title: str = "수익률 분포") -> Dict:
    fig, ax = plt.subplots(figsize=(7, 4))
    ax.hist(returns.dropna(), bins=80, color="steelblue", alpha=0.8)
    ax.axvline(returns.mean(), color="red", ls="--", lw=1, label="평균")
    ax.set_title(title); ax.legend(); ax.grid(True, alpha=0.3)
    return {"fig": fig, "png_b64": _fig_to_b64(fig)}


def plot_drawdown(equity: pd.Series, title: str = "낙폭(Drawdown)") -> Dict:
    dd = equity / equity.cummax() - 1
    fig, ax = plt.subplots(figsize=(9, 3))
    ax.fill_between(dd.index, dd.values, 0, color="red", alpha=0.4)
    ax.set_title(title); ax.set_ylabel("DD"); ax.grid(True, alpha=0.3)
    return {"fig": fig, "png_b64": _fig_to_b64(fig)}


def plot_montecarlo(sim: Dict, title: str = "몬테카를로 경로") -> Dict:
    eq = sim["simulated_equity"]
    fig, ax = plt.subplots(figsize=(9, 4))
    sample = eq[np.random.choice(len(eq), size=min(100, len(eq)), replace=False)]
    for s in sample:
        ax.plot(s, lw=0.4, alpha=0.3, color="steelblue")
    ax.plot(sim["median_path"], color="black", lw=2, label="중앙값")
    ax.plot(sim["ci_low"],  color="red",   ls="--", lw=1.0, label="5%")
    ax.plot(sim["ci_high"], color="green", ls="--", lw=1.0, label="95%")
    ax.set_title(title); ax.legend(); ax.grid(True, alpha=0.3)
    return {"fig": fig, "png_b64": _fig_to_b64(fig)}


def plot_cvd(prices: pd.Series, cvd_series: pd.Series,
             title: str = "가격 + 누적거래량델타(CVD)") -> Dict:
    fig, ax1 = plt.subplots(figsize=(9, 4))
    ax1.plot(prices.index, prices.values, color="black", lw=1, label="가격")
    ax1.set_ylabel("가격"); ax1.grid(True, alpha=0.3)
    ax2 = ax1.twinx()
    ax2.plot(cvd_series.index, cvd_series.values, color="orange", lw=1)
    ax2.set_ylabel("CVD")
    ax1.set_title(title)
    return {"fig": fig, "png_b64": _fig_to_b64(fig)}


def plot_regimes(prices: pd.Series, regimes: pd.Series,
                 title: str = "시장 국면(Regime)") -> Dict:
    df = pd.concat([prices.rename("p"), regimes.rename("r")], axis=1).dropna()
    fig, ax = plt.subplots(figsize=(9, 4))
    for k, sub in df.groupby("r"):
        ax.scatter(sub.index, sub["p"], label=f"국면 {k}", s=4)
    ax.set_title(title); ax.legend(markerscale=3); ax.grid(True, alpha=0.3)
    return {"fig": fig, "png_b64": _fig_to_b64(fig)}


def plot_score_bars(timeframe_scores: Dict[str, float],
                    title: str = "단/중/장 종합 점수") -> Dict:
    """단/중/장 점수 막대 차트."""
    names = list(timeframe_scores.keys())
    values = list(timeframe_scores.values())
    colors = ["#4caf50" if v >= 65 else ("#ff9800" if v >= 45 else "#f44336")
              for v in values]
    fig, ax = plt.subplots(figsize=(7, 4))
    bars = ax.bar(names, values, color=colors)
    for b, v in zip(bars, values):
        ax.text(b.get_x() + b.get_width()/2, v + 1, f"{v:.1f}",
                ha="center", fontsize=11, fontweight="bold")
    ax.set_ylim(0, 105)
    ax.axhline(65, color="#4caf50", ls="--", lw=0.7, label="BUY 기준")
    ax.axhline(45, color="#f44336", ls="--", lw=0.7, label="SELL 기준")
    ax.set_title(title); ax.legend(); ax.grid(True, alpha=0.3, axis="y")
    return {"fig": fig, "png_b64": _fig_to_b64(fig)}


# ================================================================== #
#  기관급 차트 (Institutional)
# ================================================================== #
def plot_mc_fan(proj: Dict, title: str = "") -> Dict:
    """미래 주가 몬테카를로 부채꼴(fan) 차트 — 분위수 밴드 + 표본 경로."""
    if not proj or "pctl" not in proj:
        return {}
    p = proj["pctl"]
    h = proj["horizon_days"]
    s0 = proj["start_price"]
    x = np.arange(1, h + 1)
    name = proj.get("name", "")

    fig, ax = plt.subplots(figsize=(9, 4.5))
    # 표본 경로 (옅게)
    for s in proj.get("paths_sample", [])[:80]:
        ax.plot(x, s, lw=0.3, alpha=0.15, color="steelblue")
    # 분위수 밴드
    ax.fill_between(x, p["p5"], p["p95"], color="steelblue", alpha=0.18,
                    label="5~95% 구간")
    ax.fill_between(x, p["p25"], p["p75"], color="steelblue", alpha=0.32,
                    label="25~75% 구간")
    ax.plot(x, p["p50"], color="navy", lw=2, label="중앙값(p50)")
    ax.axhline(s0, color="black", ls=":", lw=1, label=f"현재가 {s0:,.0f}")
    ax.set_title(title or f"[{name}] 미래 주가 몬테카를로 ({h}일 후)")
    ax.set_xlabel("영업일"); ax.set_ylabel("예상 가격")
    ax.legend(fontsize=9, loc="upper left"); ax.grid(True, alpha=0.3)
    return {"fig": fig, "png_b64": _fig_to_b64(fig)}


def plot_mc_multi(mc_tf: Dict, title: str = "단/중/장 미래 주가 시뮬레이션") -> Dict:
    """단/중/장 3개 부채꼴 차트를 한 장에."""
    valid = [(n, d) for n, d in mc_tf.items()
             if isinstance(d, dict) and "pctl" in d]
    if not valid:
        return {}
    fig, axes = plt.subplots(1, len(valid), figsize=(6 * len(valid), 4.2))
    if len(valid) == 1:
        axes = [axes]
    for ax, (n, proj) in zip(axes, valid):
        p = proj["pctl"]; h = proj["horizon_days"]; s0 = proj["start_price"]
        x = np.arange(1, h + 1)
        ax.fill_between(x, p["p5"], p["p95"], color="steelblue", alpha=0.18)
        ax.fill_between(x, p["p25"], p["p75"], color="steelblue", alpha=0.32)
        ax.plot(x, p["p50"], color="navy", lw=2)
        ax.axhline(s0, color="black", ls=":", lw=1)
        up = proj.get("up_prob", 0)
        ax.set_title(f"{n} ({h}일) · 상승확률 {up:.0f}%")
        ax.set_xlabel("영업일"); ax.grid(True, alpha=0.3)
    fig.suptitle(title, fontsize=13, fontweight="bold")
    fig.tight_layout()
    return {"fig": fig, "png_b64": _fig_to_b64(fig)}


def plot_mc_dist(proj: Dict, title: str = "") -> Dict:
    """종착 가격 분포 히스토그램."""
    if not proj or "terminal" not in proj:
        return {}
    term = np.asarray(proj["terminal"])
    s0 = proj["start_price"]
    name = proj.get("name", "")
    fig, ax = plt.subplots(figsize=(8, 4))
    ax.hist(term, bins=60, color="slateblue", alpha=0.75)
    ax.axvline(s0, color="black", ls=":", lw=1.5, label=f"현재가 {s0:,.0f}")
    ax.axvline(proj.get("median_price", s0), color="green", ls="--", lw=1.5,
               label=f"중앙값 {proj.get('median_price',0):,.0f}")
    ax.axvline(proj.get("ci90_low", s0), color="red", ls="--", lw=1,
               label="5%")
    ax.axvline(proj.get("ci90_high", s0), color="red", ls="--", lw=1,
               label="95%")
    ax.set_title(title or f"[{name}] {proj.get('horizon_days',0)}일 후 가격 분포")
    ax.set_xlabel("예상 가격"); ax.set_ylabel("빈도")
    ax.legend(fontsize=9); ax.grid(True, alpha=0.3)
    return {"fig": fig, "png_b64": _fig_to_b64(fig)}


def plot_factor_risk(decomp: Dict, title: str = "팩터 위험 분해") -> Dict:
    """팩터별 위험 기여도 수평 막대."""
    if not decomp or "contrib_pct" not in decomp:
        return {}
    labels_map = decomp.get("factor_labels", {})
    items = sorted(decomp["contrib_pct"].items(),
                   key=lambda kv: abs(kv[1]), reverse=True)
    names = [labels_map.get(k, k) for k, _ in items]
    vals = [v for _, v in items]
    colors = ["#9c27b0" if k == "specific" else "#1a5fb4"
              for k, _ in items]
    fig, ax = plt.subplots(figsize=(8, max(3, len(names) * 0.6)))
    ax.barh(names, vals, color=colors)
    for i, v in enumerate(vals):
        ax.text(v + (0.5 if v >= 0 else -0.5), i, f"{v:.0f}%",
                va="center", ha="left" if v >= 0 else "right", fontsize=10)
    ax.invert_yaxis()
    ax.set_xlabel("총 위험 대비 기여도 (%)")
    ax.set_title(title); ax.grid(True, alpha=0.3, axis="x")
    return {"fig": fig, "png_b64": _fig_to_b64(fig)}


def plot_stress(stress: Dict, title: str = "시나리오 스트레스 테스트") -> Dict:
    """위기 시나리오별 예상 손실 막대."""
    if not stress or "scenarios" not in stress:
        return {}
    sc = stress["scenarios"]
    names = list(sc.keys())
    losses = [sc[n]["shock_pct"] for n in names]
    fig, ax = plt.subplots(figsize=(8, 4))
    bars = ax.bar(names, losses, color="#c62828", alpha=0.8)
    for b, v in zip(bars, losses):
        ax.text(b.get_x() + b.get_width() / 2, v - 1.5, f"{v:.0f}%",
                ha="center", va="top", fontsize=10, color="white",
                fontweight="bold")
    ax.axhline(0, color="black", lw=0.8)
    ax.set_ylabel("예상 충격 (%)")
    ax.set_title(title)
    ax.grid(True, alpha=0.3, axis="y")
    plt.setp(ax.get_xticklabels(), rotation=15, ha="right")
    return {"fig": fig, "png_b64": _fig_to_b64(fig)}


def plot_scorecard(scorecard: Dict, title: str = "기관 스코어카드") -> Dict:
    """평가 축별 점수 레이더(거미줄) 차트."""
    if not scorecard or "pillars" not in scorecard:
        return {}
    pil = scorecard["pillars"]
    cats = list(pil.keys())
    vals = [pil[c]["score"] for c in cats]
    N = len(cats)
    angles = np.linspace(0, 2 * np.pi, N, endpoint=False).tolist()
    vals_c = vals + vals[:1]
    angles_c = angles + angles[:1]

    fig, ax = plt.subplots(figsize=(6.5, 6.5),
                           subplot_kw=dict(polar=True))
    ax.plot(angles_c, vals_c, color="#1a5fb4", lw=2)
    ax.fill(angles_c, vals_c, color="#1a5fb4", alpha=0.25)
    ax.set_xticks(angles)
    ax.set_xticklabels(cats, fontsize=10)
    ax.set_ylim(0, 100)
    ax.set_yticks([25, 50, 75, 100])
    ax.set_yticklabels(["25", "50", "75", "100"], fontsize=8)
    g = scorecard.get("overall_grade", "")
    s = scorecard.get("overall_score", 0)
    ax.set_title(f"{title}  —  종합 {s:.0f}점 (등급 {g})",
                 fontsize=13, fontweight="bold", pad=20)
    return {"fig": fig, "png_b64": _fig_to_b64(fig)}
