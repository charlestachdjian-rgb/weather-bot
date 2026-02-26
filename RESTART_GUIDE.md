# Restart Guide - Processes to Resume

## Current Running Processes (Before Restart)

1. **Weather Bot (Paris)** - Terminal 7
   - Location: `weather-bot/`
   - Command: `python weather_monitor.py`
   - Log file: `weather_log.jsonl`
   - Status: Enhanced version with all 6 tasks complete

2. **AutoHero Scanner** - Terminal 3
   - Location: `autohero-scanner/`
   - Command: `python autohero_scanner.py`
   - Log file: `data/scanner.log`
   - Status: Monitoring ~1,818 cars for price drops

## Commands to Restart After Reboot

### Option 1: Using Kiro (Recommended)

Open Kiro and run these commands:

```
Start weather bot for Paris
Start autohero scanner
```

Or use the tool directly:

```python
# Start Weather Bot (Paris)
controlPwshProcess(
    action="start",
    command="python weather_monitor.py",
    cwd="weather-bot"
)

# Start AutoHero Scanner
controlPwshProcess(
    action="start",
    command="python autohero_scanner.py",
    cwd="autohero-scanner"
)
```

### Option 2: Manual Terminal Commands

Open two separate terminals:

**Terminal 1 - Weather Bot:**
```bash
cd C:\Users\Charl\Desktop\Cursor\weather-bot
python weather_monitor.py
```

**Terminal 2 - AutoHero Scanner:**
```bash
cd C:\Users\Charl\Desktop\Cursor\autohero-scanner
python autohero_scanner.py
```

## Verification After Restart

### Check Weather Bot is Running
```python
# In Kiro
listProcesses()
```

Expected output should show:
- `python weather_monitor.py` in weather-bot (running)
- `python autohero_scanner.py` in autohero-scanner (running)

### Check Weather Bot Output
```python
# In Kiro
getProcessOutput(terminalId="<weather_bot_id>", lines=30)
```

Should show:
- "PARIS TEMPERATURE MARKET — ENHANCED 5-LAYER STRATEGY"
- "Layer 5: Guarded Ceiling NO / Lock-In YES (6 safeguards)"
- Current temperature readings
- Market snapshots

### Check AutoHero Scanner Output
```python
# In Kiro
getProcessOutput(terminalId="<autohero_id>", lines=30)
```

Should show:
- "Starting scan..."
- "Found X cars matching filters"
- Scan completion messages

## Current Status Summary

### Weather Bot (Paris)
- **Balance:** $36.05 (started with $30)
- **Profit:** +$6.05 from Feb 25
- **Enhancements:**
  - ✅ MIN_YES threshold: 1% (was 3%)
  - ✅ Daily summary logging at midnight
  - ✅ Tight T2 buffer (3.5°C) dormant data collection
  - ✅ 6th guard added (peak-reached check)
- **Data collected:** Feb 16-26 (11 days)

### AutoHero Scanner
- **Tracking:** ~1,818 cars
- **Filters:** Max €25k, no diesel, 100+ HP, <150k km
- **Scan frequency:** Every ~6 minutes
- **Alert threshold:** ≥5% price drop
- **Telegram:** Configured and tested ✅

## Multi-City Expansion (Ready to Deploy)

After restart, you can optionally start additional city monitors:

### London Monitor
```python
controlPwshProcess(
    action="start",
    command="python weather_monitor.py --city london",
    cwd="weather-bot"
)
```

### NYC Monitor
```python
controlPwshProcess(
    action="start",
    command="python weather_monitor.py --city nyc",
    cwd="weather-bot"
)
```

**Note:** weather_monitor.py needs modification to accept --city argument first (see MULTICITY_SETUP.md)

## Files to Check After Restart

### Weather Bot Logs
- `weather-bot/weather_log.jsonl` - All observations and signals
- `weather-bot/paper_trading.db` - Position history and P&L

### AutoHero Logs
- `autohero-scanner/data/scanner.log` - Scan history
- `autohero-scanner/data/prices.json` - Price database

## Troubleshooting

### If Weather Bot Doesn't Start
1. Check Python is in PATH: `python --version`
2. Check dependencies: `pip install -r requirements.txt`
3. Check .env file exists (optional for Telegram)

### If AutoHero Scanner Doesn't Start
1. Check dependencies: `pip install -r requirements.txt`
2. Check .env file has Telegram credentials
3. Verify internet connection

### If Processes Stop Unexpectedly
```python
# Check process status
listProcesses()

# Check process output for errors
getProcessOutput(terminalId="<id>", lines=100)
```

## Git Status

All changes are committed and pushed to GitHub:
- Repository: https://github.com/charlestachdjian-rgb/weather-bot
- Latest commit: `efa4275` - Tasks 3 & 4: Multi-city infrastructure + fixed backtest collector

## Quick Reference

**List all processes:**
```python
listProcesses()
```

**Stop a process:**
```python
controlPwshProcess(action="stop", terminalId="<id>")
```

**Check process output:**
```python
getProcessOutput(terminalId="<id>", lines=50)
```

---

**Last Updated:** Feb 26, 2026, 23:00 CET  
**Ready for restart:** Yes  
**Processes to resume:** 2 (Weather Bot + AutoHero Scanner)
