"""
Trade Size Cluster — 체결 크기 분포 분석
============================================================
체결 크기 분포로 기관/개인 트레이더 추정.
- 작은 체결 다수 = 개미 (retail)
- 큰 체결 + 정수 사이즈 = 기관 (institutional)

분류:
  - retail:    1~50주
  - small_inst: 51~500
  - large_inst: 501~5000
  - block:    5000+ (대량 블록)
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional


def trade_size_distribution(ticks: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not ticks:
        return {"n_ticks": 0, "buckets": {}}
    buckets = {"retail": 0, "small_inst": 0,
                "large_inst": 0, "block": 0}
    bucket_vol = {k: 0 for k in buckets}
    for t in ticks:
        sz = int(t.get("size") or 0)
        if sz <= 0:
            continue
        if sz <= 50:
            k = "retail"
        elif sz <= 500:
            k = "small_inst"
        elif sz <= 5000:
            k = "large_inst"
        else:
            k = "block"
        buckets[k] += 1
        bucket_vol[k] += sz
    total_vol = sum(bucket_vol.values()) or 1
    return {
        "n_ticks":     len(ticks),
        "buckets":     buckets,
        "volumes":     bucket_vol,
        "volume_share": {k: round(v/total_vol, 3)
                          for k, v in bucket_vol.items()},
        "interpretation": _interp(bucket_vol, total_vol),
    }


def _interp(vol: Dict[str, int], total: int) -> str:
    if total <= 0:
        return "데이터 없음"
    inst_share = (vol["small_inst"] + vol["large_inst"] + vol["block"]) / total
    block_share = vol["block"] / total
    if block_share > 0.3:
        return "블록 트레이드 비중 높음 — 기관 활동 강함"
    if inst_share > 0.6:
        return "기관 우위 (작은 체결 < 40%)"
    if inst_share < 0.3:
        return "개인 우위 (작은 체결 > 70%)"
    return "혼재"
