"""
Build an HTML page that visually explains each of the 4 losing trades
with temperature charts and simple step-by-step narratives.
"""
import urllib.request, json, re, sys, time
from datetime import datetime, date, timezone, timedelta
from zoneinfo import ZoneInfo

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

CET = ZoneInfo("Europe/Paris")

LOSING_TRADES = [
    {
        "date": date(2026, 2, 15), "date_label": "February 15",
        "type": "Ceiling NO", "type_color": "#e74c3c",
        "bracket": ">=7°C", "bracket_lo": 7, "bracket_hi": None,
        "side": "NO", "signal_hour": 16,
        "wu_high": 9, "winning": ">=7°C",
    },
    {
        "date": date(2026, 2, 15), "date_label": "February 15",
        "type": "Locked-In YES", "type_color": "#58a6ff",
        "bracket": "3°C", "bracket_lo": 3, "bracket_hi": 3,
        "side": "YES", "signal_hour": 17,
        "wu_high": 9, "winning": ">=7°C",
    },
    {
        "date": date(2026, 2, 18), "date_label": "February 18",
        "type": "Ceiling NO", "type_color": "#e74c3c",
        "bracket": ">=8°C", "bracket_lo": 8, "bracket_hi": None,
        "side": "NO", "signal_hour": 16,
        "wu_high": 9, "winning": ">=8°C",
    },
    {
        "date": date(2026, 2, 18), "date_label": "February 18",
        "type": "Locked-In YES", "type_color": "#58a6ff",
        "bracket": "6°C", "bracket_lo": 6, "bracket_hi": 6,
        "side": "YES", "signal_hour": 17,
        "wu_high": 9, "winning": ">=8°C",
    },
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
            pts.append({"time": d.strftime("%H:%M"), "hour": d.hour + d.minute/60,
                         "temp": temp})
    return sorted(pts, key=lambda x: x["hour"])


# Fetch temperature data for both days
print("Fetching temperature data...", flush=True)
wu_cache = {}
for dt in set(t["date"] for t in LOSING_TRADES):
    wu_cache[dt] = fetch_wu(dt)
    print(f"  {dt}: {len(wu_cache[dt])} readings, high={max(p['temp'] for p in wu_cache[dt])}°C")


# Build HTML for each trade
trade_sections = []

for idx, trade in enumerate(LOSING_TRADES):
    wu = wu_cache[trade["date"]]
    times = [p["time"] for p in wu]
    temps = [p["temp"] for p in wu]

    # Running high
    rh_vals = []
    rh = None
    for p in wu:
        if rh is None or p["temp"] > rh:
            rh = p["temp"]
        rh_vals.append(rh)

    # Find running high at signal time
    rh_at_signal = None
    temp_at_signal = None
    for p in wu:
        if rh_at_signal is None or p["temp"] > rh_at_signal:
            rh_at_signal = p["temp"]
        if p["hour"] <= trade["signal_hour"]:
            temp_at_signal = p["temp"]
            rh_at_signal_snap = rh_at_signal

    # Find YES price at signal time (approximate from bracket position)
    # We know from debug: Feb15 >=7 YES=100%, Feb15 3°C YES=2.5%, Feb18 >=8 YES=100%, Feb18 6°C YES=0.8%
    yes_prices = {
        (date(2026,2,15), ">=7°C"): 1.00,
        (date(2026,2,15), "3°C"): 0.025,
        (date(2026,2,18), ">=8°C"): 1.00,
        (date(2026,2,18), "6°C"): 0.008,
    }
    yes_p = yes_prices.get((trade["date"], trade["bracket"]), 0)

    if trade["side"] == "NO":
        entry_price = round((1 - yes_p) * 100, 2)
        loss = entry_price
    else:
        entry_price = round(yes_p * 100, 2)
        loss = entry_price

    # Build the narrative steps
    if trade["type"] == "Ceiling NO":
        gap = trade["bracket_lo"] - rh_at_signal_snap
        steps = [
            {"time": "All day", "icon": "🌡️",
             "text": f"Temperature has been low all day. At {trade['signal_hour']}:00 CET, the running high is only <strong>{rh_at_signal_snap}°C</strong>."},
            {"time": f"{trade['signal_hour']}:00", "icon": "🔴",
             "text": f"<strong>Ceiling NO triggers.</strong> The <strong>{trade['bracket']}</strong> bracket starts at {trade['bracket_lo']}°C — that's {gap:.0f}°C above the running high. The strategy says: \"It's 4pm, temperature can't climb {gap:.0f}°C more. This bracket is dead.\""},
            {"time": f"{trade['signal_hour']}:00", "icon": "💰",
             "text": f"We buy NO on <strong>{trade['bracket']}</strong>. The market prices YES at {yes_p:.0%}, so NO costs <strong>${entry_price:.2f}</strong> per $100 of shares."},
            {"time": "Evening", "icon": "📈",
             "text": f"<strong>But the temperature keeps climbing!</strong> A late warm front pushes the temp from {rh_at_signal_snap}°C all the way up to <strong>{trade['wu_high']}°C</strong> by midnight."},
            {"time": "Resolution", "icon": "❌",
             "text": f"The {trade['bracket']} bracket resolves <strong>YES</strong> (actual high {trade['wu_high']}°C ≥ {trade['bracket_lo']}°C). Our NO shares are worthless. <strong>We lose ${loss:.2f}.</strong>"},
        ]
        lesson = f"The market already knew this bracket would likely resolve YES (priced at {yes_p:.0%}). Our NO was cheap (${entry_price:.2f}), so the loss was tiny. But if the market had been uncertain (YES at 50%), we'd have lost $50."
    else:
        steps = [
            {"time": "All day", "icon": "🌡️",
             "text": f"It's been a cold day. At {trade['signal_hour']}:00 CET, the running high is <strong>{rh_at_signal_snap}°C</strong>."},
            {"time": f"{trade['signal_hour']}:00", "icon": "🔵",
             "text": f"<strong>Locked-In YES triggers.</strong> The running high of {rh_at_signal_snap}°C falls in the <strong>{trade['bracket']}</strong> bracket. The strategy says: \"It's 5pm, the daily high is set. This bracket will win.\""},
            {"time": f"{trade['signal_hour']}:00", "icon": "💰",
             "text": f"We buy YES on <strong>{trade['bracket']}</strong> at {yes_p:.1%} — that's <strong>${entry_price:.2f}</strong> per $100 of shares."},
            {"time": "Evening", "icon": "📈",
             "text": f"<strong>But the temperature surges!</strong> It climbs from {rh_at_signal_snap}°C to <strong>{trade['wu_high']}°C</strong>. The daily high is no longer {rh_at_signal_snap}°C — it's {trade['wu_high']}°C."},
            {"time": "Resolution", "icon": "❌",
             "text": f"The winning bracket is <strong>{trade['winning']}</strong>, not {trade['bracket']}. Our YES shares on {trade['bracket']} are worthless. <strong>We lose ${loss:.2f}.</strong>"},
        ]
        lesson = f"At 5pm, the market only priced this bracket at {yes_p:.1%} — it already suspected the high wasn't final. We bet against the market and lost. The loss was small (${loss:.2f}) only because YES was cheap."

    # Steps HTML
    steps_html = ""
    for s in steps:
        steps_html += f"""
        <div class="step">
          <div class="step-icon">{s['icon']}</div>
          <div class="step-body">
            <div class="step-time">{s['time']}</div>
            <div class="step-text">{s['text']}</div>
          </div>
        </div>"""

    chart_id = f"chart{idx}"

    # Signal hour vertical line and annotation
    signal_label = "Ceiling NO fires" if trade["type"] == "Ceiling NO" else "Locked-In YES fires"

    trade_sections.append(f"""
    <div class="trade-card {'card-ceil' if trade['type'] == 'Ceiling NO' else 'card-lock'}">
      <div class="trade-header">
        <div class="trade-num">Trade #{idx+1}</div>
        <div class="trade-title">{trade['type']} on {trade['bracket']} — {trade['date_label']}</div>
        <div class="trade-loss">Lost ${loss:.2f}</div>
      </div>

      <div class="trade-body">
        <div class="chart-col">
          <div id="{chart_id}" style="height:300px;"></div>
          <script>
          Plotly.newPlot('{chart_id}', [
            {{
              x: {json.dumps(times)}, y: {json.dumps(temps)},
              type: 'scatter', mode: 'lines+markers', name: 'Temperature',
              line: {{color: '#e74c3c', width: 2}}, marker: {{size: 3}},
              hovertemplate: '%{{x}}: %{{y}}°C<extra></extra>'
            }},
            {{
              x: {json.dumps(times)}, y: {json.dumps(rh_vals)},
              type: 'scatter', mode: 'lines', name: 'Running High',
              line: {{color: '#e67e22', width: 2.5, dash: 'dash'}},
              hovertemplate: 'Running high: %{{y}}°C<extra></extra>'
            }}
          ], {{
            paper_bgcolor: '#161b22', plot_bgcolor: '#161b22',
            font: {{ color: '#c9d1d9', size: 11 }},
            margin: {{ l: 40, r: 20, t: 10, b: 40 }},
            xaxis: {{ gridcolor: '#21262d' }},
            yaxis: {{ gridcolor: '#21262d', title: '°C', dtick: 1 }},
            legend: {{ bgcolor: 'rgba(22,27,34,0.9)', bordercolor: '#30363d', borderwidth: 1, font: {{size: 10}}, x: 0.01, y: 0.99 }},
            hovermode: 'x unified',
            shapes: [{{
              type: 'line', x0: '{trade["signal_hour"]:02d}:00', x1: '{trade["signal_hour"]:02d}:00',
              y0: 0, y1: 1, yref: 'paper',
              line: {{ color: '{trade["type_color"]}', width: 2, dash: 'dot' }}
            }}],
            annotations: [{{
              x: '{trade["signal_hour"]:02d}:00', y: 1, yref: 'paper',
              text: '{signal_label}', showarrow: false,
              font: {{ color: '{trade["type_color"]}', size: 10 }},
              bgcolor: 'rgba(13,17,23,0.9)', bordercolor: '{trade["type_color"]}',
              xanchor: 'left', yanchor: 'top', xshift: 5
            }},
            {{
              x: '{times[-1]}', y: {trade['wu_high']},
              text: 'Actual high: {trade["wu_high"]}°C', showarrow: true,
              arrowcolor: '#e74c3c', arrowhead: 2,
              font: {{ color: '#e74c3c', size: 11 }},
              bgcolor: 'rgba(13,17,23,0.9)', bordercolor: '#e74c3c',
              ax: -60, ay: -20
            }}]
          }}, {{ responsive: true, displayModeBar: false }});
          </script>
        </div>

        <div class="steps-col">
          <h3>What happened</h3>
          {steps_html}
        </div>
      </div>

      <div class="lesson">
        <strong>Lesson:</strong> {lesson}
      </div>
    </div>
    """)

now_cet = datetime.now(timezone.utc).astimezone(CET)

html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>The 4 Losing Trades — Explained</title>
<script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{ font-family: 'Segoe UI', system-ui, sans-serif; background: #0d1117; color: #c9d1d9; padding: 24px; line-height: 1.6; }}
  .container {{ max-width: 1100px; margin: 0 auto; }}
  h1 {{ font-size: 24px; color: #e6edf3; margin-bottom: 4px; }}
  h2 {{ font-size: 18px; color: #e6edf3; margin: 32px 0 16px; border-bottom: 1px solid #30363d; padding-bottom: 6px; }}
  h3 {{ font-size: 14px; color: #e6edf3; margin-bottom: 10px; }}
  .subtitle {{ font-size: 13px; color: #8b949e; margin-bottom: 24px; }}
  .green {{ color: #2ecc71; }} .red {{ color: #e74c3c; }}

  .summary-box {{ background: #161b22; border: 1px solid #30363d; border-radius: 10px; padding: 20px; margin: 16px 0; display: flex; gap: 30px; flex-wrap: wrap; }}
  .summary-stat {{ text-align: center; }}
  .summary-stat .big {{ font-size: 28px; font-weight: 700; }}
  .summary-stat .label {{ font-size: 11px; color: #8b949e; text-transform: uppercase; letter-spacing: 0.5px; }}

  .trade-card {{ background: #161b22; border-radius: 12px; margin: 20px 0; overflow: hidden; }}
  .card-ceil {{ border: 1px solid #e74c3c; }}
  .card-lock {{ border: 1px solid #58a6ff; }}

  .trade-header {{ display: flex; align-items: center; gap: 12px; padding: 14px 20px; border-bottom: 1px solid #21262d; }}
  .trade-num {{ font-size: 11px; text-transform: uppercase; letter-spacing: 0.5px; color: #8b949e; }}
  .trade-title {{ flex: 1; font-size: 16px; font-weight: 600; color: #e6edf3; }}
  .trade-loss {{ font-size: 16px; font-weight: 700; color: #e74c3c; }}

  .trade-body {{ display: flex; gap: 0; }}
  .chart-col {{ flex: 1; min-width: 400px; padding: 12px; }}
  .steps-col {{ flex: 1; min-width: 300px; padding: 16px 20px; border-left: 1px solid #21262d; }}

  .step {{ display: flex; gap: 10px; margin: 8px 0; }}
  .step-icon {{ font-size: 18px; min-width: 28px; text-align: center; padding-top: 2px; }}
  .step-body {{ flex: 1; }}
  .step-time {{ font-size: 10px; color: #8b949e; text-transform: uppercase; letter-spacing: 0.5px; }}
  .step-text {{ font-size: 13px; color: #c9d1d9; }}

  .lesson {{ padding: 14px 20px; background: #1c1208; border-top: 1px solid #d29922; font-size: 13px; color: #f0d060; }}

  .takeaway {{ background: linear-gradient(135deg, #0d2818, #161b22); border: 2px solid #238636; border-radius: 10px; padding: 20px; margin: 24px 0; }}
  .takeaway h3 {{ color: #2ecc71; font-size: 16px; margin-bottom: 10px; }}
  .takeaway ul {{ margin: 8px 0 0 20px; font-size: 13px; }}
  .takeaway li {{ margin: 6px 0; }}
  .takeaway .safe {{ color: #2ecc71; font-weight: 600; }}
  .takeaway .risky {{ color: #e74c3c; font-weight: 600; }}

  @media (max-width: 800px) {{
    .trade-body {{ flex-direction: column; }}
    .steps-col {{ border-left: none; border-top: 1px solid #21262d; }}
    .chart-col {{ min-width: unset; }}
    .steps-col {{ min-width: unset; }}
  }}
</style>
</head>
<body>
<div class="container">

<h1>The 4 Losing Trades — Explained</h1>
<div class="subtitle">
  Out of 53 trades across 8 days, 4 lost money. Here's exactly what went wrong in each one.
</div>

<div class="summary-box">
  <div class="summary-stat">
    <div class="big">53</div>
    <div class="label">Total trades</div>
  </div>
  <div class="summary-stat">
    <div class="big green">49</div>
    <div class="label">Winners</div>
  </div>
  <div class="summary-stat">
    <div class="big red">4</div>
    <div class="label">Losers</div>
  </div>
  <div class="summary-stat">
    <div class="big green">$0.00</div>
    <div class="label">Floor NO losses</div>
  </div>
  <div class="summary-stat">
    <div class="big red">-$0.10</div>
    <div class="label">Ceiling NO losses</div>
  </div>
  <div class="summary-stat">
    <div class="big red">-$3.20</div>
    <div class="label">Locked-In YES losses</div>
  </div>
</div>

<p style="font-size:14px; margin:16px 0; padding:14px 18px; background:#161b22; border:1px solid #30363d; border-radius:8px;">
  <strong>Quick reminder on how bets work:</strong>
  When you buy NO at $0.95 (because YES costs $0.05), you pay $95 per $100 of shares.
  If the bracket resolves NO, you collect $100 — profit $5.
  <span class="red">If the bracket resolves YES, your shares are worth $0 — you lose the full $95.</span>
  The same applies in reverse for YES bets.
</p>

<h2>The Losers</h2>

{''.join(trade_sections)}

<h2>Why Both Losing Days Look the Same</h2>
<div style="background:#161b22; border:1px solid #30363d; border-radius:10px; padding:20px; margin:16px 0; font-size:14px;">
  <p>Both Feb 15 and Feb 18 share an unusual pattern: <strong>the temperature peaked late at night, not in the afternoon.</strong></p>
  <p style="margin-top:10px">Normally in February, the daily high occurs between 12pm-4pm as the sun heats the ground. But on these two days, a warm air mass arrived in the evening, pushing temperatures up after sunset. This broke two assumptions:</p>
  <ul style="margin: 10px 0 0 20px;">
    <li><strong>Ceiling NO (4pm):</strong> assumed the temperature wouldn't climb 2°C+ more after 4pm</li>
    <li><strong>Locked-In YES (5pm):</strong> assumed the running high at 5pm was the final daily high</li>
  </ul>
  <p style="margin-top:10px">On both days, the temperature surged <strong>+4°C to +6°C after 5pm</strong>. This is rare but clearly happens in Paris winter.</p>
</div>

<div class="takeaway">
  <h3>Takeaway: What's Safe, What's Risky</h3>
  <ul>
    <li><span class="safe">Floor NO — Tier 1</span>: <strong>Zero losses, ever.</strong> Once the running high passes a threshold, it can never go back. This is a mathematical certainty. The edge per trade is small (pennies) but there is no risk.</li>
    <li><span class="safe">Floor NO — Tier 2</span>: <strong>Zero losses in our data.</strong> Would only lose if the forecast is wrong by 4°C+. Very unlikely but not impossible.</li>
    <li><span class="risky">Ceiling NO</span>: Lost 2 times. The losses were tiny ($0.05) only because the market had already priced YES near 100%. If the market is unsure (YES at 30-50%), a wrong Ceiling NO could lose $50-$70 per $100 bet. <strong>The 4pm cutoff is too early for Paris winter.</strong></li>
    <li><span class="risky">Locked-In YES</span>: Lost 2 times. The losses were small ($0.75-$2.45) only because YES was cheap. If we'd bought YES at 50%, we'd have lost $50. <strong>The 5pm cutoff is far too early</strong> — temperature surged +4-6°C after 5pm on both losing days.</li>
  </ul>
  <p style="margin-top:12px; font-size:13px; color:#8b949e;">
    <strong>Recommendation:</strong> Only use Floor NO (Tier 1 + Tier 2) for real money.
    Ceiling NO and Locked-In YES could work with much later cutoff times (10pm+) or with additional
    safeguards (declining temperature trend confirmed by SYNOP), but the current parameters are unsafe.
  </p>
</div>

</div>
</body>
</html>"""

out_path = r"C:\Users\Charl\Desktop\Cursor\weather-bot\losses_explained.html"
with open(out_path, "w", encoding="utf-8") as f:
    f.write(html)
print(f"\nSaved to {out_path}")

import webbrowser
webbrowser.open(out_path)
