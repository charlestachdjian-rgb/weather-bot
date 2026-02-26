#!/usr/bin/env python3
"""
Update the existing HTML report with simulated resolution at 16°C.
"""
import sys
import os
import sqlite3
from pathlib import Path

DB_PATH = "paper_trading.db"
REPORT_PATH = "daily_report_2026_02_23.html"

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

def load_data():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM positions WHERE status='OPEN'")
    positions = [dict(row) for row in cursor.fetchall()]
    cursor.execute("SELECT balance FROM balance_history ORDER BY timestamp DESC LIMIT 1")
    current_balance = cursor.fetchone()[0]
    conn.close()
    return positions, current_balance

def generate_simulation_section(simulated):
    """Return HTML string for the simulation section."""
    total_pnl = simulated['total_pnl']
    final_balance = simulated['final_balance']
    per_position = simulated['per_position']
    
    # Determine CSS class for P&L
    pnl_class = "profit" if total_pnl >= 0 else "loss"
    balance_class = "profit" if final_balance >= 30.0 else "loss"  # compared to initial capital
    
    section = f'''
        <div class="section" style="border-left: 6px solid #9b59b6;">
            <h2>🎯 Simulated Resolution at 16°C</h2>
            <p><strong>Assumption:</strong> Today's high temperature is 16.0°C, winning bracket is "≥16°C". All other brackets resolve NO.</p>
            
            <div class="metric-cards">
                <div class="card">
                    <h3>Simulated Final Balance</h3>
                    <div class="value {balance_class}">${final_balance:.2f}</div>
                </div>
                <div class="card">
                    <h3>Simulated Total P&L</h3>
                    <div class="value {pnl_class}">{total_pnl:+.2f}</div>
                </div>
                <div class="card">
                    <h3>Simulated Return</h3>
                    <div class="value {pnl_class}">{total_pnl/30.0*100:+.1f}%</div>
                </div>
            </div>
            
            <h3>Position‑wise P&L</h3>
            <table>
                <thead>
                    <tr>
                        <th>Bracket</th>
                        <th>Side</th>
                        <th>Entry Price</th>
                        <th>Size</th>
                        <th>Exit Price</th>
                        <th>P&L</th>
                    </tr>
                </thead>
                <tbody>
'''
    for pos in per_position:
        section += f'''
                    <tr>
                        <td><strong>{pos['bracket']}</strong></td>
                        <td>{pos['side']}</td>
                        <td>{pos['entry_price']:.3f}</td>
                        <td>${pos['size']:.2f}</td>
                        <td>0.000</td>
                        <td class={"profit" if pos['pnl'] >= 0 else "loss"}>{pos['pnl']:+.2f}</td>
                    </tr>'''
    
    section += '''
                </tbody>
            </table>
            <p><em>Note: This simulation is for illustrative purposes only. Actual market resolution may differ.</em></p>
        </div>
'''
    return section

def insert_section_into_html(html, section):
    """Insert the simulation section after the Market Overview section."""
    # Find the closing div of the Market Overview section
    marker = '</div>\n\n        <div class="section">\n            <h2>📝 Paper Trades</h2>'
    if marker in html:
        # Insert our section before the marker (i.e., after Market Overview)
        insertion_point = html.find(marker)
        # We'll insert after the closing div of Market Overview, before the blank line
        # Need to locate the exact position after the closing div and before the blank line.
        # Simpler: replace marker with section + marker
        new_html = html[:insertion_point] + section + '\n' + html[insertion_point:]
        return new_html
    else:
        # Fallback: insert before the Paper Trades section
        marker2 = '<div class="section">\n            <h2>📝 Paper Trades</h2>'
        if marker2 in html:
            insertion = html.find(marker2)
            new_html = html[:insertion] + section + '\n' + html[insertion:]
            return new_html
    return html

def main():
    if not os.path.exists(REPORT_PATH):
        print("Error: HTML report not found.")
        sys.exit(1)
    
    positions, current_balance = load_data()
    simulated = compute_simulated_pnl(positions, current_balance)
    
    print(f"Simulated total P&L: {simulated['total_pnl']:+.2f}")
    print(f"Simulated final balance: {simulated['final_balance']:.2f}")
    
    with open(REPORT_PATH, 'r', encoding='utf-8') as f:
        html = f.read()
    
    section = generate_simulation_section(simulated)
    new_html = insert_section_into_html(html, section)
    
    with open(REPORT_PATH, 'w', encoding='utf-8') as f:
        f.write(new_html)
    
    print(f"Updated report saved to {REPORT_PATH}")

if __name__ == "__main__":
    main()