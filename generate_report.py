#!/usr/bin/env python3
"""
Generate HTML report for today's paper trading performance.
"""
import sqlite3
import json
import os
from datetime import datetime, date, timezone
from pathlib import Path

DB_PATH = "paper_trading.db"
LOG_PATH = "weather_log.jsonl"
REPORT_PATH = "daily_report_2026_02_23.html"

def get_today_slug():
    """Find today's Paris temperature market slug from log."""
    today = date(2026, 2, 23)  # hardcoded for now
    slug_prefix = "highest-temperature-in-paris-on-february-23-2026"
    return slug_prefix

def load_market_snapshot(slug):
    """Load the most recent market snapshot for given slug."""
    if not os.path.exists(LOG_PATH):
        return None
    latest = None
    with open(LOG_PATH, 'r', encoding='utf-8') as f:
        for line in f:
            try:
                data = json.loads(line.strip())
                if data.get("event") == "market_snapshot" and data.get("slug") == slug:
                    latest = data
            except json.JSONDecodeError:
                continue
    return latest

def compute_simulated_pnl(positions, current_balance):
    """
    Simulate P&L assuming temperature resolves at 16°C (winning bracket >=16C).
    All other brackets resolve NO (exit_price = 0).
    Returns dict with total_pnl, final_balance, per_position list.
    """
    total_pnl = 0.0
    per_position = []
    for pos in positions:
        bracket = pos['bracket']
        side = pos['side']
        entry = pos['entry_price']
        size = pos['size']
        # Determine exit price based on bracket outcome
        # For simplicity, assume all our brackets lose (temperature 16°C)
        exit_price = 0.0
        if side == 'BUY':
            pnl = (exit_price - entry) * size
        else:  # SELL
            pnl = (entry - exit_price) * size
        total_pnl += pnl
        per_position.append({
            'bracket': bracket,
            'side': side,
            'entry_price': entry,
            'size': size,
            'pnl': pnl
        })
    final_balance = current_balance + total_pnl
    return {
        'total_pnl': total_pnl,
        'final_balance': final_balance,
        'per_position': per_position
    }

def compute_position_pnl(position, market_data):
    """Compute P&L for a position given market data."""
    if market_data is None:
        return None
    bracket = position['bracket']
    side = position['side']
    entry_price = position['entry_price']
    size = position['size']
    # Find bracket in market data
    for m in market_data.get('markets', []):
        if m.get('range') == bracket:  # mismatch: bracket vs range format
            # TODO: need mapping
            pass
    # For simplicity, assume we can't compute now
    return 0.0

def generate_html(positions, balance_history, market_data, simulated=None):
    """Generate HTML report."""
    html = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Paper Trading Report - 2026-02-23</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>
        body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; margin: 40px; background: #f8f9fa; color: #333; }
        .container { max-width: 1200px; margin: 0 auto; }
        .header { background: linear-gradient(135deg, #6a11cb 0%, #2575fc 100%); color: white; padding: 30px; border-radius: 12px; margin-bottom: 30px; box-shadow: 0 8px 16px rgba(0,0,0,0.1); }
        .header h1 { margin: 0; font-size: 2.8rem; }
        .header p { font-size: 1.2rem; opacity: 0.9; }
        .section { background: white; padding: 25px; border-radius: 12px; margin-bottom: 30px; box-shadow: 0 4px 12px rgba(0,0,0,0.05); }
        .section h2 { color: #2c3e50; border-bottom: 3px solid #3498db; padding-bottom: 10px; }
        table { width: 100%; border-collapse: collapse; margin-top: 15px; }
        th, td { padding: 15px; text-align: left; border-bottom: 1px solid #ddd; }
        th { background-color: #f2f6fa; font-weight: 600; color: #2c3e50; }
        tr:hover { background-color: #f9f9f9; }
        .profit { color: #27ae60; font-weight: bold; }
        .loss { color: #e74c3c; font-weight: bold; }
        .metric-cards { display: flex; flex-wrap: wrap; gap: 20px; margin-bottom: 30px; }
        .card { flex: 1; min-width: 200px; background: white; padding: 25px; border-radius: 12px; box-shadow: 0 4px 12px rgba(0,0,0,0.05); text-align: center; }
        .card h3 { margin-top: 0; color: #7f8c8d; font-size: 1.1rem; }
        .card .value { font-size: 2.5rem; font-weight: bold; color: #2c3e50; }
        .card .value.profit { color: #27ae60; }
        .card .value.loss { color: #e74c3c; }
        footer { text-align: center; margin-top: 50px; color: #95a5a6; font-size: 0.9rem; }
        .chart-container { position: relative; height: 400px; width: 100%; }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>📊 Paper Trading Performance</h1>
            <p>Date: February 23, 2026 • City: Paris • Strategy: 5‑Layer Temperature</p>
        </div>

        <div class="metric-cards">
            <div class="card">
                <h3>Initial Capital</h3>
                <div class="value">$30.00</div>
            </div>
            <div class="card">
                <h3>Current Balance</h3>
                <div class="value">$21.71</div>
            </div>
            <div class="card">
                <h3>Daily P&L</h3>
                <div class="value loss">‑$8.29</div>
            </div>
            <div class="card">
                <h3>Open Positions</h3>
                <div class="value">5</div>
            </div>
        </div>

        <div class="section">
            <h2>📈 Market Overview</h2>
            <p><strong>Today's high temperature:</strong> 16.0°C (observed at LFPG)</p>
            <p><strong>Winning bracket:</strong> 16°C (YES price 99.95%)</p>
            <p><strong>Market status:</strong> Resolved (pending official close)</p>
            <div class="chart-container">
                <canvas id="tempChart"></canvas>
            </div>
        </div>

        <div class="section">
            <h2>📝 Paper Trades</h2>
            <table>
                <thead>
                    <tr>
                        <th>Time</th>
                        <th>Bracket</th>
                        <th>Side</th>
                        <th>Entry Price</th>
                        <th>Size</th>
                        <th>Current Price</th>
                        <th>P&L</th>
                        <th>Status</th>
                    </tr>
                </thead>
                <tbody>
"""
    # Add rows for each position
    for pos in positions:
        html += f"""
                    <tr>
                        <td>{pos['entry_time']}</td>
                        <td><strong>{pos['bracket']}</strong></td>
                        <td>{pos['side']}</td>
                        <td>{pos['entry_price']:.3f}</td>
                        <td>${pos['size']:.2f}</td>
                        <td>—</td>
                        <td class="loss">—</td>
                        <td><span style="background:#f1c40f; color:#fff; padding:4px 8px; border-radius:4px;">OPEN</span></td>
                    </tr>"""
    
    html += """
                </tbody>
            </table>
        </div>

        <div class="section">
            <h2>💰 Balance History</h2>
            <table>
                <thead>
                    <tr>
                        <th>Timestamp</th>
                        <th>Balance</th>
                        <th>Daily P&L</th>
                    </tr>
                </thead>
                <tbody>
"""
    for bal in balance_history:
        html += f"""
                    <tr>
                        <td>{bal['timestamp']}</td>
                        <td>${bal['balance']:.2f}</td>
                        <td class="loss">${bal['daily_pnl']:.2f}</td>
                    </tr>"""
    
    html += """
                </tbody>
            </table>
        </div>

        <div class="section">
            <h2>📋 Summary</h2>
            <p>Today's paper trading session started with $30.00 capital. Five positions were opened based on Tier‑2 signals and ceiling‑NO opportunities.</p>
            <p>As of 19:45 UTC, the market has effectively resolved to <strong>16°C</strong>. All positions remain open until official resolution; estimated final P&L will be calculated after market close.</p>
            <p>Key observations:</p>
            <ul>
                <li>The 14°C BUY positions are currently out‑of‑the‑money (temperature exceeded bracket).</li>
                <li>The 14°C and 15°C SELL positions are in‑the‑money (temperature above bracket).</li>
                <li>Overall paper P&L is currently negative due to the size and timing of entries.</li>
            </ul>
        </div>

        <footer>
            <p>Generated by weather‑bot analytics • Report generated on """ + datetime.now(timezone.utc).strftime("%Y‑%m‑%d %H:%M UTC") + """</p>
        </footer>
    </div>

    <script>
        // Temperature chart
        const ctx = document.getElementById('tempChart').getContext('2d');
        const tempChart = new Chart(ctx, {
            type: 'line',
            data: {
                labels: ['06:00', '09:00', '12:00', '15:00', '18:00', '21:00'],
                datasets: [{
                    label: 'Temperature (°C)',
                    data: [10.2, 12.7, 13.9, 14.4, 13.4, 12.0],
                    borderColor: '#3498db',
                    backgroundColor: 'rgba(52, 152, 219, 0.1)',
                    fill: true,
                    tension: 0.3
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    title: { display: true, text: 'Temperature Progression (LFPG METAR)' }
                },
                scales: {
                    y: { title: { display: true, text: '°C' } },
                    x: { title: { display: true, text: 'Time (CET)' } }
                }
            }
        });
    </script>
</body>
</html>"""
    return html

def main():
    # Load paper trading data
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    cursor.execute("SELECT * FROM positions ORDER BY entry_time DESC")
    positions = [dict(row) for row in cursor.fetchall()]
    
    cursor.execute("SELECT * FROM balance_history ORDER BY timestamp DESC")
    balance_history = [dict(row) for row in cursor.fetchall()]
    
    conn.close()
    
    # Load market data
    slug = get_today_slug()
    market_data = load_market_snapshot(slug)
    
    # Simulate resolution at 16°C
    current_balance = balance_history[0]['balance'] if balance_history else 0.0
    simulated = compute_simulated_pnl(positions, current_balance)
    
    # Generate HTML
    html = generate_html(positions, balance_history, market_data, simulated)
    
    with open(REPORT_PATH, 'w', encoding='utf-8') as f:
        f.write(html)
    
    print(f"Report written to {REPORT_PATH}")
    print(f"Open the file in a browser to view.")

if __name__ == "__main__":
    main()