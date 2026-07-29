@echo off
rem Launcher for the DualGuard EA installer.
rem This file is intentionally ASCII-only: Russian text lives in the
rem PowerShell script, where Unicode is handled correctly.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0Install-DualGuard.ps1"
if errorlevel 1 (
    echo.
    echo Installer reported a problem. See the messages above.
    pause
)
