"""
backtest_spy_1h.py
═══════════════════════════════════════════════════════════
SPY 1-hour validation test.

Uses Yahoo Finance 1h data (2 years available).
Runs G and L strategies with NY session hours.

NY session in UTC:
  13:00 = 9:00am ET  (pre-market active)
  14:00 = 10:00am ET (NY open + first hour peak)
  15:00 = 11:00am ET (mid-morning)
  19:00 = 3:00pm ET  (power hour)
═══════════════════════════════════════════════════════════
"""

import sys, os, json, time, warnings
import numpy as np
import pandas as pd
warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from indicator_engine      import compute_pavp, compute_trend_speed, compute_atr
from volume_profile_engine import compute_volume_intelligence, compute_cvd_triangles
from wme_sweep_engine      import compute_wme_signal, get_wme_confidence_modifier

# ─────────────────────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────────────────────

WINDOW            = 80    # fewer bars needed on 1h
MAX_BARS_IN_TRADE = 20    # 20 x 1h = ~3 trading days max
COOLDOWN          = 10    # 10 x 1h = 10 hours cooldown
STARTING_CAPITAL  = 1000.0
RISK_PER_TRADE    = 0.02

# NY session hours in UTC for 1h bars
# 13=9am, 14=10am, 15=11am, 19=3pm ET
HOURS_G = [13, 14, 15, 19]   # G strategy — broader
HOURS_L = [13, 14]            # L strategy — tightest (open + first hour only)


# ─────────────────────────────────────────────────────────────
# DATA FETCH
# ─────────────────────────────────────────────────────────────

def fetch_spy_1h():
    try:
        import yfinance as yf
    except ImportError:
        raise RuntimeError("yfinance not installed. Run: pip install yfinance")

    print("  Fetching SPY 1h from Yahoo Finance (2-year period)...")

    try:
        ticker = yf.Ticker("SPY")
        df     = ticker.history(period="730d", interval="1h", auto_adjust=True)
    except Exception as e:
        raise RuntimeError(f"Yahoo fetch failed: {e}")

    if df.empty:
        raise RuntimeError("No data returned for SPY 1h")

    df = df.reset_index()
    time_col = "Datetime" if "Datetime" in df.columns else "Date"
    df = df.rename(columns={
        time_col: "time", "Open": "open", "High": "high",
        "Low": "low", "Close": "close", "Volume": "volume"
    })
    df["time"] = pd.to_datetime(df["time"]).dt.tz_localize(None)
    df = (df[["time","open","high","low","close","volume"]]
          .dropna().sort_values("time").reset_index(drop=True))

    print(f"  Got {len(df)} bars  ({df.time.iloc[0].date()} to {df.time.iloc[-1].date()})")
    return df


# ─────────────────────────────────────────────────────────────
# OUTCOME EVALUATOR
# ─────────────────────────────────────────────────────────────

def evaluate_outcome(direction, entry_price, stop_loss, exit_targets,
                     future_highs, future_lows, max_bars):
    is_long       = direction == "LONG"
    targets_hit   = []
    remaining_pct = 100.0
    stop_dist     = abs(entry_price - stop_loss)
    if stop_dist == 0:
        stop_dist = entry_price * 0.003

    for bar in range(min(max_bars, len(future_highs))):
        bh = float(future_highs[bar])
        bl = float(future_lows[bar])

        if is_long and bl <= stop_loss:
            partial_r = sum(
                (t["price"]-entry_price)/stop_dist*(t["partial_pct"]/100)
                for t in targets_hit)
            return {"outcome": "WIN" if partial_r > 0.5 else "LOSS",
                    "bars_to_outcome": bar+1,
                    "r_multiple": round(partial_r-(remaining_pct/100), 3)}

        if not is_long and bh >= stop_loss:
            partial_r = sum(
                (entry_price-t["price"])/stop_dist*(t["partial_pct"]/100)
                for t in targets_hit)
            return {"outcome": "WIN" if partial_r > 0.5 else "LOSS",
                    "bars_to_outcome": bar+1,
                    "r_multiple": round(partial_r-(remaining_pct/100), 3)}

        for tgt in exit_targets:
            if tgt["price"] in [t["price"] for t in targets_hit]:
                continue
            if ((is_long and bh >= tgt["price"]) or
                    (not is_long and bl <= tgt["price"])):
                targets_hit.append(tgt)
                remaining_pct -= tgt["partial_pct"]

        if remaining_pct <= 5:
            r = sum(
                (t["price"]-entry_price if is_long else entry_price-t["price"])
                /stop_dist*(t["partial_pct"]/100) for t in targets_hit)
            return {"outcome":"WIN","bars_to_outcome":bar+1,
                    "r_multiple":round(r,3)}

    final = float(future_highs[-1] if is_long else future_lows[-1])
    r     = round((final-entry_price if is_long else entry_price-final)/stop_dist, 3)
    return {"outcome":"WIN" if r>=0 else "LOSS",
            "bars_to_outcome":max_bars,"r_multiple":r}


# ─────────────────────────────────────────────────────────────
# CONFIG RUNNER
# ─────────────────────────────────────────────────────────────

def run_config(df, config_name, allowed_hours, allowed_grades=None):
    n      = len(df)
    closes = df["close"].values
    highs  = df["high"].values
    lows   = df["low"].values
    times  = df["time"].values

    trade_log      = []
    no_trade_count = 0
    total_signals  = 0
    last_trade_bar = -999

    print(f"\n  [{config_name}] hours={allowed_hours} grades={allowed_grades}")

    for i in range(WINDOW, n - MAX_BARS_IN_TRADE - 1):
        total_signals += 1

        if i - last_trade_bar < COOLDOWN:
            continue

        slice_df = df.iloc[i - WINDOW: i].copy().reset_index(drop=True)

        try:
            pavp     = compute_pavp(slice_df)
            atr_data = compute_atr(slice_df)
            vol_intel= compute_volume_intelligence(slice_df, pavp)
            swing    = vol_intel.get("swing_profiles", {})
            wme      = compute_wme_signal(slice_df, bar_tolerance=0)
            curr     = wme["current"]
        except Exception:
            continue

        has_long  = curr["long_entry"]
        has_short = curr["short_entry"]

        if not has_long and not has_short:
            no_trade_count += 1
            continue

        direction = "LONG" if has_long else "SHORT"

        # Hour filter
        hour_now = pd.Timestamp(times[i]).hour
        if hour_now not in allowed_hours:
            no_trade_count += 1
            continue

        # SPY is long-biased — block shorts outside power hour
        # Only allow shorts at 19 UTC (3pm ET power hour)
        if direction == "SHORT" and hour_now != 19:
            no_trade_count += 1
            continue

        # Confidence and grade
        in_lvn  = swing.get("in_lvn", False)
        hvn_zones = swing.get("hvn_zones", [])
        current_price = float(closes[i-1])
        in_hvn  = any(z["price_low"] <= current_price <= z["price_high"]
                      for z in hvn_zones)

        wme_mod    = get_wme_confidence_modifier(wme, in_lvn, in_hvn, i)
        speed_abs  = abs(curr["speed"])
        confidence = round(min(100.0, max(0.0,
                        55.0 + min(speed_abs*15, 20) + wme_mod["modifier"])), 1)
        grade      = "A" if confidence >= 75 else "B" if confidence >= 60 else "C"

        if grade == "C":
            no_trade_count += 1
            continue

        if allowed_grades is not None and grade not in allowed_grades:
            no_trade_count += 1
            continue

        # Entry
        entry_price  = float(closes[i])
        atr_val      = atr_data.get("atr_value", entry_price * 0.002)
        stop_loss    = round(
            entry_price - atr_val*3.0 if direction=="LONG"
            else entry_price + atr_val*3.0, 6)
        exit_targets = vol_intel.get(
            "exit_targets_long" if direction=="LONG"
            else "exit_targets_short", [])

        result = evaluate_outcome(
            direction, entry_price, stop_loss, exit_targets,
            highs[i+1: i+1+MAX_BARS_IN_TRADE],
            lows[i+1:  i+1+MAX_BARS_IN_TRADE],
            MAX_BARS_IN_TRADE)

        last_trade_bar = i

        trade_log.append({
            "bar_index":       i,
            "timestamp":       str(pd.Timestamp(times[i])),
            "hour_of_day":     hour_now,
            "config":          config_name,
            "symbol":          "SPY",
            "timeframe":       "1h",
            "direction":       direction,
            "grade":           grade,
            "confidence":      confidence,
            "in_lvn":          in_lvn,
            "in_hvn":          in_hvn,
            "wme_speed":       round(float(curr["speed"]), 4),
            "entry_price":     round(entry_price, 4),
            "stop_loss":       round(stop_loss, 4),
            "targets_hit":     len(result.get("targets_hit", [])),
            "r_multiple":      result["r_multiple"],
            "outcome":         result["outcome"],
            "bars_to_outcome": result["bars_to_outcome"],
        })

    wins = sum(1 for t in trade_log if t["outcome"] == "WIN")
    n_t  = len(trade_log)
    wr   = round(wins/n_t*100, 1) if n_t else 0.0
    avg_r= round(sum(t["r_multiple"] for t in trade_log)/max(n_t,1), 3)
    print(f"         Trades={n_t}  WR={wr}%  AvgR={avg_r}")
    return trade_log, total_signals, no_trade_count


# ─────────────────────────────────────────────────────────────
# REPORT + PNL
# ─────────────────────────────────────────────────────────────

def build_report(trade_log, config_name, total_bars, total_signals, no_trade):
    df = pd.DataFrame(trade_log)
    n  = len(df)
    if n == 0:
        return {"config":config_name,"total_trades":0,"win_rate":0.0,"avg_r":0.0}

    wins  = (df.outcome=="WIN").sum()
    split = int(total_bars * 0.70)

    def _stats(sub):
        sn = len(sub)
        if sn == 0: return {"count":0,"wins":0,"win_rate":0.0,"avg_r":0.0}
        sw = int((sub.outcome=="WIN").sum())
        return {"count":int(sn),"wins":sw,
                "win_rate":round(sw/sn*100,1),
                "avg_r":round(float(sub.r_multiple.mean()),3)}

    def _grp(col, val):
        sub = df[df[col]==val]
        sn  = len(sub)
        sw  = (sub.outcome=="WIN").sum()
        return {"count":int(sn),
                "win_rate":round(sw/sn*100,1) if sn else 0.0,
                "avg_r":round(sub.r_multiple.mean(),3) if sn else 0.0}

    return {
        "config":        config_name,
        "symbol":        "SPY",
        "timeframe":     "1h",
        "total_bars":    total_bars,
        "total_signals": total_signals,
        "total_trades":  n,
        "wins":          int(wins),
        "win_rate":      round(wins/n*100, 1),
        "avg_r":         round(df.r_multiple.mean(), 3),
        "breakeven_wr":  "35.7% (at 1.8R)",
        "by_grade":      {g: _grp("grade",g) for g in ("A","B","C")},
        "by_direction":  {d: _grp("direction",d) for d in df.direction.unique()},
        "by_hour":       {str(h): _grp("hour_of_day",h)
                          for h in sorted(df.hour_of_day.unique())},
        "lvn_trades":    _grp("in_lvn", True),
        "robustness": {
            "first_70_pct": _stats(df[df.bar_index < split]),
            "last_30_pct":  _stats(df[df.bar_index >= split]),
            "first_half":   _stats(df.iloc[:n//2]),
            "second_half":  _stats(df.iloc[n//2:]),
        },
        "filter_effectiveness": {
            "total_signals": total_signals,
            "no_trade":      no_trade,
            "trades_taken":  n,
            "filter_rate":   round((total_signals-n)/max(total_signals,1)*100,1),
        },
    }


def simulate_pnl(trade_log):
    capital = STARTING_CAPITAL
    peak    = STARTING_CAPITAL
    max_dd  = 0.0
    for t in trade_log:
        capital += capital * RISK_PER_TRADE * t["r_multiple"]
        capital  = max(capital, 0.01)
        if capital > peak: peak = capital
        dd = (peak - capital) / peak * 100
        if dd > max_dd: max_dd = dd
    return {
        "final":            round(capital, 2),
        "peak":             round(peak, 2),
        "max_drawdown_pct": round(max_dd, 1),
        "total_return_pct": round((capital-STARTING_CAPITAL)/STARTING_CAPITAL*100, 1),
    }


# ─────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────

def main():
    print(f"\n{'='*60}")
    print(f"  SPY 1H VALIDATION TEST")
    print(f"  Yahoo Finance — 2 year period")
    print(f"  G strategy: hours {HOURS_G}")
    print(f"  L strategy: hours {HOURS_L}")
    print(f"{'='*60}")

    df = fetch_spy_1h()
    total_bars = len(df)

    if total_bars < WINDOW + MAX_BARS_IN_TRADE + 10:
        print(f"  Insufficient bars ({total_bars}). Exiting.")
        return

    t0      = time.time()
    results = {}

    configs = [
        ("SPY_1h_G", HOURS_G, None),
        ("SPY_1h_L", HOURS_L, ["B"]),
    ]

    for name, hours, grades in configs:
        tlog, tsig, nt = run_config(df, name, hours, grades)
        report         = build_report(tlog, name, total_bars, tsig, nt)
        report["pnl"]  = simulate_pnl(tlog)
        results[name]  = report

        pd.DataFrame(tlog).to_csv(f"{name}_trade_log.csv", index=False)
        with open(f"{name}_report.json","w") as f:
            json.dump(report, f, indent=2)

    # ── Summary table ─────────────────────────────────────────
    print(f"\n{'='*72}")
    print(f"  SPY 1H RESULTS  (${STARTING_CAPITAL:,.0f} start, {RISK_PER_TRADE*100:.0f}% risk)")
    print(f"{'='*72}")
    print(f"  {'Config':<16} {'Bars':>6} {'Trades':>7} {'Win%':>6} "
          f"{'AvgR':>6} {'Final$':>9} {'Ret%':>6} {'DD%':>6}")
    print(f"  {'-'*68}")

    for name, r in results.items():
        pnl = r.get("pnl", {})
        rob = r.get("robustness", {})
        fh  = rob.get("first_half",  {}).get("win_rate", 0)
        sh  = rob.get("second_half", {}).get("win_rate", 0)
        print(f"  {name:<16} {r['total_bars']:>6} {r['total_trades']:>7} "
              f"{r['win_rate']:>5.1f}% {r.get('avg_r',0):>6.3f} "
              f"${pnl.get('final',0):>8,.2f} "
              f"{pnl.get('total_return_pct',0):>5.1f}% "
              f"{pnl.get('max_drawdown_pct',0):>5.1f}%")
        print(f"  {'':16} Robustness: first_half={fh:.0f}%  second_half={sh:.0f}%")
        print(f"  {'':16} By hour: {r.get('by_hour',{})}")

    # Compare to 15m SPY result
    print(f"\n  Comparison to 15m SPY:")
    print(f"  SPY_15m_G: 7 trades  71.4% WR  +0.284R  $1,039 (+3.9%) ← 60d only")
    print(f"  SPY_15m_L: 7 trades  71.4% WR  +0.288R  $1,039 (+3.9%) ← 60d only")

    print(f"\n  Breakeven: 35.7% at 1.8R")
    print(f"  Time: {time.time()-t0:.1f}s")
    print(f"\n  Files: SPY_1h_G_trade_log.csv + SPY_1h_L_trade_log.csv")
    print(f"         SPY_1h_G_report.json + SPY_1h_L_report.json")


if __name__ == "__main__":
    main()
