# Weather Bot — Implementation Instructions for Kiro

You are working on a Polymarket temperature trading bot (`weather_monitor.py`). Another AI agent has performed a deep analysis of all historical data and strategy logic. Below are the findings and implementation tasks. Execute them in order.

---

## CRITICAL DATA BUG TO FIX FIRST

`backtest_data.json` has a serious data integrity problem: the `wu_high`, `wu_low`, `synop_high`, `synop_low`, `openmeteo_high`, and `openmeteo_low` fields are **identical across all cities on the same date**. For example, on Feb 3, London, Seoul, Buenos Aires, Ankara, Wellington all show `wu_high=10, openmeteo_high=9.4, synop_high=9.5`. This is clearly Paris weather data copy-pasted to every city.

The **market data** (questions, brackets, volumes, winning_range, resolved_to) IS correct and per-city. Only the weather fields are wrong.

**Action:** When you rebuild the backtest data collector (see Task 3 below), make sure each city fetches its OWN weather data from the correct station.

---

## TASK 1: Lower MIN_YES_FOR_ALERT from 3% to 1%

**File:** `weather_monitor.py`

**Change:** Find the line:
```python
MIN_YES_FOR_ALERT = 0.03  # if YES < 3 cents, edge is too small
```
Change to:
```python
MIN_YES_FOR_ALERT = 0.01  # if YES < 1 cent, edge is too small
```

**Why:** Simulation on 8 Paris days showed that at 3% threshold, many valid T1 kills are filtered out because the market has already repriced YES to 1-2%. Lowering to 1% unlocks ~50 additional trades, all 100% correct. The edge per trade is smaller ($1-2 instead of $3+) but they compound over time. This is the lowest-risk, highest-impact change.

---

## TASK 2: Add T2 buffer of 3.5°C as secondary check

**File:** `weather_monitor.py`

**Change:** Add a new constant:
```python
FORECAST_KILL_BUFFER_TIGHT = 3.5  # Tighter T2 buffer for Paris (validated on 8 days)
```

**Do NOT replace the existing 4.0°C buffer.** Instead, add a secondary T2 pass that uses 3.5°C but ONLY for Paris. On 8 days of Paris data, a 3.5°C buffer produced 33 T2 signals with 0 wrong (vs 26 at 4.0°C). However, 8 days is not enough to be confident. Log these as `FLOOR_NO_FORECAST_TIGHT` signals but **do not trade them yet** — just collect data. After 30+ days, if still 100% correct, promote to active trading.

**Implementation:**
- After the existing T2 check in `detect_signals()`, add a second check with the 3.5°C buffer
- If it fires but the 4.0°C check didn't, log it as `FLOOR_NO_FORECAST_TIGHT` with a note
- Do NOT append it to the signals list — just log to `weather_log.jsonl`
- This is the same pattern used for `GUARANTEED_NO_CEIL` (dormant, collecting data)

---

## TASK 3: Multi-city data collection

This is the biggest opportunity. The strategy works on pure physics (temperature can only go up during the day), so it applies to any city. But we need proper per-city weather data.

### Cities to add (ranked by Polymarket bracket volume):

| City | Avg bracket volume | METAR station | Resolution source |
|------|-------------------|---------------|-------------------|
| London | $38,517 | EGLL (Heathrow) | Weather Underground / EGLL |
| Seoul | $30,109 | RKSI (Incheon) | Weather Underground / RKSI |
| NYC | $33,392 | KLGA (LaGuardia) | Weather Underground / KLGA |
| Ankara | $17,647 | LTAC (Esenboğa) | Weather Underground / LTAC |
| Wellington | $14,005 | NZWN (Wellington) | Weather Underground / NZWN |
| Buenos Aires | $11,672 | SAEZ (Ezeiza) | Weather Underground / SAEZ |
| Toronto | $11,433 | CYYZ (Pearson) | Weather Underground / CYYZ |
| São Paulo | $5,763 | SBGR (Guarulhos) | Weather Underground / SBGR |

**IMPORTANT about Southern Hemisphere cities (Buenos Aires, Wellington, São Paulo):** These are in summer right now (February). Temperature behavior is different — daily highs can occur later, and the "temperature only goes up" assumption may not hold as cleanly. Collect data but do NOT trade these cities until you have 30+ days of analysis. Focus on Northern Hemisphere cities first (London, Seoul, NYC, Ankara, Toronto).

### Data to collect per city (every polling cycle):

**1. METAR (primary — this is what Polymarket resolves against)**
- Source: `https://aviationweather.gov/api/data/metar?ids={STATION}&format=json&hours=1`
- Fields needed: `temp` (°C, integer), `obsTime` (timestamp)
- Frequency: Every 5 minutes during trading hours (METAR updates every 30 min but you want to catch it fast)
- Store: timestamp, station, temp_c, raw METAR string

**2. OpenMeteo forecast (for T2 signals)**
- Source: `https://api.open-meteo.com/v1/forecast?latitude={LAT}&longitude={LON}&daily=temperature_2m_max&hourly=temperature_2m&timezone={TZ}&forecast_days=1`
- Fields needed: `daily.temperature_2m_max` (forecast high), `hourly.temperature_2m` (hourly forecast)
- Frequency: Once at startup, then every hour (forecast updates)
- Store: timestamp, forecast_high, hourly array, bias correction

**3. Polymarket markets (for bracket prices)**
- Source: Gamma API (already implemented for Paris)
- Fields needed: All bracket YES/NO prices, volumes, open/closed status
- Frequency: Every polling cycle
- Store: timestamp, all bracket prices, YES sum

**Station coordinates for OpenMeteo:**

| City | Latitude | Longitude | Timezone |
|------|----------|-----------|----------|
| Paris (existing) | 49.0097 | 2.5479 | Europe/Paris |
| London | 51.4700 | -0.4543 | Europe/London |
| Seoul | 37.4602 | 126.4407 | Asia/Seoul |
| NYC | 40.7769 | -73.8740 | America/New_York |
| Ankara | 40.1281 | 32.9951 | Europe/Istanbul |
| Wellington | -41.3272 | 174.8053 | Pacific/Auckland |
| Buenos Aires | -34.8222 | -58.5358 | America/Argentina/Buenos_Aires |
| Toronto | 43.6772 | -79.6306 | America/Toronto |
| São Paulo | -23.4356 | -46.4731 | America/Sao_Paulo |

### Implementation approach:

**Option A (recommended): Separate monitor per city**
- Create a config dict per city with station, coordinates, timezone, Polymarket slug pattern
- Run `weather_monitor.py` with a `--city` argument
- Each instance writes to its own log file (`weather_log_london.jsonl`, etc.)
- Start with Paris + London + NYC (3 instances)

**Option B: Single monitor, multi-city loop**
- One process cycles through all cities each polling interval
- Simpler to manage but slower (5 cities × 3 API calls = 15 calls per cycle)
- Risk: if one city's API call is slow, it delays all others

### Polymarket slug patterns per city:
- Paris: `highest-temperature-in-paris-on-{month}-{day}-{year}`
- London: `highest-temperature-in-london-on-{month}-{day}-{year}`
- Seoul: `highest-temperature-in-seoul-on-{month}-{day}-{year}`
- NYC: `highest-temperature-in-nyc-on-{month}-{day}-{year}`
- etc.

You can verify the exact slug format by checking the Gamma API for today's markets.

### OpenMeteo bias correction per city:

For Paris, the measured bias is +0.6°C average (range +0.1 to +1.3°C), and we use +1.0°C as a safety margin. For other cities, **start with +1.0°C** and measure the actual bias over the first 2 weeks of data collection. Then adjust per city.

Store the daily OM forecast vs actual WU high for each city so we can compute per-city bias after 14+ days.

---

## TASK 4: Fix backtest_data.json collection script

Find the script that generates `backtest_data.json` (likely `backtest.py` or `backtest_nyc.py`). The bug is that it fetches weather data (OM, SYNOP, WU timeseries) only once and applies it to all cities. Fix it to:

1. For each city-day combination, fetch OpenMeteo data using that city's coordinates
2. For each city-day, fetch METAR data from that city's station
3. For each city-day, fetch SYNOP data from that city's SYNOP station (if available)
4. Store per-city weather data in the JSON

The market data fetching (Polymarket brackets, prices, resolutions) appears correct already.

Also add `price_histories` for all cities, not just NYC. The NYC backtest file has intraday price snapshots which are essential for measuring real edge. Without them, we can only estimate.

---

## TASK 5: Ceiling NO — add 6th guard (peak-reached check)

**File:** `weather_monitor.py`

**Change:** In the `should_block_risky_signal()` function, add a 6th guard:

```python
# Guard 6: Peak-reached check — only allow if daily high >= forecast - 0.5°C
if forecast_high is not None and running_high < forecast_high - 0.5:
    reasons.append(f"Peak not reached: high {running_high}°C < forecast {forecast_high}°C - 0.5")
```

You'll need to pass `forecast_high` into `should_block_risky_signal()`. Add it as a parameter.

**Why:** Simulation showed this guard would have:
- Blocked Feb 15 (daily high 3°C, forecast 8.8°C → 3 < 8.3, blocked ✅)
- Blocked Feb 18 (daily high 5°C, forecast 9.8°C → 5 < 9.3, blocked ✅)
- Allowed Feb 23 (daily high 16°C, forecast 15.7°C → 16 > 15.2, allowed ✅)

**Keep GUARANTEED_NO_CEIL dormant.** This guard improves it but 87% accuracy across multi-city data is not good enough when losses are $100 each. Continue collecting data.

---

## TASK 6: Daily data quality logging

Add a daily summary log entry (at midnight or market close) that records:

```json
{
  "type": "daily_summary",
  "date": "2026-02-27",
  "city": "paris",
  "wu_high": 14,
  "wu_low": 8,
  "synop_high": 13.8,
  "synop_low": 7.9,
  "openmeteo_forecast_high": 13.2,
  "openmeteo_bias_correction": 1.0,
  "corrected_forecast": 14.2,
  "actual_om_error": 0.8,
  "dynamic_bias_9am": 0.5,
  "signals_fired": 5,
  "signals_blocked": 1,
  "trades_executed": 4,
  "trades_correct": 4,
  "daily_pnl": 3.50,
  "metar_readings_count": 48,
  "synop_readings_count": 24
}
```

This is critical for building the per-city bias correction database and for validating strategy performance over time.

---

## FINDINGS SUMMARY (do not implement, just context)

### SYNOP vs METAR relationship:
- SYNOP consistently reads 0.3°C LOWER than METAR for daily highs (6/8 days)
- Feb 11: SYNOP=11.3°C vs METAR=13°C (1.7°C gap)
- Feb 21: SYNOP=15.4°C vs METAR=16°C (0.6°C gap)
- SYNOP does NOT lead METAR — it lags or underreads
- Do NOT use SYNOP to predict METAR rounding (pre-T1 idea is invalid)
- SYNOP is useful as confirmation/context layer only

### NYC market efficiency:
- Across 20 days of real intraday price data, only 1 T1 edge window found (Feb 20, YES=3.6%)
- NYC market reprices dead brackets to near-zero almost instantly
- Paris is actually better for T1 edge because lower volume = slower repricing
- For multi-city, prioritize cities with moderate volume ($10k-$20k per bracket) over high volume

### OpenMeteo bias (Paris, 8 days):
- Average error: +0.6°C (OM underforecasts)
- Range: +0.1°C to +1.3°C
- Current correction of +1.0°C is slightly aggressive but safe
- Keep +1.0°C until 30+ days of data confirms the true average

### T2 buffer sensitivity (Paris only, valid data):
- All buffers from 2.0°C to 5.0°C produced 0 wrong signals on 8 days
- This is because OM+1.0°C bias is conservative enough
- 8 days is too few to trust tight buffers — keep 4.0°C for active trading
- Log 3.5°C signals as dormant for future evaluation

### Strategy P&L reality:
- Winning T1 trades earn $1-5 per $100 bet (YES is usually 1-5% when bracket dies)
- Losing trades cost $100 (total loss)
- Need 20-100 winning trades to recover from 1 loss
- This is why only mathematically certain strategies (Floor NO) are viable
- Any strategy that "predicts" rather than "observes" is too risky

---

## PRIORITY ORDER

1. **Task 1** — MIN_YES change (1 line, immediate impact)
2. **Task 6** — Daily summary logging (needed before multi-city)
3. **Task 3** — Multi-city data collection (start with London + NYC)
4. **Task 4** — Fix backtest data collector
5. **Task 5** — Ceiling NO 6th guard
6. **Task 2** — Tight T2 buffer logging

Start with Tasks 1 and 6, then move to Task 3. Tasks 2, 4, and 5 can wait until after multi-city collection is running.
