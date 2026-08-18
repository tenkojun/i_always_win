"""
Ollama HTTP 추론 클라이언트
============================
DeepSeek-R1 등 reasoning 모델 출력에서 <think>...</think> 영역을
분리하고, JSON 응답 모드를 지원한다.

사용 예
-------
    from engine.llm.client import generate, generate_json
    txt = generate("Hello")
    obj = generate_json("Reply JSON: {\"a\":1}")
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

import requests

from .ollama_setup import OLLAMA_HOST

_DEFAULT_MODEL = "deepseek-r1:7b"
_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)


class LLMError(Exception):
    pass


def _strip_think(text: str) -> str:
    """DeepSeek-R1 등의 <think>...</think> reasoning 영역 제거."""
    return _THINK_RE.sub("", text or "").strip()


def _extract_json(text: str) -> Optional[Dict[str, Any]]:
    """LLM 출력에서 첫 번째 JSON 객체 추출 (markdown fence 허용)."""
    if not text:
        return None
    # ```json ... ``` 펜스 제거
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```",
                      text, re.DOTALL | re.IGNORECASE)
    if fence:
        try:
            return json.loads(fence.group(1))
        except json.JSONDecodeError:
            pass
    # 균형 잡힌 첫 {...} 블록 추출
    depth = 0
    start = -1
    for i, ch in enumerate(text):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start >= 0:
                try:
                    return json.loads(text[start:i + 1])
                except json.JSONDecodeError:
                    start = -1
    return None


def generate(prompt: str,
             model: str = _DEFAULT_MODEL,
             system: Optional[str] = None,
             temperature: float = 0.0,
             max_tokens: int = 1024,
             timeout: int = 180,
             strip_think: bool = True) -> str:
    """단발 텍스트 생성. <think> 자동 제거(기본)."""
    payload: Dict[str, Any] = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        # keep_alive=-1 (integer) = 모델을 VRAM에 영구 상주.
        # string "-1"은 Ollama가 duration parse 실패 → "missing unit" 에러.
        # duration string("5m","1h")이거나 integer(-1/0/양수초)여야 함.
        "keep_alive": -1,
        "options": {
            "temperature": temperature,
            "num_predict": max_tokens,
        },
    }
    if system:
        payload["system"] = system
    try:
        r = requests.post(f"{OLLAMA_HOST}/api/generate",
                          json=payload, timeout=timeout)
        if r.status_code != 200:
            raise LLMError(f"HTTP {r.status_code}: {r.text[:200]}")
        j = r.json()
        out = j.get("response", "")
        return _strip_think(out) if strip_think else out
    except requests.RequestException as e:
        raise LLMError(f"요청 실패: {e}") from e


def generate_json(prompt: str,
                  model: str = _DEFAULT_MODEL,
                  system: Optional[str] = None,
                  temperature: float = 0.0,
                  max_tokens: int = 2048,
                  timeout: int = 240) -> Optional[Dict[str, Any]]:
    """
    JSON 객체 응답 강제. 모델 출력에서 첫 JSON 블록을 파싱.

    Ollama의 format='json' 옵션은 R1 reasoning 모델과 일부 호환 이슈가
    있어, 프롬프트에 JSON 강제 + 후처리 추출을 사용한다.
    """
    text = generate(prompt, model=model, system=system,
                    temperature=temperature, max_tokens=max_tokens,
                    timeout=timeout, strip_think=True)
    obj = _extract_json(text)
    if obj is None:
        # JSON 추출 실패해도 raw text는 반환 (디버깅용)
        raise LLMError(f"JSON 파싱 실패. raw: {text[:300]}")
    return obj


def health_check(model: str = _DEFAULT_MODEL) -> Dict[str, Any]:
    """모델 가용 여부 + 간단한 추론 1회로 응답 시간 측정."""
    import time
    from .ollama_setup import is_ollama_running, is_model_installed
    out: Dict[str, Any] = {
        "running": is_ollama_running(),
        "model": model,
        "model_installed": False,
        "latency_ms": None,
        "tokens_per_sec": None,
        "error": None,
    }
    if not out["running"]:
        out["error"] = "Ollama 서비스 정지"
        return out
    out["model_installed"] = is_model_installed(model)
    if not out["model_installed"]:
        out["error"] = f"모델 {model} 미설치"
        return out
    try:
        t0 = time.time()
        r = requests.post(f"{OLLAMA_HOST}/api/generate",
                          json={"model": model, "prompt": "Say OK.",
                                "stream": False,
                                "options": {"temperature": 0.0,
                                            "num_predict": 16}},
                          timeout=60)
        if r.status_code != 200:
            out["error"] = f"HTTP {r.status_code}"
            return out
        j = r.json()
        elapsed = time.time() - t0
        out["latency_ms"] = int(elapsed * 1000)
        ec = j.get("eval_count") or 0
        ed = j.get("eval_duration") or 1
        if ec and ed:
            out["tokens_per_sec"] = round(ec / (ed / 1e9), 1)
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {e}"
    return out
