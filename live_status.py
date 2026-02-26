"""
Live monitoring script to check system status and current trading opportunities.
Run this to see what's happening right now.
"""
import json
from datetime import datetime, timezone
import os

def check_live_status():
    """Check current system status and trading opportunities."""
    print("=" * 70)
    print("LIVE WEATHER TRADING STATUS")
    print("=" * 70)
    
    # Check if weather_monitor is running
    try:
        import psutil
        python_processes = []
        for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
            if 'python' in proc.info['name'].lower():
                cmdline = ' '.join(proc.info.get('cmdline') or [])
                if 'weather_monitor' in cmdline:
                    python_processes.append(f"PID {proc.info['pid']}: {proc.info['name']}")
    except ImportError:
        python_processes = ["psutil not installed - cannot check processes"]
    
    print(f"Python processes found: {len(python_processes)}")
    for proc in python_processes:
        print(f"  {proc}")
    
    # Check logs
    log_path = r"C:\Users\Charl\Desktop\Cursor\weather-bot\weather_log.jsonl"
    if os.path.exists(log_path):
        with open(log_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
            last_line = lines[-1] if lines else "No logs"
        
        try:
            data = json.loads(last_line)
            if data.get("event") == "observation":
                print(f"\nLatest observation: {data.get('temp_c', '?')}°C")
                print(f"Daily high: {data.get('daily_high_c', '?')}°C")
                print(f"Time: {data.get('ts', '?')}")
        except:
            print(f"\nCould not parse last line")
    else:
        print(f"\nNo log file at {log_path}")
    
    # Check paper trading DB
    db_path = r"C:\Users\Charl\Desktop\Cursor\weather-bot\paper_trading.db"
    if os.path.exists(db_path):
        print(f"\nPaper trading DB exists: {os.path.getsize(db_path)} bytes")
        
        # Try to read positions
        try:
            import sqlite3
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM positions WHERE status='OPEN'")
            open_positions = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM positions")
            total_positions = cursor.fetchone()[0]
            conn.close()
            print(f"Open positions: {open_positions}")
            print(f"Total positions: {total_positions}")
        except Exception as e:
            print(f"Could not read DB: {e}")
    else:
        print("\nPaper trading DB not created yet")
    
    # Check forecast
    forecast_path = r"C:\Users\Charl\Desktop\Cursor\weather-bot\tomorrow_forecast_chart.html"
    if os.path.exists(forecast_path):
        print(f"\nForecast chart available")
        print("Open tomorrow_forecast_chart.html to see forecast visualization")
    else:
        print("\nNo forecast chart found")
    
    print("\n" + "=" * 70)
    print("WHAT TO DO NOW:")
    print("=" * 70)
    print("1. Check if weather_monitor.py is running")
    print("   - Look for python processes with 'weather_monitor'")
    print("2. Check the log for recent observations")
    print("   - Last temperature readings")
    print("   - Current daily high")
    print("3. Monitor for signals:")
    print("   - Tier 1: When running high crosses brackets")
    print("   - Tier 2: At 9:00 based on forecast")
    print("   - Midday T2: At 12:00")
    print("   - Ceiling NO: After 16:00")
    print("   - Locked-IN YES: After 17:00")
    print("\nPress Ctrl+C to stop this script")
    print("=" * 70)

if __name__ == "__main__":
    check_live_status()