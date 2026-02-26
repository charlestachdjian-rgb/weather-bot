@echo off
echo ========================================
echo PARIS WEATHER PAPER TRADING SYSTEM
echo Starting for Feb 23, 2026
echo ========================================
echo.

REM Check if Python is available
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python not found. Please install Python 3.8+.
    pause
    exit /b 1
)

echo Step 1: Analyzing forecast and optimizing $30 strategy...
python smart_30_dollar_strategy.py

echo.
echo Step 2: Starting weather monitor (background)...
start /B python weather_monitor.py
echo Weather monitor started in background.

echo.
echo Step 3: Starting $30 paper trading system...
python paper_trade.py --mode live --balance 30 --trade-size 5

echo.
echo ========================================
echo PAPER TRADING ACTIVE
echo Monitor will:
echo 1. Detect trading signals automatically
echo 2. Execute paper trades with $30 capital
echo 3. Optimize position sizing for maximum edge
echo ========================================
echo.
echo Press Ctrl+C to stop all systems.
echo ========================================

REM Keep the window open
pause