"""Fetch today's temperatures from METAR, SYNOP, and Open-Meteo and build an HTML chart."""
import urllib.request, json, re, sys
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

CET = ZoneInfo("Europe/Paris")
CDG_LAT, CDG_LON = 49.0097, 2.5479
today_utc = datetime.now(timezone.utc).date()
today_str = today_utc.strftime("%Y-%m-%d")


def fetch_metar_history():
    """METAR 12-hour history from aviationweather.gov."""
    url = (
        "https://aviationweather.gov/api/data/metar"
        "?ids=LFPG&format=json&hours=18"
    )
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=15) as r:
        data = json.loads(r.read())

    pts = []
    for obs in data:
        temp = obs.get("temp")
        obs_time = obs.get("obsTime") or obs.get("reportTime")
        if temp is None or not obs_time:
            continue
        # obsTime is epoch seconds
        dt = datetime.fromtimestamp(int(obs_time), tz=timezone.utc).astimezone(CET)
        if dt.date() == today_utc:
            pts.append((dt.isoformat(), round(float(temp), 1)))
    pts.sort()
    return pts


def fetch_synop_history():
    """SYNOP hourly data from OGIMET for today."""
    begin = today_utc.strftime("%Y%m%d") + "0000"
    url = f"https://www.ogimet.com/cgi-bin/getsynop?block=07157&begin={begin}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=15) as r:
        text = r.read().decode("utf-8", errors="replace")

    pts = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or not line.startswith("07157"):
            continue
        parts = line.split(",")
        if len(parts) < 6:
            continue
        year, month, day, hour, minute = (
            int(parts[1]), int(parts[2]), int(parts[3]),
            int(parts[4]), int(parts[5]),
        )
        dt_utc = datetime(year, month, day, hour, minute, tzinfo=timezone.utc)
        dt_cet = dt_utc.astimezone(CET)
        if dt_cet.date() != today_utc:
            continue

        m = re.search(r'\b1([01])(\d{3})\b', line)
        if not m:
            continue
        sign = 1 if m.group(1) == "0" else -1
        temp = sign * int(m.group(2)) / 10.0
        pts.append((dt_cet.isoformat(), temp))
    pts.sort()
    return pts


def fetch_openmeteo_history():
    """Open-Meteo 15-minute data for today."""
    url = (
        f"https://api.open-meteo.com/v1/forecast?"
        f"latitude={CDG_LAT}&longitude={CDG_LON}"
        f"&minutely_15=temperature_2m"
        f"&past_minutely_15=96&forecast_minutely_15=0"
        f"&timezone=Europe/Paris"
    )
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=15) as r:
        data = json.loads(r.read())

    m15 = data.get("minutely_15", {})
    times = m15.get("time", [])
    temps = m15.get("temperature_2m", [])

    pts = []
    for t, temp in zip(times, temps):
        if temp is None:
            continue
        if t.startswith(today_str):
            dt_cet = datetime.fromisoformat(t).replace(tzinfo=CET)
            pts.append((dt_cet.isoformat(), temp))
    pts.sort()
    return pts


def build_html(metar, synop, openmeteo):
    now_cet = datetime.now(timezone.utc).astimezone(CET)

    def to_js_data(pts, label, color, dash="false"):
        if not pts:
            return ""
        xs = ", ".join(f"'{t}'" for t, _ in pts)
        ys = ", ".join(f"{v}" for _, v in pts)
        return f"""{{
            x: [{xs}],
            y: [{ys}],
            type: 'scatter',
            mode: 'lines+markers',
            name: '{label}',
            line: {{color: '{color}', width: 2.5, dash: {dash}}},
            marker: {{size: 5}},
            hovertemplate: '%{{y:.1f}}°C<br>%{{x|%H:%M}}<extra>{label}</extra>'
        }}"""

    metar_trace = to_js_data(metar, "METAR (1°C, primary)", "#e74c3c")
    synop_trace = to_js_data(synop, "SYNOP (0.1°C, same station)", "#2ecc71")
    om_trace = to_js_data(openmeteo, "Open-Meteo (0.1°C, model)", "#3498db", "'dot'")

    all_temps = [v for _, v in metar + synop + openmeteo]
    y_min = min(all_temps) - 1 if all_temps else 0
    y_max = max(all_temps) + 1 if all_temps else 20

    # Generate integer tick marks for bracket boundaries
    bracket_min = int(y_min)
    bracket_max = int(y_max) + 1
    shapes = []
    for deg in range(bracket_min, bracket_max + 1):
        shapes.append(f"""{{
            type: 'line', xref: 'paper', x0: 0, x1: 1,
            y0: {deg}, y1: {deg},
            line: {{color: 'rgba(150,150,150,0.3)', width: 1, dash: 'dot'}}
        }}""")

    current_high = max(all_temps) if all_temps else 0

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Paris CDG Temperature — {today_str}</title>
<script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{
    font-family: 'Segoe UI', system-ui, -apple-system, sans-serif;
    background: #0d1117;
    color: #c9d1d9;
    min-height: 100vh;
    padding: 24px;
  }}
  .header {{
    max-width: 1100px;
    margin: 0 auto 16px;
  }}
  .header h1 {{
    font-size: 22px;
    font-weight: 600;
    color: #e6edf3;
  }}
  .header .subtitle {{
    font-size: 13px;
    color: #8b949e;
    margin-top: 4px;
  }}
  .stats {{
    display: flex;
    gap: 24px;
    margin: 16px auto;
    max-width: 1100px;
  }}
  .stat-card {{
    background: #161b22;
    border: 1px solid #30363d;
    border-radius: 8px;
    padding: 14px 20px;
    flex: 1;
  }}
  .stat-card .label {{
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.5px;
    color: #8b949e;
  }}
  .stat-card .value {{
    font-size: 28px;
    font-weight: 700;
    margin-top: 4px;
  }}
  .stat-card .detail {{
    font-size: 12px;
    color: #8b949e;
    margin-top: 2px;
  }}
  .metar-val {{ color: #e74c3c; }}
  .synop-val {{ color: #2ecc71; }}
  .om-val {{ color: #3498db; }}
  #chart {{
    max-width: 1100px;
    height: 500px;
    margin: 0 auto;
    background: #161b22;
    border: 1px solid #30363d;
    border-radius: 8px;
    padding: 12px;
  }}
  .footer {{
    max-width: 1100px;
    margin: 16px auto 0;
    font-size: 11px;
    color: #484f58;
    text-align: center;
  }}
</style>
</head>
<body>

<div class="header">
  <h1>Paris CDG (LFPG) — Temperature Tracker</h1>
  <div class="subtitle">
    {today_str} &middot; Polymarket resolution source: Weather Underground / LFPG
  </div>
</div>

<div class="stats">
  <div class="stat-card">
    <div class="label">METAR (Primary)</div>
    <div class="value metar-val">{metar[-1][1]:.0f}°C</div>
    <div class="detail">1°C precision &middot; {len(metar)} readings today</div>
  </div>
  <div class="stat-card">
    <div class="label">SYNOP (Secondary)</div>
    <div class="value synop-val">{synop[-1][1]:.1f}°C</div>
    <div class="detail">0.1°C precision &middot; Same CDG sensors</div>
  </div>
  <div class="stat-card">
    <div class="label">Open-Meteo (Tertiary)</div>
    <div class="value om-val">{openmeteo[-1][1]:.1f}°C</div>
    <div class="detail">0.1°C model data &middot; Trend indicator</div>
  </div>
  <div class="stat-card">
    <div class="label">Daily High</div>
    <div class="value" style="color:#f0c040">{current_high:.1f}°C</div>
    <div class="detail">From SYNOP (most precise station data)</div>
  </div>
</div>

<div id="chart"></div>

<div class="footer">
  Generated {now_cet.strftime('%Y-%m-%d %H:%M CET')} &middot;
  METAR = aviationweather.gov &middot;
  SYNOP = OGIMET (station 07157) &middot;
  Open-Meteo = model interpolation
</div>

<script>
const traces = [
  {metar_trace},
  {synop_trace},
  {om_trace}
];

const layout = {{
  paper_bgcolor: '#161b22',
  plot_bgcolor: '#161b22',
  font: {{ color: '#c9d1d9', family: 'Segoe UI, system-ui, sans-serif', size: 12 }},
  height: 480,
  margin: {{ l: 55, r: 30, t: 30, b: 50 }},
  xaxis: {{
    type: 'date',
    gridcolor: '#21262d',
    tickformat: '%H:%M',
    title: {{ text: 'Time (CET)', standoff: 10 }},
  }},
  yaxis: {{
    gridcolor: '#21262d',
    title: {{ text: 'Temperature (°C)', standoff: 10 }},
    range: [{y_min}, {y_max}],
    dtick: 1,
  }},
  legend: {{
    bgcolor: 'rgba(22,27,34,0.9)',
    bordercolor: '#30363d',
    borderwidth: 1,
    font: {{ size: 11 }},
    x: 0.01, y: 0.99,
  }},
  shapes: [{', '.join(shapes)}],
  hovermode: 'x unified',
}};

Plotly.newPlot('chart', traces, layout, {{
  responsive: true,
  displayModeBar: true,
  modeBarButtonsToRemove: ['lasso2d', 'select2d'],
}});
</script>
</body>
</html>"""


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    print("Fetching METAR history...", flush=True)
    metar = fetch_metar_history()
    print(f"  {len(metar)} points")

    print("Fetching SYNOP history...", flush=True)
    synop = fetch_synop_history()
    print(f"  {len(synop)} points")

    print("Fetching Open-Meteo history...", flush=True)
    openmeteo = fetch_openmeteo_history()
    print(f"  {len(openmeteo)} points")

    out = r"C:\Users\Charl\Desktop\Cursor\weather-bot\temperature_chart.html"
    html = build_html(metar, synop, openmeteo)
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"\nChart saved to {out}")
    print("Opening in browser...")

    import webbrowser
    webbrowser.open(out)
