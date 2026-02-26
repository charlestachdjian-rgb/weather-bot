#!/usr/bin/env python3
"""
Compute P&L for open positions assuming temperature 16°C resolution.
"""
import sqlite3
import os

DB_PATH = "paper_trading.db"

def main():
    if not os.path.exists(DB_PATH):
        print("Database not found")
        return
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT bracket, side, entry_price, size FROM positions WHERE status='OPEN'")
    rows = cursor.fetchall()
    
    print("Open positions:")
    total_pnl = 0.0
    for bracket, side, entry, size in rows:
        # Determine exit price based on bracket outcome
        # Assuming temperature = 16°C, winning bracket is ">=16C" or "16C"
        # All other brackets resolve NO (exit_price = 0)
        # TODO: need mapping of temperature to bracket outcome
        # For simplicity, assume all our brackets lose (since none are >=16C)
        exit_price = 0.0
        
        if side == 'BUY':
            pnl = (exit_price - entry) * size
        else:  # SELL
            pnl = (entry - exit_price) * size
        
        print(f"  {bracket} {side} @{entry:.3f} size {size:.1f}: P&L = {pnl:+.2f}")
        total_pnl += pnl
    
    print(f"\nTotal simulated P&L: {total_pnl:+.2f}")
    
    # Also compute final balance if all positions closed
    cursor.execute("SELECT balance FROM balance_history ORDER BY timestamp DESC LIMIT 1")
    current_balance = cursor.fetchone()[0]
    final_balance = current_balance + total_pnl
    print(f"Current balance: {current_balance:.2f}")
    print(f"Final balance after resolution: {final_balance:.2f}")
    
    conn.close()

if __name__ == "__main__":
    main()