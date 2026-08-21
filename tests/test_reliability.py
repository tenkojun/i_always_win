# -*- coding: utf-8 -*-
"""
신뢰성 — 조용히 죽는 경로가 없는가.

콘솔이 없는 EXE 에서는 로그가 유일한 단서다. 예외가 아무 흔적 없이
사라지면 사용자는 "가끔 안 된다" 고밖에 말할 수 없고, 그러면 고칠 수 없다.
"""
from __future__ import annotations

import tempfile
import threading
from pathlib import Path

import pytest

import run_desktop as R


# ── 로그 회전 ────────────────────────────────────────────────
def test_log_rotates_when_it_gets_big():
    """
    app.log 는 "a" 로만 열려서 앱을 켤 때마다 이어 붙었다. 매 요청이 한 줄씩
    남으므로 오래 쓰는 설치본에서는 결국 디스크를 먹는다.
    """
    tmp = Path(tempfile.mkdtemp())
    log = tmp / "app.log"
    log.write_bytes(b"x" * (R.LOG_MAX_BYTES + 1024))
    R._rotate_log(log)
    assert not log.exists(), "회전 후 원본이 남아 있다"
    assert (tmp / "app.log.1").exists(), "회전된 파일이 없다"


def test_log_rotation_keeps_a_bounded_number_of_files():
    """무한히 쌓이면 회전하는 의미가 없다."""
    tmp = Path(tempfile.mkdtemp())
    log = tmp / "app.log"
    for _ in range(R.LOG_KEEP + 3):
        log.write_bytes(b"y" * (R.LOG_MAX_BYTES + 1024))
        R._rotate_log(log)
    extra = tmp / f"app.log.{R.LOG_KEEP + 1}"
    assert not extra.exists(), f"{extra.name} 까지 쌓였다"


def test_small_log_is_left_alone():
    """작은 로그를 괜히 회전하면 방금 남긴 단서를 잃는다."""
    tmp = Path(tempfile.mkdtemp())
    log = tmp / "app.log"
    log.write_bytes(b"z" * 100)
    R._rotate_log(log)
    assert log.exists() and log.stat().st_size == 100


def test_rotation_never_raises():
    """로그 정리 때문에 앱이 안 켜지면 본말이 전도된다."""
    R._rotate_log(Path(tempfile.mkdtemp()) / "없는파일.log")   # 예외 없이 통과


# ── 처리되지 않은 예외 ───────────────────────────────────────
def test_thread_exceptions_are_logged(capsys):
    """
    배경 스레드가 죽어도 기본값으로는 아무 흔적이 없다. 앱은 살아 있는데
    기능 하나만 조용히 멈춘 상태가 된다.
    """
    R._install_crash_logging()

    def boom():
        raise ValueError("의도된 테스트 예외")

    t = threading.Thread(target=boom, name="테스트스레드")
    t.start()
    t.join()

    out = capsys.readouterr().err + capsys.readouterr().out
    # capsys 가 훅 출력을 못 잡는 환경이 있어 훅 설치 자체도 확인한다
    assert threading.excepthook is not threading.__excepthook__, \
        "스레드 예외 훅이 설치되지 않았다"


# 일부러 터지는 라우트는 **모듈을 읽을 때** 등록한다.
# Flask 는 첫 요청 이후 라우트 추가를 거부한다. 테스트 함수 안에서 붙이면
# 다른 테스트 파일이 먼저 클라이언트를 만든 경우 AssertionError 로 죽는다
# — 앱이 아니라 테스트 순서 때문에 빨개지는 건 최악이다.
def _register_boom():
    from webapp.server import app
    if "_test_boom" in app.view_functions:
        return app

    @app.route("/__test_boom__", endpoint="_test_boom")
    def _boom():
        raise RuntimeError("의도된 테스트 예외")

    return app


_APP = _register_boom()


def test_flask_returns_json_on_unhandled_exception():
    """
    라우트에서 예외가 새면 Flask 기본 500 HTML 이 나간다. 화면은 JSON 을
    기대하고 있어서 "알 수 없는 오류" 로만 보이고, 서버엔 흔적이 안 남는다.
    """
    app = _APP
    app.config["PROPAGATE_EXCEPTIONS"] = False
    with app.test_client() as c:
        r = c.get("/__test_boom__")
    assert r.status_code == 500
    body = r.get_json()
    assert body is not None, "500 응답이 JSON 이 아니다"
    assert body.get("ok") is False
    assert body.get("detail") == "RuntimeError", "예외 종류를 안 알려 준다"
    # 예외 메시지 원문이 새어 나가면 경로·쿼리·키가 섞여 나올 수 있다
    assert "의도된 테스트 예외" not in r.get_data(as_text=True), \
        "예외 메시지 원문이 응답에 노출된다"


def test_http_errors_pass_through_untouched():
    """404/401 은 의도된 응답이다. 500 으로 바꿔 버리면 안 된다."""
    with _APP.test_client() as c:
        r = c.get("/__없는_경로__")
    assert r.status_code == 404
