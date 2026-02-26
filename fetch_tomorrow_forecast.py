#!/usr/bin/env python3
"""Fetch OpenMeteo forecast for tomorrow and generate HTML chart."""
import urllib.request
import json
from datetime import date, timedelta

# Paris CDG coordinates
CDG_LAT = 49.0097
CDG_LON = 2.5479

# Tomorrow's date
tomorrow = date.today() + timedelta(days=1)
tomorrow_str = tomorrow.strftime("%Y-%m-%d")

print(f"Fetching OpenMeteo forecast for {tomorrow_str}...")

# Fetch hourly forecast
url = (f"https://api.open-meteo.com/v1/forecast?"
       f"latitude={CDG_LAT}&longitude={CDG_LON}"
       f"&hourly=temperature_2m"
       f"&timezone=Europe/Paris"
       f"&start_date={tomorrow_str}&end_date={tomorrow_str}")

req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
with urllib.request.urlopen(req, timeout=15) as r:
    data = json.loads(r.read())

hourly = data.get("hourly", {})
times = hourly.get("time", [])
temps = hourly.get("temperature_2m", [])

if not times or not temps:
    print("No forecast data available")
    exit(1)

# Extract hour and temp
forecast_data = []
for t, temp in zip(times, temps):
    hour = int(t.split("T")[1].split(":")[0])
    forecast_data.append({"hour": hour, "temp": temp})

# Calculate stats
temps_only = [d["temp"] for d in forecast_data]
forecast_high = max(temps_only)
forecast_low = min(temps_only)
peak_hour = forecast_data[temps_only.index(forecast_high)]["hour"]

# Apply bias correction
BIAS_CORRECTION = 1.0
corrected_high = forecast_high + BIAS_CORRECTION

print(f"  Raw forecast high: {forecast_high:.1f}°C at {peak_hour}:00")
print(f"  Corrected high: {corrected_high:.1f}°C (+ {BIAS_CORRECTION}°C bias)")
print(f"  Forecast low: {forecast_low:.1f}°C")

# Generate HTML
html = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>Tomorrow's Forecast - {tomorrow_str}</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>
        body {{ font-family: 'Segoe UI', sans-serif; margin: 40px; background: #f5f7fa; }}
        .container {{ max-width: 1200px; margin: 0 auto; }}
        .header {{ background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); 
                   color: white; padding: 30px; border-radius: 12px; margin-bottom: 30px; }}
        .header h1 {{ margin: 0; }}
        .stats {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); 
                 gap: 20px; margin-bottom: 30px; }}
        .stat {{ background: white; padding: 20px; border-radius: 8px; box-shadow: 0 2px 8px rgba(0,0,0,0.1); }}
        .stat-label {{ color: #666; font-size: 0.9rem; margin-bottom: 5px; }}
        .stat-value {{ font-size: 2rem; font-weight: bold; color: #667eea; }}
        .chart-container {{ background: white; padding: 30px; border-radius: 12px; 
                           box-shadow: 0 2px 8px rgba(0,0,0,0.1); }}
        .note {{ background: #fff3cd; border-left: 4px solid #ffc107; padding: 15px; 
                margin-top: 20px; border-radius: 4px; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>🌡️ Tomorrow's Temperature Forecast</h1>
            <p>Date: {tomorrow_str} | Source: OpenMeteo | Location: Paris CDG</p>
        </div>
        
        <div class="stats">
            <div class="stat">
                <div class="stat-label">Raw Forecast High</div>
                <div class="stat-value">{forecast_high:.1f}°C</div>
            </div>
            <div class="stat">
                <div class="stat-label">Corrected High (+1°C bias)</div>
                <div class="stat-value">{corrected_high:.1f}°C</div>
            </div>
            <div class="stat">
                <div class="stat-label">Forecast Low</div>
                <div class="stat-value">{forecast_low:.1f}°C</div>
            </div>
            <div class="stat">
                <div class="stat-label">Peak Hour</div>
                <div class="stat-value">{peak_hour}:00</div>
            </div>
        </div>
        
        <div class="chart-container">
            <canvas id="forecastChart"></canvas>
        </div>
        
        <div class="note">
            <strong>Note:</strong> OpenMeteo historically underforecasts by ~1.0°C. 
            The corrected high ({corrected_high:.1f}°C) is used for trading signals.
        </div>
    </div>
    
    <script>
        const ctx = document.getElementById('forecastChart').getContext('2d');
        new Chart(ctx, {{
            type: 'line',
            data: {{
                labels: {json.dumps([f"{d['hour']:02d}:00" for d in forecast_data])},
                datasets: [{{
                    label: 'Raw Forecast',
                    data: {json.dumps([d['temp'] for d in forecast_data])},
                    borderColor: '#667eea',
                    backgroundColor: 'rgba(102, 126, 234, 0.1)',
                    tension: 0.4,
                    fill: true
                }}, {{
                    label: 'Corrected (+1°C)',
                    data: {json.dumps([d['temp'] + BIAS_CORRECTION for d in forecast_data])},
                    borderColor: '#f093fb',
                    backgroundColor: 'rgba(240, 147, 251, 0.1)',
                    borderDash: [5, 5],
                    tension: 0.4,
                    fill: false
                }}]
            }},
            options: {{
                responsive: true,
                maintainAspectRatio: true,
                plugins: {{
                    title: {{
                        display: true,
                        text: 'Hourly Temperature Forecast - {tomorrow_str}',
                        font: {{ size: 18 }}
                    }},
                    legend: {{
                        display: true,
                        position: 'top'
                    }}
                }},
                scales: {{
                    y: {{
                        title: {{
                            display: true,
                            text: 'Temperature (°C)'
                        }},
                        ticks: {{
                            callback: function(value) {{
                                return value + '°C';
                            }}
                        }}
                    }},
                    x: {{
                        title: {{
                            display: true,
                            text: 'Hour (CET)'
                        }}
                    }}
                }}
            }}
        }});
    </script>
</body>
</html>"""

# Save HTML
output_file = "tomorrow_forecast.html"
with open(output_file, "w", encoding="utf-8") as f:
    f.write(html)

print(f"\nSaved to {output_file}")
