# -*- coding: utf-8 -*-
"""
콘솔 인코딩 안전장치
====================
한국어 윈도우의 기본 콘솔 코드페이지는 cp949 다. 여기에 이모지나
cp949 에 없는 문자를 ``print`` 하면 그 줄에서 ``UnicodeEncodeError`` 가
난다. 로그 한 줄 때문에 그 작업 전체가 죽는다는 뜻이다.

실제로 그랬다 — ``main.py`` 의 ``print(f"📥 1) 데이터 로드 …")`` 한 줄이
종목 분석 작업을 통째로 실패시켰다::

    'cp949' codec can't encode character '\\U0001f4e5' in position 0

이모지를 하나씩 지우는 건 두더지잡기다. 출력 스트림 자체를 UTF-8 로
바꾸고, 그래도 못 쓰는 문자가 있으면 예외 대신 대체 문자로 넘긴다.
로그의 정확도보다 작업이 죽지 않는 게 중요하다.
"""
from __future__ import annotations

import sys

_applied = False


def make_console_safe() -> bool:
    """
    ``sys.stdout`` / ``sys.stderr`` 를 UTF-8 + ``errors='replace'`` 로 바꾼다.
    한 번만 적용되며, 실패해도 조용히 넘어간다(여기서 죽으면 본말전도다).

    반환값: 실제로 무언가 바꿨으면 True.
    """
    global _applied
    if _applied:
        return False
    _applied = True

    changed = False
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        if stream is None:
            continue
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:          # 파일로 갈아끼운 경우 등
            continue
        try:
            if (getattr(stream, "encoding", "") or "").lower().replace("-", "") \
                    != "utf8" or getattr(stream, "errors", "") != "replace":
                reconfigure(encoding="utf-8", errors="replace")
                changed = True
        except Exception:
            pass
    return changed
