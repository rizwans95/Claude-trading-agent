"""
backtest_v3.py
═══════════════════════════════════════════════════════════
Three-config backtest comparing WME Sweep integration.

Config A — WME strict   : WME cross+triangle same candle, no CVD required
Config B — WME relaxed  : WME cross+triangle within 1 bar, no CVD required
Config C — WME + CVD    : WME same candle AND CVD triangle stopped

Exit logic: level-based (POC/VAH/VAL from volume_profile_engine)
═══════════════════════════════════════════════════════════
"""

import sys, os, json, time, warnings, requests
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

WINDOW            = 120
MAX_BARS_IN_TRADE = 60
COOLDOWN          = 20
TOTAL_BARS        = 10000

# SSL patch for Windows
import requests as _req
_orig = _req.get
def _no_verify(url, **kw):
    kw.setdefault("verify", False)
    return _orig(url, **kw)
_req.get = _no_verify


# ─────────────────────────────────────────────────────────────
# DATA FETCH
# ─────────────────────────────────────────────────────────────

def fetch_binance(symbol="BTCUSDT", interval="15m", total_bars=TOTAL_BARS):
    url, bars, end_ms = "https://api.binance.com/api/v3/klines", [], None
    print(f"  Fetching {total_bars} bars ({symbol} {interval})...")
    while len(bars) < total_bars:
        params = {"symbol": symbol, "interval": interval,
                  "limit": min(1000, total_bars - len(bars))}
        if end_ms: params["endTime"] = end_ms
        try:
            r = requests.get(url, params=params, timeout=15)
            r.raise_for_status()
            chunk = r.json()
        except Exception as e:
            print(f"  Fetch error: {e}"); break
        if not chunk: break
        bars  = chunk + bars
        end_ms = int(chunk[0][0]) - 1
        time.sleep(0.2)
        if len(chunk) < 1000: break

    df = pd.DataFrame(bars, columns=[
        "open_time","open","high","low","close","volume",
        "close_time","qav","num_trades","tbbav","tbqav","ignore"])
    df["time"]   = pd.to_datetime(df["open_time"].astype(float), unit="ms")
    for c in ["open","high","low","close","volume"]:
        df[c] = df[c].astype(float)
    df = df[["time","open","high","low","close","volume"]].sort_values("time").reset_index(drop=True)
    print(f"  Got {len(df)} bars  ({df.time.iloc[0].date()} to {df.time.iloc[-1].date()})")
    return df


# ─────────────────────────────────────────────────────────────
# OUTCOME EVALUATOR (level-based exits)
# ─────────────────────────────────────────────────────────────

def evaluate_outcome(direction, entry_price, stop_loss, exit_targets,
                     future_highs, future_lows, max_bars):
    is_long       = direction == "LONG"
    targets_hit   = []
    remaining_pct = 100.0
    exit_price    = entry_price
    stop_dist     = abs(entry_price - stop_loss)
    if stop_dist == 0: stop_dist = entry_price * 0.003

    for bar in range(min(max_bars, len(future_highs))):
        bh = float(future_highs[bar])
        bl = float(future_lows[bar])

        # Stop check
        if is_long  and bl <= stop_loss:
            partial_r = sum((t["price"]-entry_price)/stop_dist*(t["partial_pct"]/100)
                            for t in targets_hit)
            return {"outcome": "WIN" if partial_r > 0.5 else "LOSS",
                    "bars_to_outcome": bar+1, "targets_hit": targets_hit,
                    "exit_price": round(stop_loss,6),
                    "r_multiple": round(partial_r - (remaining_pct/100), 3)}
        if not is_long and bh >= stop_loss:
            partial_r = sum((entry_price-t["price"])/stop_dist*(t["partial_pct"]/100)
                            for t in targets_hit)
            return {"outcome": "WIN" if partial_r > 0.5 else "LOSS",
                    "bars_to_outcome": bar+1, "targets_hit": targets_hit,
                    "exit_price": round(stop_loss,6),
                    "r_multiple": round(partial_r - (remaining_pct/100), 3)}

        # Target checks
        for tgt in exit_targets:
            if tgt["price"] in [t["price"] for t in targets_hit]: continue
            if (is_long and bh >= tgt["price"]) or (not is_long and bl <= tgt["price"]):
                targets_hit.append(tgt)
                remaining_pct -= tgt["partial_pct"]
                exit_price = tgt["price"]

        if remaining_pct <= 5:
            r = sum((t["price"]-entry_price if is_long else entry_price-t["price"])
                    /stop_dist*(t["partial_pct"]/100) for t in targets_hit)
            return {"outcome":"WIN","bars_to_outcome":bar+1,
                    "targets_hit":targets_hit,"exit_price":round(exit_price,6),
                    "r_multiple":round(r,3)}

    # Timeout
    final = float(future_highs[-1] if is_long else future_lows[-1])
    r     = round((final-entry_price if is_long else entry_price-final)/stop_dist, 3)
    return {"outcome":"WIN" if r>=0 else "LOSS","bars_to_outcome":max_bars,
            "targets_hit":targets_hit,"exit_price":round(final,6),"r_multiple":r}


# ─────────────────────────────────────────────────────────────
# SINGLE CONFIG BACKTEST
# ─────────────────────────────────────────────────────────────

def run_config(df, symbol, timeframe, config_name,
               bar_tolerance=0, require_cvd=False):
    """
    Run one backtest configuration.

    bar_tolerance: 0 = same candle, 1 = within 1 bar
    require_cvd:   True = CVD triangles must have stopped recently
    """
    n      = len(df)
    closes = df["close"].values
    highs  = df["high"].values
    lows   = df["low"].values
    times  = df["time"].values

    trade_log       = []
    no_trade_count  = 0
    total_signals   = 0
    last_trade_bar  = -999

    print(f"\n  [{config_name}] bar_tolerance={bar_tolerance} require_cvd={require_cvd}")

    for i in range(WINDOW, n - MAX_BARS_IN_TRADE - 1):

        total_signals += 1

        # Cooldown
        if i - last_trade_bar < COOLDOWN:
            continue

        slice_df = df.iloc[i - WINDOW: i].copy().reset_index(drop=True)

        try:
            # Core indicators
            pavp     = compute_pavp(slice_df)
            atr_data = compute_atr(slice_df)
            vol_intel= compute_volume_intelligence(slice_df, pavp)
            swing    = vol_intel.get("swing_profiles", {})

            # WME signal
            wme = compute_wme_signal(slice_df, bar_tolerance=bar_tolerance)
            curr = wme["current"]

        except Exception:
            continue

        # Check for any WME entry signal
        has_long  = curr["long_entry"]
        has_short = curr["short_entry"]

        if not has_long and not has_short:
            no_trade_count += 1
            continue

        direction = "LONG" if has_long else "SHORT"

        # CVD filter (Config C only)
        if require_cvd:
            try:
                tri = compute_cvd_triangles(slice_df, cost_rank_thresh=75.0)
                if direction == "LONG":
                    # Downward triangles (upward arrows) must have just stopped
                    if tri.get("bars_since_up", 999) != 1:
                        no_trade_count += 1
                        continue
                else:
                    # Upward triangles (downward arrows) must have just stopped
                    if tri.get("bars_since_down", 999) != 1:
                        no_trade_count += 1
                        continue
            except Exception:
                no_trade_count += 1
                continue

        # Location context
        in_lvn = swing.get("in_lvn", False)
        hvn_zones = swing.get("hvn_zones", [])
        current_price = float(closes[i-1])
        in_hvn = any(
            z["price_low"] <= current_price <= z["price_high"]
            for z in hvn_zones
        )

        # Confidence
        wme_mod = get_wme_confidence_modifier(wme, in_lvn, in_hvn, i)
        base_conf = 55.0
        speed_abs = abs(curr["speed"])
        base_conf += min(speed_abs * 15, 20)
        base_conf += wme_mod["modifier"]
        confidence = round(min(100.0, max(0.0, base_conf)), 1)
        grade      = "A" if confidence >= 75 else "B" if confidence >= 60 else "C"

        # Skip grade C
        if grade == "C":
            no_trade_count += 1
            continue

        # Entry and stop
        entry_price = float(closes[i])
        atr_val     = atr_data.get("atr_value", entry_price * 0.002)
        stop_loss   = round(entry_price - atr_val*3.0, 6) if direction=="LONG" \
                      else round(entry_price + atr_val*3.0, 6)

        # Exit targets
        exit_targets = vol_intel.get("exit_targets_long"  if direction=="LONG"
                                     else "exit_targets_short", [])

        # Simulate outcome
        result = evaluate_outcome(
            direction, entry_price, stop_loss, exit_targets,
            highs[i+1: i+1+MAX_BARS_IN_TRADE],
            lows[i+1:  i+1+MAX_BARS_IN_TRADE],
            MAX_BARS_IN_TRADE
        )

        outcome    = result["outcome"]
        bars_to    = result["bars_to_outcome"]
        r_multiple = result["r_multiple"]

        last_trade_bar = i

        trade_log.append({
            "bar_index":          i,
            "timestamp":          str(pd.Timestamp(times[i])),
            "hour_of_day":        pd.Timestamp(times[i]).hour,
            "config":             config_name,
            "symbol":             symbol,
            "timeframe":          timeframe,
            "direction":          direction,
            "grade":              grade,
            "confidence":         confidence,
            "in_lvn":             in_lvn,
            "in_hvn":             in_hvn,
            "wme_speed":          round(float(curr["speed"]), 4),
            "wme_sweep_bars_ago": int(min(curr["bars_since_sweep_low"],
                                         curr["bars_since_sweep_high"])),
            "wme_sweep_count_20": int(curr["sweep_count_20"]),
            "htf_bull":           bool(curr["htf_bull"]),
            "entry_price":        round(entry_price, 6),
            "stop_loss":          round(stop_loss, 6),
            "targets_hit":        len(result["targets_hit"]),
            "r_multiple":         r_multiple,
            "outcome":            outcome,
            "bars_to_outcome":    bars_to,
        })

    wins = sum(1 for t in trade_log if t["outcome"] == "WIN")
    n_t  = len(trade_log)
    wr   = round(wins/n_t*100, 1) if n_t else 0.0

    print(f"         Trades: {n_t}  WR: {wr}%  Avg R: "
          f"{round(sum(t['r_multiple'] for t in trade_log)/max(n_t,1),3)}")

    return trade_log, total_signals, no_trade_count


# ─────────────────────────────────────────────────────────────
# REPORT BUILDER
# ─────────────────────────────────────────────────────────────

def build_report(trade_log, config_name, symbol, timeframe,
                 total_bars, total_signals, no_trade_count):
    df = pd.DataFrame(trade_log)
    n  = len(df)
    if n == 0:
        return {"config": config_name, "total_trades": 0, "win_rate": 0.0}

    wins = (df.outcome=="WIN").sum()
    wr   = round(wins/n*100, 1)
    avg_r= round(df.r_multiple.mean(), 3)

    def _grp(col, val):
        sub = df[df[col]==val]
        sn  = len(sub)
        sw  = (sub.outcome=="WIN").sum()
        return {"count":int(sn),
                "win_rate":round(sw/sn*100,1) if sn else 0.0,
                "avg_r":round(sub.r_multiple.mean(),3) if sn else 0.0}

    return {
        "config":        config_name,
        "symbol":        symbol,
        "timeframe":     timeframe,
        "total_bars":    total_bars,
        "total_signals": total_signals,
        "total_trades":  n,
        "wins":          int(wins),
        "win_rate":      wr,
        "avg_r":         avg_r,
        "breakeven_wr":  "35.7% (at 1.8R)",

        "by_grade": {
            g: _grp("grade", g) for g in ("A","B","C")
        },
        "by_direction": {
            d: _grp("direction", d)
            for d in df.direction.unique()
        },
        "lvn_trades": _grp("in_lvn", True),
        "hvn_trades": _grp("in_hvn", True),

        "by_hour": {
            str(h): _grp("hour_of_day", h)
            for h in sorted(df.hour_of_day.unique())
        },

        "filter_effectiveness": {
            "total_signals": total_signals,
            "no_trade":      no_trade_count,
            "trades_taken":  n,
            "filter_rate":   round((total_signals-n)/max(total_signals,1)*100,1),
        }
    }


# ─────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────

def main():
    symbol, timeframe = "BTCUSDT", "15m"

    print(f"\n{'='*60}")
    print(f"  TRADING AGENT V3 — WME SWEEP BACKTEST")
    print(f"  {symbol} {timeframe.upper()}  |  3 Configurations")
    print(f"{'='*60}")

    df = fetch_binance(symbol, timeframe, TOTAL_BARS)

    configs = [
        ("A_WME_strict",   0, False),
        ("B_WME_relaxed",  1, False),
        ("C_WME_CVD",      0, True),
    ]

    all_results = {}

    t0 = time.time()
    for name, tol, cvd in configs:
        tlog, tsig, ntrade = run_config(
            df, symbol, timeframe, name,
            bar_tolerance=tol, require_cvd=cvd
        )
        report = build_report(tlog, name, symbol, timeframe,
                              len(df), tsig, ntrade)
        all_results[name] = report

        # Save per-config files
        pd.DataFrame(tlog).to_csv(f"BTCUSDT_15m_{name}_trade_log.csv", index=False)
        with open(f"BTCUSDT_15m_{name}_report.json","w") as f:
            json.dump(report, f, indent=2)

    print(f"\n  Total time: {time.time()-t0:.1f}s")

    # Dollar P&L Simulation
    STARTING_CAPITAL = 1000.0
    RISK_PER_TRADE   = 0.02   # 2% of current capital per trade

    pnl_results = {}
    for name, report in all_results.items():
        csv_path = f"BTCUSDT_15m_{name}_trade_log.csv"
        try:
            tdf = pd.read_csv(csv_path)
        except Exception:
            pnl_results[name] = {"final": STARTING_CAPITAL, "peak": STARTING_CAPITAL,
                                  "max_drawdown_pct": 0.0, "total_return_pct": 0.0}
            continue
        if len(tdf) == 0:
            pnl_results[name] = {"final": STARTING_CAPITAL, "peak": STARTING_CAPITAL,
                                  "max_drawdown_pct": 0.0, "total_return_pct": 0.0}
            continue

        capital = STARTING_CAPITAL
        peak    = STARTING_CAPITAL
        max_dd  = 0.0

        for _, row in tdf.iterrows():
            risk_dollars = capital * RISK_PER_TRADE
            capital     += risk_dollars * row["r_multiple"]
            capital      = max(capital, 0.01)
            if capital > peak:
                peak = capital
            dd = (peak - capital) / peak * 100
            if dd > max_dd:
                max_dd = dd

        total_return = round((capital - STARTING_CAPITAL) / STARTING_CAPITAL * 100, 1)
        pnl_results[name] = {
            "final":            round(capital, 2),
            "peak":             round(peak, 2),
            "max_drawdown_pct": round(max_dd, 1),
            "total_return_pct": total_return,
        }
        all_results[name]["pnl"] = pnl_results[name]

    print(f"\n{'='*72}")
    print(f"  COMPARISON SUMMARY  (starting capital: ${STARTING_CAPITAL:,.0f})")
    print(f"{'='*72}")
    print(f"  {'Config':<22} {'Trades':>6} {'Win%':>6} {'Avg R':>6} "
          f"{'Final $':>9} {'Return%':>8} {'MaxDD%':>7}")
    print(f"  {'-'*68}")
    for name, report in all_results.items():
        pnl = pnl_results.get(name, {})
        print(f"  {name:<22} {report['total_trades']:>6} "
              f"{report['win_rate']:>5.1f}% {report['avg_r']:>6.3f} "
              f"${pnl.get('final', 0):>8,.2f} "
              f"{pnl.get('total_return_pct', 0):>7.1f}% "
              f"{pnl.get('max_drawdown_pct', 0):>6.1f}%")
    print(f"\n  Breakeven win rate: 35.7%  (at 1.8R)")
    print(f"  Risk per trade:     2% of capital (compounding)")
    print(f"  Starting capital:   ${STARTING_CAPITAL:,.0f}")

    with open("BTCUSDT_15m_v3_comparison.json","w") as f:
        json.dump(all_results, f, indent=2)
    print(f"\n  Files saved: 3 CSV + 3 JSON + 1 comparison JSON")


if __name__ == "__main__":
    main()
