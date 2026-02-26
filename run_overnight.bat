@echo off
echo ========================================
echo OVERNIGHT WEATHER MONITOR FOR FEB 23
echo Starting at %time%
echo ========================================
echo.

REM Create environment without Telegram
copy .env .env.backup 2>nul
echo CITY=paris > .env
echo POLL_MIN_DAY=5 >> .env
echo POLL_MIN_NIGHT=15 >> .env

echo Step 1: Starting weather monitor without Telegram...
start "Weather Monitor" python weather_monitor_no_telegram.py

echo Step 2: Starting paper trading with $30...
start "Paper Trading" python paper_trade.py --mode live --balance 30 --trade-size 5

echo.
echo ========================================
echo SYSTEMS RUNNING:
echo 1. Weather Monitor (no Telegram)
echo 2. Paper Trading ($30 capital)
echo ========================================
echo.
echo Both systems will run overnight and trade tomorrow.
echo Check logs in the morning.
echo ========================================

REM Wait a moment
timeout /t 5 /nobreak >nul

echo.
echo Current time: %date% %time%
echo.
echo Press any key to stop all systems...
pause >nul

REM Cleanup
taskkill /FI "WINDOWTITLE eq Weather Monitor*" /F 2>nul
taskkill /FI "WINDOWTITLE eq Paper Trading*" /F 2>nul
del .env 2>nul
copy .env.backup .env 2>nul
del .env.backup 2>nul

echo.
echo Systems stopped.
pause