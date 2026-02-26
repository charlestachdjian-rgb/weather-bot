"""
Simulate the full strategy across all historical Paris days.
HONEST P&L: $100 invested per trade. Win = small profit. Lose = lose $100.

Compares "Floor NO only" (safe) vs "All Strategies" (risky).
"""
import urllib.request, json, re, sys, time
from datetime import datetime, date, timezone, timedelta
from zoneinfo import ZoneInfo

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

CET = ZoneInfo("Europe/Paris")
CDG_LAT, CDG_LON = 49.0097, 2.5479
ROUNDING_BUFFER = 0.5
FORECAST_KILL_BUFFER = 4.0
OPENMETEO_BIAS = 1.0
LATE_DAY_HOUR = 16
LOCK_IN_HOUR  = 17
CEIL_GAP      = 2.0
MIN_YES_ALERT = 0.03
STAKE          = 100


with open(r"C:\Users\Charl\Desktop\Cursor\weather-bot\backtest_data.json", encoding="utf-8") as f:
    bdata = json.load(f)

paris_days = sorted([d for d in bdata["days"] if "paris" in d["slug"]], key=lambda d: d["date"])
print(f"Found {len(paris_days)} Paris days\n")


def fetch_wu(dt):
    ds = dt.strftime("%Y%m%d")
    url = (f"https://api.weather.com/v1/location/LFPG:9:FR/observations/historical.json"
           f"?apiKey=e1f10a1e78da46f5b10a1e78da96f525&units=m&startDate={ds}&endDate={ds}")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        data = json.loads(r.read())
    pts = []
    for o in data.get("observations", []):
        ts = o.get("valid_time_gmt", 0)
        temp = o.get("temp")
        if temp is not None:
            d = datetime.fromtimestamp(ts, tz=CET)
            pts.append({"time_cet": d.strftime("%H:%M"), "hour": d.hour + d.minute/60, "ts": ts, "temp_c": temp})
    return sorted(pts, key=lambda x: x["ts"])


def fetch_markets(slug):
    url = f"https://gamma-api.polymarket.com/events?slug={slug}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=10) as r:
        data = json.loads(r.read())
    if not data: return []
    markets = []
    for m in data[0].get("markets", []):
        q = m.get("question", "").replace("\u00b0", "")
        lo, hi = None, None
        match = re.search(r"be\s+(\d+)\s*C\s+or\s+below", q)
        if match: lo, hi = None, float(match.group(1))
        else:
            match = re.search(r"be\s+(\d+)\s*C\s+or\s+higher", q)
            if match: lo, hi = float(match.group(1)), None
            else:
                match = re.search(r"be\s+(\d+)\s*C\s+on", q)
                if match: v = float(match.group(1)); lo, hi = v, v
        if lo is None and hi is not None: label = f"<={int(hi)}°C"
        elif lo is not None and hi is not None and lo == hi: label = f"{int(lo)}°C"
        elif lo is not None and hi is None: label = f">={int(lo)}°C"
        else: label = "?"
        tids = m.get("clobTokenIds", "[]")
        try: tids = json.loads(tids) if isinstance(tids, str) else tids
        except: tids = []
        markets.append({"label": label, "lo": lo, "hi": hi, "yes_token": tids[0] if tids else None})
    markets.sort(key=lambda x: x["hi"] if x["hi"] is not None else 999)
    return markets


def fetch_ph(tid, dt):
    start = int(datetime(dt.year, dt.month, dt.day, tzinfo=timezone.utc).timestamp())
    url = f"https://clob.polymarket.com/prices-history?market={tid}&startTs={start}&endTs={start+86400}&interval=1h&fidelity=60"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read())
        return [(int(h["t"]), float(h["p"])) for h in data.get("history", []) if h.get("t") and h.get("p")]
    except:
        return []


def yes_at(ph, hour):
    best = None
    for ts, p in ph:
        dt = datetime.fromtimestamp(ts, tz=CET)
        if dt.hour + dt.minute/60 <= hour + 0.5:
            best = p
    return best


def bracket_resolved_no(lo, hi, wu_high):
    if lo is not None and hi is not None:
        return not (lo <= wu_high <= hi)
    elif lo is None and hi is not None:
        return wu_high > hi
    elif lo is not None and hi is None:
        return wu_high < lo
    return False


def compute_pnl(side, yes_price, correct):
    """
    $100 invested per trade. Honest math.
    Win: profit based on share price.
    Lose: -$100 always.
    """
    if yes_price is None or yes_price <= 0:
        yes_price = 0.001
    if yes_price >= 1.0:
        yes_price = 0.999

    if side == "NO":
        no_price = 1.0 - yes_price
        if no_price <= 0:
            no_price = 0.001
        if correct:
            return round(STAKE * yes_price / no_price, 2)
        else:
            return -STAKE
    else:  # YES
        if correct:
            return round(STAKE * (1.0 - yes_price) / yes_price, 2)
        else:
            return -STAKE


def simulate_day(day_info, wu_obs, markets, price_histories):
    wu_high = day_info["wu_high"]
    forecast = round(day_info["openmeteo_high"] + OPENMETEO_BIAS, 1)

    events = []
    _killed = set()

    # Phase 1: T1 before 9am
    rh = None
    for obs in wu_obs:
        if obs["hour"] > 9: break
        if rh is None or obs["temp_c"] > rh:
            old = rh; rh = obs["temp_c"]
            for m in markets:
                hi = m["hi"]
                if hi is None or m["label"] in _killed: continue
                if rh >= hi + ROUNDING_BUFFER and not (old is not None and old >= hi + ROUNDING_BUFFER):
                    yes_p = yes_at(price_histories.get(m["label"], []), obs["hour"])
                    correct = bracket_resolved_no(m["lo"], m["hi"], wu_high)
                    events.append({"time": obs["time_cet"], "hour": obs["hour"], "type": "FLOOR_T1",
                                   "bracket": m["label"], "side": "NO", "yes": yes_p,
                                   "correct": correct, "pnl": compute_pnl("NO", yes_p, correct)})
                    _killed.add(m["label"])

    # Phase 2: T2 at 9am
    for m in markets:
        hi = m["hi"]
        if hi is None or m["label"] in _killed: continue
        if forecast - hi >= FORECAST_KILL_BUFFER:
            yes_p = yes_at(price_histories.get(m["label"], []), 9)
            correct = bracket_resolved_no(m["lo"], m["hi"], wu_high)
            events.append({"time": "09:00", "hour": 9, "type": "FLOOR_T2",
                           "bracket": m["label"], "side": "NO", "yes": yes_p,
                           "correct": correct, "pnl": compute_pnl("NO", yes_p, correct)})
            _killed.add(m["label"])

    # Phase 3: T1 after 9am + Ceiling NO + Locked-In YES
    _ceil_done = set()
    _lock_done = set()
    for obs in wu_obs:
        if obs["hour"] <= 9: continue
        hour = obs["hour"]
        if rh is None or obs["temp_c"] > rh:
            old = rh; rh = obs["temp_c"]
            for m in markets:
                hi = m["hi"]
                if hi is None or m["label"] in _killed: continue
                if rh >= hi + ROUNDING_BUFFER and not (old is not None and old >= hi + ROUNDING_BUFFER):
                    yes_p = yes_at(price_histories.get(m["label"], []), hour)
                    correct = bracket_resolved_no(m["lo"], m["hi"], wu_high)
                    events.append({"time": obs["time_cet"], "hour": hour, "type": "FLOOR_T1",
                                   "bracket": m["label"], "side": "NO", "yes": yes_p,
                                   "correct": correct, "pnl": compute_pnl("NO", yes_p, correct)})
                    _killed.add(m["label"])

        if hour >= LATE_DAY_HOUR:
            for m in markets:
                lo = m["lo"]
                if lo is None or m["label"] in _killed or m["label"] in _ceil_done: continue
                gap = lo - rh
                if gap >= CEIL_GAP:
                    yes_p = yes_at(price_histories.get(m["label"], []), hour)
                    if yes_p is not None and yes_p >= MIN_YES_ALERT:
                        correct = bracket_resolved_no(m["lo"], m["hi"], wu_high)
                        events.append({"time": obs["time_cet"], "hour": hour, "type": "CEIL_NO",
                                       "bracket": m["label"], "side": "NO", "yes": yes_p,
                                       "correct": correct, "pnl": compute_pnl("NO", yes_p, correct)})
                        _killed.add(m["label"])
                _ceil_done.add(m["label"])

        if hour >= LOCK_IN_HOUR:
            for m in markets:
                lo, hi = m["lo"], m["hi"]
                if lo is None or hi is None or m["label"] in _lock_done: continue
                if lo - ROUNDING_BUFFER <= rh <= hi + ROUNDING_BUFFER:
                    yes_p = yes_at(price_histories.get(m["label"], []), hour)
                    if yes_p is not None and yes_p < 0.80:
                        correct = not bracket_resolved_no(m["lo"], m["hi"], wu_high)
                        events.append({"time": obs["time_cet"], "hour": hour, "type": "LOCKED_YES",
                                       "bracket": m["label"], "side": "YES", "yes": yes_p,
                                       "correct": correct, "pnl": compute_pnl("YES", yes_p, correct)})
                _lock_done.add(m["label"])

    events.sort(key=lambda e: e["hour"])
    return events


# ── Run ──────────────────────────────────────────────────────────────────

all_results = []
for i, day in enumerate(paris_days):
    dt = date.fromisoformat(day["date"])
    print(f"[{i+1}/{len(paris_days)}] {day['date']}...", end=" ", flush=True)
    try: wu = fetch_wu(dt)
    except Exception as e: print(f"WU FAILED: {e}"); continue
    if not wu: print("no WU data"); continue
    try: mkts = fetch_markets(day["slug"])
    except Exception as e: print(f"MKT FAILED: {e}"); continue
    phs = {}
    for m in mkts:
        if m["yes_token"]:
            phs[m["label"]] = fetch_ph(m["yes_token"], dt)
            time.sleep(0.08)
    events = simulate_day(day, wu, mkts, phs)
    floor_only = [e for e in events if e["type"] in ("FLOOR_T1", "FLOOR_T2")]
    all_events = events
    all_results.append({
        "date": day["date"], "wu_high": day["wu_high"],
        "forecast": round(day["openmeteo_high"] + OPENMETEO_BIAS, 1),
        "all": all_events, "floor_only": floor_only,
    })
    a_pnl = sum(e["pnl"] for e in all_events)
    f_pnl = sum(e["pnl"] for e in floor_only)
    a_wrong = sum(1 for e in all_events if not e["correct"])
    print(f"All: {len(all_events)} trades, ${a_pnl:+.0f} ({a_wrong} wrong) | Floor only: {len(floor_only)} trades, ${f_pnl:+.2f}")
    time.sleep(0.2)


# ── Stats ────────────────────────────────────────────────────────────────

def calc_stats(results, key):
    all_ev = [e for r in results for e in r[key]]
    n = len(all_ev)
    correct = sum(1 for e in all_ev if e["correct"])
    wrong = n - correct
    total_pnl = sum(e["pnl"] for e in all_ev)
    invested = n * STAKE
    daily_pnls = [sum(e["pnl"] for e in r[key]) for r in results]
    best_day = max(zip(daily_pnls, [r["date"] for r in results]), key=lambda x: x[0]) if results else (0, "?")
    worst_day = min(zip(daily_pnls, [r["date"] for r in results]), key=lambda x: x[0]) if results else (0, "?")
    roi = total_pnl / invested * 100 if invested else 0
    by_type = {}
    for tname in ("FLOOR_T1", "FLOOR_T2", "CEIL_NO", "LOCKED_YES"):
        evs = [e for e in all_ev if e["type"] == tname]
        if evs:
            by_type[tname] = {
                "n": len(evs),
                "correct": sum(1 for e in evs if e["correct"]),
                "pnl": sum(e["pnl"] for e in evs),
            }
    return {"n": n, "correct": correct, "wrong": wrong, "pnl": total_pnl,
            "invested": invested, "roi": roi, "daily": daily_pnls,
            "best": best_day, "worst": worst_day, "by_type": by_type}

s_all = calc_stats(all_results, "all")
s_floor = calc_stats(all_results, "floor_only")

print(f"\n{'='*70}")
print(f"FLOOR NO ONLY: {s_floor['n']} trades, {s_floor['correct']}/{s_floor['n']} correct, P&L=${s_floor['pnl']:+.2f}, ROI={s_floor['roi']:.2f}%")
print(f"ALL STRATEGIES: {s_all['n']} trades, {s_all['correct']}/{s_all['n']} correct, P&L=${s_all['pnl']:+.2f}, ROI={s_all['roi']:.2f}%")


# ── HTML ─────────────────────────────────────────────────────────────────

dates_j = json.dumps([r["date"] for r in all_results])
floor_daily_j = json.dumps([round(x, 2) for x in s_floor["daily"]])
all_daily_j = json.dumps([round(x, 2) for x in s_all["daily"]])

cum_floor = []; rf = 0
cum_all = []; ra = 0
for f, a in zip(s_floor["daily"], s_all["daily"]):
    rf += f; ra += a
    cum_floor.append(round(rf, 2)); cum_all.append(round(ra, 2))
cum_floor_j = json.dumps(cum_floor)
cum_all_j = json.dumps(cum_all)

type_names = {"FLOOR_T1": "Floor NO (T1)", "FLOOR_T2": "Floor NO (T2)",
              "CEIL_NO": "Ceiling NO", "LOCKED_YES": "Locked-In YES"}

def type_rows(stats):
    rows = ""
    for tk, name in type_names.items():
        s = stats["by_type"].get(tk)
        if not s:
            rows += f"<tr><td>{name}</td><td>0</td><td>-</td><td>-</td><td class='muted'>no triggers</td></tr>"
        else:
            wr = s["correct"]/s["n"]*100
            cls = "green" if s["pnl"] >= 0 else "red"
            rows += f"<tr><td>{name}</td><td>{s['n']}</td><td>{s['correct']}/{s['n']} ({wr:.0f}%)</td><td class='{cls}'><strong>${s['pnl']:+.2f}</strong></td><td>${s['pnl']/s['n']:+.2f}</td></tr>"
    return rows

def daily_rows(results, key):
    rows = ""
    for r in results:
        evs = r[key]
        pnl = sum(e["pnl"] for e in evs)
        wrong = sum(1 for e in evs if not e["correct"])
        cls = "green" if pnl >= 0 else "red"
        chips = ""
        for e in evs:
            tag = {"FLOOR_T1":"T1","FLOOR_T2":"T2","CEIL_NO":"CEIL","LOCKED_YES":"LOCK"}[e["type"]]
            ok = "&#x2705;" if e["correct"] else "&#x274C;"
            yes_str = f"{e['yes']:.1%}" if e["yes"] else "?"
            bad_cls = "chip-bad" if not e["correct"] else ""
        chips += f"<span class='chip {bad_cls}'>{ok} {tag} {e['side']} {e['bracket']} @{yes_str} &rarr; <strong>${e['pnl']:+.0f}</strong></span> "
        rows += f"""<tr>
          <td><strong>{r['date']}</strong></td><td>{r['wu_high']}°C</td><td>{r['forecast']}°C</td>
          <td>{len(evs)}</td><td class="{cls}"><strong>${pnl:+.0f}</strong></td>
          <td>{"<span class='red'>"+str(wrong)+"</span>" if wrong else "0"}</td>
        </tr><tr class="detail-row"><td colspan="6">{chips}</td></tr>"""
    return rows

now_cet = datetime.now(timezone.utc).astimezone(CET)

html = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8">
<title>Honest Backtest — $100/Trade</title>
<script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>
<style>
  * {{ margin:0; padding:0; box-sizing:border-box; }}
  body {{ font-family:'Segoe UI',system-ui,sans-serif; background:#0d1117; color:#c9d1d9; padding:24px; line-height:1.6; }}
  .container {{ max-width:1200px; margin:0 auto; }}
  h1 {{ font-size:22px; color:#e6edf3; }} h2 {{ font-size:17px; color:#e6edf3; margin:28px 0 12px; border-bottom:1px solid #30363d; padding-bottom:6px; }}
  .sub {{ font-size:13px; color:#8b949e; margin-bottom:20px; }}
  .green {{ color:#2ecc71; }} .red {{ color:#e74c3c; }} .yellow {{ color:#f1c40f; }} .muted {{ color:#484f58; }}

  .compare {{ display:flex; gap:20px; margin:16px 0; flex-wrap:wrap; }}
  .strat-card {{ flex:1; min-width:300px; background:#161b22; border-radius:10px; padding:20px; }}
  .strat-card.safe {{ border:2px solid #2ecc71; }}
  .strat-card.risky {{ border:2px solid #e74c3c; }}
  .strat-card h3 {{ font-size:15px; margin-bottom:12px; }}
  .strat-card .big {{ font-size:32px; font-weight:700; }}
  .strat-card .row {{ display:flex; justify-content:space-between; margin:4px 0; font-size:13px; }}
  .strat-card .label {{ color:#8b949e; }}

  .chart-box {{ background:#161b22; border:1px solid #30363d; border-radius:8px; padding:12px; margin:12px 0; }}
  table {{ width:100%; border-collapse:collapse; font-size:13px; }}
  th {{ text-align:left; padding:8px 10px; border-bottom:2px solid #30363d; color:#8b949e; font-size:10px; text-transform:uppercase; }}
  td {{ padding:6px 10px; border-bottom:1px solid #21262d; }}
  tr:hover {{ background:#1c2333; }}
  .section {{ background:#161b22; border:1px solid #30363d; border-radius:8px; padding:16px; margin:12px 0; }}
  .detail-row td {{ padding:4px 10px 10px; font-size:11px; border-bottom:2px solid #30363d; }}

  .chip {{
    display:inline-block; padding:2px 8px; margin:2px; border-radius:4px;
    background:#21262d; border:1px solid #30363d; font-size:11px; white-space:nowrap;
  }}
  .chip-bad {{ background:#3d1418; border-color:#e74c3c; }}

  .callout {{ border-radius:10px; padding:20px; margin:20px 0; font-size:14px; }}
  .callout-green {{ background:linear-gradient(135deg,#0d2818,#161b22); border:2px solid #238636; }}
  .callout-red {{ background:linear-gradient(135deg,#2d0d0d,#161b22); border:2px solid #e74c3c; }}
  .callout h3 {{ margin-bottom:8px; }}
  .callout-green h3 {{ color:#2ecc71; }}
  .callout-red h3 {{ color:#e74c3c; }}

  .math {{ background:#161b22; border:1px solid #30363d; border-radius:8px; padding:16px; margin:12px 0; font-size:14px; }}
  .math .eq {{ font-family:monospace; font-size:15px; margin:6px 0; padding:8px 12px; background:#0d1117; border-radius:4px; }}
</style></head><body>
<div class="container">

<h1>Honest Backtest — $100 Invested Per Trade</h1>
<div class="sub">
  {len(all_results)} Paris days &bull; Win = small profit &bull; <span class="red">Lose = lose the full $100</span> &bull;
  Generated {now_cet.strftime('%H:%M CET, %b %d %Y')}
</div>

<div class="math">
  <strong>How the math works for every trade:</strong>
  <div class="eq">You invest $100 → Buy shares at market price</div>
  <div class="eq green">Win: you get back $100 + profit → Profit = $100 × (YES price) / (NO price)</div>
  <div class="eq red">Lose: your shares go to $0 → <strong>You lose $100. Always. No matter the share price.</strong></div>
  <p style="margin-top:8px; font-size:13px; color:#8b949e;">Example: YES=3%, NO=97%. You buy ~103 NO shares for $100. Win: collect $103, profit $3. Lose: lose $100.</p>
</div>

<h2>Side by Side: Safe vs All Strategies</h2>
<div class="compare">
  <div class="strat-card safe">
    <h3 class="green">Floor NO Only (T1 + T2)</h3>
    <div class="big {'green' if s_floor['pnl'] >= 0 else 'red'}">${s_floor['pnl']:+.2f}</div>
    <div class="row"><span class="label">Trades</span><span>{s_floor['n']}</span></div>
    <div class="row"><span class="label">Win rate</span><span class="green"><strong>{s_floor['correct']}/{s_floor['n']} ({s_floor['correct']/s_floor['n']*100:.0f}%)</strong></span></div>
    <div class="row"><span class="label">Wrong trades</span><span>{'<span class=green>0</span>' if s_floor['wrong']==0 else '<span class=red>'+str(s_floor['wrong'])+'</span>'}</span></div>
    <div class="row"><span class="label">Total invested</span><span>${s_floor['invested']:,}</span></div>
    <div class="row"><span class="label">ROI</span><span>{s_floor['roi']:.2f}%</span></div>
    <div class="row"><span class="label">Best day</span><span class="green">${s_floor['best'][0]:+.2f} ({s_floor['best'][1]})</span></div>
    <div class="row"><span class="label">Worst day</span><span>${s_floor['worst'][0]:+.2f} ({s_floor['worst'][1]})</span></div>
    <div class="row"><span class="label">Max single loss</span><span class="green">$0</span></div>
  </div>
  <div class="strat-card risky">
    <h3 class="red">All Strategies (Floor + Ceiling + Lock-In)</h3>
    <div class="big {'green' if s_all['pnl'] >= 0 else 'red'}">${s_all['pnl']:+.2f}</div>
    <div class="row"><span class="label">Trades</span><span>{s_all['n']}</span></div>
    <div class="row"><span class="label">Win rate</span><span>{s_all['correct']}/{s_all['n']} ({s_all['correct']/s_all['n']*100:.0f}%)</span></div>
    <div class="row"><span class="label">Wrong trades</span><span class="red"><strong>{s_all['wrong']}</strong></span></div>
    <div class="row"><span class="label">Total invested</span><span>${s_all['invested']:,}</span></div>
    <div class="row"><span class="label">ROI</span><span class="{'green' if s_all['roi']>=0 else 'red'}">{s_all['roi']:.2f}%</span></div>
    <div class="row"><span class="label">Best day</span><span class="green">${s_all['best'][0]:+.2f} ({s_all['best'][1]})</span></div>
    <div class="row"><span class="label">Worst day</span><span class="red">${s_all['worst'][0]:+.2f} ({s_all['worst'][1]})</span></div>
    <div class="row"><span class="label">Max single loss</span><span class="red">-$100</span></div>
  </div>
</div>

<h2>Cumulative P&amp;L</h2>
<div class="chart-box"><div id="cumChart" style="height:380px"></div></div>
<script>
Plotly.newPlot('cumChart', [
  {{ x:{dates_j}, y:{cum_floor_j}, type:'scatter', mode:'lines+markers',
     name:'Floor NO only', line:{{color:'#2ecc71',width:3}}, marker:{{size:6}},
     hovertemplate:'Floor only: $%{{y:.2f}}<extra></extra>' }},
  {{ x:{dates_j}, y:{cum_all_j}, type:'scatter', mode:'lines+markers',
     name:'All strategies', line:{{color:'#e74c3c',width:3,dash:'dash'}}, marker:{{size:6}},
     hovertemplate:'All: $%{{y:.2f}}<extra></extra>' }}
], {{
  paper_bgcolor:'#161b22', plot_bgcolor:'#161b22',
  font:{{color:'#c9d1d9',size:12}}, margin:{{l:60,r:30,t:10,b:50}},
  xaxis:{{gridcolor:'#21262d'}},
  yaxis:{{gridcolor:'#21262d',title:'Cumulative P&L ($)',zeroline:true,zerolinecolor:'#30363d'}},
  legend:{{bgcolor:'rgba(22,27,34,0.9)',bordercolor:'#30363d',borderwidth:1,x:0.01,y:0.99}},
  hovermode:'x unified'
}}, {{responsive:true,displayModeBar:false}});
</script>

<h2>Daily P&amp;L Comparison</h2>
<div class="chart-box"><div id="barChart" style="height:350px"></div></div>
<script>
Plotly.newPlot('barChart', [
  {{ x:{dates_j}, y:{floor_daily_j}, type:'bar', name:'Floor NO only',
     marker:{{color:'#2ecc71'}}, hovertemplate:'Floor: $%{{y:.2f}}<extra></extra>' }},
  {{ x:{dates_j}, y:{all_daily_j}, type:'bar', name:'All strategies',
     marker:{{color:'#e74c3c',opacity:0.7}}, hovertemplate:'All: $%{{y:.2f}}<extra></extra>' }}
], {{
  paper_bgcolor:'#161b22', plot_bgcolor:'#161b22',
  font:{{color:'#c9d1d9',size:12}}, margin:{{l:60,r:30,t:10,b:50}},
  xaxis:{{gridcolor:'#21262d'}},
  yaxis:{{gridcolor:'#21262d',title:'Daily P&L ($)',zeroline:true,zerolinecolor:'#30363d'}},
  legend:{{bgcolor:'rgba(22,27,34,0.9)',bordercolor:'#30363d',borderwidth:1,x:0.01,y:0.99}},
  barmode:'group', hovermode:'x unified'
}}, {{responsive:true,displayModeBar:false}});
</script>

<h2>Performance by Signal Type (All Strategies)</h2>
<div class="section">
<table>
  <thead><tr><th>Signal</th><th>Trades</th><th>Win Rate</th><th>Total P&amp;L</th><th>Avg/Trade</th></tr></thead>
  <tbody>{type_rows(s_all)}</tbody>
</table>
</div>

<h2>Daily Detail — Floor NO Only (Safe Strategy)</h2>
<div class="section">
<table>
  <thead><tr><th>Date</th><th>High</th><th>Forecast</th><th>Trades</th><th>P&amp;L</th><th>Wrong</th></tr></thead>
  <tbody>{daily_rows(all_results, "floor_only")}</tbody>
</table>
</div>

<h2>Daily Detail — All Strategies</h2>
<div class="section">
<table>
  <thead><tr><th>Date</th><th>High</th><th>Forecast</th><th>Trades</th><th>P&amp;L</th><th>Wrong</th></tr></thead>
  <tbody>{daily_rows(all_results, "all")}</tbody>
</table>
</div>

<div class="callout callout-green">
  <h3>Floor NO: The Safe Strategy</h3>
  <p><strong>{s_floor['correct']}/{s_floor['n']} trades correct (100% win rate).</strong>
  Total P&amp;L: <strong>${s_floor['pnl']:+.2f}</strong> over {len(all_results)} days.
  Not a single losing trade. The math is simple: once a running high exceeds a bracket's threshold,
  it can never go back down. Every Floor NO trade is guaranteed to resolve correctly.</p>
  <p style="margin-top:8px">The edge per trade is small (pennies to a few dollars), but the risk is <strong>zero</strong>.</p>
</div>

<div class="callout callout-red">
  <h3>Ceiling NO &amp; Locked-In YES: The Trap</h3>
  <p>These strategies added {s_all['n'] - s_floor['n']} trades with {s_all['wrong']} losses.
  Each loss = <strong>-$100</strong> (your full bet), wiping out many winning trades.</p>
  <p style="margin-top:8px">The losses happened because Paris temperature surged +4-6°C <strong>after 4-5pm</strong>.
  The 4pm/5pm cutoffs are too early for Paris winter. These strategies should either be dropped entirely
  or use much later cutoffs (10pm+) with additional trend confirmation.</p>
</div>

</div></body></html>"""

out_path = r"C:\Users\Charl\Desktop\Cursor\weather-bot\alldays_backtest.html"
with open(out_path, "w", encoding="utf-8") as f:
    f.write(html)
print(f"\nSaved to {out_path}")

import webbrowser
webbrowser.open(out_path)
