@echo off
setlocal enabledelayedexpansion
title cuhibot Local Environment Runner
echo ==================================================
echo         cuhibot Local Environment Runner
echo ==================================================
echo.

:: Clean up any orphaned Cloudflare Tunnels and Python instances first to release ports/file locks
echo Stopping any previous running instances...
taskkill /f /im cloudflared.exe >nul 2>&1
taskkill /f /im python.exe >nul 2>&1
timeout /t 1 /nobreak >nul

:: Clean old log if exists
if exist tunnel.log del tunnel.log

:: Default port
set LOCAL_PORT=8080

:: Parse PORT from .env if it exists
if exist .env (
    for /f "usebackq tokens=1,2 delims==" %%i in (`findstr /i "^PORT=" .env`) do (
        set "VAL=%%j"
        set "VAL=!VAL:"=!"
        set "VAL=!VAL:'=!"
        for /f "tokens=*" %%a in ("!VAL!") do set "VAL=%%a"
        if not "!VAL!"=="" set LOCAL_PORT=!VAL!
    )
)

echo.
echo [1/3] Starting Cloudflare Tunnel on port !LOCAL_PORT!...
start "Cloudflare Tunnel Daemon" /min cmd /c "cloudflared.exe tunnel --url http://localhost:!LOCAL_PORT! > tunnel.log 2>&1"

:: Wait for URL and update .env
python update_env.py

:: Set environment to skip starting the duplicate embedded server in bot.py
set SKIP_EMBEDDED_SERVER=1

echo [2/3] Starting FastAPI Backend Server...
start "FastAPI Server" cmd /k "python server.py"

echo [3/3] Starting Telegram Bot...
start "Telegram Bot" cmd /k "python bot.py"

echo.
echo ==================================================
echo Services have been successfully launched!
echo.
echo  - Keep both the FastAPI Server and Telegram Bot windows open.
echo  - You can view detailed FastAPI requests in the Server window.
echo  - Close them to stop the local environment.
echo ==================================================
echo.
pause
