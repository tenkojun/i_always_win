"""
실시간 워커 (C12) — 백그라운드에서 속보/awareness 모니터링.
새 항목 감지 시 EventBus로 push.
"""
from __future__ import annotations

import threading
import time
from typing import Set

from . import eventbus


_STOP = threading.Event()
_STARTED = False
_LOCK = threading.Lock()

# 중복 push 방지용 hash 캐시
_SEEN_BREAKING: Set[int] = set()
_SEEN_AWARENESS: Set[str] = set()


def _hash_title(s: str) -> int:
    h = 5381
    for ch in s:
        h = ((h << 5) + h + ord(ch)) & 0xFFFFFFFF
    return h


def _get_all_alerts(limit=60, only_high=False):
    """awareness 모듈에서 안전하게 alerts 조회."""
    try:
        from engine.awareness.alert_engine import get_all_alerts
        res = get_all_alerts(limit=limit, only_high_impact=only_high)
        return res.get("items", []) if isinstance(res, dict) else (res or [])
    except Exception:
        return []


def _poll_breaking():
    """속보 새 항목 감지 → publish('breaking_new', [...])
    awareness alert engine의 전체 알림을 속보로 간주.
    """
    items = _get_all_alerts(limit=30, only_high=False)
    if not items:
        return
    new_items = []
    for it in items:
        t = it.get("title", "") or it.get("headline", "")
        if not t:
            continue
        h = _hash_title(t)
        if h in _SEEN_BREAKING:
            continue
        _SEEN_BREAKING.add(h)
        new_items.append(it)
    # 캐시 시드 직후엔 push 안 함
    if not new_items or len(_SEEN_BREAKING) <= len(items):
        return
    eventbus.publish("breaking_new",
                      {"items": new_items[:10],
                       "count": len(new_items)})
    # 캐시 너무 크면 절반 컷
    if len(_SEEN_BREAKING) > 500:
        _SEEN_BREAKING.clear()
        for it in items[:50]:
            _SEEN_BREAKING.add(_hash_title(
                it.get("title", "") or it.get("headline", "")))


def _poll_awareness():
    """high-impact 전용 알림 push."""
    items = _get_all_alerts(limit=20, only_high=True)
    if not items:
        return
    new_items = []
    for it in items:
        key = ((it.get("symbol", "") or it.get("asset", "")) + "|" +
               (it.get("title", "") or it.get("headline", "")))[:200]
        if not key.strip("|"):
            continue
        if key in _SEEN_AWARENESS:
            continue
        _SEEN_AWARENESS.add(key)
        new_items.append(it)
    if not new_items or len(_SEEN_AWARENESS) <= len(items):
        return
    eventbus.publish("awareness_alert",
                      {"items": new_items[:5]})
    if len(_SEEN_AWARENESS) > 500:
        _SEEN_AWARENESS.clear()


def _loop(interval_sec: int = 30):
    """폴링 루프 — interval_sec마다 모니터."""
    # 첫 1회는 시드용 (캐시 채우기, push 없음)
    items = _get_all_alerts(limit=30)
    for it in items:
        t = it.get("title", "") or it.get("headline", "")
        if t:
            _SEEN_BREAKING.add(_hash_title(t))
    # 본 루프
    while not _STOP.is_set():
        _poll_breaking()
        _poll_awareness()
        _STOP.wait(interval_sec)


def start(interval_sec: int = 30) -> None:
    global _STARTED
    with _LOCK:
        if _STARTED:
            return
        _STARTED = True
        _STOP.clear()
        t = threading.Thread(target=_loop, args=(interval_sec,),
                              daemon=True)
        t.start()


def stop() -> None:
    global _STARTED
    with _LOCK:
        _STOP.set()
        _STARTED = False
