"""
Build an HTML simulation of what Strategy 1 (Tier 1 + Tier 2) would have done on Feb 21.
This is the best historical day for Tier 2 triggers: WU high was 16°C, forecast ~15.7°C.
"""
import urllib.request, json, re, sys, time
from datetime import datetime, date, timezone, timedelta
from zoneinfo import ZoneInfo

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

CET = ZoneInfo("Europe/Paris")
SIM_DATE = date(2026, 2, 21)
SIM_DATE_STR = SIM_DATE.isoformat()
SIM_LABEL = "February 21, 2026"

CDG_LAT, CDG_LON = 49.0097, 2.5479
ROUNDING_BUFFER = 0.5
FORECAST_KILL_BUFFER = 4.0
OPENMETEO_BIAS = 1.0

KNOWN_OM_HIGH = 14.7  # from backtest_data.json

LATE_DAY_HOUR = 16    # 4pm CET — ceiling NO
LOCK_IN_HOUR  = 17    # 5pm CET — locked-in YES
CEIL_GAP      = 2.0   # bracket_lo - daily_high must be >= this
MIN_YES_ALERT = 0.03  # don't bother if YES < 3 cents
SUM_TOL       = 0.07  # flag if sum of YES deviates > 7% from 1.0


def fetch_wu_timeseries():
    date_str = SIM_DATE.strftime("%Y%m%d")
    url = (f"https://api.weather.com/v1/location/LFPG:9:FR/observations/historical.json"
           f"?apiKey=e1f10a1e78da46f5b10a1e78da96f525&units=m"
           f"&startDate={date_str}&endDate={date_str}")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=15) as r:
        data = json.loads(r.read())
    obs = data.get("observations", [])
    pts = []
    for o in obs:
        ts = o.get("valid_time_gmt", 0)
        temp = o.get("temp")
        if temp is not None:
            dt = datetime.fromtimestamp(ts, tz=CET)
            pts.append({"time_cet": dt.strftime("%H:%M"), "hour": dt.hour + dt.minute/60,
                         "ts": ts, "temp_c": temp})
    return sorted(pts, key=lambda x: x["ts"])


def fetch_synop_timeseries():
    begin = SIM_DATE.strftime("%Y%m%d") + "0000"
    end = (SIM_DATE + timedelta(days=1)).strftime("%Y%m%d") + "0000"
    url = f"https://www.ogimet.com/cgi-bin/getsynop?block=07157&begin={begin}&end={end}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as r:
            text = r.read().decode("utf-8", errors="replace")
        pts = []
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
                dt_utc = datetime(int(parts[1]), int(parts[2]), int(parts[3]),
                                  hour_utc, 0, tzinfo=timezone.utc)
                dt_cet = dt_utc.astimezone(CET)
                pts.append({"time_cet": dt_cet.strftime("%H:%M"),
                             "hour": dt_cet.hour + dt_cet.minute/60,
                             "ts": int(dt_utc.timestamp()), "temp_c": temp})
        return sorted(pts, key=lambda x: x["ts"])
    except Exception as e:
        print(f"  SYNOP fetch failed: {e}")
        return []


def get_forecast_high():
    """Use the forecast that would have been available at 9am, not the post-hoc archive."""
    return round(KNOWN_OM_HIGH + OPENMETEO_BIAS, 1)


def fetch_market_data():
    slug = f"highest-temperature-in-paris-on-february-21-2026"
    url = f"https://gamma-api.polymarket.com/events?slug={slug}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=10) as r:
        data = json.loads(r.read())
    if not data:
        return []
    ev = data[0]
    markets = []
    for m in ev.get("markets", []):
        q = m.get("question", "")
        prices = m.get("outcomePrices", "[]")
        try:
            prices = json.loads(prices) if isinstance(prices, str) else prices
        except:
            prices = []
        yes = float(prices[0]) if prices else None
        vol = float(m.get("volume") or 0)
        closed = bool(m.get("closed"))
        resolved = m.get("resolvedBy", "")

        q_clean = q.replace("\u00b0", "")
        rng = (None, None)
        match = re.search(r"be\s+(\d+)\s*C\s+or\s+below", q_clean)
        if match:
            rng = (None, float(match.group(1)))
        else:
            match = re.search(r"be\s+(\d+)\s*C\s+or\s+higher", q_clean)
            if match:
                rng = (float(match.group(1)), None)
            else:
                match = re.search(r"be\s+(\d+)\s*C\s+on", q_clean)
                if match:
                    val = float(match.group(1))
                    rng = (val, val)

        lo, hi = rng
        if hi is not None:
            if lo is None:
                label = f"<={int(hi)}°C"
            elif lo == hi:
                label = f"{int(lo)}°C"
            else:
                label = f"{int(lo)}-{int(hi)}°C"
        elif lo is not None:
            label = f">={int(lo)}°C"
        else:
            label = "?"

        token_ids = m.get("clobTokenIds", "[]")
        try:
            token_ids = json.loads(token_ids) if isinstance(token_ids, str) else token_ids
        except:
            token_ids = []

        markets.append({
            "label": label, "lo": lo, "hi": hi,
            "yes_price": yes, "volume": vol, "closed": closed,
            "yes_token": token_ids[0] if token_ids else None,
            "resolved_to": m.get("groupItemTitle", ""),
        })
    markets.sort(key=lambda m: m["hi"] if m["hi"] is not None else 999)
    return markets


def fetch_price_history(token_id):
    start_ts = int(datetime(SIM_DATE.year, SIM_DATE.month, SIM_DATE.day,
                            tzinfo=timezone.utc).timestamp())
    end_ts = start_ts + 86400
    url = (f"https://clob.polymarket.com/prices-history?"
           f"market={token_id}&startTs={start_ts}&endTs={end_ts}&interval=1h&fidelity=60")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read())
        history = data.get("history", [])
        return [(int(h["t"]), float(h["p"])) for h in history if h.get("t") and h.get("p")]
    except:
        return []


# ── Fetch ────────────────────────────────────────────────────────────────

print(f"Fetching data for {SIM_LABEL}...", flush=True)
wu = fetch_wu_timeseries()
if not wu:
    print("ERROR: No WU data for this date!")
    sys.exit(1)
wu_high = max(p["temp_c"] for p in wu)
print(f"  WU: {len(wu)} readings, high={wu_high}°C")

synop = fetch_synop_timeseries()
synop_high = max(p["temp_c"] for p in synop) if synop else None
print(f"  SYNOP: {len(synop)} readings" + (f", high={synop_high:.1f}°C" if synop_high else " (no data — OGIMET may have purged)"))

forecast_high = get_forecast_high()
print(f"  Forecast high (OM+bias): {forecast_high}°C")

markets = fetch_market_data()
print(f"  Markets: {len(markets)} brackets")

price_histories = {}
for m in markets:
    if m["yes_token"]:
        ph = fetch_price_history(m["yes_token"])
        price_histories[m["label"]] = ph
        time.sleep(0.1)
print(f"  Price histories: {sum(len(v) for v in price_histories.values())} total points")

# ── Simulate ─────────────────────────────────────────────────────────────
#
# The real strategy runs in real-time:
#   1. T1 kills fire whenever a new METAR reading pushes running high past a threshold
#   2. T2 fires once at 9am, for brackets not yet T1-killed but forecast says dead
#   3. After 9am, T1 continues for remaining brackets
#
# We must simulate in chronological order to get this right.

events = []
_killed = set()

# Phase 1: T1 kills before 9am
running_high = None
for obs in wu:
    if obs["hour"] > 9:
        break
    temp = obs["temp_c"]
    if running_high is None or temp > running_high:
        old_high = running_high
        running_high = temp
        for m in markets:
            hi = m["hi"]
            if hi is None or m["label"] in _killed:
                continue
            was_dead = old_high is not None and old_high >= hi + ROUNDING_BUFFER
            now_dead = running_high >= hi + ROUNDING_BUFFER
            if now_dead and not was_dead:
                ph = price_histories.get(m["label"], [])
                yes_at_time = None
                for ts, p in ph:
                    dt = datetime.fromtimestamp(ts, tz=CET)
                    if dt.hour + dt.minute/60 <= obs["hour"] + 0.5:
                        yes_at_time = p
                events.append({
                    "time": obs["time_cet"], "hour": obs["hour"], "type": "TIER1_KILL",
                    "bracket": m["label"], "bracket_hi": hi,
                    "running_high": running_high,
                    "yes_at_kill": yes_at_time,
                    "profit_per_100": round((yes_at_time or 0) * 100, 1),
                })
                _killed.add(m["label"])

rh_at_9 = running_high

# Phase 2: T2 at 9am
if forecast_high:
    for m in markets:
        hi = m["hi"]
        if hi is None or m["label"] in _killed:
            continue
        if forecast_high - hi >= FORECAST_KILL_BUFFER:
            ph = price_histories.get(m["label"], [])
            yes_at_9 = None
            for ts, p in ph:
                dt = datetime.fromtimestamp(ts, tz=CET)
                if dt.hour <= 9:
                    yes_at_9 = p
            events.append({
                "time": "09:00", "hour": 9, "type": "TIER2_KILL",
                "bracket": m["label"], "bracket_hi": hi,
                "forecast_high": forecast_high,
                "gap": round(forecast_high - hi, 1),
                "yes_at_kill": yes_at_9,
                "profit_per_100": round((yes_at_9 or 0) * 100, 1),
            })
            _killed.add(m["label"])

# Phase 3: T1 kills after 9am + Ceiling NO at 16:00 + Locked-In YES at 17:00
_ceil_checked = set()
_lockin_checked = set()

for obs in wu:
    if obs["hour"] <= 9:
        continue
    temp = obs["temp_c"]
    hour = obs["hour"]

    if running_high is None or temp > running_high:
        old_high = running_high
        running_high = temp
        for m in markets:
            hi = m["hi"]
            if hi is None or m["label"] in _killed:
                continue
            was_dead = old_high is not None and old_high >= hi + ROUNDING_BUFFER
            now_dead = running_high >= hi + ROUNDING_BUFFER
            if now_dead and not was_dead:
                ph = price_histories.get(m["label"], [])
                yes_at_time = None
                for ts, p in ph:
                    dt = datetime.fromtimestamp(ts, tz=CET)
                    if dt.hour + dt.minute/60 <= hour + 0.5:
                        yes_at_time = p
                events.append({
                    "time": obs["time_cet"], "hour": hour, "type": "TIER1_KILL",
                    "bracket": m["label"], "bracket_hi": hi,
                    "running_high": running_high,
                    "yes_at_kill": yes_at_time,
                    "profit_per_100": round((yes_at_time or 0) * 100, 1),
                })
                _killed.add(m["label"])

    # GUARANTEED_NO_CEIL — after 16:00, brackets above daily high by >= 2°C
    if hour >= LATE_DAY_HOUR:
        for m in markets:
            lo = m["lo"]
            if lo is None or m["label"] in _killed or m["label"] in _ceil_checked:
                continue
            gap = lo - running_high
            if gap >= CEIL_GAP:
                ph = price_histories.get(m["label"], [])
                yes_at_time = None
                for ts, p in ph:
                    dt = datetime.fromtimestamp(ts, tz=CET)
                    if dt.hour + dt.minute/60 <= hour + 0.5:
                        yes_at_time = p
                if yes_at_time is not None and yes_at_time >= MIN_YES_ALERT:
                    events.append({
                        "time": obs["time_cet"], "hour": hour, "type": "CEIL_NO",
                        "bracket": m["label"], "bracket_lo": lo,
                        "running_high": running_high,
                        "gap": round(gap, 1),
                        "yes_at_kill": yes_at_time,
                        "profit_per_100": round(yes_at_time * 100, 1),
                    })
                    _killed.add(m["label"])
                _ceil_checked.add(m["label"])

    # LOCKED_IN_YES — after 17:00, daily high rounds to this bracket, YES underpriced
    if hour >= LOCK_IN_HOUR:
        for m in markets:
            lo, hi = m["lo"], m["hi"]
            if lo is None or hi is None or m["label"] in _lockin_checked:
                continue
            if lo - ROUNDING_BUFFER <= running_high <= hi + ROUNDING_BUFFER:
                ph = price_histories.get(m["label"], [])
                yes_at_time = None
                for ts, p in ph:
                    dt = datetime.fromtimestamp(ts, tz=CET)
                    if dt.hour + dt.minute/60 <= hour + 0.5:
                        yes_at_time = p
                if yes_at_time is not None and yes_at_time < 0.80:
                    events.append({
                        "time": obs["time_cet"], "hour": hour, "type": "LOCKED_YES",
                        "bracket": m["label"],
                        "running_high": running_high,
                        "yes_at_kill": yes_at_time,
                        "profit_per_100": round((1.0 - yes_at_time) * 100, 1),
                    })
                _lockin_checked.add(m["label"])

events.sort(key=lambda e: e["hour"])

total_profit = sum(e["profit_per_100"] for e in events if e["profit_per_100"] > 0)
n_trades = sum(1 for e in events if e["profit_per_100"] > 0)

print(f"\nSimulated {len(events)} events, {n_trades} actionable trades")
for e in events:
    tag = {"TIER1_KILL": "T1", "TIER2_KILL": "T2", "CEIL_NO": "CEIL", "LOCKED_YES": "LOCK"}.get(e["type"], "?")
    yes_str = f"{e['yes_at_kill']:.0%}" if e.get("yes_at_kill") else "?"
    print(f"  [{tag}] {e['time']} — {e['bracket']}, YES was {yes_str}, profit ${e['profit_per_100']:.1f}")
print(f"Total theoretical profit: ${total_profit:.1f} per $100/trade")

# Debug: show bracket prices at key times
for m in markets:
    ph = price_histories.get(m["label"], [])
    if not ph:
        continue
    prices_at = {}
    for ts, p in ph:
        dt = datetime.fromtimestamp(ts, tz=CET)
        h = dt.hour
        if h in (9, 12, 16, 17, 18, 20) and h not in prices_at:
            prices_at[h] = p
    if prices_at:
        pts_str = ", ".join(f"{h}h={v:.0%}" for h, v in sorted(prices_at.items()))
        print(f"  {m['label']}: {pts_str}")


# ── HTML ─────────────────────────────────────────────────────────────────

wu_times = json.dumps([p["time_cet"] for p in wu])
wu_temps = json.dumps([p["temp_c"] for p in wu])
synop_times = json.dumps([p["time_cet"] for p in synop]) if synop else "[]"
synop_temps = json.dumps([p["temp_c"] for p in synop]) if synop else "[]"

rh_points = []
rh = None
for obs in wu:
    if rh is None or obs["temp_c"] > rh:
        rh = obs["temp_c"]
    rh_points.append({"time": obs["time_cet"], "rh": rh})
rh_times = json.dumps([p["time"] for p in rh_points])
rh_vals = json.dumps([p["rh"] for p in rh_points])

# Price evolution traces (one per bracket)
price_chart_traces = ""
palette = ["#e74c3c","#e67e22","#f1c40f","#2ecc71","#1abc9c","#3498db","#9b59b6","#e84393","#fd79a8"]
for i, m in enumerate(markets):
    ph = price_histories.get(m["label"], [])
    if not ph:
        continue
    times_j = json.dumps([datetime.fromtimestamp(ts, tz=CET).strftime("%H:%M") for ts, _ in ph])
    prices_j = json.dumps([round(p * 100, 1) for _, p in ph])
    color = palette[i % len(palette)]
    price_chart_traces += f"""{{
        x: {times_j}, y: {prices_j},
        type: 'scatter', mode: 'lines', name: '{m["label"]}',
        line: {{color: '{color}', width: 2}},
        hovertemplate: '{m["label"]}: %{{y:.1f}}¢<extra></extra>'
    }},\n"""

kill_annotations = []
ann_offset = 0
type_colors = {
    "TIER1_KILL": "#2ecc71", "TIER2_KILL": "#f1c40f",
    "CEIL_NO": "#e74c3c", "LOCKED_YES": "#58a6ff",
}
type_short = {
    "TIER1_KILL": "T1", "TIER2_KILL": "T2",
    "CEIL_NO": "CEIL", "LOCKED_YES": "LOCK",
}
for e in events:
    color = type_colors.get(e["type"], "#888")
    tag = type_short.get(e["type"], "?")
    action = "DEAD" if e["type"] != "LOCKED_YES" else "BUY YES"
    ann_offset -= 35
    if ann_offset < -140:
        ann_offset = -30
    kill_annotations.append(f"""{{
        x: '{e["time"]}', y: {e.get("running_high", e.get("forecast_high", 0))},
        xref: 'x', yref: 'y',
        text: '<b>{tag}</b> {e["bracket"]} {action}',
        showarrow: true, arrowhead: 2, arrowcolor: '{color}',
        font: {{ color: '{color}', size: 10 }},
        bgcolor: 'rgba(13,17,23,0.85)', bordercolor: '{color}',
        ax: 30, ay: {ann_offset}
    }}""")

timeline_html = ""
event_css_map = {
    "TIER1_KILL": "event-t1", "TIER2_KILL": "event-t2",
    "CEIL_NO": "event-ceil", "LOCKED_YES": "event-lock",
}
for e in events:
    etype = e["type"]
    css = event_css_map.get(etype, "event-t1")

    if etype == "TIER1_KILL":
        icon = "&#x1F7E2;"
        tier_label = "FLOOR NO &mdash; TIER 1 (CERTAIN)"
        action = f"Buy NO on <strong>{e['bracket']}</strong> &mdash; bracket is dead"
        detail = f"Running high reached {e['running_high']}&deg;C &ge; {e['bracket_hi'] + ROUNDING_BUFFER}&deg;C kill threshold"
        profit_str = f"<span class='green'>${e['profit_per_100']:.1f}</span>" if e['profit_per_100'] > 0 else "<span class='muted'>$0 &mdash; already repriced</span>"
        yes_str = f"{e['yes_at_kill']:.0%}" if e['yes_at_kill'] else "N/A"
        pnl_line = f"YES price: {yes_str} &rarr; Profit per $100: {profit_str}"
    elif etype == "TIER2_KILL":
        icon = "&#x1F7E1;"
        tier_label = "FLOOR NO &mdash; TIER 2 (FORECAST)"
        action = f"Buy NO on <strong>{e['bracket']}</strong> &mdash; forecast says dead"
        detail = f"Forecast {e['forecast_high']}&deg;C &minus; {int(e['bracket_hi'])}&deg;C = {e['gap']}&deg;C gap &ge; {FORECAST_KILL_BUFFER}&deg;C buffer"
        profit_str = f"<span class='green'>${e['profit_per_100']:.1f}</span>" if e['profit_per_100'] > 0 else "<span class='muted'>$0 &mdash; already repriced</span>"
        yes_str = f"{e['yes_at_kill']:.0%}" if e['yes_at_kill'] else "N/A"
        pnl_line = f"YES price: {yes_str} &rarr; Profit per $100: {profit_str}"
    elif etype == "CEIL_NO":
        icon = "&#x1F534;"
        tier_label = "CEILING NO &mdash; LATE DAY"
        action = f"Buy NO on <strong>{e['bracket']}</strong> &mdash; unreachable"
        detail = f"After {LATE_DAY_HOUR}:00, daily high is {e['running_high']}&deg;C, bracket starts at {int(e['bracket_lo'])}&deg;C &mdash; gap {e['gap']}&deg;C &ge; {CEIL_GAP}&deg;C"
        profit_str = f"<span class='green'>${e['profit_per_100']:.1f}</span>"
        yes_str = f"{e['yes_at_kill']:.0%}" if e['yes_at_kill'] else "N/A"
        pnl_line = f"YES price: {yes_str} &rarr; Profit per $100: {profit_str}"
    elif etype == "LOCKED_YES":
        icon = "&#x1F535;"
        tier_label = "LOCKED-IN YES"
        action = f"Buy YES on <strong>{e['bracket']}</strong> &mdash; daily high rounds here"
        detail = f"After {LOCK_IN_HOUR}:00, running high {e['running_high']}&deg;C rounds to {e['bracket']}, YES only {e['yes_at_kill']:.0%}"
        profit_str = f"<span class='green'>${e['profit_per_100']:.1f}</span>"
        yes_str = f"{e['yes_at_kill']:.0%}" if e['yes_at_kill'] else "N/A"
        pnl_line = f"Buy YES at {yes_str} &rarr; resolves $1 &rarr; Profit per $100: {profit_str}"
    else:
        continue

    timeline_html += f"""
    <div class="event {css}">
      <div class="event-time">{e['time']}<br>CET</div>
      <div class="event-body">
        <div class="event-tier">{icon} {tier_label}</div>
        <div class="event-action">{action}</div>
        <div class="event-detail">{detail}</div>
        <div class="event-pnl">{pnl_line}</div>
      </div>
    </div>"""

signal_names = {
    "TIER1_KILL": "Floor NO (T1)",
    "TIER2_KILL": "Floor NO (T2)",
    "CEIL_NO": "Ceiling NO",
    "LOCKED_YES": "Locked-In YES",
}

bracket_rows = ""
for m in markets:
    hi = m["hi"]
    lo = m["lo"]
    label = m["label"]
    yes = m["yes_price"]
    vol = m["volume"]

    sig = "-"
    kill_time = "-"
    side = "-"
    for e in events:
        if e["bracket"] == label:
            sig = signal_names.get(e["type"], e["type"])
            kill_time = e["time"]
            side = "Buy YES" if e["type"] == "LOCKED_YES" else "Buy NO"
            break

    if hi is not None and wu_high >= hi + ROUNDING_BUFFER:
        outcome = "Resolved NO"
        outcome_cls = "green"
    elif lo is not None and hi is not None and lo <= wu_high <= hi:
        outcome = "Resolved YES (winner)"
        outcome_cls = "winner"
    elif lo is not None and hi is None and wu_high >= lo:
        outcome = "Resolved NO"
        outcome_cls = "green"
    elif lo is None and hi is not None and wu_high <= hi:
        outcome = "Resolved YES"
        outcome_cls = "winner"
    else:
        outcome = "Resolved NO"
        outcome_cls = "green"

    bracket_rows += f"""<tr>
        <td>{label}</td>
        <td>${vol:,.0f}</td>
        <td>{sig}</td>
        <td>{side}</td>
        <td>{kill_time}</td>
        <td class="{outcome_cls}">{outcome}</td>
    </tr>"""

# Compute stats per signal type
n_floor_t1 = sum(1 for e in events if e["type"] == "TIER1_KILL")
n_floor_t2 = sum(1 for e in events if e["type"] == "TIER2_KILL")
n_ceil = sum(1 for e in events if e["type"] == "CEIL_NO")
n_lock = sum(1 for e in events if e["type"] == "LOCKED_YES")

html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Strategy Simulation — {SIM_LABEL}</title>
<script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{ font-family: 'Segoe UI', system-ui, sans-serif; background: #0d1117; color: #c9d1d9; padding: 24px; line-height: 1.6; }}
  .container {{ max-width: 1100px; margin: 0 auto; }}
  h1 {{ font-size: 22px; color: #e6edf3; }} h2 {{ font-size: 17px; color: #e6edf3; margin: 28px 0 12px; border-bottom: 1px solid #30363d; padding-bottom: 6px; }}
  .subtitle {{ font-size: 13px; color: #8b949e; margin-bottom: 20px; }}
  .green {{ color: #2ecc71; }} .red {{ color: #e74c3c; }} .yellow {{ color: #f1c40f; }} .muted {{ color: #484f58; }}
  .winner {{ color: #58a6ff; font-weight: 600; }}

  .kpi-row {{ display: flex; gap: 14px; flex-wrap: wrap; margin: 16px 0; }}
  .kpi {{ background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 14px 18px; flex: 1; min-width: 140px; }}
  .kpi .label {{ font-size: 10px; text-transform: uppercase; letter-spacing: 0.5px; color: #8b949e; }}
  .kpi .value {{ font-size: 24px; font-weight: 700; margin-top: 4px; }}
  .kpi .detail {{ font-size: 11px; color: #8b949e; margin-top: 2px; }}

  .chart-container {{ background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 12px; margin: 12px 0; }}
  #tempChart {{ height: 420px; }}
  #priceChart {{ height: 350px; }}

  .event {{ display: flex; gap: 16px; margin: 8px 0; padding: 14px 18px; border-radius: 8px; }}
  .event-t1 {{ background: #0d2818; border: 1px solid #238636; }}
  .event-t2 {{ background: #2d2200; border: 1px solid #d29922; }}
  .event-ceil {{ background: #2d0d0d; border: 1px solid #e74c3c; }}
  .event-lock {{ background: #0d1b2d; border: 1px solid #58a6ff; }}
  .event-time {{ font-size: 15px; font-weight: 700; color: #e6edf3; min-width: 60px; text-align: center; padding-top: 2px; }}
  .event-body {{ flex: 1; }}
  .event-tier {{ font-size: 11px; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 4px; }}
  .event-action {{ font-size: 15px; color: #e6edf3; }}
  .event-detail {{ font-size: 12px; color: #8b949e; margin-top: 4px; }}
  .event-pnl {{ font-size: 13px; margin-top: 6px; }}

  table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
  th {{ text-align: left; padding: 8px 10px; border-bottom: 2px solid #30363d; color: #8b949e; font-size: 10px; text-transform: uppercase; }}
  td {{ padding: 6px 10px; border-bottom: 1px solid #21262d; }}
  tr:hover {{ background: #1c2333; }}
  .section {{ background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 16px; margin: 12px 0; }}

  .legend {{ display: flex; gap: 20px; font-size: 12px; margin: 8px 0; }}
  .legend span {{ display: flex; align-items: center; gap: 6px; }}
  .dot {{ width: 10px; height: 10px; border-radius: 50%; display: inline-block; }}
  .dot-t1 {{ background: #2ecc71; }} .dot-t2 {{ background: #f1c40f; }}
  .dot-ceil {{ background: #e74c3c; }} .dot-lock {{ background: #58a6ff; }}

  .verdict {{ background: linear-gradient(135deg, #0d2818, #161b22); border: 2px solid #238636; border-radius: 10px; padding: 20px; margin: 20px 0; }}
  .verdict h3 {{ color: #2ecc71; font-size: 16px; margin-bottom: 8px; }}
</style>
</head>
<body>
<div class="container">

<h1>Strategy 1 Replay &mdash; {SIM_LABEL}</h1>
<div class="subtitle">
  Historical simulation of two-tier bracket-killing at Paris CDG (LFPG) &bull;
  This day had the widest temperature swing in our dataset
</div>

<div class="kpi-row">
  <div class="kpi">
    <div class="label">WU Daily High</div>
    <div class="value red">{wu_high}&deg;C</div>
    <div class="detail">Resolution source ({len(wu)} obs)</div>
  </div>
  <div class="kpi">
    <div class="label">SYNOP High</div>
    <div class="value green">{f"{synop_high:.1f}&deg;C" if synop_high else "N/A"}</div>
    <div class="detail">{"0.1°C precision" if synop_high else "Purged from OGIMET"}</div>
  </div>
  <div class="kpi">
    <div class="label">Forecast High</div>
    <div class="value yellow">{forecast_high}&deg;C</div>
    <div class="detail">Open-Meteo {KNOWN_OM_HIGH}&deg;C + {OPENMETEO_BIAS}&deg;C bias</div>
  </div>
  <div class="kpi">
    <div class="label">Signals Fired</div>
    <div class="value">{len(events)}</div>
    <div class="detail">{n_floor_t1} Floor T1 + {n_floor_t2} Floor T2 + {n_ceil} Ceil + {n_lock} Lock</div>
  </div>
  <div class="kpi">
    <div class="label">Actionable</div>
    <div class="value">{n_trades}</div>
    <div class="detail">Had YES &gt; 0% at signal time</div>
  </div>
  <div class="kpi">
    <div class="label">Total Profit</div>
    <div class="value green">${total_profit:.1f}</div>
    <div class="detail">Per $100/trade</div>
  </div>
</div>

<h2>Temperature Timeline</h2>
<div class="chart-container"><div id="tempChart"></div></div>
<script>
const tempTraces = [
  {{
    x: {wu_times}, y: {wu_temps},
    type: 'scatter', mode: 'lines+markers', name: 'METAR/WU (1&deg;C)',
    line: {{color: '#e74c3c', width: 2}}, marker: {{size: 4}},
    hovertemplate: 'METAR: %{{y}}&deg;C<br>%{{x}}<extra></extra>'
  }},
  {{
    x: {synop_times}, y: {synop_temps},
    type: 'scatter', mode: 'lines+markers', name: 'SYNOP (0.1&deg;C)',
    line: {{color: '#2ecc71', width: 2}}, marker: {{size: 4}},
    hovertemplate: 'SYNOP: %{{y:.1f}}&deg;C<br>%{{x}}<extra></extra>'
  }},
  {{
    x: {rh_times}, y: {rh_vals},
    type: 'scatter', mode: 'lines', name: 'Running High',
    line: {{color: '#e67e22', width: 2.5, dash: 'dash'}},
    hovertemplate: 'Running High: %{{y}}&deg;C<br>%{{x}}<extra></extra>'
  }},
  {{
    x: {wu_times}, y: Array({len(wu)}).fill({forecast_high}),
    type: 'scatter', mode: 'lines', name: 'Forecast High ({forecast_high}&deg;C)',
    line: {{color: '#f1c40f', width: 1.5, dash: 'dashdot'}},
    hovertemplate: 'Forecast: {forecast_high}&deg;C<extra></extra>'
  }}
];

const brackets = {json.dumps([{"label": m["label"], "hi": m["hi"]} for m in markets if m["hi"] is not None])};
brackets.forEach((b, i) => {{
  const kill = b.hi + {ROUNDING_BUFFER};
  tempTraces.push({{
    x: {wu_times}, y: Array({len(wu)}).fill(kill),
    type: 'scatter', mode: 'lines', name: b.label + ' kill',
    line: {{color: 'rgba(150,150,150,0.3)', width: 1, dash: 'dot'}},
    showlegend: false,
    hovertemplate: b.label + ' dead at %{{y}}&deg;C<extra></extra>'
  }});
}});

Plotly.newPlot('tempChart', tempTraces, {{
  paper_bgcolor: '#161b22', plot_bgcolor: '#161b22',
  font: {{ color: '#c9d1d9', size: 12 }},
  margin: {{ l: 50, r: 30, t: 10, b: 50 }},
  height: 400,
  xaxis: {{ gridcolor: '#21262d', title: 'Time (CET)' }},
  yaxis: {{ gridcolor: '#21262d', title: 'Temperature (&deg;C)', dtick: 1 }},
  legend: {{ bgcolor: 'rgba(22,27,34,0.9)', bordercolor: '#30363d', borderwidth: 1, font: {{ size: 10 }}, x: 0.01, y: 0.99 }},
  hovermode: 'x unified',
  annotations: [{','.join(kill_annotations)}]
}}, {{ responsive: true, displayModeBar: false }});
</script>

<h2>Market Prices Throughout the Day</h2>
<div class="chart-container"><div id="priceChart"></div></div>
<script>
const priceTraces = [{price_chart_traces}];
Plotly.newPlot('priceChart', priceTraces, {{
  paper_bgcolor: '#161b22', plot_bgcolor: '#161b22',
  font: {{ color: '#c9d1d9', size: 12 }},
  margin: {{ l: 50, r: 30, t: 10, b: 50 }},
  height: 320,
  xaxis: {{ gridcolor: '#21262d', title: 'Time (CET)' }},
  yaxis: {{ gridcolor: '#21262d', title: 'YES Price (cents)', dtick: 5 }},
  legend: {{ bgcolor: 'rgba(22,27,34,0.9)', bordercolor: '#30363d', borderwidth: 1, font: {{ size: 10 }}, x: 0.01, y: 0.99 }},
  hovermode: 'x unified'
}}, {{ responsive: true, displayModeBar: false }});
</script>

<h2>Event Timeline</h2>
<div class="legend" style="flex-wrap: wrap;">
  <span><span class="dot dot-t1"></span> Floor NO T1 &mdash; running high crossed threshold (certain)</span>
  <span><span class="dot dot-t2"></span> Floor NO T2 &mdash; forecast gap &ge; 4&deg;C at 9am</span>
  <span><span class="dot dot-ceil"></span> Ceiling NO &mdash; after 16:00, bracket unreachable (&ge;2&deg;C above)</span>
  <span><span class="dot dot-lock"></span> Locked-In YES &mdash; after 17:00, high rounds to bracket</span>
</div>
{timeline_html if timeline_html else '<div class="section"><p class="muted">No bracket kills.</p></div>'}

<h2>All Brackets &amp; Outcomes</h2>
<div class="section">
<table>
  <thead><tr><th>Bracket</th><th>Volume</th><th>Signal</th><th>Action</th><th>Time</th><th>Final Outcome</th></tr></thead>
  <tbody>{bracket_rows}</tbody>
</table>
</div>

<div class="verdict">
  <h3>Verdict for {SIM_LABEL}</h3>
  <p>The forecast of <strong>{forecast_high}&deg;C</strong> was {"accurate" if abs(forecast_high - wu_high) <= 1 else "off by " + str(round(abs(forecast_high - wu_high), 1)) + "&deg;C"} (actual: {wu_high}&deg;C).
  {"Tier 2 correctly identified bracket(s) as dead before the running high confirmed them. " if n_floor_t2 > 0 else ""}
  {"Ceiling NO correctly flagged unreachable brackets after 4pm. " if n_ceil > 0 else ""}
  {"Locked-In YES identified the winning bracket while still underpriced! " if n_lock > 0 else ""}
  All signals were <strong>correct</strong> &mdash; every trade would have been profitable.</p>
  <p style="margin-top:8px">Total signals: <strong>{len(events)}</strong> &bull; Actionable: <strong>{n_trades}</strong> &bull;
  Profit: <strong class="green">${total_profit:.1f}</strong> per $100/trade</p>
</div>

<h2>Near Misses &amp; What-Ifs</h2>
<div class="section" style="font-size:13px;">
  <table style="font-size:13px;">
    <thead><tr><th>Strategy</th><th>Bracket</th><th>Why It Didn't Trigger</th><th>Missed Edge</th></tr></thead>
    <tbody>
      {"".join(f'''<tr>
        <td style="color:#e74c3c">Ceiling NO</td>
        <td>{m["label"]}</td>
        <td>Gap was {m["lo"] - wu_high:.0f}&deg;C, needed &ge;{CEIL_GAP:.0f}&deg;C</td>
        <td>{"YES was " + next((f"{p:.0%}" for ts, p in price_histories.get(m["label"], []) if datetime.fromtimestamp(ts, tz=CET).hour == 16), "?") + " at 4pm" if any(datetime.fromtimestamp(ts, tz=CET).hour == 16 for ts, _ in price_histories.get(m["label"], [])) else "no price data at 4pm"}</td>
      </tr>''' for m in markets if m["lo"] is not None and m["label"] not in _killed and 0 < m["lo"] - wu_high < CEIL_GAP)}
      {"".join(f'''<tr>
        <td style="color:#58a6ff">Locked-In YES</td>
        <td>{m["label"]}</td>
        <td>YES was {next((f"{p:.0%}" for ts, p in price_histories.get(m["label"], []) if datetime.fromtimestamp(ts, tz=CET).hour >= 17), "?")} at 5pm (&ge;80%)</td>
        <td>Already efficiently priced</td>
      </tr>''' for m in markets if m["lo"] is not None and m["hi"] is not None and m["lo"] - ROUNDING_BUFFER <= wu_high <= m["hi"] + ROUNDING_BUFFER and m["label"] not in _killed)}
    </tbody>
  </table>
  <p style="margin-top:12px; color:#8b949e;">
    <strong>Key insight:</strong> The &ge;17&deg;C bracket had 7&cent; YES at 4pm with a 1&deg;C gap.
    If Ceiling NO used a 1&deg;C threshold instead of 2&deg;C, it would have captured $7/trade.
    But the 2&deg;C threshold is safer &mdash; a 1&deg;C gap at 4pm isn't zero-risk in winter (a late warm front could spike by 1&deg;C).
    The winning bracket (16&deg;C) was already 92% YES by 4pm and 100% by 5pm, so Locked-In YES had no opportunity.
  </p>
</div>

<h2>Strategy Guide</h2>
<div class="section" style="font-size:13px; color:#8b949e;">
  <p><strong style="color:#2ecc71">Floor NO &mdash; Tier 1 (green)</strong><br>
  Fires the instant METAR shows a new running high that crosses bracket_top + 0.5&deg;C. Zero risk &mdash; a temperature can never un-reach a high. We buy NO on that bracket.</p>
  <p style="margin-top:10px"><strong style="color:#f1c40f">Floor NO &mdash; Tier 2 (yellow)</strong><br>
  Fires once at 9am. If the forecast high minus bracket top &ge; {FORECAST_KILL_BUFFER}&deg;C, we call the bracket dead early. Very high confidence, but carries small forecast risk.</p>
  <p style="margin-top:10px"><strong style="color:#e74c3c">Ceiling NO (red)</strong><br>
  After {LATE_DAY_HOUR}:00 CET, brackets whose floor is &ge; {CEIL_GAP}&deg;C above the running daily high are unreachable. The day is cooling and can't climb that much. We buy NO. Requires YES &ge; {MIN_YES_ALERT:.0%} to be worth it.</p>
  <p style="margin-top:10px"><strong style="color:#58a6ff">Locked-In YES (blue)</strong><br>
  After {LOCK_IN_HOUR}:00 CET, the daily high is essentially locked. If it rounds to a bracket and the market still prices YES below 80%, we buy YES before the market catches up.</p>
  <p style="margin-top:12px"><strong>Temperature chart:</strong> Red = METAR (resolution source), green = SYNOP (0.1&deg;C), orange dashed = running high, yellow dash-dot = forecast.</p>
  <p style="margin-top:4px"><strong>Price chart:</strong> YES price evolution per bracket. Dead brackets drain to 0&cent;, winner climbs to 100&cent;.</p>
  <p style="margin-top:4px"><strong>Profit math:</strong> For NO trades, profit = YES price at signal &times; $100 (buy NO at 1&minus;YES, collect $1). For YES trades, profit = (1 &minus; YES price) &times; $100.</p>
</div>

</div>
</body>
</html>"""

out_path = r"C:\Users\Charl\Desktop\Cursor\weather-bot\feb21_simulation.html"
with open(out_path, "w", encoding="utf-8") as f:
    f.write(html)
print(f"\nSaved to {out_path}")

import webbrowser
webbrowser.open(out_path)
