# Multi-City Expansion Setup Guide

## Overview

The weather bot can now monitor multiple cities simultaneously. Each city runs as a separate instance with its own configuration, data sources, and log file.

## Files Created

### 1. `city_config.json`
Central configuration for all supported cities with:
- METAR station codes
- SYNOP station codes  
- OpenMeteo coordinates (lat/lon)
- Timezone
- Per-city bias correction (start with 1.0°C, adjust after 14+ days)
- Polling intervals

### 2. `backtest_multicity.py`
Fixed backtest data collector that fetches CORRECT per-city weather data:
- Uses per-city METAR stations
- Uses per-city SYNOP stations
- Uses per-city OpenMeteo coordinates
- Verifies data is different across cities
- Outputs to `backtest_multicity_data.json`

### 3. `start_multicity.py`
Reference script showing how to launch multiple city monitors.

## Current Status

### Supported Cities (in city_config.json)
1. **Paris** - LFPG (existing, tested)
2. **London** - EGLL (ready to deploy)
3. **NYC** - KLGA (ready to deploy)
4. **Seoul** - RKSI (ready to deploy)
5. **Ankara** - LTAC (ready to deploy)
6. **Toronto** - CYYZ (ready to deploy)

### NOT Included (Southern Hemisphere - Summer)
- Wellington (NZWN)
- Buenos Aires (SAEZ)
- São Paulo (SBGR)

**Reason:** These cities are in summer (February). Temperature behavior is different - daily highs can occur later in the day. The "temperature only goes up" assumption may not hold as cleanly. Collect data but DO NOT trade until 30+ days of analysis.

## How to Run Multi-City Monitoring

### Option A: Separate Instances (Recommended)

Start one instance per city in separate terminals:

```bash
# Terminal 1 - Paris (already running)
python weather_monitor.py --city paris

# Terminal 2 - London
python weather_monitor.py --city london

# Terminal 3 - NYC
python weather_monitor.py --city nyc
```

Each instance:
- Reads from `city_config.json`
- Writes to `weather_log_{city}.jsonl`
- Runs independently
- Can be stopped/restarted without affecting others

### Option B: Using Kiro's controlPwshProcess

```python
# Start London monitor
controlPwshProcess(
    action="start",
    command="python weather_monitor.py --city london",
    cwd="weather-bot"
)

# Start NYC monitor  
controlPwshProcess(
    action="start",
    command="python weather_monitor.py --city nyc",
    cwd="weather-bot"
)
```

## Required Changes to weather_monitor.py

The current `weather_monitor.py` is hardcoded for Paris. To support multi-city, it needs:

### 1. Add command-line argument parsing
```python
import argparse

parser = argparse.ArgumentParser()
parser.add_argument("--city", default="paris", help="City to monitor")
args = parser.parse_args()

CITY = args.city
```

### 2. Load city config
```python
import json
from pathlib import Path

config_path = Path(__file__).parent / "city_config.json"
with open(config_path) as f:
    CITY_CONFIGS = json.load(f)

CITY_CONFIG = CITY_CONFIGS[CITY]
```

### 3. Replace hardcoded values
```python
# OLD (hardcoded)
LOCAL_TZ = ZoneInfo("Europe/Paris")
METAR_URL = "https://aviationweather.gov/api/data/metar?ids=LFPG&format=json"
SYNOP_URL = "https://www.ogimet.com/cgi-bin/getsynop?block=07157&begin={begin}"
OPENMETEO_URL = "https://api.open-meteo.com/v1/forecast?latitude=49.0097&longitude=2.5479..."

# NEW (from config)
LOCAL_TZ = ZoneInfo(CITY_CONFIG["timezone"])
METAR_URL = f"https://aviationweather.gov/api/data/metar?ids={CITY_CONFIG['metar_station']}&format=json"
SYNOP_URL = f"https://www.ogimet.com/cgi-bin/getsynop?block={CITY_CONFIG['synop_station']}&begin={{begin}}"
OPENMETEO_URL = (f"https://api.open-meteo.com/v1/forecast?"
                 f"latitude={CITY_CONFIG['lat']}&longitude={CITY_CONFIG['lon']}..."
                 f"&timezone={CITY_CONFIG['timezone']}")
```

### 4. Update log file path
```python
# OLD
LOG_FILE = Path(__file__).resolve().parent / "weather_log.jsonl"

# NEW
LOG_FILE = Path(__file__).resolve().parent / f"weather_log_{CITY}.jsonl"
```

### 5. Update slug generation
```python
def date_slug(d: date) -> str:
    month = d.strftime("%B").lower()
    city_slug = CITY_CONFIG["slug"]
    return f"highest-temperature-in-{city_slug}-on-{month}-{d.day}-{d.year}"
```

## Testing Multi-City Setup

### 1. Test backtest data collector
```bash
python backtest_multicity.py
```

Expected output:
- Fetches Feb 3, 2026 data for all cities
- Shows DIFFERENT weather data per city
- London: ~7°C, Buenos Aires: ~37°C, Seoul: different temps
- Saves to `backtest_multicity_data.json`

### 2. Test single city monitor
```bash
python weather_monitor.py --city london
```

Expected:
- Loads London config from city_config.json
- Fetches METAR from EGLL
- Fetches SYNOP from station 03772
- Fetches OpenMeteo for London coordinates
- Writes to weather_log_london.jsonl

## Per-City Bias Correction

Start all cities with +1.0°C OpenMeteo bias. After 14+ days:

1. Extract daily summaries from logs:
```python
import json

city = "london"
with open(f"weather_log_{city}.jsonl") as f:
    summaries = [json.loads(line) for line in f if '"type": "daily_summary"' in line]

errors = [s["actual_om_error"] for s in summaries if s.get("actual_om_error") is not None]
avg_error = sum(errors) / len(errors)
print(f"{city}: Average OM error = {avg_error:.2f}°C")
```

2. Update `city_config.json`:
```json
{
  "london": {
    ...
    "openmeteo_bias": 0.8  // Adjusted based on measured error
  }
}
```

3. Restart monitor to use new bias

## Deployment Priority

### Phase 1: Northern Hemisphere (Now)
1. **Paris** - Already running, tested
2. **London** - High volume ($38k/bracket), same timezone as Paris
3. **NYC** - High volume ($33k/bracket), different timezone (good coverage)

### Phase 2: After 7 days of Phase 1 data
4. **Seoul** - High volume ($30k/bracket), Asian timezone
5. **Ankara** - Medium volume ($17k/bracket)
6. **Toronto** - Medium volume ($11k/bracket)

### Phase 3: After 30 days (if strategy proves robust)
7. **Wellington** - Southern Hemisphere, collect data only
8. **Buenos Aires** - Southern Hemisphere, collect data only
9. **São Paulo** - Southern Hemisphere, collect data only

## Monitoring Multiple Cities

### Check all running monitors
```python
listProcesses()
```

### Check specific city output
```python
getProcessOutput(terminalId="london_monitor_id", lines=50)
```

### Stop a city monitor
```python
controlPwshProcess(action="stop", terminalId="london_monitor_id")
```

## Data Analysis Across Cities

After collecting 7+ days of multi-city data:

1. **Compare strategy performance per city**
   - Which cities have more T1 opportunities?
   - Which cities have slower market repricing?
   - Which cities have better forecast accuracy?

2. **Validate bias corrections**
   - Is +1.0°C correct for all cities?
   - Do some cities need tighter/looser buffers?

3. **Identify best trading opportunities**
   - Cities with moderate volume ($10-20k) may have better edges
   - High volume cities (NYC) reprice faster = smaller edges

## Known Issues

### Issue 1: Original backtest_data.json has wrong weather data
- **Problem:** All cities on Feb 3 show wu_high=10, synop_high=9.5 (Paris data)
- **Fix:** Use `backtest_multicity.py` instead
- **Status:** Fixed in new script

### Issue 2: weather_monitor.py is Paris-only
- **Problem:** Hardcoded station codes, coordinates, timezone
- **Fix:** Add --city argument and load from city_config.json
- **Status:** Documented above, needs implementation

### Issue 3: Southern Hemisphere cities
- **Problem:** Summer temps behave differently (peak can be late afternoon)
- **Fix:** Collect data but don't trade until 30+ days of analysis
- **Status:** Excluded from initial deployment

## Next Steps

1. ✅ Create city_config.json
2. ✅ Create backtest_multicity.py
3. ✅ Document multi-city setup
4. ⏳ Modify weather_monitor.py to accept --city argument
5. ⏳ Test London monitor
6. ⏳ Deploy London + NYC monitors
7. ⏳ Collect 7 days of data
8. ⏳ Analyze per-city performance
9. ⏳ Adjust bias corrections
10. ⏳ Expand to Seoul, Ankara, Toronto

---

**Last Updated:** Feb 26, 2026  
**Status:** Infrastructure ready, weather_monitor.py needs modification
