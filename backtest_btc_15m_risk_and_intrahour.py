"""
backtest_btc_15m_risk_and_intrahour.py
═══════════════════════════════════════════════════════════════
Two tests in one run:

TEST A — RISK LEVELS
  Same signal logic, different risk %:
  2%, 3%, 5%, 10%
  On: BTC_15m_G and BTC_15m_L

TEST B — INTRA-HOUR TRADING
  Instead of one trade per session hour (cooldown=20 bars),
  allow ALL valid signals within the session hour.
  One trade open at a time — new signal only after previous closes.
  Compare: ONE_PER_HOUR vs ALL_IN_HOUR

Starting capital: $52.50 (live balance)

Run in SSH window:
  cd /root/trading && python3 backtest_btc_15m_risk_and_intrahour.py
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

STARTING_CAPITAL = 52.50

# ─────────────────────────────────────────────────────────────
# STRATEGY DEFINITIONS (15m only)
# ─────────────────────────────────────────────────────────────

STRATEGIES = [
    {
        "key":            "BTC_15m_G",
        "symbol":         "BTCUSDT",
        "bybit_interval": "15",
        "total_bars":     10000,
        "window":         120,
        "max_bars":       60,       # 60 bars = 15 hours to resolve
        "allowed_hours":  [7, 12, 14, 18],
        "allowed_grades": None,     # A and B
        "mode":           "G",
    },
    {
        "key":            "BTC_15m_L",
        "symbol":         "BTCUSDT",
        "bybit_interval": "15",
        "total_bars":     10000,
        "window":         120,
        "max_bars":       60,
        "allowed_hours":  [7, 12, 14],
        "allowed_grades": ["B"],
        "mode":           "L",
    },
]

# ─────────────────────────────────────────────────────────────
# RISK LEVELS TO TEST
# ─────────────────────────────────────────────────────────────

RISK_LEVELS = [0.02, 0.03, 0.05, 0.10]

# ─────────────────────────────────────────────────────────────
# DATA FETCHER
# ─────────────────────────────────────────────────────────────

def fetch_bybit(symbol, bybit_interval, total_bars):
    url    = "https://api.bybit.com/v5/market/kline"
    bars   = []
    end_ms = None

    print(f"    Fetching {total_bars} bars...", end="", flush=True)

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
            print(f"\n    [ERROR] {e}")
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

    bars.reverse()
    df = pd.DataFrame(bars, columns=["time","open","high","low","close","volume","turnover"])
    for col in ["open","high","low","close","volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["time"] = pd.to_datetime(df["time"].astype(np.int64), unit="ms", utc=True)
    df = df.dropna().reset_index(drop=True)
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

        for tgt in exit_targets:
            if tgt["price"] in [t["price"] for t in targets_hit]:
                continue
            if (is_long and bh >= tgt["price"]) or (not is_long and bl <= tgt["price"]):
                targets_hit.append(tgt)
                remaining_pct -= tgt["partial_pct"]

        if remaining_pct <= 5:
            r = sum(
                (t["price"] - entry_price if is_long else entry_price - t["price"])
                / stop_dist * (t["partial_pct"] / 100)
                for t in targets_hit
            )
            return {"outcome": "WIN", "bars_to_outcome": bar + 1, "r_multiple": round(r, 3)}

    final = float(future_highs[-1] if is_long else future_lows[-1])
    r     = round((final - entry_price if is_long else entry_price - final) / stop_dist, 3)
    return {"outcome": "WIN" if r >= 0 else "LOSS", "bars_to_outcome": max_bars, "r_multiple": r}


# ─────────────────────────────────────────────────────────────
# TRADE LOG — MODE A: ONE PER HOUR (original, cooldown=20)
# ─────────────────────────────────────────────────────────────

def generate_log_one_per_hour(df, strat):
    """
    Original logic: cooldown of 20 bars between trades.
    Effectively one trade per session hour at most.
    """
    n      = len(df)
    closes = df["close"].values
    highs  = df["high"].values
    lows   = df["low"].values
    times  = df["time"].values

    WINDOW         = strat["window"]
    MAX_BARS       = strat["max_bars"]
    COOLDOWN       = 20
    allowed_hours  = strat["allowed_hours"]
    allowed_grades = strat["allowed_grades"]

    trade_log      = []
    last_trade_bar = -999

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
            continue

        direction = "LONG" if has_long else "SHORT"
        hour_now  = pd.Timestamp(times[i]).hour

        if hour_now not in allowed_hours:
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
            continue
        if allowed_grades is not None and grade not in allowed_grades:
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
            "r_multiple":      result["r_multiple"],
            "outcome":         result["outcome"],
            "bars_to_outcome": result["bars_to_outcome"],
        })

    return trade_log


# ─────────────────────────────────────────────────────────────
# TRADE LOG — MODE B: ALL SIGNALS WITHIN SESSION HOUR
# ─────────────────────────────────────────────────────────────

def generate_log_all_in_hour(df, strat):
    """
    Intra-hour logic:
    - Any valid signal within a session hour is taken
    - Only one trade open at a time
    - New trade only after previous one resolves
    - No cooldown — just wait for previous trade to finish
    - Still requires grade A or B, WME confirmation
    - On 15m bars: up to 4 signals per hour (xx:00, xx:15, xx:30, xx:45)
    """
    n      = len(df)
    closes = df["close"].values
    highs  = df["high"].values
    lows   = df["low"].values
    times  = df["time"].values

    WINDOW         = strat["window"]
    MAX_BARS       = strat["max_bars"]
    allowed_hours  = strat["allowed_hours"]
    allowed_grades = strat["allowed_grades"]

    trade_log         = []
    next_available_bar = 0   # bar index after which we can trade again

    for i in range(WINDOW, n - MAX_BARS - 1):

        # Wait until previous trade has resolved
        if i < next_available_bar:
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
            continue

        direction = "LONG" if has_long else "SHORT"
        ts        = pd.Timestamp(times[i])
        hour_now  = ts.hour
        minute_now = ts.minute

        # Must be within session hour
        if hour_now not in allowed_hours:
            continue

        # Must be within the hour (not the last bar — no time to trade)
        # Allow signals at :00, :15, :30, :45 — all 4 slots in the hour
        if minute_now > 45:
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
            continue
        if allowed_grades is not None and grade not in allowed_grades:
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

        # Next trade only after this one resolves
        next_available_bar = i + result["bars_to_outcome"] + 1

        trade_log.append({
            "bar_index":       i,
            "timestamp":       str(pd.Timestamp(times[i])),
            "hour_of_day":     hour_now,
            "minute":          minute_now,
            "direction":       direction,
            "grade":           grade,
            "confidence":      confidence,
            "r_multiple":      result["r_multiple"],
            "outcome":         result["outcome"],
            "bars_to_outcome": result["bars_to_outcome"],
        })

    return trade_log


# ─────────────────────────────────────────────────────────────
# MM SIMULATOR
# ─────────────────────────────────────────────────────────────

def simulate(trade_log, risk_pct, starting_capital=52.50):
    capital      = starting_capital
    peak         = starting_capital
    max_dd       = 0.0
    equity_curve = [round(capital, 2)]

    for t in trade_log:
        pnl      = capital * risk_pct * t["r_multiple"]
        capital += pnl
        capital  = max(capital, 0.01)

        if capital > peak:
            peak = capital

        dd = (peak - capital) / peak * 100
        if dd > max_dd:
            max_dd = dd

        equity_curve.append(round(capital, 2))

    trades  = len(trade_log)
    wins    = sum(1 for t in trade_log if t["outcome"] == "WIN")
    avg_r   = round(sum(t["r_multiple"] for t in trade_log) / max(trades, 1), 3)

    # ~100 days of 15m data
    days          = 100
    trades_per_wk = round(trades / (days / 7), 1)
    weekly_dollar = round((capital - starting_capital) / (days / 7), 2)

    return {
        "final":            round(capital, 2),
        "total_return_pct": round((capital - starting_capital) / starting_capital * 100, 1),
        "max_drawdown_pct": round(max_dd, 1),
        "trades":           trades,
        "wins":             wins,
        "losses":           trades - wins,
        "win_rate":         round(wins / trades * 100, 1) if trades else 0.0,
        "avg_r":            avg_r,
        "trades_per_week":  trades_per_wk,
        "weekly_dollar":    weekly_dollar,
    }


# ─────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────

def main():
    print(f"\n{'='*70}")
    print(f"  BTC 15m BACKTEST — RISK LEVELS + INTRA-HOUR TRADING")
    print(f"  Starting capital: ${STARTING_CAPITAL:.2f}")
    print(f"{'='*70}\n")

    t0 = time.time()

    # Fetch data once per symbol (shared between strategies)
    print("  [1/2] Fetching BTC 15m data...")
    df = fetch_bybit("BTCUSDT", "15", 10000)

    # ─────────────────────────────────────────────────────────
    # TEST A: RISK LEVELS
    # ─────────────────────────────────────────────────────────
    print(f"\n{'='*70}")
    print(f"  TEST A — RISK LEVELS (2% / 3% / 5% / 10%)")
    print(f"  Signal mode: ONE PER HOUR (original strategy)")
    print(f"{'='*70}")

    print(f"\n  {'Strategy':<14} {'Risk':>5} {'Trades':>6} {'WR%':>5} "
          f"{'AvgR':>6} {'Return%':>8} {'MaxDD%':>7} {'$/wk':>7} {'Tr/wk':>6}")
    print(f"  {'-'*70}")

    test_a_results = {}
    for strat in STRATEGIES:
        key = strat["key"]
        print(f"\n  Generating signals for {key}...")
        tlog = generate_log_one_per_hour(df, strat)
        wins = sum(1 for t in tlog if t["outcome"] == "WIN")
        print(f"  {key}: {len(tlog)} trades, WR={wins/len(tlog)*100:.1f}% "
              f"AvgR={sum(t['r_multiple'] for t in tlog)/max(len(tlog),1):.3f}")

        test_a_results[key] = {}
        for risk in RISK_LEVELS:
            r = simulate(tlog, risk, STARTING_CAPITAL)
            test_a_results[key][f"{int(risk*100)}pct"] = r
            ruin = " ⚠ RUIN RISK" if r["max_drawdown_pct"] > 30 else ""
            print(f"  {key:<14} {risk*100:>4.0f}% "
                  f"{r['trades']:>6} "
                  f"{r['win_rate']:>4.1f}% "
                  f"{r['avg_r']:>6.3f} "
                  f"{r['total_return_pct']:>7.1f}% "
                  f"{r['max_drawdown_pct']:>6.1f}% "
                  f"${r['weekly_dollar']:>6.2f} "
                  f"{r['trades_per_week']:>5.1f}"
                  f"{ruin}")

    # ─────────────────────────────────────────────────────────
    # TEST B: INTRA-HOUR TRADING
    # ─────────────────────────────────────────────────────────
    print(f"\n\n{'='*70}")
    print(f"  TEST B — INTRA-HOUR TRADING")
    print(f"  All valid signals within session hour taken")
    print(f"  One trade open at a time — next after previous resolves")
    print(f"{'='*70}")

    test_b_results = {}
    for strat in STRATEGIES:
        key = strat["key"]
        print(f"\n  [{key}] Generating intra-hour signals...")
        tlog_ih = generate_log_all_in_hour(df, strat)
        tlog_op = generate_log_one_per_hour(df, strat)

        wins_ih = sum(1 for t in tlog_ih if t["outcome"] == "WIN")
        wins_op = sum(1 for t in tlog_op if t["outcome"] == "WIN")

        # Minute distribution
        minute_dist = {}
        for t in tlog_ih:
            m = t.get("minute", 0)
            minute_dist[m] = minute_dist.get(m, 0) + 1

        print(f"  {key} intra-hour: {len(tlog_ih)} trades | "
              f"WR={wins_ih/max(len(tlog_ih),1)*100:.1f}%")
        print(f"  {key} one/hour:   {len(tlog_op)} trades | "
              f"WR={wins_op/max(len(tlog_op),1)*100:.1f}%")
        print(f"  Signal minutes: " +
              ", ".join(f":{m:02d}={c}" for m, c in sorted(minute_dist.items())))

        test_b_results[key] = {}

        # Compare at 2% risk
        for mode, tlog in [("ONE_PER_HOUR", tlog_op), ("ALL_IN_HOUR", tlog_ih)]:
            for risk in RISK_LEVELS:
                r = simulate(tlog, risk, STARTING_CAPITAL)
                test_b_results[key][f"{mode}_{int(risk*100)}pct"] = r

        # Print head-to-head table
        print(f"\n  {key} — ONE PER HOUR vs ALL IN HOUR at each risk level:")
        print(f"  {'Mode':<14} {'Risk':>5} {'Trades':>6} {'WR%':>5} "
              f"{'Return%':>8} {'MaxDD%':>7} {'$/wk':>7}")
        print(f"  {'-'*60}")

        for risk in RISK_LEVELS:
            r_op = simulate(tlog_op, risk, STARTING_CAPITAL)
            r_ih = simulate(tlog_ih, risk, STARTING_CAPITAL)
            ruin_op = " ⚠" if r_op["max_drawdown_pct"] > 30 else ""
            ruin_ih = " ⚠" if r_ih["max_drawdown_pct"] > 30 else ""
            print(f"  {'ONE_PER_HOUR':<14} {risk*100:>4.0f}% "
                  f"{r_op['trades']:>6} {r_op['win_rate']:>4.1f}% "
                  f"{r_op['total_return_pct']:>7.1f}% "
                  f"{r_op['max_drawdown_pct']:>6.1f}% "
                  f"${r_op['weekly_dollar']:>6.2f}{ruin_op}")
            print(f"  {'ALL_IN_HOUR':<14} {risk*100:>4.0f}% "
                  f"{r_ih['trades']:>6} {r_ih['win_rate']:>4.1f}% "
                  f"{r_ih['total_return_pct']:>7.1f}% "
                  f"{r_ih['max_drawdown_pct']:>6.1f}% "
                  f"${r_ih['weekly_dollar']:>6.2f}{ruin_ih}")
            print(f"  {'-'*60}")

    # ─────────────────────────────────────────────────────────
    # SUMMARY
    # ─────────────────────────────────────────────────────────
    print(f"\n{'='*70}")
    print(f"  SUMMARY — Best config per strategy")
    print(f"  (ranked by $/week with MaxDD < 15%)")
    print(f"{'='*70}")

    candidates = []
    for strat in STRATEGIES:
        key = strat["key"]
        tlog_op = generate_log_one_per_hour(df, strat)
        tlog_ih = generate_log_all_in_hour(df, strat)
        for mode, tlog in [("ONE/HR", tlog_op), ("ALL/HR", tlog_ih)]:
            for risk in RISK_LEVELS:
                r = simulate(tlog, risk, STARTING_CAPITAL)
                if r["max_drawdown_pct"] <= 15.0 and r["weekly_dollar"] > 0:
                    candidates.append({
                        "strategy": key,
                        "mode":     mode,
                        "risk":     f"{int(risk*100)}%",
                        **r,
                    })

    candidates.sort(key=lambda x: x["weekly_dollar"], reverse=True)

    print(f"\n  {'Strategy':<14} {'Mode':<10} {'Risk':>5} {'Trades':>6} "
          f"{'WR%':>5} {'Return%':>8} {'MaxDD%':>7} {'$/wk':>7}")
    print(f"  {'-'*70}")
    for c in candidates[:10]:
        print(f"  {c['strategy']:<14} {c['mode']:<10} {c['risk']:>5} "
              f"{c['trades']:>6} {c['win_rate']:>4.1f}% "
              f"{c['total_return_pct']:>7.1f}% "
              f"{c['max_drawdown_pct']:>6.1f}% "
              f"${c['weekly_dollar']:>6.2f}")

    # Save results
    out = {
        "starting_capital": STARTING_CAPITAL,
        "test_a_risk_levels": test_a_results,
        "test_b_intrahour":   test_b_results,
        "top_candidates":     candidates[:10],
    }
    with open("backtest_risk_intrahour_results.json", "w") as f:
        json.dump(out, f, indent=2, default=str)

    print(f"\n  Saved: backtest_risk_intrahour_results.json")
    print(f"  Total runtime: {time.time()-t0:.1f}s")
    print(f"{'='*70}\n")


if __name__ == "__main__":
    main()
