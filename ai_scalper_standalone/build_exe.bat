@echo off
cd /d "%~dp0"
setlocal

echo ================================================
echo   AI Scalper Pro -- building desktop .exe
echo ================================================
echo.

where python >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Python not found in PATH. Install Python 3.10+ from python.org and try again.
    pause
    exit /b 1
)

echo [1/4] Installing project dependencies...
pip install -r requirements.txt
if errorlevel 1 goto :fail

echo.
echo [2/4] Installing PyInstaller...
pip install -r requirements-build.txt
if errorlevel 1 goto :fail

echo.
echo [3/4] Building AI_Scalper_Pro.exe (this takes a couple of minutes)...
REM --exclude-module config: config.py stays OUTSIDE the exe as a plain editable
REM file next to the program (needed for hot-reload of settings and so you can
REM change symbols/profile without rebuilding).
python -m PyInstaller --noconfirm --onefile --windowed --name "AI_Scalper_Pro" --exclude-module config --hidden-import accounts_tab --hidden-import accounts --hidden-import account_supervisor --collect-all MetaTrader5 --collect-all anthropic --collect-all openai --collect-all pystray --collect-all certifi --collect-all cryptography desktop_app.py
if errorlevel 1 goto :fail

echo.
echo [4/4] Copying config.py next to the built exe...
REM Only copy if dist\config.py doesn't exist yet. If it already exists, it
REM likely has settings you changed through the running program (MT5 login,
REM added symbols, saved password, etc.) -- overwriting it on every rebuild
REM would silently wipe those out. Delete dist\config.py yourself first if
REM you really want a fresh copy of the project's config.py.
if exist dist\config.py (
    echo dist\config.py already exists -- keeping it as-is ^(your saved settings are preserved^).
) else (
    copy /Y config.py dist\config.py >nul
    echo Copied a fresh config.py -- edit it or use the program's Settings tab to configure your broker.
)

echo.
echo ================================================
echo   DONE!  dist\AI_Scalper_Pro.exe
echo ================================================
echo.
echo You can run it right now (double-click dist\AI_Scalper_Pro.exe),
echo or build a full Windows installer -- see BUILD.md, step 2.
echo.
pause
exit /b 0

:fail
echo.
echo [ERROR] Build stopped -- see the message above.
pause
exit /b 1
