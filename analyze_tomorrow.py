"""
Analyze today's weather data and tomorrow's forecast for Paris temperature markets.
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

# Open-Meteo URLs
OPENMETEO_FORECAST_URL = (
    f"https://api.open-meteo.com/v1/forecast?"
    f"latitude={CDG_LAT}&longitude={CDG_LON}"
    f"&daily=temperature_2m_max"
    f"&timezone=Europe/Paris"
    f"&forecast_days=1"
)

OPENMETEO_HOURLY_URL = (
    f"https://api.open-meteo.com/v1/forecast?"
    f"latitude={CDG_LAT}&longitude={CDG_LON}"
    f"&hourly=temperature_2m"
    f"&timezone=Europe/Paris"
    f"&forecast_days=1"
)

def fetch_tomorrow_forecast():
    """Fetch tomorrow's forecast high from Open-Meteo."""
    try:
        with urllib.request.urlopen(OPENMETEO_FORECAST_URL, timeout=10) as r:
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
    try:
        with urllib.request.urlopen(OPENMETEO_HOURLY_URL, timeout=10) as r:
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

def predict_brackets(forecast_high):
    """Predict which temperature brackets might be relevant tomorrow."""
    if forecast_high is None:
        return []
    
    # Typical Polymarket brackets for Paris
    forecast_val = forecast_high["adjusted"]
    
    # Generate potential brackets
    brackets = []
    base = int(round(forecast_val))
    
    # Lower brackets
    for i in range(max(0, base - 8), base + 1):
        brackets.append({
            "type": "lower",
            "value": i,
            "label": f"≤{i}°C" if i < base else f"{i}°C",
            "gap_to_forecast": forecast_val - i
        })
    
    # Upper brackets
    for i in range(base + 1, base + 6):
        brackets.append({
            "type": "upper",
            "value": i,
            "label": f"≥{i}°C",
            "gap_to_forecast": i - forecast_val
        })
    
    return brackets

def analyze_tier2_opportunities(brackets, forecast_high):
    """Analyze potential Tier 2 opportunities for tomorrow."""
    if forecast_high is None:
        return []
    
    forecast_val = forecast_high["adjusted"]
    opportunities = []
    
    FORECAST_KILL_BUFFER = 4.0  # °C buffer for Tier 2
    UPPER_KILL_BUFFER = 5.0     # °C buffer for T2 Upper
    
    for bracket in brackets:
        if bracket["type"] == "lower" and bracket["gap_to_forecast"] >= FORECAST_KILL_BUFFER:
            opportunities.append({
                "bracket": bracket["label"],
                "type": "FLOOR_NO_T2",
                "forecast_gap": bracket["gap_to_forecast"],
                "required_buffer": FORECAST_KILL_BUFFER,
                "confidence": "HIGH" if bracket["gap_to_forecast"] >= 6.0 else "MEDIUM",
                "action": "Buy NO at 9am if YES > 3%"
            })
        
        elif bracket["type"] == "upper" and bracket["gap_to_forecast"] >= UPPER_KILL_BUFFER:
            opportunities.append({
                "bracket": bracket["label"],
                "type": "T2_UPPER",
                "forecast_gap": bracket["gap_to_forecast"],
                "required_buffer": UPPER_KILL_BUFFER,
                "confidence": "MEDIUM" if bracket["gap_to_forecast"] >= 7.0 else "LOW",
                "action": "Buy NO at 9am if YES > 3% AND no OM underforecast"
            })
    
    return opportunities

def main():
    print("=" * 70)
    print("PARIS TEMPERATURE MARKET ANALYSIS")
    print(f"Today: {TODAY.strftime('%Y-%m-%d')} | Tomorrow: {TOMORROW.strftime('%Y-%m-%d')}")
    print("=" * 70)
    
    # 1. Analyze today's data
    print("\nTODAY'S DATA (Feb 22):")
    today_data = analyze_today_data()
    
    if today_data["actual_high"] is not None:
        print(f"  • Daily high: {today_data['actual_high']}°C")
        print(f"  • Observations: {today_data['total_observations']} records")
        
        # Show recent trend
        if today_data["observations"]:
            recent = today_data["observations"][-5:]
            print(f"  • Recent trend (last {len(recent)} readings):")
            for obs in recent:
                trend = f" ({obs['trend']})" if obs.get('trend') else ""
                print(f"    {obs['time']}: {obs['temp']}°C (high: {obs['daily_high']}°C){trend}")
    else:
        print("  • No data available for today")
    
    # 2. Fetch tomorrow's forecast
    print("\nTOMORROW'S FORECAST (Feb 23):")
    forecast = fetch_tomorrow_forecast()
    hourly = fetch_tomorrow_hourly()
    
    if forecast:
        print(f"  • Open-Meteo raw: {forecast['raw']}°C")
        print(f"  • With bias correction (+{forecast['bias']}°C): {forecast['adjusted']}°C")
        
        if hourly:
            print(f"  • Peak temperature: {hourly['peak_temp'] + OPENMETEO_BIAS:.1f}°C at {hourly['peak_time']}")
            print(f"  • Hourly forecast available: {len(hourly['points'])} points")
            
            # Show temperature progression
            print(f"  • Temperature progression (key hours):")
            key_hours = [6, 9, 12, 15, 18, 21]
            for target_hour in key_hours:
                closest = min(hourly["points"], key=lambda x: abs(x["hour"] - target_hour))
                if abs(closest["hour"] - target_hour) <= 1.5:
                    print(f"    {closest['time']}: {closest['temp'] + OPENMETEO_BIAS:.1f}°C")
    else:
        print("  • Forecast not available")
    
    # 3. Analyze potential brackets
    if forecast:
        print("\nPOTENTIAL BRACKETS FOR TOMORROW:")
        brackets = predict_brackets(forecast)
        
        # Group by type
        lower_brackets = [b for b in brackets if b["type"] == "lower"]
        upper_brackets = [b for b in brackets if b["type"] == "upper"]
        
        print(f"  • Forecast center: {forecast['adjusted']}°C")
        print(f"  • Lower brackets (<=X°C or X°C):")
        for bracket in lower_brackets[-8:]:  # Show closest 8
            buffer = bracket["gap_to_forecast"]
            status = "SAFE" if buffer >= 4.0 else "WATCH" if buffer >= 2.0 else "RISKY"
            print(f"    {bracket['label'].replace('≤', '<=').replace('°C', 'C')}: gap={buffer:.1f}C [{status}]")
        
        print(f"  • Upper brackets (>=X°C):")
        for bracket in upper_brackets[:5]:  # Show first 5
            buffer = bracket["gap_to_forecast"]
            status = "SAFE" if buffer >= 5.0 else "WATCH" if buffer >= 3.0 else "RISKY"
            print(f"    {bracket['label'].replace('≥', '>=').replace('°C', 'C')}: gap={buffer:.1f}C [{status}]")
    
    # 4. Tier 2 opportunities
    if forecast:
        print("\n💰 TIER 2 TRADING OPPORTUNITIES:")
        opportunities = analyze_tier2_opportunities(brackets, forecast)
        
        if opportunities:
            print(f"  • Found {len(opportunities)} potential Tier 2 opportunities:")
            for opp in opportunities:
                conf_color = "🟢" if opp["confidence"] == "HIGH" else "🟡" if opp["confidence"] == "MEDIUM" else "🔴"
                print(f"    {conf_color} {opp['bracket']}: {opp['type']}")
                print(f"      Gap: {opp['forecast_gap']:.1f}°C (need ≥{opp['required_buffer']}°C)")
                print(f"      Action: {opp['action']}")
        else:
            print("  • No clear Tier 2 opportunities based on forecast alone")
            print("  • Need to check dynamic bias at 9am tomorrow")
    
    # 5. Trading strategy recommendations
    print("\n🎯 TRADING STRATEGY FOR TOMORROW:")
    
    if forecast:
        forecast_val = forecast["adjusted"]
        
        print("  1. FLOOR NO T1 (Mathematical Certainty):")
        print("     • Wait for running high to cross bracket thresholds")
        print("     • Zero risk - temperature can't go back down")
        print("     • Execute as soon as METAR confirms")
        
        print("\n  2. FLOOR NO T2 (9am Forecast):")
        print("     • At 9am CET, check Open-Meteo forecast")
        print("     • Buy NO on brackets where forecast - bracket ≥ 4°C")
        print(f"     • Potential targets: brackets ≤{int(forecast_val - 4)}°C")
        
        print("\n  3. T2 UPPER (9am Forecast - Upper Brackets):")
        print("     • At 9am, check for upper brackets far above forecast")
        print("     • Requires: bracket - forecast ≥ 5°C AND no OM underforecast")
        print(f"     • Potential targets: brackets ≥{int(forecast_val + 5)}°C")
        
        print("\n  4. MIDDAY T2 (Noon Reassessment):")
        print("     • At 12pm, re-evaluate with 6h of real data")
        print("     • Use running high + OM remaining trajectory")
        print("     • Tighter 2.5°C buffer")
        
        print("\n  5. GUARDED LATE-DAY SIGNALS:")
        print("     • After 4pm: Ceiling NO if gap ≥ 2°C + 5 guards pass")
        print("     • After 5pm: Locked-In YES if bracket locked in + 5 guards")
        print("     • Guards: OM peak hour, remaining max, trend, etc.")
        
        if hourly:
            print(f"\n⚠️  IMPORTANT NOTE:")
            print(f"   • OM predicts peak at {hourly['peak_time']}")
            print(f"   • Ceiling NO should wait until after {int(hourly['peak_hour']) + 2}:00")
            print(f"   • Monitor SYNOP trend for rising temperatures")
    else:
        print("  • No forecast available - monitor real-time data tomorrow")
    
    print("\n" + "=" * 70)
    print("KEY TAKEAWAYS:")
    print("1. Floor NO strategies (T1 + T2) are mathematically safe")
    print("2. Always wait for 9am dynamic bias calculation")
    print("3. Monitor multiple data sources (METAR, SYNOP, Open-Meteo)")
    print("4. Use safety guards for late-day signals")
    print("=" * 70)

if __name__ == "__main__":
    main()