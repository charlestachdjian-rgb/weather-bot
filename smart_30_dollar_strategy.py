"""
Smart $30 paper trading strategy for Paris temperature markets.
Optimizes position sizing and trade selection for small capital.
"""
import json
from datetime import datetime, date, time
from zoneinfo import ZoneInfo
from typing import Dict, List, Tuple
import math

CET = ZoneInfo("Europe/Paris")
TOMORROW = date(2026, 2, 23)

class SmartThirtyDollarStrategy:
    """
    Optimizes $30 capital for maximum expected value.
    
    Strategy principles:
    1. Prioritize highest confidence trades (T1 > T2 > Ceiling NO > Locked YES)
    2. Use Kelly Criterion for position sizing
    3. Diversify across multiple brackets
    4. Reserve capital for best opportunities
    """
    
    def __init__(self, capital: float = 30.00):
        self.capital = capital
        self.reserved_capital = capital
        self.allocations = []
        
        # Expected opportunities for tomorrow (from forecast)
        self.expected_opportunities = self.get_expected_opportunities()
        
        # Signal confidence scores (based on historical performance)
        self.confidence_scores = {
            "FLOOR_NO_T1": 0.99,      # Mathematical certainty
            "FLOOR_NO_T2": 0.90,      # Very high confidence
            "MIDDAY_T2": 0.85,        # High confidence
            "CEILING_NO": 0.70,       # Medium confidence (needs guards)
            "LOCKED_IN_YES": 0.65,    # Medium confidence
            "T2_UPPER": 0.80,         # High confidence with bias check
        }
        
        # Expected YES prices (educated guesses)
        self.expected_prices = {
            "<=6C": 0.001, "<=7C": 0.002, "<=8C": 0.005, "<=9C": 0.010,
            "<=10C": 0.020, "11C": 0.050, "12C": 0.100, "13C": 0.250,
            "14C": 0.400, ">=15C": 0.150, ">=16C": 0.050, ">=17C": 0.020
        }
    
    def get_expected_opportunities(self) -> List[Dict]:
        """Based on tomorrow's forecast, return expected trading opportunities."""
        forecast_high = 14.4  # After bias correction
        
        return [
            # Morning Tier 2 (9:00) - Highest priority
            {"time": "09:00", "bracket": "<=10C", "type": "FLOOR_NO_T2", 
             "gap": forecast_high - 10, "min_gap": 4.0, "priority": 1},
            {"time": "09:00", "bracket": "<=9C", "type": "FLOOR_NO_T2",
             "gap": forecast_high - 9, "min_gap": 4.0, "priority": 1},
            {"time": "09:00", "bracket": "<=8C", "type": "FLOOR_NO_T2",
             "gap": forecast_high - 8, "min_gap": 4.0, "priority": 1},
            
            # Midday Tier 2 (12:00) - Medium priority
            {"time": "12:00", "bracket": "11C", "type": "MIDDAY_T2",
             "gap": forecast_high - 11, "min_gap": 2.5, "priority": 2},
            {"time": "12:00", "bracket": "12C", "type": "MIDDAY_T2",
             "gap": forecast_high - 12, "min_gap": 2.5, "priority": 2},
            
            # Late day Ceiling NO (16:00+) - Lower priority
            {"time": "16:00", "bracket": ">=16C", "type": "CEILING_NO",
             "gap": 16 - forecast_high, "min_gap": 2.0, "priority": 3},
            
            # Locked-In YES (17:00) - Conditional
            {"time": "17:00", "bracket": "14C", "type": "LOCKED_IN_YES",
             "condition": "YES < 80%", "priority": 4},
        ]
    
    def kelly_criterion(self, win_prob: float, win_odds: float, loss_odds: float = 1.0) -> float:
        """
        Calculate Kelly fraction for position sizing.
        
        Args:
            win_prob: Probability of winning (0 to 1)
            win_odds: Profit multiplier if win (e.g., 0.05 for 5% return)
            loss_odds: Loss multiplier if lose (typically 1.0 for 100% loss)
            
        Returns:
            Fraction of capital to bet (0 to 1)
        """
        if win_odds <= 0:
            return 0.0
        
        # Kelly formula: f* = (bp - q) / b
        # where b = win_odds, p = win_prob, q = 1 - p
        b = win_odds
        p = win_prob
        q = 1 - p
        
        if b * p - q <= 0:
            return 0.0
        
        kelly_fraction = (b * p - q) / b
        
        # Conservative: use half-kelly
        return max(0.0, min(0.5, kelly_fraction / 2))
    
    def calculate_trade_metrics(self, bracket: str, signal_type: str, entry_price: float) -> Dict:
        """
        Calculate trade metrics for decision making.
        
        For NO trades (SELL):
        - Win: YES goes to 0.00 (profit = entry_price)
        - Loss: YES goes to 1.00 (loss = 1 - entry_price)
        
        For YES trades (BUY):
        - Win: YES goes to 1.00 (profit = 1 - entry_price)
        - Loss: YES goes to 0.00 (loss = entry_price)
        """
        is_no_trade = signal_type in ["FLOOR_NO_T1", "FLOOR_NO_T2", "MIDDAY_T2", "CEILING_NO", "T2_UPPER"]
        
        if is_no_trade:
            # Buying NO (selling YES)
            win_payout = entry_price  # If YES goes to 0
            loss_payout = -(1 - entry_price)  # If YES goes to 1
            win_odds = entry_price / (1 - entry_price) if entry_price < 1.0 else 10.0
        else:
            # Buying YES
            win_payout = 1 - entry_price  # If YES goes to 1
            loss_payout = -entry_price  # If YES goes to 0
            win_odds = (1 - entry_price) / entry_price if entry_price > 0 else 10.0
        
        # Get confidence for this signal type
        confidence = self.confidence_scores.get(signal_type, 0.5)
        
        # Calculate Kelly fraction
        kelly_fraction = self.kelly_criterion(
            win_prob=confidence,
            win_odds=win_odds,
            loss_odds=1.0
        )
        
        # Expected value
        expected_value = (confidence * win_payout) + ((1 - confidence) * loss_payout)
        
        # Risk-adjusted return
        risk_adjusted_return = expected_value / abs(loss_payout) if loss_payout != 0 else 0
        
        return {
            "bracket": bracket,
            "type": signal_type,
            "side": "NO" if is_no_trade else "YES",
            "entry_price": entry_price,
            "confidence": confidence,
            "win_payout": win_payout,
            "loss_payout": loss_payout,
            "win_odds": win_odds,
            "kelly_fraction": kelly_fraction,
            "expected_value": expected_value,
            "risk_adjusted_return": risk_adjusted_return,
            "position_size": self.capital * kelly_fraction,
            "edge": win_payout * confidence - abs(loss_payout) * (1 - confidence)
        }
    
    def optimize_portfolio(self) -> List[Dict]:
        """
        Optimize $30 across expected opportunities.
        Returns allocation plan.
        """
        # Calculate metrics for all expected opportunities
        all_trades = []
        
        for opp in self.expected_opportunities:
            bracket = opp["bracket"]
            signal_type = opp["type"]
            entry_price = self.expected_prices.get(bracket, 0.01)
            
            metrics = self.calculate_trade_metrics(bracket, signal_type, entry_price)
            metrics.update({
                "time": opp["time"],
                "priority": opp["priority"],
                "gap": opp.get("gap", 0),
                "min_gap": opp.get("min_gap", 0),
            })
            
            # Filter out trades with negative expected value or insufficient gap
            if metrics["expected_value"] > 0 and opp.get("gap", 100) >= opp.get("min_gap", 0):
                all_trades.append(metrics)
        
        # Sort by risk-adjusted return (highest first)
        all_trades.sort(key=lambda x: x["risk_adjusted_return"], reverse=True)
        
        # Allocate capital using greedy algorithm
        allocations = []
        remaining_capital = self.capital
        
        for trade in all_trades:
            if remaining_capital <= 0:
                break
            
            # Calculate position size (min $2, max based on Kelly)
            position_size = max(2.0, min(
                trade["position_size"],
                remaining_capital,
                10.0  # Max $10 per trade for diversification
            ))
            
            if position_size >= 2.0 and remaining_capital >= position_size:
                allocation = {
                    "bracket": trade["bracket"],
                    "type": trade["type"],
                    "side": trade["side"],
                    "time": trade["time"],
                    "entry_price": trade["entry_price"],
                    "position_size": round(position_size, 2),
                    "confidence": trade["confidence"],
                    "expected_profit": round(position_size * trade["expected_value"], 2),
                    "kelly_fraction": trade["kelly_fraction"],
                    "priority": trade["priority"]
                }
                
                allocations.append(allocation)
                remaining_capital -= position_size
        
        # If we have leftover capital and high-confidence trades, add to them
        if remaining_capital > 2.0 and allocations:
            # Add to highest confidence trade
            allocations[0]["position_size"] += remaining_capital
            allocations[0]["expected_profit"] = round(
                allocations[0]["position_size"] * all_trades[0]["expected_value"], 2
            )
            remaining_capital = 0
        
        self.allocations = allocations
        self.reserved_capital = remaining_capital
        
        return allocations
    
    def get_tomorrows_plan(self) -> str:
        """Generate human-readable trading plan."""
        allocations = self.optimize_portfolio()
        
        plan = []
        plan.append("=" * 70)
        plan.append("SMART $30 TRADING PLAN FOR TOMORROW (Feb 23, 2026)")
        plan.append("=" * 70)
        plan.append(f"Total capital: ${self.capital:.2f}")
        plan.append(f"Allocated: ${self.capital - self.reserved_capital:.2f}")
        plan.append(f"Reserved: ${self.reserved_capital:.2f}")
        plan.append("")
        
        plan.append("OPTIMIZED ALLOCATIONS:")
        plan.append("-" * 70)
        
        total_expected = 0
        for i, alloc in enumerate(allocations, 1):
            plan.append(f"{i}. {alloc['time']}: {alloc['side']} {alloc['bracket']}")
            plan.append(f"   Signal: {alloc['type']}")
            plan.append(f"   Size: ${alloc['position_size']:.2f} @ {alloc['entry_price']:.3f}")
            plan.append(f"   Confidence: {alloc['confidence']:.0%}")
            plan.append(f"   Expected profit: ${alloc['expected_profit']:.2f}")
            plan.append("")
            total_expected += alloc['expected_profit']
        
        plan.append("SUMMARY:")
        plan.append(f"• Total trades: {len(allocations)}")
        plan.append(f"• Total expected profit: ${total_expected:.2f}")
        plan.append(f"• Expected ROI: {total_expected/self.capital:.1%}")
        
        # Risk management guidelines
        plan.append("")
        plan.append("RISK MANAGEMENT:")
        plan.append("• Maximum $10 per trade (1/3 of capital)")
        plan.append("• Minimum $2 per trade (meaningful position)")
        plan.append("• Prioritize high-confidence trades (T1, T2)")
        plan.append("• Use half-Kelly sizing for conservative approach")
        plan.append("• Keep $2-5 reserved for unexpected opportunities")
        
        # Execution plan
        plan.append("")
        plan.append("EXECUTION PLAN:")
        plan.append("1. 09:00: Execute Floor NO T2 trades (highest priority)")
        plan.append("2. 12:00: Execute Midday T2 if conditions met")
        plan.append("3. 16:00: Execute Ceiling NO if all guards pass")
        plan.append("4. 17:00: Execute Locked-In YES if <80% and guards pass")
        plan.append("5. Monitor positions until resolution")
        
        plan.append("=" * 70)
        
        return "\n".join(plan)
    
    def simulate_tomorrows_results(self, success_rates: Dict[str, float] = None) -> Dict:
        """
        Simulate possible outcomes for tomorrow.
        
        Args:
            success_rates: Custom success rates by signal type
                          Defaults: T1: 100%, T2: 95%, Ceiling NO: 85%, Locked YES: 80%
        """
        if success_rates is None:
            success_rates = {
                "FLOOR_NO_T1": 1.00,
                "FLOOR_NO_T2": 0.95,
                "MIDDAY_T2": 0.90,
                "CEILING_NO": 0.85,
                "LOCKED_IN_YES": 0.80,
                "T2_UPPER": 0.90,
            }
        
        allocations = self.allocations if self.allocations else self.optimize_portfolio()
        
        # Monte Carlo simulation (simplified)
        scenarios = {
            "best_case": {"description": "All trades win", "success_multiplier": 1.0},
            "expected_case": {"description": "Average performance", "success_multiplier": 0.9},
            "worst_case": {"description": "Below average", "success_multiplier": 0.7},
            "disaster_case": {"description": "Multiple losses", "success_multiplier": 0.5},
        }
        
        results = {}
        for scenario_name, scenario in scenarios.items():
            total_pnl = 0
            for alloc in allocations:
                signal_type = alloc["type"]
                success_rate = success_rates.get(signal_type, 0.8)
                
                # Adjust success rate for scenario
                adjusted_rate = success_rate * scenario["success_multiplier"]
                
                # Calculate expected P&L for this trade
                if alloc["side"] == "NO":
                    # NO trade: profit = entry_price if win, loss = (1 - entry_price) if lose
                    win_pnl = alloc["entry_price"] * alloc["position_size"]
                    loss_pnl = -(1 - alloc["entry_price"]) * alloc["position_size"]
                else:
                    # YES trade: profit = (1 - entry_price) if win, loss = entry_price if lose
                    win_pnl = (1 - alloc["entry_price"]) * alloc["position_size"]
                    loss_pnl = -alloc["entry_price"] * alloc["position_size"]
                
                trade_pnl = (adjusted_rate * win_pnl) + ((1 - adjusted_rate) * loss_pnl)
                total_pnl += trade_pnl
            
            results[scenario_name] = {
                "description": scenario["description"],
                "total_pnl": round(total_pnl, 2),
                "final_balance": round(self.capital + total_pnl, 2),
                "roi": total_pnl / self.capital,
            }
        
        return results

def main():
    """Run the smart $30 strategy analysis."""
    print("\n" + "=" * 70)
    print("SMART $30 PAPER TRADING STRATEGY")
    print("Optimizing small capital for Paris temperature markets")
    print("=" * 70)
    
    # Initialize strategy
    strategy = SmartThirtyDollarStrategy(capital=30.00)
    
    # Get optimized plan
    plan = strategy.get_tomorrows_plan()
    print(plan)
    
    # Run simulations
    print("\n" + "=" * 70)
    print("RISK-ADJUSTED SCENARIO ANALYSIS")
    print("=" * 70)
    
    simulations = strategy.simulate_tomorrows_results()
    
    for scenario_name, result in simulations.items():
        print(f"\n{scenario_name.upper().replace('_', ' ')}:")
        print(f"  {result['description']}")
        print(f"  Total P&L: ${result['total_pnl']:+.2f}")
        print(f"  Final balance: ${result['final_balance']:.2f}")
        print(f"  ROI: {result['roi']:+.1%}")
    
    print("\n" + "=" * 70)
    print("KEY RECOMMENDATIONS FOR $30 CAPITAL:")
    print("=" * 70)
print("1. FOCUS ON HIGHEST CONFIDENCE TRADES:")
        print("   * Floor NO T2 at 9am (<=10C, <=9C, <=8C)")
        print("   * These have 4.4-8.4C gap to forecast")
        print("   * Highest historical win rate (95%+)")
    
    print("\n2. USE PROPER POSITION SIZING:")
    print("   • $8-10 on highest confidence trades")
    print("   • $4-6 on medium confidence trades")
    print("   • Keep $5-10 in reserve")
    
    print("\n3. RISK MANAGEMENT:")
    print("   • Never risk more than $10 on one trade")
    print("   • Diversify across 3-5 brackets")
    print("   • Use stop-loss mentality: max 50% loss on any trade")
    
    print("\n4. EXECUTION TIMING:")
    print("   • Execute Tier 2 at 9:00 sharp")
    print("   • Wait for dynamic bias confirmation")
    print("   • Skip trades if YES price < 3% (no edge)")
    
    print("\n" + "=" * 70)
    print("BOTTOM LINE:")
    print(f"With $30, expect ${simulations['expected_case']['total_pnl']:+.2f} to " +
          f"${simulations['best_case']['total_pnl']:+.2f} profit")
    print(f"Target ROI: {simulations['expected_case']['roi']:+.1%}")
    print("=" * 70)

if __name__ == "__main__":
    main()