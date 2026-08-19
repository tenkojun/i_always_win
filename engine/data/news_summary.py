"""
뉴스 심층 분석  —  제목 번역 + 기사 본문 fetch + 핵심 요약 + 시장 시그널
==========================================================================
우선순위:
  1) 제목 → DeepL 번역 (항상 시도)
  2) 기사 URL 본문 fetch → 핵심 문장 추출 → DeepL 번역
  3) 본문 fetch 실패 시 → 제목 기반 키워드 분석으로 상세 내러티브 생성
  4) DeepL 없으면 → 키워드 기반 한글 요약만

반환 dict
---------
title_kr         : DeepL 번역 제목 (없으면 None)
signal           : STRONG_BUY / BUY / HOLD / SELL / STRONG_SELL
signal_kr        : 강한 매수 / 매수 검토 / 관망 / 매도 검토 / 강한 매도
signal_color     : up / amber / down
signal_rationale : 시그널 근거 + 해설 (한글, 2~4문장)
context_tags     : [실적, 애널리스트, 인수합병, 규제·법률, 매크로, 경영진]
deepl_summary    : 핵심 내용 한글 번역 (없으면 None)
deepl_ok         : bool
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional, Any

from .keyconfig import get_key
from .sentiment import analyze_sentiment, _tokenize


# ── 키워드 한국어 해설 사전 ─────────────────────────────────────
_KW_EXPLAIN = {
    "beat":         "실적이 시장 예상치를 상회(어닝 서프라이즈)",
    "beats":        "실적이 시장 예상치를 상회(어닝 서프라이즈)",
    "miss":         "실적이 시장 예상치를 하회(어닝 쇼크)",
    "misses":       "실적이 시장 예상치를 하회(어닝 쇼크)",
    "record":       "사상 최대 실적 또는 신고가 기록",
    "surge":        "주가·실적이 급격히 상승",
    "soar":         "주가·매출이 급등",
    "plunge":       "주가·실적이 급격히 하락",
    "crash":        "시장·주가 급락",
    "upgrade":      "애널리스트가 투자의견을 상향 조정",
    "downgrade":    "애널리스트가 투자의견을 하향 조정",
    "acquisition":  "기업 인수·합병 추진 발표",
    "merger":       "기업 합병 추진 발표",
    "buyback":      "자사주 매입 계획 발표 — 주주환원 긍정적",
    "dividend":     "배당금 지급 또는 증가 발표",
    "layoff":       "인력 구조조정 발표",
    "layoffs":      "대규모 인력 감축 발표",
    "fraud":        "회계·경영 부정행위 의혹 또는 확인",
    "bankruptcy":   "파산 신청 또는 보호 신청",
    "investigation":"규제 당국의 조사 개시",
    "lawsuit":      "소송 제기 또는 법적 분쟁",
    "recall":       "제품 리콜 또는 안전 문제 발생",
    "approval":     "규제기관 또는 이사회 승인 획득",
    "guidance":     "향후 실적 전망치 제시",
    "inflation":    "물가상승(인플레이션) 관련 이슈",
    "rate":         "금리 변동 관련 이슈",
    "recession":    "경기침체 가능성 또는 우려",
    "fed":          "미국 연방준비제도(Fed) 관련 발표",
    "sanctions":    "국제 제재 조치 관련",
    "ban":          "제품·서비스 금지 조치",
    "partnership":  "전략적 파트너십·제휴 체결",
    "breakthrough": "기술·의학적 돌파구 달성",
}

_STRONG_BUY_TRIGGERS = {
    "beat","beats","record","surge","soar","breakthrough","approval",
    "approved","acquisition","buyback","dividend","partnership","win","wins",
    "upgrade","upgraded",
}
_STRONG_SELL_TRIGGERS = {
    "miss","misses","fraud","bankruptcy","default","crash","plunge",
    "downgrade","downgraded","recall","ban","sanction","sanctions",
    "investigation","lawsuit","layoff","layoffs",
}
_MACRO_TRIGGERS = {
    "fed","federal","fomc","rate","rates","inflation","recession",
    "gdp","yield","treasury","tariff","tariffs","cpi","ppi",
}
_CONTEXT_MAP = {
    "실적":    {"beat","beats","miss","misses","earnings","profit","revenue",
               "eps","guidance","outlook","quarterly","annual","sales"},
    "애널리스트":{"upgrade","upgraded","downgrade","downgraded","target",
               "price","analyst","rating","outperform","underperform"},
    "인수합병": {"acquisition","merger","acquire","acquires","deal","takeover",
               "buyout","spinoff","divest","merge"},
    "규제·법률":{"lawsuit","fraud","investigation","ban","sanction","sanctions",
               "fine","penalty","recall","regulation","regulatory","sec","doj"},
    "매크로":  _MACRO_TRIGGERS,
    "경영진":  {"ceo","cfo","coo","founder","resign","resigns","appoint",
               "appoints","retire","retires","insider"},
}


def _context_tags(title_tokens: List[str],
                  body_tokens: List[str] | None = None) -> List[str]:
    """
    제목 가중치 3x, 본문 1x. 최소 가중점수 3 이상에서만 태그 부여.
    이유: 기존엔 본문 사이드바·푸터의 무관 단어로 매크로 오탐 발생.
    제목에 한 번이라도 매칭되면 자동 통과, 본문만으로는 최소 3개 매칭 필요.
    """
    title_set = set(title_tokens)
    body_set = set(body_tokens or [])
    out = []
    for tag, kws in _CONTEXT_MAP.items():
        title_hits = len(title_set & kws)
        body_hits = len(body_set & kws)
        score = title_hits * 3 + body_hits
        if title_hits >= 1 or score >= 3:
            out.append(tag)
    return out


# ── 상세 내러티브 생성 ────────────────────────────────────────────
def _build_signal_narrative(signal: str, score: float,
                             pos_words: List[str], neg_words: List[str],
                             context_tags: List[str],
                             strong_hits: List[str]) -> str:
    """
    시그널 근거를 2~4문장으로 설명:
    1) 감지된 핵심 키워드가 무엇을 의미하는지
    2) 왜 이 시그널인지
    3) 투자자가 주의해야 할 점
    """
    parts = []

    # 1) 핵심 키워드 해설
    explained = []
    for w in strong_hits[:3]:
        ex = _KW_EXPLAIN.get(w.lower())
        if ex:
            explained.append(f"'{w}'({ex})")
    if explained:
        parts.append("핵심 키워드: " + ", ".join(explained) + ".")

    # 2) 시그널 근거
    if signal == "STRONG_BUY":
        parts.append(
            "복수의 강한 긍정 신호가 감지되었습니다. "
            "어닝 서프라이즈·승인·인수합병 등의 이벤트는 "
            "단기 주가 상승 모멘텀을 유발하는 가장 강력한 촉매입니다.")
        parts.append("다만 단기 급등 후 차익실현 매물이 출회될 수 있으므로, "
                     "추가 공시와 거래량을 함께 확인하세요.")
    elif signal == "BUY":
        parts.append(
            "전반적으로 긍정적인 내용의 뉴스입니다. "
            "시장 심리에 우호적으로 작용할 가능성이 높으나, "
            "일부 상반된 신호도 존재하므로 분할 접근을 권장합니다.")
    elif signal == "STRONG_SELL":
        parts.append(
            "복수의 강한 부정 신호가 감지되었습니다. "
            "어닝 쇼크·부정행위·파산 등의 이벤트는 "
            "단기 주가에 강한 하락 압력을 가합니다.")
        parts.append("포지션을 즉각 점검하고, "
                     "추가 공시가 나올 때까지 신규 매수를 자제하세요.")
    elif signal == "SELL":
        parts.append(
            "전반적으로 부정적인 내용의 뉴스입니다. "
            "시장 심리에 불리하게 작용할 가능성이 높으며, "
            "비중 축소 또는 관망을 검토할 시점입니다.")
    else:  # HOLD
        if "매크로" in context_tags:
            parts.append(
                "금리·Fed·인플레이션 등 매크로 이벤트 관련 뉴스입니다. "
                "매크로 뉴스는 개별 종목보다 시장 전체에 영향을 주므로, "
                "방향성이 확정될 때까지 관망이 합리적입니다.")
        else:
            parts.append(
                "뚜렷한 매수·매도 신호가 없는 중립적인 뉴스입니다. "
                "추가 데이터 확인 후 방향을 결정하세요.")

    # 3) 컨텍스트 경고
    if "규제·법률" in context_tags and signal not in ("STRONG_SELL","SELL"):
        parts.append("규제·법률 관련 내용이 포함되어 있어 "
                     "추가 조사 진행 여부를 모니터링할 필요가 있습니다.")

    return " ".join(parts)


def derive_market_signal(score: float,
                         pos_words: List[str],
                         neg_words: List[str],
                         title: str,
                         body: str = "") -> Dict[str, Any]:
    # 강력 트리거는 제목에만 — 본문은 사이드바 오염 위험.
    # 컨텍스트 태그는 제목(3x) + 본문(1x) 가중치.
    title_tokens = _tokenize(title)
    body_tokens = _tokenize(body) if body else []
    title_set = set(title_tokens)

    strong_buy_hit  = list(title_set & _STRONG_BUY_TRIGGERS)
    strong_sell_hit = list(title_set & _STRONG_SELL_TRIGGERS)
    macro_hit       = title_set & _MACRO_TRIGGERS
    ctx_tags        = _context_tags(title_tokens, body_tokens)

    if score >= 0.35 and strong_buy_hit and not strong_sell_hit:
        sig, sig_kr, color = "STRONG_BUY", "강한 매수", "up"
        hits = strong_buy_hit
    elif score >= 0.15:
        sig, sig_kr, color = "BUY", "매수 검토", "up"
        hits = strong_buy_hit or (pos_words or [])[:2]
    elif score <= -0.35 and strong_sell_hit and not strong_buy_hit:
        sig, sig_kr, color = "STRONG_SELL", "강한 매도", "down"
        hits = strong_sell_hit
    elif score <= -0.15:
        sig, sig_kr, color = "SELL", "매도 검토", "down"
        hits = strong_sell_hit or (neg_words or [])[:2]
    else:
        sig, sig_kr, color = "HOLD", "관망", "amber"
        hits = list(macro_hit)[:2]

    rationale = _build_signal_narrative(sig, score, pos_words, neg_words,
                                        ctx_tags, hits)
    return {
        "signal":           sig,
        "signal_kr":        sig_kr,
        "signal_color":     color,
        "signal_rationale": rationale,
        "context_tags":     ctx_tags,
    }


# ── 기사 본문 fetch ───────────────────────────────────────────────
_SKIP_TAGS = {"script", "style", "nav", "header", "footer",
              "aside", "noscript", "button", "form", "iframe",
              "figure", "figcaption", "ul", "ol", "li"}
_BODY_CONTAINERS = {"article", "main"}


def _fetch_article_body(url: str, max_chars: int = 3000) -> str:
    """기사 본문 추출 — <article>/<main> 내 <p>만 모아 사이드바 오염 차단.

    1) <article>/<main> 발견 시 그 안의 <p>만 추출
    2) 없으면 전체 <p> 중 링크 밀도 낮은 것만 사용
    3) 짧은 단편(<60자) 제외 — 캡션·UI 라벨 거름
    """
    if not url or not url.startswith("http"):
        return ""
    try:
        import requests
        from html.parser import HTMLParser

        class _P(HTMLParser):
            def __init__(self):
                super().__init__()
                self.paragraphs: List[str] = []
                self._in_p = False
                self._cur: List[str] = []
                self._link_chars = 0
                self._total_chars = 0
                # 컨테이너 추적
                self._container_depth = 0  # article/main 깊이
                self._saw_container = False
                # skip 추적
                self._skip_depth = 0
                # 링크 추적
                self._in_a = False

            def handle_starttag(self, tag, attrs):
                if tag in _BODY_CONTAINERS:
                    self._container_depth += 1
                    self._saw_container = True
                elif tag in _SKIP_TAGS:
                    self._skip_depth += 1
                elif tag == "p" and self._skip_depth == 0:
                    self._in_p = True
                    self._cur = []
                    self._link_chars = 0
                    self._total_chars = 0
                elif tag == "a" and self._in_p:
                    self._in_a = True

            def handle_endtag(self, tag):
                if tag in _BODY_CONTAINERS:
                    if self._container_depth > 0:
                        self._container_depth -= 1
                elif tag in _SKIP_TAGS:
                    if self._skip_depth > 0:
                        self._skip_depth -= 1
                elif tag == "p" and self._in_p:
                    text = " ".join(self._cur).strip()
                    text = re.sub(r"\s+", " ", text)
                    # 사이드바·관련기사 필터:
                    #   - 60자 미만 단편 제외
                    #   - 링크 비율 40% 초과(관련기사 목록) 제외
                    if len(text) >= 60:
                        link_ratio = (self._link_chars
                                      / max(1, self._total_chars))
                        if link_ratio < 0.40:
                            # 컨테이너 외부 단락은 절반 가중치
                            in_container = self._container_depth > 0
                            if in_container or not self._saw_container:
                                self.paragraphs.append(text)
                    self._in_p = False
                    self._cur = []
                    self._in_a = False
                elif tag == "a":
                    self._in_a = False

            def handle_data(self, data):
                if self._in_p and self._skip_depth == 0:
                    self._cur.append(data)
                    n = len(data)
                    self._total_chars += n
                    if self._in_a:
                        self._link_chars += n

        headers = {
            "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                           "AppleWebKit/537.36 Chrome/120 Safari/537.36"),
            "Accept-Language": "en-US,en;q=0.9",
        }
        r = requests.get(url, timeout=10, headers=headers,
                         allow_redirects=True)
        if r.status_code != 200:
            return ""
        p = _P()
        p.feed(r.text)
        body = " ".join(p.paragraphs)
        return body[:max_chars]
    except Exception:
        return ""


def _extract_key_content(title: str, body: str,
                         max_chars: int = 1200) -> str:
    """제목 + 본문에서 투자 관련 핵심 문장 추출."""
    if not body:
        return title
    # 투자·재무 키워드 포함 문장 우선
    priority_re = re.compile(
        r"\b(revenue|profit|loss|earn|EPS|guidance|beat|miss|"
        r"upgrade|downgrade|acquisition|merger|FDA|SEC|rate|"
        r"dividend|buyback|forecast|outlook|CEO|revenue|shares|"
        r"stock|market|quarter|annual|growth|decline)\b", re.I)
    sentences = re.split(r"(?<=[.!?])\s+", body[:max_chars * 2])
    priority = [s for s in sentences if priority_re.search(s) and len(s) > 40]
    rest = [s for s in sentences if s not in priority and len(s) > 40]
    ordered = (priority[:5] + rest)[:8]
    result = " ".join(ordered)
    if len(result) > max_chars:
        result = result[:max_chars].rsplit(" ", 1)[0] + "…"
    return (title + ". " + result).strip() if result else title


# ── DeepL 번역 ───────────────────────────────────────────────────
# 인증 방식: 2025-11부터 form-body(auth_key) 폐기 → 헤더 인증 필수
#   Authorization: DeepL-Auth-Key <key>
_DEEPL_LAST_ERROR: Optional[str] = None  # 디버깅용 (서버 로그 X)


def _translate_deepl(text: str, target_lang: str = "KO") -> Optional[str]:
    """DeepL API 번역. 키 없거나 실패 시 None."""
    global _DEEPL_LAST_ERROR
    key = get_key("deepl")
    if not key or not text:
        _DEEPL_LAST_ERROR = "no_key_or_text"
        return None
    try:
        import requests
        base = ("https://api-free.deepl.com"
                if key.endswith(":fx") else "https://api.deepl.com")
        r = requests.post(
            f"{base}/v2/translate",
            headers={
                "Authorization": f"DeepL-Auth-Key {key}",
                "Content-Type": "application/json",
                "User-Agent": "Plutus/2.0",
            },
            json={"text": [text[:4800]], "target_lang": target_lang},
            timeout=15,
        )
        if r.status_code != 200:
            _DEEPL_LAST_ERROR = f"http_{r.status_code}: {r.text[:200]}"
            return None
        j = r.json()
        translations = j.get("translations") or []
        if translations:
            t = translations[0].get("text", "").strip()
            _DEEPL_LAST_ERROR = None
            return t if t else None
        _DEEPL_LAST_ERROR = "empty_translations"
    except Exception as e:
        _DEEPL_LAST_ERROR = f"exc: {type(e).__name__}: {e}"
    return None


def deepl_last_error() -> Optional[str]:
    """디버깅용: 마지막 DeepL 호출 에러 메시지."""
    return _DEEPL_LAST_ERROR


# ── 공개 함수 ─────────────────────────────────────────────────────
def analyze_news_full(title: str,
                      url: str = "",
                      body: str = "") -> Dict[str, Any]:
    """
    뉴스 전체 분석 파이프라인:
    1) keyword sentiment
    2) 제목 DeepL 번역
    3) 기사 본문 fetch
    4) 핵심 내용 추출 + DeepL 번역
    5) 시장 시그널 + 상세 내러티브
    """
    # 1) 감성 분석
    sent = analyze_sentiment(title, body)

    deepl_key = get_key("deepl")

    # 2) 제목 번역 (DeepL 있을 때)
    title_kr = None
    if deepl_key:
        title_kr = _translate_deepl(title)

    # 3) 기사 본문 fetch
    fetched_body = body
    if not fetched_body and url:
        fetched_body = _fetch_article_body(url)

    # 4) 핵심 내용 추출 + 번역
    deepl_summary = None
    deepl_ok = False
    if deepl_key:
        key_content = _extract_key_content(title, fetched_body)
        # 본문이 있으면 본문 요약 번역, 없으면 제목만
        text_to_translate = key_content if fetched_body else title
        if text_to_translate and text_to_translate != title:
            # 본문 번역 (제목 번역과 중복 방지)
            deepl_summary = _translate_deepl(text_to_translate)
        elif fetched_body:
            deepl_summary = _translate_deepl(key_content)
        deepl_ok = deepl_summary is not None

    # 5) 시장 시그널 + 내러티브
    sig_info = derive_market_signal(
        score=sent["score"],
        pos_words=sent["pos_words"],
        neg_words=sent["neg_words"],
        title=title,
        body=fetched_body,
    )

    return {
        **sent,
        **sig_info,
        "title_kr":      title_kr,
        "deepl_summary": deepl_summary,
        "deepl_ok":      deepl_ok,
        "body_fetched":  bool(fetched_body),
    }
