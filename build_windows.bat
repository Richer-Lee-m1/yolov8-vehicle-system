@echo off
REM Build the Windows exe for VehicleCount (P4).
REM Run on a Windows machine with Python 3.11/3.12, from files\product\.
REM
REM 1. Install packaging + runtime dependencies:
REM        python -m pip install -r requirements-packaging.txt
REM 2. Static ffmpeg: download the gyan.dev "essentials" build
REM    (https://www.gyan.dev/ffmpeg/builds/) and copy ffmpeg.exe
REM    into tools\  (the spec bundles it next to the exe).
REM 3. Build:
REM        python -m PyInstaller vehicle-count.spec --clean --noconfirm
REM    Output: dist\VehicleCount\VehicleCount.exe (+ ui/ models/ data/ ffmpeg.exe)
REM 4. Smoke test (no Python needed on the target machine):
REM        dist\VehicleCount\VehicleCount.exe --version
REM        dist\VehicleCount\VehicleCount.exe --no-browser
REM    then open http://127.0.0.1:8931/ and run the TEST1 preset.
REM    R18: default launch opens the native desktop window (pywebview /
REM    WebView2); on a machine without WebView2 it prints a message and
REM    falls back to the browser. --browser forces the browser path.
python -m pip install -r requirements-packaging.txt
if not exist tools\ffmpeg.exe (
  echo [build] tools\ffmpeg.exe missing - download the gyan.dev essentials build and copy ffmpeg.exe into tools\
  exit /b 1
)
python -m PyInstaller vehicle-count.spec --clean --noconfirm
