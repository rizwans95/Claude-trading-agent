"""
backtest_wme_cvd_comparison.py  v2
═══════════════════════════════════════════════════════════
Entry trigger comparison on BTCUSDT — 15m AND 1h

Config A — WME Only
  Entry: WME sweep + cross + triangle (same candle)
  CVD:   Not required

Config B — WME + CVD Both Required
  Entry: WME sweep + cross + triangle (same candle)
         AND CVD triangles must have just stopped

Config C — CVD Only
  Entry: CVD triangles must have just stopped
  WME:   Not required

Baseline — BTC_G locked strategy (from backtest results)
  15m: 28 trades  82.1% WR  +0.172 avg R  +15.0% return

Timeframes: 15m and 1h
Session hours: 7, 12, 14, 18 UTC
Starting capital: $1,000 at 2% risk per trade
═══════════════════════════════════════════════════════════
"""

import sys, os, json, time, warnings, requests
import numpy as np
import pandas as pd
warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from indicator_engine      import compute_pavp, compute_atr
from volume_profile_engine import compute_volume_intelligence, compute_cvd_triangles
from wme_sweep_engine      import compute_wme_signal, get_wme_confidence_modifier

# SSL patch
import requests as _req
_orig = _req.get
def _no_verify(url, **kw):
    kw.setdefault("verify", False)
    return _orig(url, **kw)
_req.get = _no_verify

STARTING_CAPITAL = 1000.0
RISK_PER_TRADE   = 0.02
SESSION_HOURS    = [7, 12, 14, 18]

TIMEFRAMES = [
    {
        "label":          "15m",
        "bybit_interval": "15",
        "window":         120,
        "max_bars":       60,
        "cooldown":       20,
        "total_bars":     10000,
    },
    {
        "label":          "1h",
        "bybit_interval": "60",
        "window":         80,
        "max_bars":       20,
        "cooldown":       8,
        "total_bars":     5000,
    },
]

CONFIGS = [
    {
        "name":        "A_WME_ONLY",
        "require_wme": True,
        "require_cvd": False,
    },
    {
        "name":        "B_WME_AND_CVD",
        "require_wme": True,
        "require_cvd": True,
    },
    {
        "name":        "C_CVD_ONLY",
        "require_wme": False,
        "require_cvd": True,
    },
]

BASELINE = {
    "15m": {"trades":28,"win_rate":82.1,"avg_r":0.172,"return_pct":15.0,"max_dd":4.1,"final":1150.0},
    "1h":  None,
}


# ─────────────────────────────────────────────────────────────
# DATA FETCH
# ─────────────────────────────────────────────────────────────

def fetch_bybit(symbol, interval, total_bars):
    url, bars, end_ms = "https://api.bybit.com/v5/market/kline", [], None
    print(f"  Fetching {total_bars} bars ({symbol} {interval}) from Bybit...")
    while len(bars) < total_bars:
        params = {
            "category": "linear",
            "symbol":   symbol,
            "interval": interval,
            "limit":    min(1000, total_bars - len(bars)),
        }
        if end_ms:
            params["end"] = end_ms
        try:
            r = requests.get(url, params=params, timeout=20)
            r.raise_for_status()
            p = r.json()
        except Exception as e:
            print(f"  Fetch error: {e}")
            break
        if p.get("retCode") != 0:
            raise RuntimeError(f"Bybit error: {p.get('retMsg')}")
        chunk = p.get("result", {}).get("list", [])
        if not chunk:
            break
        bars.extend(chunk)
        end_ms = min(int(row[0]) for row in chunk) - 1
        time.sleep(0.2)
        if len(chunk) < 1000:
            break

    if not bars:
        raise RuntimeError(f"No data returned for {symbol} {interval}")

    df = pd.DataFrame(bars, columns=[
        "open_time","open","high","low","close","volume","turnover"])
    df["time"] = pd.to_datetime(df["open_time"].astype(float), unit="ms")
    for c in ["open","high","low","close","volume"]:
        df[c] = df[c].astype(float)
    df = (df[["time","open","high","low","close","volume"]]
          .drop_duplicates("time").sort_values("time")
          .tail(total_bars).reset_index(drop=True))
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
    stop_dist     = abs(entry_price - stop_loss) or entry_price * 0.003

    for bar in range(min(max_bars, len(future_highs))):
        bh = float(future_highs[bar])
        bl = float(future_lows[bar])

        if is_long and bl <= stop_loss:
            pr = sum((t["price"]-entry_price)/stop_dist*(t["partial_pct"]/100)
                     for t in targets_hit)
            return {"outcome":"WIN" if pr>0.5 else "LOSS",
                    "r":round(pr-(remaining_pct/100),3)}
        if not is_long and bh >= stop_loss:
            pr = sum((entry_price-t["price"])/stop_dist*(t["partial_pct"]/100)
                     for t in targets_hit)
            return {"outcome":"WIN" if pr>0.5 else "LOSS",
                    "r":round(pr-(remaining_pct/100),3)}

        for tgt in exit_targets:
            if tgt["price"] in [t["price"] for t in targets_hit]:
                continue
            if ((is_long  and bh >= tgt["price"]) or
                    (not is_long and bl <= tgt["price"])):
                targets_hit.append(tgt)
                remaining_pct -= tgt["partial_pct"]

        if remaining_pct <= 5:
            r = sum((t["price"]-entry_price if is_long else entry_price-t["price"])
                    /stop_dist*(t["partial_pct"]/100) for t in targets_hit)
            return {"outcome":"WIN","r":round(r,3)}

    final = float(future_highs[-1] if is_long else future_lows[-1])
    r     = round((final-entry_price if is_long else entry_price-final)/stop_dist, 3)
    return {"outcome":"WIN" if r>=0 else "LOSS","r":r}


# ─────────────────────────────────────────────────────────────
# SINGLE CONFIG BACKTEST
# ─────────────────────────────────────────────────────────────

def run_config(df, tf_cfg, cfg):
    n        = len(df)
    closes   = df["close"].values
    highs    = df["high"].values
    lows     = df["low"].values
    times    = df["time"].values
    WINDOW   = tf_cfg["window"]
    MAX_BARS = tf_cfg["max_bars"]
    COOLDOWN = tf_cfg["cooldown"]

    trades = []
    last   = -999

    for i in range(WINDOW, n - MAX_BARS - 1):
        if i - last < COOLDOWN:
            continue

        slice_df = df.iloc[i-WINDOW:i].copy().reset_index(drop=True)

        try:
            pavp     = compute_pavp(slice_df)
            atr_data = compute_atr(slice_df)
            vi       = compute_volume_intelligence(slice_df, pavp)
            swing    = vi.get("swing_profiles", {})
        except Exception:
            continue

        hour_now = pd.Timestamp(times[i]).hour
        if hour_now not in SESSION_HOURS:
            continue

        # ── Evaluate WME signal ───────────────────────────────
        wme_long  = False
        wme_short = False
        wme_curr  = {}

        if cfg["require_wme"]:
            try:
                wme      = compute_wme_signal(slice_df, bar_tolerance=0)
                wme_curr = wme["current"]
                wme_long  = wme_curr.get("long_entry",  False)
                wme_short = wme_curr.get("short_entry", False)
            except Exception:
                continue
            if not wme_long and not wme_short:
                continue

        # ── Evaluate CVD signal ───────────────────────────────
        cvd_long  = False
        cvd_short = False

        try:
            tri = compute_cvd_triangles(slice_df, cost_rank_thresh=75.0)
            # Upward triangles just stopped = BULLISH ENTRY (go LONG)
            if tri.get("bars_since_up", 999) == 1:
                cvd_long = True
            # Downward triangles just stopped = BEARISH ENTRY (go SHORT)
            if tri.get("bars_since_down", 999) == 1:
                cvd_short = True
        except Exception:
            if cfg["require_cvd"]:
                continue

        if cfg["require_cvd"] and not cvd_long and not cvd_short:
            continue

        # ── Determine direction ───────────────────────────────
        if cfg["require_wme"] and cfg["require_cvd"]:
            # Both required — direction must agree
            if wme_long  and cvd_long:  direction = "LONG"
            elif wme_short and cvd_short: direction = "SHORT"
            else: continue  # signals disagree — skip

        elif cfg["require_wme"] and not cfg["require_cvd"]:
            # WME only
            direction = "LONG" if wme_long else "SHORT"

        else:
            # CVD only
            if cvd_long and not cvd_short:
                direction = "LONG"
            elif cvd_short and not cvd_long:
                direction = "SHORT"
            elif cvd_long and cvd_short:
                # Both firing — use WME as tiebreaker if available
                try:
                    if not wme_curr:
                        wme = compute_wme_signal(slice_df, bar_tolerance=0)
                        wme_curr = wme["current"]
                    if wme_curr.get("long_entry"):  direction = "LONG"
                    elif wme_curr.get("short_entry"): direction = "SHORT"
                    else: direction = "LONG"
                except Exception:
                    direction = "LONG"
            else:
                continue

        # ── Confidence and grade ──────────────────────────────
        in_lvn = swing.get("in_lvn", False)
        hvn_z  = swing.get("hvn_zones", [])
        cp     = float(closes[i-1])
        in_hvn = any(z["price_low"]<=cp<=z["price_high"] for z in hvn_z)

        # For CVD-only, compute WME just for confidence modifier
        if not wme_curr:
            try:
                wme      = compute_wme_signal(slice_df, bar_tolerance=0)
                wme_curr = wme["current"]
            except Exception:
                wme_curr = {"speed": 0.0}

        wm   = get_wme_confidence_modifier(
            {"current": wme_curr,
             "sweep_low": np.array([False]),
             "sweep_high": np.array([False]),
             "sweep_low_count_20": np.array([0]),
             "sweep_high_count_20": np.array([0])},
            in_lvn, in_hvn, i
        ) if wme_curr else {"modifier": 0, "notes": []}

        spd  = abs(wme_curr.get("speed", 0.0))
        conf = round(min(100, max(0, 55+min(spd*15,20)+wm["modifier"])), 1)
        grade = "A" if conf>=75 else "B" if conf>=60 else "C"

        if grade == "C":
            continue

        # ── Trade simulation ──────────────────────────────────
        ep  = float(closes[i])
        atr = atr_data.get("atr_value", ep*0.002)
        sl  = round(ep-atr*3 if direction=="LONG" else ep+atr*3, 6)
        et  = vi.get("exit_targets_long" if direction=="LONG"
                     else "exit_targets_short", [])

        res  = evaluate_outcome(direction, ep, sl, et,
                                highs[i+1:i+1+MAX_BARS],
                                lows[i+1:i+1+MAX_BARS], MAX_BARS)
        last = i

        trades.append({
            "direction": direction,
            "grade":     grade,
            "hour":      hour_now,
            "outcome":   res["outcome"],
            "r":         res["r"],
            "in_lvn":    in_lvn,
        })

    return trades


# ─────────────────────────────────────────────────────────────
# PNL + REPORT
# ─────────────────────────────────────────────────────────────

def simulate_pnl(trades):
    capital = STARTING_CAPITAL
    peak    = STARTING_CAPITAL
    max_dd  = 0.0
    for t in trades:
        capital += capital * RISK_PER_TRADE * t["r"]
        capital  = max(capital, 0.01)
        if capital > peak: peak = capital
        dd = (peak-capital)/peak*100
        if dd > max_dd: max_dd = dd
    return {
        "final":      round(capital, 2),
        "return_pct": round((capital-STARTING_CAPITAL)/STARTING_CAPITAL*100, 1),
        "max_dd":     round(max_dd, 1),
    }


def build_report(trades, config_name, tf_label):
    n    = len(trades)
    wins = sum(1 for t in trades if t["outcome"]=="WIN")
    wr   = round(wins/n*100,1) if n else 0.0
    avgr = round(sum(t["r"] for t in trades)/max(n,1),3)
    pnl  = simulate_pnl(trades)

    by_dir = {}
    for d in ["LONG","SHORT"]:
        sub = [t for t in trades if t["direction"]==d]
        sn  = len(sub); sw = sum(1 for t in sub if t["outcome"]=="WIN")
        if sn:
            by_dir[d] = {"count":sn,"win_rate":round(sw/sn*100,1)}

    by_hour = {}
    for h in SESSION_HOURS:
        sub = [t for t in trades if t["hour"]==h]
        sn  = len(sub); sw = sum(1 for t in sub if t["outcome"]=="WIN")
        if sn:
            by_hour[str(h)] = {"count":sn,"win_rate":round(sw/sn*100,1)}

    lvn = [t for t in trades if t["in_lvn"]]
    ln  = len(lvn); lw = sum(1 for t in lvn if t["outcome"]=="WIN")

    return {
        "config":       config_name,
        "timeframe":    tf_label,
        "trades":       n,
        "wins":         wins,
        "win_rate":     wr,
        "avg_r":        avgr,
        "final":        pnl["final"],
        "return_pct":   pnl["return_pct"],
        "max_dd":       pnl["max_dd"],
        "by_direction": by_dir,
        "by_hour":      by_hour,
        "lvn_trades":   {"count":ln,"win_rate":round(lw/ln*100,1) if ln else 0.0},
    }


# ─────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────

def main():
    symbol = "BTCUSDT"

    print(f"\n{'='*72}")
    print(f"  WME vs CVD vs WME+CVD ENTRY TRIGGER COMPARISON  v2")
    print(f"  Asset: {symbol}  |  Timeframes: 15m and 1h")
    print(f"  Hours: {SESSION_HOURS} UTC  |  ${STARTING_CAPITAL:,.0f} at {RISK_PER_TRADE*100:.0f}% risk")
    print(f"{'='*72}")

    all_results = {}
    t0 = time.time()

    for tf in TIMEFRAMES:
        tf_label = tf["label"]
        print(f"\n  --- {tf_label} ---")

        try:
            df = fetch_bybit(symbol, tf["bybit_interval"], tf["total_bars"])
        except Exception as e:
            print(f"  FETCH FAILED: {e}")
            continue

        if len(df) < tf["window"] + tf["max_bars"] + 10:
            print(f"  Insufficient bars ({len(df)}). Skipping.")
            continue

        all_results[tf_label] = {}

        for cfg in CONFIGS:
            print(f"  Running {cfg['name']}...")
            try:
                trades = run_config(df, tf, cfg)
                report = build_report(trades, cfg["name"], tf_label)
                all_results[tf_label][cfg["name"]] = report
                print(f"    -> {report['trades']} trades  WR={report['win_rate']}%  "
                      f"AvgR={report['avg_r']}  ${report['final']:,.2f} "
                      f"({report['return_pct']:+.1f}%)")
            except Exception as e:
                print(f"    ERROR: {e}")
                import traceback; traceback.print_exc()

    # ── Comparison Table ──────────────────────────────────────
    print(f"\n{'='*72}")
    print(f"  FULL COMPARISON TABLE")
    print(f"{'='*72}")
    print(f"  {'Config':<20} {'TF':<5} {'Trades':>7} {'Win%':>6} "
          f"{'Avg R':>6} {'Final$':>9} {'Ret%':>7} {'DD%':>6}")
    print(f"  {'-'*68}")

    for tf_label in ["15m","1h"]:
        if tf_label not in all_results:
            continue
        for cfg_name, r in all_results[tf_label].items():
            print(f"  {cfg_name:<20} {tf_label:<5} {r['trades']:>7} "
                  f"{r['win_rate']:>5.1f}% {r['avg_r']:>6.3f} "
                  f"${r['final']:>8,.2f} {r['return_pct']:>6.1f}% "
                  f"{r['max_dd']:>5.1f}%")

        # Baseline
        b = BASELINE.get(tf_label)
        if b:
            print(f"  {'D_LOCKED_BASELINE':<20} {tf_label:<5} {b['trades']:>7} "
                  f"{b['win_rate']:>5.1f}% {b['avg_r']:>6.3f} "
                  f"${b['final']:>8,.2f} {b['return_pct']:>6.1f}% "
                  f"{b['max_dd']:>5.1f}%  <- LOCKED")
        print()

    # ── Verdict ───────────────────────────────────────────────
    print(f"{'='*72}")
    print(f"  VERDICT")
    print(f"{'='*72}")

    for tf_label in ["15m","1h"]:
        if tf_label not in all_results:
            continue
        print(f"\n  {tf_label}:")
        r_a = all_results[tf_label].get("A_WME_ONLY", {})
        r_b = all_results[tf_label].get("B_WME_AND_CVD", {})
        r_c = all_results[tf_label].get("C_CVD_ONLY", {})

        for label, r in [("WME only    ", r_a),
                          ("WME + CVD   ", r_b),
                          ("CVD only    ", r_c)]:
            if r.get("trades",0) > 0:
                print(f"    {label}: {r['win_rate']:>5.1f}% WR  "
                      f"{r['return_pct']:>+6.1f}%  (n={r['trades']})")

        # Best config
        best = max(
            [("A_WME_ONLY",r_a),("B_WME_AND_CVD",r_b),("C_CVD_ONLY",r_c)],
            key=lambda x: x[1].get("return_pct",-999) if x[1].get("trades",0)>=5 else -999
        )
        if best[1].get("trades",0) >= 5:
            print(f"\n    Best on {tf_label}: {best[0]}  "
                  f"({best[1]['win_rate']}% WR  {best[1]['return_pct']:+.1f}%)")

            # CVD standalone verdict
            if r_c.get("trades",0) >= 5:
                cvd_wr = r_c["win_rate"]
                wme_wr = r_a.get("win_rate",0)
                if cvd_wr >= 75:
                    print(f"    CVD standalone: STRONG independent edge ({cvd_wr}% WR)")
                elif cvd_wr >= 35.7:
                    print(f"    CVD standalone: Above breakeven ({cvd_wr}% WR) but weaker than WME ({wme_wr}%)")
                else:
                    print(f"    CVD standalone: Below breakeven ({cvd_wr}% WR) -- needs WME context")

    print(f"\n  Time: {time.time()-t0:.1f}s")

    with open("wme_cvd_comparison_results.json","w") as f:
        json.dump(all_results, f, indent=2)
    print(f"  Saved: wme_cvd_comparison_results.json")


if __name__ == "__main__":
    main()
