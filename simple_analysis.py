"""
Simple analysis of today's data and tomorrow's forecast for Paris temperature markets.
"""
import json
from datetime import datetime, date, timedelta, timezone
from zoneinfo import ZoneInfo
import urllib.request
import re

CET = ZoneInfo("Europe/Paris")
TODAY = date(2026, 2, 22)
TOMORROW = date(2026, 2, 23)

# Configuration
CDG_LAT, CDG_LON = 49.0097, 2.5479
OPENMETEO_BIAS = 1.0  # Open-Meteo underforecasts by ~1°C

def fetch_tomorrow_forecast():
    """Fetch tomorrow's forecast high from Open-Meteo."""
    url = (
        f"https://api.open-meteo.com/v1/forecast?"
        f"latitude={CDG_LAT}&longitude={CDG_LON}"
        f"&daily=temperature_2m_max"
        f"&timezone=Europe/Paris"
        f"&forecast_days=1"
    )
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            data = json.loads(r.read())
        daily = data.get("daily", {})
        maxes = daily.get("temperature_2m_max", [])
        if maxes and maxes[0] is not None:
            raw = float(maxes[0])
            adjusted = round(raw + OPENMETEO_BIAS, 1)
            return {"raw": raw, "adjusted": adjusted, "bias": OPENMETEO_BIAS}
    except Exception as e:
        print(f"Error fetching forecast: {e}")
    return None

def fetch_tomorrow_hourly():
    """Fetch tomorrow's hourly forecast from Open-Meteo."""
    url = (
        f"https://api.open-meteo.com/v1/forecast?"
        f"latitude={CDG_LAT}&longitude={CDG_LON}"
        f"&hourly=temperature_2m"
        f"&timezone=Europe/Paris"
        f"&forecast_days=1"
    )
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            data = json.loads(r.read())
        hourly = data.get("hourly", {})
        times = hourly.get("time", [])
        temps = hourly.get("temperature_2m", [])
        
        points = []
        for t, temp in zip(times, temps):
            if temp is not None:
                dt = datetime.fromisoformat(t)
                hour = dt.hour + dt.minute / 60
                points.append({"hour": hour, "temp": temp, "time": dt.strftime("%H:%M")})
        
        # Find peak hour
        if points:
            peak = max(points, key=lambda x: x["temp"])
            return {
                "points": points,
                "peak_hour": peak["hour"],
                "peak_temp": peak["temp"],
                "peak_time": peak["time"]
            }
    except Exception as e:
        print(f"Error fetching hourly forecast: {e}")
    return None

def analyze_today_data():
    """Analyze today's data from the log file."""
    today_high = None
    observations = []
    
    try:
        with open(r"C:\Users\Charl\Desktop\Cursor\weather-bot\weather_log.jsonl", "r", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    data = json.loads(line)
                    
                    # Check if it's today's data
                    ts = datetime.fromisoformat(data.get("ts", "")).astimezone(CET)
                    if ts.date() != TODAY:
                        continue
                    
                    if data.get("event") == "observation":
                        temp = data.get("temp_c")
                        daily_high = data.get("daily_high_c")
                        hour = ts.hour + ts.minute / 60
                        
                        observations.append({
                            "time": ts.strftime("%H:%M"),
                            "hour": hour,
                            "temp": temp,
                            "daily_high": daily_high,
                            "synop": data.get("synop_temp_c"),
                            "openmeteo": data.get("openmeteo_temp_c"),
                            "trend": data.get("openmeteo_trend")
                        })
                        
                        if daily_high and (today_high is None or daily_high > today_high):
                            today_high = daily_high
                            
                except json.JSONDecodeError:
                    continue
    except FileNotFoundError:
        print("Log file not found")
    
    # Find actual high from observations
    actual_high = None
    if observations:
        actual_high = max(obs["daily_high"] for obs in observations if obs["daily_high"] is not None)
    
    return {
        "actual_high": actual_high,
        "observations": observations[-50:],  # Last 50 observations
        "total_observations": len(observations)
    }

def main():
    print("=" * 70)
    print("PARIS TEMPERATURE MARKET ANALYSIS")
    print(f"Today: {TODAY.strftime('%Y-%m-%d')} | Tomorrow: {TOMORROW.strftime('%Y-%m-%d')}")
    print("=" * 70)
    
    # 1. Analyze today's data
    print("\nTODAY'S DATA (Feb 22):")
    today_data = analyze_today_data()
    
    if today_data["actual_high"] is not None:
        print(f"  * Daily high: {today_data['actual_high']}C")
        print(f"  * Observations: {today_data['total_observations']} records")
        
        # Show recent trend
        if today_data["observations"]:
            recent = today_data["observations"][-5:]
            print(f"  * Recent trend (last {len(recent)} readings):")
            for obs in recent:
                trend = f" ({obs['trend']})" if obs.get('trend') else ""
                print(f"    {obs['time']}: {obs['temp']}C (high: {obs['daily_high']}C){trend}")
    else:
        print("  * No data available for today")
    
    # 2. Fetch tomorrow's forecast
    print("\nTOMORROW'S FORECAST (Feb 23):")
    forecast = fetch_tomorrow_forecast()
    hourly = fetch_tomorrow_hourly()
    
    if forecast:
        print(f"  * Open-Meteo raw: {forecast['raw']}C")
        print(f"  * With bias correction (+{forecast['bias']}C): {forecast['adjusted']}C")
        
        if hourly:
            print(f"  * Peak temperature: {hourly['peak_temp'] + OPENMETEO_BIAS:.1f}C at {hourly['peak_time']}")
            print(f"  * Hourly forecast available: {len(hourly['points'])} points")
            
            # Show temperature progression
            print(f"  * Temperature progression (key hours):")
            key_hours = [6, 9, 12, 15, 18, 21]
            for target_hour in key_hours:
                closest = min(hourly["points"], key=lambda x: abs(x["hour"] - target_hour))
                if abs(closest["hour"] - target_hour) <= 1.5:
                    print(f"    {closest['time']}: {closest['temp'] + OPENMETEO_BIAS:.1f}C")
    else:
        print("  * Forecast not available")
    
    # 3. Analyze potential brackets
    if forecast:
        forecast_val = forecast['adjusted']
        print(f"\nPOTENTIAL BRACKETS FOR TOMORROW:")
        print(f"  * Forecast center: {forecast_val}C")
        
        # Generate brackets
        base = int(round(forecast_val))
        
        print(f"  * Lower brackets (<=XC or XC):")
        for i in range(max(0, base - 8), base + 1):
            gap = forecast_val - i
            status = "SAFE" if gap >= 4.0 else "WATCH" if gap >= 2.0 else "RISKY"
            label = f"<={i}C" if i < base else f"{i}C"
            print(f"    {label}: gap={gap:.1f}C [{status}]")
        
        print(f"  * Upper brackets (>=XC):")
        for i in range(base + 1, base + 6):
            gap = i - forecast_val
            status = "SAFE" if gap >= 5.0 else "WATCH" if gap >= 3.0 else "RISKY"
            print(f"    >={i}C: gap={gap:.1f}C [{status}]")
        
        # Tier 2 opportunities
        print(f"\nTIER 2 TRADING OPPORTUNITIES:")
        opportunities = []
        
        # Lower brackets with >=4C gap
        lower_targets = []
        for i in range(max(0, base - 8), base + 1):
            gap = forecast_val - i
            if gap >= 4.0:
                lower_targets.append((i, gap))
        
        if lower_targets:
            print(f"  * FLOOR NO T2 targets (9am forecast):")
            for target, gap in lower_targets:
                label = f"<={target}C" if target < base else f"{target}C"
                print(f"    - {label}: gap={gap:.1f}C (need >=4.0C)")
        
        # Upper brackets with >=5C gap  
        upper_targets = []
        for i in range(base + 1, base + 6):
            gap = i - forecast_val
            if gap >= 5.0:
                upper_targets.append((i, gap))
        
        if upper_targets:
            print(f"  * T2 UPPER targets (9am forecast):")
            for target, gap in upper_targets:
                print(f"    - >={target}C: gap={gap:.1f}C (need >=5.0C)")
                print(f"      Requires: No OM underforecast in morning")
        
        if not lower_targets and not upper_targets:
            print(f"  * No clear Tier 2 opportunities based on forecast alone")
            print(f"  * Need to check dynamic bias at 9am tomorrow")
    
    # 4. Trading strategy recommendations
    print("\nTRADING STRATEGY FOR TOMORROW:")
    
    if forecast:
        forecast_val = forecast["adjusted"]
        
        print("  1. FLOOR NO T1 (Mathematical Certainty):")
        print("     * Wait for running high to cross bracket thresholds")
        print("     * Zero risk - temperature can't go back down")
        print("     * Execute as soon as METAR confirms")
        
        print("\n  2. FLOOR NO T2 (9am Forecast):")
        print("     * At 9am CET, check Open-Meteo forecast")
        print("     * Buy NO on brackets where forecast - bracket >= 4C")
        print(f"     * Potential targets: brackets <={int(forecast_val - 4)}C")
        
        print("\n  3. T2 UPPER (9am Forecast - Upper Brackets):")
        print("     * At 9am, check for upper brackets far above forecast")
        print("     * Requires: bracket - forecast >= 5C AND no OM underforecast")
        print(f"     * Potential targets: brackets >={int(forecast_val + 5)}C")
        
        print("\n  4. MIDDAY T2 (Noon Reassessment):")
        print("     * At 12pm, re-evaluate with 6h of real data")
        print("     * Use running high + OM remaining trajectory")
        print("     * Tighter 2.5C buffer")
        
        print("\n  5. GUARDED LATE-DAY SIGNALS:")
        print("     * After 4pm: Ceiling NO if gap >= 2C + 5 guards pass")
        print("     * After 5pm: Locked-In YES if bracket locked in + 5 guards")
        print("     * Guards: OM peak hour, remaining max, trend, etc.")
        
        if hourly:
            print(f"\nIMPORTANT NOTE:")
            print(f"   * OM predicts peak at {hourly['peak_time']}")
            print(f"   * Ceiling NO should wait until after {int(hourly['peak_hour']) + 2}:00")
            print(f"   * Monitor SYNOP trend for rising temperatures")
    else:
        print("  * No forecast available - monitor real-time data tomorrow")
    
    print("\n" + "=" * 70)
    print("KEY TAKEAWAYS:")
    print("1. Floor NO strategies (T1 + T2) are mathematically safe")
    print("2. Always wait for 9am dynamic bias calculation")
    print("3. Monitor multiple data sources (METAR, SYNOP, Open-Meteo)")
    print("4. Use safety guards for late-day signals")
    print("=" * 70)

if __name__ == "__main__":
    main()