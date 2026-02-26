#!/usr/bin/env python3
"""Generate comparison charts for Feb 24 and Feb 25."""
import json
from datetime import datetime
from collections import defaultdict

# Read log file
with open("weather_log.jsonl", "r", encoding="utf-8") as f:
    logs = [json.loads(line) for line in f if line.strip()]

# Extract data for both days
feb24_data = defaultdict(list)
feb25_data = defaultdict(list)

for entry in logs:
    if entry.get("event") != "observation":
        continue
    
    ts = entry.get("ts", "")
    if "2026-02-24" in ts:
        data = feb24_data
        date_str = "2026-02-24"
    elif "2026-02-25" in ts:
        data = feb25_data
        date_str = "2026-02-25"
    else:
        continue
    
    # Parse timestamp
    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    hour = dt.hour + dt.minute / 60.0
    
    # Extract temperatures
    metar = entry.get("temp_c")
    synop = entry.get("synop_temp_c")
    om = entry.get("openmeteo_temp_c")
    daily_high = entry.get("daily_high_c")
    
    if metar is not None:
        data["hours"].append(hour)
        data["metar"].append(metar)
        data["synop"].append(synop if synop and synop > -30 else None)
        data["om"].append(om)
        data["daily_high"].append(daily_high)

# Generate HTML
html = """<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>Feb 24 vs Feb 25 Temperature Comparison</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>
        body { font-family: 'Segoe UI', sans-serif; margin: 40px; background: #f5f7fa; }
        .container { max-width: 1400px; margin: 0 auto; }
        .header { background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); 
                   color: white; padding: 30px; border-radius: 12px; margin-bottom: 30px; }
        .header h1 { margin: 0; }
        .charts { display: grid; grid-template-columns: 1fr; gap: 30px; }
        .chart-container { background: white; padding: 30px; border-radius: 12px; 
                           box-shadow: 0 2px 8px rgba(0,0,0,0.1); }
        .chart-title { font-size: 1.5rem; font-weight: bold; margin-bottom: 20px; color: #333; }
        .stats { display: grid; grid-template-columns: repeat(2, 1fr); gap: 20px; margin-bottom: 30px; }
        .stat-box { background: white; padding: 20px; border-radius: 8px; box-shadow: 0 2px 8px rgba(0,0,0,0.1); }
        .stat-label { color: #666; font-size: 0.9rem; margin-bottom: 5px; }
        .stat-value { font-size: 1.8rem; font-weight: bold; color: #667eea; }
        .note { background: #e3f2fd; border-left: 4px solid #2196f3; padding: 15px; 
                margin-top: 20px; border-radius: 4px; }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>🌡️ Temperature Comparison: Feb 24 vs Feb 25</h1>
            <p>Comparing METAR (actual), SYNOP (secondary), and OpenMeteo (forecast) data</p>
        </div>
        
        <div class="stats">
            <div class="stat-box">
                <div class="stat-label">Feb 24 Daily High</div>
                <div class="stat-value">""" + f"{max(feb24_data['daily_high']):.1f}°C" + """</div>
                <div style="margin-top: 10px; color: #666;">OpenMeteo predicted: 15.8°C (raw) / 16.8°C (corrected)</div>
            </div>
            <div class="stat-box">
                <div class="stat-label">Feb 25 Daily High</div>
                <div class="stat-value">""" + f"{max(feb25_data['daily_high']):.1f}°C" + """</div>
                <div style="margin-top: 10px; color: #666;">OpenMeteo predicted: ~19°C (estimated)</div>
            </div>
        </div>
        
        <div class="charts">
            <div class="chart-container">
                <div class="chart-title">February 24, 2026</div>
                <canvas id="chart24"></canvas>
            </div>
            
            <div class="chart-container">
                <div class="chart-title">February 25, 2026</div>
                <canvas id="chart25"></canvas>
            </div>
        </div>
        
        <div class="note">
            <strong>Key Observations:</strong><br>
            • Feb 24: Temperature peaked at 14°C around 5 PM, then declined. OpenMeteo overforecast by ~3°C.<br>
            • Feb 25: Temperature climbed steadily from 18°C → 21°C during afternoon. Multiple FLOOR_NO_CERTAIN signals fired.<br>
            • SYNOP data shows -30.5°C errors (data unavailable) on Feb 24 evening.<br>
            • OpenMeteo (purple line) is the forecast, not real-time observation.
        </div>
    </div>
    
    <script>
        // Feb 24 Chart
        const ctx24 = document.getElementById('chart24').getContext('2d');
        new Chart(ctx24, {
            type: 'line',
            data: {
                labels: """ + json.dumps([f"{h:.1f}h" for h in feb24_data["hours"]]) + """,
                datasets: [
                    {
                        label: 'METAR (Actual)',
                        data: """ + json.dumps(feb24_data["metar"]) + """,
                        borderColor: '#f44336',
                        backgroundColor: 'rgba(244, 67, 54, 0.1)',
                        borderWidth: 3,
                        tension: 0.4,
                        fill: false
                    },
                    {
                        label: 'Daily High',
                        data: """ + json.dumps(feb24_data["daily_high"]) + """,
                        borderColor: '#ff9800',
                        backgroundColor: 'rgba(255, 152, 0, 0.1)',
                        borderWidth: 2,
                        borderDash: [5, 5],
                        tension: 0.4,
                        fill: false
                    },
                    {
                        label: 'SYNOP (Secondary)',
                        data: """ + json.dumps(feb24_data["synop"]) + """,
                        borderColor: '#4caf50',
                        backgroundColor: 'rgba(76, 175, 80, 0.1)',
                        borderWidth: 2,
                        tension: 0.4,
                        fill: false,
                        spanGaps: true
                    },
                    {
                        label: 'OpenMeteo (Forecast)',
                        data: """ + json.dumps(feb24_data["om"]) + """,
                        borderColor: '#9c27b0',
                        backgroundColor: 'rgba(156, 39, 176, 0.1)',
                        borderWidth: 2,
                        borderDash: [10, 5],
                        tension: 0.4,
                        fill: false
                    }
                ]
            },
            options: {
                responsive: true,
                maintainAspectRatio: true,
                plugins: {
                    legend: { display: true, position: 'top' }
                },
                scales: {
                    y: {
                        title: { display: true, text: 'Temperature (°C)' },
                        ticks: { callback: function(value) { return value + '°C'; } }
                    },
                    x: {
                        title: { display: true, text: 'Hour (CET)' }
                    }
                }
            }
        });
        
        // Feb 25 Chart
        const ctx25 = document.getElementById('chart25').getContext('2d');
        new Chart(ctx25, {
            type: 'line',
            data: {
                labels: """ + json.dumps([f"{h:.1f}h" for h in feb25_data["hours"]]) + """,
                datasets: [
                    {
                        label: 'METAR (Actual)',
                        data: """ + json.dumps(feb25_data["metar"]) + """,
                        borderColor: '#f44336',
                        backgroundColor: 'rgba(244, 67, 54, 0.1)',
                        borderWidth: 3,
                        tension: 0.4,
                        fill: false
                    },
                    {
                        label: 'Daily High',
                        data: """ + json.dumps(feb25_data["daily_high"]) + """,
                        borderColor: '#ff9800',
                        backgroundColor: 'rgba(255, 152, 0, 0.1)',
                        borderWidth: 2,
                        borderDash: [5, 5],
                        tension: 0.4,
                        fill: false
                    },
                    {
                        label: 'SYNOP (Secondary)',
                        data: """ + json.dumps(feb25_data["synop"]) + """,
                        borderColor: '#4caf50',
                        backgroundColor: 'rgba(76, 175, 80, 0.1)',
                        borderWidth: 2,
                        tension: 0.4,
                        fill: false,
                        spanGaps: true
                    },
                    {
                        label: 'OpenMeteo (Forecast)',
                        data: """ + json.dumps(feb25_data["om"]) + """,
                        borderColor: '#9c27b0',
                        backgroundColor: 'rgba(156, 39, 176, 0.1)',
                        borderWidth: 2,
                        borderDash: [10, 5],
                        tension: 0.4,
                        fill: false
                    }
                ]
            },
            options: {
                responsive: true,
                maintainAspectRatio: true,
                plugins: {
                    legend: { display: true, position: 'top' }
                },
                scales: {
                    y: {
                        title: { display: true, text: 'Temperature (°C)' },
                        ticks: { callback: function(value) { return value + '°C'; } }
                    },
                    x: {
                        title: { display: true, text: 'Hour (CET)' }
                    }
                }
            }
        });
    </script>
</body>
</html>"""

with open("feb24_25_comparison.html", "w", encoding="utf-8") as f:
    f.write(html)

print("Generated feb24_25_comparison.html")
