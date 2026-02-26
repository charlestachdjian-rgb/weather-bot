"""
Paris temperature market backtester.
Fetches all historical Polymarket Paris temp events, actual weather data,
and intraday market prices. Outputs backtest_data.json for the report builder.
"""
import urllib.request, json, re, sys, time
from datetime import datetime, date, timezone, timedelta
from zoneinfo import ZoneInfo

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

CET = ZoneInfo("Europe/Paris")
GAMMA_URL = "https://gamma-api.polymarket.com/events"
CLOB_URL = "https://clob.polymarket.com/prices-history"

MARKET_DAYS = [
    date(2026, 2, 11),
    date(2026, 2, 15), date(2026, 2, 16), date(2026, 2, 17),
    date(2026, 2, 18), date(2026, 2, 19), date(2026, 2, 20),
    date(2026, 2, 21), date(2026, 2, 22),
]


def slug_for_date(d):
    return f"highest-temperature-in-paris-on-{d.strftime('%B').lower()}-{d.day}-{d.year}"


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
    with urllib.request.urlopen(req, timeout=15) as r:
        data = json.loads(r.read())
    if not data:
        return None
    return data[0]


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

        token_ids = m.get("clobTokenIds") or "[]"
        try:
            token_ids = json.loads(token_ids) if isinstance(token_ids, str) else token_ids
        except Exception:
            token_ids = []

        result.append({
            "question": q,
            "range": rng,
            "range_label": range_label(rng),
            "yes_price": yes_price,
            "volume": vol,
            "closed": closed,
            "resolved_to": resolved_to,
            "yes_token": token_ids[0] if token_ids else None,
        })
    result.sort(key=lambda m: (m["range"][0] if m["range"][0] is not None else -999))
    return result


def fetch_price_history(token_id, d):
    """Fetch minute-level YES price history for a token on a given date."""
    start_ts = int(datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp())
    end_ts = start_ts + 86400
    url = f"{CLOB_URL}?market={token_id}&startTs={start_ts}&endTs={end_ts}&interval=1h&fidelity=60"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read())
        history = data.get("history", [])
        return [(int(h["t"]), float(h["p"])) for h in history if h.get("t") and h.get("p")]
    except Exception:
        return []


def fetch_wu_day(d):
    date_str = d.strftime("%Y%m%d")
    url = (f"https://api.weather.com/v1/location/LFPG:9:FR/observations/historical.json"
           f"?apiKey=e1f10a1e78da46f5b10a1e78da96f525&units=m"
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


def fetch_synop_day(d):
    begin = d.strftime("%Y%m%d") + "0000"
    end = (d + timedelta(days=1)).strftime("%Y%m%d") + "0000"
    url = f"https://www.ogimet.com/cgi-bin/getsynop?block=07157&begin={begin}&end={end}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as r:
            text = r.read().decode("utf-8", errors="replace")
        data = []
        for line in text.splitlines():
            if not line.strip() or line.startswith("#") or not line.startswith("07157"):
                continue
            parts = line.split(",")
            if len(parts) < 6:
                continue
            hour_utc = int(parts[4])
            m = re.search(r'\b1([01])(\d{3})\b', line)
            if m:
                sign = 1 if m.group(1) == "0" else -1
                temp = sign * int(m.group(2)) / 10.0
                ts = int(datetime(int(parts[1]), int(parts[2]), int(parts[3]),
                                  hour_utc, 0, tzinfo=timezone.utc).timestamp())
                data.append((ts, temp))
        if not data:
            return None
        temp_vals = [t for _, t in data]
        return {"high": max(temp_vals), "low": min(temp_vals),
                "readings": len(data), "timeseries": data}
    except Exception as e:
        print(f"    SYNOP error {d}: {e}")
        return None


def fetch_openmeteo_day(d):
    date_str = d.strftime("%Y-%m-%d")
    days_ago = (datetime.now(timezone.utc).date() - d).days
    base = ("https://api.open-meteo.com/v1/forecast" if days_ago <= 5
            else "https://archive-api.open-meteo.com/v1/archive")
    url = (f"{base}?latitude=49.0097&longitude=2.5479"
           f"&hourly=temperature_2m"
           f"&start_date={date_str}&end_date={date_str}"
           f"&timezone=UTC")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read())
        hourly = data.get("hourly", {})
        times = hourly.get("time", [])
        temps = hourly.get("temperature_2m", [])
        ts_data = []
        for t_str, temp in zip(times, temps):
            if temp is None:
                continue
            dt = datetime.fromisoformat(t_str).replace(tzinfo=timezone.utc)
            ts_data.append((int(dt.timestamp()), temp))
        if not ts_data:
            return None
        temp_vals = [t for _, t in ts_data]
        return {"high": max(temp_vals), "low": min(temp_vals),
                "readings": len(ts_data), "timeseries": ts_data}
    except Exception as e:
        print(f"    OM error {d}: {e}")
        return None


# ── Main ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 70)
    print("  POLYMARKET PARIS TEMPERATURE — BACKTEST DATA COLLECTOR")
    print("=" * 70)

    all_days = []

    for d in MARKET_DAYS:
        slug = slug_for_date(d)
        print(f"\n{'='*60}")
        print(f"  {d} — {slug}")
        print(f"{'='*60}")

        # Fetch event
        print("  Fetching Polymarket event...", end=" ", flush=True)
        try:
            ev = fetch_event(slug)
        except Exception as e:
            print(f"ERROR: {e}")
            continue
        if not ev:
            print("not found")
            continue
        markets = parse_markets(ev)
        print(f"{len(markets)} brackets")

        # Fetch price histories for each bracket
        print("  Fetching price histories...", end=" ", flush=True)
        price_histories = {}
        for m in markets:
            if m["yes_token"]:
                ph = fetch_price_history(m["yes_token"], d)
                price_histories[m["range_label"]] = ph
                time.sleep(0.1)
        total_pts = sum(len(v) for v in price_histories.values())
        print(f"{total_pts} price points across {len(price_histories)} brackets")

        # Fetch actual temps
        print("  Fetching Weather Underground...", end=" ", flush=True)
        wu = fetch_wu_day(d)
        print(f"high={wu['high']}°C" if wu else "failed")

        print("  Fetching SYNOP...", end=" ", flush=True)
        synop = fetch_synop_day(d)
        print(f"high={synop['high']:.1f}°C" if synop else "failed")
        time.sleep(0.5)

        print("  Fetching Open-Meteo...", end=" ", flush=True)
        om = fetch_openmeteo_day(d)
        print(f"high={om['high']:.1f}°C" if om else "failed")

        # Determine resolution
        winning = [m for m in markets if m["resolved_to"] == "YES"]
        winning_range = winning[0]["range_label"] if winning else None

        # Show summary
        print(f"\n  Resolution: {winning_range or 'OPEN'}")
        print(f"  {'Bracket':<10} {'Final YES':>10} {'Volume':>10} {'Resolved':>10}")
        print(f"  {'-'*45}")
        for m in markets:
            yes_str = f"{m['yes_price']:.0%}" if m["yes_price"] is not None else "?"
            res_str = m["resolved_to"] or "-"
            vol_str = f"${m['volume']:,.0f}"
            marker = " <--" if m["resolved_to"] == "YES" else ""
            print(f"  {m['range_label']:<10} {yes_str:>10} {vol_str:>10} {res_str:>10}{marker}")

        day_data = {
            "date": d.isoformat(),
            "slug": slug,
            "closed": bool(ev.get("closed")),
            "winning_bracket": winning_range,
            "wu": {"high": wu["high"], "low": wu["low"],
                   "timeseries": wu["timeseries"]} if wu else None,
            "synop": {"high": synop["high"], "low": synop["low"],
                      "timeseries": synop["timeseries"]} if synop else None,
            "openmeteo": {"high": om["high"], "low": om["low"],
                          "timeseries": om["timeseries"]} if om else None,
            "markets": [],
            "price_histories": {},
        }
        for m in markets:
            day_data["markets"].append({
                "range_label": m["range_label"],
                "range": m["range"],
                "yes_price": m["yes_price"],
                "volume": m["volume"],
                "resolved_to": m["resolved_to"],
            })
        for label, ph in price_histories.items():
            day_data["price_histories"][label] = ph

        all_days.append(day_data)

    # ── Save data ────────────────────────────────────────────────────────
    out_path = r"C:\Users\Charl\Desktop\Cursor\weather-bot\backtest_data.json"
    output = {
        "generated": datetime.now(timezone.utc).isoformat(),
        "days": all_days,
    }
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2)
    print(f"\n\nData saved to {out_path}")

    # ── Quick console analysis ───────────────────────────────────────────
    print("\n" + "=" * 70)
    print("  QUICK ANALYSIS")
    print("=" * 70)

    resolved = [d for d in all_days if d["closed"] and d["winning_bracket"]]

    print(f"\nResolved days: {len(resolved)}")
    for d in resolved:
        wu_h = d["wu"]["high"] if d["wu"] else "?"
        syn_h = f"{d['synop']['high']:.1f}" if d["synop"] else "?"
        om_h = f"{d['openmeteo']['high']:.1f}" if d["openmeteo"] else "?"
        print(f"  {d['date']}: WU_high={wu_h}°C  SYNOP_high={syn_h}°C  OM_high={om_h}°C  -> {d['winning_bracket']}")

    # WU vs SYNOP consistency
    print("\nWU vs SYNOP high comparison:")
    for d in resolved:
        if d["wu"] and d["synop"]:
            wu_h = d["wu"]["high"]
            syn_h = d["synop"]["high"]
            diff = syn_h - wu_h
            rounded_match = "MATCH" if round(syn_h) == wu_h else f"MISMATCH (round={round(syn_h)})"
            print(f"  {d['date']}: WU={wu_h}°C  SYNOP={syn_h:.1f}°C  diff={diff:+.1f}  {rounded_match}")

    # Price evolution analysis: how early did the winning bracket reach 90%?
    print("\nWinning bracket price evolution:")
    for d in resolved:
        wb = d["winning_bracket"]
        ph = d["price_histories"].get(wb, [])
        if not ph:
            print(f"  {d['date']} ({wb}): no price history")
            continue
        # Find earliest time above 90%, 80%, 50%
        for threshold in [0.5, 0.8, 0.9]:
            above = [(ts, p) for ts, p in ph if p >= threshold]
            if above:
                t = datetime.fromtimestamp(above[0][0], tz=CET)
                print(f"  {d['date']} ({wb}): first >{threshold:.0%} at {t.strftime('%H:%M CET')}")
            else:
                print(f"  {d['date']} ({wb}): never reached {threshold:.0%}")

    # Losing brackets that traded at high YES prices (= bad bets)
    print("\nLosing brackets that had YES > 20% at some point:")
    for d in resolved:
        wb = d["winning_bracket"]
        for label, ph in d["price_histories"].items():
            if label == wb:
                continue
            if not ph:
                continue
            max_yes = max(p for _, p in ph)
            if max_yes > 0.20:
                peak_ts = [ts for ts, p in ph if p == max_yes][0]
                peak_t = datetime.fromtimestamp(peak_ts, tz=CET)
                final_mkt = [m for m in d["markets"] if m["range_label"] == label]
                resolved_to = final_mkt[0]["resolved_to"] if final_mkt else "?"
                print(f"  {d['date']} {label}: peaked at {max_yes:.0%} ({peak_t.strftime('%H:%M CET')}), resolved {resolved_to}")

    print(f"\nDone. Full data in {out_path}")
