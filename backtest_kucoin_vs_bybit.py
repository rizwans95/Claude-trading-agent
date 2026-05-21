"""
backtest_kucoin_vs_bybit.py
=============================================================
Compares signal results using Bybit vs KuCoin as data source.

Tests BTC_G and BTC_L strategies on both feeds and reports:
  - Win rate, avg R, trade count
  - Signal agreement rate (do both feeds fire same signals?)
  - Price difference at entry

Run:
  py backtest_kucoin_vs_bybit.py
=============================================================
"""

import sys, os, warnings, requests, time
import pandas as pd
import numpy as np
warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from indicator_engine      import compute_pavp, compute_atr
from volume_profile_engine import compute_volume_intelligence
from wme_sweep_engine      import compute_wme_signal, get_wme_confidence_modifier

# ─────────────────────────────────────────────────────────────
# DATA FETCHERS
# ─────────────────────────────────────────────────────────────

def fetch_bybit(symbol, interval, bars=5000):
    """Fetch OHLCV from Bybit linear futures — paginates to get more bars."""
    url      = "https://api.bybit.com/v5/market/kline"
    all_rows = []
    end_ms   = None

    while len(all_rows) < bars:
        params = {
            "category": "linear",
            "symbol":   symbol,
            "interval": interval,
            "limit":    1000,
        }
        if end_ms:
            params["end"] = end_ms

        r = requests.get(url, params=params, timeout=15, verify=False)
        r.raise_for_status()
        data = r.json()
        if data.get("retCode") != 0:
            raise RuntimeError(f"Bybit error: {data.get('retMsg')}")
        chunk = data.get("result", {}).get("list", [])
        if not chunk:
            break

        all_rows.extend(chunk)
        # Oldest timestamp in this batch — paginate further back
        oldest = min(int(row[0]) for row in chunk)
        end_ms  = oldest - 1

        if len(chunk) < 1000:
            break  # no more data available

        time.sleep(0.2)  # rate limit

    if not all_rows:
        raise RuntimeError(f"No Bybit data for {symbol}")

    df = pd.DataFrame(all_rows, columns=["open_time","open","high","low","close","volume","turnover"])
    df["time"] = pd.to_datetime(df["open_time"].astype(float), unit="ms")
    for c in ["open","high","low","close","volume"]:
        df[c] = df[c].astype(float)
    df = df[["time","open","high","low","close","volume"]].sort_values("time").reset_index(drop=True)
    return df.tail(bars).reset_index(drop=True)


def fetch_kucoin(symbol, interval_min, bars=5000):
    """
    Fetch OHLCV from KuCoin Futures — paginates backwards to get more bars.
    KuCoin returns max ~200 bars per call so we loop.
    """
    url      = "https://api-futures.kucoin.com/api/v1/kline/query"
    all_rows = []
    end_ms   = int(time.time() * 1000)
    chunk_ms = 200 * interval_min * 60 * 1000  # 200 bars per chunk

    while len(all_rows) < bars:
        start_ms = end_ms - chunk_ms
        params = {
            "symbol":      symbol,
            "granularity": interval_min,
            "from":        start_ms,
            "to":          end_ms,
        }
        r = requests.get(url, params=params, timeout=15, verify=False)
        r.raise_for_status()
        data = r.json()
        if data.get("code") != "200000":
            raise RuntimeError(f"KuCoin error: {data.get('msg')}")
        rows = data.get("data", [])
        if not rows:
            break

        all_rows.extend(rows)
        # Move end_ms back for next chunk
        oldest = min(int(row[0]) for row in rows)
        end_ms  = oldest - 1

        if len(rows) < 10:
            break  # no more data

        time.sleep(0.2)

    if not all_rows:
        raise RuntimeError(f"No KuCoin data for {symbol}")

    df = pd.DataFrame(all_rows, columns=["open_time","open","high","low","close","volume","turnover"])
    df["time"] = pd.to_datetime(df["open_time"].astype(float), unit="ms")
    for c in ["open","high","low","close","volume"]:
        df[c] = df[c].astype(float)
    df = df[["time","open","high","low","close","volume"]].sort_values("time").reset_index(drop=True)
    # Deduplicate and sort
    df = df.drop_duplicates(subset=["time"]).sort_values("time").reset_index(drop=True)
    return df.tail(bars).reset_index(drop=True)


# ─────────────────────────────────────────────────────────────
# OUTCOME EVALUATOR
# ─────────────────────────────────────────────────────────────

def evaluate_outcome(direction, entry, stop, exit_targets, fut_highs, fut_lows, max_bars=60):
    is_long    = direction == "LONG"
    targets_hit= []
    remaining  = 100.0
    stop_dist  = abs(entry - stop)
    if stop_dist == 0: stop_dist = entry * 0.003

    for bar in range(min(max_bars, len(fut_highs))):
        bh, bl = float(fut_highs[bar]), float(fut_lows[bar])
        if is_long and bl <= stop:
            r = sum((t["price"]-entry)/stop_dist*(t["partial_pct"]/100) for t in targets_hit)
            return "WIN" if r>0.5 else "LOSS", round(r-remaining/100, 3)
        if not is_long and bh >= stop:
            r = sum((entry-t["price"])/stop_dist*(t["partial_pct"]/100) for t in targets_hit)
            return "WIN" if r>0.5 else "LOSS", round(r-remaining/100, 3)
        for tgt in exit_targets:
            if tgt["price"] in [t["price"] for t in targets_hit]: continue
            if (is_long and bh>=tgt["price"]) or (not is_long and bl<=tgt["price"]):
                targets_hit.append(tgt); remaining -= tgt["partial_pct"]
        if remaining <= 5:
            r = sum((t["price"]-entry if is_long else entry-t["price"])
                    /stop_dist*(t["partial_pct"]/100) for t in targets_hit)
            return "WIN", round(r, 3)

    final = float(fut_highs[-1] if is_long else fut_lows[-1])
    r = round((final-entry if is_long else entry-final)/stop_dist, 3)
    return ("WIN" if r>=0 else "LOSS"), r


# ─────────────────────────────────────────────────────────────
# SIGNAL SCANNER
# ─────────────────────────────────────────────────────────────

def scan(df, strat):
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

    trades = []
    last_bar = -999

    for i in range(WINDOW, n - MAX_BARS - 1):
        if i - last_bar < COOLDOWN:
            continue
        slice_df = df.iloc[i-WINDOW:i].copy().reset_index(drop=True)
        try:
            pavp      = compute_pavp(slice_df)
            atr_data  = compute_atr(slice_df)
            vol_intel = compute_volume_intelligence(slice_df, pavp)
            wme       = compute_wme_signal(slice_df, bar_tolerance=0)
            curr      = wme["current"]
        except Exception:
            continue

        if not curr["long_entry"] and not curr["short_entry"]:
            continue

        direction = "LONG" if curr["long_entry"] else "SHORT"
        hour_now  = pd.Timestamp(times[i]).hour
        if hour_now not in allowed_hours:
            continue

        swing = vol_intel.get("swing_profiles", {})
        in_lvn = swing.get("in_lvn", False)
        in_hvn = any(z["price_low"]<=float(closes[i-1])<=z["price_high"]
                     for z in swing.get("hvn_zones",[]))
        wme_mod    = get_wme_confidence_modifier(wme, in_lvn, in_hvn, i)
        confidence = round(min(100, max(0, 55+min(abs(curr["speed"])*15,20)+wme_mod["modifier"])),1)
        grade = "A" if confidence>=75 else "B" if confidence>=60 else "C"

        if grade == "C": continue
        if allowed_grades and grade not in allowed_grades: continue

        entry_px  = float(closes[i])
        atr_val   = atr_data.get("atr_value", entry_px*0.002)
        stop_px   = round(entry_px-atr_val*3 if direction=="LONG" else entry_px+atr_val*3, 4)
        exit_tgts = vol_intel.get(
            "exit_targets_long" if direction=="LONG" else "exit_targets_short", [])

        outcome, r = evaluate_outcome(
            direction, entry_px, stop_px, exit_tgts,
            highs[i+1:i+1+MAX_BARS], lows[i+1:i+1+MAX_BARS], MAX_BARS)

        last_bar = i
        trades.append({
            "bar": i, "hour": hour_now, "direction": direction,
            "grade": grade, "confidence": confidence,
            "entry": entry_px, "stop": stop_px,
            "outcome": outcome, "r": r,
            "timestamp": str(pd.Timestamp(times[i])),
        })

    return trades


def stats(trades):
    if not trades:
        return {"count":0,"win_rate":0,"avg_r":0,"total_r":0}
    wins = sum(1 for t in trades if t["outcome"]=="WIN")
    rs   = [t["r"] for t in trades]
    return {
        "count":    len(trades),
        "wins":     wins,
        "losses":   len(trades)-wins,
        "win_rate": round(wins/len(trades)*100,1),
        "avg_r":    round(sum(rs)/len(trades),3),
        "total_r":  round(sum(rs),3),
    }


# ─────────────────────────────────────────────────────────────
# SIGNAL AGREEMENT CHECK
# ─────────────────────────────────────────────────────────────

def compare_signals(bybit_trades, kucoin_trades, tolerance_min=15):
    """Check how many signals agree between the two feeds."""
    agreements = 0
    conflicts  = 0
    bybit_only = 0
    kucoin_only= 0

    # Match by hour (within same session hour)
    bb_by_hour  = {}
    for t in bybit_trades:
        h = t["hour"]
        bb_by_hour.setdefault(h, []).append(t)

    kc_by_hour = {}
    for t in kucoin_trades:
        h = t["hour"]
        kc_by_hour.setdefault(h, []).append(t)

    all_hours = set(list(bb_by_hour.keys()) + list(kc_by_hour.keys()))
    for h in sorted(all_hours):
        bb = bb_by_hour.get(h, [])
        kc = kc_by_hour.get(h, [])
        if bb and kc:
            if bb[0]["direction"] == kc[0]["direction"]:
                agreements += 1
                # Compare entry prices
            else:
                conflicts += 1
        elif bb:
            bybit_only += 1
        elif kc:
            kucoin_only += 1

    total = agreements + conflicts + bybit_only + kucoin_only
    return {
        "agreements":   agreements,
        "conflicts":    conflicts,
        "bybit_only":   bybit_only,
        "kucoin_only":  kucoin_only,
        "agreement_rate": round(agreements/total*100,1) if total else 0,
    }


# ─────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────

STRATEGIES = [
    {
        "key":           "BTC_G",
        "bybit_symbol":  "BTCUSDT",
        "kucoin_symbol": "XBTUSDTM",
        "interval":      "15",
        "interval_min":  15,
        "bars":          5000,
        "window":        120, "max_bars": 60, "cooldown": 20,
        "allowed_hours": [7,12,14,18], "allowed_grades": None,
    },
    {
        "key":           "BTC_L",
        "bybit_symbol":  "BTCUSDT",
        "kucoin_symbol": "XBTUSDTM",
        "interval":      "15",
        "interval_min":  15,
        "bars":          5000,
        "window":        120, "max_bars": 60, "cooldown": 20,
        "allowed_hours": [7,12,14], "allowed_grades": ["B"],
    },
]

if __name__ == "__main__":
    print("\n" + "="*65)
    print("  BYBIT vs KUCOIN DATA SOURCE BACKTEST")
    print("="*65)

    results = {}

    for strat in STRATEGIES:
        key = strat["key"]
        print(f"\n  ── {key} ──────────────────────────────────")

        # Fetch Bybit data
        print(f"  Fetching Bybit {strat['bybit_symbol']} {strat['interval']}m...")
        try:
            df_bb = fetch_bybit(strat["bybit_symbol"], strat["interval"], strat.get("bars",5000))
            print(f"  Bybit: {len(df_bb)} bars | {df_bb['time'].iloc[0]} → {df_bb['time'].iloc[-1]}")
        except Exception as e:
            print(f"  Bybit fetch failed: {e}")
            df_bb = None

        # Fetch KuCoin data
        print(f"  Fetching KuCoin {strat['kucoin_symbol']} {strat['interval_min']}m...")
        try:
            df_kc = fetch_kucoin(strat["kucoin_symbol"], strat["interval_min"], strat.get("bars",5000))
            print(f"  KuCoin: {len(df_kc)} bars | {df_kc['time'].iloc[0]} → {df_kc['time'].iloc[-1]}")
        except Exception as e:
            print(f"  KuCoin fetch failed: {e}")
            df_kc = None

        if df_bb is None and df_kc is None:
            print("  Both feeds failed — skipping")
            continue

        # Scan signals
        bb_trades = scan(df_bb, strat) if df_bb is not None else []
        kc_trades = scan(df_kc, strat) if df_kc is not None else []

        bb_stats = stats(bb_trades)
        kc_stats = stats(kc_trades)
        agreement = compare_signals(bb_trades, kc_trades)

        results[key] = {
            "bybit":     bb_stats,
            "kucoin":    kc_stats,
            "agreement": agreement,
        }

        # Price difference at matching signals
        price_diffs = []
        for t_bb in bb_trades:
            # Find closest KuCoin signal by hour
            kc_same = [t for t in kc_trades if t["hour"]==t_bb["hour"]
                       and t["direction"]==t_bb["direction"]]
            if kc_same:
                diff = abs(t_bb["entry"] - kc_same[0]["entry"])
                pct  = diff / t_bb["entry"] * 100
                price_diffs.append(pct)

        avg_price_diff = round(sum(price_diffs)/len(price_diffs), 4) if price_diffs else 0

        print(f"\n  {'Metric':<25} {'Bybit':>10} {'KuCoin':>10}")
        print(f"  {'─'*47}")
        print(f"  {'Total trades':<25} {bb_stats['count']:>10} {kc_stats['count']:>10}")
        print(f"  {'Win rate':<25} {bb_stats['win_rate']:>9}% {kc_stats['win_rate']:>9}%")
        print(f"  {'Avg R':<25} {bb_stats['avg_r']:>10} {kc_stats['avg_r']:>10}")
        print(f"  {'Total R':<25} {bb_stats['total_r']:>10} {kc_stats['total_r']:>10}")
        print(f"\n  Signal Agreement:")
        print(f"    Agreement rate:   {agreement['agreement_rate']}%")
        print(f"    Agreed signals:   {agreement['agreements']}")
        print(f"    Conflicting:      {agreement['conflicts']}")
        print(f"    Bybit only:       {agreement['bybit_only']}")
        print(f"    KuCoin only:      {agreement['kucoin_only']}")
        print(f"    Avg price diff:   {avg_price_diff}%")

        # Verdict
        wr_diff = kc_stats["win_rate"] - bb_stats["win_rate"]
        if agreement["agreement_rate"] >= 85 and abs(wr_diff) <= 3:
            verdict = "SAFE TO SWITCH — feeds produce near-identical signals"
        elif agreement["agreement_rate"] >= 70:
            verdict = "MOSTLY ALIGNED — minor differences, monitor closely if switching"
        else:
            verdict = "SIGNIFICANT DIVERGENCE — switching would change strategy behaviour"

        print(f"\n  VERDICT: {verdict}")

    print(f"\n{'='*65}")
    print("  SUMMARY")
    print(f"{'='*65}")
    for key, r in results.items():
        b, k = r["bybit"], r["kucoin"]
        a = r["agreement"]
        print(f"\n  {key}:")
        print(f"    Bybit:   {b['win_rate']}% WR | {b['avg_r']}R avg | {b['count']} trades")
        print(f"    KuCoin:  {k['win_rate']}% WR | {k['avg_r']}R avg | {k['count']} trades")
        print(f"    Signal agreement: {a['agreement_rate']}%")
    print(f"\n{'='*65}\n")
