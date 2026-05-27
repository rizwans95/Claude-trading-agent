"""
walkforward_simulator.py (v3 - fixed fetcher, no end param)
Walk-Forward Simulator for Trading Agent V2

Bybit API behaviour confirmed:
  - Use START only (no end param) — returns bars forward from start
  - With end param — returns 1000 bars BEFORE end, ignores start
  - Each batch: advance start = last_bar_timestamp + 1_interval

Period: Aug 1 to Nov 30 2022 (BTC bear market + FTX collapse)
Run: cd /root/trading && python3 walkforward_simulator.py
"""
import sys, os, json, time, warnings, requests
import numpy as np
import pandas as pd
from datetime import datetime, timezone
warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from indicator_engine      import compute_pavp, compute_atr
from volume_profile_engine import compute_volume_intelligence
from wme_sweep_engine      import compute_wme_signal, get_wme_confidence_modifier

STARTING_BALANCE = 52.50
RISK_PCT         = 0.02
WINDOW           = 120
MAX_HOLD_BARS    = 60
COOLDOWN         = 20
START_MS         = 1659312000000   # Aug 1 2022 00:00 UTC
END_MS           = 1669852740000   # Nov 30 2022 23:59 UTC
INTERVAL         = 15
TARGET_BARS      = 10000

STRATEGIES = [
    {"key": "BTC_G", "allowed_hours": [7,12,14,18], "allowed_grades": None,  "mode": "G"},
    {"key": "BTC_L", "allowed_hours": [7,12,14],    "allowed_grades": ["B"], "mode": "L"},
]

CSV_COLUMNS = [
    "signal_id","timestamp","utc_hour","strategy","symbol","timeframe","mode",
    "direction","grade","confidence","in_session","wme_signal","cvd_signal","in_lvn",
    "entry_price","stop_loss","stop_pct","target_1","target_2","target_3",
    "account_balance","status","exit_price","exit_time","r_multiple","pnl_usdt",
    "running_wins","running_losses","running_wr","running_equity","notes",
]

# ─────────────────────────────────────────────────────────────
# FETCHER — start only, no end param, walk forward
# ─────────────────────────────────────────────────────────────

def fetch_bybit_range(symbol, interval_min, start_ms, end_ms, target_bars=10000):
    url          = "https://api.bybit.com/v5/market/kline"
    all_bars     = []
    cur_ms       = start_ms
    interval_ms  = interval_min * 60 * 1000
    batch_num    = 0

    print(f"  Fetching {symbol} {interval_min}m bars (Aug 1 to Nov 30 2022)...")
    print(f"  Target: {target_bars} bars — NO end param, walking forward from start")

    while len(all_bars) < target_bars and cur_ms < end_ms:
        # KEY FIX: no 'end' parameter — only start
        params = {
            "category": "linear",
            "symbol":   symbol,
            "interval": str(interval_min),
            "start":    cur_ms,
            "limit":    1000,
        }

        try:
            r    = requests.get(url, params=params, timeout=20, verify=False)
            data = r.json()
        except Exception as e:
            print(f"\n  [ERROR] Batch {batch_num+1} failed: {e}")
            time.sleep(2)
            continue

        raw_batch = data.get("result", {}).get("list", [])
        if not raw_batch:
            print(f"\n  No more data returned — stopping at {datetime.fromtimestamp(cur_ms/1000, tz=timezone.utc)}")
            break

        # Sort ascending (oldest first)
        batch = sorted(raw_batch, key=lambda x: int(x[0]))

        # Filter: only keep bars within our date range
        batch = [b for b in batch if int(b[0]) <= end_ms]
        if not batch:
            break

        all_bars.extend(batch)
        batch_num += 1

        # Advance: next batch starts after last bar in this batch
        last_ts = int(batch[-1][0])
        cur_ms  = last_ts + interval_ms

        last_dt  = datetime.fromtimestamp(last_ts/1000, tz=timezone.utc)
        done_pct = min(100, (last_ts - start_ms) / (end_ms - start_ms) * 100)
        print(f"  Batch {batch_num:>2} | {len(all_bars):>6} bars | "
              f"{last_dt.strftime('%Y-%m-%d')} | {done_pct:5.1f}% complete", end="\r")

        time.sleep(0.25)

        # If Bybit returned fewer than limit, we've reached the end
        if len(raw_batch) < 1000:
            print(f"\n  Reached end of available data at batch {batch_num}")
            break

    print(f"\n  Total fetched: {len(all_bars)} bars")

    if not all_bars:
        raise RuntimeError("No data returned — check Bybit API and connectivity")

    df = pd.DataFrame(all_bars, columns=["time","open","high","low","close","volume","turnover"])
    for col in ["open","high","low","close","volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["time"] = pd.to_datetime(df["time"].astype(np.int64), unit="ms", utc=True)
    df = df.drop_duplicates(subset=["time"]).sort_values("time").reset_index(drop=True).dropna()

    if len(df) > target_bars:
        df = df.iloc[:target_bars].reset_index(drop=True)

    print(f"  Date range: {df['time'].iloc[0].strftime('%Y-%m-%d %H:%M')} "
          f"to {df['time'].iloc[-1].strftime('%Y-%m-%d %H:%M')}")
    print(f"  Price range: ${df['close'].min():,.0f} to ${df['close'].max():,.0f}")
    print(f"  Final bar count: {len(df)}")
    return df

# ─────────────────────────────────────────────────────────────
# SIGNAL ENGINE
# ─────────────────────────────────────────────────────────────

def evaluate_signal(slice_df, strat, bar_time_utc):
    try:
        pavp     = compute_pavp(slice_df)
        atr_data = compute_atr(slice_df)
        vi       = compute_volume_intelligence(slice_df, pavp)
        swing    = vi.get("swing_profiles", {})
        wme      = compute_wme_signal(slice_df, bar_tolerance=0)
        curr     = wme["current"]
    except Exception:
        return None

    has_long  = curr.get("long_entry", False)
    has_short = curr.get("short_entry", False)
    if not has_long and not has_short:
        return None

    direction = "LONG" if has_long else "SHORT"
    if bar_time_utc.hour not in strat["allowed_hours"]:
        return None

    cp     = float(slice_df["close"].iloc[-1])
    hvn_z  = swing.get("hvn_zones", [])
    in_hvn = any(z["price_low"] <= cp <= z["price_high"] for z in hvn_z)
    in_lvn = swing.get("in_lvn", False)
    wm     = get_wme_confidence_modifier(wme, in_lvn, in_hvn, len(slice_df)-1)
    spd    = abs(curr.get("speed", 0.0))
    conf   = round(min(100, max(0, 55 + min(spd*15, 20) + wm["modifier"])), 1)
    grade  = "A" if conf >= 75 else "B" if conf >= 60 else "C"

    if grade == "C":
        return None
    if strat["allowed_grades"] is not None and grade not in strat["allowed_grades"]:
        return None

    atr_val   = atr_data.get("atr_value", cp * 0.002)
    stop      = round(cp - atr_val*3 if direction=="LONG" else cp + atr_val*3, 4)
    stop_dist = abs(cp - stop)
    stop_pct  = round(stop_dist/cp*100, 3) if cp > 0 else 0

    et_key  = "exit_targets_long" if direction=="LONG" else "exit_targets_short"
    targets = [t["price"] for t in vi.get(et_key, [])[:3]]
    while len(targets) < 3:
        n = len(targets) + 1
        targets.append(round(cp + atr_val*3*n if direction=="LONG"
                             else cp - atr_val*3*n, 4))

    return {
        "direction": direction, "grade": grade, "confidence": conf,
        "in_lvn": in_lvn, "entry_price": cp, "stop_loss": stop,
        "stop_pct": stop_pct, "target_1": targets[0],
        "target_2": targets[1], "target_3": targets[2], "wme_signal": True,
    }

# ─────────────────────────────────────────────────────────────
# OUTCOME EVALUATOR
# ─────────────────────────────────────────────────────────────

def check_outcome(trade, bar_high, bar_low):
    d  = trade["direction"]
    ep = trade["entry_price"]
    sl = trade["stop_loss"]
    t1 = trade["target_1"]
    sd = abs(ep - sl) or ep * 0.003
    if d == "LONG":
        if bar_low  <= sl: return "LOSS", sl, -1.0
        if t1 and bar_high >= t1: return "WIN", t1, round((t1-ep)/sd, 3)
    else:
        if bar_high >= sl: return "LOSS", sl, -1.0
        if t1 and bar_low  <= t1: return "WIN", t1, round((ep-t1)/sd, 3)
    return "OPEN", None, None

# ─────────────────────────────────────────────────────────────
# RUNNING STATS
# ─────────────────────────────────────────────────────────────

def calc_stats(trades):
    closed = [t for t in trades if t["status"] in ("WIN","LOSS","TIMEOUT")]
    wins   = sum(1 for t in closed if t["status"]=="WIN")
    losses = len(closed) - wins
    wr     = round(wins/len(closed)*100, 1) if closed else 0.0
    eq     = STARTING_BALANCE
    for t in closed:
        if t.get("r_multiple") is not None:
            eq += eq * RISK_PCT * t["r_multiple"]
            eq  = max(eq, 0.01)
    return wins, losses, wr, round(eq, 4)

# ─────────────────────────────────────────────────────────────
# WALK-FORWARD LOOP
# ─────────────────────────────────────────────────────────────

def run_walkforward(df, strat):
    n              = len(df)
    trade_log      = []
    sig_counter    = 1
    open_trade     = None
    last_trade_bar = -999
    errors         = 0
    t0             = time.time()

    print(f"\n  [{strat['key']}] Walk-forward: {n} bars "
          f"({df['time'].iloc[WINDOW].strftime('%Y-%m-%d')} to "
          f"{df['time'].iloc[-1].strftime('%Y-%m-%d')})...")

    for i in range(WINDOW, n):

        # Check if open trade resolves on this bar
        if open_trade is not None:
            bh = float(df["high"].iloc[i])
            bl = float(df["low"].iloc[i])
            bt = df["time"].iloc[i]
            status, exit_px, r = check_outcome(open_trade, bh, bl)

            if status in ("WIN", "LOSS"):
                open_trade.update({
                    "status": status, "exit_price": round(exit_px, 4),
                    "exit_time": str(bt), "r_multiple": r,
                    "pnl_usdt": round(open_trade["account_balance"]*RISK_PCT*r, 4)
                })
                open_trade = None
            elif i - open_trade["bar_index"] >= MAX_HOLD_BARS:
                cp = float(df["close"].iloc[i])
                ep = open_trade["entry_price"]
                sl = open_trade["stop_loss"]
                sd = abs(ep-sl) or ep*0.003
                r  = round((cp-ep if open_trade["direction"]=="LONG" else ep-cp)/sd, 3)
                open_trade.update({
                    "status": "TIMEOUT", "exit_price": round(cp, 4),
                    "exit_time": str(df["time"].iloc[i]), "r_multiple": r,
                    "pnl_usdt": round(open_trade["account_balance"]*RISK_PCT*r, 4)
                })
                open_trade = None

        if open_trade is not None or i - last_trade_bar < COOLDOWN:
            continue

        slice_df     = df.iloc[i-WINDOW:i].copy().reset_index(drop=True)
        bar_time_utc = pd.Timestamp(df["time"].iloc[i]).tz_convert("UTC")

        try:
            sig = evaluate_signal(slice_df, strat, bar_time_utc)
        except Exception:
            errors += 1
            continue

        if sig is None:
            continue

        sig_id         = f"WF-{strat['key']}-{sig_counter:03d}"
        sig_counter   += 1
        last_trade_bar = i

        closed_so_far = [t for t in trade_log if t["status"] in ("WIN","LOSS","TIMEOUT")]
        _, _, _, equity = calc_stats(closed_so_far) if closed_so_far else (0,0,0,STARTING_BALANCE)

        trade = {
            "signal_id": sig_id, "bar_index": i,
            "timestamp": str(bar_time_utc), "utc_hour": bar_time_utc.hour,
            "strategy": strat["key"], "symbol": "BTCUSDT",
            "timeframe": "15m", "mode": strat["mode"],
            "direction": sig["direction"], "grade": sig["grade"],
            "confidence": sig["confidence"], "in_session": True,
            "wme_signal": sig["wme_signal"], "cvd_signal": "",
            "in_lvn": sig["in_lvn"], "entry_price": sig["entry_price"],
            "stop_loss": sig["stop_loss"], "stop_pct": sig["stop_pct"],
            "target_1": sig["target_1"], "target_2": sig["target_2"],
            "target_3": sig["target_3"],
            "account_balance": round(equity, 4), "status": "OPEN",
            "exit_price": None, "exit_time": None,
            "r_multiple": None, "pnl_usdt": None,
            "running_wins": 0, "running_losses": 0,
            "running_wr": 0.0, "running_equity": round(equity, 4),
            "notes": "WalkForward Aug-Nov 2022 bear market",
        }
        trade_log.append(trade)
        open_trade = trade

        pct = i/n*100
        print(f"  [{strat['key']}] {pct:5.1f}% | "
              f"{bar_time_utc.strftime('%Y-%m-%d')} | "
              f"${float(df['close'].iloc[i]):,.0f} | "
              f"Trade #{sig_counter-1} {sig['direction']} "
              f"Grade={sig['grade']} Conf={sig['confidence']:.0f}%  ", end="\r")

    # Close any trade still open at data end
    if open_trade is not None and open_trade["status"] == "OPEN":
        cp = float(df["close"].iloc[-1])
        ep = open_trade["entry_price"]; sl = open_trade["stop_loss"]
        sd = abs(ep-sl) or ep*0.003
        r  = round((cp-ep if open_trade["direction"]=="LONG" else ep-cp)/sd, 3)
        open_trade.update({
            "status": "TIMEOUT", "exit_price": round(cp, 4),
            "exit_time": str(df["time"].iloc[-1]), "r_multiple": r,
            "pnl_usdt": round(open_trade["account_balance"]*RISK_PCT*r, 4)
        })

    # Add running stats to all trades
    for idx, t in enumerate(trade_log):
        w, l, wr, eq = calc_stats(trade_log[:idx+1])
        t.update({"running_wins":w,"running_losses":l,
                  "running_wr":wr,"running_equity":round(eq,4)})

    print(f"\n  [{strat['key']}] Done — {len(trade_log)} trades "
          f"in {time.time()-t0:.1f}s ({errors} errors)")
    return trade_log

# ─────────────────────────────────────────────────────────────
# REPORT
# ─────────────────────────────────────────────────────────────

def print_report(trades, key):
    closed = [t for t in trades if t["status"] in ("WIN","LOSS","TIMEOUT")]
    if not trades:
        print(f"  [{key}] No trades generated."); return {}

    wins   = [t for t in closed if t["status"]=="WIN"]
    losses = [t for t in closed if t["status"] in ("LOSS","TIMEOUT")]
    total  = len(closed)
    wr     = round(len(wins)/total*100, 1) if total else 0
    avg_r  = round(sum(t["r_multiple"] for t in closed
                       if t["r_multiple"] is not None)/max(total,1), 3)

    eq=STARTING_BALANCE; peak=STARTING_BALANCE; max_dd=0.0
    for t in closed:
        if t["r_multiple"] is not None:
            eq += eq*RISK_PCT*t["r_multiple"]; eq=max(eq,0.01)
            if eq>peak: peak=eq
            dd=(peak-eq)/peak*100
            if dd>max_dd: max_dd=dd

    ret   = round((eq-STARTING_BALANCE)/STARTING_BALANCE*100, 1)
    ts    = pd.Timestamp(trades[0]["timestamp"])
    te    = pd.Timestamp(trades[-1]["timestamp"])
    days  = max((te-ts).days, 1); weeks=days/7
    wkd   = round((eq-STARTING_BALANCE)/weeks, 2)
    tpw   = round(total/weeks, 1)

    lt  = [t for t in closed if t["direction"]=="LONG"]
    st  = [t for t in closed if t["direction"]=="SHORT"]
    ga  = [t for t in closed if t["grade"]=="A"]
    gb  = [t for t in closed if t["grade"]=="B"]
    lwr = round(sum(1 for t in lt if t["status"]=="WIN")/max(len(lt),1)*100,1)
    swr = round(sum(1 for t in st if t["status"]=="WIN")/max(len(st),1)*100,1)
    awr = round(sum(1 for t in ga if t["status"]=="WIN")/max(len(ga),1)*100,1)
    bwr = round(sum(1 for t in gb if t["status"]=="WIN")/max(len(gb),1)*100,1)

    print(f"\n  {'─'*57}")
    print(f"  {key} WALK-FORWARD RESULTS (Aug-Nov 2022 bear market)")
    print(f"  {'─'*57}")
    print(f"  Period: {days} days  |  Signals: {len(trades)}  |  Closed: {total}")
    print(f"  Win rate:     {wr}%  ({len(wins)}W / {len(losses)}L)")
    print(f"  Avg R:        {avg_r}  |  Total return: {ret}%")
    print(f"  Final equity: ${eq:.2f}  |  Max drawdown: {max_dd:.1f}%")
    print(f"  $/week: ${wkd}  |  Trades/week: {tpw}")
    print(f"  LONG {len(lt)} trades WR={lwr}%  |  SHORT {len(st)} trades WR={swr}%")
    print(f"  Grade A {len(ga)} WR={awr}%  |  Grade B {len(gb)} WR={bwr}%")

    if closed:
        print(f"\n  Last 10 closed trades:")
        print(f"  {'ID':<22} {'Dir':<6} {'Gr'} {'Entry':>9} {'Exit':>9} {'R':>7} Status")
        print(f"  {'─'*67}")
        for t in closed[-10:]:
            xp = f"${t['exit_price']:>8,.1f}" if t['exit_price'] else "      open"
            rm = f"{t['r_multiple']:>+7.3f}" if t['r_multiple'] is not None else "      —"
            print(f"  {t['signal_id']:<22} {t['direction']:<6} {t['grade']:<2} "
                  f"${t['entry_price']:>8,.1f} {xp} {rm} {t['status']}")

    return {
        "strategy":key,"period_days":days,"total_trades":len(trades),
        "closed_trades":total,"wins":len(wins),"losses":len(losses),
        "win_rate":wr,"avg_r":avg_r,"total_return_pct":ret,
        "final_equity":round(eq,2),"max_drawdown_pct":round(max_dd,1),
        "weekly_dollar":wkd,"trades_per_week":tpw,
        "long_wr":lwr,"short_wr":swr,"grade_a_wr":awr,"grade_b_wr":bwr,
    }

# ─────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────

def main():
    print(f"\n{'='*60}")
    print(f"  WALK-FORWARD SIMULATOR v3 — Trading Agent V2")
    print(f"  Period: Aug 1 to Nov 30 2022 | Balance: ${STARTING_BALANCE} | Risk: 2%")
    print(f"{'='*60}\n")
    t0 = time.time()

    try:
        df = fetch_bybit_range("BTCUSDT", INTERVAL, START_MS, END_MS, TARGET_BARS)
    except Exception as e:
        print(f"\n  FATAL: {e}"); sys.exit(1)

    all_results={}; all_logs={}
    for strat in STRATEGIES:
        trades              = run_walkforward(df, strat)
        results             = print_report(trades, strat["key"])
        all_results[strat["key"]] = results
        all_logs[strat["key"]]    = trades

    all_rows = []
    for trades in all_logs.values():
        for t in trades:
            all_rows.append({col: t.get(col,"") for col in CSV_COLUMNS})
    if all_rows:
        pd.DataFrame(all_rows, columns=CSV_COLUMNS).to_csv(
            "walkforward_trade_log.csv", index=False)
        print(f"\n  Trade log: walkforward_trade_log.csv ({len(all_rows)} rows)")

    with open("walkforward_results.json","w") as f:
        json.dump({"period":"2022-08-01 to 2022-11-30",
                   "starting_balance":STARTING_BALANCE,"risk_pct":RISK_PCT,
                   "results":all_results}, f, indent=2)
    print(f"  Results:   walkforward_results.json")

    print(f"\n{'='*62}")
    print(f"  COMPARISON — Backtest (2024-25 bull) vs Walk-Forward (2022 bear)")
    print(f"{'='*62}")
    ref = {"BTC_G":(83.9,0.155,9.9,2.7,0.36), "BTC_L":(92.9,0.302,8.8,0.7,0.54)}
    for strat in STRATEGIES:
        key=strat["key"]; r=all_results.get(key,{}); bt=ref.get(key,(0,0,0,0,0))
        print(f"\n  [{key}]")
        print(f"  Win rate:  {bt[0]:>6.1f}% (bull)  vs  {r.get('win_rate',0):>6.1f}% (bear)")
        print(f"  Avg R:     {bt[1]:>7.3f} (bull)  vs  {r.get('avg_r',0):>7.3f} (bear)")
        print(f"  Return:    {bt[2]:>6.1f}% (bull)  vs  {r.get('total_return_pct',0):>6.1f}% (bear)")
        print(f"  Max DD:    {bt[3]:>6.1f}% (bull)  vs  {r.get('max_drawdown_pct',0):>6.1f}% (bear)")
        print(f"  $/week:   ${bt[4]:>6.2f} (bull)  vs ${r.get('weekly_dollar',0):>6.2f} (bear)")

    print(f"\n  Runtime: {time.time()-t0:.0f}s")
    print(f"\n  VERDICT:")
    print(f"  WR > 70% in bear  = robust edge across all markets")
    print(f"  WR 50-70%         = regime-dependent, use caution")
    print(f"  WR < 50%          = bull-market strategy only\n")

if __name__ == "__main__":
    main()
