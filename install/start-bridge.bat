@echo off
rem Starts the DualGuard bridge (local HTTP server on 127.0.0.1:8080).
rem Keep this window open while trading - closing it stops the bridge.
cd /d "%~dp0..\bridge"

if not exist "venv\Scripts\python.exe" (
    echo.
    echo Bridge is not set up yet. Run setup-bridge.bat first.
    echo.
    pause
    exit /b 1
)

echo Starting DualGuard bridge on http://127.0.0.1:8080
echo Keep this window open. Press Ctrl+C to stop.
echo.
"venv\Scripts\python.exe" main.py
pause
