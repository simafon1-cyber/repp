@echo off
rem Starts the Trader application (multi-account MT5 manager).
cd /d "%~dp0..\trader_app"
if exist "venv\Scripts\pythonw.exe" (
    start "" "venv\Scripts\pythonw.exe" main.py
) else (
    start "" pythonw main.py
)
