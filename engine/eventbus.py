"""
EventBus — 서버 내부 pub/sub (C12 SSE용)
=========================================
어디서든 `publish(event_type, data)` 호출하면 구독 중인 모든
SSE 클라이언트로 즉시 push.

스레드 안전. 클라이언트별 큐 분리 (한 클라이언트 끊겨도 다른 영향 X).
"""
from __future__ import annotations

import queue
import threading
import time
from typing import Any, Dict, List


class EventBus:
    def __init__(self):
        self._clients: List[queue.Queue] = []
        self._lock = threading.RLock()
        self._n_published = 0

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=200)
        with self._lock:
            self._clients.append(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            if q in self._clients:
                self._clients.remove(q)

    def publish(self, event_type: str, data: Any) -> int:
        """모든 구독자에게 push. 반환: 도달 클라이언트 수."""
        msg = {"type": event_type, "data": data,
               "ts": time.time()}
        delivered = 0
        dead: List[queue.Queue] = []
        with self._lock:
            for q in list(self._clients):
                try:
                    q.put_nowait(msg)
                    delivered += 1
                except queue.Full:
                    dead.append(q)
            for q in dead:
                if q in self._clients:
                    self._clients.remove(q)
            self._n_published += 1
        return delivered

    def stats(self) -> Dict[str, int]:
        with self._lock:
            return {
                "subscribers": len(self._clients),
                "total_published": self._n_published,
            }


# 전역 싱글톤
bus = EventBus()


def publish(event_type: str, data: Any) -> int:
    return bus.publish(event_type, data)


def subscribe() -> queue.Queue:
    return bus.subscribe()


def unsubscribe(q: queue.Queue) -> None:
    bus.unsubscribe(q)


def stats() -> Dict[str, int]:
    return bus.stats()
