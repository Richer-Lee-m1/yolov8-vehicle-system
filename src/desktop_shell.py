# -*- coding: utf-8 -*-
"""
Desktop shell (R18 / P6): run the product UI in a native window.

    python app.py              -> desktop window (pywebview, WebView2 on Windows)
    python app.py --browser    -> old behaviour: open the system browser
    python app.py --no-browser -> server only, no window and no browser

How it works (research-desktop-shell-r17.md, scheme B): the existing
self-written http.server service starts in a daemon thread on a free
127.0.0.1 port, and pywebview opens a native window on that URL.
Closing the window shuts the service down. If the native window cannot
start (no WebView2 runtime on Windows, no GUI toolkit/display on this
Linux dev box), the shell falls back to opening the system browser and
keeps serving, so the product stays usable; the fallback reason is
printed to the console instead of failing silently.
"""

import os
import sys
import threading
import webbrowser

TITLE = "车辆检测与分型统计系统"
WINDOW_WIDTH, WINDOW_HEIGHT = 1440, 900
MIN_SIZE = (1024, 640)
WEBVIEW2_DOWNLOAD = ("https://developer.microsoft.com/en-us/microsoft-edge/"
                     "webview2/#download-section")


def _start_service(port):
    """Start the product HTTP server in a daemon thread; return (srv, url)."""
    import server  # noqa: E402 - src on sys.path (app.py inserts it)
    srv = server.start_server(port=port or 0, host="127.0.0.1")
    actual_port = srv.server_address[1]
    thread = threading.Thread(target=srv.serve_forever, name="http-server",
                              daemon=True)
    thread.start()
    return srv, f"http://127.0.0.1:{actual_port}/"


def _fallback_browser(srv, url, reason):
    """Browser fallback: keep serving, open the URL, wait until stopped."""
    print(f"[shell] 原生窗口不可用（{reason}），已回退到浏览器访问：{url}",
          flush=True)
    try:
        webbrowser.open(url)
    except Exception as exc:  # headless machine: browser open just fails
        print(f"[shell] 打开浏览器失败：{exc}", flush=True)
    try:
        threading.Event().wait()  # serve until killed / window never existed
    except KeyboardInterrupt:
        pass
    finally:
        srv.shutdown()


def run_desktop(port=None):
    """Start the service and open the native window. Blocks until exit."""
    force_browser = os.environ.get("VEHICLECOUNT_FORCE_BROWSER") == "1"
    srv, url = _start_service(port)
    if force_browser:
        _fallback_browser(srv, url, "已强制浏览器模式")
        return 0
    try:
        import webview
    except ImportError as exc:
        _fallback_browser(srv, url, f"未安装 pywebview：{exc}")
        return 0
    # Windows: pin edgechromium so a missing WebView2 runtime fails loudly
    # instead of silently falling back to the retired mshtml (IE) engine.
    gui = "edgechromium" if sys.platform.startswith("win") else None
    try:
        window = webview.create_window(
            TITLE, url,
            width=WINDOW_WIDTH, height=WINDOW_HEIGHT,
            min_size=MIN_SIZE, resizable=True,
        )
    except Exception as exc:
        _fallback_browser(srv, url, f"创建窗口失败：{exc}")
        return 0

    stopped = threading.Event()

    def _on_closed():
        srv.shutdown()
        stopped.set()

    window.events.closed += _on_closed
    try:
        webview.start(gui=gui, debug=False)
    except Exception as exc:
        # No WebView2 runtime / no display / no GUI toolkit: say so and
        # fall back to the browser instead of dying.
        print(f"[shell] 启动原生窗口失败：{exc}", flush=True)
        if sys.platform.startswith("win"):
            print(f"[shell] 请安装 WebView2 Runtime 后重试："
                  f"{WEBVIEW2_DOWNLOAD}", flush=True)
        _fallback_browser(srv, url, "原生窗口启动失败")
        return 0
    # webview.start() returned: the window is gone; make sure the
    # service is stopped even if the closed event did not fire.
    srv.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(run_desktop())
