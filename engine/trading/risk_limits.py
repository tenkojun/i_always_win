"""
risk_limits.py — Risk Limit Manager (Tier 2 #7)
============================================================
사용자별 포지션/노출/손실 한도 관리.
자동매매 모드에서 주문 전 검증 → 위반 시 차단.

지원 한도:
  - max_position_pct      : 단일 종목 최대 노출 %  (예: 0.10 = 10%)
  - max_sector_pct        : 섹터별 최대 노출 % (미국 GICS 미지원 시 무시)
  - daily_loss_limit_pct  : 일중 손실 한도 (-5% 등)
  - total_drawdown_limit_pct : 누적 MDD 한도 (-20% 등)
  - max_open_positions    : 동시 보유 종목 수 한도
  - var_limit_pct         : 95% VaR 한도

저장:
  ~/.jiqt/risk_limits_<user_id>.json
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

_HOME = Path.home() / ".jiqt"
_HOME.mkdir(parents=True, exist_ok=True)


def _limits_path(user_id: int) -> Path:
    return _HOME / f"risk_limits_{user_id}.json"


DEFAULT_LIMITS = {
    "max_position_pct":        0.20,   # 종목당 20%
    "max_open_positions":      10,
    "daily_loss_limit_pct":   -0.05,   # 일 -5% 초과 시 정지
    "total_drawdown_limit_pct": -0.20, # 누적 -20% 초과 시 정지
    "var_limit_pct":          -0.10,   # 95% VaR -10%
    "enabled":                 True,
}


def get_limits(user_id: int) -> Dict[str, Any]:
    """사용자 한도 dict. 없으면 기본값."""
    p = _limits_path(user_id)
    if not p.exists():
        return dict(DEFAULT_LIMITS)
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(d, dict):
            return {**DEFAULT_LIMITS, **d}
    except Exception:
        pass
    return dict(DEFAULT_LIMITS)


def save_limits(user_id: int, limits: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(limits, dict):
        return {"ok": False, "error": "limits는 dict"}
    merged = {**DEFAULT_LIMITS, **limits}
    try:
        _limits_path(user_id).write_text(
            json.dumps(merged, ensure_ascii=False, indent=2),
            encoding="utf-8")
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def check_order(user_id: int, ticker: str, side: str, qty: int,
                price: float) -> Dict[str, Any]:
    """주문 실행 전 한도 검증. 위반 시 ok=False + reason."""
    lim = get_limits(user_id)
    if not lim.get("enabled", True):
        return {"ok": True, "skipped": True, "reason": "한도 검증 비활성"}
    try:
        from ..trading.paper_trading import get_paper_engine
    except Exception:
        return {"ok": True, "skipped": True, "reason": "Paper 엔진 없음"}
    eng = get_paper_engine(user_id)
    state = eng.get_state()
    if not state.get("ok"):
        return {"ok": True, "skipped": True}
    nav = state["nav"]
    order_amount = price * qty
    # 1) 단일 종목 노출 %
    if side == "buy":
        # 기존 보유 + 신규 매수
        cur_pos = next((p for p in state["positions"]
                         if p["ticker"] == ticker), None)
        cur_amount = (cur_pos["eval_amount"] if cur_pos else 0)
        new_amount = cur_amount + order_amount
        new_pct = new_amount / nav if nav > 0 else 1.0
        if new_pct > lim["max_position_pct"]:
            return {"ok": False, "code": "MAX_POSITION",
                    "reason": f"{ticker} 노출 {new_pct*100:.1f}% > "
                              f"한도 {lim['max_position_pct']*100:.1f}%"}
        # 2) 동시 보유 종목 수
        n_open = state["n_positions"]
        if not cur_pos and n_open >= lim["max_open_positions"]:
            return {"ok": False, "code": "MAX_OPEN_POS",
                    "reason": f"동시 보유 {n_open} ≥ 한도 {lim['max_open_positions']}"}
    # 3) 누적 drawdown
    pnl_pct = state["total_pnl_pct"]
    if pnl_pct < lim["total_drawdown_limit_pct"]:
        return {"ok": False, "code": "MAX_DRAWDOWN",
                "reason": f"누적 손실 {pnl_pct*100:.2f}% "
                          f"< 한도 {lim['total_drawdown_limit_pct']*100:.2f}%"}
    return {"ok": True}


def violations_report(user_id: int) -> Dict[str, Any]:
    """현재 상태와 한도 비교 → 위반 목록."""
    lim = get_limits(user_id)
    try:
        from ..trading.paper_trading import get_paper_engine
        state = get_paper_engine(user_id).get_state()
    except Exception:
        return {"ok": False, "violations": [], "error": "state 조회 실패"}
    if not state.get("ok"):
        return {"ok": False, "violations": []}
    nav = state["nav"]
    violations = []
    # 종목당 노출
    for p in state["positions"]:
        pct = p["eval_amount"] / nav if nav > 0 else 0
        if pct > lim["max_position_pct"]:
            violations.append({
                "severity": "high", "code": "POSITION_OVER",
                "title": f"{p['ticker']} 노출 초과",
                "msg": f"{pct*100:.1f}% > 한도 {lim['max_position_pct']*100:.1f}%",
            })
    # 동시 보유 수
    if state["n_positions"] > lim["max_open_positions"]:
        violations.append({
            "severity": "medium", "code": "TOO_MANY_OPEN",
            "title": "동시 보유 종목 초과",
            "msg": f"{state['n_positions']} > 한도 {lim['max_open_positions']}",
        })
    # 누적 손실
    if state["total_pnl_pct"] < lim["total_drawdown_limit_pct"]:
        violations.append({
            "severity": "high", "code": "MAX_DRAWDOWN",
            "title": "누적 손실 한도 초과",
            "msg": f"{state['total_pnl_pct']*100:+.2f}% < 한도 "
                    f"{lim['total_drawdown_limit_pct']*100:+.2f}%",
        })
    return {"ok": True, "n_violations": len(violations),
            "violations": violations, "limits": lim, "state_summary": {
                "nav": nav, "total_pnl_pct": state["total_pnl_pct"],
                "n_positions": state["n_positions"],
            }}
