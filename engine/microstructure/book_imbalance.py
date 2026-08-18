"""
Order Book Imbalance — 매수/매도 잔량 비율
============================================================
호가 10단계 총량 비교로 단기 가격 방향 추정.

수식:
  imbalance = (total_bid_size - total_ask_size) /
              (total_bid_size + total_ask_size)
  -1 ~ +1, 0 = 균형
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional


def book_imbalance(book: Dict[str, Any],
                    depth: int = 10) -> Dict[str, Any]:
    """단일 호가 스냅샷 → imbalance 지표.

    book: {bids: [{price,size}], asks: [{price,size}]}
    """
    bids = (book.get("bids") or [])[:depth]
    asks = (book.get("asks") or [])[:depth]
    bv = sum(int(b.get("size") or 0) for b in bids)
    av = sum(int(a.get("size") or 0) for a in asks)
    total = bv + av
    if total <= 0:
        return {"imbalance": 0.0, "bid_size": 0, "ask_size": 0,
                "n_levels": 0, "interpretation": "데이터 없음"}
    imb = (bv - av) / total
    if   imb >  0.4:  interp = "강한 매수 우위 (가격 상승 압력)"
    elif imb >  0.15: interp = "매수 우위"
    elif imb < -0.4:  interp = "강한 매도 우위 (가격 하락 압력)"
    elif imb < -0.15: interp = "매도 우위"
    else:             interp = "균형"
    return {
        "imbalance":      round(imb, 4),
        "bid_size":       bv,
        "ask_size":       av,
        "total_size":     total,
        "n_levels":       min(len(bids), len(asks)),
        "best_bid":       bids[0]["price"] if bids else 0,
        "best_ask":       asks[0]["price"] if asks else 0,
        "spread":         (asks[0]["price"] - bids[0]["price"])
                            if bids and asks else 0,
        "interpretation": interp,
    }


def pressure_score(books: List[Dict[str, Any]],
                    depth: int = 5) -> Dict[str, Any]:
    """최근 N개 호가 스냅샷의 평균 imbalance.
    단일 스냅샷보다 안정적인 압력 추정.
    """
    if not books:
        return {"avg_imbalance": 0.0, "n": 0,
                "interpretation": "데이터 없음"}
    imbs = []
    for b in books[-50:]:
        r = book_imbalance(b, depth=depth)
        imbs.append(r["imbalance"])
    if not imbs:
        return {"avg_imbalance": 0.0, "n": 0}
    avg = sum(imbs) / len(imbs)
    std = (sum((x - avg)**2 for x in imbs) / len(imbs)) ** 0.5
    return {
        "avg_imbalance": round(avg, 4),
        "std":           round(std, 4),
        "n":             len(imbs),
        "latest":        round(imbs[-1], 4),
        "trend":         "상승" if avg > 0.1 else "하락" if avg < -0.1 else "중립",
    }
