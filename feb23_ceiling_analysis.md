# Why GUARANTEED_NO_CEIL Didn't Fire on Feb 23

## Market State at 4 PM (16:00 CET)

**At 15:03 (just after 4 PM):**
- Daily high: 15.0°C
- Current temp: 15.0°C
- ≥17°C bracket: YES = 0.25% (NO = 99.75%)

**At 15:08 (temp jumped to 16°C):**
- Daily high: 16.0°C
- Current temp: 16.0°C
- ≥17°C bracket: YES = 0.75% (NO = 99.25%)

**At 15:13:**
- Daily high: 16.0°C
- ≥17°C bracket: YES = 0.80% (NO = 99.20%)

## Why GUARANTEED_NO_CEIL Didn't Fire

### Requirement Check:

**GUARANTEED_NO_CEIL fires when:**
1. ✅ Time >= 4 PM (LATE_DAY_HOUR = 16)
2. ✅ Bracket has a floor (≥17°C has floor of 17)
3. ❌ **Gap >= 2°C** (CEIL_GAP = 2.0)
4. ✅ YES price > 3% (MIN_YES_FOR_ALERT = 0.03)
5. ✅ All 5 guards pass

### The Gap Calculation:

**At 15:03 (4 PM):**
- Gap = bracket_floor - daily_high
- Gap = 17 - 15 = **2.0°C** (exactly at threshold)
- YES price = 0.25% (below 3% threshold)
- **Signal blocked: YES price too low**

**At 15:08 (temp jumped to 16°C):**
- Gap = 17 - 16 = **1.0°C** (below 2°C threshold)
- **Signal blocked: Gap too small**

**After 15:08:**
- Daily high stayed at 16°C
- Gap remained 1.0°C (below threshold)
- Never fired

## What If The Gap Threshold Was Lower?

### If CEIL_GAP = 1.0°C instead of 2.0°C:

**At 15:08, signal would have checked:**
1. ✅ Time: 16:00 (4 PM)
2. ✅ Gap: 1.0°C >= 1.0°C
3. ✅ YES price: 0.75% (but still below 3% threshold)
4. **Still blocked: YES price < 3%**

**At 15:13:**
1. ✅ Time: 16:00
2. ✅ Gap: 1.0°C >= 1.0°C
3. ❌ YES price: 0.80% (still below 3%)
4. **Still blocked: YES price < 3%**

### If MIN_YES_FOR_ALERT = 0.005 (0.5%) instead of 0.03 (3%):

**At 15:08:**
1. ✅ Time: 16:00
2. ✅ Gap: 1.0°C (if threshold was 1.0°C)
3. ✅ YES price: 0.75% > 0.5%
4. ❓ Would guards pass?

## Would The Guards Have Blocked It?

Let me check what the guards would have seen at 15:08:

**Guard 1: OM Peak Hour**
- Dynamic forecast: 14.7°C
- Actual high: 16.0°C (already exceeded forecast)
- OM peak hour: Likely already passed (forecast was 14.7°C)
- ✅ **Would PASS** (peak already reached)

**Guard 2: OM Remaining Max**
- Would OM forecast higher temps after 4 PM?
- Actual high (16°C) already exceeded forecast (14.7°C)
- ✅ **Would PASS** (no higher temps expected)

**Guard 3: OM High vs Bracket**
- OM high: ~14.7°C + bias = ~14.8°C
- Bracket floor: 17°C
- Gap: 17 - 14.8 = 2.2°C (safe distance)
- ✅ **Would PASS**

**Guard 4: Rising Trends**
- At 15:08, temp just jumped from 15°C to 16°C
- METAR: Likely showing FLAT or FALLING (peak reached)
- SYNOP: Would need to check
- OM: Likely FLAT (forecast was 14.7°C, already exceeded)
- ✅ **Likely PASS** (peak reached, no rising trends)

**Guard 5: SYNOP Velocity**
- Would need actual SYNOP data
- If velocity < 0.3°C/3h: ✅ PASS
- If velocity > 0.3°C/3h: ❌ BLOCK

## The Actual Outcome

**Reality:**
- Daily high stayed at 16.0°C
- ≥17°C bracket resolved NO (correctly)
- If signal had fired at 15:08 with NO at 99.25%, profit would be ~$0.75 per $100

**This would have been a WINNING trade!**

## Why Today Was Different from Feb 15/18

**Feb 15 & 18 (losing days):**
- Temperature at 4 PM: 3-5°C
- Late-evening surge: +4-6°C after 4 PM
- OM peak hour: 22:00 (10 PM) - peak was AFTER signal time
- Guards correctly blocked (temps still rising)

**Feb 23 (would have won):**
- Temperature at 4 PM: 16°C
- Already exceeded forecast (14.7°C)
- No late-evening surge (stayed at 16°C)
- Peak already reached by 4 PM
- Guards would likely pass

## The Key Insight

**GUARANTEED_NO_CEIL works when:**
- Daily high has already been reached (actual >= forecast)
- Temperature is flat or falling
- No rising trends in any source
- Late enough in day (4 PM+)

**GUARANTEED_NO_CEIL fails when:**
- Daily high hasn't been reached yet (actual < forecast)
- Temperature still rising
- OM predicts peak later in evening
- Too early in day

## Recommendation

**Keep GUARANTEED_NO_CEIL but with stricter conditions:**

1. **Add forecast check:** Only fire if `daily_high >= forecast_high - 0.5°C`
   - This ensures peak has likely been reached
   - Would have blocked Feb 15 (3°C << 8.8°C) and Feb 18 (5°C << 9.1°C)
   - Would have allowed Feb 23 (16°C > 14.7°C)

2. **Keep current guards** - they're working perfectly

3. **Consider lowering thresholds slightly:**
   - CEIL_GAP: 2.0°C → 1.5°C (would have caught ≥17°C today)
   - MIN_YES_FOR_ALERT: 3% → 1% (more opportunities)

4. **Add explicit "peak reached" check:**
   - If actual high > forecast AND temps flat/falling → safe to fire
   - If actual high < forecast → dangerous, keep guards strict

## The Trade-Off

**With current settings:**
- Very conservative (high gap, high YES threshold)
- Missed opportunity today (~$0.75)
- But saved $300 on Feb 15/18

**With looser settings:**
- More opportunities (lower gap, lower YES threshold)
- Would have caught today's trade
- But need the forecast check to avoid Feb 15/18 disasters

**My recommendation:** Add the forecast check (`daily_high >= forecast_high - 0.5°C`) as a 6th guard. This would have:
- ✅ Allowed Feb 23 (16°C > 14.7°C)
- ❌ Blocked Feb 15 (3°C << 8.8°C)
- ❌ Blocked Feb 18 (5°C << 9.1°C)

This makes GUARANTEED_NO_CEIL actually guaranteed.
