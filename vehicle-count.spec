# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec — VehicleCount one-folder bundle (P4).

Build (Linux proof build on this dev VM, run from files/product/):
    python -m PyInstaller vehicle-count.spec --clean --noconfirm

Build (Windows .exe — run the same command on a Windows machine;
see build_windows.bat). The spec is platform-aware: Linux bundles
the system ffmpeg, Windows expects tools/ffmpeg.exe (static build).

Output: dist/VehicleCount/  (exe + _internal/ holding ui/ models/
data/ ffmpeg and the Python libs)
Target total size: <= 600 MB (roadmap P4).
"""

import os
import sys

block_cipher = None
ROOT = os.getcwd()  # run pyinstaller from files/product/

binaries = []
datas = [
    (os.path.join(ROOT, "ui"), "ui"),
    (os.path.join(ROOT, "models", "yolov8s-visdrone.onnx"), "models"),
    (os.path.join(ROOT, "data", "geometry_template.json"), "data"),
    (os.path.join(ROOT, "data", "param_presets.json"), "data"),
    # R14/P4: ship the three demo videos so the presets are READY
    # out of the box on the user's machine (~29 MB, still <=600 MB).
    (os.path.join(ROOT, "test-videos"), "test-videos"),
]
if sys.platform.startswith("linux"):
    # System ffmpeg (Ubuntu): bundle the binary; PyInstaller pulls its
    # shared-library dependencies automatically. Verified by running
    # the frozen exe with a clean PATH (R11).
    binaries += [("/usr/bin/ffmpeg", ".")]
elif sys.platform.startswith("win"):
    # Place a static ffmpeg.exe (gyan.dev essentials build) in tools/
    # before building; see build_windows.bat.
    binaries += [(os.path.join(ROOT, "tools", "ffmpeg.exe"), ".")]

a = Analysis(
    ["app.py"],
    pathex=[os.path.join(ROOT, "src")],
    binaries=binaries,
    datas=datas,
    hiddenimports=[
        "server", "engine", "paths", "counting", "movements",
        "onnx_detector", "tracker", "planning", "merge_surveys",
        "report_export", "desktop_shell",
        # R18: pywebview shell (its WSGI server is bottle) + dynamic backends
        "webview", "bottle", "proxy_tools",
    ] + ([
        # Windows-only: edgechromium/winforms backends are loaded
        # dynamically by gui="edgechromium"; static analysis misses them.
        "webview.platforms.edgechromium", "webview.platforms.winforms",
        "clr_loader",
    ] if sys.platform.startswith("win") else []),
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["torch", "torchvision", "ultralytics", "matplotlib",
              "tkinter"],
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

# R16/P4 slim: drop libicudata.so.74 (~29 MB, Qt leftover, needed by
# nothing in the bundle — no DT_NEEDED references it). NOTE: the other
# apparent top-level duplicates (libopenblasp, libavcodec, ...) are NOT
# in a.binaries at all — COLLECT adds them as SYMLINK entries pointing
# into the wheel *.libs dirs. They cost ~0 in dist/ but the zip script
# used to materialise them as real copies (+118 MB). That is fixed in
# build_portable_zip.py (skip in-bundle symlinks), not here.
_slim_drop = {"libicudata.so.74"}
a.binaries = [
    t for t in a.binaries
    if not (
        os.path.basename(t[0]) in _slim_drop
        and "/" not in t[0].replace(os.sep, "/")
    )
]

exe_kwargs = dict(
    exclude_binaries=True,  # one-folder: libs/data go to COLLECT, not EXE
    name="VehicleCount",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,  # keep console: server/engine logs stay visible
)
if sys.platform.startswith("win"):
    exe_kwargs["icon"] = os.path.join(ROOT, "assets", "app.ico")
    exe_kwargs["version"] = os.path.join(ROOT, "version_info.txt")

# NOTE: pyz and a.scripts MUST be positional: EXE() reads them from
# *args; passed as keywords they are silently dropped, which builds
# an exe whose TOC has no PYZ entry ("PYZ archive entry not found").
exe = EXE(pyz, a.scripts, **exe_kwargs)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    name="VehicleCount",
)
