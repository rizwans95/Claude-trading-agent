"""
backtest_bwab.py
=============================================================
Backtests the BWAB strategy (RSI + KST + TRIX mean reversion)
and compares it against BTC_G and BTC_L.

Also tests a COMBO mode:
  - When BTC_G/L fires → take BTC_G/L signal
  - When BTC_G/L says NO TRADE AND BWAB fires → take BWAB signal

Run:
  py backtest_bwab.py
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
# DATA FETCH
# ─────────────────────────────────────────────────────────────

def fetch_bybit(symbol="BTCUSDT", interval="15", bars=5000):
    url      = "https://api.bybit.com/v5/market/kline"
    all_rows = []
    end_ms   = None

    while len(all_rows) < bars:
        params = {"category":"linear","symbol":symbol,"interval":interval,"limit":1000}
        if end_ms:
            params["end"] = end_ms
        r = requests.get(url, params=params, timeout=15, verify=False)
        r.raise_for_status()
        data  = r.json()
        chunk = data.get("result",{}).get("list",[])
        if not chunk: break
        all_rows.extend(chunk)
        oldest = min(int(row[0]) for row in chunk)
        end_ms  = oldest - 1
        if len(chunk) < 1000: break
        time.sleep(0.2)

    df = pd.DataFrame(all_rows, columns=["open_time","open","high","low","close","volume","turnover"])
    df["time"] = pd.to_datetime(df["open_time"].astype(float), unit="ms")
    for c in ["open","high","low","close","volume"]:
        df[c] = df[c].astype(float)
    df = df[["time","open","high","low","close","volume"]].sort_values("time").reset_index(drop=True)
    return df.tail(bars).reset_index(drop=True)


# ─────────────────────────────────────────────────────────────
# BWAB INDICATORS
# ─────────────────────────────────────────────────────────────

def compute_rsi(series, length=5):
    delta = series.diff()
    up    = delta.clip(lower=0)
    down  = (-delta).clip(lower=0)
    # RMA (Wilder's smoothing) = EMA with alpha = 1/length
    alpha = 1.0 / length
    gain  = up.ewm(alpha=alpha, adjust=False).mean()
    loss  = down.ewm(alpha=alpha, adjust=False).mean()
    rs    = gain / loss
    return 100 - (100 / (1 + rs))


def compute_kst(close,
                roclen1=10, roclen2=15, roclen3=15, roclen4=35,
                smalen1=10, smalen2=3,  smalen3=2,  smalen4=3,
                siglen=4):
    def smaroc(s, rlen, smalen):
        roc = s.diff(rlen) / s.shift(rlen) * 100
        return roc.rolling(smalen).mean()

    kst = (smaroc(close, roclen1, smalen1)
         + 2 * smaroc(close, roclen2, smalen2)
         + 3 * smaroc(close, roclen3, smalen3)
         + 4 * smaroc(close, roclen4, smalen4))
    sig = kst.rolling(siglen).mean()
    return kst, sig


def compute_trix(close, length=14):
    e1  = close.ewm(span=length, adjust=False).mean()
    e2  = e1.ewm(span=length, adjust=False).mean()
    e3  = e2.ewm(span=length, adjust=False).mean()
    out = 10000 * np.log(e3).diff()
    return out


def compute_bwab_signals(df):
    """
    Compute BWAB entry signals for each bar.
    Returns DataFrame with columns: long_signal, short_signal
    """
    close = df["close"]

    rsi        = compute_rsi(close, length=5)
    kst, _     = compute_kst(close)
    trix       = compute_trix(close, length=14)

    # BWAB conditions
    longrsi    = rsi < 35
    longkst    = kst < kst.shift(2)           # kst < kst[2]
    shortrsi   = rsi > 73
    shorttrix  = trix < trix.shift(1)         # trix < trix[1]

    long_sig   = longrsi & longkst
    short_sig  = shortrsi & shorttrix

    return pd.DataFrame({
        "rsi":        rsi,
        "kst":        kst,
        "trix":       trix,
        "long_sig":   long_sig,
        "short_sig":  short_sig,
    }, index=df.index)


# ─────────────────────────────────────────────────────────────
# BWAB BACKTEST
# ─────────────────────────────────────────────────────────────

def backtest_bwab(df, atr_stop_mult=3.0, use_atr_stop=True):
    """
    Backtest BWAB strategy.
    Since BWAB has no native stop, we add an ATR-based stop
    for risk management (without it losses are unlimited).
    Also test pure signal-based exit.
    """
    sigs    = compute_bwab_signals(df)
    closes  = df["close"].values
    highs   = df["high"].values
    lows    = df["low"].values
    n       = len(df)

    # Compute ATR for stops
    df_atr  = df.copy()
    atr_vals= []
    win     = 14
    for i in range(n):
        if i < win:
            atr_vals.append(closes[i] * 0.002)
            continue
        sl    = df_atr.iloc[i-win:i]
        hl    = (sl["high"] - sl["low"]).mean()
        atr_vals.append(hl)
    atr_arr = np.array(atr_vals)

    trades     = []
    in_trade   = False
    direction  = None
    entry_px   = None
    stop_px    = None
    entry_bar  = None

    for i in range(50, n-1):  # warmup 50 bars for indicators
        if pd.isna(sigs["kst"].iloc[i]) or pd.isna(sigs["trix"].iloc[i]):
            continue

        if not in_trade:
            if sigs["long_sig"].iloc[i]:
                direction  = "LONG"
                entry_px   = closes[i]
                atr_v      = atr_arr[i]
                stop_px    = entry_px - atr_v * atr_stop_mult
                entry_bar  = i
                in_trade   = True

            elif sigs["short_sig"].iloc[i]:
                direction  = "SHORT"
                entry_px   = closes[i]
                atr_v      = atr_arr[i]
                stop_px    = entry_px + atr_v * atr_stop_mult
                entry_bar  = i
                in_trade   = True

        else:
            stop_dist = abs(entry_px - stop_px)
            cur_px    = closes[i]

            # Check stop hit
            stopped = False
            if use_atr_stop:
                if direction == "LONG"  and lows[i]  <= stop_px:
                    stopped = True; cur_px = stop_px
                if direction == "SHORT" and highs[i] >= stop_px:
                    stopped = True; cur_px = stop_px

            # Check opposite signal exit
            opp_signal = (direction == "LONG"  and sigs["short_sig"].iloc[i]) or \
                         (direction == "SHORT" and sigs["long_sig"].iloc[i])

            if stopped or opp_signal or i == n-2:
                # Close trade
                if stop_dist > 0:
                    r = (cur_px - entry_px) / stop_dist if direction == "LONG" \
                        else (entry_px - cur_px) / stop_dist
                else:
                    r = 0

                outcome = "WIN" if r > 0 else "LOSS"
                trades.append({
                    "bar":       entry_bar,
                    "direction": direction,
                    "entry":     entry_px,
                    "exit":      cur_px,
                    "stop":      stop_px,
                    "r":         round(r, 3),
                    "outcome":   outcome,
                    "exit_type": "STOP" if stopped else ("OPP_SIGNAL" if opp_signal else "EOD"),
                    "bars_held": i - entry_bar,
                })
                in_trade = False

                # Immediately enter opposite if signal active
                if opp_signal and not stopped:
                    direction  = "SHORT" if direction == "LONG" else "LONG"
                    entry_px   = closes[i]
                    atr_v      = atr_arr[i]
                    stop_px    = (entry_px - atr_v * atr_stop_mult) if direction == "LONG" \
                                 else (entry_px + atr_v * atr_stop_mult)
                    entry_bar  = i
                    in_trade   = True

    return trades


# ─────────────────────────────────────────────────────────────
# BTC_G / BTC_L SCANNER (same as before)
# ─────────────────────────────────────────────────────────────

def scan_btc_strategy(df, strat):
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

    trades   = []
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

        swing  = vol_intel.get("swing_profiles", {})
        in_lvn = swing.get("in_lvn", False)
        in_hvn = any(z["price_low"]<=float(closes[i-1])<=z["price_high"]
                     for z in swing.get("hvn_zones",[]))
        wme_mod    = get_wme_confidence_modifier(wme, in_lvn, in_hvn, i)
        confidence = round(min(100, max(0, 55+min(abs(curr["speed"])*15,20)+wme_mod["modifier"])),1)
        grade      = "A" if confidence>=75 else "B" if confidence>=60 else "C"

        if grade == "C": continue
        if allowed_grades and grade not in allowed_grades: continue

        entry_px  = float(closes[i])
        atr_val   = atr_data.get("atr_value", entry_px*0.002)
        stop_px   = round(entry_px-atr_val*3 if direction=="LONG" else entry_px+atr_val*3, 4)
        exit_tgts = vol_intel.get(
            "exit_targets_long" if direction=="LONG" else "exit_targets_short", [])

        # Evaluate outcome
        stop_dist = abs(entry_px - stop_px)
        outcome, r = "OPEN", 0.0
        for bar in range(min(MAX_BARS, n-i-1)):
            bh = float(highs[i+1+bar])
            bl = float(lows[i+1+bar])
            if direction=="LONG" and bl <= stop_px:
                r = -1.0; outcome = "LOSS"; break
            if direction=="SHORT" and bh >= stop_px:
                r = -1.0; outcome = "LOSS"; break
            # Check TPs
            for tgt in exit_tgts:
                tp = tgt["price"]
                if (direction=="LONG" and bh >= tp) or (direction=="SHORT" and bl <= tp):
                    r = round((tp-entry_px if direction=="LONG" else entry_px-tp)/stop_dist, 3)
                    outcome = "WIN"; break
            if outcome != "OPEN": break
        if outcome == "OPEN":
            final = float(closes[min(i+MAX_BARS, n-1)])
            r = round((final-entry_px if direction=="LONG" else entry_px-final)/stop_dist, 3)
            outcome = "WIN" if r >= 0 else "LOSS"

        last_bar = i
        trades.append({
            "bar": i, "direction": direction, "grade": grade,
            "confidence": confidence, "entry": entry_px,
            "stop": stop_px, "outcome": outcome, "r": r,
            "hour": hour_now, "timestamp": str(pd.Timestamp(times[i])),
        })

    return trades


# ─────────────────────────────────────────────────────────────
# COMBO STRATEGY
# ─────────────────────────────────────────────────────────────

def backtest_combo(df, btcg_trades, bwab_trades):
    """
    Combine BTC_G and BWAB:
    - Use BTC_G signals when they fire
    - Fill NO TRADE gaps with BWAB signals
    """
    # Build set of bars where BTC_G was active
    btcg_bars = set(t["bar"] for t in btcg_trades)

    # Only take BWAB signals that don't overlap with BTC_G active bars (+/- 20 bars cooldown)
    combined = list(btcg_trades)
    for t in bwab_trades:
        # Check if any BTC_G trade is within 20 bars
        overlap = any(abs(t["bar"] - b) < 20 for b in btcg_bars)
        if not overlap:
            combined.append(dict(t, source="BWAB"))

    for t in combined:
        if "source" not in t:
            t["source"] = "BTC_G"

    combined.sort(key=lambda x: x["bar"])
    return combined


# ─────────────────────────────────────────────────────────────
# STATS
# ─────────────────────────────────────────────────────────────

def stats(trades, label=""):
    if not trades:
        return {"label":label,"count":0,"win_rate":0,"avg_r":0,"total_r":0,"wins":0,"losses":0}
    wins   = sum(1 for t in trades if t["outcome"]=="WIN")
    rs     = [t["r"] for t in trades]
    return {
        "label":    label,
        "count":    len(trades),
        "wins":     wins,
        "losses":   len(trades)-wins,
        "win_rate": round(wins/len(trades)*100,1),
        "avg_r":    round(sum(rs)/len(trades),3),
        "total_r":  round(sum(rs),3),
        "best_r":   round(max(rs),3),
        "worst_r":  round(min(rs),3),
    }


def print_stats(s):
    print(f"  Trades:    {s['count']}  ({s['wins']}W / {s['losses']}L)")
    print(f"  Win rate:  {s['win_rate']}%")
    print(f"  Avg R:     {s['avg_r']}")
    print(f"  Total R:   {s['total_r']}")
    print(f"  Best R:    {s['best_r']}")
    print(f"  Worst R:   {s['worst_r']}")


# ─────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────

BTC_G_STRAT = {
    "window":120,"max_bars":60,"cooldown":20,
    "allowed_hours":[7,12,14,18],"allowed_grades":None,
}
BTC_L_STRAT = {
    "window":120,"max_bars":60,"cooldown":20,
    "allowed_hours":[7,12,14],"allowed_grades":["B"],
}

if __name__ == "__main__":
    print("\n" + "="*65)
    print("  BWAB vs BTC_G vs BTC_L — STRATEGY COMPARISON")
    print("="*65)

    print("\n  Fetching 5000 bars of BTCUSDT 15m from Bybit...")
    df = fetch_bybit("BTCUSDT", "15", 5000)
    print(f"  {len(df)} bars | {df['time'].iloc[0]} → {df['time'].iloc[-1]}")

    # ── Run BWAB ──────────────────────────────────────────
    print("\n  Running BWAB backtest...")
    bwab_trades = backtest_bwab(df, atr_stop_mult=3.0, use_atr_stop=True)
    bwab_stats  = stats(bwab_trades, "BWAB")

    # ── Run BTC_G ────────────────────────────────────────
    print("  Running BTC_G backtest...")
    btcg_trades = scan_btc_strategy(df, BTC_G_STRAT)
    btcg_stats  = stats(btcg_trades, "BTC_G")

    # ── Run BTC_L ────────────────────────────────────────
    print("  Running BTC_L backtest...")
    btcl_trades = scan_btc_strategy(df, BTC_L_STRAT)
    btcl_stats  = stats(btcl_trades, "BTC_L")

    # ── Run COMBO ────────────────────────────────────────
    print("  Running COMBO (BTC_G + BWAB gap fill)...")
    combo_trades = backtest_combo(df, btcg_trades, bwab_trades)
    combo_stats  = stats(combo_trades, "COMBO")

    # ── Print Results ────────────────────────────────────
    print("\n" + "="*65)
    print("  RESULTS")
    print("="*65)

    for s in [bwab_stats, btcg_stats, btcl_stats, combo_stats]:
        print(f"\n  ── {s['label']} ──")
        print_stats(s)

    # ── Side by side ─────────────────────────────────────
    print(f"\n  {'Strategy':<12} {'Trades':>7} {'Win%':>7} {'Avg R':>8} {'Total R':>9}")
    print(f"  {'─'*47}")
    for s in [bwab_stats, btcg_stats, btcl_stats, combo_stats]:
        print(f"  {s['label']:<12} {s['count']:>7} {s['win_rate']:>6}% {s['avg_r']:>8} {s['total_r']:>9}")

    # ── BWAB exit type breakdown ─────────────────────────
    if bwab_trades:
        stops   = sum(1 for t in bwab_trades if t.get("exit_type")=="STOP")
        sigs    = sum(1 for t in bwab_trades if t.get("exit_type")=="OPP_SIGNAL")
        avg_held= round(sum(t["bars_held"] for t in bwab_trades)/len(bwab_trades),1)
        print(f"\n  BWAB Exit breakdown:")
        print(f"    Stopped out:       {stops} ({round(stops/len(bwab_trades)*100,1)}%)")
        print(f"    Opposite signal:   {sigs}  ({round(sigs/len(bwab_trades)*100,1)}%)")
        print(f"    Avg bars held:     {avg_held} bars ({round(avg_held*15/60,1)}h)")

    # ── COMBO source breakdown ───────────────────────────
    if combo_trades:
        btcg_c  = sum(1 for t in combo_trades if t.get("source")=="BTC_G")
        bwab_c  = sum(1 for t in combo_trades if t.get("source")=="BWAB")
        bwab_w  = sum(1 for t in combo_trades if t.get("source")=="BWAB" and t["outcome"]=="WIN")
        btcg_w  = sum(1 for t in combo_trades if t.get("source")=="BTC_G" and t["outcome"]=="WIN")
        print(f"\n  COMBO breakdown:")
        print(f"    BTC_G signals: {btcg_c} trades ({round(btcg_w/btcg_c*100,1) if btcg_c else 0}% WR)")
        print(f"    BWAB fills:    {bwab_c} trades ({round(bwab_w/bwab_c*100,1) if bwab_c else 0}% WR)")

    # ── Verdict ──────────────────────────────────────────
    print(f"\n{'='*65}")
    print("  VERDICT")
    print(f"{'='*65}")

    if bwab_stats["win_rate"] >= 60 and bwab_stats["avg_r"] > 0:
        bwab_verdict = "VIABLE — worth adding alongside current strategy"
    elif bwab_stats["win_rate"] >= 50:
        bwab_verdict = "MARGINAL — needs optimisation before live use"
    else:
        bwab_verdict = "UNDERPERFORMS — not recommended for live trading"

    combo_improvement = round(combo_stats["total_r"] - btcg_stats["total_r"], 3)
    if combo_improvement > 0.5 and combo_stats["win_rate"] >= btcg_stats["win_rate"] - 5:
        combo_verdict = f"COMBO HELPS — adds +{combo_improvement}R total vs BTC_G alone"
    elif combo_improvement > 0:
        combo_verdict = f"MARGINAL IMPROVEMENT — +{combo_improvement}R, monitor closely"
    else:
        combo_verdict = f"COMBO HURTS — reduces performance by {abs(combo_improvement)}R"

    print(f"\n  BWAB standalone:  {bwab_verdict}")
    print(f"  BWAB + BTC_G:     {combo_verdict}")
    print(f"\n{'='*65}\n")
