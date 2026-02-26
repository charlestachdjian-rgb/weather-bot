"""
Test the enhanced 5-layer strategy on all 8 historical Paris days.

Replays real METAR, SYNOP, and Open-Meteo data through the weather_monitor.py
signal detection logic and validates:
  - All signals are correct (bracket actually resolved as predicted)
  - Guards block losing signals
  - P&L calculation
"""
import urllib.request, json, re, sys, time
from datetime import datetime, date, timezone, timedelta
from zoneinfo import ZoneInfo

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# Import the actual strategy logic from weather_monitor.py
sys.path.insert(0, r"C:\Users\Charl\Desktop\Cursor\weather-bot")
import weather_monitor as wm

CET = ZoneInfo("Europe/Paris")
STAKE = 100

with open(r"C:\Users\Charl\Desktop\Cursor\weather-bot\backtest_data.json", encoding="utf-8") as f:
    bdata = json.load(f)
paris_days = sorted([d for d in bdata["days"] if "paris" in d["slug"]], key=lambda d: d["date"])
print(f"Testing enhanced strategy on {len(paris_days)} Paris days\n")


# ── Data fetchers (same as backtest) ──────────────────────────────────────

def fetch_wu(dt):
    ds = dt.strftime("%Y%m%d")
    url = (f"https://api.weather.com/v1/location/LFPG:9:FR/observations/historical.json"
           f"?apiKey=e1f10a1e78da46f5b10a1e78da96f525&units=m&startDate={ds}&endDate={ds}")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        data = json.loads(r.read())
    pts = []
    for o in data.get("observations", []):
        ts = o.get("valid_time_gmt", 0)
        temp = o.get("temp")
        if temp is not None:
            d = datetime.fromtimestamp(ts, tz=CET)
            pts.append({"time": d.strftime("%H:%M"), "hour": d.hour + d.minute/60, "ts": ts, "temp": temp})
    return sorted(pts, key=lambda x: x["ts"])


def fetch_synop(dt):
    begin = dt.strftime("%Y%m%d") + "0000"
    end = (dt + timedelta(days=1)).strftime("%Y%m%d") + "0000"
    url = f"https://www.ogimet.com/cgi-bin/getsynop?block=07157&begin={begin}&end={end}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as r:
            text = r.read().decode("utf-8", errors="replace")
        pts = []
        for line in text.splitlines():
            if not line.strip() or line.startswith("#") or not line.startswith("07157"): continue
            parts = line.split(",")
            if len(parts) < 6: continue
            h = int(parts[4])
            m = re.search(r'\b1([01])(\d{3})\b', line)
            if m:
                sign = 1 if m.group(1) == "0" else -1
                temp = sign * int(m.group(2)) / 10.0
                dt_utc = datetime(int(parts[1]), int(parts[2]), int(parts[3]), h, 0, tzinfo=timezone.utc)
                dt_cet = dt_utc.astimezone(CET)
                pts.append({"hour": dt_cet.hour + dt_cet.minute/60, "temp": temp})
        return sorted(pts, key=lambda x: x["hour"])
    except:
        return []


def fetch_om_hourly(dt):
    ds = dt.isoformat()
    url = (f"https://archive-api.open-meteo.com/v1/archive?"
           f"latitude=49.0097&longitude=2.5479"
           f"&hourly=temperature_2m&timezone=Europe/Paris"
           f"&start_date={ds}&end_date={ds}")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read())
        times = data.get("hourly", {}).get("time", [])
        temps = data.get("hourly", {}).get("temperature_2m", [])
        pts = []
        for t, tmp in zip(times, temps):
            if tmp is not None:
                dl = datetime.fromisoformat(t)
                pts.append({"hour": dl.hour + dl.minute/60, "temp": tmp})
        return pts
    except:
        return []


def fetch_markets(slug):
    url = f"https://gamma-api.polymarket.com/events?slug={slug}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=10) as r:
        data = json.loads(r.read())
    if not data: return []
    markets = []
    for m in data[0].get("markets", []):
        q = m.get("question", "").replace("\u00b0", "")
        lo, hi = None, None
        match = re.search(r"be\s+(-?\d+)\s*C\s+or\s+below", q)
        if match: lo, hi = None, float(match.group(1))
        else:
            match = re.search(r"be\s+(-?\d+)\s*C\s+or\s+higher", q)
            if match: lo, hi = float(match.group(1)), None
            else:
                match = re.search(r"be\s+(-?\d+)\s*C\s+on", q)
                if match: v = float(match.group(1)); lo, hi = v, v

        tids = m.get("clobTokenIds", "[]")
        try: tids = json.loads(tids) if isinstance(tids, str) else tids
        except: tids = []
        prices = m.get("outcomePrices", "[]")
        try: prices = json.loads(prices) if isinstance(prices, str) else prices
        except: prices = []

        markets.append({
            "question": m.get("question", ""),
            "temp_range": (lo, hi),
            "yes_price": float(prices[0]) if prices else None,
            "no_price": float(prices[1]) if len(prices) > 1 else None,
            "volume": float(m.get("volume", 0)),
            "slug": m.get("slug", ""),
            "token_id": tids[0] if tids else "",
            "no_token_id": tids[1] if len(tids) > 1 else "",
            "closed": bool(m.get("closed")),
        })
    return markets


def fetch_ph(tid, dt):
    start = int(datetime(dt.year, dt.month, dt.day, tzinfo=timezone.utc).timestamp())
    url = f"https://clob.polymarket.com/prices-history?market={tid}&startTs={start}&endTs={start+86400}&interval=1h&fidelity=60"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read())
        return [(int(h["t"]), float(h["p"])) for h in data.get("history", []) if h.get("t") and h.get("p")]
    except:
        return []


def yes_at(ph, hour):
    best = None
    for ts, p in ph:
        dt = datetime.fromtimestamp(ts, tz=CET)
        if dt.hour + dt.minute/60 <= hour + 0.5: best = p
    return best


def bracket_resolved_no(lo, hi, wu_high):
    if lo is not None and hi is not None: return not (lo <= wu_high <= hi)
    elif lo is None and hi is not None: return wu_high > hi
    elif lo is not None and hi is None: return wu_high < lo
    return False


def compute_pnl(side, yes_price, correct):
    if yes_price is None or yes_price <= 0: yes_price = 0.001
    if yes_price >= 1.0: yes_price = 0.999
    if side == "NO":
        no_price = 1.0 - yes_price
        if no_price <= 0: no_price = 0.001
        return round(STAKE * yes_price / no_price, 2) if correct else -STAKE
    else:
        return round(STAKE * (1.0 - yes_price) / yes_price, 2) if correct else -STAKE


# ── Simulate one day ─────────────────────────────────────────────────────

def simulate_day(day_info, wu_obs, synop, om_hourly, markets, price_histories):
    wu_high = day_info["wu_high"]
    static_forecast = round(day_info["openmeteo_high"] + wm.OPENMETEO_BIAS_CORRECTION, 1)

    # Reset weather_monitor globals for this day
    wm._killed_brackets = set()
    wm._fired_signals = set()
    wm._midday_reassessment_done = False

    events = []
    _signaled = set()

    running_high = None
    metar_history = []
    dynamic_bias = None
    dynamic_forecast = None
    bias_computed = False
    midday_done = False

    # Build market structures compatible with weather_monitor.detect_signals
    wm_markets = markets

    for obs in wu_obs:
        hour = obs["hour"]
        temp = obs["temp"]

        # Update running high
        if running_high is None or temp > running_high:
            running_high = temp

        # Accumulate METAR history
        metar_history.append({"hour": hour, "temp": temp})

        # Compute dynamic bias at 9am
        if not bias_computed and hour >= 9 and om_hourly:
            dynamic_bias = round(wm.compute_dynamic_bias(metar_history, om_hourly, 9), 2)
            dynamic_forecast = round(static_forecast + max(0, dynamic_bias), 1)
            bias_computed = True

        # Update market YES prices based on price history
        for m in wm_markets:
            label = wm.range_label(*m["temp_range"])
            ph = price_histories.get(label, [])
            p = yes_at(ph, hour)
            if p is not None:
                m["yes_price"] = p
                m["no_price"] = 1.0 - p

        # Build a datetime for this observation
        dt_local = datetime(2026, int(day_info["date"].split("-")[1]),
                           int(day_info["date"].split("-")[2]),
                           int(hour), int((hour % 1) * 60), tzinfo=CET)

        # Mark midday done after the window
        if hour >= wm.MIDDAY_HOUR + 1 and not midday_done:
            wm._midday_reassessment_done = True
            midday_done = True

        # Call the actual detect_signals from weather_monitor.py
        signals = wm.detect_signals(
            wm_markets, running_high, dt_local,
            forecast_high=static_forecast,
            om_trend=None,
            om_hourly=om_hourly,
            metar_history=metar_history,
            synop_history=synop,
            dynamic_bias=dynamic_bias,
            dynamic_forecast=dynamic_forecast,
        )

        today_str = day_info["date"]
        for sig in signals:
            if sig["type"] in ("SUM_OVERPRICED", "SUM_UNDERPRICED"):
                continue
            sig_key = f"{sig['type']}::{sig['range']}::{today_str}"
            if sig_key in _signaled:
                continue
            _signaled.add(sig_key)

            lo, hi = None, None
            for m in wm_markets:
                if wm.range_label(*m["temp_range"]) == sig["range"]:
                    lo, hi = m["temp_range"]
                    break

            if sig["our_side"] == "NO":
                correct = bracket_resolved_no(lo, hi, wu_high)
            else:
                correct = not bracket_resolved_no(lo, hi, wu_high)

            pnl = compute_pnl(sig["our_side"], sig["yes_price"], correct)
            events.append({
                "time": obs["time"],
                "hour": hour,
                "type": sig["type"],
                "bracket": sig["range"],
                "side": sig["our_side"],
                "yes": sig["yes_price"],
                "correct": correct,
                "pnl": pnl,
                "note": sig.get("note", ""),
            })

    events.sort(key=lambda e: e["hour"])
    return events


# ── Run ──────────────────────────────────────────────────────────────────

total_trades = 0
total_correct = 0
total_pnl = 0.0
total_losses = 0
all_events = []

for i, day in enumerate(paris_days):
    dt = date.fromisoformat(day["date"])
    print(f"[{i+1}/{len(paris_days)}] {day['date']} (WU high: {day['wu_high']}°C, OM: {day['openmeteo_high']}°C)", end=" ", flush=True)

    try: wu = fetch_wu(dt)
    except Exception as e: print(f"WU FAIL: {e}"); continue
    if not wu: print("no WU"); continue

    synop = fetch_synop(dt)
    om = fetch_om_hourly(dt)

    try: mkts = fetch_markets(day["slug"])
    except Exception as e: print(f"MKT FAIL: {e}"); continue

    phs = {}
    for m in mkts:
        label = wm.range_label(*m["temp_range"])
        if m["token_id"]:
            phs[label] = fetch_ph(m["token_id"], dt)
            time.sleep(0.08)
        # Force markets open for simulation (they're historically closed)
        m["closed"] = False

    events = simulate_day(day, wu, synop, om, mkts, phs)
    day_pnl = sum(e["pnl"] for e in events)
    day_correct = sum(1 for e in events if e["correct"])
    day_wrong = sum(1 for e in events if not e["correct"])

    total_trades += len(events)
    total_correct += day_correct
    total_pnl += day_pnl
    total_losses += day_wrong

    status = "✓" if day_wrong == 0 else "✗ LOSS"
    types = {}
    for e in events:
        types[e["type"]] = types.get(e["type"], 0) + 1
    type_str = " ".join(f"{t}:{n}" for t, n in sorted(types.items()))

    print(f"→ {len(events)} trades, {day_correct}/{len(events)} correct, ${day_pnl:+.2f} {status}")
    if type_str:
        print(f"         {type_str}")

    for e in events:
        ok = "✓" if e["correct"] else "✗"
        print(f"    {e['time']} {ok} {e['type']:20s} {e['side']:3s} {e['bracket']:8s} YES={e['yes']:.1%} → ${e['pnl']:+.2f}")

    if day_wrong > 0:
        print(f"  ⚠️  INCORRECT TRADES:")
        for e in events:
            if not e["correct"]:
                print(f"      {e['time']} {e['type']} {e['side']} {e['bracket']} — {e['note']}")

    all_events.append({"date": day["date"], "events": events, "pnl": day_pnl})
    print()
    time.sleep(0.2)


# ── Summary ──────────────────────────────────────────────────────────────

print("=" * 70)
print(f"ENHANCED STRATEGY TEST RESULTS")
print("=" * 70)
print(f"  Days tested:     {len(all_events)}")
print(f"  Total trades:    {total_trades}")
print(f"  Correct:         {total_correct}/{total_trades} ({total_correct/total_trades*100:.0f}%)" if total_trades else "  Correct: 0/0")
print(f"  Losses:          {total_losses}")
print(f"  Total P&L:       ${total_pnl:+.2f}")
print(f"  Avg P&L/day:     ${total_pnl/len(all_events):+.2f}" if all_events else "")

if total_losses == 0:
    print(f"\n  ✅ ALL {total_trades} TRADES CORRECT — STRATEGY VALIDATED")
else:
    print(f"\n  ❌ {total_losses} LOSING TRADES — NEEDS INVESTIGATION")

# Type breakdown
types_all = {}
for r in all_events:
    for e in r["events"]:
        t = e["type"]
        types_all.setdefault(t, {"n": 0, "correct": 0, "pnl": 0})
        types_all[t]["n"] += 1
        if e["correct"]: types_all[t]["correct"] += 1
        types_all[t]["pnl"] += e["pnl"]

print(f"\n  {'Signal Type':<22} {'Trades':>6} {'Win%':>6} {'P&L':>10}")
print(f"  {'-'*50}")
for t, v in sorted(types_all.items()):
    wr = v["correct"] / v["n"] * 100 if v["n"] else 0
    print(f"  {t:<22} {v['n']:>6} {wr:>5.0f}% ${v['pnl']:>+9.2f}")
print()
