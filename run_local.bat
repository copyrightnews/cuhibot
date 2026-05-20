@echo off
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

echo.
echo [1/2] Starting Cloudflare Tunnel in the background...
start "Cloudflare Tunnel Daemon" /min cmd /c "cloudflared.exe tunnel --url http://localhost:8080 > tunnel.log 2>&1"

:: Wait for URL and update .env
python update_env.py

echo [2/2] Starting Telegram Bot (includes FastAPI Backend)...
start "Telegram Bot" cmd /k "python bot.py"

echo.
echo ==================================================
echo Services have been successfully launched!
echo.
echo  - Keep the Telegram Bot window open to keep running.
echo  - Close it to stop the bot and the backend server.
echo ==================================================
echo.
pause
