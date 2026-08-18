"""
GDELT 2.0 Doc API 폴링
========================
무료, 키 없음. 글로벌 실시간 뉴스 피드.

API: https://api.gdeltproject.org/api/v2/doc/doc

파라미터:
  query      : 검색 쿼리 (lucene 문법)
  mode       : ArtList (기본)
  format     : json
  timespan   : 1h / 4h / 24h
  maxrecords : 최대 250

응답 article: {url, url_mobile, title, seendate, socialimage, domain,
              language, sourcecountry}
"""
from __future__ import annotations

import datetime as dt
from typing import Any, Dict, List, Optional

import requests

_API = "https://api.gdeltproject.org/api/v2/doc/doc"

# JIQT 관심사 폭넓게 — 매크로 + 빅테크 + 한국 + 지정학
_DEFAULT_QUERY = (
    '(Fed OR FOMC OR "rate hike" OR "rate cut" OR CPI OR inflation '
    'OR GDP OR Nvidia OR TSMC OR Samsung OR KOSPI OR OPEC '
    'OR oil OR Bitcoin OR Ukraine OR Taiwan OR China OR tariff '
    'OR Apple OR Microsoft) sourcelang:eng'
)


def fetch_recent(query: str = _DEFAULT_QUERY,
                 timespan: str = "1h",
                 maxrecords: int = 75,
                 timeout: int = 10) -> List[Dict[str, Any]]:
    """
    GDELT에서 최근 뉴스 fetch. 실패 시 빈 리스트.

    Returns
    -------
    [{title, url, domain, seendate(ISO), country, language}, ...]
    """
    try:
        r = requests.get(_API, params={
            "query": query,
            "mode": "ArtList",
            "format": "json",
            "timespan": timespan,
            "maxrecords": min(250, max(10, maxrecords)),
            "sort": "DateDesc",
        }, timeout=timeout,
            headers={"User-Agent": "JIQT/1.0 (research)"})
        if r.status_code != 200:
            return []
        j = r.json() or {}
        arts = j.get("articles") or []
        out = []
        for a in arts:
            title = (a.get("title") or "").strip()
            if not title:
                continue
            out.append({
                "title":     title,
                "url":       a.get("url") or "",
                "domain":    a.get("domain") or "",
                "seendate":  _parse_seen(a.get("seendate") or ""),
                "country":   a.get("sourcecountry") or "",
                "language":  a.get("language") or "",
            })
        return out
    except Exception:
        return []


def _parse_seen(s: str) -> str:
    """GDELT seendate '20260521T143000Z' → ISO 8601."""
    if not s or len(s) < 15:
        return ""
    try:
        d = dt.datetime.strptime(s[:15], "%Y%m%dT%H%M%S")
        return d.replace(tzinfo=dt.timezone.utc).isoformat()
    except Exception:
        return s
