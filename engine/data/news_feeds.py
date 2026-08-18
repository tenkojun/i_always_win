"""
국가별 RSS 뉴스 피드 통합기
=============================
각 나라의 주요 경제·금융 매체 RSS를 병합 수집하고 중복 제거.

지원 국가:
  KR : 한국 — 연합뉴스(경제) / 매일경제 / 한국경제
  US : 미국 — Yahoo Finance / MarketWatch / CNBC
  JP : 일본 — Nikkei Asia (영문) / Japan Times
  CN : 중국 — SCMP Business / China Daily
  EU : 유럽 — Investing.com Europe / DW Business

설계 원칙:
- RSS 차단·다운된 소스가 있어도 다른 소스로 계속 동작
- ThreadPool 병렬 fetch로 응답 < 3초 유지
- 제목 60자 prefix로 중복 제거
- 각 항목에 source/country 라벨 부착
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional

# 국가별 피드 정의 — (label, url, encoding-hint)
COUNTRY_FEEDS: Dict[str, List[Dict[str, str]]] = {
    "KR": [
        {"label": "연합뉴스",
         "url": "https://www.yna.co.kr/rss/economy.xml"},
        {"label": "매일경제",
         "url": "https://www.mk.co.kr/rss/50200011/"},
        {"label": "한국경제",
         "url": "https://www.hankyung.com/feed/finance"},
    ],
    "US": [
        {"label": "Yahoo Finance",
         "url": "https://feeds.finance.yahoo.com/rss/2.0/headline"
                "?s=^GSPC&region=US&lang=en-US"},
        {"label": "MarketWatch",
         "url": "https://feeds.marketwatch.com/marketwatch/topstories/"},
        {"label": "CNBC",
         "url": "https://www.cnbc.com/id/100003114/device/rss/rss.html"},
    ],
    "JP": [
        {"label": "Nikkei Asia",
         "url": "https://asia.nikkei.com/rss/feed/nar"},
        {"label": "Japan Times",
         "url": "https://www.japantimes.co.jp/feed/topstories/"},
    ],
    "CN": [
        {"label": "SCMP Business",
         "url": "https://www.scmp.com/rss/92/feed"},
        {"label": "China Daily",
         "url": "https://www.chinadaily.com.cn/rss/business_rss.xml"},
    ],
    "EU": [
        {"label": "Investing EU",
         "url": "https://www.investing.com/rss/news_357.rss"},
        {"label": "DW Business",
         "url": "https://rss.dw.com/rdf/rss-en-eco"},
    ],
}

COUNTRY_LABELS = {
    "KR": "한국", "US": "미국", "JP": "일본",
    "CN": "중국", "EU": "유럽",
}


def _fetch_rss_one(feed: Dict[str, str], limit: int = 8,
                   timeout: int = 6) -> List[Dict[str, str]]:
    """단일 RSS 피드 파싱."""
    import requests
    items: List[Dict[str, str]] = []
    try:
        r = requests.get(feed["url"],
                         headers={"User-Agent": "Mozilla/5.0 JIQT/1.0"},
                         timeout=timeout)
        if r.status_code != 200:
            return items
        # RDF/RSS/Atom 모두 처리
        try:
            root = ET.fromstring(r.content)
        except ET.ParseError:
            return items
        # 네임스페이스 정리
        # <item>(RSS 2.0/RDF) 또는 <entry>(Atom)
        nodes = list(root.iter("item")) or list(root.iter(
            "{http://www.w3.org/2005/Atom}entry"))
        for it in nodes:
            title = (it.findtext("title")
                     or it.findtext("{http://www.w3.org/2005/Atom}title")
                     or "")
            link = (it.findtext("link")
                    or "")
            if not link:
                # Atom <link href="..."/>
                lk = it.find("{http://www.w3.org/2005/Atom}link")
                if lk is not None:
                    link = lk.get("href", "")
            pub = (it.findtext("pubDate")
                   or it.findtext("{http://purl.org/dc/elements/1.1/}date")
                   or it.findtext(
                       "{http://www.w3.org/2005/Atom}published") or "")
            title = (title or "").strip()
            if title:
                items.append({
                    "title": title,
                    "link": (link or "").strip(),
                    "pub": (pub or "").strip(),
                    "src": feed["label"],
                })
            if len(items) >= limit:
                break
    except Exception:
        pass
    return items


def fetch_country_news(country: str, limit_per_source: int = 8,
                       total_limit: int = 25) -> Dict[str, Any]:
    """
    한 국가의 모든 피드를 병렬 fetch + 병합 + 중복 제거.

    Returns
    -------
    {
        "country": "KR",
        "country_label": "한국",
        "items": [{title, link, pub, src}, ...],
        "sources": ["연합뉴스", "매일경제", ...],
        "live_sources": ["연합뉴스", ...],  # 실제 응답 받은 소스만
    }
    """
    country = (country or "").upper()
    feeds = COUNTRY_FEEDS.get(country, [])
    if not feeds:
        return {"country": country, "country_label": "",
                "items": [], "sources": [], "live_sources": []}

    all_items: List[Dict[str, str]] = []
    live_sources: List[str] = []

    with ThreadPoolExecutor(max_workers=len(feeds)) as ex:
        futures = {ex.submit(_fetch_rss_one, f, limit_per_source): f
                   for f in feeds}
        for fut in as_completed(futures):
            feed = futures[fut]
            try:
                items = fut.result(timeout=8)
                if items:
                    live_sources.append(feed["label"])
                    all_items.extend(items)
            except Exception:
                pass

    # 중복 제거 — 제목 60자 prefix 기준
    seen = set()
    uniq: List[Dict[str, str]] = []
    for n in all_items:
        k = n["title"][:60]
        if k in seen:
            continue
        seen.add(k)
        n["country"] = country
        uniq.append(n)
        if len(uniq) >= total_limit:
            break

    return {
        "country": country,
        "country_label": COUNTRY_LABELS.get(country, country),
        "items": uniq,
        "sources": [f["label"] for f in feeds],
        "live_sources": live_sources,
    }


def available_countries() -> List[Dict[str, str]]:
    """프론트엔드 탭용 — 국가 코드 + 라벨 리스트."""
    return [{"code": c, "label": COUNTRY_LABELS[c]}
            for c in COUNTRY_FEEDS.keys()]
