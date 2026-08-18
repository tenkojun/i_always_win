"""
engine.microstructure — 마이크로구조 분석 (틱 데이터 기반)
============================================================
KIS WebSocket으로 받은 실시간 체결/호가를 분석:

- Speed of Tape       — 체결 속도 (초당 거래 수/볼륨)
- Order Book Imbalance — 매수/매도 잔량 비율
- Sweep Detection     — 큰 단일 주문이 여러 호가 동시 흡수
- Trade Size Cluster  — 체결 크기 분포로 기관/개인 추정
- Real CVD            — buy_size - sell_size 누적 (가짜 CVD와 다름)

데이터 소스:
- 실시간: engine.data.sources.kis_websocket의 buffer
- 폴백: 데이터 없으면 분석 불가 (caveat 메시지)
"""
from .speed_of_tape  import speed_of_tape, tape_acceleration
from .book_imbalance import book_imbalance, pressure_score
from .sweep_detect   import detect_sweeps
from .real_cvd       import real_cvd, cvd_divergence
from .trade_cluster  import trade_size_distribution

__all__ = [
    "speed_of_tape", "tape_acceleration",
    "book_imbalance", "pressure_score",
    "detect_sweeps",
    "real_cvd", "cvd_divergence",
    "trade_size_distribution",
]
