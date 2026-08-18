# ==============================================================================
# [03/25] micro.py — EDGE 스프레드 · Amihud · 제곱근 임팩트 · capacity
# ==============================================================================

"""
jiqtx.micro — 일봉 OHLCV로 계산 가능한 유동성·거래비용 지표.

원본 리포트의 일봉 VPIN/CVD 프록시를 대체한다.
Andersen & Bondarenko (Review of Finance 2015)는 VPIN이 거래강도와 기계적으로
연동되며 그 예측력이 거래강도·실현변동성에 흡수된다고 반박했다. 체결방향을
모르는 일봉 기반 VPIN 프록시는 정보 함량이 사실상 0이다.

대체 지표
---------
- EDGE      : Ardia, Guidotti & Kroencke (JFE 2024, 161:103916).
              OHLC 전부를 최적 결합. 거래가 희소해도 점근적 불편.
- CS        : Corwin & Schultz (JF 2012). High-Low.
- CHL       : Abdi & Ranaldo (RFS 2017). Close-High-Low.
- Roll      : Roll (1984). 공분산 기반.
- Amihud    : Amihud (2002). |r| / dollar volume.
- Kyle-Ob.  : 변동성/거래량 기반 유동성 수준.
- 제곱근 임팩트: G ≈ Y·σ_d·√(Q/ADV)  (Almgren et al. 2005; Tóth et al.; Bouchaud)

주의
----
EDGE 구현은 공개된 pseudocode 구조를 따랐다. 운영 투입 전 참조 구현
(R `bidask` 패키지)과 수치를 대조할 것. 본 모듈은 CS/CHL/Roll을 함께
계산해 교차검증(cross-check) 필드를 제공한다.
"""

import numpy as np
import pandas as pd
from dataclasses import dataclass, asdict
from typing import Optional, Dict


# ---------------------------------------------------------------- 스프레드 추정


def _safe_log(x: np.ndarray) -> np.ndarray:
    x = np.asarray(x, dtype=float)
    out = np.full_like(x, np.nan)
    ok = np.isfinite(x) & (x > 0)
    out[ok] = np.log(x[ok])
    return out


def edge_spread(o, h, l, c, sign: bool = False) -> float:
    """
    EDGE 유효 스프레드 추정량 (Ardia-Guidotti-Kroencke 2024).

    Returns
    -------
    float : 유효 스프레드(가격 대비 비율). 음수 분산이면 nan.
    """
    o, h, l, c = map(lambda z: _safe_log(np.asarray(z, float)), (o, h, l, c))
    n = len(o)
    if n < 5:
        return np.nan

    m = (h + l) / 2.0

    # t 와 t-1
    o1, h1, l1, c1, m1 = o[:-1], h[:-1], l[:-1], c[:-1], m[:-1]
    o_, h_, l_, c_, m_ = o[1:], h[1:], l[1:], c[1:], m[1:]

    # 지시함수: 가격이 전부 같은 날은 정보가 없음
    tau = ((h_ != l_) | (l_ != c1)).astype(float)
    po1 = tau * (o_ != h_)
    po2 = tau * (o_ != l_)
    pc1 = tau * (c1 != h1)
    pc2 = tau * (c1 != l1)

    valid = np.isfinite(o_) & np.isfinite(h_) & np.isfinite(l_) & \
            np.isfinite(c_) & np.isfinite(m_) & np.isfinite(m1) & np.isfinite(c1)
    if valid.sum() < 5:
        return np.nan

    def nm(x):  # nan-safe mean over valid
        return float(np.nanmean(np.where(valid, x, np.nan)))

    pt = nm(tau)
    po = nm(po1) + nm(po2)
    pc = nm(pc1) + nm(pc2)
    if not np.isfinite(pt) or pt <= 0 or po <= 0 or pc <= 0:
        return np.nan

    r1 = m_ - o_
    r2 = o_ - m1
    r3 = m_ - c1
    r4 = c1 - m1
    r5 = o_ - c1

    # tau 가중 평균으로 디민
    def demean_tau(r):
        w = np.where(valid & (tau > 0), 1.0, 0.0)
        if w.sum() == 0:
            return r
        mu = np.nansum(np.where(w > 0, r, 0.0)) / w.sum()
        return r - mu

    d1, d3, d5 = demean_tau(r1), demean_tau(r3), demean_tau(r5)

    x1 = -4.0 / po * d1 * r2 - 4.0 / pc * d3 * r4
    x2 = -4.0 / po * d1 * r5 - 4.0 / pc * d5 * r4

    e1, e2 = nm(x1), nm(x2)
    v1 = nm(x1 ** 2) - e1 ** 2
    v2 = nm(x2 ** 2) - e2 ** 2
    if not np.isfinite(v1) or not np.isfinite(v2) or (v1 + v2) <= 0:
        return np.nan

    s2 = (v2 * e1 + v1 * e2) / (v1 + v2)
    if not np.isfinite(s2):
        return np.nan
    if sign:
        return float(np.sign(s2) * np.sqrt(abs(s2)))
    return float(np.sqrt(max(s2, 0.0)))


def corwin_schultz(high, low) -> float:
    """Corwin-Schultz (2012) High-Low 스프레드. 음수 추정치는 0으로 절단 후 평균."""
    h = _safe_log(np.asarray(high, float))
    l = _safe_log(np.asarray(low, float))
    if len(h) < 3:
        return np.nan
    hl = h - l
    beta = hl[:-1] ** 2 + hl[1:] ** 2
    h2 = np.maximum(h[:-1], h[1:])
    l2 = np.minimum(l[:-1], l[1:])
    gamma = (h2 - l2) ** 2
    k = 3.0 - 2.0 * np.sqrt(2.0)
    alpha = (np.sqrt(2.0 * beta) - np.sqrt(beta)) / k - np.sqrt(gamma / k)
    s = 2.0 * (np.exp(alpha) - 1.0) / (1.0 + np.exp(alpha))
    s = np.where(np.isfinite(s), s, np.nan)
    s = np.clip(s, 0.0, None)
    return float(np.nanmean(s)) if np.isfinite(s).any() else np.nan


def abdi_ranaldo(high, low, close) -> float:
    """Abdi-Ranaldo (2017) Close-High-Low 추정량."""
    h = _safe_log(np.asarray(high, float))
    l = _safe_log(np.asarray(low, float))
    c = _safe_log(np.asarray(close, float))
    if len(c) < 3:
        return np.nan
    m = (h + l) / 2.0
    x = 4.0 * (c[:-1] - m[:-1]) * (c[:-1] - m[1:])
    s2 = np.nanmean(x)
    return float(np.sqrt(max(s2, 0.0))) if np.isfinite(s2) else np.nan


def roll_spread(close) -> float:
    """Roll (1984) 공분산 추정량."""
    c = _safe_log(np.asarray(close, float))
    r = np.diff(c)
    r = r[np.isfinite(r)]
    if len(r) < 5:
        return np.nan
    cov = float(np.cov(r[:-1], r[1:])[0, 1])
    return float(2.0 * np.sqrt(-cov)) if cov < 0 else 0.0


# ---------------------------------------------------------------- 유동성 수준


def amihud_illiquidity(ret: np.ndarray, dollar_vol: np.ndarray) -> float:
    """Amihud (2002). 1e6 스케일로 표기 (백만달러당 % 가격충격 개념)."""
    r = np.abs(np.asarray(ret, float))
    dv = np.asarray(dollar_vol, float)
    ok = np.isfinite(r) & np.isfinite(dv) & (dv > 0)
    if ok.sum() < 20:
        return np.nan
    return float(np.mean(r[ok] / dv[ok]) * 1e6)


def kyle_obizhaeva(ret: np.ndarray, dollar_vol: np.ndarray) -> float:
    """Kyle-Obizhaeva 계열 변동성/거래량 유동성 지표 (수준 추정에 강함)."""
    r = np.asarray(ret, float)
    dv = np.asarray(dollar_vol, float)
    ok = np.isfinite(r) & np.isfinite(dv) & (dv > 0)
    if ok.sum() < 20:
        return np.nan
    sig = float(np.std(r[ok], ddof=1))
    med_dv = float(np.median(dv[ok]))
    if med_dv <= 0:
        return np.nan
    return float(sig / (med_dv ** (1.0 / 3.0)))


def zero_return_ratio(ret: np.ndarray) -> float:
    r = np.asarray(ret, float)
    r = r[np.isfinite(r)]
    if len(r) == 0:
        return np.nan
    return float(np.mean(np.abs(r) < 1e-12))


# ---------------------------------------------------------------- 임팩트·용량


def sqrt_impact(participation: float, daily_vol: float, Y: float = 0.7) -> float:
    """
    제곱근 임팩트 법칙: G ≈ Y · σ_daily · sqrt(Q/ADV).

    participation = Q / ADV (일평균거래대금 대비 주문 비중)
    선형 모델은 대형 주문 비용을 극심하게 과소평가한다.
    """
    p = max(float(participation), 0.0)
    return float(Y * float(daily_vol) * np.sqrt(p))


def roundtrip_cost(spread: float, participation: float, daily_vol: float,
                   Y: float = 0.7) -> float:
    """왕복 비용 = 스프레드(반값 ×2) + 임팩트 ×2."""
    sp = 0.0 if not np.isfinite(spread) else float(spread)
    return float(sp + 2.0 * sqrt_impact(participation, daily_vol, Y))


def capacity_curve(adv_usd: float, daily_vol: float, gross_edge_ann: float,
                   turnover_per_year: float = 12.0,
                   aum_grid: Optional[np.ndarray] = None,
                   spread: float = 0.0005, Y: float = 0.7) -> pd.DataFrame:
    """
    AUM별 순 알파 곡선. 알파가 0이 되는 지점이 전략 용량(capacity).

    gross_edge_ann : 비용 전 연 초과수익 추정(예: 0.04 = 4%)
    """
    if aum_grid is None:
        aum_grid = np.array([1e5, 1e6, 5e6, 2e7, 1e8, 5e8, 2e9])
    rows = []
    for aum in aum_grid:
        # 1회 거래 규모 = AUM (완전 회전 가정), 일 참여율 = AUM / ADV
        part = aum / max(adv_usd, 1.0)
        rt = roundtrip_cost(spread, part, daily_vol, Y)
        net = gross_edge_ann - turnover_per_year * rt
        rows.append({"aum_usd": float(aum), "participation": float(part),
                     "roundtrip_cost": float(rt), "net_alpha_ann": float(net)})
    return pd.DataFrame(rows)


# ---------------------------------------------------------------- 요약 컨테이너


@dataclass
class LiquidityProfile:
    spread_edge: float
    spread_cs: float
    spread_chl: float
    spread_roll: float
    spread_used: float
    spread_bps: float
    spread_dispersion: float      # 추정량 간 불일치 = 측정 불확실성
    amihud: float
    kyle_obizhaeva: float
    zero_ret_ratio: float
    adv_usd: float
    days_to_liquidate_1pct_aum: float
    tradable: bool
    reason: str = ""

    def to_dict(self) -> Dict:
        return asdict(self)


def liquidity_profile(df: pd.DataFrame, max_spread_bps: float,
                      min_adv_usd: float, max_zero_ratio: float,
                      window: int = 252, aum_usd: float = 1e7) -> LiquidityProfile:
    """
    df: columns = Open, High, Low, Close, Volume (최근 구간)
    """
    d = df.tail(window)
    o, h, l, c = d["Open"].values, d["High"].values, d["Low"].values, d["Close"].values
    v = d["Volume"].values.astype(float)
    ret = np.diff(np.log(np.where(c > 0, c, np.nan)))
    dollar_vol = c[1:] * v[1:]

    s_edge = edge_spread(o, h, l, c)
    s_cs = corwin_schultz(h, l)
    s_chl = abdi_ranaldo(h, l, c)
    s_roll = roll_spread(c)

    cands = np.array([s_edge, s_cs, s_chl, s_roll], dtype=float)
    finite = cands[np.isfinite(cands)]
    # 우선순위: EDGE > CHL > CS > Roll
    s_used = next((s for s in (s_edge, s_chl, s_cs, s_roll) if np.isfinite(s)), np.nan)
    disp = float(np.nanstd(finite)) if len(finite) >= 2 else np.nan

    adv = float(np.nanmedian(dollar_vol)) if len(dollar_vol) else np.nan
    zr = zero_return_ratio(ret)
    ami = amihud_illiquidity(ret, dollar_vol)
    ko = kyle_obizhaeva(ret, dollar_vol)

    # 청산 소요일수: 포지션(AUM의 1%)을 ADV의 10%씩 처분
    pos = 0.01 * aum_usd
    dtl = float(pos / max(0.10 * adv, 1.0)) if np.isfinite(adv) else np.nan

    bps = s_used * 1e4 if np.isfinite(s_used) else np.nan
    reasons = []
    if not np.isfinite(bps):
        reasons.append("스프레드 추정 불가")
    elif bps > max_spread_bps:
        reasons.append(f"스프레드 {bps:.0f}bp > 한도 {max_spread_bps:.0f}bp")
    if np.isfinite(adv) and adv < min_adv_usd:
        reasons.append(f"ADV ${adv:,.0f} < 한도 ${min_adv_usd:,.0f}")
    if np.isfinite(zr) and zr > max_zero_ratio:
        reasons.append(f"무거래일 비율 {zr:.1%} 초과")

    return LiquidityProfile(
        spread_edge=s_edge, spread_cs=s_cs, spread_chl=s_chl, spread_roll=s_roll,
        spread_used=s_used, spread_bps=bps, spread_dispersion=disp,
        amihud=ami, kyle_obizhaeva=ko, zero_ret_ratio=zr, adv_usd=adv,
        days_to_liquidate_1pct_aum=dtl,
        tradable=(len(reasons) == 0), reason="; ".join(reasons),
    )
