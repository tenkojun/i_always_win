"""
포트폴리오 종합 분석
======================
사용자 보유 종목 전체를 합산해 포트폴리오 단위로 평가:
  - 가중 수익률 (현재 평가액 비중)
  - 섹터/카테고리 분포
  - 페어 상관행렬 → 분산 효과
  - 포트폴리오 변동성 (가중 표준편차)
  - 최대 낙폭, Sharpe
  - 종목별 기여도 (수익/리스크)
"""
from __future__ import annotations

import datetime as dt
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd


def _fetch_returns(ticker: str,
                   period_days: int = 365) -> Optional[pd.Series]:
    """단일 종목 일간 로그수익률. 실패 시 None."""
    from ..data.sources import fetch_ohlcv_best
    end = dt.date.today().strftime("%Y-%m-%d")
    start = (dt.date.today()
             - dt.timedelta(days=period_days + 30)).strftime("%Y-%m-%d")
    try:
        r = fetch_ohlcv_best(ticker, start=start, end=end,
                             cross_validate=False)
        df = r.get("df")
        if df is None or len(df) < 30:
            return None
        rets = np.log(df["close"] / df["close"].shift(1)).dropna()
        return rets.tail(period_days)
    except Exception:
        return None


def _fetch_sector(ticker: str) -> str:
    """다중소스 펀더에서 섹터 추출. 실패 시 '기타'."""
    try:
        from ..data.sources import fetch_fundamentals_best
        f = fetch_fundamentals_best(ticker) or {}
        return (f.get("sector") or "기타").upper()
    except Exception:
        return "기타"


def analyze(holdings: List[Dict[str, Any]],
            period_days: int = 365) -> Dict[str, Any]:
    """
    holdings: portfolio_holdings 행 리스트
              (ticker, quantity, avg_cost, currency)
    """
    if not holdings:
        return {"ok": False, "error": "보유 종목 없음"}

    # 1) 종목별 시세/펀더 fetch
    enriched: List[Dict[str, Any]] = []
    rets_map: Dict[str, pd.Series] = {}
    for h in holdings:
        tk = h["ticker"]
        rets = _fetch_returns(tk, period_days)
        sector = _fetch_sector(tk)
        # 현재가 (마지막 close)
        cur_px = None
        if rets is not None and len(rets) > 0:
            # 누적합으로 마지막 종가 추정 못 함 — 별도 fetch
            from ..data.sources import fetch_ohlcv_best
            try:
                r = fetch_ohlcv_best(tk,
                                     start=(dt.date.today()
                                            - dt.timedelta(days=10)
                                            ).strftime("%Y-%m-%d"),
                                     end=dt.date.today().strftime("%Y-%m-%d"),
                                     cross_validate=False)
                cur_px = float(r["df"]["close"].iloc[-1])
            except Exception:
                pass
        qty = float(h["quantity"])
        avg = float(h["avg_cost"])
        cost = qty * avg
        value = (cur_px or avg) * qty
        pnl = value - cost
        pnl_pct = (pnl / cost * 100) if cost else 0
        enriched.append({
            "ticker": tk, "quantity": qty, "avg_cost": avg,
            "current_price": cur_px, "cost": cost, "value": value,
            "pnl": pnl, "pnl_pct": pnl_pct, "sector": sector,
        })
        if rets is not None:
            rets_map[tk] = rets

    total_cost = sum(e["cost"] for e in enriched)
    total_value = sum(e["value"] for e in enriched)
    total_pnl = total_value - total_cost
    total_pnl_pct = (total_pnl / total_cost * 100) if total_cost else 0

    # 2) 비중 (현재 평가액 기준)
    weights = {}
    if total_value > 0:
        for e in enriched:
            weights[e["ticker"]] = e["value"] / total_value

    # 3) 섹터 분포
    sector_dist: Dict[str, float] = {}
    for e in enriched:
        sec = e["sector"] or "기타"
        sector_dist[sec] = sector_dist.get(sec, 0) + e["value"]
    sector_pct = {k: v / total_value * 100
                  for k, v in sector_dist.items()} if total_value > 0 else {}

    # 4) 페어 상관행렬 (공통 날짜)
    corr_data: Dict[str, Any] = {}
    if len(rets_map) >= 2:
        df_rets = pd.DataFrame(rets_map).dropna()
        if len(df_rets) >= 30:
            corr = df_rets.corr()
            corr_data["matrix"] = corr.round(2).to_dict()
            # 평균 페어 상관 (대각 제외)
            n = len(corr)
            tri = corr.values[np.triu_indices(n, k=1)]
            corr_data["avg_pair_corr"] = round(float(np.mean(tri)), 3)
            # 분산효과 = 1 - sqrt(평균상관) (Markowitz 직관)
            corr_data["diversification"] = round(
                max(0, 1 - np.sqrt(max(0, np.mean(tri)))), 3)

    # 5) 포트폴리오 변동성 (가중)
    port_metrics: Dict[str, Any] = {}
    if len(rets_map) >= 1 and weights:
        # 공통 날짜로 정렬
        df_rets = pd.DataFrame(rets_map).dropna()
        if len(df_rets) >= 30:
            w_arr = np.array([weights.get(c, 0)
                              for c in df_rets.columns])
            w_arr = w_arr / w_arr.sum() if w_arr.sum() > 0 else w_arr
            port_rets = df_rets.values @ w_arr
            port_metrics["annual_volatility"] = round(
                float(np.std(port_rets) * np.sqrt(252)), 4)
            port_metrics["annual_return"] = round(
                float(np.mean(port_rets) * 252), 4)
            sharpe = (port_metrics["annual_return"]
                      / port_metrics["annual_volatility"]
                      if port_metrics["annual_volatility"] > 0 else 0)
            port_metrics["sharpe"] = round(sharpe, 2)
            # 누적 + MDD
            cum = np.cumprod(1 + port_rets)
            peak = np.maximum.accumulate(cum)
            dd = (cum / peak - 1)
            port_metrics["max_drawdown"] = round(float(dd.min()), 4)
            # 종목별 기여도 (단순: weight * 종목 annual return)
            contrib = {}
            for c in df_rets.columns:
                w = weights.get(c, 0)
                ar = float(df_rets[c].mean() * 252)
                contrib[c] = {
                    "weight": round(w, 4),
                    "annual_return": round(ar, 4),
                    "contribution": round(w * ar, 4),
                }
            port_metrics["contributions"] = contrib

    # 6) 인사이트 (한국어)
    insights: List[str] = []
    if total_pnl_pct > 5:
        insights.append(f"포트폴리오 총수익률 +{total_pnl_pct:.2f}%로 양호.")
    elif total_pnl_pct < -5:
        insights.append(f"총수익률 {total_pnl_pct:.2f}% — 손실 큰 종목 점검 필요.")
    if sector_pct:
        top_sec, top_pct = max(sector_pct.items(), key=lambda x: x[1])
        if top_pct > 50:
            insights.append(
                f"{top_sec} 섹터 비중 {top_pct:.1f}% — 집중도 높음, 분산 권장.")
    if corr_data.get("avg_pair_corr", 0) > 0.7:
        insights.append("종목 간 평균 상관 0.7+ — 동조화 위험, "
                        "다른 섹터/자산군 추가 권장.")
    elif corr_data.get("avg_pair_corr", 1) < 0.3:
        insights.append("종목 간 평균 상관 0.3 미만 — 분산효과 양호.")
    if port_metrics.get("sharpe", 0) > 1:
        insights.append(
            f"Sharpe {port_metrics['sharpe']} — 위험조정수익률 우수.")
    elif port_metrics.get("sharpe", 0) < 0:
        insights.append(f"Sharpe {port_metrics['sharpe']} — 위험 대비 수익 부족.")
    if port_metrics.get("max_drawdown", 0) < -0.20:
        insights.append(
            f"MDD {abs(port_metrics['max_drawdown'])*100:.1f}% — "
            "낙폭 큼, 헤지 검토.")

    return {
        "ok": True,
        "summary": {
            "count": len(enriched),
            "total_cost": round(total_cost, 2),
            "total_value": round(total_value, 2),
            "total_pnl": round(total_pnl, 2),
            "total_pnl_pct": round(total_pnl_pct, 2),
        },
        "holdings": enriched,
        "weights": {k: round(v, 4) for k, v in weights.items()},
        "sector_distribution": {k: round(v, 2)
                                 for k, v in sector_pct.items()},
        "correlation": corr_data,
        "portfolio_metrics": port_metrics,
        "insights": insights,
        "period_days": period_days,
    }
