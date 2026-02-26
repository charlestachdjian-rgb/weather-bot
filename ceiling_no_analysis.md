# GUARANTEED_NO_CEIL Analysis

## What It Does

**Logic:** After 4 PM (LATE_DAY_HOUR = 16), if:
- A bracket's floor is ≥2°C above the current daily high
- YES price is still > 3%
- All 5 guards pass
→ Buy NO on that bracket (bet it won't be reached)

**Example:** At 4 PM, daily high is 5°C, and the "≥8°C" bracket is priced at 7% YES
- Gap: 8 - 5 = 3°C (exceeds 2°C threshold)
- Logic: "Temperature can't climb 3°C in the last few hours"
- Trade: Buy NO at 93 cents

## Historical Performance (8 Days)

### Signals Generated:
- **Feb 15:** 2 CEIL signals (5°C and ≥7°C at 4 PM)
- **Feb 18:** 1 CEIL signal (≥8°C at 4 PM)
- **Total:** 3 signals

### What Happened:

#### Feb 15 - BOTH Would Have Lost
**Signal 1: 5°C CEIL at 4 PM**
- Daily high at 4 PM: 3°C
- Gap: 5 - 3 = 2°C (meets threshold)
- Actual outcome: Temp reached 9°C by midnight
- **Would have lost $100** (but guards blocked it)

**Signal 2: ≥7°C CEIL at 4 PM**
- Daily high at 4 PM: 3°C  
- Gap: 7 - 3 = 4°C (big gap!)
- Actual outcome: Temp reached 9°C by midnight
- **Would have lost $100** (but guards blocked it)

**Why guards blocked:**
- OM peak hour: 22:00 (10 PM) - peak was AFTER signal time
- OM forecasted 7.8°C later in evening
- METAR and SYNOP both showing RISING trends
- SYNOP velocity: +1.0°C/3h (climbing fast)

#### Feb 18 - Would Have Lost
**Signal: ≥8°C CEIL at 4 PM**
- Daily high at 4 PM: 5°C
- Gap: 8 - 5 = 3°C (big gap!)
- Actual outcome: Temp reached 9°C by 11 PM
- **Would have lost $100** (but guards blocked it)

**Why guards blocked:**
- OM peak hour: 22:00 (10 PM) - peak was AFTER signal time
- OM forecasted 8.1°C later in evening
- SYNOP and OM showing RISING trends
- SYNOP velocity: +0.9°C/3h

## The Pattern

**All 3 signals would have lost $300 total.**

Both days had the same pattern:
1. Temperature was low in afternoon (3-5°C)
2. Large gap to upper brackets (2-4°C)
3. Signal fired at 4 PM saying "can't reach those brackets"
4. Late-evening warm front pushed temps up 4-6°C more
5. Upper brackets were reached by 10-11 PM

## Why The Guards Worked Perfectly

The 5 guards caught the danger signs:

1. **OM Peak Hour:** OpenMeteo predicted peak at 10 PM, not 4 PM
2. **OM Remaining Max:** Forecast showed higher temps coming later
3. **Rising Trends:** METAR/SYNOP/OM all showed temps still climbing
4. **SYNOP Velocity:** +0.9 to +1.0°C per 3 hours (significant climb)
5. **OM High vs Bracket:** OM predicted highs near or above the "unreachable" brackets

## The Problem with GUARANTEED_NO_CEIL

**The Assumption:** "After 4 PM, temperature can't climb 2+ degrees"

**Why It Fails:**
- Paris winter has late-evening warm fronts
- 4 PM is too early - temps can still rise significantly
- Even with 2°C buffer, temps climbed 4-6°C after 4 PM on these days
- The "guarantee" isn't guaranteed

**The Risk:**
- One wrong trade = -$100
- Would need 20-30 winning Floor NO trades to recover
- 3 signals in 8 days, all would have lost = -$300

## Should We Keep It?

### Arguments FOR:
- Guards have been 100% effective (blocked all 3 dangerous signals)
- Could work on days with stable/falling temps after 4 PM
- Low frequency (3 signals in 8 days)

### Arguments AGAINST:
- **0% success rate** - all 3 signals would have lost without guards
- **No proven edge** - never executed a winning trade
- **High risk** - one failure = -$100
- **Guards are reactive** - they catch danger signs, but the signal itself is flawed
- **Better alternative exists** - Floor NO strategies have 100% win rate

## Comparison to Floor NO

**Floor NO (T1/T2):**
- 50 trades, 50/50 correct
- +$36.74 profit
- Zero losses
- Logic: Temperature already crossed threshold (mathematical certainty)

**GUARANTEED_NO_CEIL:**
- 0 trades executed (all blocked)
- $0 profit
- Would have been -$300 without guards
- Logic: Temperature won't climb 2°C after 4 PM (assumption, not certainty)

## Recommendation

**REMOVE GUARANTEED_NO_CEIL**

Reasons:
1. 0% historical success rate (all 3 would have lost)
2. No proven edge - never executed a winning trade
3. Relies on assumption ("temps won't rise after 4 PM") not certainty
4. Guards are doing all the work - the signal itself is broken
5. Floor NO strategies already provide consistent profit with zero risk

**The Core Issue:** 
GUARANTEED_NO_CEIL tries to predict the future ("temps won't rise"). Floor NO observes the past ("temps already crossed"). Predicting is gambling. Observing is certainty.

## Alternative

If you want to keep a late-day strategy:
- Move trigger time to 6 PM or 7 PM (closer to actual peak)
- Increase gap requirement to 3°C or 4°C
- Only fire when ALL sources show FLAT or FALLING trends
- Only fire when current high ≥ forecast high

But even then, it's still predicting vs observing. Floor NO is safer.
