@echo off
rem ASCII-only launcher: Russian text lives in the PowerShell script.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0Enable-PrivateMode.ps1"
if errorlevel 1 (
    echo.
    pause
)
