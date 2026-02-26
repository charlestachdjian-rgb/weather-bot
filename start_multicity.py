"""
Multi-city weather monitor launcher.
Starts separate weather_monitor.py instances for each specified city.
Each instance writes to its own log file: weather_log_{city}.jsonl
"""
import subprocess
import sys
import json
from pathlib import Path

# Cities to monitor (start with these 3)
CITIES_TO_MONITOR = ["paris", "london", "nyc"]

def load_city_config():
    config_path = Path(__file__).parent / "city_config.json"
    with open(config_path) as f:
        return json.load(f)

def start_city_monitor(city_key, config):
    """Start a weather_monitor.py instance for a specific city."""
    print(f"Starting monitor for {city_key.upper()}...")
    
    # Set environment variables for this city
    env_vars = {
        "CITY": city_key,
        "CITY_SLUG": config["slug"],
        "CITY_TZ": config["timezone"],
        "METAR_STATION": config["metar_station"],
        "SYNOP_STATION": config["synop_station"],
        "CITY_LAT": str(config["lat"]),
        "CITY_LON": str(config["lon"]),
        "OPENMETEO_BIAS": str(config["openmeteo_bias"]),
        "POLL_MIN_DAY": str(config["poll_min_day"]),
        "POLL_MIN_NIGHT": str(config["poll_min_night"]),
        "LOG_FILE": f"weather_log_{city_key}.jsonl",
    }
    
    # Build command
    cmd = [sys.executable, "weather_monitor.py"]
    
    # Start process
    process = subprocess.Popen(
        cmd,
        env={**subprocess.os.environ, **env_vars},
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1
    )
    
    return process

if __name__ == "__main__":
    print("=" * 70)
    print("  MULTI-CITY WEATHER MONITOR LAUNCHER")
    print("=" * 70)
    print(f"\nCities to monitor: {', '.join(CITIES_TO_MONITOR)}")
    print("\nNOTE: This script is for reference. Use controlPwshProcess to start")
    print("      separate instances with --city argument instead.")
    print("\nExample:")
    for city in CITIES_TO_MONITOR:
        print(f"  python weather_monitor.py --city {city}")
    print("\n" + "=" * 70)
    
    config = load_city_config()
    
    print("\nCity configurations loaded:")
    for city in CITIES_TO_MONITOR:
        if city in config:
            c = config[city]
            print(f"  {city:10} Station: {c['metar_station']:6} Coords: ({c['lat']:.2f}, {c['lon']:.2f})")
        else:
            print(f"  {city:10} ERROR: Not found in city_config.json")
