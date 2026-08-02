@echo off
rem Enables autostart of the AI Scalper Pro bridge at Windows logon.
rem ASCII-only on purpose: Russian text lives in the PowerShell script.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0Enable-BridgeAutostart.ps1"
if errorlevel 1 (
    echo.
    echo Setup reported a problem. See the messages above.
    pause
)
