"""
backtest_btc_15m_1h.py
═══════════════════════════════════════════════════════════════
BTC Backtest — 15m vs 1H comparison using live strategy logic.

Strategies tested:
  BTC_15m_G  — BTCUSDT 15m  hours 7,12,14,18  Grade A+B
  BTC_15m_L  — BTCUSDT 15m  hours 7,12,14     Grade B only
  BTC_1H_G   — BTCUSDT 1H   hours 7,12,14,18  Grade A+B  ← NEW
  BTC_1H_L   — BTCUSDT 1H   hours 7,12,14     Grade B only ← NEW

MM configs:
  MM2  2% fixed risk   (current live setting)
  MM3  3% fixed risk
  MM7  After-win scale (best performer in v4)

Starting capital: $52.50  (your actual balance)

Run on VPS:
  cd /root/trading
  python3 backtest_btc_15m_1h.py

Output:
  - Console comparison table
  - backtest_btc_15m_1h_results.json
═══════════════════════════════════════════════════════════════
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
# STRATEGY DEFINITIONS
# ─────────────────────────────────────────────────────────────

STARTING_CAPITAL = 52.50   # your real balance

STRATEGIES = [
    {
        "key":            "BTC_15m_G",
        "symbol":         "BTCUSDT",
        "timeframe":      "15m",
        "bybit_interval": "15",
        "total_bars":     10000,
        "window":         120,
        "max_bars":       60,
        "cooldown":       20,
        "allowed_hours":  [7, 12, 14, 18],
        "allowed_grades": None,       # A and B
        "mode":           "G",
    },
    {
        "key":            "BTC_15m_L",
        "symbol":         "BTCUSDT",
        "timeframe":      "15m",
        "bybit_interval": "15",
        "total_bars":     10000,
        "window":         120,
        "max_bars":       60,
        "cooldown":       20,
        "allowed_hours":  [7, 12, 14],
        "allowed_grades": ["B"],
        "mode":           "L",
    },
    {
        "key":            "BTC_1H_G",
        "symbol":         "BTCUSDT",
        "timeframe":      "1H",
        "bybit_interval": "60",
        "total_bars":     3000,       # ~4 months of 1H bars
        "window":         80,
        "max_bars":       20,         # 20 hours to resolve
        "cooldown":       8,          # 8 bars cooldown
        "allowed_hours":  [7, 12, 14, 18],
        "allowed_grades": None,
        "mode":           "G",
    },
    {
        "key":            "BTC_1H_L",
        "symbol":         "BTCUSDT",
        "timeframe":      "1H",
        "bybit_interval": "60",
        "total_bars":     3000,
        "window":         80,
        "max_bars":       20,
        "cooldown":       8,
        "allowed_hours":  [7, 12, 14],
        "allowed_grades": ["B"],
        "mode":           "L",
    },
]

# ─────────────────────────────────────────────────────────────
# MM CONFIGS (3 most relevant)
# ─────────────────────────────────────────────────────────────

MM_CONFIGS = [
    {
        "name":      "MM2_FIXED_2PCT",
        "base_risk": 0.02,
    },
    {
        "name":      "MM3_FIXED_3PCT",
        "base_risk": 0.03,
    },
    {
        "name":      "MM7_AFTER_WIN_SCALE",
        "base_risk": 0.02,
        "scale_wins": True,
        "win_step":  0.0025,
        "win_cap":   0.03,
    },
]

# ─────────────────────────────────────────────────────────────
# DATA FETCHER
# ─────────────────────────────────────────────────────────────

def fetch_bybit(symbol, bybit_interval, total_bars):
    """Fetch OHLCV from Bybit in batches of 1000."""
    url  = "https://api.bybit.com/v5/market/kline"
    bars = []
    end_ms = None

    print(f"    Fetching {total_bars} bars ({bybit_interval}m)...", end="", flush=True)

    while len(bars) < total_bars:
        params = {
            "category": "linear",
            "symbol":   symbol,
            "interval": bybit_interval,
            "limit":    min(1000, total_bars - len(bars)),
        }
        if end_ms:
            params["end"] = end_ms

        try:
            r    = requests.get(url, params=params, timeout=15)
            data = r.json()
        except Exception as e:
            print(f"\n    [ERROR] Bybit fetch failed: {e}")
            break

        batch = data.get("result", {}).get("list", [])
        if not batch:
            break

        bars.extend(batch)
        end_ms = int(batch[-1][0]) - 1

        if len(batch) < 1000:
            break

        time.sleep(0.3)

    print(f" got {len(bars)} bars")

    if not bars:
        raise RuntimeError(f"No data returned for {symbol} {bybit_interval}")

    bars.reverse()
    df = pd.DataFrame(bars, columns=["time","open","high","low","close","volume","turnover"])
    for col in ["open","high","low","close","volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["time"] = pd.to_datetime(df["time"].astype(np.int64), unit="ms", utc=True)
    df = df.dropna().reset_index(drop=True)
    return df


# ─────────────────────────────────────────────────────────────
# OUTCOME EVALUATOR (same as backtest_v4)
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

        # Stop hit
        if is_long and bl <= stop_loss:
            partial_r = sum(
                (t["price"] - entry_price) / stop_dist * (t["partial_pct"] / 100)
                for t in targets_hit
            )
            return {
                "outcome":         "WIN" if partial_r > 0.5 else "LOSS",
                "bars_to_outcome": bar + 1,
                "r_multiple":      round(partial_r - (remaining_pct / 100), 3),
            }

        if not is_long and bh >= stop_loss:
            partial_r = sum(
                (entry_price - t["price"]) / stop_dist * (t["partial_pct"] / 100)
                for t in targets_hit
            )
            return {
                "outcome":         "WIN" if partial_r > 0.5 else "LOSS",
                "bars_to_outcome": bar + 1,
                "r_multiple":      round(partial_r - (remaining_pct / 100), 3),
            }

        # Check targets
        for tgt in exit_targets:
            if tgt["price"] in [t["price"] for t in targets_hit]:
                continue
            if (is_long and bh >= tgt["price"]) or (not is_long and bl <= tgt["price"]):
                targets_hit.append(tgt)
                remaining_pct -= tgt["partial_pct"]

        # All targets hit
        if remaining_pct <= 5:
            r = sum(
                (t["price"] - entry_price if is_long else entry_price - t["price"])
                / stop_dist * (t["partial_pct"] / 100)
                for t in targets_hit
            )
            return {"outcome": "WIN", "bars_to_outcome": bar + 1, "r_multiple": round(r, 3)}

    # Time expiry
    final = float(future_highs[-1] if is_long else future_lows[-1])
    r     = round((final - entry_price if is_long else entry_price - final) / stop_dist, 3)
    return {"outcome": "WIN" if r >= 0 else "LOSS", "bars_to_outcome": max_bars, "r_multiple": r}


# ─────────────────────────────────────────────────────────────
# TRADE LOG GENERATOR
# ─────────────────────────────────────────────────────────────

def generate_trade_log(df, strat):
    n      = len(df)
    closes = df["close"].values
    highs  = df["high"].values
    lows   = df["low"].values
    times  = df["time"].values

    WINDOW         = strat["window"]
    MAX_BARS       = strat["max_bars"]
    COOLDOWN       = strat["cooldown"]
    allowed_hours  = strat["allowed_hours"]
    allowed_grades = strat["allowed_grades"]
    mode           = strat["mode"]

    trade_log      = []
    last_trade_bar = -999
    skipped_grade  = 0
    skipped_hour   = 0
    skipped_nowme  = 0

    for i in range(WINDOW, n - MAX_BARS - 1):
        if i - last_trade_bar < COOLDOWN:
            continue

        slice_df = df.iloc[i - WINDOW: i].copy().reset_index(drop=True)

        try:
            pavp      = compute_pavp(slice_df)
            atr_data  = compute_atr(slice_df)
            vol_intel = compute_volume_intelligence(slice_df, pavp)
            swing     = vol_intel.get("swing_profiles", {})
            wme       = compute_wme_signal(slice_df, bar_tolerance=0)
            curr      = wme["current"]
        except Exception:
            continue

        has_long  = curr["long_entry"]
        has_short = curr["short_entry"]
        if not has_long and not has_short:
            skipped_nowme += 1
            continue

        direction = "LONG" if has_long else "SHORT"
        hour_now  = pd.Timestamp(times[i]).hour

        if hour_now not in allowed_hours:
            skipped_hour += 1
            continue

        in_lvn = swing.get("in_lvn", False)
        in_hvn = any(
            z["price_low"] <= float(closes[i - 1]) <= z["price_high"]
            for z in swing.get("hvn_zones", [])
        )

        wme_mod    = get_wme_confidence_modifier(wme, in_lvn, in_hvn, i)
        speed_abs  = abs(curr["speed"])
        confidence = round(min(100.0, max(0.0,
                        55.0 + min(speed_abs * 15, 20) + wme_mod["modifier"])), 1)
        grade      = "A" if confidence >= 75 else "B" if confidence >= 60 else "C"

        if grade == "C":
            skipped_grade += 1
            continue
        if allowed_grades is not None and grade not in allowed_grades:
            skipped_grade += 1
            continue

        entry_price  = float(closes[i])
        atr_val      = atr_data.get("atr_value", entry_price * 0.002)
        stop_loss    = round(
            entry_price - atr_val * 3.0 if direction == "LONG"
            else entry_price + atr_val * 3.0, 6
        )
        exit_targets = vol_intel.get(
            "exit_targets_long" if direction == "LONG" else "exit_targets_short", []
        )

        result = evaluate_outcome(
            direction, entry_price, stop_loss, exit_targets,
            highs[i + 1: i + 1 + MAX_BARS],
            lows[i + 1: i + 1 + MAX_BARS],
            MAX_BARS,
        )

        last_trade_bar = i
        trade_log.append({
            "bar_index":       i,
            "timestamp":       str(pd.Timestamp(times[i])),
            "hour_of_day":     hour_now,
            "direction":       direction,
            "grade":           grade,
            "confidence":      confidence,
            "in_lvn":          in_lvn,
            "r_multiple":      result["r_multiple"],
            "outcome":         result["outcome"],
            "bars_to_outcome": result["bars_to_outcome"],
            "mode":            mode,
        })

    return trade_log, {"skipped_nowme": skipped_nowme,
                       "skipped_hour":  skipped_hour,
                       "skipped_grade": skipped_grade}


# ─────────────────────────────────────────────────────────────
# MM SIMULATOR
# ─────────────────────────────────────────────────────────────

def simulate_mm(trade_log, mm_cfg, starting_capital=52.50):
    capital       = starting_capital
    peak          = starting_capital
    max_dd        = 0.0
    current_risk  = mm_cfg.get("base_risk", 0.02)
    equity_curve  = [round(capital, 2)]
    trade_results = []

    for t in trade_log:
        risk_dollars = capital * current_risk
        pnl          = risk_dollars * t["r_multiple"]
        capital     += pnl
        capital      = max(capital, 0.01)

        if capital > peak:
            peak = capital

        dd = (peak - capital) / peak * 100
        if dd > max_dd:
            max_dd = dd

        # MM7: scale up on wins, back on losses
        if mm_cfg.get("scale_wins"):
            if t["outcome"] == "WIN":
                current_risk = min(mm_cfg.get("win_cap", 0.03),
                                   current_risk + mm_cfg.get("win_step", 0.0025))
            else:
                current_risk = max(mm_cfg.get("base_risk", 0.02),
                                   current_risk - mm_cfg.get("win_step", 0.0025))

        equity_curve.append(round(capital, 2))
        trade_results.append({
            **t,
            "capital_after": round(capital, 2),
            "risk_used":     round(current_risk, 4),
            "dd_pct":        round(dd, 2),
            "pnl":           round(pnl, 4),
        })

    trades_taken = len(trade_results)
    wins         = sum(1 for t in trade_results if t["outcome"] == "WIN")
    avg_r        = round(sum(t["r_multiple"] for t in trade_results) / max(trades_taken, 1), 3)

    # Weekly projection based on trade frequency
    # Approximate: 10000 15m bars = ~100 days, 3000 1H bars = ~125 days
    days_covered = 100 if trades_taken > 20 else 60
    trades_per_week = round(trades_taken / (days_covered / 7), 1)
    weekly_return_pct = round((capital - starting_capital) / starting_capital * 100
                               / (days_covered / 7), 2)
    weekly_dollar = round((capital - starting_capital) / (days_covered / 7), 2)

    return {
        "final":              round(capital, 2),
        "peak":               round(peak, 2),
        "max_drawdown_pct":   round(max_dd, 1),
        "total_return_pct":   round((capital - starting_capital) / starting_capital * 100, 1),
        "trades_taken":       trades_taken,
        "wins":               wins,
        "losses":             trades_taken - wins,
        "win_rate":           round(wins / trades_taken * 100, 1) if trades_taken else 0.0,
        "avg_r":              avg_r,
        "trades_per_week":    trades_per_week,
        "weekly_return_pct":  weekly_return_pct,
        "weekly_dollar":      weekly_dollar,
        "equity_curve":       equity_curve,
        "trade_results":      trade_results,
    }


# ─────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────

def main():
    print(f"\n{'='*70}")
    print(f"  BTC BACKTEST — 15m vs 1H COMPARISON")
    print(f"  Starting capital: ${STARTING_CAPITAL:.2f} (your live balance)")
    print(f"  Strategies: BTC_15m_G, BTC_15m_L, BTC_1H_G, BTC_1H_L")
    print(f"  MM configs: 2% fixed, 3% fixed, after-win scale")
    print(f"{'='*70}\n")

    raw_logs = {}
    t0 = time.time()

    # Step 1: Generate raw trade logs
    for strat in STRATEGIES:
        key = strat["key"]
        print(f"  [{key}] Fetching data...")
        try:
            df = fetch_bybit(strat["symbol"], strat["bybit_interval"], strat["total_bars"])
        except Exception as e:
            print(f"  [{key}] FAILED: {e}")
            raw_logs[key] = []
            continue

        print(f"  [{key}] Running signal scanner...")
        tlog, skips = generate_trade_log(df, strat)
        raw_logs[key] = tlog

        wins   = sum(1 for t in tlog if t["outcome"] == "WIN")
        n      = len(tlog)
        avg_r  = round(sum(t["r_multiple"] for t in tlog) / max(n, 1), 3)
        longs  = sum(1 for t in tlog if t["direction"] == "LONG")
        shorts = sum(1 for t in tlog if t["direction"] == "SHORT")

        print(f"  [{key}] {n} trades | WR={(wins/n*100 if n else 0):.1f}% | "
              f"AvgR={avg_r} | L={longs} S={shorts}")
        print(f"         Skipped: no-WME={skips['skipped_nowme']} "
              f"off-hours={skips['skipped_hour']} "
              f"grade-C={skips['skipped_grade']}")

    print(f"\n  Data fetch + scan: {time.time()-t0:.1f}s")

    # Step 2: Apply MM configs
    all_results = {}
    for strat in STRATEGIES:
        key  = strat["key"]
        tlog = raw_logs.get(key, [])
        if not tlog:
            continue

        strat_results = {}
        for mm in MM_CONFIGS:
            result = simulate_mm(tlog, mm, starting_capital=STARTING_CAPITAL)
            strat_results[mm["name"]] = result
        all_results[key] = strat_results

    # Step 3: Print comparison table
    print(f"\n{'='*90}")
    print(f"  RESULTS — Starting capital: ${STARTING_CAPITAL:.2f}")
    print(f"{'='*90}")
    print(f"  {'Strategy':<14} {'MM Config':<24} {'Trades':>6} {'WR%':>5} "
          f"{'AvgR':>6} {'Final$':>8} {'Return%':>8} {'MaxDD%':>7} "
          f"{'$/wk':>7} {'Tr/wk':>6}")
    print(f"  {'-'*88}")

    for strat in STRATEGIES:
        key = strat["key"]
        if key not in all_results:
            continue
        for mm in MM_CONFIGS:
            r = all_results[key].get(mm["name"], {})
            print(f"  {key:<14} {mm['name']:<24} "
                  f"{r.get('trades_taken',0):>6} "
                  f"{r.get('win_rate',0):>4.1f}% "
                  f"{r.get('avg_r',0):>6.3f} "
                  f"${r.get('final',STARTING_CAPITAL):>7.2f} "
                  f"{r.get('total_return_pct',0):>7.1f}% "
                  f"{r.get('max_drawdown_pct',0):>6.1f}% "
                  f"${r.get('weekly_dollar',0):>6.2f} "
                  f"{r.get('trades_per_week',0):>5.1f}")
        print(f"  {'-'*88}")

    # Step 4: Best per strategy summary
    print(f"\n{'='*70}")
    print(f"  BEST MM CONFIG PER STRATEGY (by total return)")
    print(f"{'='*70}")
    for strat in STRATEGIES:
        key = strat["key"]
        if key not in all_results:
            continue
        best_mm   = max(all_results[key], key=lambda m: all_results[key][m]["total_return_pct"])
        best      = all_results[key][best_mm]
        print(f"  {key:<14} → {best_mm:<24} "
              f"WR={best['win_rate']:.1f}% "
              f"Return={best['total_return_pct']:.1f}% "
              f"${best['weekly_dollar']:.2f}/wk "
              f"MaxDD={best['max_drawdown_pct']:.1f}%")

    # Step 5: 15m vs 1H comparison
    print(f"\n{'='*70}")
    print(f"  15m vs 1H HEAD-TO-HEAD (MM2 — 2% fixed risk)")
    print(f"{'='*70}")
    mm_name = "MM2_FIXED_2PCT"
    pairs   = [("BTC_15m_G", "BTC_1H_G"), ("BTC_15m_L", "BTC_1H_L")]
    for tf15, tf1h in pairs:
        r15 = all_results.get(tf15, {}).get(mm_name, {})
        r1h = all_results.get(tf1h, {}).get(mm_name, {})
        print(f"\n  {tf15} vs {tf1h}:")
        print(f"    {'Metric':<20} {'15m':>10} {'1H':>10}")
        print(f"    {'-'*42}")
        for label, k15, k1h in [
            ("Trades",       r15.get('trades_taken',0),     r1h.get('trades_taken',0)),
            ("Win Rate",     f"{r15.get('win_rate',0):.1f}%", f"{r1h.get('win_rate',0):.1f}%"),
            ("Avg R",        f"{r15.get('avg_r',0):.3f}",   f"{r1h.get('avg_r',0):.3f}"),
            ("Total Return", f"{r15.get('total_return_pct',0):.1f}%", f"{r1h.get('total_return_pct',0):.1f}%"),
            ("Max Drawdown", f"{r15.get('max_drawdown_pct',0):.1f}%", f"{r1h.get('max_drawdown_pct',0):.1f}%"),
            ("$/week",       f"${r15.get('weekly_dollar',0):.2f}",  f"${r1h.get('weekly_dollar',0):.2f}"),
            ("Trades/week",  f"{r15.get('trades_per_week',0):.1f}", f"{r1h.get('trades_per_week',0):.1f}"),
        ]:
            print(f"    {label:<20} {str(k15):>10} {str(k1h):>10}")

    # Step 6: Save results
    out_file = "backtest_btc_15m_1h_results.json"
    save_data = {}
    for key, strat_res in all_results.items():
        save_data[key] = {}
        for mm_name_k, res in strat_res.items():
            save_data[key][mm_name_k] = {k: v for k, v in res.items()
                                          if k not in ("equity_curve", "trade_results")}

    with open(out_file, "w") as f:
        json.dump(save_data, f, indent=2)

    print(f"\n  Results saved to {out_file}")
    print(f"  Total runtime: {time.time()-t0:.1f}s")
    print(f"{'='*70}\n")


if __name__ == "__main__":
    main()
