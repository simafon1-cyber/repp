@echo off
rem Installs the AI Scalper Pro expert advisor into MetaTrader 5.
rem ASCII-only on purpose: Russian text lives in the PowerShell script.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0Install-AIScalper.ps1"
if errorlevel 1 (
    echo.
    echo Installer reported a problem. See the messages above.
    pause
)
