#!/usr/bin/env python3
"""
Verify P&L calculations for open positions, using the exact logic from paper_trade.py.
"""
import sqlite3
import os

DB_PATH = "paper_trading.db"

def load_positions():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM positions WHERE status='OPEN'")
    rows = cursor.fetchall()
    positions = [dict(row) for row in rows]
    conn.close()
    return positions

def compute_cost(position, trade_size=5.0):
    """Compute cost as per paper_trade.py line 81."""
    side = position['side']
    entry_price = position['entry_price']
    if side == 'BUY':
        return trade_size * entry_price
    else:  # SELL
        return trade_size * (1 - entry_price)

def compute_pnl(position, exit_price=0.0):
    """Compute P&L as per paper_trade.py close_position."""
    side = position['side']
    entry = position['entry_price']
    size = position['size']
    if side == 'BUY':
        return (exit_price - entry) * size
    else:
        return (entry - exit_price) * size

def main():
    positions = load_positions()
    trade_size = 5.0  # from start_paper_trading.bat
    
    print("Position details (trade_size = $5):")
    print("="*80)
    total_cost = 0.0
    total_pnl = 0.0
    for pos in positions:
        cost = compute_cost(pos, trade_size)
        pnl = compute_pnl(pos, 0.0)
        total_cost += cost
        total_pnl += pnl
        print(f"Bracket: {pos['bracket']}, Side: {pos['side']}, Entry: {pos['entry_price']:.3f}, Size: {pos['size']:.2f}")
        print(f"  Cost: ${cost:.2f}, P&L @0.0: ${pnl:.2f}")
        print()
    
    print(f"Total cost of open positions: ${total_cost:.2f}")
    print(f"Total simulated P&L (exit_price=0): ${total_pnl:.2f}")
    
    # Compare with current balance
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT balance FROM balance_history ORDER BY timestamp DESC LIMIT 1")
    current_balance = cursor.fetchone()[0]
    conn.close()
    
    print(f"Current balance: ${current_balance:.2f}")
    print(f"Initial capital: $30.00")
    print(f"Balance if positions closed (current + P&L): ${current_balance + total_pnl:.2f}")
    
    # Verify that cost matches balance deduction?
    # The balance after opening a position should be initial_balance - cumulative cost.
    # Let's compute cumulative cost from all positions (including closed?) but we only have open.
    # For simplicity, we can compute expected balance = 30 - total_cost (assuming no other trades).
    # However there may be multiple trades with overlapping costs; the balance history tracks.
    # Let's fetch the first balance entry (initial) and see.
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT balance FROM balance_history ORDER BY timestamp ASC LIMIT 1")
    initial_balance = cursor.fetchone()[0]
    conn.close()
    print(f"First recorded balance: ${initial_balance:.2f}")
    
if __name__ == "__main__":
    main()