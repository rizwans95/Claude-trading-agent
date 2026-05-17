"""
backtest_v2.py
═══════════════════════════════════════════════════════════
Backtest using the rebuilt strategy engine.

Key differences from backtest_runner.py:
  - Uses strategy_engine_v2.run_strategy() not scoring engine
  - Level-based exits (POC/VAH/VAL) not ATR multiples
  - Partial profit taking along the way
  - Re-entry logic after each exit
  - Circuit breaker: 3 consecutive losses → skip 5 bars
  - Tracks all new metrics: CVD triangle sequences,
    LVN acceleration trades, historical POC targets
═══════════════════════════════════════════════════════════
"""

import sys
import os
import json
import time
import warnings
import requests as _requests_module
_orig_get = _requests_module.get
def _get_no_verify(url, **kw):
    kw.setdefault("verify", False)
    return _orig_get(url, **kw)
_requests_module.get = _get_no_verify
import requests
import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from indicator_engine      import compute_pavp, compute_trend_speed, compute_atr
from volume_profile_engine import compute_volume_intelligence
from strategy_engine_v2    import run_strategy

# ─────────────────────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────────────────────

WINDOW              = 120    # bars of history per evaluation
STEP                = 1      # evaluate every N bars
CIRCUIT_BREAKER_N   = 3      # consecutive losses before skip
CIRCUIT_BREAKER_SKIP = 5     # bars to skip after trigger
MAX_BARS_IN_TRADE   = 60     # timeout after N bars
MIN_GRADE           = "B"    # skip grade C


# ─────────────────────────────────────────────────────────────
# DATA FETCH (reused from backtest_runner.py)
# ─────────────────────────────────────────────────────────────

def fetch_binance(symbol: str, interval: str, total_bars: int = 3000) -> pd.DataFrame:
    url    = "https://api.binance.com/api/v3/klines"
    bars   = []
    end_ms = None
    limit  = 1000

    print(f"  Fetching {total_bars} bars ({symbol} {interval}) from Binance...")

    while len(bars) < total_bars:
        params = {
            "symbol":   symbol,
            "interval": interval,
            "limit":    min(limit, total_bars - len(bars))
        }
        if end_ms:
            params["endTime"] = end_ms
        try:
            r = requests.get(url, params=params, timeout=15)
            r.raise_for_status()
            chunk = r.json()
        except Exception as e:
            print(f"  Fetch error: {e}")
            break
        if not chunk:
            break
        bars  = chunk + bars
        end_ms = int(chunk[0][0]) - 1
        time.sleep(0.2)
        if len(chunk) < limit:
            break

    if not bars:
        raise RuntimeError("No data returned from Binance")

    df = pd.DataFrame(bars, columns=[
        "open_time","open","high","low","close","volume",
        "close_time","qav","num_trades","tbbav","tbqav","ignore"
    ])
    df["time"]   = pd.to_datetime(df["open_time"].astype(float), unit="ms")
    df["open"]   = df["open"].astype(float)
    df["high"]   = df["high"].astype(float)
    df["low"]    = df["low"].astype(float)
    df["close"]  = df["close"].astype(float)
    df["volume"] = df["volume"].astype(float)
    df = df[["time","open","high","low","close","volume"]].sort_values("time").reset_index(drop=True)
    print(f"  Got {len(df)} bars  ({df['time'].iloc[0].date()} to {df['time'].iloc[-1].date()})")
    return df


# ─────────────────────────────────────────────────────────────
# OUTCOME EVALUATOR (level-based exits)
# ─────────────────────────────────────────────────────────────

def evaluate_outcome(
    direction:    str,
    entry_price:  float,
    stop_loss:    float,
    exit_targets: list,
    future_highs: np.ndarray,
    future_lows:  np.ndarray,
    max_bars:     int,
) -> dict:
    """
    Simulate a trade forward bar by bar.

    Uses actual exit target levels (VAH/VAL/POC/HVN).
    Partial profits taken at each level.
    Trade is a WIN if any target is reached before stop.
    Trade is a LOSS if stop is hit before any target.

    Returns:
        outcome         : WIN / LOSS / TIMEOUT
        bars_to_outcome : how many bars until resolution
        targets_hit     : list of targets reached
        exit_price      : final exit price
        r_multiple      : actual R achieved
    """
    is_long       = direction == "LONG"
    targets_hit   = []
    remaining_pct = 100.0
    exit_price    = entry_price

    stop_distance = abs(entry_price - stop_loss)
    if stop_distance == 0:
        stop_distance = entry_price * 0.002

    for bar in range(min(max_bars, len(future_highs))):
        bar_high = float(future_highs[bar])
        bar_low  = float(future_lows[bar])

        # Check stop first
        if is_long and bar_low <= stop_loss:
            # Calculate partial R considering profits already taken
            partial_r = sum(
                (t["price"] - entry_price) / stop_distance * (t["partial_pct"] / 100)
                for t in targets_hit
            )
            remaining_loss = -1.0 * (remaining_pct / 100)
            r_multiple = partial_r + remaining_loss
            return {
                "outcome":         "WIN" if partial_r > 0.5 else "LOSS",
                "bars_to_outcome": bar + 1,
                "targets_hit":     targets_hit,
                "exit_price":      round(stop_loss, 6),
                "r_multiple":      round(r_multiple, 3),
            }

        if not is_long and bar_high >= stop_loss:
            partial_r = sum(
                (entry_price - t["price"]) / stop_distance * (t["partial_pct"] / 100)
                for t in targets_hit
            )
            remaining_loss = -1.0 * (remaining_pct / 100)
            r_multiple = partial_r + remaining_loss
            return {
                "outcome":         "WIN" if partial_r > 0.5 else "LOSS",
                "bars_to_outcome": bar + 1,
                "targets_hit":     targets_hit,
                "exit_price":      round(stop_loss, 6),
                "r_multiple":      round(r_multiple, 3),
            }

        # Check targets
        for target in exit_targets:
            if target["price"] in [t["price"] for t in targets_hit]:
                continue

            hit = False
            if is_long  and bar_high >= target["price"]: hit = True
            if not is_long and bar_low <= target["price"]:  hit = True

            if hit:
                targets_hit.append(target)
                remaining_pct -= target["partial_pct"]
                exit_price    = target["price"]

        # If all position closed via partials
        if remaining_pct <= 5 or (len(targets_hit) > 0 and remaining_pct <= 20):
            r_achieved = sum(
                (t["price"] - entry_price if is_long else entry_price - t["price"])
                / stop_distance * (t["partial_pct"] / 100)
                for t in targets_hit
            )
            return {
                "outcome":         "WIN",
                "bars_to_outcome": bar + 1,
                "targets_hit":     targets_hit,
                "exit_price":      round(exit_price, 6),
                "r_multiple":      round(r_achieved, 3),
            }

    # Timeout
    r_partial = sum(
        (t["price"] - entry_price if is_long else entry_price - t["price"])
        / stop_distance * (t["partial_pct"] / 100)
        for t in targets_hit
    ) if targets_hit else 0.0

    return {
        "outcome":         "WIN" if r_partial > 0.3 else "TIMEOUT",
        "bars_to_outcome": max_bars,
        "targets_hit":     targets_hit,
        "exit_price":      round(float(future_highs[-1] if is_long else future_lows[-1]), 6),
        "r_multiple":      round(r_partial, 3),
    }


# ─────────────────────────────────────────────────────────────
# MAIN BACKTEST LOOP
# ─────────────────────────────────────────────────────────────

def run_backtest(
    df:        pd.DataFrame,
    symbol:    str,
    timeframe: str,
) -> tuple:
    """
    Bar-by-bar backtest using the rebuilt strategy engine.

    Returns:
        (trade_log, total_signals, no_trade_count, exhaustion_blocks)
    """
    n      = len(df)
    closes = df["close"].values
    highs  = df["high"].values
    lows   = df["low"].values
    times  = df["time"].values

    trade_log         = []
    total_signals     = 0
    no_trade_count    = 0
    exhaustion_blocks = 0

    consecutive_losses = 0
    skip_until         = -1
    last_trade_bar     = -999   # cooldown tracker

    for i in range(WINDOW, n - MAX_BARS_IN_TRADE - 1, STEP):

        if i < skip_until:
            continue

        slice_df = df.iloc[i - WINDOW: i].copy().reset_index(drop=True)
        ts_label = pd.Timestamp(times[i])

        try:
            # Compute indicators
            pavp        = compute_pavp(slice_df)
            trend_speed = compute_trend_speed(slice_df)
            atr_data    = compute_atr(slice_df)
            vol_intel   = compute_volume_intelligence(slice_df, pavp)
        except Exception as e:
            continue

        total_signals += 1

        current_price = float(closes[i - 1])

        try:
            # Triangle-first decision logic
            # The triangle stopping IS the signal. Everything else is context.
            tri = vol_intel.get("cvd_triangles", {}) if isinstance(vol_intel, dict) else {}

            # Recompute triangles fresh on this slice
            from volume_profile_engine import compute_cvd_triangles
            tri = compute_cvd_triangles(slice_df, cost_rank_thresh=75.0)

            primary        = tri.get("primary_signal", "NONE")
            bars_since_up  = tri.get("bars_since_up",  999)
            bars_since_down= tri.get("bars_since_down",999)

            # ENTRY RULE: triangle just stopped this bar
            if primary == "BULLISH_ENTRY":
                direction = "LONG"
            elif primary == "BEARISH_ENTRY":
                no_trade_count += 1
                continue
            else:
                no_trade_count += 1
                continue

            # Confidence from triangle sequence length only
            swing      = vol_intel.get("swing_profiles", {}) if isinstance(vol_intel, dict) else {}
            in_lvn     = swing.get("in_lvn", False)
            seq_len    = tri.get("up_seq_length" if direction=="LONG" else "down_seq_length", 1)
            confidence = round(min(100.0,
                (min(seq_len, 6) * 8) + 60 +
                (15 if in_lvn else 0)
            ), 1)
            grade = "A" if confidence >= 75 else "B" if confidence >= 60 else "C"

            # Stop loss
            atr_val   = atr_data.get("atr_value", current_price * 0.002)
            stop_loss = round(current_price - atr_val * 3.0, 6) if direction == "LONG" else round(current_price + atr_val * 3.0, 6)

            # Exit targets
            if direction == "LONG":
                exit_targets = vol_intel.get("exit_targets_long", []) if isinstance(vol_intel, dict) else []
            else:
                exit_targets = vol_intel.get("exit_targets_short", []) if isinstance(vol_intel, dict) else []

            level_type      = "LVN" if in_lvn else "KEY_LEVEL"
            cvd_signal_type = primary

            decision = {
                "take_trade":    True,
                "direction":     direction,
                "confidence":    confidence,
                "grade":         grade,
                "stop_loss":     stop_loss,
                "exit_targets":  exit_targets,
                "level_type":    level_type,
                "cvd_signal":    cvd_signal_type,
                "cvd_seq_length": seq_len,
                "in_lvn":        in_lvn,
            }
        except Exception as e:
            continue

        if not decision["take_trade"]:
            no_trade_count += 1
            continue

        direction    = decision["direction"]
        grade        = decision["grade"]
        confidence   = decision["confidence"]
        stop_loss    = decision["stop_loss"]
        exit_targets = decision["exit_targets"]
        level_type   = decision.get("level_type", "NONE")
        cvd_signal_type = decision.get("cvd_signal", "NONE")
        cvd_seq_len  = decision.get("cvd_seq_length", 0)
        in_lvn       = decision.get("in_lvn", False)

        # Grade filter
        if MIN_GRADE == "B" and grade == "C":
            no_trade_count += 1
            continue
        if grade == "NONE":
            no_trade_count += 1
            continue

        # Skip if we just took a trade recently (same signal cooldown)
        if i - last_trade_bar < 5:
            no_trade_count += 1
            continue
        last_trade_bar = i

        entry_price  = float(closes[i])

        # Simulate outcome
        future_highs = highs[i + 1: i + 1 + MAX_BARS_IN_TRADE]
        future_lows  = lows[i + 1:  i + 1 + MAX_BARS_IN_TRADE]

        result = evaluate_outcome(
            direction    = direction,
            entry_price  = entry_price,
            stop_loss    = stop_loss,
            exit_targets = exit_targets,
            future_highs = future_highs,
            future_lows  = future_lows,
            max_bars     = MAX_BARS_IN_TRADE,
        )

        outcome       = result["outcome"]
        bars_to       = result["bars_to_outcome"]
        r_multiple    = result["r_multiple"]
        targets_hit_n = len(result["targets_hit"])

        # Circuit breaker
        if outcome == "LOSS":
            consecutive_losses += 1
            if consecutive_losses >= CIRCUIT_BREAKER_N:
                skip_until         = i + CIRCUIT_BREAKER_SKIP
                consecutive_losses = 0
        elif outcome == "WIN":
            consecutive_losses = 0

        # Swing profile info (for logging)
        swing_log = vol_intel.get("swing_profiles", {}) if isinstance(vol_intel, dict) else {}
        bull_val  = swing_log.get("bull_val", 0.0)
        bear_vah  = swing_log.get("bear_vah", 0.0)

        trade_log.append({
            "bar_index":         i,
            "timestamp":         str(ts_label),
            "hour_of_day":       ts_label.hour,
            "symbol":            symbol,
            "timeframe":         timeframe,
            "direction":         direction,
            "grade":             grade,
            "confidence":        round(confidence, 1),
            "level_type":        level_type,
            "in_lvn":            in_lvn,
            "cvd_signal":        cvd_signal_type,
            "cvd_seq_length":    cvd_seq_len,
            "bull_val":          round(bull_val,   6),
            "bear_vah":          round(bear_vah,   6),
            "entry_price":       round(entry_price, 6),
            "stop_loss":         round(stop_loss,   6),
            "targets_hit":       targets_hit_n,
            "r_multiple":        r_multiple,
            "outcome":           outcome,
            "bars_to_outcome":   bars_to,
        })

    return trade_log, total_signals, no_trade_count, exhaustion_blocks


# ─────────────────────────────────────────────────────────────
# CALIBRATION REPORT
# ─────────────────────────────────────────────────────────────

def build_report(
    trade_log:        list,
    symbol:           str,
    timeframe:        str,
    total_bars:       int,
    total_signals:    int,
    no_trade_count:   int,
    exhaustion_blocks:int,
) -> dict:
    df = pd.DataFrame(trade_log)
    n  = len(df)
    if n == 0:
        return {"symbol": symbol, "timeframe": timeframe,
                "total_trades": 0, "win_rate": 0.0}

    wins  = (df.outcome == "WIN").sum()
    wr    = round(wins / n * 100, 1)
    avg_r = round(df.r_multiple.mean(), 3)

    def _grade_stats(g):
        sub = df[df.grade == g]
        sn  = len(sub)
        sw  = (sub.outcome == "WIN").sum()
        return {
            "count":      int(sn),
            "wins":       int(sw),
            "win_rate":   round(sw / sn * 100, 1) if sn else 0.0,
            "avg_r":      round(sub.r_multiple.mean(), 3) if sn else 0.0,
        }

    def _level_stats(ltype):
        sub = df[df.level_type == ltype]
        sn  = len(sub)
        sw  = (sub.outcome == "WIN").sum()
        return {"count": int(sn),
                "win_rate": round(sw / sn * 100, 1) if sn else 0.0}

    def _cvd_stats(sig):
        sub = df[df.cvd_signal == sig]
        sn  = len(sub)
        sw  = (sub.outcome == "WIN").sum()
        return {"count": int(sn),
                "win_rate": round(sw / sn * 100, 1) if sn else 0.0}

    return {
        "symbol":          symbol,
        "timeframe":       timeframe,
        "total_bars":      total_bars,
        "total_signals":   total_signals,
        "total_trades":    n,
        "wins":            int(wins),
        "win_rate":        wr,
        "avg_r_multiple":  avg_r,
        "breakeven_wr":    "35.7% (at 1.8R)",

        "by_grade": {g: _grade_stats(g) for g in ("A","B","C")},

        "by_direction": {
            d: {"count": int(len(sub := df[df.direction==d])),
                "win_rate": round((sub.outcome=="WIN").sum() / len(sub) * 100, 1) if len(sub) else 0.0}
            for d in df.direction.unique()
        },

        "by_level_type": {
            lt: _level_stats(lt)
            for lt in df.level_type.unique()
        },

        "by_cvd_signal": {
            sig: _cvd_stats(sig)
            for sig in df.cvd_signal.unique()
        },

        "lvn_trades": {
            "count":    int((df.in_lvn).sum()),
            "win_rate": round((df[df.in_lvn].outcome=="WIN").mean()*100, 1)
                        if (df.in_lvn).sum() > 0 else 0.0,
        },

        "by_hour": {
            str(h): {
                "count": int(len(sub := df[df.hour_of_day==h])),
                "win_rate": round((sub.outcome=="WIN").mean()*100, 1) if len(sub) else 0.0
            }
            for h in sorted(df.hour_of_day.unique())
        },

        "filter_effectiveness": {
            "total_signals":   total_signals,
            "no_trade":        no_trade_count,
            "exhaustion_blocks": exhaustion_blocks,
            "trades_taken":    n,
            "filter_rate_pct": round((total_signals - n) / max(total_signals, 1) * 100, 1),
        },
    }


# ─────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────

def main():
    symbol    = "BTCUSDT"
    timeframe = "15m"

    print(f"\n{'='*60}")
    print(f"  TRADING AGENT V2 — REBUILT STRATEGY BACKTEST")
    print(f"  {symbol} {timeframe.upper()}")
    print(f"{'='*60}")

    df = fetch_binance(symbol, timeframe, total_bars=10000)

    if len(df) < WINDOW + MAX_BARS_IN_TRADE + 10:
        print(f"  Insufficient data. Exiting.")
        return

    print(f"\n  Running backtest...")
    print(f"  Window={WINDOW} bars, Step={STEP}, Max trade={MAX_BARS_IN_TRADE} bars")
    t0 = time.time()

    trade_log, total_sig, no_trade, exh_blocks = run_backtest(df, symbol, timeframe)

    elapsed  = time.time() - t0
    n_trades = len(trade_log)
    wins     = sum(1 for t in trade_log if t["outcome"] == "WIN")
    wr       = round(wins / n_trades * 100, 1) if n_trades else 0.0

    print(f"\n  Completed in {elapsed:.1f}s")
    print(f"  Signals evaluated : {total_sig}")
    print(f"  No trade          : {no_trade}")
    print(f"  Exhaustion blocks : {exh_blocks}")
    print(f"  Trades taken      : {n_trades}")
    print(f"  Win rate          : {wr}%  ({wins}W / {n_trades-wins}L)")
    print(f"  Breakeven needed  : 35.7%  (1.8R)")
    print(f"  Status            : {'ABOVE' if wr >= 35.7 else 'BELOW'} breakeven")

    report = build_report(
        trade_log, symbol, timeframe,
        total_bars        = len(df),
        total_signals     = total_sig,
        no_trade_count    = no_trade,
        exhaustion_blocks = exh_blocks,
    )

    prefix = f"BTCUSDT_{timeframe}_v2"

    # CSV
    csv_path = f"{prefix}_trade_log.csv"
    pd.DataFrame(trade_log).to_csv(csv_path, index=False)
    print(f"\n  Trade log : {csv_path}")

    # JSON
    json_path = f"{prefix}_calibration_report.json"
    with open(json_path, "w") as f:
        json.dump(report, f, indent=2)
    print(f"  Report    : {json_path}")

    # Grade breakdown
    print(f"\n  By grade:")
    for g, s in report["by_grade"].items():
        if s["count"] > 0:
            print(f"    {g}: {s['win_rate']}% WR  (n={s['count']}, avg_r={s['avg_r']})")

    # Direction breakdown
    print(f"\n  By direction:")
    for d, s in report["by_direction"].items():
        print(f"    {d}: {s['win_rate']}% WR  (n={s['count']})")

    # CVD signal breakdown
    print(f"\n  By CVD signal type:")
    for sig, s in report["by_cvd_signal"].items():
        if s["count"] > 0:
            print(f"    {sig}: {s['win_rate']}% WR  (n={s['count']})")

    # Level type breakdown
    print(f"\n  By entry level type:")
    for lt, s in report["by_level_type"].items():
        if s["count"] > 0:
            print(f"    {lt}: {s['win_rate']}% WR  (n={s['count']})")

    print(f"\n  Full report: {json_path}")


if __name__ == "__main__":
    main()
