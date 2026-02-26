#!/usr/bin/env python3
"""Verify METAR data matches Weather Underground via API."""
import json
import urllib.request
from datetime import datetime
from collections import defaultdict

# Read our METAR log data
with open("weather_log.jsonl", "r", encoding="utf-8") as f:
    logs = [json.loads(line) for line in f if line.strip()]

# Extract daily highs from our METAR data
our_daily_highs = {}
for entry in logs:
    if entry.get("event") != "observation":
        continue
    
    ts = entry.get("ts", "")
    if not ts:
        continue
    
    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    date_str = dt.strftime("%Y-%m-%d")
    
    daily_high = entry.get("daily_high_c")
    if daily_high is not None:
        if date_str not in our_daily_highs:
            our_daily_highs[date_str] = daily_high
        else:
            our_daily_highs[date_str] = max(our_daily_highs[date_str], daily_high)

# Check recent dates via WU API
dates_to_check = [
    "2026-02-25",
    "2026-02-24", 
    "2026-02-23",
    "2026-02-22",
    "2026-02-21",
]

print("Verifying METAR vs Weather Underground (Polymarket source)\n")
print("="*80)
print(f"{'Date':<12} {'Our METAR':<12} {'WU API':<12} {'Diff':<10} {'Status'}")
print("="*80)

results = []
for date_str in dates_to_check:
    if date_str not in our_daily_highs:
        continue
    
    our_high = our_daily_highs[date_str]
    year, month, day = date_str.split("-")
    
    # Call WU API
    url = f"https://api.weather.com/v1/location/LFPG:9:FR/observations/historical.json?apiKey=e1f10a1e78da46f5b10a1e78da96f525&units=m&startDate={year}{month}{day}"
    
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as response:
            data = json.loads(response.read())
        
        # Extract max temperature from observations
        observations = data.get("observations", [])
        if observations:
            temps = [obs.get("temp") for obs in observations if obs.get("temp") is not None]
            if temps:
                wu_high = max(temps)
                diff = our_high - wu_high
                
                if abs(diff) < 0.5:  # Allow 0.5°C tolerance for rounding
                    status = "✅ MATCH"
                else:
                    status = f"❌ DIFF: {diff:+.1f}°C"
                
                print(f"{date_str:<12} {our_high:.1f}°C{'':<7} {wu_high:.1f}°C{'':<7} {diff:+.1f}°C{'':<5} {status}")
                results.append({"date": date_str, "match": abs(diff) < 0.5, "diff": diff})
            else:
                print(f"{date_str:<12} {our_high:.1f}°C{'':<7} {'No temps':<12} {'N/A':<10} ⚠️ No data")
        else:
            print(f"{date_str:<12} {our_high:.1f}°C{'':<7} {'No obs':<12} {'N/A':<10} ⚠️ No observations")
    
    except Exception as e:
        print(f"{date_str:<12} {our_high:.1f}°C{'':<7} {'ERROR':<12} {'N/A':<10} ⚠️ {str(e)[:30]}")

print("="*80)

# Summary
if results:
    matches = sum(1 for r in results if r["match"])
    total = len(results)
    print(f"\nSummary: {matches}/{total} days match (within 0.5°C tolerance)")
    
    if matches == total:
        print("✅ Perfect correlation! METAR data matches Weather Underground.")
        print("   Our trading signals are based on the same data Polymarket uses for resolution.")
    else:
        print("⚠️ WARNING: Some discrepancies found!")
        print("   This could affect trading accuracy.")
        for r in results:
            if not r["match"]:
                print(f"     {r['date']}: {r['diff']:+.1f}°C difference")
