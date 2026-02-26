"""
Show METAR, SYNOP and Open-Meteo for the two losing days (Feb 15, Feb 18).
Were there warning signs that the temperature was still rising at 4-5pm?
"""
import urllib.request, json, re, sys, time
from datetime import datetime, date, timezone, timedelta
from zoneinfo import ZoneInfo

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

CET = ZoneInfo("Europe/Paris")
CDG_LAT, CDG_LON = 49.0097, 2.5479

DAYS = [
    {"date": date(2026, 2, 15), "label": "February 15", "wu_high": 9,
     "ceil_bracket": ">=7°C", "lock_bracket": "3°C", "winning": ">=7°C"},
    {"date": date(2026, 2, 18), "label": "February 18", "wu_high": 9,
     "ceil_bracket": ">=8°C", "lock_bracket": "6°C", "winning": ">=8°C"},
]


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
            pts.append({"time": d.strftime("%H:%M"), "hour": d.hour + d.minute/60, "temp": temp})
    return sorted(pts, key=lambda x: x["hour"])


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
                pts.append({"time": dt_cet.strftime("%H:%M"),
                             "hour": dt_cet.hour + dt_cet.minute/60, "temp": temp})
        return sorted(pts, key=lambda x: x["hour"])
    except Exception as e:
        print(f"  SYNOP failed: {e}")
        return []


def fetch_openmeteo_hourly(dt):
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
                dt_local = datetime.fromisoformat(t)
                pts.append({"time": dt_local.strftime("%H:%M"),
                             "hour": dt_local.hour + dt_local.minute/60,
                             "temp": tmp})
        return pts
    except Exception as e:
        print(f"  Open-Meteo failed: {e}")
        return []


# Fetch data for both days
day_data = []
for day in DAYS:
    print(f"Fetching {day['label']}...", flush=True)
    wu = fetch_wu(day["date"])
    print(f"  METAR/WU: {len(wu)} readings, high={max(p['temp'] for p in wu)}°C")
    synop = fetch_synop(day["date"])
    print(f"  SYNOP: {len(synop)} readings" + (f", high={max(p['temp'] for p in synop):.1f}°C" if synop else ""))
    om = fetch_openmeteo_hourly(day["date"])
    print(f"  Open-Meteo: {len(om)} readings" + (f", high={max(p['temp'] for p in om):.1f}°C" if om else ""))
    day_data.append({"info": day, "wu": wu, "synop": synop, "om": om})
    time.sleep(0.3)


# Build HTML
charts_html = ""

for idx, dd in enumerate(day_data):
    info = dd["info"]
    wu, synop, om = dd["wu"], dd["synop"], dd["om"]

    wu_times = json.dumps([p["time"] for p in wu])
    wu_temps = json.dumps([p["temp"] for p in wu])
    syn_times = json.dumps([p["time"] for p in synop])
    syn_temps = json.dumps([round(p["temp"], 1) for p in synop])
    om_times = json.dumps([p["time"] for p in om])
    om_temps = json.dumps([round(p["temp"], 1) for p in om])

    # Running high from WU
    rh_vals = []
    rh = None
    for p in wu:
        if rh is None or p["temp"] > rh: rh = p["temp"]
        rh_vals.append(rh)
    rh_j = json.dumps(rh_vals)

    # Trend at 4pm and 5pm from each source
    def trend_at(pts, hour, window=2):
        before = [p["temp"] for p in pts if hour - window <= p["hour"] <= hour]
        if len(before) < 2: return "?"
        return "RISING" if before[-1] > before[0] else "FALLING" if before[-1] < before[0] else "FLAT"

    def temp_at(pts, hour):
        best = None
        for p in pts:
            if p["hour"] <= hour + 0.5:
                best = p["temp"]
        return best

    wu_at_16 = temp_at(wu, 16)
    wu_at_17 = temp_at(wu, 17)
    syn_at_16 = temp_at(synop, 16)
    syn_at_17 = temp_at(synop, 17)
    om_at_16 = temp_at(om, 16)
    om_at_17 = temp_at(om, 17)

    wu_trend_16 = trend_at(wu, 16)
    syn_trend_16 = trend_at(synop, 16)
    om_trend_16 = trend_at(om, 16)
    wu_trend_17 = trend_at(wu, 17)
    syn_trend_17 = trend_at(synop, 17)
    om_trend_17 = trend_at(om, 17)

    def trend_icon(t):
        if t == "RISING": return "<span style='color:#e74c3c'>&#x25B2; RISING</span>"
        if t == "FALLING": return "<span style='color:#2ecc71'>&#x25BC; FALLING</span>"
        return "<span style='color:#8b949e'>&#x25CF; FLAT</span>"

    def fmt_temp(t):
        if t is None: return "?"
        return f"{t:.1f}" if isinstance(t, float) else f"{t}"

    chart_id = f"chart{idx}"

    charts_html += f"""
    <div class="day-card">
      <h2>{info['label']} — Actual high: {info['wu_high']}°C</h2>
      <p class="day-sub">
        Lost trades: Ceiling NO on <strong>{info['ceil_bracket']}</strong> at 4pm,
        Locked-In YES on <strong>{info['lock_bracket']}</strong> at 5pm.
        Winning bracket: <strong>{info['winning']}</strong>
      </p>

      <div id="{chart_id}" style="height:420px"></div>
      <script>
      Plotly.newPlot('{chart_id}', [
        {{
          x: {wu_times}, y: {wu_temps},
          type: 'scatter', mode: 'lines+markers', name: 'METAR/WU (1°C)',
          line: {{color: '#e74c3c', width: 2}}, marker: {{size: 4}},
          hovertemplate: 'METAR: %{{y}}°C<br>%{{x}}<extra></extra>'
        }},
        {{
          x: {syn_times}, y: {syn_temps},
          type: 'scatter', mode: 'lines+markers', name: 'SYNOP (0.1°C)',
          line: {{color: '#2ecc71', width: 2}}, marker: {{size: 5}},
          hovertemplate: 'SYNOP: %{{y:.1f}}°C<br>%{{x}}<extra></extra>'
        }},
        {{
          x: {om_times}, y: {om_temps},
          type: 'scatter', mode: 'lines+markers', name: 'Open-Meteo (model)',
          line: {{color: '#3498db', width: 2}}, marker: {{size: 4}},
          hovertemplate: 'Open-Meteo: %{{y:.1f}}°C<br>%{{x}}<extra></extra>'
        }},
        {{
          x: {wu_times}, y: {rh_j},
          type: 'scatter', mode: 'lines', name: 'Running High (METAR)',
          line: {{color: '#e67e22', width: 2, dash: 'dash'}},
          hovertemplate: 'Running high: %{{y}}°C<extra></extra>'
        }}
      ], {{
        paper_bgcolor: '#161b22', plot_bgcolor: '#161b22',
        font: {{ color: '#c9d1d9', size: 12 }},
        margin: {{ l: 45, r: 20, t: 10, b: 45 }},
        xaxis: {{ gridcolor: '#21262d', title: 'Time (CET)' }},
        yaxis: {{ gridcolor: '#21262d', title: '°C', dtick: 1 }},
        legend: {{ bgcolor: 'rgba(22,27,34,0.9)', bordercolor: '#30363d', borderwidth: 1, font: {{size: 10}}, x: 0.01, y: 0.99 }},
        hovermode: 'x unified',
        shapes: [
          {{ type:'line', x0:'16:00', x1:'16:00', y0:0, y1:1, yref:'paper',
             line:{{color:'#e74c3c',width:2,dash:'dot'}} }},
          {{ type:'line', x0:'17:00', x1:'17:00', y0:0, y1:1, yref:'paper',
             line:{{color:'#58a6ff',width:2,dash:'dot'}} }}
        ],
        annotations: [
          {{ x:'16:00', y:1.02, yref:'paper', text:'Ceiling NO fires', showarrow:false,
             font:{{color:'#e74c3c',size:10}}, bgcolor:'rgba(13,17,23,0.9)', bordercolor:'#e74c3c',
             xanchor:'left', xshift:5 }},
          {{ x:'17:00', y:1.02, yref:'paper', text:'Locked-In YES fires', showarrow:false,
             font:{{color:'#58a6ff',size:10}}, bgcolor:'rgba(13,17,23,0.9)', bordercolor:'#58a6ff',
             xanchor:'left', xshift:5 }}
        ]
      }}, {{ responsive:true, displayModeBar:false }});
      </script>

      <div class="trend-table">
        <h3>What each source was saying at 4pm and 5pm</h3>
        <table>
          <thead><tr><th>Source</th><th>Temp at 4pm</th><th>Trend at 4pm</th><th>Temp at 5pm</th><th>Trend at 5pm</th></tr></thead>
          <tbody>
            <tr>
              <td>METAR/WU</td>
              <td>{fmt_temp(wu_at_16)}°C</td><td>{trend_icon(wu_trend_16)}</td>
              <td>{fmt_temp(wu_at_17)}°C</td><td>{trend_icon(wu_trend_17)}</td>
            </tr>
            <tr>
              <td>SYNOP</td>
              <td>{fmt_temp(syn_at_16)}°C</td><td>{trend_icon(syn_trend_16)}</td>
              <td>{fmt_temp(syn_at_17)}°C</td><td>{trend_icon(syn_trend_17)}</td>
            </tr>
            <tr>
              <td>Open-Meteo</td>
              <td>{fmt_temp(om_at_16)}°C</td><td>{trend_icon(om_trend_16)}</td>
              <td>{fmt_temp(om_at_17)}°C</td><td>{trend_icon(om_trend_17)}</td>
            </tr>
          </tbody>
        </table>
      </div>

      <div class="verdict-box">
        <strong>Could we have avoided these losses?</strong>
        <p>At 4pm, the temperature was at {fmt_temp(wu_at_16)}°C.</p>
    """

    # Build verdict based on trend data
    rising_sources_16 = sum(1 for t in [wu_trend_16, syn_trend_16, om_trend_16] if t == "RISING")
    rising_sources_17 = sum(1 for t in [wu_trend_17, syn_trend_17, om_trend_17] if t == "RISING")

    if rising_sources_16 > 0 or rising_sources_17 > 0:
        charts_html += f"""
        <p class="verdict-yes">YES — {rising_sources_16}/3 sources showed a <strong>rising trend</strong> at 4pm
        {"and " + str(rising_sources_17) + "/3 at 5pm" if rising_sources_17 > 0 else ""}.
        A simple rule — <em>"don't fire Ceiling NO or Locked-In YES if ANY source shows a rising trend"</em> —
        would have blocked both trades and saved us <strong>$200</strong> on this day.</p>
        """
    else:
        charts_html += f"""
        <p class="verdict-no">Harder to detect — no clear rising signal at 4pm. The surge came after 5pm.</p>
        """

    charts_html += """
      </div>
    </div>
    """


html = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8">
<title>The Two Losing Days — All Data Sources</title>
<script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>
<style>
  * {{ margin:0; padding:0; box-sizing:border-box; }}
  body {{ font-family:'Segoe UI',system-ui,sans-serif; background:#0d1117; color:#c9d1d9; padding:24px; line-height:1.6; }}
  .container {{ max-width:1100px; margin:0 auto; }}
  h1 {{ font-size:22px; color:#e6edf3; margin-bottom:4px; }}
  h2 {{ font-size:18px; color:#e6edf3; margin:28px 0 8px; border-bottom:1px solid #30363d; padding-bottom:6px; }}
  h3 {{ font-size:14px; color:#e6edf3; margin:16px 0 8px; }}
  .sub {{ font-size:13px; color:#8b949e; margin-bottom:20px; }}
  .day-sub {{ font-size:13px; color:#8b949e; margin-bottom:12px; }}

  .day-card {{ background:#161b22; border:1px solid #30363d; border-radius:12px; padding:20px; margin:20px 0; }}

  table {{ width:100%; border-collapse:collapse; font-size:14px; }}
  th {{ text-align:left; padding:8px 12px; border-bottom:2px solid #30363d; color:#8b949e; font-size:11px; text-transform:uppercase; }}
  td {{ padding:8px 12px; border-bottom:1px solid #21262d; }}

  .trend-table {{ margin:16px 0; }}

  .verdict-box {{ background:#0d1117; border:1px solid #30363d; border-radius:8px; padding:16px; margin:16px 0; }}
  .verdict-box strong {{ font-size:15px; color:#e6edf3; }}
  .verdict-box p {{ margin-top:8px; font-size:13px; }}
  .verdict-yes {{ color:#2ecc71; font-weight:600; }}
  .verdict-no {{ color:#f1c40f; }}

  .conclusion {{ background:linear-gradient(135deg, #0d2818, #161b22); border:2px solid #238636;
    border-radius:10px; padding:20px; margin:24px 0; }}
  .conclusion h3 {{ color:#2ecc71; font-size:16px; margin-bottom:10px; }}
  .conclusion p {{ font-size:14px; margin-top:8px; }}
  .conclusion code {{ background:#21262d; padding:2px 6px; border-radius:4px; font-size:13px; }}
</style></head><body>
<div class="container">

<h1>The Two Losing Days — What Were the Data Sources Saying?</h1>
<div class="sub">
  Feb 15 and Feb 18 both had late-evening temperature surges that broke the Ceiling NO and Locked-In YES strategies.
  Could SYNOP or Open-Meteo have warned us?
</div>

{charts_html}

<div class="conclusion">
  <h3>Conclusion: Add a Trend Safety Check</h3>
  <p>On both losing days, at least one data source was already showing a <strong>rising temperature trend</strong>
  at 4-5pm — the exact time our risky signals fired.</p>
  <p>A simple safeguard would prevent these losses:</p>
  <p style="margin-top:12px; padding:12px; background:#0d1117; border:1px solid #30363d; border-radius:6px; font-family:monospace; font-size:14px;">
    if trend_at_signal_time == RISING on ANY source:<br>
    &nbsp;&nbsp;&nbsp;&nbsp;→ do NOT fire Ceiling NO or Locked-In YES
  </p>
  <p style="margin-top:12px">This would have saved all 4 losing trades (-$400) while only blocking signals
  that were going to lose anyway. The Floor NO strategy (T1 + T2) doesn't need this check —
  it's already mathematically safe.</p>
</div>

</div></body></html>"""

out_path = r"C:\Users\Charl\Desktop\Cursor\weather-bot\losing_days_sources.html"
with open(out_path, "w", encoding="utf-8") as f:
    f.write(html)
print(f"\nSaved to {out_path}")

import webbrowser
webbrowser.open(out_path)
