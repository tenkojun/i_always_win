"""
================================================================
  API 키 설정 관리자  (Secure Key Config)
================================================================
Plutus 다중소스 데이터 레이어의 API 키를 안전하게 관리한다.

원칙
----
- 키는 **코드·로그에 절대 박지 않는다**.
- 우선순위:  환경변수  >  로컬 설정파일(.data/keys.json)
- 키가 하나도 없어도 시스템은 완전히 동작한다(무키 폴백:
  야후 + Stooq). 키는 "있으면 품질이 오르는 보강재"다.
- 설정파일은 사용자 홈 디렉터리에만 저장되고, 프로그램
  설정창에서 사용자가 직접 입력한다(채팅·코드 경유 금지).

지원 키
-------
- FINNHUB_API_KEY      : 실시간 시세·뉴스·펀더멘털 (분당 60콜)
- ALPHAVANTAGE_API_KEY : 기술지표·펀더멘털 (분당 5콜/일 25콜)
- FMP_API_KEY          : 재무제표·SEC공시·밸류에이션 (일 250콜)
- ALPACA_API_KEY_ID / ALPACA_API_SECRET_KEY
      시장 수급 스캐너용. 무료 Basic 등급으로도 **SIP(전 거래소 통합)**
      히스토리컬 바를 받을 수 있다 — 단 `end` 가 15분 이전이어야 한다
      (그 안쪽을 조회하면 42210000 `subscription does not permit
      querying recent SIP data`). 흔히 알려진 "무료는 IEX 2.5% 뿐"은
      **실시간 스트림에만** 해당한다.
      없으면 야후(통합 거래량)로 폴백하므로 필수 아님.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Dict, Optional

# 환경변수 이름 ↔ 내부 키 이름
_ENV_MAP = {
    "finnhub": "FINNHUB_API_KEY",
    "alphavantage": "ALPHAVANTAGE_API_KEY",
    "fmp": "FMP_API_KEY",
    "deepl": "DEEPL_API_KEY",
    "brave": "BRAVE_SEARCH_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    # Alpaca 는 ID/시크릿 두 값이 한 쌍이다
    "alpaca": "ALPACA_API_KEY_ID",
    "alpaca_secret": "ALPACA_API_SECRET_KEY",
}

from engine.paths import DATA_DIR as _CONFIG_DIR, KEYS_FILE as _CONFIG_FILE


def _load_file_keys() -> Dict[str, str]:
    """로컬 설정파일에서 키를 읽는다(없으면 빈 dict)."""
    try:
        if _CONFIG_FILE.exists():
            with open(_CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            return {k: str(v) for k, v in data.items()
                    if v and str(v).strip()}
    except Exception:
        pass
    return {}


def get_key(provider: str) -> Optional[str]:
    """
    provider('finnhub'|'alphavantage'|'fmp')의 API 키를 반환.
    환경변수 우선, 없으면 로컬 설정파일. 없으면 None.
    """
    provider = provider.lower()
    env_name = _ENV_MAP.get(provider)
    if env_name:
        v = os.environ.get(env_name, "").strip()
        if v:
            return v
    file_keys = _load_file_keys()
    v = file_keys.get(provider, "").strip()
    return v or None


def set_key(provider: str, key: str) -> bool:
    """
    로컬 설정파일에 키를 저장(프로그램 설정창 전용).
    홈 디렉터리에만 기록, 권한 0600 시도.
    """
    provider = provider.lower()
    if provider not in _ENV_MAP:
        return False
    try:
        _CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        cur = _load_file_keys()
        cur[provider] = str(key).strip()
        with open(_CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cur, f, indent=2)
        try:
            os.chmod(_CONFIG_FILE, 0o600)
        except Exception:
            pass
        return True
    except Exception:
        return False


def key_status() -> Dict[str, bool]:
    """각 provider 키 보유 여부(값은 노출하지 않음 — 불리언만)."""
    return {p: (get_key(p) is not None) for p in _ENV_MAP}


def masked_status() -> Dict[str, str]:
    """UI 표시용: 키 끝 4자리만 마스킹 노출(없으면 '미설정')."""
    out: Dict[str, str] = {}
    for p in _ENV_MAP:
        k = get_key(p)
        if k and len(k) >= 4:
            out[p] = "••••" + k[-4:]
        elif k:
            out[p] = "••••"
        else:
            out[p] = "미설정"
    return out


# ── 하위 호환 래퍼 (load_keys 사용처 호환) ──────────────────────
def load_keys() -> Dict[str, str]:
    """전체 키 dict 반환 (값 노출 — 내부 사용 전용)."""
    merged: Dict[str, str] = {}
    file_keys = _load_file_keys()
    for p, env_name in _ENV_MAP.items():
        v = os.environ.get(env_name, "").strip()
        if v:
            merged[p] = v
        elif file_keys.get(p):
            merged[p] = file_keys[p]
    return merged
