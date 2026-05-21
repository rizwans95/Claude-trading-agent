"""
bwab_diagnostics.py
=============================================================
Deep analysis of WHY BWAB underperforms.

Examines:
1. Signal frequency and clustering
2. Forward returns after signals
3. Performance split by market regime (trend vs range)
4. Which condition (RSI or KST/TRIX) is the weak link
5. Optimal RSI thresholds via parameter scan
6. Timeframe comparison (15m vs 1h vs 4h)

Run:
  py bwab_diagnostics.py
=============================================================
"""

import sys, os, warnings, requests, time
import pandas as pd
import numpy as np
warnings.filterwarnings("ignore")

# ─────────────────────────────────────────────────────────────
# DATA FETCH
# ─────────────────────────────────────────────────────────────

def fetch_bybit(symbol="BTCUSDT", interval="15", bars=5000):
    url = "https://api.bybit.com/v5/market/kline"
    all_rows = []
    end_ms = None
    while len(all_rows) < bars:
        params = {"category":"linear","symbol":symbol,"interval":interval,"limit":1000}
        if end_ms: params["end"] = end_ms
        r = requests.get(url, params=params, timeout=15, verify=False)
        r.raise_for_status()
        chunk = r.json().get("result",{}).get("list",[])
        if not chunk: break
        all_rows.extend(chunk)
        end_ms = min(int(row[0]) for row in chunk) - 1
        if len(chunk) < 1000: break
        time.sleep(0.2)
    df = pd.DataFrame(all_rows, columns=["open_time","open","high","low","close","volume","turnover"])
    df["time"] = pd.to_datetime(df["open_time"].astype(float), unit="ms")
    for c in ["open","high","low","close","volume"]: df[c] = df[c].astype(float)
    return df[["time","open","high","low","close","volume"]].sort_values("time").reset_index(drop=True).tail(bars).reset_index(drop=True)

# ─────────────────────────────────────────────────────────────
# INDICATORS
# ─────────────────────────────────────────────────────────────

def compute_rsi(series, length=5):
    delta = series.diff()
    alpha = 1.0 / length
    gain  = delta.clip(lower=0).ewm(alpha=alpha, adjust=False).mean()
    loss  = (-delta).clip(lower=0).ewm(alpha=alpha, adjust=False).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))

def compute_kst(close):
    def smaroc(s, rlen, smalen):
        return (s.diff(rlen) / s.shift(rlen) * 100).rolling(smalen).mean()
    return (smaroc(close,10,10) + 2*smaroc(close,15,3) +
            3*smaroc(close,15,2) + 4*smaroc(close,35,3))

def compute_trix(close, length=14):
    e1 = close.ewm(span=length, adjust=False).mean()
    e2 = e1.ewm(span=length, adjust=False).mean()
    e3 = e2.ewm(span=length, adjust=False).mean()
    return 10000 * np.log(e3).diff()

def compute_atr(df, length=14):
    hl = df["high"] - df["low"]
    hc = (df["high"] - df["close"].shift()).abs()
    lc = (df["low"]  - df["close"].shift()).abs()
    tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    return tr.ewm(span=length, adjust=False).mean()

# ─────────────────────────────────────────────────────────────
# FORWARD RETURN ANALYSIS
# ─────────────────────────────────────────────────────────────

def fwd_returns(df, signal_mask, horizons=[4,8,16,32], direction="long"):
    close = df["close"].values
    bars  = df.index[signal_mask].tolist()
    results = {}
    for h in horizons:
        rets = []
        for i in bars:
            if i + h >= len(df): continue
            ret = close[i+h] - close[i]
            if direction == "short": ret = -ret
            rets.append(ret)
        if rets:
            pct_pos = sum(1 for r in rets if r > 0) / len(rets) * 100
            results[h] = {"mean": np.mean(rets), "pct_pos": pct_pos, "n": len(rets)}
    return results

# ─────────────────────────────────────────────────────────────
# RSI THRESHOLD SCAN
# ─────────────────────────────────────────────────────────────

def scan_rsi_thresholds(df, rsi, kst, trix):
    """Find optimal RSI thresholds by scanning different values."""
    close = df["close"].values
    highs = df["high"].values
    lows  = df["low"].values
    n     = len(df)

    # ATR for stops
    atr = compute_atr(df).values

    best_long  = {"threshold": 35, "win_rate": 0, "trades": 0}
    best_short = {"threshold": 73, "win_rate": 0, "trades": 0}

    print("\n  Long threshold scan (RSI < X AND KST falling):")
    print(f"  {'RSI<':>6} {'Signals':>8} {'Win%':>7} {'AvgR':>7}")
    print(f"  {'─'*34}")

    for thresh in [25, 28, 30, 32, 35, 38, 40, 45]:
        long_sig = (rsi < thresh) & (kst < kst.shift(2))
        wins = 0; losses = 0
        rs = []
        last = -20
        for i in df.index[long_sig].tolist():
            if i - last < 10 or i + 16 >= n: continue
            entry = close[i]
            stop  = entry - atr[i] * 3
            sd    = entry - stop
            if sd <= 0: continue
            # Check next 32 bars
            result = None
            for b in range(1, min(33, n-i)):
                if lows[i+b] <= stop:
                    result = -1.0; break
                if highs[i+b] >= entry + sd:
                    result = 1.0; break
            if result is None:
                result = (close[min(i+32,n-1)] - entry) / sd
            rs.append(result)
            if result > 0: wins += 1
            else: losses += 1
            last = i

        total = wins + losses
        wr    = round(wins/total*100,1) if total else 0
        avgr  = round(np.mean(rs),3) if rs else 0
        marker = " ← original" if thresh == 35 else ""
        print(f"  RSI<{thresh:>3} {total:>8} {wr:>6}% {avgr:>7}{marker}")
        if total >= 5 and wr > best_long["win_rate"]:
            best_long = {"threshold": thresh, "win_rate": wr, "trades": total}

    print(f"\n  Short threshold scan (RSI > X AND TRIX falling):")
    print(f"  {'RSI>':>6} {'Signals':>8} {'Win%':>7} {'AvgR':>7}")
    print(f"  {'─'*34}")

    for thresh in [60, 65, 68, 70, 73, 75, 78, 80]:
        short_sig = (rsi > thresh) & (trix < trix.shift(1))
        wins = 0; losses = 0
        rs = []
        last = -20
        for i in df.index[short_sig].tolist():
            if i - last < 10 or i + 16 >= n: continue
            entry = close[i]
            stop  = entry + atr[i] * 3
            sd    = stop - entry
            if sd <= 0: continue
            result = None
            for b in range(1, min(33, n-i)):
                if highs[i+b] >= stop:
                    result = -1.0; break
                if lows[i+b] <= entry - sd:
                    result = 1.0; break
            if result is None:
                result = (entry - close[min(i+32,n-1)]) / sd
            rs.append(result)
            if result > 0: wins += 1
            else: losses += 1
            last = i

        total = wins + losses
        wr    = round(wins/total*100,1) if total else 0
        avgr  = round(np.mean(rs),3) if rs else 0
        marker = " ← original" if thresh == 73 else ""
        print(f"  RSI>{thresh:>3} {total:>8} {wr:>6}% {avgr:>7}{marker}")
        if total >= 5 and wr > best_short["win_rate"]:
            best_short = {"threshold": thresh, "win_rate": wr, "trades": total}

    return best_long, best_short


# ─────────────────────────────────────────────────────────────
# TIMEFRAME COMPARISON
# ─────────────────────────────────────────────────────────────

def test_timeframe(interval, label, bars=2000):
    try:
        df_tf = fetch_bybit("BTCUSDT", interval, bars)
        close = df_tf["close"]
        rsi   = compute_rsi(close)
        kst   = compute_kst(close)
        trix  = compute_trix(close)
        atr   = compute_atr(df_tf).values
        n     = len(df_tf)
        highs = df_tf["high"].values
        lows  = df_tf["low"].values
        closes= close.values

        long_sig  = (rsi < 35) & (kst < kst.shift(2))
        short_sig = (rsi > 73) & (trix < trix.shift(1))

        wins = 0; losses = 0; rs = []
        last = -10
        for i in df_tf.index[long_sig | short_sig].tolist():
            if i - last < 5 or i + 16 >= n: continue
            is_long = long_sig.iloc[i]
            entry   = closes[i]
            stop    = (entry - atr[i]*3) if is_long else (entry + atr[i]*3)
            sd      = abs(entry - stop)
            if sd <= 0: continue
            result  = None
            for b in range(1, min(33, n-i)):
                if is_long and lows[i+b] <= stop:
                    result = -1.0; break
                if not is_long and highs[i+b] >= stop:
                    result = -1.0; break
                tgt = entry + sd if is_long else entry - sd
                if is_long and highs[i+b] >= tgt:
                    result = 1.0; break
                if not is_long and lows[i+b] <= tgt:
                    result = 1.0; break
            if result is None:
                result = (closes[min(i+32,n-1)] - entry) / sd * (1 if is_long else -1)
            rs.append(result)
            if result > 0: wins += 1
            else: losses += 1
            last = i

        total = wins + losses
        wr    = round(wins/total*100,1) if total else 0
        avgr  = round(np.mean(rs),3) if rs else 0
        sigs  = long_sig.sum() + short_sig.sum()
        print(f"  {label:<8} {len(df_tf):>6} bars | {sigs:>4} signals | {total:>4} trades | {wr:>6}% WR | {avgr:>7} avg R")
    except Exception as e:
        print(f"  {label:<8} ERROR: {e}")


# ─────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("\n" + "="*65)
    print("  BWAB DIAGNOSTIC ANALYSIS")
    print("="*65)

    print("\n  Fetching 5000 bars of 15m BTCUSDT...")
    df    = fetch_bybit("BTCUSDT", "15", 5000)
    close = df["close"]
    print(f"  {len(df)} bars | {df['time'].iloc[0]} → {df['time'].iloc[-1]}")

    rsi  = compute_rsi(close)
    kst  = compute_kst(close)
    trix = compute_trix(close)
    atr  = compute_atr(df)
    ma50 = close.rolling(50).mean()

    long_sig  = (rsi < 35) & (kst < kst.shift(2))
    short_sig = (rsi > 73) & (trix < trix.shift(1))

    # ── 1. Signal Frequency ──────────────────────────────
    print(f"\n{'─'*65}")
    print("  1. SIGNAL FREQUENCY")
    print(f"{'─'*65}")
    print(f"  LONG signals:   {long_sig.sum()} ({round(long_sig.sum()/len(df)*100,1)}% of bars)")
    print(f"  SHORT signals:  {short_sig.sum()} ({round(short_sig.sum()/len(df)*100,1)}% of bars)")
    print(f"  Both same bar:  {(long_sig & short_sig).sum()}")
    print(f"  RSI mean: {rsi.mean():.1f} | RSI < 35 on {(rsi<35).sum()} bars | RSI > 73 on {(rsi>73).sum()} bars")

    # Clustering
    consec = 0; max_consec = 0
    for v in long_sig:
        consec = consec+1 if v else 0
        max_consec = max(max_consec, consec)
    print(f"  Max consecutive LONG bars: {max_consec} (means entering same losing trade {max_consec}x)")

    # ── 2. Forward Returns ───────────────────────────────
    print(f"\n{'─'*65}")
    print("  2. FORWARD RETURNS AFTER SIGNAL")
    print(f"{'─'*65}")

    l_fwd = fwd_returns(df, long_sig,  horizons=[4,8,16,32], direction="long")
    s_fwd = fwd_returns(df, short_sig, horizons=[4,8,16,32], direction="short")

    print(f"\n  After LONG signal (% of time price goes UP):")
    print(f"  {'Horizon':<12} {'% Up':>8} {'Avg $':>10} {'n':>5}")
    for h, v in l_fwd.items():
        mins = h * 15
        label = f"+{h} bars ({mins//60}h{mins%60 if mins%60 else ''})"
        print(f"  {label:<12} {v['pct_pos']:>7.1f}% {v['mean']:>+10.2f} {v['n']:>5}")

    print(f"\n  After SHORT signal (% of time price goes DOWN):")
    print(f"  {'Horizon':<12} {'% Down':>8} {'Avg $':>10} {'n':>5}")
    for h, v in s_fwd.items():
        mins = h * 15
        label = f"+{h} bars ({mins//60}h{mins%60 if mins%60 else ''})"
        print(f"  {label:<12} {v['pct_pos']:>7.1f}% {v['mean']:>+10.2f} {v['n']:>5}")

    # ── 3. Regime Split ──────────────────────────────────
    print(f"\n{'─'*65}")
    print("  3. PERFORMANCE BY MARKET REGIME")
    print(f"{'─'*65}")

    uptrend   = close > ma50
    downtrend = close < ma50

    long_up   = long_sig & uptrend
    long_down = long_sig & downtrend
    short_up  = short_sig & uptrend
    short_down= short_sig & downtrend

    l_up_fwd   = fwd_returns(df, long_up,   [8], "long")
    l_down_fwd = fwd_returns(df, long_down, [8], "long")
    s_up_fwd   = fwd_returns(df, short_up,  [8], "short")
    s_down_fwd = fwd_returns(df, short_down,[8], "short")

    print(f"\n  LONG signals split by trend (8-bar / 2h forward return):")
    if 8 in l_up_fwd:
        v = l_up_fwd[8]
        print(f"  In UPTREND:   {long_up.sum():>4} signals | {v['pct_pos']:.1f}% up | avg {v['mean']:+.2f}")
    if 8 in l_down_fwd:
        v = l_down_fwd[8]
        print(f"  In DOWNTREND: {long_down.sum():>4} signals | {v['pct_pos']:.1f}% up | avg {v['mean']:+.2f}")

    print(f"\n  SHORT signals split by trend (8-bar / 2h forward return):")
    if 8 in s_up_fwd:
        v = s_up_fwd[8]
        print(f"  In UPTREND:   {short_up.sum():>4} signals | {v['pct_pos']:.1f}% down | avg {v['mean']:+.2f}")
    if 8 in s_down_fwd:
        v = s_down_fwd[8]
        print(f"  In DOWNTREND: {short_down.sum():>4} signals | {v['pct_pos']:.1f}% down | avg {v['mean']:+.2f}")

    # ── 4. Which condition is the weak link ──────────────
    print(f"\n{'─'*65}")
    print("  4. WHICH CONDITION IS THE WEAK LINK?")
    print(f"{'─'*65}")

    rsi_only_long  = rsi < 35
    kst_only_long  = kst < kst.shift(2)
    rsi_only_short = rsi > 73
    trix_only_short= trix < trix.shift(1)

    for label, mask, direction in [
        ("RSI<35 alone",     rsi_only_long,   "long"),
        ("KST falling alone",kst_only_long,   "long"),
        ("BOTH (original)",  long_sig,         "long"),
        ("RSI>73 alone",     rsi_only_short,  "short"),
        ("TRIX falling alone",trix_only_short,"short"),
        ("BOTH (original)",  short_sig,        "short"),
    ]:
        fwd = fwd_returns(df, mask, [8], direction)
        if 8 in fwd:
            v = fwd[8]
            print(f"  {label:<25} n={v['n']:>4} | {v['pct_pos']:>5.1f}% correct | avg {v['mean']:>+8.2f}")
        if label == "BOTH (original)" and direction == "long":
            print()

    # ── 5. RSI Threshold Scan ────────────────────────────
    print(f"\n{'─'*65}")
    print("  5. OPTIMAL RSI THRESHOLD SCAN")
    print(f"{'─'*65}")
    best_long, best_short = scan_rsi_thresholds(df, rsi, kst, trix)
    print(f"\n  Best LONG threshold:  RSI < {best_long['threshold']} ({best_long['win_rate']}% WR, {best_long['trades']} trades)")
    print(f"  Best SHORT threshold: RSI > {best_short['threshold']} ({best_short['win_rate']}% WR, {best_short['trades']} trades)")

    # ── 6. Timeframe Comparison ──────────────────────────
    print(f"\n{'─'*65}")
    print("  6. TIMEFRAME COMPARISON")
    print(f"{'─'*65}")
    print(f"  {'TF':<8} {'Bars':>6} {'Sigs':>5} {'Trades':>7} {'WR':>7} {'Avg R':>8}")
    print(f"  {'─'*50}")
    for interval, label, bars in [
        ("15",  "15m",    5000),
        ("60",  "1h",     2000),
        ("240", "4h",     1000),
        ("D",   "1D",     500),
    ]:
        test_timeframe(interval, label, bars)

    # ── Summary ──────────────────────────────────────────
    print(f"\n{'─'*65}")
    print("  DIAGNOSIS SUMMARY")
    print(f"{'─'*65}")

    l8 = l_fwd.get(8, {})
    s8 = s_fwd.get(8, {})
    l8_up = l_up_fwd.get(8, {})
    l8_dn = l_down_fwd.get(8, {})

    issues = []
    if l8.get("pct_pos", 50) < 55:
        issues.append("LONG signals have no forward edge on 15m — price continues down after entry")
    if s8.get("pct_pos", 50) < 55:
        issues.append("SHORT signals have no forward edge on 15m")
    if l8_dn.get("mean", 0) < l8_up.get("mean", 0):
        issues.append("LONG signals in downtrend are catching falling knives — big underperformer")
    if long_sig.sum() > len(df) * 0.05:
        issues.append(f"Signal fires too frequently ({long_sig.sum()} longs / {len(df)} bars) — oversensitive RSI(5)")
    if max_consec > 3:
        issues.append(f"Signals cluster ({max_consec} consecutive bars) — re-entering same losing trade")

    if issues:
        for i, issue in enumerate(issues, 1):
            print(f"\n  Issue {i}: {issue}")
    else:
        print("\n  No major structural issues found — underperformance may be regime-specific")

    print(f"\n{'='*65}\n")
HEREDOC