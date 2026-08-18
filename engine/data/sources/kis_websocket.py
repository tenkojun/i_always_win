"""
KIS WebSocket — 실시간 호가/체결 구독
============================================================
한국투자증권 OpenAPI WebSocket 클라이언트.
실시간 호가 (10단계) + 체결 (tick-by-tick) 구독 → 마이크로구조 분석에 공급.

구조:
- KISWebSocketClient: asyncio 기반 단일 클라이언트
- 백그라운드 thread에서 asyncio loop 실행
- 종목별 buffer (deque) — 최근 N틱 저장
- 콜백 등록: on_tick(symbol, data), on_orderbook(symbol, data)
- 키 없거나 연결 실패 시 noop 모드 (마이크로구조 모듈이 historic 데이터로 폴백)

장기 운영:
- 자동 재연결 (5초 backoff)
- 핑/퐁 keepalive
- 종목별 구독 add/remove
"""
from __future__ import annotations

import asyncio
import collections
import json
import threading
import time
from typing import Any, Callable, Deque, Dict, List, Optional

# 실시간 데이터 버퍼 크기 (종목당)
_TICK_BUFFER_SIZE = 2000
_BOOK_BUFFER_SIZE = 500


class KISWebSocketClient:
    """실시간 호가/체결 구독 — 싱글톤."""

    # 실시간 TR 코드
    TR_TICK_KR = "H0STCNT0"   # 국내주식 실시간 체결
    TR_BOOK_KR = "H0STASP0"   # 국내주식 실시간 호가
    TR_TICK_US = "HDFSCNT0"   # 해외주식 실시간 체결
    # WebSocket URL
    WS_REAL = "ws://ops.koreainvestment.com:21000"
    WS_VTS  = "ws://ops.koreainvestment.com:31000"

    def __init__(self, mode: str = "vts"):
        self.mode = mode
        self.ws_url = self.WS_VTS if mode == "vts" else self.WS_REAL
        self.approval_key: Optional[str] = None
        # 종목별 데이터 버퍼
        self.tick_buf: Dict[str, Deque[Dict[str, Any]]] = {}
        self.book_buf: Dict[str, Deque[Dict[str, Any]]] = {}
        # 구독 중인 종목 set
        self.subscribed_ticks: set = set()
        self.subscribed_books: set = set()
        # 콜백
        self.on_tick: Optional[Callable[[str, Dict[str, Any]], None]] = None
        self.on_book: Optional[Callable[[str, Dict[str, Any]], None]] = None
        # asyncio 관련
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._ws: Any = None
        self._running = False
        self._reconnect_delay = 5.0

    # ─── 구독 관리 ───────────────────────────────────────────
    def subscribe_ticks(self, ticker: str, market: str = "kr") -> None:
        """체결 구독. ticker가 6자리 숫자면 국내, 영문이면 미국."""
        ticker = ticker.strip()
        if ticker in self.subscribed_ticks:
            return
        self.subscribed_ticks.add(ticker)
        if ticker not in self.tick_buf:
            self.tick_buf[ticker] = collections.deque(maxlen=_TICK_BUFFER_SIZE)
        # 실제 ws 메시지는 _ws_loop 안에서 전송
        if self._loop and self._running:
            asyncio.run_coroutine_threadsafe(
                self._send_subscribe(ticker, "tick", market), self._loop)

    def subscribe_orderbook(self, ticker: str, market: str = "kr") -> None:
        ticker = ticker.strip()
        if ticker in self.subscribed_books:
            return
        self.subscribed_books.add(ticker)
        if ticker not in self.book_buf:
            self.book_buf[ticker] = collections.deque(maxlen=_BOOK_BUFFER_SIZE)
        if self._loop and self._running:
            asyncio.run_coroutine_threadsafe(
                self._send_subscribe(ticker, "book", market), self._loop)

    def unsubscribe(self, ticker: str) -> None:
        self.subscribed_ticks.discard(ticker)
        self.subscribed_books.discard(ticker)

    # ─── 버퍼 조회 ───────────────────────────────────────────
    def get_recent_ticks(self, ticker: str, n: int = 100) -> List[Dict[str, Any]]:
        buf = self.tick_buf.get(ticker)
        if not buf:
            return []
        return list(buf)[-n:]

    def get_recent_books(self, ticker: str, n: int = 50) -> List[Dict[str, Any]]:
        buf = self.book_buf.get(ticker)
        if not buf:
            return []
        return list(buf)[-n:]

    # ─── 시작/중단 ───────────────────────────────────────────
    def start(self) -> Dict[str, Any]:
        """백그라운드 thread에서 asyncio loop 시작."""
        if self._running:
            return {"ok": True, "already_running": True}
        # approval_key 발급
        ak = self._get_approval_key()
        if not ak:
            return {"ok": False, "error": "approval_key 발급 실패 — 키 확인"}
        self.approval_key = ak
        self._running = True
        self._thread = threading.Thread(target=self._run_loop,
                                         name="kis-ws", daemon=True)
        self._thread.start()
        return {"ok": True, "mode": self.mode}

    def stop(self) -> None:
        self._running = False
        if self._loop and self._loop.is_running():
            try:
                asyncio.run_coroutine_threadsafe(
                    self._close_ws(), self._loop)
            except Exception:
                pass

    def status(self) -> Dict[str, Any]:
        return {
            "running": self._running,
            "mode": self.mode,
            "approval_key_set": bool(self.approval_key),
            "n_tick_subscribed": len(self.subscribed_ticks),
            "n_book_subscribed": len(self.subscribed_books),
            "buffers": {tk: len(buf) for tk, buf in self.tick_buf.items()},
        }

    # ─── 내부: approval_key (HMAC 인증용) ────────────────────
    def _get_approval_key(self) -> Optional[str]:
        from .kis import load_keys
        import requests
        k = load_keys().get(self.mode, {})
        if not k.get("app_key") or not k.get("app_secret"):
            return None
        base = ("https://openapivts.koreainvestment.com:29443"
                 if self.mode == "vts"
                 else "https://openapi.koreainvestment.com:9443")
        try:
            r = requests.post(
                base + "/oauth2/Approval",
                json={"grant_type": "client_credentials",
                       "appkey": k["app_key"],
                       "secretkey": k["app_secret"]},
                timeout=10,
            )
            if r.status_code == 200:
                return r.json().get("approval_key")
        except Exception:
            pass
        return None

    # ─── 내부: asyncio loop ──────────────────────────────────
    def _run_loop(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._ws_main())
        except Exception:
            pass
        finally:
            try:
                self._loop.close()
            except Exception:
                pass

    async def _ws_main(self):
        """메인 루프 — 끊기면 재연결."""
        try:
            import websockets
        except ImportError:
            # websockets 라이브러리 없음 — noop
            self._running = False
            return
        while self._running:
            try:
                async with websockets.connect(self.ws_url, ping_interval=20,
                                                ping_timeout=10) as ws:
                    self._ws = ws
                    # 재구독
                    for tk in list(self.subscribed_ticks):
                        await self._send_subscribe(tk, "tick", "kr")
                    for tk in list(self.subscribed_books):
                        await self._send_subscribe(tk, "book", "kr")
                    # 수신
                    async for msg in ws:
                        if not self._running:
                            break
                        self._handle_message(msg)
            except Exception:
                if not self._running:
                    break
                await asyncio.sleep(self._reconnect_delay)
        self._ws = None

    async def _send_subscribe(self, ticker: str, kind: str,
                               market: str = "kr"):
        if not self._ws or not self.approval_key:
            return
        # tr_id 선택
        if kind == "tick":
            tr = self.TR_TICK_US if market == "us" else self.TR_TICK_KR
        else:
            tr = self.TR_BOOK_KR if market == "kr" else self.TR_BOOK_KR
        payload = {
            "header": {
                "approval_key": self.approval_key,
                "custtype": "P",
                "tr_type": "1",     # 1=등록, 2=해제
                "content-type": "utf-8",
            },
            "body": {
                "input": {"tr_id": tr, "tr_key": ticker},
            },
        }
        try:
            await self._ws.send(json.dumps(payload))
        except Exception:
            pass

    async def _close_ws(self):
        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass
        self._ws = None

    def _handle_message(self, msg: str):
        """수신 메시지 파싱 — 종목별 buffer + 콜백."""
        # KIS WebSocket은 양식이 두 가지: JSON(인증/응답) vs pipe-delimited (실시간 데이터)
        try:
            if msg.startswith("{"):
                # JSON 응답 — 인증 완료/구독 결과/에러
                # 필요시 로깅, 데이터엔 영향 없음
                return
            # pipe-delimited 실시간 데이터
            # 형식: tr_id|count|encrypt|data1^data2^...
            parts = msg.split("|", 3)
            if len(parts) < 4:
                return
            tr_id = parts[0]
            data_str = parts[3]
            records = data_str.split("^")
            if tr_id == self.TR_TICK_KR:
                tick = self._parse_tick_kr(records)
                if tick:
                    tk = tick["ticker"]
                    self.tick_buf.setdefault(tk, collections.deque(
                        maxlen=_TICK_BUFFER_SIZE)).append(tick)
                    if self.on_tick:
                        try: self.on_tick(tk, tick)
                        except Exception: pass
            elif tr_id == self.TR_BOOK_KR:
                book = self._parse_book_kr(records)
                if book:
                    tk = book["ticker"]
                    self.book_buf.setdefault(tk, collections.deque(
                        maxlen=_BOOK_BUFFER_SIZE)).append(book)
                    if self.on_book:
                        try: self.on_book(tk, book)
                        except Exception: pass
        except Exception:
            pass

    # ─── 데이터 파서 (KIS 공식 필드 순서 — open-trading-api 참조) ─
    def _parse_tick_kr(self, r: List[str]) -> Optional[Dict[str, Any]]:
        """실시간 체결 (H0STCNT0) 필드. 일부만 사용."""
        try:
            return {
                "ticker": r[0],                        # 종목코드
                "time":   r[1],                        # HHMMSS
                "price":  float(r[2]) if r[2] else 0,  # 현재가
                "side":   r[10] if len(r) > 10 else "",  # 매수/매도 구분
                "size":   int(r[12]) if len(r) > 12 and r[12] else 0,
                "_ts":    time.time(),
            }
        except Exception:
            return None

    def _parse_book_kr(self, r: List[str]) -> Optional[Dict[str, Any]]:
        """실시간 호가 (H0STASP0) — 10단계 매수/매도.

        매도호가: r[3..12], 매수호가: r[13..22]
        매도잔량: r[23..32], 매수잔량: r[33..42]
        """
        try:
            asks, bids = [], []
            for i in range(10):
                ap = float(r[3+i]) if r[3+i] else 0
                av = int(r[23+i]) if r[23+i] else 0
                bp = float(r[13+i]) if r[13+i] else 0
                bv = int(r[33+i]) if r[33+i] else 0
                if ap > 0: asks.append({"price": ap, "size": av})
                if bp > 0: bids.append({"price": bp, "size": bv})
            return {
                "ticker": r[0],
                "time":   r[1],
                "asks":   asks,
                "bids":   bids,
                "_ts":    time.time(),
            }
        except Exception:
            return None


# ── 싱글톤 ────────────────────────────────────────────────────
_singletons: Dict[str, KISWebSocketClient] = {}
_singleton_lock = threading.Lock()


def get_ws_client(mode: str = "vts") -> KISWebSocketClient:
    """모드별 싱글톤. 자동 시작 안 함 — start() 명시 호출 필요."""
    with _singleton_lock:
        if mode not in _singletons:
            _singletons[mode] = KISWebSocketClient(mode=mode)
        return _singletons[mode]
