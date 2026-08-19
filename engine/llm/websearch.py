"""
무료 웹 검색 어댑터
=====================
LLM 컨텍스트 보강용. 우선순위:
  1) Brave Search API (키 있으면) — 무료 2000회/월, 품질 우수
  2) DuckDuckGo HTML scrape — 키 불필요, rate limit 약함

각 결과는 {title, url, snippet, source} dict로 정규화.
검색 후 본문은 abstract만 사용(전체 페이지 fetch는 호출자가 결정).
"""
from __future__ import annotations

import html as _html
import re
from typing import Any, Dict, List, Optional
from urllib.parse import quote_plus

import requests

from ..data.keyconfig import get_key


_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
       "AppleWebKit/537.36 Chrome/120 Safari/537.36")


def _clean(s: str) -> str:
    s = _html.unescape(s or "")
    s = re.sub(r"<[^>]+>", "", s)
    return re.sub(r"\s+", " ", s).strip()


# ── Brave Search (선택) ──────────────────────────────────────────
def _search_brave(query: str, limit: int = 6) -> List[Dict[str, str]]:
    key = get_key("brave")
    if not key:
        return []
    try:
        r = requests.get(
            "https://api.search.brave.com/res/v1/web/search",
            params={"q": query, "count": limit, "freshness": "pw"},
            headers={"X-Subscription-Token": key,
                     "Accept": "application/json",
                     "User-Agent": "Plutus/2.0"},
            timeout=8,
        )
        if r.status_code != 200:
            return []
        j = r.json() or {}
        out = []
        for it in (j.get("web", {}).get("results") or [])[:limit]:
            out.append({
                "title": _clean(it.get("title", "")),
                "url":   it.get("url", ""),
                "snippet": _clean(it.get("description", "")),
                "source": "brave",
                "date": it.get("page_age", ""),
            })
        return out
    except Exception:
        return []


# ── DuckDuckGo HTML scrape (무료/무키) ────────────────────────────
_DDG_RESULT_RE = re.compile(
    r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>'
    r'.*?<a[^>]+class="result__snippet"[^>]*>(.*?)</a>',
    re.DOTALL | re.IGNORECASE,
)


def _search_duckduckgo(query: str, limit: int = 6) -> List[Dict[str, str]]:
    try:
        r = requests.post(
            "https://html.duckduckgo.com/html/",
            data={"q": query},
            headers={"User-Agent": _UA,
                     "Accept-Language": "en-US,en;q=0.9"},
            timeout=8,
        )
        if r.status_code != 200:
            return []
        out = []
        for m in _DDG_RESULT_RE.finditer(r.text):
            url = m.group(1)
            # DDG 결과 URL은 redirect 형식: /l/?uddg=...
            if url.startswith("/l/") or url.startswith("//"):
                rm = re.search(r"uddg=([^&]+)", url)
                if rm:
                    from urllib.parse import unquote
                    url = unquote(rm.group(1))
            title = _clean(m.group(2))
            snippet = _clean(m.group(3))
            if title and url:
                out.append({"title": title, "url": url,
                            "snippet": snippet, "source": "duckduckgo",
                            "date": ""})
            if len(out) >= limit:
                break
        return out
    except Exception:
        return []


# ── 통합 검색 ─────────────────────────────────────────────────────
def web_search(query: str, limit: int = 6) -> List[Dict[str, str]]:
    """우선순위로 검색 — Brave가 키 있으면 우선, 아니면 DDG."""
    results = _search_brave(query, limit)
    if results:
        return results
    return _search_duckduckgo(query, limit)


def search_with_provenance(query: str,
                           limit: int = 6) -> Dict[str, Any]:
    """결과 + 어느 provider 썼는지."""
    brave_key = bool(get_key("brave"))
    results = web_search(query, limit)
    provider = ("brave" if brave_key and results
                else "duckduckgo" if results else "none")
    return {
        "query": query,
        "provider": provider,
        "count": len(results),
        "results": results,
    }
