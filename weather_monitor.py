"""
weather_monitor.py  --  Paris temperature market monitor + signal detector

Tracks Polymarket daily Paris high temperature markets and compares them against
real-time METAR observations from LFPG (Charles de Gaulle Airport) — the same
station Polymarket uses for resolution via Weather Underground.

Enhanced 5-layer strategy
─────────────────────────
Layer 1: FLOOR_NO_CERTAIN (T1) – running high already exceeds bracket. Zero risk.
Layer 2: FLOOR_NO_FORECAST (T2) – OM forecast high (bias-corrected) exceeds lower
         bracket by ≥4°C. Very high confidence, early morning entry.
Layer 3: T2_UPPER – OM forecast is far below upper bracket (≥5°C gap). Requires
         dynamic bias check to confirm OM is not underforecasting.
Layer 4: MIDDAY_T2 – At noon, re-evaluate with 6h of real data. Tighter 2.5°C
         buffer validated by OM remaining trajectory + dynamic bias.
Layer 5: GUARANTEED_NO_CEIL / LOCKED_IN_YES – Late-day signals, guarded by 5
         safeguards (OM peak hour, OM remaining max, OM forecast vs bracket,
         multi-source trend, SYNOP velocity).

No SUM_ANOMALY signals - removed because market inefficiency doesn't predict winners.

Config (env vars):
  POLL_MIN_DAY   Minutes between observations during trading hours (default 5)
  POLL_MIN_NIGHT Minutes between observations overnight (default 15)
  CITY           City slug (default: paris)
  TELEGRAM_TOKEN, TELEGRAM_CHAT_ID  — Telegram bot credentials for alerts

Output:
  weather_log.jsonl  --  one JSON line per observation, market snapshot, or signal
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import signal as signal_module
import time
from datetime import datetime, date, timezone, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import aiohttp

# ── Config ────────────────────────────────────────────────────────────────────

POLL_MIN_DAY   = int(os.getenv("POLL_MIN_DAY",   "5"))   # 8am–8pm CET
POLL_MIN_NIGHT = int(os.getenv("POLL_MIN_NIGHT", "15"))  # overnight
CITY           = os.getenv("CITY", "paris")

# Paris markets use single-degree Celsius brackets. Resolution rounds to
# nearest integer, so a bracket "14°C" means high temp rounds to 14.
# A range is dead once daily_high >= bracket_value + 0.5
ROUNDING_BUFFER = 0.5

# After this hour (CET) the daily high is essentially set
LATE_DAY_HOUR = 16   # 4pm CET for GUARANTEED_NO_CEIL
LOCK_IN_HOUR  = 17   # 5pm CET for LOCKED_IN_YES
MIDDAY_HOUR   = 12   # Noon reassessment window

# Ceiling NO gap and T2 buffers
CEIL_GAP            = 2.0   # gap for late-day ceiling NO
FORECAST_KILL_BUFFER = 4.0  # T2 forecast gap requirement (active trading)
FORECAST_KILL_BUFFER_TIGHT = 3.5  # Tighter T2 buffer for Paris (dormant - collecting data)
UPPER_KILL_BUFFER   = 5.0   # T2 Upper: upper bracket must be ≥5°C above adjusted forecast
MIDDAY_KILL_BUFFER  = 2.5   # Midday T2: tighter buffer with 6h of real data
DYNAMIC_BIAS_DANGER = 1.0   # if morning bias > this, OM is underforecasting

# Sum anomaly tolerance
SUM_TOL = 0.07   # flag if sum of YES prices deviates > 7% from 1.0

# Minimum YES price to bother alerting on a guaranteed-NO opportunity
MIN_YES_FOR_ALERT = 0.01  # if YES < 1 cent, edge is too small

LOCAL_TZ = ZoneInfo("Europe/Paris")

LOG_FILE = Path(__file__).resolve().parent / "weather_log.jsonl"

# ── Telegram ──────────────────────────────────────────────────────────────────

def _load_dotenv() -> None:
    env_path = Path(__file__).resolve().parent / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

_load_dotenv()

TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

async def notify_telegram(session: aiohttp.ClientSession, message: str) -> None:
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        async with session.post(
            url,
            json={"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"},
            timeout=aiohttp.ClientTimeout(total=10),
        ) as r:
            if r.status != 200:
                logger.warning("Telegram error: HTTP %d", r.status)
    except Exception as e:
        logger.warning("Telegram send failed: %s", e)

# ── Logging ───────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# ── State ─────────────────────────────────────────────────────────────────────

daily_high_c: float | None = None
_current_date: date | None = None   # the CET date we're tracking
_fired_signals: set[str] = set()    # dedup: don't re-alert the same signal
_killed_brackets: set[str] = set()  # brackets killed by running high today
_forecast_high_c: float | None = None  # Open-Meteo forecast high for today
_morning_summary_sent: bool = False
_shutdown = False

# Enhanced strategy state (reset daily)
_om_hourly_forecast: list[dict] = []   # [{hour: float, temp: float}, ...]
_metar_readings: list[dict] = []       # accumulated METAR readings today
_synop_readings: list[dict] = []       # accumulated SYNOP readings today
_dynamic_bias: float | None = None     # actual − OM average over morning hours
_dynamic_forecast: float | None = None # forecast_high + max(0, dynamic_bias)
_midday_reassessment_done: bool = False

# Daily statistics tracking (for daily summary)
_daily_stats = {
    "signals_fired": 0,
    "signals_blocked": 0,
    "wu_high": None,
    "wu_low": None,
    "synop_high": None,
    "synop_low": None,
}

# ── Helpers ───────────────────────────────────────────────────────────────────

def log_event(record: dict) -> None:
    record["ts"] = datetime.now(timezone.utc).isoformat()
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")



def now_local() -> datetime:
    return datetime.now(LOCAL_TZ)


def date_slug(d: date) -> str:
    month = d.strftime("%B").lower()
    day   = str(d.day)
    return f"highest-temperature-in-{CITY}-on-{month}-{day}-{d.year}"


def poll_interval_minutes() -> int:
    """Use shorter interval during daytime CET when markets are most active."""
    h = now_local().hour
    if 6 <= h < 20:
        return POLL_MIN_DAY
    return POLL_MIN_NIGHT


def maybe_reset_daily_high() -> None:
    """Reset daily_high_c when the CET calendar date rolls over."""
    global daily_high_c, _current_date, _fired_signals, _killed_brackets
    global _forecast_high_c, _morning_summary_sent
    global _om_hourly_forecast, _metar_readings, _synop_readings
    global _dynamic_bias, _dynamic_forecast, _midday_reassessment_done
    global _daily_stats
    today = now_local().date()
    if _current_date is None:
        _current_date = today
        return
    if today != _current_date:
        # Log daily summary before resetting
        _log_daily_summary(_current_date)
        
        logger.info("New day (%s). Resetting daily high (was %.1f°C).", today, daily_high_c or 0)
        daily_high_c          = None
        _current_date         = today
        _fired_signals        = set()
        _killed_brackets      = set()
        _forecast_high_c      = None
        _morning_summary_sent = False
        _om_hourly_forecast   = []
        _metar_readings       = []
        _synop_readings       = []
        _dynamic_bias         = None
        _dynamic_forecast     = None
        _midday_reassessment_done = False
        _daily_stats = {
            "signals_fired": 0,
            "signals_blocked": 0,
            "wu_high": None,
            "wu_low": None,
            "synop_high": None,
            "synop_low": None,
        }


# ── Weather fetching ──────────────────────────────────────────────────────────

METAR_URL   = "https://aviationweather.gov/api/data/metar?ids=LFPG&format=json"
SYNOP_URL   = "https://www.ogimet.com/cgi-bin/getsynop?block=07157&begin={begin}"
OPENMETEO_URL = ("https://api.open-meteo.com/v1/forecast?"
                 "latitude=49.0097&longitude=2.5479"
                 "&current=temperature_2m,relative_humidity_2m,wind_speed_10m"
                 "&minutely_15=temperature_2m"
                 "&past_minutely_15=8&forecast_minutely_15=0"
                 "&timezone=Europe/Paris")
OPENMETEO_FORECAST_URL = ("https://api.open-meteo.com/v1/forecast?"
                          "latitude=49.0097&longitude=2.5479"
                          "&daily=temperature_2m_max"
                          "&timezone=Europe/Paris"
                          "&forecast_days=1")
OPENMETEO_HOURLY_URL = ("https://api.open-meteo.com/v1/forecast?"
                        "latitude=49.0097&longitude=2.5479"
                        "&hourly=temperature_2m"
                        "&timezone=Europe/Paris"
                        "&forecast_days=1")
GAMMA_URL   = "https://gamma-api.polymarket.com/events"

# Open-Meteo systematically reads ~0.8°C below WU. Compensate upward.
OPENMETEO_BIAS_CORRECTION = 1.0  # add 1°C to Open-Meteo forecast

# Tier 2: forecast buffer — bracket must be this many °C below the adjusted
# forecast high to be considered dead via forecast alone.
FORECAST_KILL_BUFFER = 4.0

# Track secondary source readings (not used for signals, just for display/logging)
_last_synop: dict | None = None
_last_openmeteo: dict | None = None


async def fetch_metar(session: aiohttp.ClientSession) -> dict | None:
    """Primary source: METAR/LFPG — 1°C precision, every 30 min. Matches WU resolution."""
    try:
        async with session.get(METAR_URL, timeout=aiohttp.ClientTimeout(total=10)) as r:
            if r.status != 200:
                return None
            data = await r.json()
            if not data:
                return None
            obs      = data[0]
            temp_c   = obs.get("temp")
            dewp_c   = obs.get("dewp")
            obs_time = obs.get("obsTime") or obs.get("reportTime") or ""
            raw      = obs.get("rawOb") or ""
            if temp_c is None:
                return None
            return {
                "source":      "METAR/LFPG",
                "station":     "LFPG",
                "obs_time":    obs_time,
                "temp_c":      round(float(temp_c), 1),
                "dewp_c":      round(float(dewp_c), 1) if dewp_c is not None else None,
                "wind_dir":    obs.get("wdir"),
                "wind_spd_kt": obs.get("wspd"),
                "raw_metar":   raw,
            }
    except Exception as e:
        logger.warning("METAR error: %s", e)
        return None


def _decode_synop_temp(raw_line: str) -> float | None:
    """Extract 0.1°C temperature from SYNOP group 1snTTT."""
    m = re.search(r'\b1([01])(\d{3})\b', raw_line)
    if not m:
        return None
    sign = 1 if m.group(1) == "0" else -1
    return sign * int(m.group(2)) / 10.0


async def fetch_synop(session: aiohttp.ClientSession) -> dict | None:
    """Secondary source: SYNOP/OGIMET — same CDG station, 0.1°C precision, hourly."""
    global _last_synop
    try:
        now = datetime.now(timezone.utc)
        begin = now.strftime("%Y%m%d") + "0000"
        url = SYNOP_URL.format(begin=begin)
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as r:
            if r.status != 200:
                return _last_synop
            text = await r.text()

        lines = [l.strip() for l in text.splitlines()
                 if l.strip() and not l.startswith("#") and l.startswith("07157")]
        if not lines:
            return _last_synop

        latest = lines[-1]
        temp = _decode_synop_temp(latest)
        if temp is None:
            return _last_synop

        # Extract hour from line: "07157,2026,02,22,14,00,AAXX..."
        parts = latest.split(",")
        hour_utc = int(parts[4]) if len(parts) > 4 else 0

        _last_synop = {
            "source":   "SYNOP/07157",
            "temp_c":   temp,
            "hour_utc": hour_utc,
            "raw":      latest[:100],
        }
        return _last_synop
    except Exception as e:
        logger.warning("SYNOP error: %s", e)
        return _last_synop


async def fetch_openmeteo(session: aiohttp.ClientSession) -> dict | None:
    """Tertiary source: Open-Meteo — model-based, 0.1°C, every 15 min.
    Reads ~0.5-1°C below station data but useful for trend detection."""
    global _last_openmeteo
    try:
        async with session.get(OPENMETEO_URL, timeout=aiohttp.ClientTimeout(total=10)) as r:
            if r.status != 200:
                return _last_openmeteo
            data = await r.json()

        current = data.get("current", {})
        m15 = data.get("minutely_15", {})
        times = m15.get("time", [])
        temps = m15.get("temperature_2m", [])

        trend = "?"
        if len(temps) >= 3:
            recent = [t for t in temps[-3:] if t is not None]
            if len(recent) >= 2:
                if recent[-1] > recent[0] + 0.05:
                    trend = "RISING"
                elif recent[-1] < recent[0] - 0.05:
                    trend = "FALLING"
                else:
                    trend = "FLAT"

        _last_openmeteo = {
            "source":   "OpenMeteo",
            "temp_c":   current.get("temperature_2m"),
            "humidity": current.get("relative_humidity_2m"),
            "wind_kmh": current.get("wind_speed_10m"),
            "trend":    trend,
            "m15_temps": list(zip(times[-4:], temps[-4:])) if times else [],
        }
        return _last_openmeteo
    except Exception as e:
        logger.warning("Open-Meteo error: %s", e)
        return _last_openmeteo


async def fetch_openmeteo_forecast_high(session: aiohttp.ClientSession) -> float | None:
    """Fetch today's forecast max temperature from Open-Meteo.
    Returns bias-corrected value (adds OPENMETEO_BIAS_CORRECTION)."""
    global _forecast_high_c
    try:
        async with session.get(OPENMETEO_FORECAST_URL,
                               timeout=aiohttp.ClientTimeout(total=10)) as r:
            if r.status != 200:
                return _forecast_high_c
            data = await r.json()
        daily = data.get("daily", {})
        maxes = daily.get("temperature_2m_max", [])
        if maxes and maxes[0] is not None:
            raw = float(maxes[0])
            _forecast_high_c = round(raw + OPENMETEO_BIAS_CORRECTION, 1)
            return _forecast_high_c
        return _forecast_high_c
    except Exception as e:
        logger.warning("Open-Meteo forecast error: %s", e)
        return _forecast_high_c


async def fetch_openmeteo_hourly(session: aiohttp.ClientSession) -> list[dict]:
    """Fetch today's hourly temperature forecast from Open-Meteo.
    Returns list of {hour: float, temp: float} for guard logic."""
    global _om_hourly_forecast
    try:
        async with session.get(OPENMETEO_HOURLY_URL,
                               timeout=aiohttp.ClientTimeout(total=10)) as r:
            if r.status != 200:
                return _om_hourly_forecast
            data = await r.json()
        hourly = data.get("hourly", {})
        times = hourly.get("time", [])
        temps = hourly.get("temperature_2m", [])
        pts = []
        for t, tmp in zip(times, temps):
            if tmp is not None:
                from datetime import datetime as _dt
                dl = _dt.fromisoformat(t)
                pts.append({"hour": dl.hour + dl.minute / 60, "temp": tmp})
        if pts:
            _om_hourly_forecast = pts
            logger.info("OM hourly loaded: %d points, max=%.1f°C at %d:00",
                        len(pts), max(p["temp"] for p in pts),
                        int(max(pts, key=lambda p: p["temp"])["hour"]))
        return _om_hourly_forecast
    except Exception as e:
        logger.warning("Open-Meteo hourly error: %s", e)
        return _om_hourly_forecast


# ── Dynamic bias & guard helpers ─────────────────────────────────────────

def compute_dynamic_bias(metar_history: list[dict],
                         om_hourly: list[dict],
                         up_to_hour: float) -> float:
    """Average (METAR actual − OM predicted) for morning hours.
    Positive means OM underforecasts (actual is warmer)."""
    diffs = []
    for obs in metar_history:
        if obs["hour"] > up_to_hour:
            break
        best_om = None
        for om in om_hourly:
            if abs(om["hour"] - obs["hour"]) <= 0.5:
                best_om = om["temp"]
                break
        if best_om is not None:
            diffs.append(obs["temp"] - best_om)
    return sum(diffs) / len(diffs) if diffs else 0.0


def _om_peak_hour(om_hourly: list[dict]) -> float | None:
    if not om_hourly:
        return None
    return max(om_hourly, key=lambda p: p["temp"])["hour"]


def _om_remaining_max(om_hourly: list[dict], after_hour: float) -> float | None:
    remaining = [p["temp"] for p in om_hourly if p["hour"] > after_hour]
    return max(remaining) if remaining else None


def _om_max_up_to(om_hourly: list[dict], up_to_hour: float) -> float | None:
    subset = [p["temp"] for p in om_hourly if p["hour"] <= up_to_hour]
    return max(subset) if subset else None


def _source_trend(pts: list[dict], at_hour: float, window: float = 3.0) -> str:
    """RISING / FALLING / FLAT / UNKNOWN based on first vs last temp in window."""
    relevant = [p for p in pts if at_hour - window <= p["hour"] <= at_hour]
    if len(relevant) < 2:
        return "UNKNOWN"
    delta = relevant[-1]["temp"] - relevant[0]["temp"]
    if delta > 0.3:
        return "RISING"
    if delta < -0.3:
        return "FALLING"
    return "FLAT"


def _synop_velocity(synop_readings: list[dict], at_hour: float, window: float = 3.0) -> float:
    relevant = [p for p in synop_readings if at_hour - window <= p["hour"] <= at_hour]
    if len(relevant) < 2:
        return 0.0
    return relevant[-1]["temp"] - relevant[0]["temp"]


def should_block_risky_signal(
    signal_hour: float,
    running_high: float,
    bracket_lo: float | None,
    metar_history: list[dict],
    synop_readings: list[dict],
    om_hourly: list[dict],
    forecast_high: float | None = None,
) -> tuple[bool, list[str]]:
    """Check 6 safeguards for Ceiling NO / Locked-In YES.
    Returns (should_block, list_of_reasons)."""
    reasons: list[str] = []

    # Guard 1: OM peak hour — if peak is AFTER signal time, temp hasn't peaked
    peak_h = _om_peak_hour(om_hourly)
    if peak_h is not None and peak_h > signal_hour:
        reasons.append(f"OM peak at {int(peak_h)}:00 > signal at {int(signal_hour)}:00")

    # Guard 2: OM remaining max — if OM says higher temps are coming
    rem_max = _om_remaining_max(om_hourly, signal_hour)
    if rem_max is not None and rem_max > running_high + 0.5:
        reasons.append(f"OM remaining max {rem_max:.1f}°C > running high {running_high}°C")

    # Guard 3: OM forecast vs bracket
    if om_hourly and bracket_lo is not None:
        om_high = max(p["temp"] for p in om_hourly)
        corrected = om_high + OPENMETEO_BIAS_CORRECTION
        if corrected >= bracket_lo - 1.0:
            reasons.append(f"OM high {corrected:.1f}°C near bracket {bracket_lo}°C")

    # Guard 4: multi-source trend — any source rising ⇒ block
    wu_trend = _source_trend(metar_history, signal_hour)
    syn_trend = _source_trend(synop_readings, signal_hour)
    om_trend = _source_trend(
        [{"hour": p["hour"], "temp": p["temp"]} for p in om_hourly], signal_hour)
    rising = [name for name, t in [("METAR", wu_trend), ("SYNOP", syn_trend), ("OM", om_trend)]
              if t == "RISING"]
    if rising:
        reasons.append(f"Rising trend: {', '.join(rising)}")

    # Guard 5: SYNOP velocity
    vel = _synop_velocity(synop_readings, signal_hour)
    if vel > 0.3:
        reasons.append(f"SYNOP +{vel:.1f}°C/3h")

    # Guard 6: Peak-reached check — only allow if daily high >= forecast - 0.5°C
    if forecast_high is not None and running_high < forecast_high - 0.5:
        reasons.append(f"Peak not reached: high {running_high:.1f}°C < forecast {forecast_high:.1f}°C - 0.5")

    return (len(reasons) > 0, reasons)


# ── Polymarket market fetching ────────────────────────────────────────────────

async def fetch_temperature_event(session: aiohttp.ClientSession,
                                   slug: str) -> list[dict]:
    try:
        async with session.get(
            GAMMA_URL,
            params={"slug": slug},
            timeout=aiohttp.ClientTimeout(total=10),
        ) as r:
            if r.status != 200:
                return []
            data = await r.json()
            if not isinstance(data, list) or not data:
                return []
            event   = data[0]
            markets = event.get("markets") or []
            result  = []
            for m in markets:
                q      = m.get("question") or ""
                prices = m.get("outcomePrices") or "[]"
                outs   = m.get("outcomes") or "[]"
                try:
                    prices = json.loads(prices) if isinstance(prices, str) else prices
                    outs   = json.loads(outs)   if isinstance(outs, str)   else outs
                except Exception:
                    continue
                yes_price  = float(prices[0]) if prices else None
                no_price   = float(prices[1]) if len(prices) > 1 else None
                vol        = float(m.get("volume") or 0)
                closed     = bool(m.get("closed"))
                temp_range = extract_range(q)
                tokens     = []
                try:
                    token_ids = m.get("clobTokenIds") or "[]"
                    tokens = json.loads(token_ids) if isinstance(token_ids, str) else token_ids
                except Exception:
                    pass
                result.append({
                    "question":   q,
                    "temp_range": temp_range,
                    "yes_price":  yes_price,
                    "no_price":   no_price,
                    "volume":     round(vol, 2),
                    "slug":       m.get("slug") or "",
                    "token_id":   tokens[0] if tokens else "",
                    "no_token_id": tokens[1] if len(tokens) > 1 else "",
                    "closed":     closed,
                })
            return result
    except Exception as e:
        logger.warning("fetch_temperature_event(%s): %s", slug, e)
        return []


def extract_range(question: str) -> tuple[float | None, float | None]:
    """Parse Paris-style single-degree Celsius brackets.
    
    Formats:
      "be 14°C on ..."          → (14, 14)   exact degree
      "be 9°C or below on ..."  → (None, 9)  floor bracket
      "be 17°C or higher on..." → (17, None)  ceiling bracket
    """
    q = question.replace("\u00b0", "")
    m = re.search(r"be\s+(\d+)\s*C\s+or\s+below", q)
    if m:
        return None, float(m.group(1))
    m = re.search(r"be\s+(\d+)\s*C\s+or\s+higher", q)
    if m:
        return float(m.group(1)), None
    m = re.search(r"be\s+(\d+)\s*C\s+on", q)
    if m:
        val = float(m.group(1))
        return val, val
    return None, None


def range_label(lo: float | None, hi: float | None) -> str:
    if lo is None and hi is not None:
        return f"<={hi:.0f}°C"
    if hi is None and lo is not None:
        return f">={lo:.0f}°C"
    if lo is not None and hi is not None:
        if lo == hi:
            return f"{lo:.0f}°C"
        return f"{lo:.0f}-{hi:.0f}°C"
    return "?"


# ── Signal detection ──────────────────────────────────────────────────────────

def detect_signals(markets: list[dict],
                   daily_high: float,
                   local_now: datetime,
                   forecast_high: float | None = None,
                   om_trend: str | None = None,
                   *,
                   om_hourly: list[dict] | None = None,
                   metar_history: list[dict] | None = None,
                   synop_history: list[dict] | None = None,
                   dynamic_bias: float | None = None,
                   dynamic_forecast: float | None = None) -> list[dict]:
    """
    Enhanced 5-layer signal detection.
    Layers 1-2: Floor NO (T1 certain, T2 forecast lower brackets)
    Layer 3: T2 Upper (forecast kill on upper brackets with safety checks)
    Layer 4: Midday T2 (noon reassessment with tighter buffer)
    Layer 5: GUARANTEED_NO_CEIL (dormant - collecting data)
    
    LOCKED_IN_YES removed - too risky, never fired in 9 days.
    SUM_ANOMALY signals removed - market inefficiency doesn't tell you which bracket wins.
    """
    signals = []
    hour_local = local_now.hour + local_now.minute / 60
    om_hourly = om_hourly or []
    metar_history = metar_history or []
    synop_history = synop_history or []

    open_markets = [m for m in markets if not m["closed"] and m["yes_price"] is not None]
    yes_sum      = sum(m["yes_price"] for m in open_markets)

    for m in open_markets:
        lo, hi     = m["temp_range"]
        label      = range_label(lo, hi)
        yes        = m["yes_price"]
        no         = m["no_price"] or (1.0 - yes)

        # ── Layer 1: FLOOR_NO_CERTAIN (T1) ──────────────────────────────────
        if hi is not None and daily_high >= hi + ROUNDING_BUFFER:
            if label not in _killed_brackets:
                _killed_brackets.add(label)
                logger.info("BRACKET KILLED: %s (running high %.1f°C > %.1f°C)",
                            label, daily_high, hi + ROUNDING_BUFFER)
            if yes > MIN_YES_FOR_ALERT:
                signals.append({
                    "type":        "FLOOR_NO_CERTAIN",
                    "tier":        1,
                    "our_side":    "NO",
                    "range":       label,
                    "yes_price":   yes,
                    "no_price":    no,
                    "entry_price": no,
                    "edge":        round(yes, 3),
                    "note":        f"[T1 — CERTAIN] daily_high={daily_high}°C passed {label}",
                    "daily_high":  daily_high,
                    "token_id":    m.get("no_token_id", ""),
                })
            continue

        # ── Layer 2: FLOOR_NO_FORECAST (T2 Lower) ───────────────────────────
        if (hi is not None
                and forecast_high is not None
                and forecast_high - hi >= FORECAST_KILL_BUFFER
                and yes > MIN_YES_FOR_ALERT):
            skip_reason = None
            if om_trend == "FALLING" and hour_local < 12:
                skip_reason = "OM trend FALLING in morning"
            if not skip_reason:
                signals.append({
                    "type":        "FLOOR_NO_FORECAST",
                    "tier":        2,
                    "our_side":    "NO",
                    "range":       label,
                    "yes_price":   yes,
                    "no_price":    no,
                    "entry_price": no,
                    "edge":        round(yes, 3),
                    "note":        (f"[T2 — FORECAST] forecast={forecast_high}°C, "
                                   f"bracket_top={hi}°C, gap={forecast_high - hi:.1f}°C"),
                    "daily_high":  daily_high,
                    "forecast_high": forecast_high,
                    "token_id":    m.get("no_token_id", ""),
                })
        
        # ── Layer 2b: FLOOR_NO_FORECAST_TIGHT (T2 Lower with 3.5°C buffer - DORMANT) ───
        # Tighter buffer validated on 8 Paris days (33 signals, 0 wrong)
        # Dormant - collecting data for 30+ days before activating
        # Only fires if regular T2 (4.0°C) didn't fire
        elif (hi is not None
                and forecast_high is not None
                and forecast_high - hi >= FORECAST_KILL_BUFFER_TIGHT
                and forecast_high - hi < FORECAST_KILL_BUFFER
                and yes > MIN_YES_FOR_ALERT
                and CITY == "paris"):  # Only for Paris initially
            skip_reason = None
            if om_trend == "FALLING" and hour_local < 12:
                skip_reason = "OM trend FALLING in morning"
            if not skip_reason:
                # Log but don't trade
                logger.info("T2_TIGHT DORMANT (would fire): %s gap=%.1f°C YES=%.1f%% [3.5°C buffer]",
                           label, forecast_high - hi, yes * 100)
                log_event({
                    "event": "dormant_signal",
                    "type": "FLOOR_NO_FORECAST_TIGHT",
                    "range": label,
                    "yes_price": yes,
                    "gap": round(forecast_high - hi, 1),
                    "note": f"[T2 TIGHT — DORMANT] forecast={forecast_high}°C, bracket_top={hi}°C, gap={forecast_high - hi:.1f}°C"
                })

        # ── Layer 3: T2_UPPER (upper brackets killed by low forecast) ───────
        if (lo is not None
                and hi is None
                and dynamic_forecast is not None
                and yes > MIN_YES_FOR_ALERT):
            gap = lo - dynamic_forecast
            om_underforecasting = (dynamic_bias or 0) > DYNAMIC_BIAS_DANGER
            om_hourly_max_adj = None
            if om_hourly:
                om_raw_max = max(p["temp"] for p in om_hourly)
                om_hourly_max_adj = om_raw_max + OPENMETEO_BIAS_CORRECTION + max(0, dynamic_bias or 0)

            if (gap >= UPPER_KILL_BUFFER
                    and not om_underforecasting
                    and (om_hourly_max_adj is None or om_hourly_max_adj < lo - 1.0)):
                signals.append({
                    "type":        "T2_UPPER",
                    "tier":        2,
                    "our_side":    "NO",
                    "range":       label,
                    "yes_price":   yes,
                    "no_price":    no,
                    "entry_price": no,
                    "edge":        round(yes, 3),
                    "note":        (f"[T2 UPPER] dyn_forecast={dynamic_forecast}°C, "
                                   f"bracket={lo}°C, gap={gap:.1f}°C, bias={dynamic_bias:+.1f}°C"),
                    "daily_high":  daily_high,
                    "token_id":    m.get("no_token_id", ""),
                })
            elif gap >= UPPER_KILL_BUFFER:
                reasons = []
                if om_underforecasting:
                    reasons.append(f"OM underforecasting ({dynamic_bias:+.1f}°C)")
                if om_hourly_max_adj is not None and om_hourly_max_adj >= lo - 1.0:
                    reasons.append(f"OM adj max {om_hourly_max_adj:.1f}°C near bracket")
                logger.info("T2_UPPER BLOCKED on %s: %s", label, "; ".join(reasons))
                _daily_stats["signals_blocked"] += 1

        # ── Layer 3b: T2 Upper for exact brackets ───────────────────────────
        if (lo is not None
                and hi is not None
                and lo == hi
                and dynamic_forecast is not None
                and yes > MIN_YES_FOR_ALERT):
            gap = lo - dynamic_forecast
            om_underforecasting = (dynamic_bias or 0) > DYNAMIC_BIAS_DANGER
            if gap >= UPPER_KILL_BUFFER and not om_underforecasting:
                om_hourly_max_adj = None
                if om_hourly:
                    om_raw_max = max(p["temp"] for p in om_hourly)
                    om_hourly_max_adj = om_raw_max + OPENMETEO_BIAS_CORRECTION + max(0, dynamic_bias or 0)
                if om_hourly_max_adj is None or om_hourly_max_adj < lo - 1.0:
                    signals.append({
                        "type":        "T2_UPPER",
                        "tier":        2,
                        "our_side":    "NO",
                        "range":       label,
                        "yes_price":   yes,
                        "no_price":    no,
                        "entry_price": no,
                        "edge":        round(yes, 3),
                        "note":        (f"[T2 UPPER EXACT] dyn_forecast={dynamic_forecast}°C, "
                                       f"bracket={lo}°C, gap={gap:.1f}°C"),
                        "daily_high":  daily_high,
                        "token_id":    m.get("no_token_id", ""),
                    })

        # ── Layer 4: MIDDAY_T2 (noon reassessment) ──────────────────────────
        if (MIDDAY_HOUR <= hour_local <= MIDDAY_HOUR + 1
                and not _midday_reassessment_done
                and om_hourly
                and yes > MIN_YES_FOR_ALERT):
            dyn_b = dynamic_bias or 0
            om_rem = _om_remaining_max(om_hourly, MIDDAY_HOUR)
            om_sofar = _om_max_up_to(om_hourly, MIDDAY_HOUR)
            est_remaining_rise = max(0, (om_rem or 0) - (om_sofar or 0))
            est_final = daily_high + est_remaining_rise + max(0, dyn_b) * 0.5

            # Lower brackets: running high already far above
            if hi is not None and daily_high - hi >= MIDDAY_KILL_BUFFER and est_final > hi + 1:
                signals.append({
                    "type":        "MIDDAY_T2",
                    "tier":        2,
                    "our_side":    "NO",
                    "range":       label,
                    "yes_price":   yes,
                    "no_price":    no,
                    "entry_price": no,
                    "edge":        round(yes, 3),
                    "note":        (f"[MIDDAY] rh={daily_high}°C, bracket_top={hi}°C, "
                                   f"est_final={est_final:.1f}°C"),
                    "daily_high":  daily_high,
                    "token_id":    m.get("no_token_id", ""),
                })

            # Upper brackets: estimated final high far below
            if lo is not None and lo - est_final >= MIDDAY_KILL_BUFFER:
                signals.append({
                    "type":        "MIDDAY_T2",
                    "tier":        2,
                    "our_side":    "NO",
                    "range":       label,
                    "yes_price":   yes,
                    "no_price":    no,
                    "entry_price": no,
                    "edge":        round(yes, 3),
                    "note":        (f"[MIDDAY UPPER] bracket={lo}°C, "
                                   f"est_final={est_final:.1f}°C, gap={lo - est_final:.1f}°C"),
                    "daily_high":  daily_high,
                    "token_id":    m.get("no_token_id", ""),
                })

        # ── Layer 5: GUARANTEED_NO_CEIL (guarded - DORMANT) ────────────────
        # Currently dormant - collecting data to evaluate future viability
        # Historical (9 days): 0 trades executed, 3 signals blocked (2 would have lost)
        # Issue: Only fires when dangerous (large gap = temps rising)
        #        Safe signals (small gap) have no edge (YES < 1%)
        # Keeping code for future analysis with more data
        if lo is not None and int(hour_local) >= LATE_DAY_HOUR:
            gap = lo - daily_high
            if gap >= CEIL_GAP and yes > MIN_YES_FOR_ALERT:
                blocked, reasons = should_block_risky_signal(
                    hour_local, daily_high, lo,
                    metar_history, synop_history, om_hourly, forecast_high)
                if not blocked:
                    # Signal would fire here, but keeping dormant for now
                    logger.info("CEIL_NO DORMANT (would fire): %s gap=%.1f°C YES=%.1f%% [guards passed]",
                               label, gap, yes * 100)
                    # Uncomment below to activate:
                    # signals.append({
                    #     "type":        "GUARANTEED_NO_CEIL",
                    #     "tier":        0,
                    #     "our_side":    "NO",
                    #     "range":       label,
                    #     "yes_price":   yes,
                    #     "no_price":    no,
                    #     "entry_price": no,
                    #     "edge":        round(yes, 3),
                    #     "note":        (f"daily_high={daily_high}°C, bracket={lo}°C, "
                    #                    f"gap={gap:.1f}°C, hour={int(hour_local)} CET "
                    #                    f"[ALL 5 GUARDS PASSED]"),
                    #     "daily_high":  daily_high,
                    #     "token_id":    m.get("no_token_id", ""),
                    # })
                else:
                    logger.info("CEIL_NO BLOCKED on %s: %s", label, "; ".join(reasons))
                    _daily_stats["signals_blocked"] += 1

        # ── Layer 5: LOCKED_IN_YES (REMOVED) ────────────────────────────────
        # LOCKED_IN_YES removed - too risky, never fired in 9 days
        # Would buy YES on bracket containing daily_high after 5 PM
        # Historical: 0 trades executed, 5 signals blocked (all would have lost)
        # Risk/reward doesn't justify keeping it

    # ── SUM_ANOMALY REMOVED ─────────────────────────────────────────────────
    # SUM_UNDERPRICED and SUM_OVERPRICED signals removed because:
    # - Market sum anomaly doesn't tell you WHICH bracket is mispriced
    # - You still need to pick the right bracket to win
    # - "Closest to current temp" heuristic fails when temps are rising
    # - These signals caused the Feb 23 losses (11°C and 14°C buys)
    # Only Floor NO (T1/T2/T2_UPPER/MIDDAY_T2) strategies remain - they have
    # mathematical certainty or high-confidence forecasts.

    return signals


# ── Main observation cycle ────────────────────────────────────────────────────

async def run_observation(session: aiohttp.ClientSession) -> None:
    global daily_high_c, _morning_summary_sent
    global _dynamic_bias, _dynamic_forecast, _midday_reassessment_done

    maybe_reset_daily_high()

    # 1. Get current temperature from all sources (primary + secondary)
    obs = await fetch_metar(session)
    if obs is None:
        logger.warning("METAR unavailable. Skipping this cycle.")
        return

    temp_c = obs["temp_c"]
    local_now = now_local()
    hour_f = local_now.hour + local_now.minute / 60

    # Accumulate METAR reading for trend analysis
    _metar_readings.append({"hour": hour_f, "temp": temp_c})

    # Fetch secondary sources concurrently (non-blocking, failures are OK)
    synop_data, om_data = await asyncio.gather(
        fetch_synop(session),
        fetch_openmeteo(session),
        return_exceptions=True,
    )
    if isinstance(synop_data, Exception):
        synop_data = _last_synop
    if isinstance(om_data, Exception):
        om_data = _last_openmeteo

    # Accumulate SYNOP reading (avoid duplicates by hour)
    if synop_data and not isinstance(synop_data, Exception):
        s_temp = synop_data.get("temp_c")
        s_hour = synop_data.get("hour_utc")
        if s_temp is not None and s_hour is not None:
            cet_hour = (s_hour + 1) % 24  # UTC → CET (winter)
            existing_hours = {round(p["hour"]) for p in _synop_readings}
            if cet_hour not in existing_hours:
                _synop_readings.append({"hour": cet_hour, "temp": s_temp})

    if daily_high_c is None or temp_c > daily_high_c:
        if daily_high_c is not None:
            logger.info("New daily high: %.1f°C (was %.1f°C)", temp_c, daily_high_c)
        daily_high_c = temp_c

    obs["daily_high_c"] = daily_high_c
    if synop_data and not isinstance(synop_data, Exception):
        obs["synop_temp_c"] = synop_data.get("temp_c")
    if om_data and not isinstance(om_data, Exception):
        obs["openmeteo_temp_c"] = om_data.get("temp_c")
        obs["openmeteo_trend"] = om_data.get("trend")
    log_event({"event": "observation", **obs})

    # Primary log line
    logger.info(
        "LFPG: %d°C | daily_high=%d°C | %s CET",
        temp_c, daily_high_c,
        local_now.strftime("%H:%M"),
    )
    parts = []
    if synop_data:
        parts.append(f"SYNOP={synop_data.get('temp_c', '?')}°C")
    if om_data:
        parts.append(f"OpenMeteo={om_data.get('temp_c', '?')}°C ({om_data.get('trend', '?')})")
    if parts:
        logger.info("  Secondary: %s", " | ".join(parts))

    # 2. Fetch OM hourly forecast (once per day)
    if not _om_hourly_forecast:
        await fetch_openmeteo_hourly(session)

    # 3. Fetch today's markets
    today  = local_now.date()
    slug   = date_slug(today)
    markets = await fetch_temperature_event(session, slug)

    if not markets:
        logger.info("No active markets found for slug: %s", slug)
        return

    open_markets = [m for m in markets if not m["closed"]]
    logger.info("Markets for %s: %d open / %d total", slug, len(open_markets), len(markets))

    # 4. Print comparison table
    yes_sum = sum(m["yes_price"] for m in open_markets if m["yes_price"])
    synop_str = f"  SYNOP: {synop_data['temp_c']}°C" if synop_data else ""
    om_str = ""
    if om_data:
        om_str = f"  OpenMeteo: {om_data.get('temp_c', '?')}°C ({om_data.get('trend', '?')})"

    print(f"\n  {local_now.strftime('%H:%M')} CET  |  METAR: {temp_c}°C  Daily high: {daily_high_c}°C  |  YES sum: {yes_sum:.2f}", flush=True)
    if synop_str or om_str:
        print(f"  {synop_str}{om_str}", flush=True)
    if _dynamic_bias is not None:
        print(f"  Dynamic bias: {_dynamic_bias:+.1f}°C  |  Dynamic forecast: {_dynamic_forecast}°C", flush=True)
    print(f"  {'Range':<14} {'YES%':>6}  {'NO%':>6}  {'Vol':>8}  Status", flush=True)
    print(f"  {'-'*60}", flush=True)

    for m in markets:
        lo, hi    = m["temp_range"]
        label     = range_label(lo, hi)
        yes_pct   = f"{m['yes_price']*100:.1f}%" if m["yes_price"] is not None else "n/a"
        no_pct    = f"{(1-m['yes_price'])*100:.1f}%" if m["yes_price"] is not None else "n/a"
        vol_str   = f"${m['volume']:,.0f}"
        status    = "[CLOSED]" if m["closed"] else ""

        if not m["closed"] and m["yes_price"] is not None and daily_high_c is not None:
            if hi is not None and daily_high_c >= hi + ROUNDING_BUFFER:
                status = "[T1] DEAD — buy NO"
            elif (hi is not None and _forecast_high_c is not None
                  and _forecast_high_c - hi >= FORECAST_KILL_BUFFER):
                status = "[T2] FORECAST DEAD"
            elif (lo is not None and hi is None and _dynamic_forecast is not None
                  and lo - _dynamic_forecast >= UPPER_KILL_BUFFER):
                status = "[T2↑] UPPER DEAD"
            elif hi is not None and lo is not None and lo - ROUNDING_BUFFER <= daily_high_c <= hi + ROUNDING_BUFFER:
                status = "<-- CURRENT BRACKET"
            elif lo is not None and local_now.hour >= LATE_DAY_HOUR and (lo - daily_high_c) >= 2.0:
                status = "   too high, dead"

        print(f"  {label:<14} {yes_pct:>6}  {no_pct:>6}  {vol_str:>8}  {status}", flush=True)

    print(flush=True)

    # 5. Log market snapshot
    log_event({
        "event":        "market_snapshot",
        "slug":         slug,
        "current_c":    temp_c,
        "daily_high_c": daily_high_c,
        "local_hour":   local_now.hour,
        "yes_sum":      round(yes_sum, 4),
        "dynamic_bias": _dynamic_bias,
        "dynamic_forecast": _dynamic_forecast,
        "markets": [{
            "range":     f"{m['temp_range']}",
            "yes_price": m["yes_price"],
            "no_price":  m["no_price"],
            "volume":    m["volume"],
            "closed":    m["closed"],
        } for m in markets],
    })

    # 6. Fetch forecast high (once per day)
    if _forecast_high_c is None:
        await fetch_openmeteo_forecast_high(session)
        if _forecast_high_c is not None:
            logger.info("Forecast high: %.1f°C (OM + %.1f°C bias)",
                        _forecast_high_c, OPENMETEO_BIAS_CORRECTION)

    # 7. Compute dynamic bias at 9am (once per day)
    if _dynamic_bias is None and local_now.hour >= 9 and _om_hourly_forecast and _metar_readings:
        _dynamic_bias = round(compute_dynamic_bias(_metar_readings, _om_hourly_forecast, 9), 2)
        _dynamic_forecast = round(
            (_forecast_high_c or 0) + max(0, _dynamic_bias), 1
        ) if _forecast_high_c else None
        logger.info("Dynamic bias at 9am: %+.2f°C → dynamic forecast: %s°C",
                    _dynamic_bias, _dynamic_forecast)

    # 8. Mark midday reassessment window
    if local_now.hour >= MIDDAY_HOUR + 1 and not _midday_reassessment_done:
        _midday_reassessment_done = True

    # 9. Detect and alert on signals
    om_trend = om_data.get("trend") if om_data else None
    signals = detect_signals(
        markets, daily_high_c, local_now,
        forecast_high=_forecast_high_c,
        om_trend=om_trend,
        om_hourly=_om_hourly_forecast,
        metar_history=_metar_readings,
        synop_history=_synop_readings,
        dynamic_bias=_dynamic_bias,
        dynamic_forecast=_dynamic_forecast,
    )

    for sig in signals:
        sig_key = f"{sig['type']}::{sig['range']}::{today}"
        if sig_key in _fired_signals:
            continue
        _fired_signals.add(sig_key)
        _daily_stats["signals_fired"] += 1  # Track daily signals

        tier = sig.get("tier", 0)
        tier_tag = f" [TIER {tier}]" if tier else ""
        logger.info("SIGNAL%s [%s] %s %s @ %.3f — edge=%.3f | %s",
                    tier_tag, sig["type"], sig["our_side"], sig["range"],
                    sig["entry_price"], sig["edge"], sig["note"])

        log_event({"event": "signal", **sig, "slug": slug})

        if tier == 1:
            emoji = "🟢"
            conf = "CERTAIN"
        elif tier == 2:
            emoji = "🟡"
            conf = "FORECAST"
        elif sig["type"] in ("GUARANTEED_NO_CEIL", "LOCKED_IN_YES"):
            emoji = "🟣"
            conf = f"{sig['type']} (GUARDED)"
        else:
            emoji = "🔵"
            conf = sig["type"]
        tg_msg = (
            f"{emoji} <b>{conf}: {sig['our_side']} on {sig['range']}</b>\n"
            f"Entry: {sig['entry_price']:.2f} | Edge: {sig['edge']:.3f}\n"
            f"{sig['note']}"
        )
        await notify_telegram(session, tg_msg)

    # 10. Morning summary at 9:00 CET
    if local_now.hour >= 9 and not _morning_summary_sent and markets:
        _morning_summary_sent = True
        await _send_morning_summary(session, markets, daily_high_c, local_now)


async def _send_morning_summary(session: aiohttp.ClientSession,
                                markets: list[dict],
                                daily_high: float,
                                local_now: datetime) -> None:
    """Send a 9am CET summary of all dead brackets (T1 + T2 + T2 Upper)."""
    tier1_dead = []
    tier2_dead = []
    t2_upper_dead = []

    for m in markets:
        if m["closed"]:
            continue
        lo, hi = m["temp_range"]
        label = range_label(lo, hi)
        yes = m["yes_price"]

        if hi is not None and daily_high >= hi + ROUNDING_BUFFER:
            tier1_dead.append((label, yes))
        elif (hi is not None and _forecast_high_c is not None
              and _forecast_high_c - hi >= FORECAST_KILL_BUFFER):
            tier2_dead.append((label, yes))
        elif (lo is not None and _dynamic_forecast is not None
              and lo - _dynamic_forecast >= UPPER_KILL_BUFFER
              and (_dynamic_bias or 0) <= DYNAMIC_BIAS_DANGER):
            t2_upper_dead.append((label, yes))

    lines = [
        f"<b>Morning Summary — {local_now.strftime('%b %d, %H:%M CET')}</b>",
        f"Running high: {daily_high}°C",
    ]
    if _forecast_high_c is not None:
        lines.append(f"Forecast high: {_forecast_high_c}°C (OM +{OPENMETEO_BIAS_CORRECTION}°C)")
    if _dynamic_bias is not None:
        lines.append(f"Dynamic bias: {_dynamic_bias:+.1f}°C → adjusted: {_dynamic_forecast}°C")

    if tier1_dead:
        lines.append(f"\n🟢 <b>TIER 1 — CERTAIN</b>:")
        for label, yes in tier1_dead:
            lines.append(f"  - {label} — YES={yes:.0%}" if yes else f"  - {label}")
    else:
        lines.append("\n🟢 TIER 1: none yet")

    if tier2_dead:
        lines.append(f"\n🟡 <b>TIER 2 — FORECAST</b> (≥{FORECAST_KILL_BUFFER}°C buffer):")
        for label, yes in tier2_dead:
            lines.append(f"  - {label} — YES={yes:.0%}" if yes else f"  - {label}")
    else:
        lines.append("\n🟡 TIER 2: none")

    if t2_upper_dead:
        lines.append(f"\n🟡 <b>T2 UPPER</b> (≥{UPPER_KILL_BUFFER}°C below forecast):")
        for label, yes in t2_upper_dead:
            lines.append(f"  - {label} — YES={yes:.0%}" if yes else f"  - {label}")

    alive_labels = {l for l, _ in tier1_dead + tier2_dead + t2_upper_dead}
    alive = [m for m in markets if not m["closed"]
             and range_label(*m["temp_range"]) not in alive_labels]
    if alive:
        lines.append(f"\nStill alive: {', '.join(range_label(*m['temp_range']) for m in alive)}")

    lines.append(f"\n⏰ Midday T2 at {MIDDAY_HOUR}:00 | Ceiling NO guards at {LATE_DAY_HOUR}:00 | Lock-In guards at {LOCK_IN_HOUR}:00")

    msg = "\n".join(lines)
    logger.info("MORNING SUMMARY:\n%s", msg)
    await notify_telegram(session, msg)


def _log_daily_summary(summary_date: date) -> None:
    """Log daily summary with data quality metrics."""
    global _daily_stats, daily_high_c, _metar_readings, _synop_readings
    global _forecast_high_c, _dynamic_bias
    
    # Calculate SYNOP high/low from readings
    synop_high = max((r["temp"] for r in _synop_readings), default=None) if _synop_readings else None
    synop_low = min((r["temp"] for r in _synop_readings), default=None) if _synop_readings else None
    
    # Calculate METAR low from readings (high is tracked as daily_high_c)
    metar_low = min((r["temp"] for r in _metar_readings), default=None) if _metar_readings else None
    
    # Calculate actual OM error if we have both forecast and actual
    actual_om_error = None
    if _forecast_high_c is not None and daily_high_c is not None:
        # forecast_high_c already includes +1.0°C bias correction
        # So error = actual - (OM_raw + 1.0) = actual - forecast_high_c
        actual_om_error = round(daily_high_c - _forecast_high_c, 1)
    
    summary = {
        "type": "daily_summary",
        "date": summary_date.isoformat(),
        "city": CITY,
        "wu_high": daily_high_c,  # METAR = WU (verified perfect correlation)
        "wu_low": metar_low,
        "synop_high": synop_high,
        "synop_low": synop_low,
        "openmeteo_forecast_high": _forecast_high_c,
        "openmeteo_bias_correction": OPENMETEO_BIAS_CORRECTION,
        "corrected_forecast": _forecast_high_c,
        "actual_om_error": actual_om_error,
        "dynamic_bias_9am": _dynamic_bias,
        "signals_fired": _daily_stats["signals_fired"],
        "signals_blocked": _daily_stats["signals_blocked"],
        "metar_readings_count": len(_metar_readings),
        "synop_readings_count": len(_synop_readings),
    }
    
    log_event(summary)
    logger.info("DAILY SUMMARY for %s: high=%.1f°C, forecast=%.1f°C, error=%s°C, signals=%d",
                summary_date, daily_high_c or 0, _forecast_high_c or 0, 
                actual_om_error if actual_om_error is not None else "?",
                _daily_stats["signals_fired"])


# ── Main ──────────────────────────────────────────────────────────────────────

async def main() -> None:
    global _shutdown

    def _handle_shutdown(signum, frame):
        global _shutdown
        _shutdown = True
        logger.info("Stopping...")

    if hasattr(signal_module, "SIGINT"):
        signal_module.signal(signal_module.SIGINT, _handle_shutdown)
    if hasattr(signal_module, "SIGTERM"):
        signal_module.signal(signal_module.SIGTERM, _handle_shutdown)

    logger.info("=" * 65)
    logger.info("  PARIS TEMPERATURE MARKET — ENHANCED 5-LAYER STRATEGY")
    logger.info("=" * 65)
    logger.info("  Sources:    METAR (1°C) | SYNOP (0.1°C) | Open-Meteo (hourly)")
    logger.info("  Layer 1:    Floor T1 — running high kills bracket (certain)")
    logger.info("  Layer 2:    Floor T2 — forecast kills lower bracket (%.0f°C buffer)", FORECAST_KILL_BUFFER)
    logger.info("  Layer 3:    T2 Upper — forecast kills upper bracket (%.0f°C + bias check)", UPPER_KILL_BUFFER)
    logger.info("  Layer 4:    Midday T2 — noon reassessment (%.1f°C buffer)", MIDDAY_KILL_BUFFER)
    logger.info("  Layer 5:    Guarded Ceiling NO / Lock-In YES (6 safeguards)")
    logger.info("  Guards:     OM peak hour | OM remaining max | OM vs bracket | trend | SYNOP velocity | peak reached")
    logger.info("  Poll:       %dmin (day) / %dmin (night)", POLL_MIN_DAY, POLL_MIN_NIGHT)
    logger.info("  Telegram:   %s", "enabled" if TELEGRAM_TOKEN else "disabled (no .env)")
    logger.info("  Log:        %s", LOG_FILE)
    logger.info("=" * 65)

    async with aiohttp.ClientSession() as session:
        while not _shutdown:
            try:
                await run_observation(session)
            except Exception as e:
                logger.error("Observation cycle error: %s", e, exc_info=True)
            if _shutdown:
                break
            poll = poll_interval_minutes()
            logger.info("Next observation in %d minutes...", poll)
            for _ in range(poll * 6):
                if _shutdown:
                    break
                await asyncio.sleep(10)

    logger.info("Stopped. Log saved to %s", LOG_FILE)


if __name__ == "__main__":
    asyncio.run(main())
