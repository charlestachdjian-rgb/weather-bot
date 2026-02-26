#!/usr/bin/env python3
"""
Inspect the paper trading database and print summary.
"""
import sqlite3
import json
from datetime import datetime, timezone
import sys

DB_PATH = "paper_trading.db"

def main():
    if not os.path.exists(DB_PATH):
        print("Database file not found.")
        return
    
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    # Get table list
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
    tables = cursor.fetchall()
    print("Tables in database:")
    for t in tables:
        print(f"  - {t[0]}")
    
    # Check positions table
    if 'positions' in [t[0] for t in tables]:
        cursor.execute("SELECT COUNT(*) as cnt FROM positions")
        total = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) as cnt FROM positions WHERE status='OPEN'")
        open_cnt = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) as cnt FROM positions WHERE status='CLOSED'")
        closed_cnt = cursor.fetchone()[0]
        print(f"\nPositions: total={total}, open={open_cnt}, closed={closed_cnt}")
        
        # Get some sample rows
        cursor.execute("SELECT * FROM positions ORDER BY entry_time DESC LIMIT 5")
        rows = cursor.fetchall()
        print("\nRecent positions (max 5):")
        for row in rows:
            print(dict(row))
    
    # Check balance_history table
    if 'balance_history' in [t[0] for t in tables]:
        cursor.execute("SELECT COUNT(*) as cnt FROM balance_history")
        total = cursor.fetchone()[0]
        print(f"\nBalance history entries: {total}")
        cursor.execute("SELECT * FROM balance_history ORDER BY timestamp DESC LIMIT 3")
        rows = cursor.fetchall()
        print("Recent balance entries:")
        for row in rows:
            print(dict(row))
    
    conn.close()

if __name__ == "__main__":
    import os
    main()