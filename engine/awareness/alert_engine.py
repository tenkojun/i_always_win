"""
Awareness Alert Engine
========================
여러 뉴스 소스(GDELT + 국가별 RSS)를 폴링 + 자산 매핑 + 우선순위 점수.

백그라운드 스레드가 5분마다 자동 갱신, 메모리 dict에 캐싱.
서버 엔드포인트가 이 dict를 읽어 JSON 응답.

캐싱:
  _alerts[asset_symbol] = [
      {title, url, domain, ts, age_min, priority, matched_keywords},
      ...
  ] (priority 내림차순, 최대 20개)
"""
from __future__ import annotations

import datetime as dt
import hashlib
import threading
import time
from typing import Any, Dict, List, Optional

from .event_map import (
    detect_assets, priority_score, is_high_impact, ASSET_LABELS,
)
from .gdelt import fetch_recent as fetch_gdelt

# 자산별 alert 캐시
_LOCK = threading.Lock()
_alerts: Dict[str, List[Dict[str, Any]]] = {sym: [] for sym in ASSET_LABELS}
_last_refresh: Optional[dt.datetime] = None
_refresh_count = 0
_seen_hashes: set = set()  # 중복 제거용 (title prefix hash)
_MAX_PER_ASSET = 20
_MAX_AGE_HOURS = 12  # 12시간 지난 alert 제거


def _title_hash(title: str) -> str:
    """제목 60자 prefix의 짧은 해시."""
    return hashlib.md5(title[:60].lower().encode()).hexdigest()[:10]


def _age_minutes(seen_iso: str) -> float:
    if not seen_iso:
        return 60.0
    try:
        seen = dt.datetime.fromisoformat(seen_iso.replace("Z", "+00:00"))
        if seen.tzinfo is None:
            seen = seen.replace(tzinfo=dt.timezone.utc)
        return max(0,
            (dt.datetime.now(dt.timezone.utc) - seen).total_seconds() / 60)
    except Exception:
        return 60.0


def _ingest_articles(articles: List[Dict[str, Any]]) -> int:
    """기사 리스트 → 자산 매핑 + 캐시 추가. 새로 추가된 개수 반환."""
    added = 0
    for a in articles:
        title = (a.get("title") or "").strip()
        if not title or len(title) < 12:
            continue
        h = _title_hash(title)
        if h in _seen_hashes:
            continue
        matched, assets = detect_assets(title, a.get("body", ""))
        if not assets:
            continue
        _seen_hashes.add(h)
        age_min = _age_minutes(a.get("seendate") or a.get("ts") or "")
        prio = priority_score(matched, age_min,
                              has_high_impact=is_high_impact(title))
        record = {
            "title":   title,
            "url":     a.get("url") or a.get("link") or "",
            "domain":  a.get("domain") or a.get("src") or "",
            "ts":      a.get("seendate") or a.get("pub") or "",
            "age_min": round(age_min, 1),
            "priority": round(prio, 2),
            "matched_keywords": matched[:4],
            "high_impact": is_high_impact(title),
        }
        for asset in assets:
            if asset not in _alerts:
                _alerts[asset] = []
            _alerts[asset].append(record)
        # high-impact만 영구 저장 (auth.db) — 일반은 12h 메모리만
        if record.get("high_impact"):
            try:
                from .history import save_alert
                save_alert(record, assets)
            except Exception:
                pass
        added += 1
    return added


def _prune_and_sort() -> None:
    """오래된 alert 제거 + priority 정렬 + 자산별 상위 N개만 유지."""
    cutoff_min = _MAX_AGE_HOURS * 60
    for asset, items in _alerts.items():
        fresh = [it for it in items if it["age_min"] <= cutoff_min]
        fresh.sort(key=lambda x: -x["priority"])
        _alerts[asset] = fresh[:_MAX_PER_ASSET]


def refresh_once() -> Dict[str, Any]:
    """한 번 폴링. 모든 소스 시도 + 결과 통합 + 캐시 갱신."""
    global _last_refresh, _refresh_count
    sources_used = []
    total_added = 0
    raw_count = 0
    with _LOCK:
        # 1) GDELT (실패해도 무시)
        try:
            gd = fetch_gdelt(timespan="2h", maxrecords=80, timeout=8)
            if gd:
                sources_used.append(f"gdelt({len(gd)})")
                raw_count += len(gd)
                total_added += _ingest_articles(gd)
        except Exception:
            pass

        # 2) 국가별 RSS — 이 앱이 이미 가진 인프라
        try:
            from ..data.news_feeds import fetch_country_news
            for country in ["US", "KR", "JP", "CN", "EU"]:
                r = fetch_country_news(country,
                                       limit_per_source=5,
                                       total_limit=15)
                items = r.get("items", [])
                if items:
                    sources_used.append(f"{country}({len(items)})")
                    raw_count += len(items)
                    # RSS items는 seendate 없음 — pub 사용 시도, 없으면
                    # 30분 전으로 추정
                    enriched = []
                    for it in items:
                        enriched.append({
                            "title": it.get("title", ""),
                            "url":   it.get("link", ""),
                            "domain": it.get("src", ""),
                            "seendate": it.get("pub") or "",
                            "country": country,
                        })
                    total_added += _ingest_articles(enriched)
        except Exception:
            pass

        _prune_and_sort()
        _last_refresh = dt.datetime.now(dt.timezone.utc)
        _refresh_count += 1

    return {
        "sources": sources_used,
        "raw_count": raw_count,
        "added": total_added,
        "ts": _last_refresh.isoformat() if _last_refresh else "",
    }


def get_alert_summary() -> Dict[str, Any]:
    """상단 스트립 배지용 — 자산별 alert 개수 + 최고 priority."""
    out: Dict[str, Any] = {}
    with _LOCK:
        for asset, items in _alerts.items():
            if not items:
                out[asset] = {"count": 0, "top_priority": 0,
                              "has_high_impact": False}
                continue
            top = items[0]
            out[asset] = {
                "count": len(items),
                "top_priority": top["priority"],
                "has_high_impact": any(it["high_impact"] for it in items),
            }
    return {
        "alerts": out,
        "last_refresh": (_last_refresh.isoformat()
                         if _last_refresh else ""),
        "refresh_count": _refresh_count,
    }


def get_asset_alerts(asset: str, limit: int = 15) -> Dict[str, Any]:
    """특정 자산의 alert 리스트 (drawer 표시용)."""
    with _LOCK:
        items = list(_alerts.get(asset, [])[:limit])
    return {
        "asset": asset,
        "label": ASSET_LABELS.get(asset, asset),
        "count": len(items),
        "items": items,
    }


def get_all_alerts(limit: int = 60,
                   only_high_impact: bool = False) -> Dict[str, Any]:
    """
    자산 무관 — 시간순(최신) 전체 알림. 동일 제목 중복 제거.
    각 record에 affected_assets 라벨 첨부.

    Returns
    -------
    {
        items: [{title, url, domain, ts, age_min, priority,
                 matched_keywords, high_impact, assets: [...]}],
        count: int,
        last_refresh: ISO,
    }
    """
    seen_titles = set()
    merged: List[Dict[str, Any]] = []
    with _LOCK:
        # 자산별 alert을 다 모은 뒤 제목 prefix로 중복 제거
        for asset_sym, items in _alerts.items():
            for it in items:
                title_key = (it.get("title") or "")[:60]
                if not title_key:
                    continue
                if title_key in seen_titles:
                    # 이미 추가된 알림 → assets 라벨에 추가
                    for m in merged:
                        if m["_title_key"] == title_key:
                            if asset_sym not in m["assets"]:
                                m["assets"].append(asset_sym)
                            break
                    continue
                seen_titles.add(title_key)
                rec = dict(it)
                rec["assets"] = [asset_sym]
                rec["_title_key"] = title_key
                merged.append(rec)
        last = _last_refresh.isoformat() if _last_refresh else ""

    if only_high_impact:
        merged = [m for m in merged if m.get("high_impact")]

    # high_impact 우선 + age 오름차순 (최신)
    merged.sort(key=lambda x: (0 if x.get("high_impact") else 1,
                                x.get("age_min", 9999)))
    # 내부 키 제거
    for m in merged:
        m.pop("_title_key", None)
    return {
        "items": merged[:limit],
        "count": len(merged),
        "last_refresh": last,
    }


# ── 백그라운드 자동 폴링 ─────────────────────────────────────────
_POLL_INTERVAL = 300  # 5분
_poll_thread: Optional[threading.Thread] = None
_poll_running = False


def _poll_loop() -> None:
    global _poll_running
    _poll_running = True
    # 시작 시 한 번 즉시 실행
    try:
        refresh_once()
    except Exception:
        pass
    while _poll_running:
        time.sleep(_POLL_INTERVAL)
        try:
            refresh_once()
        except Exception:
            pass


def start_polling() -> bool:
    """백그라운드 폴링 시작 (idempotent)."""
    global _poll_thread, _poll_running
    if _poll_thread and _poll_thread.is_alive():
        return False
    _poll_thread = threading.Thread(target=_poll_loop, daemon=True,
                                    name="iaw-awareness")
    _poll_thread.start()
    return True


def stop_polling() -> None:
    global _poll_running
    _poll_running = False
