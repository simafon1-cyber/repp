@echo off
rem Starts the AI Scalper Pro bridge manually (window must stay open).
cd /d "%~dp0..\ai_scalper_pro\bridge"
if exist "venv\Scripts\python.exe" (
    "venv\Scripts\python.exe" bridge_example.py
) else (
    python bridge_example.py
)
pause
