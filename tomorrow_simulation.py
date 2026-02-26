"""
Simulate tomorrow's (Feb 23) trading day based on Open-Meteo forecast.
Models temperature progression, bracket kills, and trading signals.
"""
import json
from datetime import datetime, date, timedelta, time
from zoneinfo import ZoneInfo

CET = ZoneInfo("Europe/Paris")
TOMORROW = date(2026, 2, 23)

# Configuration from weather_monitor.py
ROUNDING_BUFFER = 0.5
FORECAST_KILL_BUFFER = 4.0
UPPER_KILL_BUFFER = 5.0
MIDDAY_KILL_BUFFER = 2.5
OPENMETEO_BIAS = 1.0
LATE_DAY_HOUR = 16
LOCK_IN_HOUR = 17
CEIL_GAP = 2.0
MIN_YES_ALERT = 0.03

# Forecast data from Open-Meteo
FORECAST_HIGH = 14.4  # After bias correction
HOURLY_FORECAST = [
    {"hour": 0, "temp": 11.9}, {"hour": 1, "temp": 11.7}, {"hour": 2, "temp": 11.4},
    {"hour": 3, "temp": 11.1}, {"hour": 4, "temp": 10.8}, {"hour": 5, "temp": 10.5},
    {"hour": 6, "temp": 10.2}, {"hour": 7, "temp": 10.4}, {"hour": 8, "temp": 11.1},
    {"hour": 9, "temp": 12.7}, {"hour": 10, "temp": 13.1}, {"hour": 11, "temp": 13.5},
    {"hour": 12, "temp": 13.7}, {"hour": 13, "temp": 13.9}, {"hour": 14, "temp": 14.4},
    {"hour": 15, "temp": 14.1}, {"hour": 16, "temp": 13.4}, {"hour": 17, "temp": 13.1},
    {"hour": 18, "temp": 12.9}, {"hour": 19, "temp": 12.4}, {"hour": 20, "temp": 12.0},
    {"hour": 21, "temp": 11.9}, {"hour": 22, "temp": 11.7}, {"hour": 23, "temp": 11.5}
]

# Typical Polymarket brackets for Paris (based on past markets)
BRACKETS = [
    {"type": "floor", "value": 6, "label": "<=6C"},
    {"type": "floor", "value": 7, "label": "<=7C"},
    {"type": "floor", "value": 8, "label": "<=8C"},
    {"type": "floor", "value": 9, "label": "<=9C"},
    {"type": "floor", "value": 10, "label": "<=10C"},
    {"type": "exact", "value": 11, "label": "11C"},
    {"type": "exact", "value": 12, "label": "12C"},
    {"type": "exact", "value": 13, "label": "13C"},
    {"type": "exact", "value": 14, "label": "14C"},
    {"type": "ceiling", "value": 15, "label": ">=15C"},
    {"type": "ceiling", "value": 16, "label": ">=16C"},
    {"type": "ceiling", "value": 17, "label": ">=17C"},
    {"type": "ceiling", "value": 18, "label": ">=18C"},
    {"type": "ceiling", "value": 19, "label": ">=19C"}
]

# Simulated market prices (educated guesses based on forecast)
INITIAL_PRICES = {
    "<=6C": 0.001, "<=7C": 0.002, "<=8C": 0.005, "<=9C": 0.01, "<=10C": 0.02,
    "11C": 0.05, "12C": 0.10, "13C": 0.25, "14C": 0.40, ">=15C": 0.15,
    ">=16C": 0.05, ">=17C": 0.02, ">=18C": 0.01, ">=19C": 0.005
}

def simulate_temperature_progression():
    """Simulate temperature throughout the day based on forecast."""
    running_high = None
    daily_high = None
    observations = []
    
    for hour_data in HOURLY_FORECAST:
        hour = hour_data["hour"]
        temp = hour_data["temp"]
        
        if running_high is None or temp > running_high:
            running_high = temp
            daily_high = temp
        
        observations.append({
            "time": f"{hour:02d}:00",
            "temp": temp,
            "running_high": running_high,
            "daily_high": daily_high
        })
    
    return observations

def get_forecast_at_hour(target_hour):
    """Get forecast temperature at a specific hour."""
    for hour_data in HOURLY_FORECAST:
        if hour_data["hour"] == target_hour:
            return hour_data["temp"]
    return None

def calculate_bracket_status(bracket, daily_high, hour, forecast_high):
    """Calculate if a bracket is dead and by which signal type."""
    bracket_value = bracket["value"]
    bracket_type = bracket["type"]
    
    # Tier 1: Mathematical certainty (running high crossed bracket)
    if bracket_type == "floor":
        # Floor bracket (<=X°C)
        if daily_high >= bracket_value + ROUNDING_BUFFER:
            return "T1_DEAD", f"Running high {daily_high}C >= {bracket_value + ROUNDING_BUFFER}C"
    
    elif bracket_type == "exact":
        # Exact bracket (X°C)
        if daily_high >= bracket_value + ROUNDING_BUFFER:
            return "T1_DEAD", f"Running high {daily_high}C >= {bracket_value + ROUNDING_BUFFER}C"
    
    elif bracket_type == "ceiling":
        # Ceiling bracket (>=X°C) - can't be T1 killed
    
    # Tier 2: Forecast-based kills (9am only)
    if hour == 9 and forecast_high is not None:
        if bracket_type == "floor":
            gap = forecast_high - bracket_value
            if gap >= FORECAST_KILL_BUFFER:
                return "T2_DEAD", f"Forecast {forecast_high}C - bracket {bracket_value}C = {gap:.1f}C (>= {FORECAST_KILL_BUFFER}C)"
        
        elif bracket_type == "ceiling":
            gap = bracket_value - forecast_high
            if gap >= UPPER_KILL_BUFFER:
                return "T2_UPPER_DEAD", f"Bracket {bracket_value}C - forecast {forecast_high}C = {gap:.1f}C (>= {UPPER_KILL_BUFFER}C)"
    
    # Midday T2: Noon reassessment
    if hour == 12 and forecast_high is not None and daily_high is not None:
        if bracket_type == "floor":
            gap = daily_high - bracket_value
            if gap >= MIDDAY_KILL_BUFFER:
                return "MIDDAY_T2_DEAD", f"Running high {daily_high}C - bracket {bracket_value}C = {gap:.1f}C (>= {MIDDAY_KILL_BUFFER}C)"
        
        elif bracket_type == "ceiling":
            # Estimate final high based on remaining forecast
            remaining_hours = [h for h in HOURLY_FORECAST if h["hour"] > 12]
            if remaining_hours:
                remaining_max = max(h["temp"] for h in remaining_hours)
                estimated_final = max(daily_high, remaining_max)
                gap = bracket_value - estimated_final
                if gap >= MIDDAY_KILL_BUFFER:
                    return "MIDDAY_T2_DEAD", f"Bracket {bracket_value}C - estimated final {estimated_final:.1f}C = {gap:.1f}C (>= {MIDDAY_KILL_BUFFER}C)"
    
    # Ceiling NO: Late day (after 4pm)
    if hour >= LATE_DAY_HOUR and bracket_type == "ceiling" and daily_high is not None:
        gap = bracket_value - daily_high
        if gap >= CEIL_GAP:
            # Check guards (simplified)
            peak_hour = max(HOURLY_FORECAST, key=lambda x: x["temp"])["hour"]
            if hour >= peak_hour + 2:  # Wait 2 hours after peak
                return "CEIL_NO", f"Bracket {bracket_value}C - daily high {daily_high}C = {gap:.1f}C (>= {CEIL_GAP}C), after peak"
    
    # Locked-In YES: Late day (after 5pm) for exact brackets
    if hour >= LOCK_IN_HOUR and bracket_type == "exact" and daily_high is not None:
        if bracket_value - ROUNDING_BUFFER <= daily_high <= bracket_value + ROUNDING_BUFFER:
            return "LOCKED_YES", f"Daily high {daily_high}C locked in bracket {bracket_value}C"
    
    return "ALIVE", "Still possible"

def simulate_trading_day():
    """Main simulation function."""
    print("=" * 80)
    print("TOMORROW'S TRADING DAY SIMULATION (Feb 23, 2026)")
    print(f"Based on Open-Meteo forecast: {FORECAST_HIGH}C (after +{OPENMETEO_BIAS}C bias)")
    print("=" * 80)
    
    # Simulate temperature progression
    observations = simulate_temperature_progression()
    
    # Track bracket status
    bracket_status = {bracket["label"]: "ALIVE" for bracket in BRACKETS}
    bracket_kill_time = {}
    bracket_kill_reason = {}
    
    # Track trading signals
    signals = []
    
    print("\nTEMPERATURE PROGRESSION:")
    print("Time  Temp(C)  Running High  Daily High")
    print("-" * 40)
    
    key_hours = [0, 6, 9, 12, 14, 16, 18, 21, 23]
    for obs in observations:
        if obs["time"][:2].lstrip("0") in [str(h) for h in key_hours]:
            print(f"{obs['time']}    {obs['temp']:5.1f}      {obs['running_high']:11.1f}      {obs['daily_high']:10.1f}")
    
    print("\n" + "=" * 80)
    print("TRADING SIGNALS TIMELINE:")
    print("=" * 80)
    
    # Check at key hours
    check_hours = [9, 12, 14, 16, 17, 18, 21]
    
    for check_hour in check_hours:
        # Get temperature at this hour
        temp_data = next((obs for obs in observations if obs["time"] == f"{check_hour:02d}:00"), None)
        if not temp_data:
            continue
        
        daily_high = temp_data["daily_high"]
        hour_temp = temp_data["temp"]
        
        print(f"\n{check_hour:02d}:00 CET - Temp: {hour_temp:.1f}C, Daily High: {daily_high:.1f}C")
        print("-" * 60)
        
        hour_signals = []
        
        for bracket in BRACKETS:
            if bracket_status[bracket["label"]] != "ALIVE":
                continue
            
            status, reason = calculate_bracket_status(
                bracket, daily_high, check_hour, FORECAST_HIGH
            )
            
            if status != "ALIVE":
                bracket_status[bracket["label"]] = status
                bracket_kill_time[bracket["label"]] = f"{check_hour:02d}:00"
                bracket_kill_reason[bracket["label"]] = reason
                
                # Check if trade would be actionable
                yes_price = INITIAL_PRICES.get(bracket["label"], 0.01)
                if yes_price > MIN_YES_ALERT:
                    signal_type = status.split("_")[0]
                    if signal_type in ["T1", "T2", "MIDDAY", "CEIL", "LOCKED"]:
                        hour_signals.append({
                            "bracket": bracket["label"],
                            "type": status,
                            "reason": reason,
                            "yes_price": yes_price,
                            "profit": yes_price * 100  # $ per $100 bet
                        })
        
        if hour_signals:
            for signal in hour_signals:
                action = "BUY NO" if "DEAD" in signal["type"] or signal["type"] == "CEIL_NO" else "BUY YES"
                print(f"  {action} on {signal['bracket']}")
                print(f"    Signal: {signal['type']}")
                print(f"    Reason: {signal['reason']}")
                print(f"    YES price: {signal['yes_price']:.3f} → Profit: ${signal['profit']:.2f} per $100")
                signals.append(signal)
        else:
            print(f"  No actionable signals")
    
    print("\n" + "=" * 80)
    print("BRACKET STATUS SUMMARY:")
    print("=" * 80)
    
    print("\nBracket           Status          Killed At    Reason")
    print("-" * 70)
    
    for bracket in BRACKETS:
        label = bracket["label"]
        status = bracket_status[label]
        kill_time = bracket_kill_time.get(label, "-")
        reason = bracket_kill_reason.get(label, "-")
        
        if status == "ALIVE":
            print(f"{label:15} {status:15} {kill_time:12} {reason}")
        else:
            print(f"{label:15} {status:15} {kill_time:12} {reason[:50]}...")
    
    print("\n" + "=" * 80)
    print("TRADING SUMMARY:")
    print("=" * 80)
    
    if signals:
        total_profit = sum(s["profit"] for s in signals)
        print(f"\nTotal actionable signals: {len(signals)}")
        print(f"Total profit potential: ${total_profit:.2f} per $100 bets")
        
        print("\nSignal Type Breakdown:")
        signal_types = {}
        for signal in signals:
            sig_type = signal["type"].split("_")[0]
            signal_types[sig_type] = signal_types.get(sig_type, 0) + 1
        
        for sig_type, count in signal_types.items():
            type_profit = sum(s["profit"] for s in signals if s["type"].split("_")[0] == sig_type)
            print(f"  {sig_type}: {count} signals, ${type_profit:.2f} total profit")
        
        print("\nMost profitable opportunities:")
        sorted_signals = sorted(signals, key=lambda x: x["profit"], reverse=True)[:5]
        for i, signal in enumerate(sorted_signals, 1):
            print(f"  {i}. {signal['bracket']}: {signal['type']} (${signal['profit']:.2f})")
    else:
        print("\nNo actionable trading opportunities based on forecast")
        print("Market likely already efficient at these prices")
    
    print("\n" + "=" * 80)
    print("KEY INSIGHTS FOR TOMORROW:")
    print("=" * 80)
    
    print("\n1. TIER 2 OPPORTUNITIES (9:00):")
    print("   * Forecast high: 14.4C")
    print("   * Target brackets: <=6C, <=7C, <=8C, <=9C, <=10C")
    print("   * All have >=4.4C gap to forecast")
    print("   * IF market prices YES > 3%, execute Floor NO T2")
    
    print("\n2. MIDDAY REASSESSMENT (12:00):")
    print("   * Expected running high: ~13.7C")
    print("   * Check brackets: 11C, 12C (2.7C and 1.7C gap)")
    print("   * Might trigger Midday T2 if gap >= 2.5C")
    
    print("\n3. AFTERNOON PEAK (14:00-15:00):")
    print("   * Temperature peaks at 14.4C")
    print("   * 14C bracket becomes current range")
    print("   * Monitor for price inefficiencies")
    
    print("\n4. LATE DAY SIGNALS (16:00+):")
    print("   * Ceiling NO candidates: >=16C, >=17C")
    print("   * Need gap >= 2C + wait 2h after peak (after 16:00)")
    print("   * Locked-In YES: 14C bracket if still < 80% at 17:00")
    
    print("\n5. RISK MANAGEMENT:")
    print("   * Always wait for 9am dynamic bias check")
    print("   * Monitor SYNOP trend (rising = block signals)")
    print("   * Minimum 3% YES price for actionable edge")
    print("   * Check OM remaining max before late-day signals")
    
    print("\n" + "=" * 80)
    print("SIMULATION ASSUMPTIONS & LIMITATIONS:")
    print("=" * 80)
    print("1. Based on Open-Meteo forecast (accuracy ~±1°C)")
    print("2. Assumes market follows typical pricing patterns")
    print("3. Doesn't account for news events or unusual weather")
    print("4. Real trading requires real-time price checks")
    print("5. Dynamic bias at 9am could adjust forecast up/down")

if __name__ == "__main__":
    simulate_trading_day()