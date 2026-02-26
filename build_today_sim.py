"""
Build an HTML simulation of what Strategy 1 (Tier 1 + Tier 2) would have done today.
Fetches actual temperature timeseries and market price history for Feb 22.
"""
import urllib.request, json, re, sys, time
from datetime import datetime, date, timezone, timedelta
from zoneinfo import ZoneInfo

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

CET = ZoneInfo("Europe/Paris")
TODAY = date(2026, 2, 22)
TODAY_STR = TODAY.isoformat()

CDG_LAT, CDG_LON = 49.0097, 2.5479
ROUNDING_BUFFER = 0.5
FORECAST_KILL_BUFFER = 4.0
OPENMETEO_BIAS = 1.0


# ── Fetch data ───────────────────────────────────────────────────────────

def fetch_wu_timeseries():
    date_str = TODAY.strftime("%Y%m%d")
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
    begin = TODAY.strftime("%Y%m%d") + "0000"
    end = (TODAY + timedelta(days=1)).strftime("%Y%m%d") + "0000"
    url = f"https://www.ogimet.com/cgi-bin/getsynop?block=07157&begin={begin}&end={end}"
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


def fetch_openmeteo_forecast_high():
    # Try forecast endpoint (works for today and future)
    url = (f"https://api.open-meteo.com/v1/forecast?"
           f"latitude={CDG_LAT}&longitude={CDG_LON}"
           f"&daily=temperature_2m_max&timezone=Europe/Paris&forecast_days=2")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read())
        dates = data.get("daily", {}).get("time", [])
        maxes = data.get("daily", {}).get("temperature_2m_max", [])
        for d, mx in zip(dates, maxes):
            if d == TODAY_STR and mx is not None:
                return round(float(mx) + OPENMETEO_BIAS, 1)
    except Exception as e:
        print(f"  Open-Meteo forecast failed: {e}")
    # Fallback: try hourly archive for today
    try:
        url2 = (f"https://api.open-meteo.com/v1/forecast?"
                f"latitude={CDG_LAT}&longitude={CDG_LON}"
                f"&hourly=temperature_2m&timezone=Europe/Paris"
                f"&start_date={TODAY_STR}&end_date={TODAY_STR}")
        req = urllib.request.Request(url2, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read())
        temps = data.get("hourly", {}).get("temperature_2m", [])
        valid = [t for t in temps if t is not None]
        if valid:
            return round(max(valid) + OPENMETEO_BIAS, 1)
    except Exception as e:
        print(f"  Open-Meteo hourly fallback failed: {e}")
    return None


def fetch_market_data():
    slug = f"highest-temperature-in-paris-on-february-22-2026"
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

        # Parse range
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
        })
    markets.sort(key=lambda m: m["hi"] if m["hi"] is not None else 999)
    return markets


def fetch_price_history(token_id):
    start_ts = int(datetime(TODAY.year, TODAY.month, TODAY.day, tzinfo=timezone.utc).timestamp())
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


# ── Simulation ───────────────────────────────────────────────────────────

print("Fetching data...", flush=True)
wu = fetch_wu_timeseries()
print(f"  WU: {len(wu)} readings, high={max(p['temp_c'] for p in wu)}°C")

synop = fetch_synop_timeseries()
print(f"  SYNOP: {len(synop)} readings, high={max(p['temp_c'] for p in synop):.1f}°C")

forecast_high = fetch_openmeteo_forecast_high()
print(f"  Forecast high: {forecast_high}°C" if forecast_high else "  Forecast high: unavailable")

markets = fetch_market_data()
print(f"  Markets: {len(markets)} brackets")

# Fetch price histories
price_histories = {}
for m in markets:
    if m["yes_token"]:
        ph = fetch_price_history(m["yes_token"])
        price_histories[m["label"]] = ph
        time.sleep(0.1)
print(f"  Price histories: {sum(len(v) for v in price_histories.values())} points")

# Simulate running high and bracket kills over the day
events = []
running_high = None

for obs in wu:
    temp = obs["temp_c"]
    hour = obs["hour"]
    time_str = obs["time_cet"]

    if running_high is None or temp > running_high:
        old_high = running_high
        running_high = temp

        # Check which brackets this kills
        for m in markets:
            hi = m["hi"]
            if hi is None:
                continue
            was_dead = old_high is not None and old_high >= hi + ROUNDING_BUFFER
            now_dead = running_high >= hi + ROUNDING_BUFFER
            if now_dead and not was_dead:
                # Find YES price at this time
                ph = price_histories.get(m["label"], [])
                yes_at_time = None
                for ts, p in ph:
                    dt = datetime.fromtimestamp(ts, tz=CET)
                    if dt.hour + dt.minute/60 <= hour + 0.5:
                        yes_at_time = p
                events.append({
                    "time": time_str, "hour": hour, "type": "TIER1_KILL",
                    "bracket": m["label"], "bracket_hi": hi,
                    "running_high": running_high,
                    "yes_at_kill": yes_at_time,
                    "profit_per_100": round((yes_at_time or 0) * 100, 1),
                })

# Tier 2 kills at 9am
if forecast_high:
    t2_kills = []
    rh_at_9 = None
    for obs in wu:
        if obs["hour"] <= 9:
            if rh_at_9 is None or obs["temp_c"] > rh_at_9:
                rh_at_9 = obs["temp_c"]

    for m in markets:
        hi = m["hi"]
        if hi is None:
            continue
        already_t1 = rh_at_9 is not None and rh_at_9 >= hi + ROUNDING_BUFFER
        if not already_t1 and forecast_high - hi >= FORECAST_KILL_BUFFER:
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

events.sort(key=lambda e: e["hour"])

# Compute total P&L
total_profit = sum(e["profit_per_100"] for e in events if e["profit_per_100"] > 0)
n_trades = sum(1 for e in events if e["profit_per_100"] > 0)

print(f"\nSimulated {len(events)} events, {n_trades} actionable trades")
print(f"Total theoretical profit: ${total_profit:.1f} per $100/trade")


# ── Build HTML ───────────────────────────────────────────────────────────

# Temperature chart traces
wu_times = json.dumps([p["time_cet"] for p in wu])
wu_temps = json.dumps([p["temp_c"] for p in wu])
synop_times = json.dumps([p["time_cet"] for p in synop])
synop_temps = json.dumps([p["temp_c"] for p in synop])

# Running high line
rh_points = []
rh = None
for obs in wu:
    if rh is None or obs["temp_c"] > rh:
        rh = obs["temp_c"]
    rh_points.append({"time": obs["time_cet"], "rh": rh})
rh_times = json.dumps([p["time"] for p in rh_points])
rh_vals = json.dumps([p["rh"] for p in rh_points])

# Kill markers
kill_annotations = []
for e in events:
    color = "#2ecc71" if e["type"] == "TIER1_KILL" else "#f1c40f"
    tier = "T1" if e["type"] == "TIER1_KILL" else "T2"
    kill_annotations.append(f"""{{
        x: '{e["time"]}', y: {e.get("running_high", e.get("forecast_high", 0))},
        xref: 'x', yref: 'y',
        text: '{tier}: {e["bracket"]} DEAD',
        showarrow: true, arrowhead: 2, arrowcolor: '{color}',
        font: {{ color: '{color}', size: 10 }},
        bgcolor: 'rgba(13,17,23,0.8)', bordercolor: '{color}',
        ax: 0, ay: -30
    }}""")

# Timeline events HTML
timeline_html = ""
for e in events:
    if e["type"] == "TIER1_KILL":
        icon = "🟢"
        tier = "TIER 1 — CERTAIN"
        detail = f"Running high reached {e['running_high']}°C, exceeding {e['bracket']} (need > {e['bracket_hi'] + ROUNDING_BUFFER}°C)"
    else:
        icon = "🟡"
        tier = "TIER 2 — FORECAST"
        detail = f"Forecast {e['forecast_high']}°C is {e['gap']}°C above bracket top (buffer: {FORECAST_KILL_BUFFER}°C)"

    profit_str = f"${e['profit_per_100']:.1f}" if e['profit_per_100'] > 0 else "no edge (already repriced)"
    yes_str = f"{e['yes_at_kill']:.0%}" if e['yes_at_kill'] else "?"

    timeline_html += f"""
    <div class="event {'event-t1' if 'TIER1' in e['type'] else 'event-t2'}">
      <div class="event-time">{e['time']} CET</div>
      <div class="event-body">
        <div class="event-tier">{icon} {tier}</div>
        <div class="event-action">Buy NO on <strong>{e['bracket']}</strong> — bracket is dead</div>
        <div class="event-detail">{detail}</div>
        <div class="event-detail">YES price at kill: {yes_str} → Profit per $100: <span class="{'green' if e['profit_per_100'] > 0 else 'muted'}">{profit_str}</span></div>
      </div>
    </div>"""

# Bracket summary table
bracket_rows = ""
wu_high = max(p["temp_c"] for p in wu)
synop_high = max(p["temp_c"] for p in synop)
for m in markets:
    hi = m["hi"]
    lo = m["lo"]
    label = m["label"]
    yes = m["yes_price"]
    vol = m["volume"]

    killed_by = "-"
    kill_time = "-"
    for e in events:
        if e["bracket"] == label:
            killed_by = "Tier 1" if "TIER1" in e["type"] else "Tier 2"
            kill_time = e["time"]
            break

    if m["closed"]:
        status = "CLOSED (resolved)"
    elif killed_by != "-":
        status = f"DEAD at {kill_time}"
    elif hi is not None and lo is not None and lo - ROUNDING_BUFFER <= wu_high <= hi + ROUNDING_BUFFER:
        status = "CURRENT BRACKET"
    else:
        status = "alive"

    status_cls = "green" if "DEAD" in status else ("winner" if "CURRENT" in status else "muted")

    bracket_rows += f"""<tr>
        <td>{label}</td>
        <td>{yes:.0%}</td>
        <td>${vol:,.0f}</td>
        <td class="{status_cls}">{status}</td>
        <td>{killed_by}</td>
    </tr>"""

now_cet = datetime.now(timezone.utc).astimezone(CET)

html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Today's Strategy 1 Simulation — Feb 22, 2026</title>
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
  .kpi {{ background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 14px 18px; flex: 1; min-width: 150px; }}
  .kpi .label {{ font-size: 10px; text-transform: uppercase; letter-spacing: 0.5px; color: #8b949e; }}
  .kpi .value {{ font-size: 24px; font-weight: 700; margin-top: 4px; }}
  .kpi .detail {{ font-size: 11px; color: #8b949e; margin-top: 2px; }}

  #chart {{ background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 12px; height: 420px; }}

  .event {{ display: flex; gap: 16px; margin: 8px 0; padding: 12px 16px; border-radius: 8px; }}
  .event-t1 {{ background: #0d2818; border: 1px solid #238636; }}
  .event-t2 {{ background: #2d2200; border: 1px solid #d29922; }}
  .event-time {{ font-size: 14px; font-weight: 700; color: #e6edf3; min-width: 80px; padding-top: 2px; }}
  .event-body {{ flex: 1; }}
  .event-tier {{ font-size: 11px; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 4px; }}
  .event-action {{ font-size: 14px; color: #e6edf3; }}
  .event-detail {{ font-size: 12px; color: #8b949e; margin-top: 2px; }}

  table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
  th {{ text-align: left; padding: 8px 10px; border-bottom: 2px solid #30363d; color: #8b949e; font-size: 10px; text-transform: uppercase; }}
  td {{ padding: 6px 10px; border-bottom: 1px solid #21262d; }}
  tr:hover {{ background: #1c2333; }}
  .section {{ background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 16px; margin: 12px 0; }}

  .legend {{ display: flex; gap: 20px; font-size: 12px; margin: 8px 0; }}
  .legend span {{ display: flex; align-items: center; gap: 6px; }}
  .dot {{ width: 10px; height: 10px; border-radius: 50%; display: inline-block; }}
  .dot-t1 {{ background: #2ecc71; }}
  .dot-t2 {{ background: #f1c40f; }}
</style>
</head>
<body>
<div class="container">

<h1>Strategy 1 Simulation — February 22, 2026</h1>
<div class="subtitle">
  What our two-tier bracket-killing strategy would have done today at Paris CDG (LFPG) |
  Generated {now_cet.strftime('%H:%M CET')}
</div>

<div class="kpi-row">
  <div class="kpi">
    <div class="label">WU Daily High</div>
    <div class="value red">{wu_high}°C</div>
    <div class="detail">Resolution source ({len(wu)} readings)</div>
  </div>
  <div class="kpi">
    <div class="label">SYNOP Daily High</div>
    <div class="value green">{synop_high:.1f}°C</div>
    <div class="detail">0.1°C precision (same station)</div>
  </div>
  <div class="kpi">
    <div class="label">Forecast High</div>
    <div class="value yellow">{f"{forecast_high}°C" if forecast_high else "N/A"}</div>
    <div class="detail">Open-Meteo +{OPENMETEO_BIAS}°C correction</div>
  </div>
  <div class="kpi">
    <div class="label">Trades Triggered</div>
    <div class="value">{len(events)}</div>
    <div class="detail">{sum(1 for e in events if 'TIER1' in e['type'])} Tier 1 + {sum(1 for e in events if 'TIER2' in e['type'])} Tier 2</div>
  </div>
  <div class="kpi">
    <div class="label">Total Profit</div>
    <div class="value green">${total_profit:.1f}</div>
    <div class="detail">Per $100 per trade</div>
  </div>
</div>

<h2>Temperature + Running High</h2>
<div id="chart"></div>
<script>
const traces = [
  {{
    x: {wu_times}, y: {wu_temps},
    type: 'scatter', mode: 'lines+markers', name: 'METAR/WU (1°C)',
    line: {{color: '#e74c3c', width: 2}}, marker: {{size: 4}},
    hovertemplate: 'METAR: %{{y}}°C<br>%{{x}}<extra></extra>'
  }},
  {{
    x: {synop_times}, y: {synop_temps},
    type: 'scatter', mode: 'lines+markers', name: 'SYNOP (0.1°C)',
    line: {{color: '#2ecc71', width: 2}}, marker: {{size: 4}},
    hovertemplate: 'SYNOP: %{{y:.1f}}°C<br>%{{x}}<extra></extra>'
  }},
  {{
    x: {rh_times}, y: {rh_vals},
    type: 'scatter', mode: 'lines', name: 'Running High',
    line: {{color: '#e67e22', width: 2.5, dash: 'dash'}},
    hovertemplate: 'Running High: %{{y}}°C<br>%{{x}}<extra></extra>'
  }}
];

// Add bracket lines
const brackets = {json.dumps([{"label": m["label"], "hi": m["hi"]} for m in markets if m["hi"] is not None])};
brackets.forEach((b, i) => {{
  const killThreshold = b.hi + {ROUNDING_BUFFER};
  traces.push({{
    x: {wu_times}, y: Array({len(wu)}).fill(killThreshold),
    type: 'scatter', mode: 'lines', name: b.label + ' kill line',
    line: {{color: 'rgba(150,150,150,0.25)', width: 1, dash: 'dot'}},
    showlegend: i === 0,
    hovertemplate: b.label + ' dies at %{{y}}°C<extra></extra>'
  }});
}});

const layout = {{
  paper_bgcolor: '#161b22', plot_bgcolor: '#161b22',
  font: {{ color: '#c9d1d9', size: 12 }},
  margin: {{ l: 50, r: 30, t: 20, b: 50 }},
  height: 400,
  xaxis: {{ gridcolor: '#21262d', title: 'Time (CET)' }},
  yaxis: {{ gridcolor: '#21262d', title: 'Temperature (°C)', dtick: 1 }},
  legend: {{ bgcolor: 'rgba(22,27,34,0.9)', bordercolor: '#30363d', borderwidth: 1, font: {{ size: 10 }}, x: 0.01, y: 0.99 }},
  hovermode: 'x unified',
  annotations: [{','.join(kill_annotations)}]
}};
Plotly.newPlot('chart', traces, layout, {{ responsive: true, displayModeBar: false }});
</script>

<h2>Event Timeline</h2>
<div class="legend">
  <span><span class="dot dot-t1"></span> Tier 1 — Running high passed bracket (certain)</span>
  <span><span class="dot dot-t2"></span> Tier 2 — Forecast says bracket dead (4°C buffer)</span>
</div>

{timeline_html if timeline_html else '<div class="section"><p class="muted">No bracket kills today — all open brackets are near the daily high.</p></div>'}

<h2>Bracket Status</h2>
<div class="section">
<table>
  <thead><tr><th>Bracket</th><th>Current YES</th><th>Volume</th><th>Status</th><th>Killed By</th></tr></thead>
  <tbody>{bracket_rows}</tbody>
</table>
</div>

<h2>How to Read This</h2>
<div class="section" style="font-size:13px; color:#8b949e;">
  <p><strong>The orange dashed line</strong> is the running daily high — it can only go up. Each time it crosses a bracket's kill threshold (dotted gray lines at bracket_top + 0.5°C), that bracket is permanently dead and we buy NO.</p>
  <p style="margin-top:8px"><strong>Tier 1 (green)</strong> fires the instant METAR shows a new high that crosses a threshold. Zero risk — temperature can never un-reach a high.</p>
  <p style="margin-top:8px"><strong>Tier 2 (yellow)</strong> fires at 9am based on the forecast. If the forecast high minus the bracket top is >= {FORECAST_KILL_BUFFER}°C, we call the bracket dead early. Very high confidence but not 100%.</p>
  <p style="margin-top:8px"><strong>Profit</strong> = the YES price at the time of the kill. If YES is 5%, buying NO at $0.95 means you collect $1.00 at resolution — $5 profit per $100. If YES is already 0%, there's no edge (market already repriced).</p>
</div>

</div>
</body>
</html>"""

out_path = r"C:\Users\Charl\Desktop\Cursor\weather-bot\today_simulation.html"
with open(out_path, "w", encoding="utf-8") as f:
    f.write(html)
print(f"\nSaved to {out_path}")

import webbrowser
webbrowser.open(out_path)
