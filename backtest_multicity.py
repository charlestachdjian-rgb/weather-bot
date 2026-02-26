"""
Multi-city temperature market backtester.
Fetches historical Polymarket temp events for multiple cities with CORRECT per-city weather data.
Outputs backtest_multicity_data.json for analysis.
"""
import urllib.request, json, re, sys, time
from datetime import datetime, date, timezone, timedelta
from zoneinfo import ZoneInfo

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

GAMMA_URL = "https://gamma-api.polymarket.com/events"
CLOB_URL = "https://clob.polymarket.com/prices-history"

# City configurations with correct stations and coordinates
CITIES = {
    "paris": {
        "slug": "paris",
        "timezone": "Europe/Paris",
        "metar_station": "LFPG",
        "synop_station": "07157",
        "lat": 49.0097,
        "lon": 2.5479,
        "wu_location": "LFPG:9:FR",
    },
    "london": {
        "slug": "london",
        "timezone": "Europe/London",
        "metar_station": "EGLL",
        "synop_station": "03772",  # Heathrow
        "lat": 51.4700,
        "lon": -0.4543,
        "wu_location": "EGLL:9:UK",
    },
    "nyc": {
        "slug": "nyc",
        "timezone": "America/New_York",
        "metar_station": "KLGA",
        "synop_station": "72503",  # LaGuardia
        "lat": 40.7769,
        "lon": -73.8740,
        "wu_location": "KLGA:9:US",
    },
    "seoul": {
        "slug": "seoul",
        "timezone": "Asia/Seoul",
        "metar_station": "RKSI",
        "synop_station": "47108",  # Incheon
        "lat": 37.4602,
        "lon": 126.4407,
        "wu_location": "RKSI:9:KR",
    },
    "ankara": {
        "slug": "ankara",
        "timezone": "Europe/Istanbul",
        "metar_station": "LTAC",
        "synop_station": "17130",  # Esenboğa
        "lat": 40.1281,
        "lon": 32.9951,
        "wu_location": "LTAC:9:TR",
    },
    "wellington": {
        "slug": "wellington",
        "timezone": "Pacific/Auckland",
        "metar_station": "NZWN",
        "synop_station": "93439",
        "lat": -41.3272,
        "lon": 174.8053,
        "wu_location": "NZWN:9:NZ",
    },
    "buenos-aires": {
        "slug": "buenos-aires",
        "timezone": "America/Argentina/Buenos_Aires",
        "metar_station": "SAEZ",
        "synop_station": "87576",  # Ezeiza
        "lat": -34.8222,
        "lon": -58.5358,
        "wu_location": "SAEZ:9:AR",
    },
    "toronto": {
        "slug": "toronto",
        "timezone": "America/Toronto",
        "metar_station": "CYYZ",
        "synop_station": "71624",  # Pearson
        "lat": 43.6772,
        "lon": -79.6306,
        "wu_location": "CYYZ:9:CA",
    },
}

# Test with Feb 3, 2026 (the date from the buggy backtest_data.json)
TEST_DATES = [
    date(2026, 2, 3),
]


def slug_for_date(city_slug, d):
    month = d.strftime("%B").lower()
    return f"highest-temperature-in-{city_slug}-on-{month}-{d.day}-{d.year}"


def extract_range(question):
    q = question.lower().strip()
    m = re.search(r'be\s+(-?\d+)\s*°?\s*c\b', q)
    if m and 'or' not in q and 'higher' not in q and 'below' not in q:
        val = int(m.group(1))
        return (val, val)
    m = re.search(r'(?:be\s+(-?\d+)\s*°?\s*c\s+or\s+below|≤\s*(-?\d+)\s*°?\s*c)', q)
    if m:
        val = int(m.group(1) or m.group(2))
        return (None, val)
    m = re.search(r'(?:be\s+(-?\d+)\s*°?\s*c\s+or\s+higher|≥\s*(-?\d+)\s*°?\s*c)', q)
    if m:
        val = int(m.group(1) or m.group(2))
        return (val, None)
    return None


def range_label(rng):
    if rng is None:
        return "?"
    if rng[0] is None:
        return f"<={rng[1]}C"
    if rng[1] is None:
        return f">={rng[0]}C"
    return f"{rng[0]}C"


def fetch_event(slug):
    url = f"{GAMMA_URL}?slug={slug}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read())
        if not data:
            return None
        return data[0]
    except Exception as e:
        print(f"ERROR fetching event: {e}")
        return None


def parse_markets(event):
    markets_raw = event.get("markets") or []
    result = []
    for m in markets_raw:
        q = m.get("question") or ""
        rng = extract_range(q)
        if rng is None:
            continue
        prices = m.get("outcomePrices") or "[]"
        try:
            prices = json.loads(prices) if isinstance(prices, str) else prices
        except Exception:
            prices = []
        yes_price = float(prices[0]) if prices else None
        vol = float(m.get("volume") or 0)
        closed = bool(m.get("closed"))

        resolved_to = None
        if closed and yes_price is not None:
            if yes_price > 0.95:
                resolved_to = "YES"
            elif yes_price < 0.05:
                resolved_to = "NO"

        result.append({
            "question": q,
            "range": rng,
            "range_label": range_label(rng),
            "yes_price": yes_price,
            "volume": vol,
            "closed": closed,
            "resolved_to": resolved_to,
        })
    result.sort(key=lambda m: (m["range"][0] if m["range"][0] is not None else -999))
    return result


def fetch_wu_day(city_config, d):
    """Fetch Weather Underground data for specific city."""
    date_str = d.strftime("%Y%m%d")
    wu_loc = city_config["wu_location"]
    url = (f"https://api.weather.com/v1/location/{wu_loc}/observations/historical.json"
           f"?apiKey=e1f10a1e78da46f5b10a1e78da96f525&units=m"
           f"&startDate={date_str}&endDate={date_str}")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read())
        obs = data.get("observations", [])
        if not obs:
            return None
        temps = [o["temp"] for o in obs if o.get("temp") is not None]
        if not temps:
            return None
        return {"high": max(temps), "low": min(temps), "readings": len(temps)}
    except Exception as e:
        print(f"    WU error: {e}")
        return None


def fetch_synop_day(city_config, d):
    """Fetch SYNOP data for specific city station."""
    station = city_config["synop_station"]
    begin = d.strftime("%Y%m%d") + "0000"
    end = (d + timedelta(days=1)).strftime("%Y%m%d") + "0000"
    url = f"https://www.ogimet.com/cgi-bin/getsynop?block={station}&begin={begin}&end={end}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as r:
            text = r.read().decode("utf-8", errors="replace")
        temps = []
        for line in text.splitlines():
            if not line.strip() or line.startswith("#") or not line.startswith(station):
                continue
            m = re.search(r'\b1([01])(\d{3})\b', line)
            if m:
                sign = 1 if m.group(1) == "0" else -1
                temp = sign * int(m.group(2)) / 10.0
                temps.append(temp)
        if not temps:
            return None
        return {"high": max(temps), "low": min(temps), "readings": len(temps)}
    except Exception as e:
        print(f"    SYNOP error: {e}")
        return None


def fetch_openmeteo_day(city_config, d):
    """Fetch OpenMeteo data for specific city coordinates."""
    date_str = d.strftime("%Y-%m-%d")
    days_ago = (datetime.now(timezone.utc).date() - d).days
    base = ("https://api.open-meteo.com/v1/forecast" if days_ago <= 5
            else "https://archive-api.open-meteo.com/v1/archive")
    lat = city_config["lat"]
    lon = city_config["lon"]
    url = (f"{base}?latitude={lat}&longitude={lon}"
           f"&hourly=temperature_2m"
           f"&start_date={date_str}&end_date={date_str}"
           f"&timezone=UTC")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read())
        hourly = data.get("hourly", {})
        temps = [t for t in hourly.get("temperature_2m", []) if t is not None]
        if not temps:
            return None
        return {"high": max(temps), "low": min(temps), "readings": len(temps)}
    except Exception as e:
        print(f"    OM error: {e}")
        return None


# ── Main ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 70)
    print("  POLYMARKET MULTI-CITY TEMPERATURE — BACKTEST DATA COLLECTOR")
    print("=" * 70)
    print(f"\nCities: {', '.join(CITIES.keys())}")
    print(f"Dates: {', '.join(str(d) for d in TEST_DATES)}")

    all_results = []

    for city_key, city_config in CITIES.items():
        for d in TEST_DATES:
            slug = slug_for_date(city_config["slug"], d)
            print(f"\n{'='*60}")
            print(f"  {city_key.upper()} — {d} — {slug}")
            print(f"{'='*60}")

            # Fetch event
            print("  Fetching Polymarket event...", end=" ", flush=True)
            ev = fetch_event(slug)
            if not ev:
                print("not found")
                continue
            markets = parse_markets(ev)
            print(f"{len(markets)} brackets")

            # Fetch actual temps FOR THIS CITY
            print(f"  Fetching Weather Underground ({city_config['wu_location']})...", end=" ", flush=True)
            wu = fetch_wu_day(city_config, d)
            print(f"high={wu['high']}°C" if wu else "failed")

            print(f"  Fetching SYNOP ({city_config['synop_station']})...", end=" ", flush=True)
            synop = fetch_synop_day(city_config, d)
            print(f"high={synop['high']:.1f}°C" if synop else "failed")
            time.sleep(0.5)

            print(f"  Fetching Open-Meteo ({city_config['lat']}, {city_config['lon']})...", end=" ", flush=True)
            om = fetch_openmeteo_day(city_config, d)
            print(f"high={om['high']:.1f}°C" if om else "failed")

            # Determine resolution
            winning = [m for m in markets if m["resolved_to"] == "YES"]
            winning_range = winning[0]["range"] if winning else None

            # Show summary
            print(f"\n  Resolution: {range_label(winning_range) if winning_range else 'OPEN'}")
            print(f"  {'Bracket':<10} {'Final YES':>10} {'Volume':>10} {'Resolved':>10}")
            print(f"  {'-'*45}")
            for m in markets[:5]:  # Show first 5
                yes_str = f"{m['yes_price']:.0%}" if m["yes_price"] is not None else "?"
                res_str = m["resolved_to"] or "-"
                vol_str = f"${m['volume']:,.0f}"
                marker = " <--" if m["resolved_to"] == "YES" else ""
                print(f"  {m['range_label']:<10} {yes_str:>10} {vol_str:>10} {res_str:>10}{marker}")
            if len(markets) > 5:
                print(f"  ... and {len(markets) - 5} more brackets")

            result = {
                "date": d.isoformat(),
                "city": city_key,
                "slug": slug,
                "wu_high": wu["high"] if wu else None,
                "wu_low": wu["low"] if wu else None,
                "synop_high": synop["high"] if synop else None,
                "synop_low": synop["low"] if synop else None,
                "openmeteo_high": om["high"] if om else None,
                "openmeteo_low": om["low"] if om else None,
                "winning_range": winning_range,
                "markets": [{
                    "range": m["range"],
                    "yes_price": m["yes_price"],
                    "volume": m["volume"],
                    "resolved_to": m["resolved_to"],
                } for m in markets],
            }
            all_results.append(result)

            time.sleep(1)  # Rate limiting

    # ── Save data ────────────────────────────────────────────────────────
    output = {
        "generated": datetime.now(timezone.utc).isoformat(),
        "cities": list(CITIES.keys()),
        "results": all_results,
    }
    out_path = "backtest_multicity_data.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2)
    print(f"\n\nData saved to {out_path}")

    # ── Verification ─────────────────────────────────────────────────────
    print("\n" + "=" * 70)
    print("  VERIFICATION: Weather data should be DIFFERENT per city")
    print("=" * 70)

    for d in TEST_DATES:
        print(f"\n{d}:")
        day_results = [r for r in all_results if r["date"] == d.isoformat()]
        for r in day_results:
            wu_h = r["wu_high"] if r["wu_high"] is not None else "?"
            om_h = f"{r['openmeteo_high']:.1f}" if r["openmeteo_high"] is not None else "?"
            win = range_label(r["winning_range"]) if r["winning_range"] else "?"
            print(f"  {r['city']:15} WU={wu_h:>4}°C  OM={om_h:>5}°C  Winner={win}")

    print(f"\nDone. Full data in {out_path}")
