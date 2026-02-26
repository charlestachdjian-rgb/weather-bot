"""
NYC temperature market backtester — same analysis as Paris but with ~20 days of data.
Station: KLGA (La Guardia). Units: Fahrenheit for NYC markets.
"""
import urllib.request, json, re, sys, time
from datetime import datetime, date, timezone, timedelta
from zoneinfo import ZoneInfo

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

EST = ZoneInfo("America/New_York")
GAMMA_URL = "https://gamma-api.polymarket.com/events"
CLOB_URL = "https://clob.polymarket.com/prices-history"

NYC_DAYS = [date(2026, 2, d) for d in range(3, 23)]


def slug_for_date(d):
    month = d.strftime('%B').lower()
    return f"highest-temperature-in-nyc-on-{month}-{d.day}-{d.year}"


def extract_range_f(question):
    """Parse bracket from NYC market question (Fahrenheit).
    Formats: 'be between 32-33°F', 'be 29°F or below', 'be 40°F or higher'
    """
    q = question.lower().strip()
    # "between 32-33°f" -> range bracket
    m = re.search(r'between\s+(-?\d+)\s*[-–]\s*(-?\d+)\s*°?\s*f', q)
    if m:
        return (int(m.group(1)), int(m.group(2)))
    # "be 29°f or below/lower"
    m = re.search(r'be\s+(-?\d+)\s*°?\s*f\s+or\s+(?:below|lower)', q)
    if m:
        return (None, int(m.group(1)))
    # "be 40°f or higher"
    m = re.search(r'be\s+(-?\d+)\s*°?\s*f\s+or\s+higher', q)
    if m:
        return (int(m.group(1)), None)
    return None


def range_label(rng):
    if rng is None:
        return "?"
    if rng[0] is None:
        return f"<={rng[1]}F"
    if rng[1] is None:
        return f">={rng[0]}F"
    if rng[0] == rng[1]:
        return f"{rng[0]}F"
    return f"{rng[0]}-{rng[1]}F"


def fetch_event(slug):
    url = f"{GAMMA_URL}?slug={slug}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=15) as r:
        data = json.loads(r.read())
    return data[0] if data else None


def parse_markets(event):
    markets_raw = event.get("markets") or []
    result = []
    for m in markets_raw:
        q = m.get("question") or ""
        rng = extract_range_f(q)
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
        token_ids = m.get("clobTokenIds") or "[]"
        try:
            token_ids = json.loads(token_ids) if isinstance(token_ids, str) else token_ids
        except Exception:
            token_ids = []
        result.append({
            "question": q, "range": rng, "range_label": range_label(rng),
            "yes_price": yes_price, "volume": vol, "closed": closed,
            "resolved_to": resolved_to, "yes_token": token_ids[0] if token_ids else None,
        })
    result.sort(key=lambda m: (m["range"][0] if m["range"][0] is not None else -999))
    return result


def fetch_price_history(token_id, d):
    start_ts = int(datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp())
    end_ts = start_ts + 86400
    url = f"{CLOB_URL}?market={token_id}&startTs={start_ts}&endTs={end_ts}&interval=1h&fidelity=60"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read())
        return [(int(h["t"]), float(h["p"])) for h in data.get("history", []) if h.get("t") and h.get("p")]
    except Exception:
        return []


def fetch_wu_day(d):
    """Fetch WU observations for KLGA in imperial (Fahrenheit)."""
    date_str = d.strftime("%Y%m%d")
    url = (f"https://api.weather.com/v1/location/KLGA:9:US/observations/historical.json"
           f"?apiKey=e1f10a1e78da46f5b10a1e78da96f525&units=e"
           f"&startDate={date_str}&endDate={date_str}")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read())
        obs = data.get("observations", [])
        if not obs:
            return None
        temps = [(o.get("valid_time_gmt", 0), o["temp"]) for o in obs if o.get("temp") is not None]
        if not temps:
            return None
        temp_vals = [t for _, t in temps]
        return {"high": max(temp_vals), "low": min(temp_vals),
                "readings": len(temps), "timeseries": temps}
    except Exception as e:
        print(f"    WU error {d}: {e}")
        return None


# ── Main ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 70)
    print("  NYC TEMPERATURE MARKET — BACKTEST DATA COLLECTOR")
    print("=" * 70)

    all_days = []
    for d in NYC_DAYS:
        slug = slug_for_date(d)
        print(f"\n  {d}...", end=" ", flush=True)

        try:
            ev = fetch_event(slug)
        except Exception as e:
            print(f"event error: {e}")
            continue
        if not ev:
            print("not found")
            continue
        markets = parse_markets(ev)
        print(f"{len(markets)} brackets", end=" ", flush=True)

        # Price histories
        price_histories = {}
        for m in markets:
            if m["yes_token"]:
                ph = fetch_price_history(m["yes_token"], d)
                price_histories[m["range_label"]] = ph
                time.sleep(0.05)
        total_pts = sum(len(v) for v in price_histories.values())

        # WU actual temps
        wu = fetch_wu_day(d)
        wu_str = f"WU_high={wu['high']}°F" if wu else "WU=fail"

        winning = [m for m in markets if m["resolved_to"] == "YES"]
        winning_range = winning[0]["range_label"] if winning else "OPEN"

        print(f"| {wu_str} | {total_pts} prices | -> {winning_range}")

        day_data = {
            "date": d.isoformat(), "slug": slug,
            "closed": bool(ev.get("closed")),
            "winning_bracket": winning[0]["range_label"] if winning else None,
            "wu": {"high": wu["high"], "low": wu["low"],
                   "timeseries": wu["timeseries"]} if wu else None,
            "markets": [{"range_label": m["range_label"], "range": m["range"],
                         "yes_price": m["yes_price"], "volume": m["volume"],
                         "resolved_to": m["resolved_to"]} for m in markets],
            "price_histories": price_histories,
        }
        all_days.append(day_data)

    out_path = r"C:\Users\Charl\Desktop\Cursor\weather-bot\backtest_nyc_data.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({"generated": datetime.now(timezone.utc).isoformat(),
                    "city": "NYC", "station": "KLGA", "unit": "F",
                    "days": all_days}, f, indent=2)
    print(f"\nData saved to {out_path}")

    # ── Quick analysis ───────────────────────────────────────────────────
    resolved = [d for d in all_days if d["closed"] and d.get("winning_bracket")]
    print(f"\n{'='*70}")
    print(f"  QUICK ANALYSIS — {len(resolved)} resolved days")
    print(f"{'='*70}")

    # Resolution distribution
    print("\nResolution summary:")
    for d in resolved:
        wu_h = d["wu"]["high"] if d["wu"] else "?"
        print(f"  {d['date']}: WU_high={wu_h}°F -> {d['winning_bracket']}")

    # Winning bracket price evolution
    print("\nWinning bracket — when did it lock in?")
    for d in resolved:
        wb = d["winning_bracket"]
        ph = d.get("price_histories", {}).get(wb, [])
        if not ph:
            print(f"  {d['date']} ({wb}): no price history")
            continue
        for threshold in [0.5, 0.8, 0.9]:
            above = [(ts, p) for ts, p in sorted(ph) if p >= threshold]
            if above:
                t = datetime.fromtimestamp(above[0][0], tz=EST)
                print(f"  {d['date']} ({wb}): first >{threshold:.0%} at {t.strftime('%H:%M ET')}")
            else:
                print(f"  {d['date']} ({wb}): never reached {threshold:.0%}")

    # Losing brackets that peaked high
    print("\nLosing brackets with YES > 20%:")
    lose_count = 0
    total_profit = 0
    for d in resolved:
        wb = d["winning_bracket"]
        for label, ph in d.get("price_histories", {}).items():
            if label == wb or not ph:
                continue
            max_yes = max(p for _, p in ph)
            if max_yes > 0.20:
                lose_count += 1
                total_profit += max_yes
                peak_ts = [ts for ts, p in ph if p == max_yes][0]
                peak_t = datetime.fromtimestamp(peak_ts, tz=EST)
                print(f"  {d['date']} {label}: peaked {max_yes:.0%} ({peak_t.strftime('%H:%M ET')})")

    print(f"\nTotal mispriced NO opportunities: {lose_count} across {len(resolved)} days")
    print(f"Average per day: {lose_count/len(resolved):.1f}")
    if lose_count:
        print(f"Average profit per trade: ${total_profit/lose_count*100:.0f} per $100")
    print("\nDone.")
