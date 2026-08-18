"""
포지션 자료구조 모듈
====================
백테스트 엔진에서 현재 보유 포지션 상태를 관리한다.

속성
----
- side         : +1=롱, -1=숏, 0=관망
- size         : 보유 수량
- entry_price  : 진입가
- entry_idx    : 진입 시점 인덱스
- stop / take  : 손절가 / 익절가
- is_open      : 포지션이 열려 있는지
- unrealized() : 현재가 기준 미실현 손익
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional


@dataclass
class Position:
    side: int = 0
    size: float = 0.0
    entry_price: float = 0.0
    entry_idx: int = -1
    stop: Optional[float] = None
    take: Optional[float] = None

    @property
    def is_open(self) -> bool:
        return self.side != 0

    def unrealized(self, price: float) -> float:
        """현재가 기준 미실현 손익."""
        if not self.is_open:
            return 0.0
        return (price - self.entry_price) * self.size * self.side
