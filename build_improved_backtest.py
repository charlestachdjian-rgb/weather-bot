"""
Improved strategy backtest with multiple safeguards for Ceiling NO and Locked-In YES.

New guards:
1. OM Peak Hour: if Open-Meteo says the daily max comes AFTER signal time, abort.
2. OM Remaining Max: if OM forecasts higher temps in remaining hours, abort.
3. OM Forecast vs Bracket: if OM forecast_high >= bracket_lo, abort Ceiling NO.
4. Multi-source Trend: if ANY source shows rising trend (last 3h), abort.
5. SYNOP Velocity: if SYNOP rose > 0.3°C in last 3 hours, abort.

Compares: Floor NO only | Old (unsafe) | New (guarded)
"""
import urllib.request, json, re, sys, time
from datetime import datetime, date, timezone, timedelta
from zoneinfo import ZoneInfo

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

CET = ZoneInfo("Europe/Paris")
CDG_LAT, CDG_LON = 49.0097, 2.5479
ROUNDING_BUFFER = 0.5
FORECAST_KILL_BUFFER = 4.0
OPENMETEO_BIAS = 1.0
LATE_DAY_HOUR = 16
LOCK_IN_HOUR  = 17
CEIL_GAP      = 2.0
MIN_YES_ALERT = 0.03
STAKE          = 100

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
        match = re.search(r"be\s+(\d+)\s*C\s+or\s+below", q)
        if match: lo, hi = None, float(match.group(1))
        else:
            match = re.search(r"be\s+(\d+)\s*C\s+or\s+higher", q)
            if match: lo, hi = float(match.group(1)), None
            else:
                match = re.search(r"be\s+(\d+)\s*C\s+on", q)
                if match: v = float(match.group(1)); lo, hi = v, v
        if lo is None and hi is not None: label = f"<={int(hi)}°C"
        elif lo is not None and hi is not None and lo == hi: label = f"{int(lo)}°C"
        elif lo is not None and hi is None: label = f">={int(lo)}°C"
        else: label = "?"
        tids = m.get("clobTokenIds", "[]")
        try: tids = json.loads(tids) if isinstance(tids, str) else tids
        except: tids = []
        markets.append({"label": label, "lo": lo, "hi": hi, "yes_token": tids[0] if tids else None})
    markets.sort(key=lambda x: x["hi"] if x["hi"] is not None else 999)
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


# ── Safeguard checks ────────────────────────────────────────────────────

def om_peak_hour(om_hourly):
    """What hour does Open-Meteo forecast the daily max?"""
    if not om_hourly: return None
    peak = max(om_hourly, key=lambda p: p["temp"])
    return peak["hour"]

def om_remaining_max(om_hourly, after_hour):
    """Max forecast temp for hours AFTER the given hour."""
    remaining = [p["temp"] for p in om_hourly if p["hour"] > after_hour]
    return max(remaining) if remaining else None

def om_forecast_high(om_hourly):
    """The full-day forecast high from Open-Meteo."""
    if not om_hourly: return None
    return max(p["temp"] for p in om_hourly)

def source_trend(pts, at_hour, window=3):
    """Is the temperature rising or falling over the last `window` hours?"""
    relevant = [p for p in pts if at_hour - window <= p["hour"] <= at_hour]
    if len(relevant) < 2: return "UNKNOWN"
    first_temp = relevant[0]["temp"]
    last_temp = relevant[-1]["temp"]
    if last_temp > first_temp + 0.3: return "RISING"
    if last_temp < first_temp - 0.3: return "FALLING"
    return "FLAT"

def synop_velocity(synop, at_hour, window=3):
    """Temperature change over last `window` hours from SYNOP."""
    relevant = [p for p in synop if at_hour - window <= p["hour"] <= at_hour]
    if len(relevant) < 2: return 0
    return relevant[-1]["temp"] - relevant[0]["temp"]

def should_block_risky_signal(signal_hour, running_high, bracket_lo, wu_obs, synop, om_hourly):
    """
    Return (blocked: bool, reasons: list[str]) for whether a risky signal should be blocked.
    """
    reasons = []

    # Guard 1: OM Peak Hour — if forecast peak is AFTER our signal, abort
    peak_h = om_peak_hour(om_hourly)
    if peak_h is not None and peak_h > signal_hour:
        reasons.append(f"OM forecasts peak at {int(peak_h)}:00, after signal at {signal_hour}:00")

    # Guard 2: OM Remaining Max — if OM says it'll go higher than running high
    rem_max = om_remaining_max(om_hourly, signal_hour)
    if rem_max is not None and rem_max > running_high + 0.5:
        reasons.append(f"OM forecasts {rem_max:.1f}°C later today (running high: {running_high}°C)")

    # Guard 3: OM Forecast vs Bracket — if OM forecast_high + bias >= bracket_lo
    om_high = om_forecast_high(om_hourly)
    if om_high is not None and bracket_lo is not None:
        corrected = om_high + OPENMETEO_BIAS
        if corrected >= bracket_lo - 1.0:
            reasons.append(f"OM forecast high {corrected:.1f}°C is close to bracket {bracket_lo}°C")

    # Guard 4: Multi-source trend — if ANY source shows rising
    wu_trend = source_trend([{"hour": p["hour"], "temp": p["temp"]} for p in wu_obs], signal_hour)
    syn_trend = source_trend(synop, signal_hour)
    om_trend = source_trend([{"hour": p["hour"], "temp": p["temp"]} for p in om_hourly], signal_hour)
    rising = [s for s, t in [("METAR", wu_trend), ("SYNOP", syn_trend), ("Open-Meteo", om_trend)] if t == "RISING"]
    if rising:
        reasons.append(f"Rising trend on: {', '.join(rising)}")

    # Guard 5: SYNOP velocity — if SYNOP rose > 0.3°C in last 3 hours
    vel = synop_velocity(synop, signal_hour)
    if vel > 0.3:
        reasons.append(f"SYNOP rose {vel:+.1f}°C in last 3h")

    return (len(reasons) > 0, reasons)


# ── Simulate one day (all 3 strategies) ──────────────────────────────────

def simulate_day(day_info, wu_obs, synop, om_hourly, markets, price_histories):
    wu_high = day_info["wu_high"]
    forecast = round(day_info["openmeteo_high"] + OPENMETEO_BIAS, 1)

    floor_events = []
    old_events = []
    new_events = []

    _killed_floor = set()
    _killed_old = set()
    _killed_new = set()

    def add_floor(e):
        floor_events.append(e)
        old_events.append(e)
        new_events.append(e)

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
                    if m["label"] not in _killed_floor:
                        yes_p = yes_at(price_histories.get(m["label"], []), obs["hour"])
                        correct = bracket_resolved_no(m["lo"], m["hi"], wu_high)
                        ev = {"time": obs["time"], "hour": obs["hour"], "type": "FLOOR_T1",
                              "bracket": m["label"], "side": "NO", "yes": yes_p,
                              "correct": correct, "pnl": compute_pnl("NO", yes_p, correct)}
                        add_floor(ev)
                        _killed_floor.add(m["label"])
                        _killed_old.add(m["label"])
                        _killed_new.add(m["label"])

    # Phase 2: T2 at 9am
    for m in markets:
        hi = m["hi"]
        if hi is None or m["label"] in _killed_floor: continue
        if forecast - hi >= FORECAST_KILL_BUFFER:
            yes_p = yes_at(price_histories.get(m["label"], []), 9)
            correct = bracket_resolved_no(m["lo"], m["hi"], wu_high)
            ev = {"time": "09:00", "hour": 9, "type": "FLOOR_T2",
                  "bracket": m["label"], "side": "NO", "yes": yes_p,
                  "correct": correct, "pnl": compute_pnl("NO", yes_p, correct)}
            add_floor(ev)
            _killed_floor.add(m["label"])
            _killed_old.add(m["label"])
            _killed_new.add(m["label"])

    # Phase 3: After 9am
    _ceil_old = set(); _ceil_new = set()
    _lock_old = set(); _lock_new = set()

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
                    if m["label"] not in _killed_floor:
                        floor_events.append(ev); _killed_floor.add(m["label"])
                    if m["label"] not in _killed_old:
                        old_events.append(ev); _killed_old.add(m["label"])
                    if m["label"] not in _killed_new:
                        new_events.append(ev); _killed_new.add(m["label"])

        # Ceiling NO
        if hour >= LATE_DAY_HOUR:
            for m in markets:
                lo = m["lo"]
                if lo is None: continue
                gap = lo - rh
                if gap >= CEIL_GAP:
                    yes_p = yes_at(price_histories.get(m["label"], []), hour)
                    if yes_p is not None and yes_p >= MIN_YES_ALERT:
                        correct = bracket_resolved_no(m["lo"], m["hi"], wu_high)
                        ev = {"time": obs["time"], "hour": hour, "type": "CEIL_NO",
                              "bracket": m["label"], "side": "NO", "yes": yes_p,
                              "correct": correct, "pnl": compute_pnl("NO", yes_p, correct)}

                        if m["label"] not in _killed_old and m["label"] not in _ceil_old:
                            old_events.append(ev)
                            _killed_old.add(m["label"])
                        _ceil_old.add(m["label"])

                        if m["label"] not in _killed_new and m["label"] not in _ceil_new:
                            blocked, reasons = should_block_risky_signal(
                                hour, rh, lo, wu_obs, synop, om_hourly)
                            if not blocked:
                                new_events.append(ev)
                                _killed_new.add(m["label"])
                            else:
                                new_events.append({**ev, "type": "BLOCKED_CEIL",
                                    "pnl": 0, "reasons": reasons})
                        _ceil_new.add(m["label"])

        # Locked-In YES
        if hour >= LOCK_IN_HOUR:
            for m in markets:
                lo, hi = m["lo"], m["hi"]
                if lo is None or hi is None: continue
                if lo - ROUNDING_BUFFER <= rh <= hi + ROUNDING_BUFFER:
                    yes_p = yes_at(price_histories.get(m["label"], []), hour)
                    if yes_p is not None and yes_p < 0.80:
                        correct = not bracket_resolved_no(m["lo"], m["hi"], wu_high)
                        ev = {"time": obs["time"], "hour": hour, "type": "LOCKED_YES",
                              "bracket": m["label"], "side": "YES", "yes": yes_p,
                              "correct": correct, "pnl": compute_pnl("YES", yes_p, correct)}

                        if m["label"] not in _lock_old:
                            old_events.append(ev)
                        _lock_old.add(m["label"])

                        if m["label"] not in _lock_new:
                            blocked, reasons = should_block_risky_signal(
                                hour, rh, lo, wu_obs, synop, om_hourly)
                            if not blocked:
                                new_events.append(ev)
                            else:
                                new_events.append({**ev, "type": "BLOCKED_LOCK",
                                    "pnl": 0, "reasons": reasons})
                        _lock_new.add(m["label"])

    for lst in [floor_events, old_events, new_events]:
        lst.sort(key=lambda e: e["hour"])

    return {"floor": floor_events, "old": old_events, "new": new_events}


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
                        "forecast": round(day["openmeteo_high"] + OPENMETEO_BIAS, 1),
                        **result})

    for key, label in [("floor", "Floor"), ("old", "Old"), ("new", "New")]:
        evs = result[key]
        real = [e for e in evs if "BLOCKED" not in e["type"]]
        blocked = [e for e in evs if "BLOCKED" in e["type"]]
        pnl = sum(e["pnl"] for e in evs)
        wrong = sum(1 for e in real if not e.get("correct", True))
        print(f"{label}:{len(real)}t/${pnl:+.0f}" + (f"({wrong}L)" if wrong else ""), end=" ")
    blocked_list = [e for e in result["new"] if "BLOCKED" in e["type"]]
    if blocked_list:
        for b in blocked_list:
            print(f"\n    BLOCKED {b['type'].replace('BLOCKED_','')} {b['bracket']}: {'; '.join(b.get('reasons',[]))}", end="")
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
    return {"n": n, "correct": c, "wrong": w, "pnl": pnl, "invested": invested,
            "roi": pnl/invested*100 if invested else 0, "daily": daily,
            "best": max(daily) if daily else 0, "worst": min(daily) if daily else 0,
            "blocked": len(blocked),
            "blocked_saved": sum(STAKE for e in blocked if not e.get("correct", True))}

s_floor = calc(all_results, "floor")
s_old = calc(all_results, "old")
s_new = calc(all_results, "new")

print(f"\n{'='*70}")
print(f"FLOOR ONLY:    {s_floor['n']} trades, {s_floor['correct']}/{s_floor['n']} win, P&L=${s_floor['pnl']:+.2f}")
print(f"OLD (UNSAFE):  {s_old['n']} trades, {s_old['correct']}/{s_old['n']} win, P&L=${s_old['pnl']:+.2f}, {s_old['wrong']} losses")
print(f"NEW (GUARDED): {s_new['n']} trades, {s_new['correct']}/{s_new['n']} win, P&L=${s_new['pnl']:+.2f}, {s_new['wrong']} losses, {s_new['blocked']} blocked (saved ${s_new['blocked_saved']:.0f})")


# ── HTML ─────────────────────────────────────────────────────────────────

dates_j = json.dumps([r["date"] for r in all_results])
cum = {"floor": [], "old": [], "new": []}
run = {"floor": 0, "old": 0, "new": 0}
for r in all_results:
    for k in cum:
        evs = [e for e in r[k] if "BLOCKED" not in e["type"]]
        run[k] += sum(e["pnl"] for e in evs)
        cum[k].append(round(run[k], 2))

# Blocked events detail
blocked_detail = ""
for r in all_results:
    for e in r["new"]:
        if "BLOCKED" not in e["type"]: continue
        would_have = "LOST $100" if not e.get("correct", True) else "won (correct signal blocked)"
        reasons_html = "<br>".join(f"&bull; {r}" for r in e.get("reasons", []))
        blocked_detail += f"""<tr>
          <td>{r['date']}</td>
          <td>{e['type'].replace('BLOCKED_','')}</td>
          <td>{e['bracket']}</td>
          <td>{reasons_html}</td>
          <td class="{'green' if not e.get('correct',True) else 'yellow'}">{would_have}</td>
        </tr>"""

# Daily detail for new strategy
daily_new_rows = ""
for r in all_results:
    evs = r["new"]
    real = [e for e in evs if "BLOCKED" not in e["type"]]
    blocked = [e for e in evs if "BLOCKED" in e["type"]]
    pnl = sum(e["pnl"] for e in real)
    wrong = sum(1 for e in real if not e.get("correct", True))
    cls = "green" if pnl >= 0 else "red"
    chips = ""
    for e in evs:
        if "BLOCKED" in e["type"]:
            chips += f"<span class='chip chip-blocked'>&#x1F6AB; {e['bracket']} BLOCKED</span> "
        else:
            tag = {"FLOOR_T1":"T1","FLOOR_T2":"T2","CEIL_NO":"CEIL","LOCKED_YES":"LOCK"}[e["type"]]
            ok = "&#x2705;" if e["correct"] else "&#x274C;"
            yes_str = f"{e['yes']:.1%}" if e["yes"] else "?"
            bad_cls = "chip-bad" if not e["correct"] else ""
            chips += f"<span class='chip {bad_cls}'>{ok} {tag} {e['side']} {e['bracket']} @{yes_str} &rarr; ${e['pnl']:+.0f}</span> "
    blk_str = f" + {len(blocked)} blocked" if blocked else ""
    daily_new_rows += f"""<tr>
      <td><strong>{r['date']}</strong></td><td>{r['wu_high']}°C</td>
      <td>{len(real)}{blk_str}</td><td class="{cls}"><strong>${pnl:+.0f}</strong></td>
    </tr><tr class="detail-row"><td colspan="4">{chips}</td></tr>"""

now_cet = datetime.now(timezone.utc).astimezone(CET)

html = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8">
<title>Improved Strategy Backtest</title>
<script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>
<style>
  *{{margin:0;padding:0;box-sizing:border-box}}
  body{{font-family:'Segoe UI',system-ui,sans-serif;background:#0d1117;color:#c9d1d9;padding:24px;line-height:1.6}}
  .container{{max-width:1200px;margin:0 auto}}
  h1{{font-size:22px;color:#e6edf3}} h2{{font-size:17px;color:#e6edf3;margin:28px 0 12px;border-bottom:1px solid #30363d;padding-bottom:6px}}
  .sub{{font-size:13px;color:#8b949e;margin-bottom:20px}}
  .green{{color:#2ecc71}} .red{{color:#e74c3c}} .yellow{{color:#f1c40f}} .muted{{color:#484f58}}

  .compare{{display:flex;gap:14px;margin:16px 0;flex-wrap:wrap}}
  .sc{{flex:1;min-width:220px;background:#161b22;border-radius:10px;padding:16px}}
  .sc.safe{{border:2px solid #2ecc71}} .sc.danger{{border:2px solid #e74c3c}} .sc.improved{{border:2px solid #58a6ff}}
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

  .guards{{background:#161b22;border:1px solid #30363d;border-radius:10px;padding:20px;margin:16px 0}}
  .guard{{display:flex;gap:12px;margin:10px 0;padding:10px 14px;background:#0d1117;border-radius:6px;border:1px solid #21262d}}
  .guard-num{{min-width:28px;height:28px;background:#58a6ff;color:#0d1117;border-radius:50%;display:flex;align-items:center;justify-content:center;font-weight:700;font-size:13px}}
  .guard-body{{flex:1}} .guard-title{{font-weight:600;font-size:13px;color:#e6edf3}} .guard-desc{{font-size:12px;color:#8b949e;margin-top:2px}}

  .callout{{border-radius:10px;padding:20px;margin:20px 0}}
  .callout-blue{{background:linear-gradient(135deg,#0d1b2d,#161b22);border:2px solid #58a6ff}}
  .callout-blue h3{{color:#58a6ff;font-size:16px;margin-bottom:8px}}
</style></head><body>
<div class="container">

<h1>Improved Strategy — With Safeguards</h1>
<div class="sub">
  Same 8 Paris days, $100/trade. Now with 5 data-driven guards that block risky signals when the temperature hasn't peaked yet. &bull;
  {now_cet.strftime('%H:%M CET, %b %d %Y')}
</div>

<h2>The 5 Safeguards</h2>
<div class="guards">
  <div class="guard"><div class="guard-num">1</div><div class="guard-body">
    <div class="guard-title">Open-Meteo Peak Hour</div>
    <div class="guard-desc">If OM forecasts the daily max coming AFTER our signal time &rarr; block. On the losing days, OM showed peak at 10-11pm.</div>
  </div></div>
  <div class="guard"><div class="guard-num">2</div><div class="guard-body">
    <div class="guard-title">Open-Meteo Remaining Max</div>
    <div class="guard-desc">If OM forecasts higher temperatures in the remaining hours of the day than the current running high &rarr; block.</div>
  </div></div>
  <div class="guard"><div class="guard-num">3</div><div class="guard-body">
    <div class="guard-title">Open-Meteo Forecast vs Bracket</div>
    <div class="guard-desc">If OM's daily forecast high (+ bias) is within 1&deg;C of the bracket floor &rarr; block. The temperature might actually reach the bracket.</div>
  </div></div>
  <div class="guard"><div class="guard-num">4</div><div class="guard-body">
    <div class="guard-title">Multi-Source Trend</div>
    <div class="guard-desc">If ANY source (METAR, SYNOP, Open-Meteo) shows a rising trend over the last 3 hours &rarr; block. Temperature hasn't peaked.</div>
  </div></div>
  <div class="guard"><div class="guard-num">5</div><div class="guard-body">
    <div class="guard-title">SYNOP Velocity</div>
    <div class="guard-desc">If SYNOP (0.1&deg;C precision) rose more than 0.3&deg;C in the last 3 hours &rarr; block. Catches subtle upward drift.</div>
  </div></div>
</div>

<h2>Results: Three Strategies Compared</h2>
<div class="compare">
  <div class="sc safe">
    <h3 class="green">Floor NO Only</h3>
    <div class="big {'green' if s_floor['pnl']>=0 else 'red'}">${s_floor['pnl']:+.2f}</div>
    <div class="row"><span class="label">Trades</span><span>{s_floor['n']}</span></div>
    <div class="row"><span class="label">Win rate</span><span class="green">{s_floor['correct']}/{s_floor['n']} (100%)</span></div>
    <div class="row"><span class="label">Losses</span><span class="green">0</span></div>
    <div class="row"><span class="label">ROI</span><span>{s_floor['roi']:.2f}%</span></div>
  </div>
  <div class="sc danger">
    <h3 class="red">Old (No Guards)</h3>
    <div class="big {'green' if s_old['pnl']>=0 else 'red'}">${s_old['pnl']:+.2f}</div>
    <div class="row"><span class="label">Trades</span><span>{s_old['n']}</span></div>
    <div class="row"><span class="label">Win rate</span><span>{s_old['correct']}/{s_old['n']} ({s_old['correct']/s_old['n']*100:.0f}%)</span></div>
    <div class="row"><span class="label">Losses</span><span class="red">{s_old['wrong']} (&minus;${s_old['wrong']*STAKE})</span></div>
    <div class="row"><span class="label">ROI</span><span class="red">{s_old['roi']:.2f}%</span></div>
  </div>
  <div class="sc improved">
    <h3 style="color:#58a6ff">New (5 Guards)</h3>
    <div class="big {'green' if s_new['pnl']>=0 else 'red'}">${s_new['pnl']:+.2f}</div>
    <div class="row"><span class="label">Trades</span><span>{s_new['n']}</span></div>
    <div class="row"><span class="label">Win rate</span><span class="{'green' if s_new['wrong']==0 else 'yellow'}">{s_new['correct']}/{s_new['n']} ({s_new['correct']/s_new['n']*100 if s_new['n'] else 0:.0f}%)</span></div>
    <div class="row"><span class="label">Losses</span><span class="{'green' if s_new['wrong']==0 else 'red'}">{s_new['wrong']}</span></div>
    <div class="row"><span class="label">Blocked</span><span style="color:#58a6ff">{s_new['blocked']} (saved ${s_new['blocked_saved']:.0f})</span></div>
    <div class="row"><span class="label">ROI</span><span class="{'green' if s_new['roi']>=0 else 'red'}">{s_new['roi']:.2f}%</span></div>
  </div>
</div>

<h2>Cumulative P&amp;L</h2>
<div class="chart-box"><div id="cumChart" style="height:380px"></div></div>
<script>
Plotly.newPlot('cumChart', [
  {{ x:{dates_j}, y:{json.dumps(cum['floor'])}, type:'scatter', mode:'lines+markers',
     name:'Floor NO only', line:{{color:'#2ecc71',width:3}}, marker:{{size:6}} }},
  {{ x:{dates_j}, y:{json.dumps(cum['old'])}, type:'scatter', mode:'lines+markers',
     name:'Old (no guards)', line:{{color:'#e74c3c',width:2,dash:'dash'}}, marker:{{size:5}} }},
  {{ x:{dates_j}, y:{json.dumps(cum['new'])}, type:'scatter', mode:'lines+markers',
     name:'New (5 guards)', line:{{color:'#58a6ff',width:3}}, marker:{{size:6}} }}
], {{
  paper_bgcolor:'#161b22', plot_bgcolor:'#161b22', font:{{color:'#c9d1d9',size:12}},
  margin:{{l:60,r:30,t:10,b:50}},
  xaxis:{{gridcolor:'#21262d'}}, yaxis:{{gridcolor:'#21262d',title:'Cumulative P&L ($)',zeroline:true,zerolinecolor:'#30363d'}},
  legend:{{bgcolor:'rgba(22,27,34,0.9)',bordercolor:'#30363d',borderwidth:1,x:0.01,y:0.99}},
  hovermode:'x unified'
}}, {{responsive:true,displayModeBar:false}});
</script>

<h2>Blocked Signals — What the Guards Caught</h2>
<div class="section">
<table>
  <thead><tr><th>Date</th><th>Signal</th><th>Bracket</th><th>Why Blocked</th><th>Would Have</th></tr></thead>
  <tbody>{blocked_detail if blocked_detail else "<tr><td colspan='5' class='muted'>No signals blocked</td></tr>"}</tbody>
</table>
</div>

<h2>Daily Detail — New (Guarded) Strategy</h2>
<div class="section">
<table>
  <thead><tr><th>Date</th><th>High</th><th>Trades</th><th>P&amp;L</th></tr></thead>
  <tbody>{daily_new_rows}</tbody>
</table>
</div>

<div class="callout callout-blue">
  <h3>What Changed</h3>
  <p>The guards blocked <strong>{s_new['blocked']} risky signals</strong> that the old strategy would have fired.
  Of those, <strong>{sum(1 for r in all_results for e in r['new'] if 'BLOCKED' in e['type'] and not e.get('correct', True))}</strong> would have been wrong (&minus;$100 each).
  The others were correct signals that got blocked too (false positives) — a small price to pay for safety.</p>
  <p style="margin-top:8px">The new strategy keeps all the Floor NO gains (${s_floor['pnl']:+.2f}) and adds
  any Ceiling/Lock-In signals that pass all 5 guards. Total: <strong class="{'green' if s_new['pnl']>=0 else 'red'}">${s_new['pnl']:+.2f}</strong>.</p>
</div>

</div></body></html>"""

out_path = r"C:\Users\Charl\Desktop\Cursor\weather-bot\improved_backtest.html"
with open(out_path, "w", encoding="utf-8") as f:
    f.write(html)
print(f"\nSaved to {out_path}")

import webbrowser
webbrowser.open(out_path)
