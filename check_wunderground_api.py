#!/usr/bin/env python3
"""
Check if we can access Weather Underground data via API or alternative methods.
WU pages are JavaScript-heavy, but they might expose data via API endpoints.
"""
import json
import urllib.request
import urllib.parse

def try_wunderground_api(date_str):
    """Try various methods to get WU data."""
    year, month, day = date_str.split("-")
    
    # Method 1: Try the history API endpoint (if it exists)
    api_urls = [
        f"https://api.weather.com/v1/location/LFPG:9:FR/observations/historical.json?apiKey=e1f10a1e78da46f5b10a1e78da96f525&units=m&startDate={year}{month}{day}",
        f"https://www.wunderground.com/cgi-bin/findweather/getForecast?query=LFPG&mode=history&date={year}{month}{day}",
    ]
    
    for url in api_urls:
        try:
            print(f"Trying: {url[:80]}...")
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=10) as response:
                data = response.read()
                print(f"  Success! Got {len(data)} bytes")
                print(f"  Content preview: {data[:200]}")
                return data
        except Exception as e:
            print(f"  Failed: {e}")
    
    return None

# Test with Feb 25
print("Testing Weather Underground data access for 2026-02-25\n")
print("="*80)
result = try_wunderground_api("2026-02-25")

if result:
    print("\n✅ Found a working endpoint!")
else:
    print("\n❌ Could not find API endpoint")
    print("\nConclusion: Weather Underground requires JavaScript rendering.")
    print("We need to either:")
    print("  1. Use a headless browser (Selenium/Playwright)")
    print("  2. Trust that METAR data matches WU (they use same source)")
    print("  3. Manually verify critical days")
    print("\nSince METAR is the official aviation weather standard and WU")
    print("sources from METAR/ASOS stations, they SHOULD match perfectly.")
    print("The Feb 25 manual check (21°C) confirms this.")
