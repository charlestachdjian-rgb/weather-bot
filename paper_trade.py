"""
Paper trading system for Paris temperature markets.
Simulates trades based on signals from weather_monitor.py.
"""
import json
import time
import os
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo
from pathlib import Path
import sqlite3
from typing import Dict, List, Optional, Tuple

CET = ZoneInfo("Europe/Paris")
TODAY = date(2026, 2, 23)

class PaperTrader:
    def __init__(self, initial_balance: float = 1000.00, trade_size: float = 100.00):
        self.initial_balance = initial_balance
        self.trade_size = trade_size
        self.balance = initial_balance
        self.positions = {}  # {position_id: {details}}
        self.trade_history = []
        self.db_path = Path(__file__).parent / "paper_trading.db"
        self.setup_database()
        
    def setup_database(self):
        """Initialize SQLite database for tracking trades."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Create tables
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS positions (
                id TEXT PRIMARY KEY,
                bracket TEXT,
                side TEXT,
                entry_price REAL,
                entry_time TEXT,
                size REAL,
                status TEXT,
                exit_price REAL,
                exit_time TEXT,
                pnl REAL
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS balance_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT,
                balance REAL,
                daily_pnl REAL
            )
        ''')
        
        conn.commit()
        conn.close()
    
    def generate_position_id(self, bracket: str, side: str) -> str:
        """Generate unique position ID."""
        timestamp = datetime.now(CET).strftime("%Y%m%d%H%M%S")
        return f"{timestamp}_{bracket}_{side}"
    
    def execute_trade(self, bracket: str, side: str, entry_price: float, 
                     signal_type: str, confidence: float) -> Optional[str]:
        """
        Execute a paper trade.
        
        Args:
            bracket: Temperature bracket (e.g., "<=10C", "14C", ">=16C")
            side: "BUY" or "SELL" (in Polymarket terms: BUY = YES, SELL = NO)
            entry_price: Entry price (0.00 to 1.00)
            signal_type: Type of signal (T1, T2, CEIL_NO, LOCKED_YES)
            confidence: Confidence score (0.0 to 1.0)
            
        Returns:
            Position ID if successful, None if failed
        """
        # Check if we have enough balance
        cost = self.trade_size * entry_price if side == "BUY" else self.trade_size * (1 - entry_price)
        
        if cost > self.balance:
            print(f"  ❌ Insufficient balance: ${cost:.2f} > ${self.balance:.2f}")
            return None
        
        # Generate position ID
        position_id = self.generate_position_id(bracket, side)
        
        # Calculate position size (adjust based on confidence)
        adjusted_size = self.trade_size * min(1.0, confidence * 1.5)
        
        # Deduct from balance
        self.balance -= cost
        
        # Record position
        position = {
            "id": position_id,
            "bracket": bracket,
            "side": side,
            "entry_price": entry_price,
            "entry_time": datetime.now(CET).isoformat(),
            "size": adjusted_size,
            "status": "OPEN",
            "signal_type": signal_type,
            "confidence": confidence,
            "cost": cost
        }
        
        self.positions[position_id] = position
        
        # Save to database
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO positions (id, bracket, side, entry_price, entry_time, size, status)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (position_id, bracket, side, entry_price, position["entry_time"], adjusted_size, "OPEN"))
        
        # Record balance
        cursor.execute('''
            INSERT INTO balance_history (timestamp, balance, daily_pnl)
            VALUES (?, ?, ?)
        ''', (position["entry_time"], self.balance, self.balance - self.initial_balance))
        
        conn.commit()
        conn.close()
        
        # Add to trade history
        self.trade_history.append({
            "time": position["entry_time"],
            "action": "OPEN",
            "position_id": position_id,
            "details": position
        })
        
        print(f"  ✅ Paper trade executed: {side} {bracket} @ {entry_price:.3f}")
        print(f"     Position: {position_id}")
        print(f"     Cost: ${cost:.2f}, Balance: ${self.balance:.2f}")
        
        return position_id
    
    def close_position(self, position_id: str, exit_price: float, 
                      resolution: Optional[str] = None) -> float:
        """
        Close a paper trade position.
        
        Args:
            position_id: ID of position to close
            exit_price: Exit price (0.00 to 1.00)
            resolution: "YES" or "NO" if known
            
        Returns:
            P&L from the trade
        """
        if position_id not in self.positions:
            print(f"  ❌ Position {position_id} not found")
            return 0.0
        
        position = self.positions[position_id]
        
        # Calculate P&L
        if position["side"] == "BUY":  # Bought YES
            pnl = (exit_price - position["entry_price"]) * position["size"]
        else:  # Sold/Bought NO
            pnl = ((1 - exit_price) - (1 - position["entry_price"])) * position["size"]
        
        # Update balance
        self.balance += position["cost"] + pnl
        
        # Update position
        position["exit_price"] = exit_price
        position["exit_time"] = datetime.now(CET).isoformat()
        position["status"] = "CLOSED"
        position["pnl"] = pnl
        position["resolution"] = resolution
        
        # Update database
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
            UPDATE positions 
            SET exit_price = ?, exit_time = ?, status = ?, pnl = ?
            WHERE id = ?
        ''', (exit_price, position["exit_time"], "CLOSED", pnl, position_id))
        
        # Record balance
        cursor.execute('''
            INSERT INTO balance_history (timestamp, balance, daily_pnl)
            VALUES (?, ?, ?)
        ''', (position["exit_time"], self.balance, self.balance - self.initial_balance))
        
        conn.commit()
        conn.close()
        
        # Add to trade history
        self.trade_history.append({
            "time": position["exit_time"],
            "action": "CLOSE",
            "position_id": position_id,
            "details": position
        })
        
        result = "WIN" if pnl > 0 else "LOSS" if pnl < 0 else "BREAKEVEN"
        print(f"  📊 Position closed: {position_id}")
        print(f"     {position['side']} {position['bracket']}: {position['entry_price']:.3f} → {exit_price:.3f}")
        print(f"     P&L: ${pnl:+.2f} ({result})")
        print(f"     New balance: ${self.balance:.2f}")
        
        return pnl
    
    def get_portfolio_summary(self) -> Dict:
        """Get current portfolio summary."""
        open_positions = [p for p in self.positions.values() if p["status"] == "OPEN"]
        closed_positions = [p for p in self.positions.values() if p["status"] == "CLOSED"]
        
        total_pnl = sum(p.get("pnl", 0) for p in closed_positions)
        open_exposure = sum(p["cost"] for p in open_positions)
        
        return {
            "balance": self.balance,
            "initial_balance": self.initial_balance,
            "total_pnl": total_pnl,
            "open_positions": len(open_positions),
            "closed_positions": len(closed_positions),
            "open_exposure": open_exposure,
            "win_rate": self.calculate_win_rate()
        }
    
    def calculate_win_rate(self) -> float:
        """Calculate win rate of closed positions."""
        closed_positions = [p for p in self.positions.values() if p["status"] == "CLOSED"]
        if not closed_positions:
            return 0.0
        
        wins = sum(1 for p in closed_positions if p.get("pnl", 0) > 0)
        return wins / len(closed_positions)
    
    def print_summary(self):
        """Print portfolio summary."""
        summary = self.get_portfolio_summary()
        
        print("\n" + "=" * 60)
        print("PAPER TRADING PORTFOLIO SUMMARY")
        print("=" * 60)
        print(f"Balance: ${summary['balance']:.2f} (Initial: ${summary['initial_balance']:.2f})")
        print(f"Total P&L: ${summary['total_pnl']:+.2f}")
        print(f"Open positions: {summary['open_positions']}")
        print(f"Closed positions: {summary['closed_positions']}")
        print(f"Win rate: {summary['win_rate']:.1%}")
        print(f"Open exposure: ${summary['open_exposure']:.2f}")
        
        if self.positions:
            print("\nOpen Positions:")
            for pos_id, position in self.positions.items():
                if position["status"] == "OPEN":
                    print(f"  {position['bracket']}: {position['side']} @ {position['entry_price']:.3f}")
        
        print("=" * 60)

class SignalProcessor:
    """Process signals from weather_monitor.py for paper trading."""
    
    def __init__(self, trader: PaperTrader):
        self.trader = trader
        self.processed_signals = set()
        self.log_file = Path(__file__).parent / "weather_log.jsonl"
        
        # Signal to trade mapping
        self.signal_mapping = {
            "FLOOR_NO_CERTAIN": {"side": "SELL", "confidence": 1.0},
            "FLOOR_NO_FORECAST": {"side": "SELL", "confidence": 0.9},
            "T2_UPPER": {"side": "SELL", "confidence": 0.8},
            "MIDDAY_T2": {"side": "SELL", "confidence": 0.85},
            # GUARANTEED_NO_CEIL: dormant in weather_monitor.py (collecting data)
            # LOCKED_IN_YES: removed (too risky, never fired)
            # SUM_OVERPRICED/UNDERPRICED: removed (don't predict winners)
        }
    
    def process_new_signals(self):
        """Check for new signals in the log file and execute trades."""
        if not self.log_file.exists():
            return
        
        try:
            with open(self.log_file, 'r', encoding='utf-8') as f:
                lines = f.readlines()
            
            for line in lines:
                if not line.strip():
                    continue
                
                try:
                    data = json.loads(line)
                    
                    # Check if it's a signal
                    if data.get("event") != "signal":
                        continue
                    
                    # Check if it's today
                    ts = datetime.fromisoformat(data.get("ts", "")).astimezone(CET)
                    if ts.date() != TODAY:
                        continue
                    
                    # Generate signal key for deduplication
                    signal_key = f"{data.get('type')}_{data.get('range')}_{ts.isoformat()}"
                    
                    if signal_key in self.processed_signals:
                        continue
                    
                    # Process the signal
                    self.process_signal(data, ts)
                    self.processed_signals.add(signal_key)
                    
                except json.JSONDecodeError:
                    continue
                    
        except Exception as e:
            print(f"Error processing signals: {e}")
    
    def process_signal(self, signal_data: Dict, timestamp: datetime):
        """Process a single signal and execute paper trade."""
        signal_type = signal_data.get("type")
        bracket = signal_data.get("range", "").replace("°C", "C")
        our_side = signal_data.get("our_side")
        entry_price = signal_data.get("entry_price")
        
        if not entry_price or entry_price <= 0:
            print(f"  ⚠️  Skipping signal {signal_type} on {bracket}: invalid price")
            return
        
        # Map signal to trade parameters
        if signal_type in self.signal_mapping:
            mapping = self.signal_mapping[signal_type]
            side = mapping["side"]
            confidence = mapping["confidence"]
        else:
            # Default mapping based on our_side
            side = "BUY" if our_side == "YES" else "SELL"
            confidence = 0.7
        
        print(f"\n📡 Processing signal: {signal_type} on {bracket}")
        print(f"   Time: {timestamp.strftime('%H:%M')}, Side: {side}, Price: {entry_price:.3f}")
        
        # Execute paper trade
        position_id = self.trader.execute_trade(
            bracket=bracket,
            side=side,
            entry_price=entry_price,
            signal_type=signal_type,
            confidence=confidence
        )
        
        if position_id:
            print(f"   ✅ Paper trade executed successfully")

def simulate_tomorrows_trades():
    """
    Simulate expected trades for tomorrow based on forecast.
    This is for planning purposes.
    """
    print("=" * 70)
    print("TOMORROW'S PAPER TRADING SIMULATION (Feb 23, 2026)")
    print("=" * 70)
    
    trader = PaperTrader(initial_balance=1000.00, trade_size=100.00)
    
    # Expected signals based on forecast
    expected_trades = [
        {"time": "09:00", "bracket": "<=10C", "side": "SELL", "price": 0.02, "type": "FLOOR_NO_FORECAST", "confidence": 0.9},
        {"time": "09:00", "bracket": "<=9C", "side": "SELL", "price": 0.01, "type": "FLOOR_NO_FORECAST", "confidence": 0.9},
        {"time": "09:00", "bracket": "<=8C", "side": "SELL", "price": 0.005, "type": "FLOOR_NO_FORECAST", "confidence": 0.9},
        {"time": "12:00", "bracket": "11C", "side": "SELL", "price": 0.05, "type": "MIDDAY_T2", "confidence": 0.85},
        {"time": "12:00", "bracket": "12C", "side": "SELL", "price": 0.10, "type": "MIDDAY_T2", "confidence": 0.8},
        {"time": "16:00", "bracket": ">=16C", "side": "SELL", "price": 0.05, "type": "GUARANTEED_NO_CEIL", "confidence": 0.75},
        {"time": "17:00", "bracket": "14C", "side": "BUY", "price": 0.60, "type": "LOCKED_IN_YES", "confidence": 0.7},
    ]
    
    print("\nExpected Trades:")
    print("-" * 70)
    
    total_cost = 0
    for trade in expected_trades:
        cost = trader.trade_size * trade["price"] if trade["side"] == "BUY" else trader.trade_size * (1 - trade["price"])
        total_cost += cost
        print(f"{trade['time']}: {trade['side']} {trade['bracket']} @ {trade['price']:.3f}")
        print(f"  Cost: ${cost:.2f}, Type: {trade['type']}, Confidence: {trade['confidence']:.0%}")
    
    print(f"\nTotal expected exposure: ${total_cost:.2f}")
    print(f"Remaining balance: ${trader.initial_balance - total_cost:.2f}")
    
    # Simulate outcomes
    print("\nSimulated Outcomes (assuming all correct):")
    print("-" * 70)
    
    total_pnl = 0
    for trade in expected_trades:
        if trade["side"] == "SELL":
            # NO trades: profit = entry price (since YES goes to 0)
            pnl = trade["price"] * trader.trade_size
        else:
            # YES trades: profit = (1 - entry price) (since YES goes to 1)
            pnl = (1 - trade["price"]) * trader.trade_size
        
        total_pnl += pnl
        print(f"{trade['bracket']}: ${pnl:+.2f}")
    
    print(f"\nTotal simulated P&L: ${total_pnl:+.2f}")
    print(f"Final balance: ${trader.initial_balance + total_pnl:.2f}")
    print(f"ROI: {total_pnl / trader.initial_balance:.1%}")
    
    return trader

def main():
    """Main paper trading system."""
    import argparse
    
    parser = argparse.ArgumentParser(description='Paper trading for Paris temperature markets')
    parser.add_argument('--mode', choices=['simulate', 'live', 'summary'], default='simulate',
                       help='Operation mode: simulate (default), live, or summary')
    parser.add_argument('--balance', type=float, default=1000.00,
                       help='Initial paper balance (default: 1000.00)')
    parser.add_argument('--trade-size', type=float, default=100.00,
                       help='Trade size per position (default: 100.00)')
    
    args = parser.parse_args()
    
    if args.mode == 'simulate':
        print("Running simulation of tomorrow's expected trades...")
        trader = simulate_tomorrows_trades()
        
    elif args.mode == 'live':
        print("Starting live paper trading mode...")
        print("Will monitor weather_log.jsonl for signals and execute paper trades.")
        print(f"Initial balance: ${args.balance:.2f}, Trade size: ${args.trade_size:.2f}")
        
        trader = PaperTrader(initial_balance=args.balance, trade_size=args.trade_size)
        processor = SignalProcessor(trader)
        
        try:
            while True:
                print(f"\n[{datetime.now(CET).strftime('%H:%M:%S')}] Checking for new signals...")
                processor.process_new_signals()
                trader.print_summary()
                time.sleep(60)  # Check every minute
        except KeyboardInterrupt:
            print("\n\nPaper trading stopped by user.")
            trader.print_summary()
    
    elif args.mode == 'summary':
        trader = PaperTrader(initial_balance=args.balance, trade_size=args.trade_size)
        trader.print_summary()

if __name__ == "__main__":
    main()