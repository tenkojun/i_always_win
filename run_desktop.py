# -*- coding: utf-8 -*-
"""
Plutus ― 데스크톱 런처
=============================
이 파일을 실행하면 (또는 .exe 로 빌드하면):

  1) 내부에 분석 웹서버를 띄우고
  2) 서버가 준비될 때까지 기다린 뒤
  3) 네이티브 앱 창을 연다.

PC  : 이 런처 / .exe 실행
폰  : 같은 와이파이에서  http://<이 PC의 IP>:8765
      집 밖에서는 설정 → 외부 접근

콘솔 창을 띄우지 않는다
-----------------------
EXE 는 ``console=False`` 로 빌드한다. 그러면 파이썬의 표준 출력이
없어져서, ``print`` 한 줄에 앱이 통째로 죽을 수 있다(윈도우 빌드에서
``sys.stdout`` 이 ``None`` 이 된다). 그래서 창이 없는 상태로 실행되면
표준 출력을 ``.data/logs/app.log`` 로 돌린다. 문제가 생기면 그 파일을 본다.
"""
from __future__ import annotations

import os
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser

from version import APP_NAME, __version__

DEFAULT_PORT = 8765
_MAX_PORT_TRIES = 12


# ─────────────────────────────────────────────────────────────
#  로그 — 콘솔이 없을 때를 대비
# ─────────────────────────────────────────────────────────────
def _console_alive() -> bool:
    """
    출력이 실제로 어딘가에 닿는가.

    창 없는 빌드에서 ``sys.stdout`` 은 ``None`` 이기도 하고, PyInstaller
    버전에 따라 '아무 데도 안 쓰는' 껍데기 객체이기도 하다. 후자는
    ``None`` 검사만으로는 걸러지지 않아 로그가 통째로 사라진다.
    그래서 진짜 파일 디스크립터가 있는지까지 본다.
    """
    for s in (sys.stdout, sys.stderr):
        if s is None:
            return False
        try:
            if s.fileno() < 0:
                return False
        except Exception:
            return False
    return True


def _setup_logging() -> None:
    """
    출력이 갈 곳이 없으면 로그 파일로 돌린다.

    얼린 앱(EXE)은 조건을 따지지 않고 **항상** 파일로 보낸다.
    창 없는 빌드에서도 ``sys.stdout.fileno()`` 가 그럴듯한 값을 돌려주는
    경우가 있어서, 살아 있는지 알아맞히려다 로그를 통째로 잃는다.
    콘솔이 없는 앱에서 진단 수단은 이 파일뿐이라 잃으면 안 된다.
    """
    # 콘솔이 살아 있어도 cp949 라면 이모지 한 줄에 죽는다. 먼저 막는다.
    try:
        from engine.console import make_console_safe
        make_console_safe()
    except Exception:
        pass
    if not getattr(sys, "frozen", False) and _console_alive():
        return
    try:
        from engine.paths import DATA_DIR
        log_dir = DATA_DIR / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        f = open(log_dir / "app.log", "a", encoding="utf-8", buffering=1)
        sys.stdout = f
        sys.stderr = f
        print("\n" + "=" * 60)
        print(f"{APP_NAME} v{__version__} — {time.strftime('%Y-%m-%d %H:%M:%S')}")
    except Exception:
        # 로그조차 못 열면 조용히 버린다. print 로 죽는 것보다 낫다.
        class _Null:
            def write(self, *_a):  return 0
            def flush(self):       return None
            def isatty(self):      return False
        sys.stdout = sys.stdout or _Null()
        sys.stderr = sys.stderr or _Null()


# ─────────────────────────────────────────────────────────────
#  네트워크
# ─────────────────────────────────────────────────────────────
def _lan_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def _port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("0.0.0.0", port))
            return True
        except OSError:
            return False


def _already_running(port: int) -> bool:
    """그 포트에서 우리 앱이 이미 돌고 있는가."""
    try:
        with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/api/health", timeout=1.5) as r:
            return r.status == 200
    except Exception:
        return False


def _pick_port() -> tuple[int, bool]:
    """
    쓸 포트를 고른다. 이미 우리 앱이 떠 있으면 그 포트를 그대로 쓰고
    서버를 새로 띄우지 않는다(중복 실행 방지).
    """
    base = int(os.environ.get("IAW_PORT", DEFAULT_PORT))
    if _already_running(base):
        return base, True
    for p in range(base, base + _MAX_PORT_TRIES):
        if _port_free(p):
            return p, False
    return base, False


def _wait_for_server(url: str, timeout: float = 40.0) -> bool:
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


# ─────────────────────────────────────────────────────────────
#  서버
# ─────────────────────────────────────────────────────────────
def _serve(port: int) -> None:
    try:
        from webapp.server import app
        app.run(host="0.0.0.0", port=port, threaded=True,
                use_reloader=False)
    except Exception:
        import traceback
        traceback.print_exc()


def _shutdown() -> None:
    """창을 닫을 때 터널까지 정리한다. 남겨 두면 유령이 된다."""
    try:
        from engine.cloud.supervisor import stop as tunnel_stop
        tunnel_stop()
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────
def _icon_path() -> str | None:
    for base in (getattr(sys, "_MEIPASS", None), os.path.dirname(__file__)):
        if not base:
            continue
        p = os.path.join(base, "assets", "app.ico")
        if os.path.exists(p):
            return p
    return None


# ─────────────────────────────────────────────────────────────
#  WebView2 런타임 — 창이 안 뜨는 1순위 원인
# ─────────────────────────────────────────────────────────────
#  pywebview 는 윈도우에서 Edge WebView2 위에 화면을 그린다. 이 런타임이
#  없으면 창이 비거나 열리자마자 닫힌다. 그런데 EXE 는 console=False 라
#  오류 메시지가 어디에도 안 보인다 — 사용자 눈에는 "그냥 안 켜짐" 이다.
#  (실제로 새 PC 에서 이 증상이 났다. v3.4.2 에서 잡았다.)
#
#  윈도우 11 과 최근 윈도우 10 에는 기본 탑재지만, 정리된 이미지나 LTSC,
#  Edge 를 제거한 PC 에는 없다.
WEBVIEW2_GUID = "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
WEBVIEW2_URL = "https://developer.microsoft.com/microsoft-edge/webview2/"


def webview2_version() -> str | None:
    """설치돼 있으면 버전 문자열, 없으면 None. 윈도우가 아니면 None."""
    if os.name != "nt":
        return None
    try:
        import winreg
    except Exception:
        return None
    spots = (
        (winreg.HKEY_LOCAL_MACHINE,
         r"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\EdgeUpdate\Clients"),
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\EdgeUpdate\Clients"),
    )
    for hive, base in spots:
        try:
            with winreg.OpenKey(hive, base + "\\" + WEBVIEW2_GUID) as k:
                pv, _ = winreg.QueryValueEx(k, "pv")
                if pv and pv != "0.0.0.0":
                    return str(pv)
        except OSError:
            continue
        except Exception:
            continue
    return None


def _msgbox(text: str, title: str, flags: int) -> int:
    """
    네이티브 대화상자. 콘솔이 없어도 보인다 — 이게 요점이다.
    띄우지 못하면 0 을 돌려주고 호출한 쪽이 알아서 진행한다.
    """
    if os.name != "nt":
        return 0
    try:
        import ctypes
        return int(ctypes.windll.user32.MessageBoxW(None, text, title, flags))
    except Exception:
        return 0


def warn_missing_webview2(url: str) -> None:
    """
    런타임이 없다고 알리고 설치 페이지를 열어 준다. 그리고 **지금 당장은
    브라우저로 쓸 수 있게** 한다 — Plutus 는 어차피 로컬 웹앱이라
    브라우저에서도 기능이 전부 돈다. 설치를 강요할 이유가 없다.
    """
    MB_YESNO, MB_ICONWARNING, MB_SETFOREGROUND = 0x4, 0x30, 0x10000
    IDYES = 6
    nl = "\n"
    msg = (
        f"{APP_NAME} 를 창으로 띄우려면 Microsoft Edge WebView2 런타임이 "
        f"필요한데, 이 PC 에는 설치돼 있지 않습니다.{nl}{nl}"
        f"지금은 기본 브라우저로 열어 드립니다. 모든 기능이 그대로 동작합니다.{nl}{nl}"
        f"다음부터 앱 창으로 쓰시려면 런타임을 설치하세요.{nl}"
        f"(무료 · 마이크로소프트 공식 · 'Evergreen 부트스트래퍼'){nl}{nl}"
        "설치 페이지를 지금 여시겠습니까?"
    )
    # 여기는 "이미 뭔가 잘못된" 경로다. 로그 리다이렉트가 실패한 상태일
    # 수도 있으니 print 가 절대 예외를 내지 않게 ASCII 만 쓴다
    # (em-dash 하나로 cp949 콘솔에서 UnicodeEncodeError 가 난다).
    print("[!] WebView2 runtime not found - falling back to browser.",
          flush=True)
    if _msgbox(msg, f"{APP_NAME} — WebView2 런타임 필요",
               MB_YESNO | MB_ICONWARNING | MB_SETFOREGROUND) == IDYES:
        try:
            webbrowser.open(WEBVIEW2_URL)
        except Exception:
            pass
    _browser_mode(url)


def _browser_mode(url: str) -> None:
    """창 대신 브라우저로 띄우고 서버를 살려 둔다."""
    try:
        webbrowser.open(url)
    except Exception:
        pass
    print(f"\n브라우저에서 열었습니다: {url}", flush=True)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        print("\n종료합니다.")
    _shutdown()


def main() -> None:
    _setup_logging()

    port, reuse = _pick_port()
    url = f"http://127.0.0.1:{port}"
    ip = _lan_ip()

    print("=" * 60)
    print(f"  {APP_NAME}  v{__version__}")
    print(f"  PC  :  {url}")
    print(f"  폰  :  http://{ip}:{port}   (같은 와이파이)")
    print("=" * 60)

    if reuse:
        print("이미 실행 중인 인스턴스를 찾았습니다. 창만 엽니다.")
    else:
        threading.Thread(target=_serve, args=(port,), daemon=True).start()
        print("서버 시작 대기 중…", flush=True)
        if _wait_for_server(f"{url}/api/health", timeout=40.0):
            print("서버 준비 완료.", flush=True)
        else:
            print("주의: 서버 응답이 늦습니다. 그래도 창을 엽니다.", flush=True)

    # 창을 만들기 **전에** 런타임부터 본다. pywebview 는 런타임이 없으면
    # 창을 띄우다 실패하는데, console=False 라 그 오류가 아무 데도 안 보인다.
    # 미리 확인하면 사용자에게 왜 그런지 말해 줄 수 있다.
    if os.name == "nt":
        wv2 = webview2_version()
        print(f"WebView2 런타임: {wv2 or '없음'}", flush=True)
        if not wv2:
            warn_missing_webview2(url)
            return

    try:
        import webview  # type: ignore
        window = webview.create_window(
            APP_NAME, url,
            width=1480, height=900,
            min_size=(960, 600),
            maximized=True,
        )
        try:
            window.events.closed += _shutdown
        except Exception:
            pass
        icon = _icon_path()
        try:
            webview.start(icon=icon) if icon else webview.start()
        except TypeError:
            # 구버전 pywebview 는 icon 인자를 모른다
            webview.start()
        _shutdown()
    except ImportError:
        # pywebview 가 없으면 기본 브라우저로 연다
        _browser_mode(url)
    except Exception as e:
        # 런타임 점검을 통과하고도 창이 안 뜨는 경우가 남는다(그래픽 드라이버,
        # 손상된 WebView2, 정책으로 막힌 사용자 데이터 폴더 등). 여기서
        # 조용히 죽으면 사용자는 이유를 영영 모른다 — 말해 주고 브라우저로.
        import traceback
        print("[!] 앱 창 생성 실패:", flush=True)
        traceback.print_exc()
        MB_OK, MB_ICONERROR, MB_SETFOREGROUND = 0x0, 0x10, 0x10000
        nl = "\n"
        _msgbox(
            f"앱 창을 여는 데 실패했습니다.{nl}{nl}"
            f"{type(e).__name__}: {e}{nl}{nl}"
            f"기본 브라우저로 열어 드립니다. 기능은 전부 그대로입니다.{nl}"
            f"자세한 내용은 .data/logs/app.log 에 남습니다.",
            f"{APP_NAME} — 창을 열 수 없음",
            MB_OK | MB_ICONERROR | MB_SETFOREGROUND)
        _browser_mode(url)


if __name__ == "__main__":
    main()
