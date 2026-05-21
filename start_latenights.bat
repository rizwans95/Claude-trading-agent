@echo off
title LateNights Trading Agent V2
color 0A

echo.
echo  ================================================
echo   LateNights Trading Agent V2
echo   Starting all services...
echo  ================================================
echo.

:: Set working directory to script location
cd /d "%~dp0"

:: Check Python is available
py --version >nul 2>&1
if errorlevel 1 (
    echo  ERROR: Python not found. Install Python and try again.
    pause
    exit /b 1
)

:: Kill any existing processes on port 8000
echo  Checking for existing server on port 8000...
for /f "tokens=5" %%a in ('netstat -aon 2^>nul ^| find ":8000" ^| find "LISTENING"') do (
    taskkill /F /PID %%a >nul 2>&1
)
timeout /t 1 /nobreak >nul

:: ── START FASTAPI SERVER ────────────────────────────────
echo  Starting FastAPI server...
start "LateNights Server" cmd /k "cd /d "%~dp0" && py -m uvicorn main:app --port 8000 --log-level warning"
echo  Server starting on http://127.0.0.1:8000

:: Wait for server to be ready
echo  Waiting for server to be ready...
:WAIT_SERVER
timeout /t 2 /nobreak >nul
py -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000')" >nul 2>&1
if errorlevel 1 goto WAIT_SERVER
echo  Server is online.

:: ── START MONITOR LOOP ─────────────────────────────────
echo  Starting automation monitor loop...
start "LateNights Monitor" cmd /k "cd /d "%~dp0" && py kucoin_executor.py --mode monitor"
echo  Monitor loop running.

:: ── OPEN DASHBOARD ─────────────────────────────────────
echo  Opening dashboard...
timeout /t 2 /nobreak >nul
start "" "%~dp0dashboard_v2.html"

:: ── SHOW STATUS ────────────────────────────────────────
echo.
echo  ================================================
echo   All services started successfully
echo  ================================================
echo.
echo   Server:   http://127.0.0.1:8000 (Terminal 1)
echo   Monitor:  watching for signals  (Terminal 2)
echo   Dashboard: open in browser
echo.
echo   Next session hours (UTC): 07:00 / 12:00 / 14:00 / 18:00
echo.
echo  ================================================
echo   TO SHUT DOWN:
echo   Close this window + both terminal windows
echo   Or press any key here to stop everything
echo  ================================================
echo.
pause >nul

:: ── SHUTDOWN ───────────────────────────────────────────
echo  Shutting down...

:: Kill server
for /f "tokens=5" %%a in ('netstat -aon 2^>nul ^| find ":8000" ^| find "LISTENING"') do (
    taskkill /F /PID %%a >nul 2>&1
)

:: Close terminal windows by title
taskkill /FI "WINDOWTITLE eq LateNights Server"  /F >nul 2>&1
taskkill /FI "WINDOWTITLE eq LateNights Monitor" /F >nul 2>&1

echo  All services stopped. Goodbye.
timeout /t 2 /nobreak >nul
