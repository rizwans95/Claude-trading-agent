@echo off
title Trading Agent V2 — FastAPI Server
echo.
echo  ╔══════════════════════════════════════════════╗
echo  ║       TRADING AGENT V2 — LOCAL SERVER       ║
echo  ║  All analysis runs locally. No Claude        ║
echo  ║  credits used for standard signals.          ║
echo  ╚══════════════════════════════════════════════╝
echo.

cd /d "%~dp0phase 2 files - python backend"

echo [1/2] Installing / updating dependencies...
pip install -r requirements.txt -q

echo [2/2] Starting FastAPI server on http://127.0.0.1:8000
echo       Press Ctrl+C to stop.
echo.

python -m uvicorn main:app --host 127.0.0.1 --port 8000 --reload

pause
