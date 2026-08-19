# ==============================================================================
# [17/25] charts.py — 의존성 없는 인라인 SVG 차트 10종
# ==============================================================================

"""
jiqtx.charts — 외부 의존 없는 인라인 SVG 차트.

리포트를 단일 HTML 파일로 배포하기 위해 matplotlib/plotly 없이
순수 SVG 문자열을 생성한다. 오프라인에서도 열린다.
"""

import math
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

PAL = {
    "bg": "#0f1115", "panel": "#171a21", "grid": "#252a34",
    "text": "#e6e8ec", "muted": "#8b93a3",
    "up": "#2ec27e", "down": "#e0455f", "warn": "#e8a33d",
    "accent": "#5b8def", "accent2": "#a77bf3", "neutral": "#6b7280",
    "band1": "#5b8def22", "band2": "#5b8def44", "band3": "#5b8def66",
}


def _esc(s) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def _svg(w, h, body, title="") -> str:
    return (f'<svg viewBox="0 0 {w} {h}" width="100%" '
            f'preserveAspectRatio="xMidYMid meet" role="img" '
            f'aria-label="{_esc(title)}" style="max-width:100%;height:auto">'
            f'<rect width="{w}" height="{h}" fill="{PAL["panel"]}" rx="8"/>'
            f'{body}</svg>')


def _txt(x, y, s, size=11, fill=None, anchor="start", weight="400") -> str:
    return (f'<text x="{x:.1f}" y="{y:.1f}" font-size="{size}" '
            f'fill="{fill or PAL["muted"]}" text-anchor="{anchor}" '
            f'font-weight="{weight}" '
            f'font-family="ui-sans-serif,-apple-system,Segoe UI,Roboto,sans-serif">'
            f'{_esc(s)}</text>')


def _grid(x0, y0, w, h, n=4) -> str:
    out = []
    for i in range(n + 1):
        y = y0 + h * i / n
        out.append(f'<line x1="{x0}" y1="{y:.1f}" x2="{x0+w}" y2="{y:.1f}" '
                   f'stroke="{PAL["grid"]}" stroke-width="1"/>')
    return "".join(out)


# ---------------------------------------------------------------- fan chart

def fan_chart(fan: pd.DataFrame, s0: float, title="시뮬레이션 분포",
              w=720, h=300) -> str:
    """시뮬레이션 분위 팬차트."""
    if fan is None or len(fan) == 0:
        return ""
    pad_l, pad_r, pad_t, pad_b = 62, 14, 30, 26
    pw, ph = w - pad_l - pad_r, h - pad_t - pad_b
    n = len(fan)
    lo = float(min(fan.min().min(), s0)) * 0.98
    hi = float(max(fan.max().max(), s0)) * 1.02
    if not np.isfinite(lo) or not np.isfinite(hi) or hi <= lo:
        return ""

    def X(i):
        return pad_l + pw * i / max(n - 1, 1)

    def Y(v):
        return pad_t + ph * (1 - (v - lo) / (hi - lo))

    body = [_grid(pad_l, pad_t, pw, ph)]
    pairs = [("q05", "q95", PAL["band1"]), ("q10", "q90", PAL["band2"]),
             ("q25", "q75", PAL["band3"])]
    for a, b, col in pairs:
        if a not in fan.columns or b not in fan.columns:
            continue
        up = " ".join(f"{X(i):.1f},{Y(v):.1f}" for i, v in enumerate(fan[b]))
        dn = " ".join(f"{X(i):.1f},{Y(v):.1f}"
                      for i, v in reversed(list(enumerate(fan[a]))))
        body.append(f'<polygon points="{up} {dn}" fill="{col}" stroke="none"/>')
    if "q50" in fan.columns:
        pts = " ".join(f"{X(i):.1f},{Y(v):.1f}" for i, v in enumerate(fan["q50"]))
        body.append(f'<polyline points="{pts}" fill="none" '
                    f'stroke="{PAL["accent"]}" stroke-width="2"/>')
    body.append(f'<line x1="{pad_l}" y1="{Y(s0):.1f}" x2="{pad_l+pw}" '
                f'y2="{Y(s0):.1f}" stroke="{PAL["warn"]}" stroke-width="1.4" '
                f'stroke-dasharray="5 4"/>')
    body.append(_txt(pad_l + pw - 4, Y(s0) - 6, f"현재 {s0:,.2f}", 10,
                     PAL["warn"], "end"))
    for i in range(5):
        v = lo + (hi - lo) * i / 4
        body.append(_txt(pad_l - 8, Y(v) + 3.5, f"{v:,.0f}", 10, anchor="end"))
    for f_ in (0, 0.5, 1.0):
        i = int((n - 1) * f_)
        body.append(_txt(X(i), h - 8, f"{i}일", 10, anchor="middle"))
    body.append(_txt(pad_l, 18, title, 12, PAL["text"], weight="600"))
    return _svg(w, h, "".join(body), title)


# ---------------------------------------------------------------- 델타 바

def delta_bars(labels: Sequence[str], values: Sequence[float],
               values2: Optional[Sequence[float]] = None,
               title="팩터 델타", w=720, row_h=26,
               fmt="{:+.2%}") -> str:
    """수평 발산 막대 (표준충격 vs 하방베타)."""
    labels = list(labels)[:10]
    values = list(values)[:10]
    if not labels:
        return ""
    v2 = list(values2)[:10] if values2 is not None else None
    h = 44 + row_h * len(labels)
    pad_l, pad_r = 140, 70
    pw = w - pad_l - pad_r
    mx = max(1e-9, max(abs(x) for x in values + (v2 or []) if np.isfinite(x)))
    cx = pad_l + pw / 2

    body = [_txt(14, 20, title, 12, PAL["text"], weight="600"),
            f'<line x1="{cx}" y1="32" x2="{cx}" y2="{h-8}" '
            f'stroke="{PAL["grid"]}" stroke-width="1"/>']
    for i, (lb, v) in enumerate(zip(labels, values)):
        y = 38 + row_h * i
        if not np.isfinite(v):
            continue
        bw = abs(v) / mx * (pw / 2 - 6)
        x = cx if v >= 0 else cx - bw
        col = PAL["up"] if v >= 0 else PAL["down"]
        body.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{bw:.1f}" '
                    f'height="{row_h-12}" fill="{col}" opacity="0.85" rx="2"/>')
        if v2 is not None and i < len(v2) and np.isfinite(v2[i]):
            bw2 = abs(v2[i]) / mx * (pw / 2 - 6)
            x2 = cx if v2[i] >= 0 else cx - bw2
            body.append(f'<rect x="{x2:.1f}" y="{y+row_h-13:.1f}" '
                        f'width="{bw2:.1f}" height="3" '
                        f'fill="{PAL["warn"]}" opacity="0.95"/>')
        body.append(_txt(pad_l - 10, y + row_h / 2 - 3, lb, 10.5, PAL["text"], "end"))
        body.append(_txt(w - 10, y + row_h / 2 - 3, fmt.format(v), 10.5, col, "end"))
    body.append(_txt(14, h - 4, "■ 정적 충격  ▬ 하방베타 적용", 9, PAL["muted"]))
    return _svg(w, h, "".join(body), title)


# ---------------------------------------------------------------- 히스토그램

def histogram(values: np.ndarray, title="분포", w=720, h=250, bins=60,
              vline: Optional[float] = None, vline_label="",
              overlay_x: Optional[np.ndarray] = None,
              overlay_y: Optional[np.ndarray] = None,
              overlay_label="") -> str:
    v = np.asarray(values, float)
    v = v[np.isfinite(v)]
    if len(v) < 20:
        return ""
    lo, hi = np.quantile(v, 0.002), np.quantile(v, 0.998)
    if hi <= lo:
        return ""
    cnt, edges = np.histogram(np.clip(v, lo, hi), bins=bins, range=(lo, hi))
    pad_l, pad_r, pad_t, pad_b = 52, 14, 30, 26
    pw, ph = w - pad_l - pad_r, h - pad_t - pad_b
    mx = max(cnt.max(), 1)

    def X(x):
        return pad_l + pw * (x - lo) / (hi - lo)

    body = [_grid(pad_l, pad_t, pw, ph),
            _txt(pad_l, 18, title, 12, PAL["text"], weight="600")]
    bw = pw / bins
    for i, c in enumerate(cnt):
        bh = ph * c / mx
        body.append(f'<rect x="{pad_l+i*bw:.1f}" y="{pad_t+ph-bh:.1f}" '
                    f'width="{max(bw-0.6,0.6):.1f}" height="{bh:.1f}" '
                    f'fill="{PAL["accent"]}" opacity="0.62"/>')
    if overlay_x is not None and overlay_y is not None and len(overlay_x) > 2:
        oy = np.asarray(overlay_y, float)
        ox = np.asarray(overlay_x, float)
        m = (ox >= lo) & (ox <= hi) & np.isfinite(oy)
        if m.sum() > 2 and oy[m].max() > 0:
            sc = ph / oy[m].max()
            pts = " ".join(f"{X(x):.1f},{pad_t+ph-y*sc:.1f}"
                           for x, y in zip(ox[m], oy[m]))
            body.append(f'<polyline points="{pts}" fill="none" '
                        f'stroke="{PAL["accent2"]}" stroke-width="2"/>')
            body.append(_txt(w - 14, 18, overlay_label, 10, PAL["accent2"], "end"))
    if vline is not None and lo <= vline <= hi:
        body.append(f'<line x1="{X(vline):.1f}" y1="{pad_t}" '
                    f'x2="{X(vline):.1f}" y2="{pad_t+ph}" '
                    f'stroke="{PAL["warn"]}" stroke-width="1.6" '
                    f'stroke-dasharray="5 4"/>')
        body.append(_txt(X(vline) + 5, pad_t + 12, vline_label, 10, PAL["warn"]))
    for i in range(5):
        x = lo + (hi - lo) * i / 4
        body.append(_txt(X(x), h - 8, f"{x:,.0f}", 10, anchor="middle"))
    return _svg(w, h, "".join(body), title)


# ---------------------------------------------------------------- 레짐 타임라인

def regime_timeline(states: np.ndarray, prices: np.ndarray,
                    labels: Dict[int, str], title="국면 타임라인",
                    w=720, h=240) -> str:
    s = np.asarray(states, int)
    p = np.asarray(prices, float)[-len(s):]
    if len(s) < 20:
        return ""
    pad_l, pad_r, pad_t, pad_b = 52, 14, 30, 44
    pw, ph = w - pad_l - pad_r, h - pad_t - pad_b
    lo, hi = float(np.nanmin(p)), float(np.nanmax(p))
    if hi <= lo:
        return ""
    cols = [PAL["up"], PAL["accent"], PAL["down"], PAL["warn"], PAL["accent2"]]

    def X(i):
        return pad_l + pw * i / max(len(s) - 1, 1)

    def Y(v):
        return pad_t + ph * (1 - (v - lo) / (hi - lo))

    body = [_txt(pad_l, 18, title, 12, PAL["text"], weight="600")]
    i = 0
    while i < len(s):
        j = i
        while j + 1 < len(s) and s[j + 1] == s[i]:
            j += 1
        body.append(f'<rect x="{X(i):.1f}" y="{pad_t}" '
                    f'width="{max(X(j)-X(i),1):.1f}" height="{ph}" '
                    f'fill="{cols[s[i] % len(cols)]}" opacity="0.16"/>')
        i = j + 1
    pts = " ".join(f"{X(k):.1f},{Y(v):.1f}" for k, v in enumerate(p)
                   if np.isfinite(v))
    body.append(f'<polyline points="{pts}" fill="none" '
                f'stroke="{PAL["text"]}" stroke-width="1.4" opacity="0.9"/>')
    lx = pad_l
    for k in sorted(set(s.tolist())):
        body.append(f'<rect x="{lx}" y="{h-26}" width="11" height="11" '
                    f'rx="2" fill="{cols[k % len(cols)]}" opacity="0.7"/>')
        lab = labels.get(int(k), f"국면 {k}")
        body.append(_txt(lx + 16, h - 17, lab, 10, PAL["text"]))
        lx += 22 + len(lab) * 7.2
    return _svg(w, h, "".join(body), title)


# ---------------------------------------------------------------- 라인

def line_chart(series: Dict[str, Sequence[float]], title="", w=720, h=240,
               hline: Optional[float] = None, ylabel="",
               colors: Optional[List[str]] = None) -> str:
    if not series:
        return ""
    arrs = {k: np.asarray(v, float) for k, v in series.items()}
    allv = np.concatenate([a[np.isfinite(a)] for a in arrs.values()
                           if np.isfinite(a).any()] or [np.array([0.0])])
    if len(allv) < 3:
        return ""
    lo, hi = float(allv.min()), float(allv.max())
    if hline is not None:
        lo, hi = min(lo, hline), max(hi, hline)
    pad = (hi - lo) * 0.08 or 0.01
    lo, hi = lo - pad, hi + pad
    pad_l, pad_r, pad_t, pad_b = 58, 14, 30, 32
    pw, ph = w - pad_l - pad_r, h - pad_t - pad_b
    n = max(len(a) for a in arrs.values())
    cols = colors or [PAL["accent"], PAL["warn"], PAL["accent2"], PAL["up"]]

    def X(i):
        return pad_l + pw * i / max(n - 1, 1)

    def Y(v):
        return pad_t + ph * (1 - (v - lo) / (hi - lo))

    body = [_grid(pad_l, pad_t, pw, ph),
            _txt(pad_l, 18, title, 12, PAL["text"], weight="600")]
    if hline is not None:
        body.append(f'<line x1="{pad_l}" y1="{Y(hline):.1f}" x2="{pad_l+pw}" '
                    f'y2="{Y(hline):.1f}" stroke="{PAL["muted"]}" '
                    f'stroke-width="1" stroke-dasharray="4 4"/>')
    lx = pad_l
    for k, (name, a) in enumerate(arrs.items()):
        pts = " ".join(f"{X(i):.1f},{Y(v):.1f}" for i, v in enumerate(a)
                       if np.isfinite(v))
        if not pts:
            continue
        c = cols[k % len(cols)]
        body.append(f'<polyline points="{pts}" fill="none" stroke="{c}" '
                    f'stroke-width="1.8"/>')
        body.append(f'<rect x="{lx}" y="{h-14}" width="10" height="3" fill="{c}"/>')
        body.append(_txt(lx + 14, h - 10, name, 9.5, PAL["muted"]))
        lx += 22 + len(name) * 6.4
    for i in range(5):
        v = lo + (hi - lo) * i / 4
        body.append(_txt(pad_l - 8, Y(v) + 3.5, f"{v:.3g}", 10, anchor="end"))
    if ylabel:
        body.append(_txt(pad_l, 30, ylabel, 9, PAL["muted"]))
    return _svg(w, h, "".join(body), title)


# ---------------------------------------------------------------- 신뢰도

def reliability_diagram(tbl: pd.DataFrame, title="신뢰도 다이어그램",
                        w=380, h=320) -> str:
    if tbl is None or len(tbl) == 0:
        return ""
    pad = 46
    pw = ph = min(w, h) - pad - 24
    x0, y0 = pad, 26

    def X(v):
        return x0 + pw * v

    def Y(v):
        return y0 + ph * (1 - v)

    body = [_txt(x0, 18, title, 12, PAL["text"], weight="600"),
            f'<rect x="{x0}" y="{y0}" width="{pw}" height="{ph}" '
            f'fill="none" stroke="{PAL["grid"]}"/>',
            f'<line x1="{X(0)}" y1="{Y(0)}" x2="{X(1)}" y2="{Y(1)}" '
            f'stroke="{PAL["muted"]}" stroke-dasharray="4 4"/>']
    mx = float(tbl["n"].max()) if "n" in tbl else 1.0
    pts = []
    for _, row in tbl.iterrows():
        px, py = float(row["pred_mean"]), float(row["obs_freq"])
        rr = 3 + 6 * (float(row.get("n", 1)) / max(mx, 1)) ** 0.5
        body.append(f'<circle cx="{X(px):.1f}" cy="{Y(py):.1f}" r="{rr:.1f}" '
                    f'fill="{PAL["accent"]}" opacity="0.85"/>')
        pts.append(f"{X(px):.1f},{Y(py):.1f}")
    if len(pts) > 1:
        body.append(f'<polyline points="{" ".join(pts)}" fill="none" '
                    f'stroke="{PAL["accent"]}" stroke-width="1.4" opacity="0.6"/>')
    for v in (0, 0.5, 1.0):
        body.append(_txt(X(v), y0 + ph + 16, f"{v:.1f}", 9.5, anchor="middle"))
        body.append(_txt(x0 - 8, Y(v) + 3.5, f"{v:.1f}", 9.5, anchor="end"))
    body.append(_txt(x0 + pw / 2, y0 + ph + 32, "예측 확률", 10, anchor="middle"))
    return _svg(w, h, "".join(body), title)


# ---------------------------------------------------------------- 게이지

def gauge(value: float, lo: float, hi: float, title="", w=230, h=112,
          good_high=True, fmt="{:.1%}", threshold: Optional[float] = None) -> str:
    if not np.isfinite(value):
        return ""
    t = float(np.clip((value - lo) / (hi - lo), 0, 1))
    ok = (t > 0.5) if good_high else (t < 0.5)
    if threshold is not None and np.isfinite(threshold):
        thr = float(np.clip((threshold - lo) / (hi - lo), 0, 1))
        ok = (value >= threshold) if good_high else (value <= threshold)
    else:
        thr = None
    col = PAL["up"] if ok else PAL["down"]
    bx, bw, by = 16, w - 32, 66
    body = [_txt(bx, 22, title, 11, PAL["muted"]),
            _txt(bx, 50, fmt.format(value), 22, col, weight="700"),
            f'<rect x="{bx}" y="{by}" width="{bw}" height="8" rx="4" '
            f'fill="{PAL["grid"]}"/>',
            f'<rect x="{bx}" y="{by}" width="{bw*t:.1f}" height="8" rx="4" '
            f'fill="{col}"/>']
    if thr is not None:
        body.append(f'<line x1="{bx+bw*thr:.1f}" y1="{by-4}" '
                    f'x2="{bx+bw*thr:.1f}" y2="{by+12}" '
                    f'stroke="{PAL["warn"]}" stroke-width="2"/>')
        body.append(_txt(bx, by + 26, f"임계 {fmt.format(threshold)}", 9))
    return _svg(w, h, "".join(body), title)


def stacked_bar(labels: Sequence[str], values: Sequence[float],
                title="", w=720, h=90, palette: Optional[List[str]] = None) -> str:
    tot = float(sum(v for v in values if np.isfinite(v)))
    if tot <= 0:
        return ""
    pal = palette or [PAL["accent"], PAL["warn"], PAL["accent2"], PAL["up"]]
    bx, bw, by = 16, w - 32, 40
    body = [_txt(bx, 22, title, 12, PAL["text"], weight="600")]
    x = bx
    for i, (lb, v) in enumerate(zip(labels, values)):
        if not np.isfinite(v) or v <= 0:
            continue
        ww = bw * v / tot
        c = pal[i % len(pal)]
        body.append(f'<rect x="{x:.1f}" y="{by}" width="{ww:.1f}" height="20" '
                    f'fill="{c}" opacity="0.85"/>')
        if ww > 46:
            body.append(_txt(x + ww / 2, by + 14, f"{v/tot:.0%}", 10,
                             "#0f1115", "middle", "700"))
        body.append(_txt(x, by + 36, lb, 9.5, PAL["muted"]))
        x += ww
    return _svg(w, h, "".join(body), title)


# ---------------------------------------------------------------- 워터폴

def waterfall(labels: Sequence[str], values: Sequence[float],
              total_label: str = "총수익", title="수익 귀인",
              w=720, h=280) -> str:
    """수익 귀인 워터폴. '최근 성과가 알파인가 베타인가'를 한눈에."""
    labels = list(labels)
    values = [float(v) for v in values]
    if not labels:
        return ""
    total = sum(values)
    pad_l, pad_r, pad_t, pad_b = 58, 14, 34, 46
    pw, ph = w - pad_l - pad_r, h - pad_t - pad_b
    n = len(labels) + 1
    cum = np.concatenate([[0.0], np.cumsum(values)])
    lo = min(0.0, cum.min(), total) * 1.15
    hi = max(0.0, cum.max(), total) * 1.15
    if hi <= lo:
        hi = lo + 1e-6

    def Y(v):
        return pad_t + ph * (1 - (v - lo) / (hi - lo))

    bw = pw / n * 0.62
    step = pw / n
    body = [_grid(pad_l, pad_t, pw, ph),
            _txt(pad_l, 20, title, 12, PAL["text"], weight="600"),
            f'<line x1="{pad_l}" y1="{Y(0):.1f}" x2="{pad_l+pw}" y2="{Y(0):.1f}" '
            f'stroke="{PAL["muted"]}" stroke-width="1"/>']
    for i, (lb, v) in enumerate(zip(labels, values)):
        x = pad_l + step * i + (step - bw) / 2
        y0, y1 = Y(cum[i]), Y(cum[i + 1])
        top, hgt = min(y0, y1), max(abs(y1 - y0), 1.5)
        col = PAL["up"] if v >= 0 else PAL["down"]
        body.append(f'<rect x="{x:.1f}" y="{top:.1f}" width="{bw:.1f}" '
                    f'height="{hgt:.1f}" fill="{col}" opacity="0.85" rx="2"/>')
        body.append(_txt(x + bw / 2, top - 5, f"{v:+.1%}", 9.5, col, "middle"))
        body.append(_txt(x + bw / 2, h - 26, lb[:14], 9, PAL["muted"], "middle"))
        if i < len(labels) - 1:
            body.append(f'<line x1="{x+bw:.1f}" y1="{y1:.1f}" '
                        f'x2="{x+step:.1f}" y2="{y1:.1f}" '
                        f'stroke="{PAL["grid"]}" stroke-dasharray="3 3"/>')
    x = pad_l + step * len(labels) + (step - bw) / 2
    y1 = Y(total)
    top, hgt = min(Y(0), y1), max(abs(y1 - Y(0)), 1.5)
    body.append(f'<rect x="{x:.1f}" y="{top:.1f}" width="{bw:.1f}" '
                f'height="{hgt:.1f}" fill="{PAL["accent"]}" rx="2"/>')
    body.append(_txt(x + bw / 2, top - 5, f"{total:+.1%}", 10,
                     PAL["accent"], "middle", "700"))
    body.append(_txt(x + bw / 2, h - 26, total_label, 9.5, PAL["text"], "middle"))
    for i in range(5):
        v = lo + (hi - lo) * i / 4
        body.append(_txt(pad_l - 8, Y(v) + 3.5, f"{v:+.0%}", 9.5, anchor="end"))
    return _svg(w, h, "".join(body), title)


# ---------------------------------------------------------------- 시나리오

def scenario_chart(names: Sequence[str], rets: Sequence[float],
                   probs: Sequence[float], probs2: Optional[Sequence[float]] = None,
                   title="시나리오 · 확률 × 손익", w=720, h=300) -> str:
    """x=수익, 막대높이=확률. 기대값 기여를 시각화."""
    names, rets, probs = list(names), list(rets), list(probs)
    if not names:
        return ""
    pad_l, pad_r, pad_t, pad_b = 56, 16, 34, 62
    pw, ph = w - pad_l - pad_r, h - pad_t - pad_b
    lo, hi = min(rets) * 1.25, max(rets) * 1.25
    if hi <= lo:
        hi = lo + 0.01
    pmax = max(max(probs), max(probs2) if probs2 else 0, 1e-6)

    def X(v):
        return pad_l + pw * (v - lo) / (hi - lo)

    body = [_grid(pad_l, pad_t, pw, ph),
            _txt(pad_l, 20, title, 12, PAL["text"], weight="600"),
            f'<line x1="{X(0):.1f}" y1="{pad_t}" x2="{X(0):.1f}" '
            f'y2="{pad_t+ph}" stroke="{PAL["muted"]}" stroke-dasharray="4 4"/>']
    bw = 26
    for i, (nm, r, p) in enumerate(zip(names, rets, probs)):
        bh = ph * (p / pmax) * 0.88
        x = X(r) - bw / 2
        col = PAL["up"] if r > 0 else PAL["down"] if r < 0 else PAL["neutral"]
        body.append(f'<rect x="{x:.1f}" y="{pad_t+ph-bh:.1f}" width="{bw}" '
                    f'height="{bh:.1f}" fill="{col}" opacity="0.8" rx="2"/>')
        if probs2 is not None and i < len(probs2):
            bh2 = ph * (probs2[i] / pmax) * 0.88
            body.append(f'<rect x="{x+bw+2:.1f}" y="{pad_t+ph-bh2:.1f}" '
                        f'width="{bw*0.45:.1f}" height="{bh2:.1f}" '
                        f'fill="{PAL["accent2"]}" opacity="0.65" rx="2"/>')
        body.append(_txt(X(r), pad_t + ph - bh - 6, f"{p:.0%}", 9.5, col, "middle"))
        body.append(_txt(X(r), pad_t + ph + 16, f"{r:+.1%}", 9.5,
                         PAL["text"], "middle"))
        body.append(_txt(X(r), pad_t + ph + 30, nm[:10], 8.5,
                         PAL["muted"], "middle"))
    body.append(_txt(pad_l, h - 8, "■ 경험적 확률   ■ 모델 분포 확률", 9,
                     PAL["muted"]))
    return _svg(w, h, "".join(body), title)


# ---------------------------------------------------------------- 트레이드 사다리

def trade_ladder(entry: float, stop: float, target: float,
                 p_target: float, p_stop: float,
                 title="트레이드 구조", w=720, h=190) -> str:
    vals = [v for v in (entry, stop, target) if np.isfinite(v)]
    if len(vals) < 3:
        return ""
    lo, hi = min(vals), max(vals)
    rng = (hi - lo) or 1.0
    lo, hi = lo - rng * 0.16, hi + rng * 0.16
    pad_l, pad_r, pad_t = 108, 90, 40
    pw = w - pad_l - pad_r
    yc = pad_t + 46

    def X(v):
        return pad_l + pw * (v - lo) / (hi - lo)

    up = target > entry
    body = [_txt(16, 22, title, 12, PAL["text"], weight="600"),
            f'<line x1="{pad_l}" y1="{yc}" x2="{pad_l+pw}" y2="{yc}" '
            f'stroke="{PAL["grid"]}" stroke-width="2"/>']
    for v, lab, col, sub in (
            (stop, "손절", PAL["down"], f"P={p_stop:.0%}" if np.isfinite(p_stop) else ""),
            (entry, "진입", PAL["text"], ""),
            (target, "목표", PAL["up"], f"P={p_target:.0%}" if np.isfinite(p_target) else "")):
        x = X(v)
        body.append(f'<line x1="{x:.1f}" y1="{yc-22}" x2="{x:.1f}" '
                    f'y2="{yc+22}" stroke="{col}" stroke-width="2.5"/>')
        body.append(_txt(x, yc - 30, lab, 10.5, col, "middle", "700"))
        body.append(_txt(x, yc + 38, f"{v:,.2f}", 10, PAL["text"], "middle"))
        if sub:
            body.append(_txt(x, yc + 52, sub, 9.5, col, "middle"))
    x0, x1 = X(min(entry, stop)), X(max(entry, stop))
    body.append(f'<rect x="{x0:.1f}" y="{yc-8}" width="{x1-x0:.1f}" height="16" '
                f'fill="{PAL["down"]}" opacity="0.20"/>')
    x0, x1 = X(min(entry, target)), X(max(entry, target))
    body.append(f'<rect x="{x0:.1f}" y="{yc-8}" width="{x1-x0:.1f}" height="16" '
                f'fill="{PAL["up"]}" opacity="0.20"/>')
    body.append(_txt(16, yc + 4, "LONG" if up else "SHORT", 11,
                     PAL["up"] if up else PAL["down"], "start", "700"))
    rr = abs(target - entry) / max(abs(entry - stop), 1e-9)
    body.append(_txt(w - 14, yc + 4, f"R:R {rr:.2f}", 11, PAL["text"], "end", "700"))
    return _svg(w, h, "".join(body), title)


# ---------------------------------------------------------------- 헤지 분해

def hedge_donut(var_removed: float, title="헤지 후 위험 구성",
                w=250, h=190) -> str:
    if not np.isfinite(var_removed):
        return ""
    v = float(np.clip(var_removed, 0, 1))
    cx, cy, r, sw = w / 2, h / 2 + 6, 52, 20
    circ = 2 * math.pi * r
    body = [_txt(14, 20, title, 11.5, PAL["text"], weight="600"),
            f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="none" '
            f'stroke="{PAL["warn"]}" stroke-width="{sw}"/>',
            f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="none" '
            f'stroke="{PAL["accent"]}" stroke-width="{sw}" '
            f'stroke-dasharray="{circ*v:.1f} {circ:.1f}" '
            f'transform="rotate(-90 {cx} {cy})"/>',
            _txt(cx, cy + 2, f"{v:.0%}", 19, PAL["accent"], "middle", "700"),
            _txt(cx, cy + 18, "헤지 가능", 9, PAL["muted"], "middle"),
            _txt(14, h - 8, f"■ 팩터(헤지가능) {v:.0%}   "
                            f"■ 고유(헤지불가) {1-v:.0%}", 9, PAL["muted"])]
    return _svg(w, h, "".join(body), title)


# ---------------------------------------------------------------- 히트맵

def heatmap(M: "pd.DataFrame", title="상관 행렬", w=560, cell_min=26) -> str:
    """상관 행렬 히트맵 (파랑=음, 빨강=양)."""
    n = len(M)
    if n < 2:
        return ""
    pad_l = max(64, min(110, 8 + max(len(str(c)) for c in M.index) * 7))
    pad_t, pad_r, pad_b = 56, 16, 16
    cell = max(cell_min, (w - pad_l - pad_r) / n)
    size = cell * n
    W = pad_l + size + pad_r
    H = pad_t + size + pad_b
    body = [_txt(14, 22, title, 12, PAL["text"], weight="600")]
    for i, ri in enumerate(M.index):
        body.append(_txt(pad_l - 6, pad_t + cell * i + cell / 2 + 3.5,
                         str(ri)[:14], 9.5, PAL["muted"], "end"))
        body.append(f'<g transform="translate({pad_l + cell*i + cell/2:.1f},'
                    f'{pad_t - 6}) rotate(-45)">'
                    f'{_txt(0, 0, str(ri)[:14], 9.5, PAL["muted"], "start")}</g>')
        for j, cj in enumerate(M.columns):
            v = float(M.iloc[i, j])
            if not np.isfinite(v):
                v = 0.0
            t = (v + 1) / 2
            if v >= 0:
                col = f"rgba(224,69,95,{0.12 + 0.78*min(abs(v),1):.2f})"
            else:
                col = f"rgba(91,141,239,{0.12 + 0.78*min(abs(v),1):.2f})"
            body.append(f'<rect x="{pad_l+cell*j:.1f}" y="{pad_t+cell*i:.1f}" '
                        f'width="{cell-1:.1f}" height="{cell-1:.1f}" '
                        f'fill="{col}" rx="2"/>')
            if cell >= 30:
                body.append(_txt(pad_l + cell * j + cell / 2,
                                 pad_t + cell * i + cell / 2 + 3.5,
                                 f"{v:.2f}", 8.5,
                                 "#0f1115" if abs(v) > 0.55 else PAL["text"],
                                 "middle"))
    return _svg(W, H, "".join(body), title)


def hbar(labels: Sequence[str], values: Sequence[float], title="",
         w=680, row_h=24, fmt="{:.1%}", color: Optional[str] = None,
         threshold: Optional[float] = None) -> str:
    """단순 수평 막대 (위험기여 등)."""
    labels, values = list(labels), [float(v) for v in values]
    if not labels:
        return ""
    h = 44 + row_h * len(labels)
    pad_l = max(90, 8 + max(len(str(l)) for l in labels) * 7.4)
    pad_r = 74
    pw = w - pad_l - pad_r
    mx = max(1e-9, max(abs(v) for v in values if np.isfinite(v)))
    body = [_txt(14, 20, title, 12, PAL["text"], weight="600")]
    for i, (lb, v) in enumerate(zip(labels, values)):
        if not np.isfinite(v):
            continue
        y = 34 + row_h * i
        bw = abs(v) / mx * pw
        col = color or (PAL["accent"] if v >= 0 else PAL["down"])
        body.append(f'<rect x="{pad_l}" y="{y}" width="{bw:.1f}" '
                    f'height="{row_h-9}" fill="{col}" opacity="0.85" rx="2"/>')
        body.append(_txt(pad_l - 8, y + row_h / 2 - 2, str(lb)[:18], 10,
                         PAL["text"], "end"))
        body.append(_txt(w - 12, y + row_h / 2 - 2, fmt.format(v), 10,
                         col, "end"))
    if threshold is not None and np.isfinite(threshold):
        x = pad_l + abs(threshold) / mx * pw
        body.append(f'<line x1="{x:.1f}" y1="28" x2="{x:.1f}" y2="{h-8}" '
                    f'stroke="{PAL["warn"]}" stroke-width="1.6" '
                    f'stroke-dasharray="4 3"/>')
        body.append(_txt(x + 4, h - 10, f"한도 {fmt.format(threshold)}", 9,
                         PAL["warn"]))
    return _svg(w, h, "".join(body), title)


# ------------------------------------------------- 다지평 비교 (v2.17)

def horizon_compare(labels: Sequence[str],
                    series: Dict[str, Sequence[float]],
                    title: str = "지평 비교",
                    fmt: str = "pct", w: int = 720, h: int = 250) -> str:
    """
    단/중/장 지표를 **묶은 막대**로 나란히 놓는다.

    지평별 숫자를 표로만 주면 어긋나는 지점이 눈에 안 들어온다. 부호가
    뒤집히는 곳을 보이게 하는 게 이 차트의 목적이라 0선을 항상 그린다.

    series: {지표명: [단기, 중기, 장기]} — 라벨 수와 길이가 같아야 한다.
    """
    keys = [k for k, v in series.items() if len(v) == len(labels)]
    if not keys or not labels:
        return ""
    pad_l, pad_r, pad_t, pad_b = 52, 14, 34, 34
    pw, ph = w - pad_l - pad_r, h - pad_t - pad_b

    vals = [float(v) for k in keys for v in series[k]
            if v is not None and np.isfinite(v)]
    if not vals:
        return ""
    vmax, vmin = max(vals), min(vals)
    if vmax == vmin:
        vmax, vmin = vmax + 1e-9, vmin - 1e-9
    span = vmax - vmin
    vmax += span * 0.12
    vmin -= span * 0.12
    if vmin > 0:
        vmin = 0.0
    if vmax < 0:
        vmax = 0.0

    def y_of(v):
        return pad_t + ph * (vmax - v) / (vmax - vmin)

    colors = [PAL["accent"], PAL["accent2"], PAL["warn"], PAL["up"]]
    body = [_grid(pad_l, pad_t, pw, ph, 4),
            _txt(pad_l, 18, title, 12, PAL["text"], weight="600")]

    # 0선 — 부호 역전을 읽는 기준
    y0 = y_of(0.0)
    body.append(f'<line x1="{pad_l}" y1="{y0:.1f}" x2="{pad_l+pw}" '
                f'y2="{y0:.1f}" stroke="{PAL["muted"]}" stroke-width="1.2"/>')

    gw = pw / len(labels)                  # 지평 하나가 쓰는 폭
    bw = min(26.0, gw / (len(keys) + 1.2))  # 막대 하나 폭
    for gi, lab in enumerate(labels):
        gx = pad_l + gw * gi + gw / 2
        total = bw * len(keys)
        for ki, k in enumerate(keys):
            v = series[k][gi]
            if v is None or not np.isfinite(v):
                continue
            v = float(v)
            x = gx - total / 2 + bw * ki
            yv = y_of(v)
            top, hh = min(yv, y0), abs(yv - y0)
            body.append(f'<rect x="{x:.1f}" y="{top:.1f}" width="{bw-3:.1f}" '
                        f'height="{max(hh,1):.1f}" rx="2" '
                        f'fill="{colors[ki % len(colors)]}" opacity=".88"/>')
            s = (f"{v*100:.1f}%" if fmt == "pct" else f"{v:.2f}")
            body.append(_txt(x + (bw - 3) / 2, top - 4 if v >= 0 else top + hh + 11,
                             s, 9, PAL["text"], "middle"))
        body.append(_txt(gx, pad_t + ph + 16, lab, 11, PAL["text"], "middle",
                         weight="600"))

    # 범례
    lx = pad_l
    for ki, k in enumerate(keys):
        body.append(f'<rect x="{lx}" y="{h-13}" width="9" height="9" rx="2" '
                    f'fill="{colors[ki % len(colors)]}"/>')
        body.append(_txt(lx + 13, h - 5, k, 9.5, PAL["muted"]))
        lx += 16 + max(46, len(k) * 7)
    return _svg(w, h, "".join(body), title)


def drawdown_curve(prices: Sequence[float], title: str = "낙폭 (수중 곡선)",
                   w: int = 720, h: int = 200) -> str:
    """
    전고점 대비 낙폭을 시간축으로 그린다.

    '최대낙폭 -34%' 라는 숫자 하나로는 **얼마나 오래 물려 있었는지**를
    알 수 없다. 회복까지 걸린 기간이 실제 운용에서는 깊이만큼 중요해서
    곡선으로 보여준다.
    """
    p = np.asarray([x for x in prices if x is not None and np.isfinite(x)],
                   dtype=float)
    if len(p) < 5:
        return ""
    peak = np.maximum.accumulate(p)
    dd = np.where(peak > 0, p / peak - 1.0, 0.0)
    pad_l, pad_r, pad_t, pad_b = 52, 14, 30, 22
    pw, ph = w - pad_l - pad_r, h - pad_t - pad_b
    lo = float(min(dd.min(), -1e-4))

    xs = [pad_l + pw * i / (len(dd) - 1) for i in range(len(dd))]
    ys = [pad_t + ph * (0.0 - d) / (0.0 - lo) for d in dd]
    pts = " ".join(f"{x:.1f},{y:.1f}" for x, y in zip(xs, ys))

    body = [_grid(pad_l, pad_t, pw, ph, 3),
            _txt(pad_l, 17, title, 12, PAL["text"], weight="600"),
            f'<polygon points="{pad_l},{pad_t} {pts} {pad_l+pw},{pad_t}" '
            f'fill="{PAL["down"]}" opacity=".18"/>',
            f'<polyline points="{pts}" fill="none" stroke="{PAL["down"]}" '
            f'stroke-width="1.6"/>']
    # 최저점 표시
    i = int(np.argmin(dd))
    body.append(f'<circle cx="{xs[i]:.1f}" cy="{ys[i]:.1f}" r="3.2" '
                f'fill="{PAL["down"]}"/>')
    body.append(_txt(xs[i], ys[i] + 15, f"최대 {dd[i]*100:.1f}%", 10,
                     PAL["down"], "middle", weight="600"))
    body.append(_txt(pad_l - 6, pad_t + 4, "0%", 9.5, PAL["muted"], "end"))
    body.append(_txt(pad_l - 6, pad_t + ph, f"{lo*100:.0f}%", 9.5,
                     PAL["muted"], "end"))
    return _svg(w, h, "".join(body), title)


def tornado(labels: Sequence[str], values: Sequence[float],
            title: str = "기여도", unit: str = "%",
            w: int = 720, row_h: int = 22) -> str:
    """
    부호가 있는 기여도를 절대값 순으로 정렬해 좌우로 뻗는 막대.

    0 을 가운데 두어 방향이 한눈에 보이게 한다. 절대값 정렬이라
    "무엇이 가장 크게 밀고 당기는가" 가 위에서부터 읽힌다.
    """
    pairs = [(str(l), float(v)) for l, v in zip(labels, values)
             if v is not None and np.isfinite(v)]
    if not pairs:
        return ""
    pairs.sort(key=lambda kv: -abs(kv[1]))
    n = len(pairs)
    pad_l, pad_r, pad_t = 118, 60, 30
    h = pad_t + row_h * n + 14
    pw = w - pad_l - pad_r
    m = max(abs(v) for _, v in pairs) or 1.0
    cx = pad_l + pw / 2

    body = [_txt(14, 17, title, 12, PAL["text"], weight="600"),
            f'<line x1="{cx}" y1="{pad_t-4}" x2="{cx}" y2="{pad_t+row_h*n}" '
            f'stroke="{PAL["grid"]}" stroke-width="1"/>']
    for i, (lab, v) in enumerate(pairs):
        y = pad_t + row_h * i
        bl = abs(v) / m * (pw / 2 - 6)
        col = PAL["up"] if v > 0 else PAL["down"]
        x = cx if v > 0 else cx - bl
        body.append(f'<rect x="{x:.1f}" y="{y+4:.1f}" width="{max(bl,1):.1f}" '
                    f'height="{row_h-9}" rx="2" fill="{col}" opacity=".85"/>')
        body.append(_txt(pad_l - 10, y + row_h / 2 + 4, lab, 10.5,
                         PAL["text"], "end"))
        body.append(_txt(w - pad_r + 8, y + row_h / 2 + 4,
                         f"{v:+.2f}{unit}", 10, col, "start"))
    return _svg(w, h, "".join(body), title)
