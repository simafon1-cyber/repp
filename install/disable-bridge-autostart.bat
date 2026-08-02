@echo off
rem Disables autostart of the AI Scalper Pro bridge.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0Disable-BridgeAutostart.ps1"
