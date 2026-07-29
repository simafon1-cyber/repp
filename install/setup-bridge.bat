@echo off
rem Launcher for the DualGuard bridge setup.
rem ASCII-only on purpose: Russian text lives in the PowerShell script.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0Setup-Bridge.ps1"
if errorlevel 1 (
    echo.
    echo Setup reported a problem. See the messages above.
    pause
)
