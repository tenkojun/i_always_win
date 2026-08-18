"""
I ALWAYS WIN ― 데스크톱 런처
================================
이 파일을 실행하면 (또는 .exe 로 빌드하면):
  1) 내부에 분석 웹서버를 띄우고
  2) 서버 준비될 때까지 대기 후
  3) 자동으로 브라우저(앱 창)를 연다.

PC  : 이 런처 / .exe 실행
폰  : 같은 와이파이에서  http://<이 PC의 IP>:8765  접속
"""
from __future__ import annotations
import threading
import time
import socket
import urllib.request
import urllib.error
import webbrowser

from version import APP_NAME, __version__

PORT = 8765


def _lan_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def _serve():
    from webapp.server import app
    app.run(host="0.0.0.0", port=PORT, threaded=True)


def _wait_for_server(url: str, timeout: float = 25.0) -> bool:
    """서버가 200 OK 응답할 때까지 폴링 (최대 timeout 초)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1.5) as r:
                if r.status == 200:
                    return True
        except (urllib.error.URLError, ConnectionResetError, OSError):
            pass
        time.sleep(0.25)
    return False


def main():
    ip = _lan_ip()
    print("=" * 56)
    print("  %s  v%s" % (APP_NAME, __version__))
    print("  PC  :  http://127.0.0.1:%d" % PORT)
    print("  폰  :  http://%s:%d   (같은 와이파이)" % (ip, PORT))
    print("=" * 56)

    threading.Thread(target=_serve, daemon=True).start()

    url = "http://127.0.0.1:%d" % PORT
    print("서버 시작 대기 중…", flush=True)
    if _wait_for_server(url, timeout=25.0):
        print("서버 준비 완료. 앱 창을 엽니다.", flush=True)
    else:
        print("주의: 서버 응답이 늦습니다. 그래도 창을 엽니다.", flush=True)

    # pywebview 가 있으면 네이티브 창, 없으면 기본 브라우저
    try:
        import webview  # type: ignore
        # maximized=True → 창은 유지하되 최대화 (최소화/복원/닫기 버튼 사용 가능)
        webview.create_window(APP_NAME, url,
                              width=1480, height=900,
                              min_size=(960, 600),
                              maximized=True)
        webview.start()
    except Exception:
        webbrowser.open(url)
        print("\n창을 닫지 마세요. 종료하려면 이 콘솔에서 Ctrl+C.")
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            print("\n종료합니다.")


if __name__ == "__main__":
    main()
