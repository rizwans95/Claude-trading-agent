"""
backtest_confirmation.py
=============================================================
Compares original signal entry vs 2-confirmation entry.

Original:  Enter at close of bar where signal fires
Confirmed: Enter at close of NEXT bar (signal must still be
           valid on the following candle)

Tests both modes on BTC_G and BTC_L.
Reports: win rate, avg R, trade count, and signal survival rate
(how many signals survive to the next candle).
=============================================================
"""

import sys, os, warnings
import numpy as np
import pandas as pd
warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from indicator_engine      import compute_pavp, compute_atr
from volume_profile_engine import compute_volume_intelligence
from wme_sweep_engine      import compute_wme_signal, get_wme_confidence_modifier

import requests

# ─────────────────────────────────────────────────────────────
# DATA FETCH
# ─────────────────────────────────────────────────────────────

def fetch_bybit(symbol, interval, bars=5000):
    url    = "https://api.bybit.com/v5/market/kline"
    params = {"category":"linear","symbol":symbol,"interval":interval,"limit":min(bars,1000)}
    r = requests.get(url, params=params, timeout=20, verify=False)
    r.raise_for_status()
    chunk = r.json().get("result",{}).get("list",[])
    df = pd.DataFrame(chunk, columns=["open_time","open","high","low","close","volume","turnover"])
    df["time"] = pd.to_datetime(df["open_time"].astype(float), unit="ms")
    for c in ["open","high","low","close","volume"]:
        df[c] = df[c].astype(float)
    return df[["time","open","high","low","close","volume"]].sort_values("time").reset_index(drop=True)


# ─────────────────────────────────────────────────────────────
# OUTCOME EVALUATOR (same as backtest_v4)
# ─────────────────────────────────────────────────────────────

def evaluate_outcome(direction, entry_price, stop_loss, exit_targets,
                     future_highs, future_lows, max_bars=60):
    is_long    = direction == "LONG"
    targets_hit = []
    remaining  = 100.0
    stop_dist  = abs(entry_price - stop_loss)
    if stop_dist == 0: stop_dist = entry_price * 0.003

    for bar in range(min(max_bars, len(future_highs))):
        bh = float(future_highs[bar])
        bl = float(future_lows[bar])

        if is_long and bl <= stop_loss:
            r = sum((t["price"]-entry_price)/stop_dist*(t["partial_pct"]/100) for t in targets_hit)
            return "WIN" if r > 0.5 else "LOSS", round(r - remaining/100, 3)

        if not is_long and bh >= stop_loss:
            r = sum((entry_price-t["price"])/stop_dist*(t["partial_pct"]/100) for t in targets_hit)
            return "WIN" if r > 0.5 else "LOSS", round(r - remaining/100, 3)

        for tgt in exit_targets:
            if tgt["price"] in [t["price"] for t in targets_hit]: continue
            if (is_long and bh >= tgt["price"]) or (not is_long and bl <= tgt["price"]):
                targets_hit.append(tgt)
                remaining -= tgt["partial_pct"]

        if remaining <= 5:
            r = sum((t["price"]-entry_price if is_long else entry_price-t["price"])
                    /stop_dist*(t["partial_pct"]/100) for t in targets_hit)
            return "WIN", round(r, 3)

    final = float(future_highs[-1] if is_long else future_lows[-1])
    r = round((final-entry_price if is_long else entry_price-final)/stop_dist, 3)
    return ("WIN" if r >= 0 else "LOSS"), r


# ─────────────────────────────────────────────────────────────
# CORE SCANNER
# ─────────────────────────────────────────────────────────────

def scan_signals(df, strat):
    """
    Scan all bars and return:
    - original_trades: entries at signal bar close
    - confirmed_trades: entries at NEXT bar close (if signal still valid)
    - survival_rate: % of signals that survive to next bar
    """
    n      = len(df)
    closes = df["close"].values
    highs  = df["high"].values
    lows   = df["low"].values
    times  = df["time"].values

    WINDOW        = strat["window"]
    MAX_BARS      = strat["max_bars"]
    COOLDOWN      = strat["cooldown"]
    allowed_hours = strat["allowed_hours"]
    allowed_grades= strat.get("allowed_grades")

    original_trades  = []
    confirmed_trades = []
    total_signals    = 0
    survived_signals = 0
    last_trade_bar   = -999

    for i in range(WINDOW, n - MAX_BARS - 2):  # -2 to allow confirmation bar
        if i - last_trade_bar < COOLDOWN:
            continue

        slice_df = df.iloc[i - WINDOW: i].copy().reset_index(drop=True)
        try:
            pavp      = compute_pavp(slice_df)
            atr_data  = compute_atr(slice_df)
            vol_intel = compute_volume_intelligence(slice_df, pavp)
            wme       = compute_wme_signal(slice_df, bar_tolerance=0)
            curr      = wme["current"]
        except Exception:
            continue

        has_long  = curr["long_entry"]
        has_short = curr["short_entry"]
        if not has_long and not has_short:
            continue

        direction  = "LONG" if has_long else "SHORT"
        hour_now   = pd.Timestamp(times[i]).hour
        if hour_now not in allowed_hours:
            continue

        swing = vol_intel.get("swing_profiles", {})
        in_lvn = swing.get("in_lvn", False)
        in_hvn = any(z["price_low"] <= float(closes[i-1]) <= z["price_high"]
                     for z in swing.get("hvn_zones", []))

        wme_mod    = get_wme_confidence_modifier(wme, in_lvn, in_hvn, i)
        speed_abs  = abs(curr["speed"])
        confidence = round(min(100.0, max(0.0,
                        55.0 + min(speed_abs*15, 20) + wme_mod["modifier"])), 1)
        grade = "A" if confidence >= 75 else "B" if confidence >= 60 else "C"

        if grade == "C":
            continue
        if allowed_grades is not None and grade not in allowed_grades:
            continue

        total_signals += 1

        # ── ORIGINAL ENTRY (bar i) ──────────────────────────
        entry_orig  = float(closes[i])
        atr_val     = atr_data.get("atr_value", entry_orig * 0.002)
        stop_orig   = round(entry_orig - atr_val*3.0 if direction=="LONG"
                            else entry_orig + atr_val*3.0, 6)
        exit_tgts   = vol_intel.get(
            "exit_targets_long" if direction=="LONG" else "exit_targets_short", [])

        outcome_o, r_o = evaluate_outcome(
            direction, entry_orig, stop_orig, exit_tgts,
            highs[i+1: i+1+MAX_BARS], lows[i+1: i+1+MAX_BARS], MAX_BARS)

        last_trade_bar = i
        original_trades.append({
            "bar": i, "direction": direction, "grade": grade,
            "confidence": confidence, "entry": entry_orig,
            "stop": stop_orig, "outcome": outcome_o, "r": r_o,
            "hour": hour_now,
        })

        # ── CONFIRMATION CHECK (bar i+1) ───────────────────
        # Re-run indicators on slice ending at i+1
        try:
            slice_next = df.iloc[i - WINDOW + 1: i + 1].copy().reset_index(drop=True)
            pavp2      = compute_pavp(slice_next)
            atr2       = compute_atr(slice_next)
            vol2       = compute_volume_intelligence(slice_next, pavp2)
            wme2       = compute_wme_signal(slice_next, bar_tolerance=0)
            curr2      = wme2["current"]

            # Signal must still exist in same direction
            still_valid = (direction == "LONG"  and curr2["long_entry"]) or \
                          (direction == "SHORT" and curr2["short_entry"])
        except Exception:
            still_valid = False

        if still_valid:
            survived_signals += 1
            # Enter at next bar's close
            entry_conf = float(closes[i+1])
            atr_c      = atr2.get("atr_value", entry_conf * 0.002)
            stop_conf  = round(entry_conf - atr_c*3.0 if direction=="LONG"
                               else entry_conf + atr_c*3.0, 6)
            exit_tgts2 = vol2.get(
                "exit_targets_long" if direction=="LONG" else "exit_targets_short", [])

            outcome_c, r_c = evaluate_outcome(
                direction, entry_conf, stop_conf, exit_tgts2,
                highs[i+2: i+2+MAX_BARS], lows[i+2: i+2+MAX_BARS], MAX_BARS)

            confirmed_trades.append({
                "bar": i+1, "direction": direction, "grade": grade,
                "confidence": confidence, "entry": entry_conf,
                "stop": stop_conf, "outcome": outcome_c, "r": r_c,
                "hour": hour_now,
            })

    survival_rate = round(survived_signals / total_signals * 100, 1) if total_signals else 0
    return original_trades, confirmed_trades, total_signals, survived_signals, survival_rate


# ─────────────────────────────────────────────────────────────
# STATS CALCULATOR
# ─────────────────────────────────────────────────────────────

def calc_stats(trades):
    if not trades:
        return {"count": 0, "win_rate": 0, "avg_r": 0, "total_r": 0}
    wins   = sum(1 for t in trades if t["outcome"] == "WIN")
    total  = len(trades)
    rs     = [t["r"] for t in trades]
    return {
        "count":    total,
        "wins":     wins,
        "losses":   total - wins,
        "win_rate": round(wins / total * 100, 1),
        "avg_r":    round(sum(rs) / total, 3),
        "total_r":  round(sum(rs), 3),
        "best_r":   round(max(rs), 3),
        "worst_r":  round(min(rs), 3),
    }


# ─────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────

STRATEGIES = [
    {
        "key":           "BTC_G",
        "symbol":        "BTCUSDT",
        "interval":      "15",
        "window":        120,
        "max_bars":      60,
        "cooldown":      20,
        "allowed_hours": [7, 12, 14, 18],
        "allowed_grades": None,
    },
    {
        "key":           "BTC_L",
        "symbol":        "BTCUSDT",
        "interval":      "15",
        "window":        120,
        "max_bars":      60,
        "cooldown":      20,
        "allowed_hours": [7, 12, 14],
        "allowed_grades": ["B"],
    },
]

if __name__ == "__main__":
    print("\n" + "="*65)
    print("  CONFIRMATION BACKTEST — Original vs 2-Bar Confirmation")
    print("="*65)

    results = {}

    for strat in STRATEGIES:
        key = strat["key"]
        print(f"\n  Fetching {strat['symbol']} {strat['interval']}m data...")
        try:
            df = fetch_bybit(strat["symbol"], strat["interval"], bars=1000)
            print(f"  {len(df)} bars loaded")
        except Exception as e:
            print(f"  ERROR: {e}")
            continue

        print(f"  Scanning {key}...")
        orig, conf, total, survived, surv_rate = scan_signals(df, strat)

        orig_stats = calc_stats(orig)
        conf_stats = calc_stats(conf)

        results[key] = {
            "original":  orig_stats,
            "confirmed": conf_stats,
            "total_signals":   total,
            "survived_signals": survived,
            "survival_rate":   surv_rate,
        }

        print(f"\n  {'─'*55}")
        print(f"  Strategy: {key}")
        print(f"  {'─'*55}")
        print(f"  Total signals detected:    {total}")
        print(f"  Signals surviving to +1:   {survived} ({surv_rate}%)")
        print(f"")
        print(f"  {'Mode':<20} {'Trades':>7} {'Win%':>7} {'Avg R':>7} {'Total R':>8}")
        print(f"  {'─'*55}")
        print(f"  {'Original (bar 0)':<20} {orig_stats['count']:>7} "
              f"{orig_stats['win_rate']:>6}% {orig_stats['avg_r']:>7} {orig_stats['total_r']:>8}")
        print(f"  {'Confirmed (+1 bar)':<20} {conf_stats['count']:>7} "
              f"{conf_stats['win_rate']:>6}% {conf_stats['avg_r']:>7} {conf_stats['total_r']:>8}")
        print(f"  {'─'*55}")

        # Interpretation
        wr_diff = conf_stats["win_rate"] - orig_stats["win_rate"]
        r_diff  = conf_stats["avg_r"]    - orig_stats["avg_r"]
        missed  = total - survived

        print(f"\n  FINDINGS:")
        print(f"  Win rate change:  {wr_diff:+.1f}%")
        print(f"  Avg R change:     {r_diff:+.3f}R")
        print(f"  Signals missed:   {missed} ({100-surv_rate:.1f}% of all signals)")

        if wr_diff > 2 and r_diff > 0:
            verdict = "CONFIRMATION HELPS — consider adding"
        elif wr_diff < -2 or missed > total * 0.4:
            verdict = "CONFIRMATION HURTS — keep original entry"
        else:
            verdict = "NEUTRAL — confirmation has minimal impact"

        print(f"  Verdict:          {verdict}")

    print(f"\n{'='*65}")
    print("  SUMMARY")
    print(f"{'='*65}")
    for key, r in results.items():
        o = r["original"]
        c = r["confirmed"]
        print(f"\n  {key}:")
        print(f"    Original:  {o['win_rate']}% WR | {o['avg_r']}R avg | {o['count']} trades")
        print(f"    Confirmed: {c['win_rate']}% WR | {c['avg_r']}R avg | {c['count']} trades")
        print(f"    Survival:  {r['survival_rate']}% of signals persist to next bar")
    print(f"\n{'='*65}\n")
