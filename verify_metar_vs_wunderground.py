#!/usr/bin/env python3
"""Verify METAR data matches Weather Underground (Polymarket's source of truth)."""
import json
import urllib.request
from datetime import datetime, timedelta
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
    
    # Parse date
    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    date_str = dt.strftime("%Y-%m-%d")
    
    daily_high = entry.get("daily_high_c")
    if daily_high is not None:
        if date_str not in our_daily_highs:
            our_daily_highs[date_str] = daily_high
        else:
            our_daily_highs[date_str] = max(our_daily_highs[date_str], daily_high)

# Check recent dates
dates_to_check = [
    "2026-02-25",
    "2026-02-24", 
    "2026-02-23",
    "2026-02-22",
    "2026-02-21",
    "2026-02-20",
    "2026-02-19",
    "2026-02-18",
    "2026-02-17",
    "2026-02-16"
]

print("Fetching Weather Underground data (Polymarket's source of truth)...\n")

results = []
for date_str in dates_to_check:
    if date_str not in our_daily_highs:
        continue
    
    our_high = our_daily_highs[date_str]
    
    # Fetch Weather Underground page
    year, month, day = date_str.split("-")
    url = f"https://www.wunderground.com/history/daily/fr/mauregard/LFPG/date/{year}-{month.lstrip('0')}-{day.lstrip('0')}"
    
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as response:
            html = response.read().decode('utf-8')
        
        # Try to extract high temperature from HTML
        # Look for patterns like "High: 21°C" or similar
        wunderground_high = None
        
        # Method 1: Look for "High" label followed by temperature
        import re
        patterns = [
            r'High[:\s]+(\d+)°C',
            r'High[:\s]+(\d+)&deg;C',
            r'"high"[:\s]*(\d+)',
            r'Max[:\s]+(\d+)°C',
        ]
        
        for pattern in patterns:
            match = re.search(pattern, html, re.IGNORECASE)
            if match:
                wunderground_high = int(match.group(1))
                break
        
        if wunderground_high is None:
            # Try to find temperature table data
            # This is a fallback - WU structure may vary
            match = re.search(r'Temperature.*?(\d+)°C', html, re.DOTALL)
            if match:
                wunderground_high = int(match.group(1))
        
        if wunderground_high is not None:
            diff = our_high - wunderground_high
            match_status = "✅ MATCH" if diff == 0 else f"❌ DIFF: {diff:+.1f}°C"
            results.append({
                "date": date_str,
                "our_metar": our_high,
                "wunderground": wunderground_high,
                "diff": diff,
                "status": match_status
            })
        else:
            results.append({
                "date": date_str,
                "our_metar": our_high,
                "wunderground": "N/A",
                "diff": None,
                "status": "⚠️ Could not parse WU"
            })
    
    except Exception as e:
        results.append({
            "date": date_str,
            "our_metar": our_high,
            "wunderground": "ERROR",
            "diff": None,
            "status": f"⚠️ Error: {str(e)[:50]}"
        })

# Print results
print("=" * 80)
print(f"{'Date':<12} {'Our METAR':<12} {'WUnderground':<15} {'Diff':<10} {'Status'}")
print("=" * 80)

for r in results:
    wu_str = f"{r['wunderground']}°C" if isinstance(r['wunderground'], (int, float)) else r['wunderground']
    diff_str = f"{r['diff']:+.1f}°C" if r['diff'] is not None else "N/A"
    print(f"{r['date']:<12} {r['our_metar']:.1f}°C{'':<7} {wu_str:<15} {diff_str:<10} {r['status']}")

print("=" * 80)

# Summary
matches = sum(1 for r in results if r['diff'] == 0)
mismatches = sum(1 for r in results if r['diff'] is not None and r['diff'] != 0)
unparsed = sum(1 for r in results if r['diff'] is None)

print(f"\nSummary:")
print(f"  Matches: {matches}")
print(f"  Mismatches: {mismatches}")
print(f"  Could not verify: {unparsed}")

if mismatches > 0:
    print(f"\n⚠️ WARNING: Found {mismatches} mismatches between METAR and Weather Underground!")
    print("This could affect trading accuracy if Polymarket uses WU as source of truth.")
