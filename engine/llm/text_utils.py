"""
LLM 출력 후처리 — 한국어 강제
================================
DeepSeek-R1 등 reasoning 모델은 한국어 강제해도 영어 섞이는 경우 많음.
영어 비율이 임계 이상이면 DeepL로 한국어 번역.
"""
from __future__ import annotations

import re
from typing import Optional


# 영어 단어 휴리스틱: 라틴 알파벳 3자 이상 연속
_LATIN_WORD = re.compile(r"[A-Za-z]{3,}")
# 한국어 한 글자
_KOREAN_CHAR = re.compile(r"[가-힣]")
# 영어 문장(주로 마침표 포함, 한글 거의 없음)
_MOSTLY_ENGLISH_THRESHOLD = 0.4  # 라틴 글자 비율 40% 이상이면 영어로 간주


def english_ratio(text: str) -> float:
    """텍스트에서 라틴 글자가 차지하는 비율 (0~1)."""
    if not text:
        return 0.0
    total = sum(1 for ch in text if ch.isalpha() or "가" <= ch <= "힣")
    if total == 0:
        return 0.0
    latin = sum(1 for ch in text if ("a" <= ch.lower() <= "z"))
    return latin / total


def has_significant_english(text: str,
                            threshold: float = _MOSTLY_ENGLISH_THRESHOLD
                            ) -> bool:
    """번역할 가치가 있을 만큼 영어 비율 높은지."""
    if not text or len(text) < 15:
        return False
    return english_ratio(text) >= threshold


def polish_korean(text: str) -> str:
    """
    텍스트가 영어 비율 높으면 DeepL로 번역, 아니면 원본 그대로.

    DeepL 키 없거나 번역 실패 시 원본 반환 (실패 비차단).
    """
    if not text:
        return text
    # 짧은 fragment, ticker만 있는 것 등은 그대로
    if len(text) < 15:
        return text
    if not has_significant_english(text):
        return text
    # DeepL 시도
    try:
        from ..data.news_summary import _translate_deepl
        translated = _translate_deepl(text, target_lang="KO")
        if translated and len(translated) >= len(text) * 0.3:
            return translated
    except Exception:
        pass
    return text  # 폴백: 원본
