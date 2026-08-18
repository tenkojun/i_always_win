"""
종목별 LLM 분석 컨텍스트 빌더
=================================
LLM에게 단순 헤드라인만 주면 일반론밖에 못 함.
이 모듈은 다음을 모아 분석가 수준 컨텍스트로 묶는다:

  1) 종목 추출(헤드라인 + 본문에서 ticker 식별)
  2) 펀더멘털 (PE/PB/ROE/margin/debt/등 — 이미 다중소스 병합됨)
  3) 최근 뉴스 (Finnhub 14일)
  4) 섹터별 핵심 KPI 가이드 (분석가가 실적에서 무엇을 보는가)
  5) 웹 검색 결과 (Brave 키 있을 때만 — 보조)

빌드 결과는 LLM 프롬프트에 그대로 삽입 가능한 markdown 텍스트.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional


# ── 섹터별 분석가 체크리스트 ─────────────────────────────────────
# 분석가가 실적/뉴스에서 실제로 주목하는 KPI/포인트
_SECTOR_PLAYBOOK = {
    "TECHNOLOGY": [
        "매출 성장률 YoY (특히 클라우드/AI/데이터센터 등 세그먼트별)",
        "Gross/Operating Margin 추이 (가격결정력 척도)",
        "R&D 지출 비중 + 효율성",
        "FCF 마진 + 자사주 매입 규모",
        "가이던스 (다음 분기/연간) vs 컨센서스",
        "주요 제품 출하량 (AI GPU, iPhone, AWS instance 등)",
    ],
    "SEMICONDUCTORS": [
        "데이터센터/AI 매출 비중 (지난 분기 대비 가속/감속)",
        "Gross Margin (capacity utilization 척도)",
        "CapEx 가이던스 (공급 능력 확장 시그널)",
        "재고 수준 (cycle 단계 시그널)",
        "Hyperscaler 주문 동향 (MSFT/META/GOOG/AMZN)",
    ],
    "FINANCIAL SERVICES": [
        "Net Interest Margin (NIM) — 금리 환경 민감도",
        "대출 잔액 증가 + Non-Performing Loan 비율",
        "Tier 1 Capital Ratio (자본 적정성)",
        "수수료 수익 vs 이자 수익 비중",
        "충당금(Provision) 변화 — 경기 신호",
    ],
    "HEALTHCARE": [
        "파이프라인 단계 (Phase 1/2/3/승인)",
        "주력 제품 매출 + 특허 만료 일정",
        "FDA 승인/거절 이벤트",
        "R&D 효율 (출시당 비용)",
        "보험사 가격 협상 결과",
    ],
    "CONSUMER CYCLICAL": [
        "동일점포 매출 (Same-Store Sales) 성장률",
        "Gross Margin (할인/프로모션 부담)",
        "재고 수준 (소비 둔화 신호)",
        "신규 매장/지역 확장 페이스",
        "고객 트래픽 vs 객단가",
    ],
    "ENERGY": [
        "WTI/Brent 평균 실현가 vs 헤지 비중",
        "Production 가이던스 (boe/d)",
        "CapEx Discipline (FCF 우선 vs 성장)",
        "지정학적 노출 (러시아/중동/베네수엘라)",
        "탄소 전환 투자 비중",
    ],
}

_DEFAULT_PLAYBOOK = [
    "매출/이익 성장률 YoY",
    "마진 추이",
    "가이던스 vs 컨센서스",
    "시장 점유율 변화",
    "재무건전성 (부채/현금흐름)",
]

# 종목명 → 산업/특화 KPI (대표 종목 일부)
_KNOWN_TICKERS_KPI = {
    "NVDA": ["데이터센터 매출 (Data Center segment)",
             "Gaming 매출 (사이클 회복 여부)",
             "AI GPU 공급 능력 (H100/B100/Blackwell)",
             "Gross Margin (75% 이상 유지 여부 — pricing power 척도)",
             "다음 분기 가이던스 (특히 Data Center 가속/감속)",
             "Hyperscaler CapEx 동향 (수요 선행지표)"],
    "AAPL": ["iPhone 매출 + ASP",
             "Services 매출 성장률 (구독 모델 안정성)",
             "중국 매출 (지정학 리스크)",
             "Gross Margin (특히 Services GM 70%+)",
             "Buyback 규모 + 현금 보유"],
    "MSFT": ["Azure 매출 성장률 (vs AWS/GCP 점유율)",
             "Office 365 ARPU + 사용자 증가",
             "AI Copilot 매출 기여도",
             "Gaming (Activision 통합)",
             "Operating Margin 추이"],
    "GOOGL": ["광고 매출 (Search + YouTube)",
              "Google Cloud 마진 (흑자전환 여부)",
              "AI Capex 효율 vs 매출 기여",
              "Other Bets 손실 규모"],
    "META": ["Reality Labs 손실 추이",
             "광고 단가(ARPU) + DAU",
             "Reels 수익화 가속",
             "AI 인프라 CapEx 부담"],
    "TSLA": ["차량 인도량 + ASP",
             "Auto Gross Margin (price cut 영향)",
             "Energy/Storage 매출 성장",
             "FSD/Robotaxi 진척",
             "중국 시장 점유율"],
    "AMZN": ["AWS 매출 + Operating Margin",
             "North America Retail OPM",
             "광고 매출 성장률",
             "물류 비용 효율"],
}


def extract_tickers(text: str, hint: str = "") -> List[str]:
    """제목 + 본문에서 ticker 후보 추출.

    - 영문 대문자 1-5자 토큰
    - 단순 단어("THE", "A" 등) 제외
    - hint(서버가 알고 있는 현재 종목)는 우선 포함
    """
    if not text:
        text = ""
    tokens = set(re.findall(r"\b[A-Z]{2,5}\b", text))
    # 흔한 단어 제외
    drop = {"THE", "AND", "FOR", "BUT", "NOT", "YOU", "ALL", "CEO",
            "CFO", "COO", "USA", "EU", "AI", "IT", "OK", "UP", "PR",
            "QA", "QE", "QT", "GDP", "CPI", "PPI", "FED", "ETF",
            "API", "URL", "Q1", "Q2", "Q3", "Q4", "FY", "FQ"}
    out = [t for t in tokens if t not in drop]
    if hint:
        h = hint.strip().upper()
        if h and h not in out:
            out.insert(0, h)
    return out[:5]


def kpi_for_ticker(ticker: str, sector: str = "") -> List[str]:
    """종목 또는 섹터별 분석가 KPI 체크리스트."""
    t = (ticker or "").upper()
    if t in _KNOWN_TICKERS_KPI:
        return _KNOWN_TICKERS_KPI[t]
    sec = (sector or "").upper().strip()
    return _SECTOR_PLAYBOOK.get(sec, _DEFAULT_PLAYBOOK)


def _fundamentals_block(ticker: str) -> str:
    """다중소스 펀더멘털 → markdown 블록. 실패 시 빈 문자열."""
    try:
        from ..data.sources import fetch_fundamentals_best
        f = fetch_fundamentals_best(ticker)
        if not f or not any(k for k in f if not k.startswith("_")):
            return ""
        fields = [
            ("PE",            f.get("pe")),
            ("PB",            f.get("pb")),
            ("ROE",           f.get("roe")),
            ("Beta",          f.get("beta")),
            ("Profit Margin", f.get("profit_margin")),
            ("Debt/Equity",   f.get("debt_equity")),
            ("Current Ratio", f.get("current_ratio")),
            ("Dividend Yield", f.get("dividend_yield")),
            ("Interest Cov",  f.get("interest_coverage")),
            ("Sector",        f.get("sector")),
            ("Market Cap",    f.get("market_cap")),
            ("52w High",      f.get("52w_high")),
            ("52w Low",       f.get("52w_low")),
        ]
        lines = []
        for label, val in fields:
            if val is None or val == "":
                continue
            if isinstance(val, float):
                v = (f"{val:.4f}" if abs(val) < 1
                     else f"{val:,.2f}")
            else:
                v = str(val)
            lines.append(f"  - {label}: {v}")
        if not lines:
            return ""
        return ("### Fundamentals (multi-source merged)\n"
                + "\n".join(lines))
    except Exception:
        return ""


def _recent_news_block(ticker: str, limit: int = 5) -> str:
    """Finnhub 14일 뉴스 → markdown 블록."""
    try:
        from ..data.sources import fetch_news_best
        items = fetch_news_best(ticker, limit=limit) or []
        if not items:
            return ""
        lines = ["### Recent news (last 14 days)"]
        for i, n in enumerate(items[:limit], 1):
            title = (n.get("title") or "").strip()
            if title:
                lines.append(f"  {i}. {title[:140]}")
        if len(lines) < 2:
            return ""
        return "\n".join(lines)
    except Exception:
        return ""


def _websearch_block(query: str, limit: int = 4) -> str:
    """Brave Search 결과 → markdown 블록. 키 없거나 0개면 빈 문자열."""
    try:
        from .websearch import web_search
        results = web_search(query, limit=limit)
        if not results:
            return ""
        lines = [f"### Web search: \"{query}\""]
        for r in results[:limit]:
            snip = (r.get("snippet") or "")[:200]
            lines.append(f"  - {r.get('title','')[:100]}")
            if snip:
                lines.append(f"    {snip}")
        return "\n".join(lines)
    except Exception:
        return ""


def build_context(title: str, body: str = "",
                  ticker_hint: str = "") -> Dict[str, Any]:
    """
    뉴스 한 건에 대한 분석 컨텍스트를 빌드.

    Returns
    -------
    {
        "tickers": ["NVDA", ...],
        "primary_ticker": "NVDA",
        "sector": "Technology" or "",
        "kpis": ["데이터센터 매출", ...],
        "fundamentals_md": "...",
        "news_md": "...",
        "websearch_md": "...",
        "full_context_md": "전체 markdown 블록 (LLM 프롬프트용)",
    }
    """
    tickers = extract_tickers(f"{title}\n{body}", hint=ticker_hint)
    primary = tickers[0] if tickers else ""
    sector = ""
    # 섹터는 펀더 fetch 결과에서 가져오므로 펀더먼저 빌드
    fund_md = _fundamentals_block(primary) if primary else ""
    # sector 추출
    m = re.search(r"Sector:\s*(\S[^\n]*)", fund_md)
    if m:
        sector = m.group(1).strip()
    kpis = kpi_for_ticker(primary, sector) if primary else []
    news_md = _recent_news_block(primary) if primary else ""
    ws_md = ""
    if primary:
        ws_md = _websearch_block(
            f"{primary} stock earnings analyst expectations", limit=4)
    # 전체 컨텍스트 조립
    blocks = []
    if primary:
        blocks.append(f"### Primary Ticker: {primary}"
                      + (f"  (sector: {sector})" if sector else ""))
    if kpis:
        kpi_md = ("### Analyst KPI checklist (what professional analysts "
                  f"watch for {primary or 'this'})\n"
                  + "\n".join(f"  - {k}" for k in kpis))
        blocks.append(kpi_md)
    if fund_md:
        blocks.append(fund_md)
    if news_md:
        blocks.append(news_md)
    if ws_md:
        blocks.append(ws_md)
    return {
        "tickers":        tickers,
        "primary_ticker": primary,
        "sector":         sector,
        "kpis":           kpis,
        "fundamentals_md": fund_md,
        "news_md":        news_md,
        "websearch_md":   ws_md,
        "full_context_md": "\n\n".join(blocks),
    }
