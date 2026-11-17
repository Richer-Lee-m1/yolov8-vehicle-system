# -*- coding: utf-8 -*-
"""
Product launcher: starts the local Web UI server and opens the desktop
window (pywebview native window, WebView2 on Windows; R18 / P6).

    python app.py [--port 8931]            desktop window (default)
    python app.py --browser                open the system browser instead
    python app.py --no-browser             server only, no window/browser
    python app.py --version

Double-click entry for the packaged exe (PyInstaller target, P4).
When frozen, the same binary also runs the engine as a subcommand
(``app --engine <engine args...>``) so the server can spawn engine
workers without a separate python interpreter or engine.py on disk.
"""

import argparse
import os
import sys
import webbrowser

VERSION = "0.14"
VERSION_TAG = "v0.14 / P7 · 可视化"

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "src"))

if getattr(sys, "frozen", False):
    # Bundled helper binaries (ffmpeg) sit in _internal (sys._MEIPASS)
    # in the one-folder bundle; put that dir first on PATH so
    # engine._transcode_h264 finds the bundled ffmpeg even on a
    # machine without one installed. Inherited by the engine
    # subprocesses the server spawns. P4.
    _bundle = getattr(sys, "_MEIPASS",
                      os.path.dirname(os.path.abspath(sys.executable)))
    os.environ["PATH"] = _bundle + os.pathsep + os.environ.get("PATH", "")
    # R12: extraction tools (Python zipfile, some file managers) drop
    # Unix permission bits, leaving the bundled ffmpeg non-executable;
    # shutil.which then skips it and the engine silently falls back to
    # mp4v, which Chromium cannot play. Self-heal the bit on startup
    # (server and --engine subcommand both pass through here).
    _ffmpeg = os.path.join(_bundle, "ffmpeg")
    try:
        if os.path.isfile(_ffmpeg) and not os.access(_ffmpeg, os.X_OK):
            os.chmod(_ffmpeg, 0o755)
    except OSError:
        pass


def _run_engine_subcommand(argv):
    """Run the engine inside this (frozen) binary: ``app --engine ...``."""
    sys.argv = [sys.argv[0]] + argv
    from engine import main as engine_main  # noqa: E402
    engine_main()


if __name__ == "__main__":
    if "--engine" in sys.argv:
        _idx = sys.argv.index("--engine")
        _run_engine_subcommand(sys.argv[_idx + 1:])
        sys.exit(0)
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8931)
    ap.add_argument("--no-browser", action="store_true",
                    help="server only: no desktop window, no browser")
    ap.add_argument("--browser", action="store_true",
                    help="open the system browser instead of the desktop window")
    ap.add_argument("--version", action="store_true")
    args = ap.parse_args()
    if args.version:
        print(f"VehicleCount {VERSION_TAG}")
        sys.exit(0)
    if args.no_browser:
        from server import main as serve  # noqa: E402
        sys.argv = [sys.argv[0], "--port", str(args.port)]
        serve()
    elif args.browser:
        from server import main as serve  # noqa: E402
        webbrowser.open(f"http://127.0.0.1:{args.port}/")
        sys.argv = [sys.argv[0], "--port", str(args.port)]
        serve()
    else:
        # Default (R18): native desktop window; falls back to the browser
        # inside desktop_shell when no window can start.
        from desktop_shell import run_desktop  # noqa: E402
        sys.exit(run_desktop(port=args.port))
