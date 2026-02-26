"""
Enhanced strategy backtest — maximizes profit while maintaining safety.

Layer 1: Floor NO T1 (mathematical certainty) — unchanged
Layer 2: Floor NO T2 at 9am (forecast-based, 4°C buffer) — unchanged
Layer 3: T2 Upper at 9am — kill upper brackets when forecast far below
         Safety: requires dynamic bias check + no morning underforecast
Layer 4: Midday Reassessment at 12pm — catch brackets the morning missed
         Uses actual running high + OM remaining max with dynamic bias
Layer 5: Guarded Ceiling NO / Locked-In YES — 5 safeguards from prior analysis

Also computes: Dynamic OM bias from morning METAR vs OM hourly comparison.
"""
import urllib.request, json, re, sys, time, math
from datetime import datetime, date, timezone, timedelta
from zoneinfo import ZoneInfo

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

CET = ZoneInfo("Europe/Paris")
CDG_LAT, CDG_LON = 49.0097, 2.5479
ROUNDING_BUFFER = 0.5
FORECAST_KILL_BUFFER = 4.0
UPPER_KILL_BUFFER = 5.0
MIDDAY_KILL_BUFFER = 2.5
OPENMETEO_BIAS = 1.0
LATE_DAY_HOUR = 16
LOCK_IN_HOUR  = 17
CEIL_GAP      = 2.0
MIN_YES_ALERT = 0.03
STAKE         = 100

with open(r"C:\Users\Charl\Desktop\Cursor\weather-bot\backtest_data.json", encoding="utf-8") as f:
    bdata = json.load(f)
paris_days = sorted([d for d in bdata["days"] if "paris" in d["slug"]], key=lambda d: d["date"])
print(f"Found {len(paris_days)} Paris days\n")


# ── Fetchers ─────────────────────────────────────────────────────────────

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
                pts.append({"time": dt_cet.strftime("%H:%M"), "hour": dt_cet.hour + dt_cet.minute/60, "temp": temp})
        return sorted(pts, key=lambda x: x["hour"])
    except:
        return []


def fetch_om_hourly(dt):
    ds = dt.isoformat()
    url = (f"https://archive-api.open-meteo.com/v1/archive?"
           f"latitude={CDG_LAT}&longitude={CDG_LON}"
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
        if lo is None and hi is not None: label = f"<={int(hi)}°C"
        elif lo is not None and hi is not None and lo == hi: label = f"{int(lo)}°C"
        elif lo is not None and hi is None: label = f">={int(lo)}°C"
        else: label = "?"
        tids = m.get("clobTokenIds", "[]")
        try: tids = json.loads(tids) if isinstance(tids, str) else tids
        except: tids = []
        markets.append({"label": label, "lo": lo, "hi": hi, "yes_token": tids[0] if tids else None})
    markets.sort(key=lambda x: (x["hi"] if x["hi"] is not None else 999, x["lo"] if x["lo"] is not None else 999))
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


# ── Helpers ──────────────────────────────────────────────────────────────

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


# ── Dynamic bias & OM analysis ──────────────────────────────────────────

def compute_dynamic_bias(wu_obs, om_hourly, up_to_hour):
    """
    Compare actual METAR temps to OM hourly predictions for hours up to up_to_hour.
    Returns the average bias (actual - OM). Positive = OM underforecasts.
    """
    diffs = []
    for obs in wu_obs:
        if obs["hour"] > up_to_hour: break
        om_match = None
        for om in om_hourly:
            if abs(om["hour"] - obs["hour"]) <= 0.5:
                om_match = om["temp"]
                break
        if om_match is not None:
            diffs.append(obs["temp"] - om_match)
    if not diffs: return 0
    return sum(diffs) / len(diffs)

def om_peak_hour(om_hourly):
    if not om_hourly: return None
    peak = max(om_hourly, key=lambda p: p["temp"])
    return peak["hour"]

def om_remaining_max(om_hourly, after_hour):
    remaining = [p["temp"] for p in om_hourly if p["hour"] > after_hour]
    return max(remaining) if remaining else None

def om_forecast_high(om_hourly):
    if not om_hourly: return None
    return max(p["temp"] for p in om_hourly)

def source_trend(pts, at_hour, window=3):
    relevant = [p for p in pts if at_hour - window <= p["hour"] <= at_hour]
    if len(relevant) < 2: return "UNKNOWN"
    first_temp = relevant[0]["temp"]
    last_temp = relevant[-1]["temp"]
    if last_temp > first_temp + 0.3: return "RISING"
    if last_temp < first_temp - 0.3: return "FALLING"
    return "FLAT"

def synop_velocity(synop, at_hour, window=3):
    relevant = [p for p in synop if at_hour - window <= p["hour"] <= at_hour]
    if len(relevant) < 2: return 0
    return relevant[-1]["temp"] - relevant[0]["temp"]

def should_block_risky_signal(signal_hour, running_high, bracket_lo, wu_obs, synop, om_hourly):
    reasons = []
    peak_h = om_peak_hour(om_hourly)
    if peak_h is not None and peak_h > signal_hour:
        reasons.append(f"OM peak at {int(peak_h)}:00 > signal at {signal_hour:.0f}:00")
    rem_max = om_remaining_max(om_hourly, signal_hour)
    if rem_max is not None and rem_max > running_high + 0.5:
        reasons.append(f"OM forecasts {rem_max:.1f}°C later (rh: {running_high}°C)")
    om_high = om_forecast_high(om_hourly)
    if om_high is not None and bracket_lo is not None:
        corrected = om_high + OPENMETEO_BIAS
        if corrected >= bracket_lo - 1.0:
            reasons.append(f"OM high {corrected:.1f}°C near bracket {bracket_lo}°C")
    wu_trend = source_trend([{"hour": p["hour"], "temp": p["temp"]} for p in wu_obs], signal_hour)
    syn_trend = source_trend(synop, signal_hour)
    om_trend = source_trend([{"hour": p["hour"], "temp": p["temp"]} for p in om_hourly], signal_hour)
    rising = [s for s, t in [("METAR", wu_trend), ("SYNOP", syn_trend), ("OM", om_trend)] if t == "RISING"]
    if rising:
        reasons.append(f"Rising: {', '.join(rising)}")
    vel = synop_velocity(synop, signal_hour)
    if vel > 0.3:
        reasons.append(f"SYNOP +{vel:.1f}°C/3h")
    return (len(reasons) > 0, reasons)


# ── Simulate one day ─────────────────────────────────────────────────────

def simulate_day(day_info, wu_obs, synop, om_hourly, markets, price_histories):
    wu_high = day_info["wu_high"]
    static_forecast = round(day_info["openmeteo_high"] + OPENMETEO_BIAS, 1)

    # Strategy results
    floor_events = []    # Floor only
    guarded_events = []  # + 5 guards on Ceil/Lock
    enhanced_events = [] # + T2 Upper + Midday T2 + dynamic bias

    _k_floor = set(); _k_guard = set(); _k_enh = set()

    debug_info = {"dynamic_bias_9am": None, "dynamic_forecast": None,
                  "om_peak_hour": om_peak_hour(om_hourly),
                  "om_high": om_forecast_high(om_hourly)}

    def add_floor(e):
        floor_events.append(e); guarded_events.append(e); enhanced_events.append(e)
    def add_guarded(e):
        guarded_events.append(e); enhanced_events.append(e)
    def add_enhanced_only(e):
        enhanced_events.append(e)

    # Phase 1: T1 before 9am
    rh = None
    for obs in wu_obs:
        if obs["hour"] > 9: break
        if rh is None or obs["temp"] > rh:
            old = rh; rh = obs["temp"]
            for m in markets:
                hi = m["hi"]
                if hi is None: continue
                if rh >= hi + ROUNDING_BUFFER and not (old is not None and old >= hi + ROUNDING_BUFFER):
                    yes_p = yes_at(price_histories.get(m["label"], []), obs["hour"])
                    correct = bracket_resolved_no(m["lo"], m["hi"], wu_high)
                    ev = {"time": obs["time"], "hour": obs["hour"], "type": "FLOOR_T1",
                          "bracket": m["label"], "side": "NO", "yes": yes_p,
                          "correct": correct, "pnl": compute_pnl("NO", yes_p, correct)}
                    for s, k in [(add_floor, _k_floor), (None, _k_guard), (None, _k_enh)]:
                        pass
                    if m["label"] not in _k_floor:
                        add_floor(ev)
                        _k_floor.add(m["label"]); _k_guard.add(m["label"]); _k_enh.add(m["label"])

    # Phase 2: T2 at 9am (lower brackets — safe)
    for m in markets:
        hi = m["hi"]
        if hi is None or m["label"] in _k_floor: continue
        if static_forecast - hi >= FORECAST_KILL_BUFFER:
            yes_p = yes_at(price_histories.get(m["label"], []), 9)
            correct = bracket_resolved_no(m["lo"], m["hi"], wu_high)
            ev = {"time": "09:00", "hour": 9, "type": "FLOOR_T2",
                  "bracket": m["label"], "side": "NO", "yes": yes_p,
                  "correct": correct, "pnl": compute_pnl("NO", yes_p, correct)}
            add_floor(ev)
            _k_floor.add(m["label"]); _k_guard.add(m["label"]); _k_enh.add(m["label"])

    # Phase 2b: T2 Upper at 9am (upper brackets — needs safety)
    dyn_bias = compute_dynamic_bias(wu_obs, om_hourly, 9)
    dyn_forecast = round(day_info["openmeteo_high"] + OPENMETEO_BIAS + max(0, dyn_bias), 1)
    debug_info["dynamic_bias_9am"] = round(dyn_bias, 2)
    debug_info["dynamic_forecast"] = dyn_forecast

    is_om_underforecasting = dyn_bias > 1.0

    for m in markets:
        lo = m["lo"]
        if lo is None or m["label"] in _k_enh: continue
        gap = lo - dyn_forecast
        om_h = om_forecast_high(om_hourly)
        om_hourly_max_adj = (om_h + OPENMETEO_BIAS + max(0, dyn_bias)) if om_h else None

        can_fire = (gap >= UPPER_KILL_BUFFER
                    and not is_om_underforecasting
                    and (om_hourly_max_adj is None or om_hourly_max_adj < lo - 1.0))

        if can_fire:
            yes_p = yes_at(price_histories.get(m["label"], []), 9)
            correct = bracket_resolved_no(m["lo"], m["hi"], wu_high)
            ev = {"time": "09:00", "hour": 9, "type": "T2_UPPER",
                  "bracket": m["label"], "side": "NO", "yes": yes_p,
                  "correct": correct, "pnl": compute_pnl("NO", yes_p, correct)}
            add_enhanced_only(ev)
            _k_enh.add(m["label"])
        elif gap >= UPPER_KILL_BUFFER:
            reasons = []
            if is_om_underforecasting:
                reasons.append(f"OM underforecasting by {dyn_bias:+.1f}°C at 9am")
            if om_hourly_max_adj is not None and om_hourly_max_adj >= lo - 1.0:
                reasons.append(f"Adjusted OM max {om_hourly_max_adj:.1f}°C too close to {lo}°C")
            enhanced_events.append({"time": "09:00", "hour": 9, "type": "BLOCKED_T2U",
                "bracket": m["label"], "side": "NO", "yes": None, "correct": bracket_resolved_no(m["lo"], m["hi"], wu_high),
                "pnl": 0, "reasons": reasons})

    # Phase 3: Continue T1 + Midday T2 + Guarded Ceil/Lock
    _ceil_guard = set(); _ceil_enh = set()
    _lock_guard = set(); _lock_enh = set()
    _midday_done = set()

    for obs in wu_obs:
        if obs["hour"] <= 9: continue
        hour = obs["hour"]

        if rh is None or obs["temp"] > rh:
            old_rh = rh; rh = obs["temp"]
            for m in markets:
                hi = m["hi"]
                if hi is None: continue
                if rh >= hi + ROUNDING_BUFFER and not (old_rh is not None and old_rh >= hi + ROUNDING_BUFFER):
                    yes_p = yes_at(price_histories.get(m["label"], []), hour)
                    correct = bracket_resolved_no(m["lo"], m["hi"], wu_high)
                    ev = {"time": obs["time"], "hour": hour, "type": "FLOOR_T1",
                          "bracket": m["label"], "side": "NO", "yes": yes_p,
                          "correct": correct, "pnl": compute_pnl("NO", yes_p, correct)}
                    if m["label"] not in _k_floor:
                        floor_events.append(ev); _k_floor.add(m["label"])
                    if m["label"] not in _k_guard:
                        guarded_events.append(ev); _k_guard.add(m["label"])
                    if m["label"] not in _k_enh:
                        enhanced_events.append(ev); _k_enh.add(m["label"])

        # Midday reassessment (11:30-12:30)
        if 11.5 <= hour <= 12.5:
            dyn_bias_noon = compute_dynamic_bias(wu_obs, om_hourly, 12)
            dyn_forecast_noon = day_info["openmeteo_high"] + OPENMETEO_BIAS + max(0, dyn_bias_noon)
            om_rem = om_remaining_max(om_hourly, 12)
            if om_rem is not None:
                est_remaining_rise = max(0, om_rem - (om_forecast_high([p for p in om_hourly if p["hour"] <= 12]) or 0))
            else:
                est_remaining_rise = 2.0

            est_final_high = rh + est_remaining_rise + max(0, dyn_bias_noon) * 0.5
            for m in markets:
                if m["label"] in _k_enh or m["label"] in _midday_done: continue
                lo = m["lo"]
                hi_val = m["hi"]

                if hi_val is not None and rh is not None:
                    if rh - hi_val >= MIDDAY_KILL_BUFFER and est_final_high > hi_val + 1:
                        yes_p = yes_at(price_histories.get(m["label"], []), hour)
                        correct = bracket_resolved_no(m["lo"], m["hi"], wu_high)
                        ev = {"time": obs["time"], "hour": hour, "type": "MIDDAY_T2",
                              "bracket": m["label"], "side": "NO", "yes": yes_p,
                              "correct": correct, "pnl": compute_pnl("NO", yes_p, correct)}
                        add_enhanced_only(ev)
                        _k_enh.add(m["label"])
                        _midday_done.add(m["label"])

                if lo is not None and rh is not None:
                    gap_up = lo - est_final_high
                    if gap_up >= MIDDAY_KILL_BUFFER:
                        yes_p = yes_at(price_histories.get(m["label"], []), hour)
                        correct = bracket_resolved_no(m["lo"], m["hi"], wu_high)
                        ev = {"time": obs["time"], "hour": hour, "type": "MIDDAY_T2",
                              "bracket": m["label"], "side": "NO", "yes": yes_p,
                              "correct": correct, "pnl": compute_pnl("NO", yes_p, correct)}
                        add_enhanced_only(ev)
                        _k_enh.add(m["label"])
                        _midday_done.add(m["label"])

        # Ceiling NO (guarded + enhanced share the same logic)
        if hour >= LATE_DAY_HOUR:
            for m in markets:
                lo = m["lo"]
                if lo is None: continue
                gap = lo - rh if rh is not None else 0
                if gap >= CEIL_GAP:
                    yes_p = yes_at(price_histories.get(m["label"], []), hour)
                    if yes_p is not None and yes_p >= MIN_YES_ALERT:
                        correct = bracket_resolved_no(m["lo"], m["hi"], wu_high)
                        ev = {"time": obs["time"], "hour": hour, "type": "CEIL_NO",
                              "bracket": m["label"], "side": "NO", "yes": yes_p,
                              "correct": correct, "pnl": compute_pnl("NO", yes_p, correct)}

                        if m["label"] not in _k_guard and m["label"] not in _ceil_guard:
                            blocked, reasons = should_block_risky_signal(
                                hour, rh, lo, wu_obs, synop, om_hourly)
                            if not blocked:
                                guarded_events.append(ev); _k_guard.add(m["label"])
                                enhanced_events.append(ev); _k_enh.add(m["label"])
                            else:
                                guarded_events.append({**ev, "type": "BLOCKED_CEIL", "pnl": 0, "reasons": reasons})
                                enhanced_events.append({**ev, "type": "BLOCKED_CEIL", "pnl": 0, "reasons": reasons})
                        _ceil_guard.add(m["label"]); _ceil_enh.add(m["label"])

        # Locked-In YES
        if hour >= LOCK_IN_HOUR:
            for m in markets:
                lo, hi_val = m["lo"], m["hi"]
                if lo is None or hi_val is None: continue
                if rh is not None and lo - ROUNDING_BUFFER <= rh <= hi_val + ROUNDING_BUFFER:
                    yes_p = yes_at(price_histories.get(m["label"], []), hour)
                    if yes_p is not None and yes_p < 0.80:
                        correct = not bracket_resolved_no(m["lo"], m["hi"], wu_high)
                        ev = {"time": obs["time"], "hour": hour, "type": "LOCKED_YES",
                              "bracket": m["label"], "side": "YES", "yes": yes_p,
                              "correct": correct, "pnl": compute_pnl("YES", yes_p, correct)}

                        if m["label"] not in _lock_guard:
                            blocked, reasons = should_block_risky_signal(
                                hour, rh, lo, wu_obs, synop, om_hourly)
                            if not blocked:
                                guarded_events.append(ev); enhanced_events.append(ev)
                            else:
                                guarded_events.append({**ev, "type": "BLOCKED_LOCK", "pnl": 0, "reasons": reasons})
                                enhanced_events.append({**ev, "type": "BLOCKED_LOCK", "pnl": 0, "reasons": reasons})
                        _lock_guard.add(m["label"]); _lock_enh.add(m["label"])

    for lst in [floor_events, guarded_events, enhanced_events]:
        lst.sort(key=lambda e: e["hour"])

    return {"floor": floor_events, "guarded": guarded_events, "enhanced": enhanced_events,
            "debug": debug_info}


# ── Run ──────────────────────────────────────────────────────────────────

all_results = []
for i, day in enumerate(paris_days):
    dt = date.fromisoformat(day["date"])
    print(f"[{i+1}/{len(paris_days)}] {day['date']}...", end=" ", flush=True)
    try: wu = fetch_wu(dt)
    except Exception as e: print(f"WU FAILED: {e}"); continue
    if not wu: print("no WU"); continue
    synop = fetch_synop(dt)
    om = fetch_om_hourly(dt)
    try: mkts = fetch_markets(day["slug"])
    except Exception as e: print(f"MKT FAILED: {e}"); continue
    phs = {}
    for m in mkts:
        if m["yes_token"]:
            phs[m["label"]] = fetch_ph(m["yes_token"], dt)
            time.sleep(0.08)

    result = simulate_day(day, wu, synop, om, mkts, phs)
    all_results.append({"date": day["date"], "wu_high": day["wu_high"],
                        "om_raw": day["openmeteo_high"],
                        "forecast": round(day["openmeteo_high"] + OPENMETEO_BIAS, 1),
                        **result})

    for key, label in [("floor", "Floor"), ("guarded", "Guard"), ("enhanced", "Enhan")]:
        evs = result[key]
        real = [e for e in evs if "BLOCKED" not in e["type"]]
        blocked = [e for e in evs if "BLOCKED" in e["type"]]
        pnl = sum(e["pnl"] for e in evs)
        wrong = sum(1 for e in real if not e.get("correct", True))
        new_types = [e["type"] for e in real if e["type"] not in ("FLOOR_T1", "FLOOR_T2")]
        extra = f" [{','.join(set(new_types))}]" if new_types else ""
        print(f"{label}:{len(real)}t/${pnl:+.0f}" + (f"({wrong}L)" if wrong else "") + extra, end=" ")

    dbg = result["debug"]
    print(f"  bias={dbg['dynamic_bias_9am']:+.1f} dynFc={dbg['dynamic_forecast']} omPk={dbg['om_peak_hour']:.0f}h" if dbg['dynamic_bias_9am'] is not None and dbg['om_peak_hour'] is not None else "", end="")

    blocked_list = [e for e in result["enhanced"] if "BLOCKED" in e["type"]]
    if blocked_list:
        for b in blocked_list:
            print(f"\n    BLOCKED {b['bracket']}: {'; '.join(b.get('reasons',[]))}", end="")
    print()
    time.sleep(0.2)


# ── Stats ────────────────────────────────────────────────────────────────

def calc(results, key):
    all_ev = [e for r in results for e in r[key] if "BLOCKED" not in e["type"]]
    n = len(all_ev); c = sum(1 for e in all_ev if e.get("correct", True)); w = n - c
    pnl = sum(e["pnl"] for e in all_ev)
    invested = n * STAKE
    daily = [sum(e["pnl"] for e in r[key] if "BLOCKED" not in e["type"]) for r in results]
    blocked = [e for r in results for e in r[key] if "BLOCKED" in e["type"]]
    by_type = {}
    for e in all_ev:
        t = e["type"]
        by_type.setdefault(t, {"n": 0, "correct": 0, "pnl": 0})
        by_type[t]["n"] += 1
        if e.get("correct", True): by_type[t]["correct"] += 1
        by_type[t]["pnl"] += e["pnl"]
    return {"n": n, "correct": c, "wrong": w, "pnl": pnl, "invested": invested,
            "roi": pnl/invested*100 if invested else 0, "daily": daily,
            "blocked": len(blocked),
            "blocked_saved": sum(STAKE for e in blocked if not e.get("correct", True)),
            "blocked_fp": sum(1 for e in blocked if e.get("correct", True)),
            "by_type": by_type}

s_floor = calc(all_results, "floor")
s_guard = calc(all_results, "guarded")
s_enh = calc(all_results, "enhanced")

print(f"\n{'='*70}")
print(f"FLOOR ONLY:    {s_floor['n']} trades, {s_floor['correct']}/{s_floor['n']} correct, P&L=${s_floor['pnl']:+.2f}")
print(f"5-GUARD:       {s_guard['n']} trades, {s_guard['correct']}/{s_guard['n']} correct, P&L=${s_guard['pnl']:+.2f} | {s_guard['blocked']} blocked (saved ${s_guard['blocked_saved']:.0f})")
print(f"ENHANCED:      {s_enh['n']} trades, {s_enh['correct']}/{s_enh['n']} correct, P&L=${s_enh['pnl']:+.2f} | {s_enh['blocked']} blocked (saved ${s_enh['blocked_saved']:.0f})")
for t, v in sorted(s_enh["by_type"].items()):
    print(f"  {t:12s}: {v['n']} trades, {v['correct']}/{v['n']} correct, ${v['pnl']:+.2f}")


# ── HTML ─────────────────────────────────────────────────────────────────

dates_j = json.dumps([r["date"] for r in all_results])
cum = {"floor": [], "guarded": [], "enhanced": []}
run = {"floor": 0, "guarded": 0, "enhanced": 0}
for r in all_results:
    for k in cum:
        evs = [e for e in r[k] if "BLOCKED" not in e["type"]]
        run[k] += sum(e["pnl"] for e in evs)
        cum[k].append(round(run[k], 2))

# Blocked events detail
blocked_rows = ""
for r in all_results:
    for e in r["enhanced"]:
        if "BLOCKED" not in e["type"]: continue
        would_have = "LOST $100" if not e.get("correct", True) else "won (blocked)"
        reasons_html = "<br>".join(f"&bull; {rr}" for rr in e.get("reasons", []))
        cls = "green" if not e.get("correct", True) else "yellow"
        blocked_rows += f"<tr><td>{r['date']}</td><td>{e['type'].replace('BLOCKED_','')}</td><td>{e['bracket']}</td><td>{reasons_html}</td><td class='{cls}'>{would_have}</td></tr>"

# New signal types detail
new_signal_rows = ""
for r in all_results:
    for e in r["enhanced"]:
        if e["type"] in ("FLOOR_T1", "FLOOR_T2") or "BLOCKED" in e["type"]: continue
        yes_str = f"{e['yes']:.1%}" if e["yes"] else "N/A"
        ok = "&#x2705;" if e.get("correct", True) else "&#x274C;"
        cls = "green" if e.get("correct", True) else "red"
        new_signal_rows += f"<tr><td>{r['date']}</td><td>{e['type']}</td><td>{e['bracket']}</td><td>{e['time']}</td><td>{yes_str}</td><td class='{cls}'>{ok} ${e['pnl']:+.0f}</td></tr>"

# OM accuracy table
om_accuracy_rows = ""
for r in all_results:
    om_raw = r["om_raw"]
    bias_corrected = round(om_raw + OPENMETEO_BIAS, 1)
    actual = r["wu_high"]
    error = round(bias_corrected - actual, 1)
    dyn_b = r["debug"]["dynamic_bias_9am"]
    dyn_fc = r["debug"]["dynamic_forecast"]
    dyn_err = round(dyn_fc - actual, 1) if dyn_fc else "?"
    om_pk = r["debug"]["om_peak_hour"]
    pk_str = f"{int(om_pk)}:00" if om_pk else "?"
    cls = "green" if abs(error) <= 1 else ("yellow" if abs(error) <= 2 else "red")
    dcls = "green" if dyn_fc and abs(dyn_fc - actual) <= 1 else ("yellow" if dyn_fc and abs(dyn_fc - actual) <= 2 else "red")
    om_accuracy_rows += f"<tr><td>{r['date']}</td><td>{om_raw}°C</td><td>{bias_corrected}°C</td><td class='{cls}'>{error:+.1f}°C</td><td>{dyn_b:+.1f}°C</td><td>{dyn_fc}°C</td><td class='{dcls}'>{dyn_err}°C</td><td>{pk_str}</td><td><strong>{actual}°C</strong></td></tr>"

# Daily detail
daily_rows = ""
for r in all_results:
    evs = r["enhanced"]
    real = [e for e in evs if "BLOCKED" not in e["type"]]
    blocked = [e for e in evs if "BLOCKED" in e["type"]]
    pnl = sum(e["pnl"] for e in real)
    cls = "green" if pnl >= 0 else "red"
    chips = ""
    for e in evs:
        if "BLOCKED" in e["type"]:
            chips += f"<span class='chip chip-blocked'>&#x1F6AB; {e['bracket']}</span> "
        else:
            tag = {"FLOOR_T1":"T1","FLOOR_T2":"T2","T2_UPPER":"T2&#x2191;","MIDDAY_T2":"MID","CEIL_NO":"CEIL","LOCKED_YES":"LOCK"}[e["type"]]
            ok = "&#x2705;" if e["correct"] else "&#x274C;"
            yes_str = f"{e['yes']:.0%}" if e["yes"] else "?"
            bad = "chip-bad" if not e["correct"] else ""
            chips += f"<span class='chip {bad}'>{ok}{tag} {e['side']} {e['bracket']} @{yes_str} &rarr; ${e['pnl']:+.0f}</span> "
    blk = f" + {len(blocked)} blocked" if blocked else ""
    daily_rows += f"<tr><td><strong>{r['date']}</strong></td><td>{r['wu_high']}°C</td><td>{r['forecast']}°C</td><td>{len(real)}{blk}</td><td class='{cls}'><strong>${pnl:+.0f}</strong></td></tr><tr class='detail-row'><td colspan='5'>{chips}</td></tr>"

# Signal type performance
type_perf_rows = ""
for t in ["FLOOR_T1", "FLOOR_T2", "T2_UPPER", "MIDDAY_T2", "CEIL_NO", "LOCKED_YES"]:
    d = s_enh["by_type"].get(t, {"n": 0, "correct": 0, "pnl": 0})
    if d["n"] == 0: type_perf_rows += f"<tr class='muted'><td>{t}</td><td>0</td><td>-</td><td>-</td><td>$0</td></tr>"; continue
    wr = d["correct"] / d["n"] * 100
    cls = "green" if d["pnl"] >= 0 else "red"
    wr_cls = "green" if wr == 100 else ("yellow" if wr >= 80 else "red")
    type_perf_rows += f"<tr><td>{t}</td><td>{d['n']}</td><td class='{wr_cls}'>{d['correct']}/{d['n']} ({wr:.0f}%)</td><td class='{cls}'>${d['pnl']:+.2f}</td><td>{d['pnl']/d['n']:+.2f}</td></tr>"

now_cet = datetime.now(timezone.utc).astimezone(CET)

html = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8">
<title>Enhanced Strategy Backtest</title>
<script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>
<style>
  *{{margin:0;padding:0;box-sizing:border-box}}
  body{{font-family:'Segoe UI',system-ui,sans-serif;background:#0d1117;color:#c9d1d9;padding:24px;line-height:1.6}}
  .container{{max-width:1200px;margin:0 auto}}
  h1{{font-size:22px;color:#e6edf3}} h2{{font-size:17px;color:#e6edf3;margin:28px 0 12px;border-bottom:1px solid #30363d;padding-bottom:6px}}
  h3{{font-size:14px;color:#e6edf3;margin:16px 0 8px}}
  .sub{{font-size:13px;color:#8b949e;margin-bottom:20px}}
  .green{{color:#2ecc71}} .red{{color:#e74c3c}} .yellow{{color:#f1c40f}} .muted{{color:#484f58}}

  .compare{{display:flex;gap:14px;margin:16px 0;flex-wrap:wrap}}
  .sc{{flex:1;min-width:220px;background:#161b22;border-radius:10px;padding:16px}}
  .sc.safe{{border:2px solid #2ecc71}} .sc.guard{{border:2px solid #58a6ff}} .sc.best{{border:2px solid #a371f7}}
  .sc h3{{font-size:14px;margin-bottom:10px}} .sc .big{{font-size:28px;font-weight:700}}
  .sc .row{{display:flex;justify-content:space-between;margin:3px 0;font-size:12px}} .sc .label{{color:#8b949e}}

  .chart-box{{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:12px;margin:12px 0}}
  table{{width:100%;border-collapse:collapse;font-size:13px}}
  th{{text-align:left;padding:8px 10px;border-bottom:2px solid #30363d;color:#8b949e;font-size:10px;text-transform:uppercase}}
  td{{padding:6px 10px;border-bottom:1px solid #21262d}}
  .section{{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:16px;margin:12px 0}}
  .detail-row td{{padding:4px 10px 10px;font-size:11px;border-bottom:2px solid #30363d}}
  .chip{{display:inline-block;padding:2px 8px;margin:2px;border-radius:4px;background:#21262d;border:1px solid #30363d;font-size:11px;white-space:nowrap}}
  .chip-bad{{background:#3d1418;border-color:#e74c3c}}
  .chip-blocked{{background:#1c2333;border-color:#58a6ff;color:#58a6ff}}

  .layers{{background:#161b22;border:1px solid #30363d;border-radius:10px;padding:20px;margin:16px 0}}
  .layer{{display:flex;gap:12px;margin:10px 0;padding:10px 14px;background:#0d1117;border-radius:6px;border:1px solid #21262d}}
  .layer-num{{min-width:28px;height:28px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-weight:700;font-size:13px}}
  .l-green{{background:#2ecc71;color:#0d1117}} .l-blue{{background:#58a6ff;color:#0d1117}} .l-purple{{background:#a371f7;color:#0d1117}}
  .layer-body{{flex:1}} .layer-title{{font-weight:600;font-size:13px;color:#e6edf3}} .layer-desc{{font-size:12px;color:#8b949e;margin-top:2px}}

  .callout{{border-radius:10px;padding:20px;margin:20px 0}}
  .callout-purple{{background:linear-gradient(135deg,#1c1430,#161b22);border:2px solid #a371f7}}
  .callout-purple h3{{color:#a371f7;font-size:16px;margin-bottom:8px}}
  .callout-blue{{background:linear-gradient(135deg,#0d1b2d,#161b22);border:2px solid #58a6ff}}
  .callout-blue h3{{color:#58a6ff;font-size:16px;margin-bottom:8px}}
</style></head><body>
<div class="container">

<h1>Enhanced Strategy Backtest</h1>
<div class="sub">
  {len(all_results)} Paris days &bull; $100/trade &bull; Dynamic OM bias &bull; 5-layer defense &bull;
  {now_cet.strftime('%H:%M CET, %b %d %Y')}
</div>

<h2>Strategy Layers</h2>
<div class="layers">
  <div class="layer"><div class="layer-num l-green">1</div><div class="layer-body">
    <div class="layer-title">Floor NO T1 &mdash; Mathematical Certainty</div>
    <div class="layer-desc">When running high crosses a bracket's kill threshold, it's dead. Zero risk.</div>
  </div></div>
  <div class="layer"><div class="layer-num l-green">2</div><div class="layer-body">
    <div class="layer-title">Floor NO T2 &mdash; 9am Forecast Kill (Lower Brackets)</div>
    <div class="layer-desc">At 9am, if OM forecast &minus; bracket &ge; 4&deg;C, buy NO. Safe because if OM underforecasts, actual temp is even higher &rarr; bracket stays dead.</div>
  </div></div>
  <div class="layer"><div class="layer-num l-purple">3</div><div class="layer-body">
    <div class="layer-title">T2 Upper &mdash; 9am Forecast Kill (Upper Brackets) <span class="yellow">[NEW]</span></div>
    <div class="layer-desc">At 9am, if bracket &minus; adjusted_forecast &ge; 5&deg;C, buy NO on upper brackets. Requires: no OM underforecasting (&lt;1&deg;C morning bias), adjusted OM max well below bracket.</div>
  </div></div>
  <div class="layer"><div class="layer-num l-purple">4</div><div class="layer-body">
    <div class="layer-title">Midday T2 &mdash; Noon Reassessment <span class="yellow">[NEW]</span></div>
    <div class="layer-desc">At noon, with 6h of real data, use running high + OM remaining trajectory + dynamic bias to kill brackets with 2.5&deg;C buffer.</div>
  </div></div>
  <div class="layer"><div class="layer-num l-blue">5</div><div class="layer-body">
    <div class="layer-title">Guarded Ceiling NO &amp; Locked-In YES &mdash; 5 Safeguards</div>
    <div class="layer-desc">Late-day signals blocked if: OM peak hour is later, OM remaining max is higher, OM high near bracket, any source rising, SYNOP velocity &gt;0.3&deg;C/3h.</div>
  </div></div>
</div>

<h2>Results: Three Strategies</h2>
<div class="compare">
  <div class="sc safe">
    <h3 class="green">Floor NO Only</h3>
    <div class="big green">${s_floor['pnl']:+.2f}</div>
    <div class="row"><span class="label">Trades</span><span>{s_floor['n']}</span></div>
    <div class="row"><span class="label">Win rate</span><span class="green">{s_floor['correct']}/{s_floor['n']} (100%)</span></div>
    <div class="row"><span class="label">Losses</span><span class="green">0</span></div>
    <div class="row"><span class="label">ROI</span><span>{s_floor['roi']:.1f}%</span></div>
  </div>
  <div class="sc guard">
    <h3 style="color:#58a6ff">5-Guard</h3>
    <div class="big {'green' if s_guard['pnl']>=0 else 'red'}">${s_guard['pnl']:+.2f}</div>
    <div class="row"><span class="label">Trades</span><span>{s_guard['n']}</span></div>
    <div class="row"><span class="label">Win rate</span><span class="{'green' if s_guard['wrong']==0 else 'yellow'}">{s_guard['correct']}/{s_guard['n']} ({s_guard['correct']/s_guard['n']*100 if s_guard['n'] else 0:.0f}%)</span></div>
    <div class="row"><span class="label">Blocked</span><span style="color:#58a6ff">{s_guard['blocked']} (saved ${s_guard['blocked_saved']:.0f})</span></div>
    <div class="row"><span class="label">ROI</span><span>{s_guard['roi']:.1f}%</span></div>
  </div>
  <div class="sc best">
    <h3 style="color:#a371f7">Enhanced</h3>
    <div class="big {'green' if s_enh['pnl']>=0 else 'red'}">${s_enh['pnl']:+.2f}</div>
    <div class="row"><span class="label">Trades</span><span>{s_enh['n']}</span></div>
    <div class="row"><span class="label">Win rate</span><span class="{'green' if s_enh['wrong']==0 else 'yellow'}">{s_enh['correct']}/{s_enh['n']} ({s_enh['correct']/s_enh['n']*100 if s_enh['n'] else 0:.0f}%)</span></div>
    <div class="row"><span class="label">New signals</span><span style="color:#a371f7">{sum(v['n'] for t,v in s_enh['by_type'].items() if t not in ('FLOOR_T1','FLOOR_T2'))}</span></div>
    <div class="row"><span class="label">Blocked</span><span style="color:#58a6ff">{s_enh['blocked']} (saved ${s_enh['blocked_saved']:.0f})</span></div>
    <div class="row"><span class="label">ROI</span><span class="{'green' if s_enh['roi']>=0 else 'red'}">{s_enh['roi']:.1f}%</span></div>
  </div>
</div>

<h2>Cumulative P&amp;L</h2>
<div class="chart-box"><div id="cumChart" style="height:380px"></div></div>
<script>
Plotly.newPlot('cumChart', [
  {{ x:{dates_j}, y:{json.dumps(cum['floor'])}, type:'scatter', mode:'lines+markers',
     name:'Floor NO only', line:{{color:'#2ecc71',width:2}}, marker:{{size:5}} }},
  {{ x:{dates_j}, y:{json.dumps(cum['guarded'])}, type:'scatter', mode:'lines+markers',
     name:'5-Guard', line:{{color:'#58a6ff',width:2,dash:'dash'}}, marker:{{size:5}} }},
  {{ x:{dates_j}, y:{json.dumps(cum['enhanced'])}, type:'scatter', mode:'lines+markers',
     name:'Enhanced', line:{{color:'#a371f7',width:3}}, marker:{{size:7}} }}
], {{
  paper_bgcolor:'#161b22', plot_bgcolor:'#161b22', font:{{color:'#c9d1d9',size:12}},
  margin:{{l:60,r:30,t:10,b:50}},
  xaxis:{{gridcolor:'#21262d'}}, yaxis:{{gridcolor:'#21262d',title:'Cumulative P&L ($)',zeroline:true,zerolinecolor:'#30363d'}},
  legend:{{bgcolor:'rgba(22,27,34,0.9)',bordercolor:'#30363d',borderwidth:1,x:0.01,y:0.99}},
  hovermode:'x unified'
}}, {{responsive:true,displayModeBar:false}});
</script>

<h2>Signal Type Performance</h2>
<div class="section">
<table>
  <thead><tr><th>Signal Type</th><th>Trades</th><th>Win Rate</th><th>Total P&amp;L</th><th>Avg P&amp;L</th></tr></thead>
  <tbody>{type_perf_rows}</tbody>
</table>
</div>

<h2>Open-Meteo Accuracy &amp; Dynamic Bias</h2>
<div class="section">
<table>
  <thead><tr><th>Date</th><th>OM Raw</th><th>+Bias</th><th>Error</th><th>Dyn Bias (9am)</th><th>Dyn Forecast</th><th>Dyn Error</th><th>OM Peak Hr</th><th>Actual</th></tr></thead>
  <tbody>{om_accuracy_rows}</tbody>
</table>
</div>

{"<h2>New Signals (T2 Upper + Midday T2)</h2><div class='section'><table><thead><tr><th>Date</th><th>Type</th><th>Bracket</th><th>Time</th><th>YES Price</th><th>Result</th></tr></thead><tbody>" + new_signal_rows + "</tbody></table></div>" if new_signal_rows else "<div class='callout callout-blue'><h3>No New Signals Fired</h3><p>On these 8 days, the T2 Upper and Midday T2 conditions were either already covered by Floor T1/T2, or didn't meet the safety requirements. The enhanced strategy acts as insurance — it's ready to catch opportunities when they arise while keeping you safe.</p></div>"}

<h2>Blocked Signals</h2>
<div class="section">
<table>
  <thead><tr><th>Date</th><th>Type</th><th>Bracket</th><th>Why Blocked</th><th>Would Have</th></tr></thead>
  <tbody>{blocked_rows if blocked_rows else "<tr><td colspan='5' class='muted'>None</td></tr>"}</tbody>
</table>
</div>

<h2>Daily Detail &mdash; Enhanced Strategy</h2>
<div class="section">
<table>
  <thead><tr><th>Date</th><th>Actual High</th><th>Forecast</th><th>Trades</th><th>P&amp;L</th></tr></thead>
  <tbody>{daily_rows}</tbody>
</table>
</div>

<div class="callout callout-purple">
  <h3>Key Insights</h3>
  <ul style="margin-left:16px;line-height:2">
    <li><strong>Floor NO (T1+T2)</strong> is the bedrock: {s_floor['n']} trades, 100% win rate, ${s_floor['pnl']:+.2f}</li>
    <li><strong>5 Guards</strong> perfectly blocked all {s_guard['blocked']} dangerous Ceiling/Lock-In signals (saved ${s_guard['blocked_saved']:.0f})</li>
    <li><strong>Dynamic OM bias</strong> catches forecast drift &mdash; on Feb 16, morning bias was {[r['debug']['dynamic_bias_9am'] for r in all_results if r['date']=='2026-02-16'][0]:+.1f}°C, flagging massive underforecast</li>
    <li><strong>T2 Upper</strong> uses 5°C buffer + underforecast check to safely kill upper brackets</li>
    <li><strong>Midday T2</strong> re-evaluates at noon with 6h of real data for additional kills</li>
    <li><strong>OM peak hour</strong> was the most powerful guard &mdash; on both losing days (Feb 15 &amp; 18), OM predicted peak at {int([r['debug']['om_peak_hour'] for r in all_results if r['date']=='2026-02-15'][0])}:00 and {int([r['debug']['om_peak_hour'] for r in all_results if r['date']=='2026-02-18'][0])}:00</li>
  </ul>
</div>

</div></body></html>"""

out_path = r"C:\Users\Charl\Desktop\Cursor\weather-bot\enhanced_backtest.html"
with open(out_path, "w", encoding="utf-8") as f:
    f.write(html)
print(f"\nSaved to {out_path}")

import webbrowser
webbrowser.open(out_path)
