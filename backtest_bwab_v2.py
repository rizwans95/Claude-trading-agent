"""
backtest_bwab_v2.py
=============================================================
BWAB strategy with four fixes applied:

Fix 1: RSI(14) instead of RSI(5)
Fix 2: Daily timeframe as primary (also tests 1h/4h)
Fix 3: 14-bar cooldown after any signal
Fix 4: Trend filter — longs only above MA50, shorts only below MA50

Also compares original BWAB vs fixed BWAB side by side.

Run:
  py backtest_bwab_v2.py
=============================================================
"""

import sys, os, warnings, requests, time
import pandas as pd
import numpy as np
warnings.filterwarnings("ignore")

# ─────────────────────────────────────────────────────────────
# DATA FETCH
# ─────────────────────────────────────────────────────────────

def fetch_bybit(symbol="BTCUSDT", interval="D", bars=1000):
    url      = "https://api.bybit.com/v5/market/kline"
    all_rows = []
    end_ms   = None
    while len(all_rows) < bars:
        params = {"category":"linear","symbol":symbol,"interval":interval,"limit":200}
        if end_ms: params["end"] = end_ms
        r = requests.get(url, params=params, timeout=15, verify=False)
        r.raise_for_status()
        chunk = r.json().get("result",{}).get("list",[])
        if not chunk: break
        all_rows.extend(chunk)
        end_ms = min(int(row[0]) for row in chunk) - 1
        if len(chunk) < 10: break
        time.sleep(0.2)
    df = pd.DataFrame(all_rows,
        columns=["open_time","open","high","low","close","volume","turnover"])
    df["time"] = pd.to_datetime(df["open_time"].astype(float), unit="ms")
    for c in ["open","high","low","close","volume"]:
        df[c] = df[c].astype(float)
    df = df[["time","open","high","low","close","volume"]].sort_values("time").reset_index(drop=True)
    return df.tail(bars).reset_index(drop=True)


# ─────────────────────────────────────────────────────────────
# INDICATORS
# ─────────────────────────────────────────────────────────────

def compute_rsi(series, length=14):
    delta = series.diff()
    alpha = 1.0 / length
    gain  = delta.clip(lower=0).ewm(alpha=alpha, adjust=False).mean()
    loss  = (-delta).clip(lower=0).ewm(alpha=alpha, adjust=False).mean()
    rs    = gain / loss
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
# BACKTEST ENGINE
# ─────────────────────────────────────────────────────────────

def run_backtest(df, config, label=""):
    """
    Generic BWAB backtest with configurable settings.

    config keys:
      rsi_length:      int   (5=original, 14=fixed)
      long_rsi_thresh: float (35=original)
      short_rsi_thresh:float (73=original)
      cooldown:        int   (0=original, 14=fixed)
      trend_filter:    bool  (False=original, True=fixed)
      ma_length:       int   (50 for trend filter)
      atr_mult:        float (3.0)
    """
    rsi_len   = config.get("rsi_length", 14)
    long_thr  = config.get("long_rsi_thresh", 35)
    short_thr = config.get("short_rsi_thresh", 73)
    cooldown  = config.get("cooldown", 14)
    use_trend = config.get("trend_filter", False)
    ma_len    = config.get("ma_length", 50)
    atr_mult  = config.get("atr_mult", 3.0)

    close = df["close"]
    highs = df["high"].values
    lows  = df["low"].values
    closes= close.values
    n     = len(df)

    rsi   = compute_rsi(close, rsi_len)
    kst   = compute_kst(close)
    trix  = compute_trix(close)
    atr   = compute_atr(df).values
    ma    = close.rolling(ma_len).mean()

    # Base signals
    long_cond  = (rsi < long_thr)  & (kst < kst.shift(2))
    short_cond = (rsi > short_thr) & (trix < trix.shift(1))

    # Trend filter
    if use_trend:
        long_cond  = long_cond  & (close > ma)   # only long in uptrend
        short_cond = short_cond & (close < ma)   # only short in downtrend

    trades    = []
    last_bar  = -cooldown - 1
    in_trade  = False
    direction = None
    entry_px  = None
    stop_px   = None
    entry_bar = None

    for i in range(max(rsi_len*3, ma_len, 50), n-1):
        if pd.isna(rsi.iloc[i]) or pd.isna(kst.iloc[i]) or pd.isna(trix.iloc[i]):
            continue

        if not in_trade:
            # Cooldown check
            if i - last_bar < cooldown:
                continue

            if long_cond.iloc[i]:
                direction = "LONG"
                entry_px  = closes[i]
                stop_px   = entry_px - atr[i] * atr_mult
                entry_bar = i
                in_trade  = True
                last_bar  = i

            elif short_cond.iloc[i]:
                direction = "SHORT"
                entry_px  = closes[i]
                stop_px   = entry_px + atr[i] * atr_mult
                entry_bar = i
                in_trade  = True
                last_bar  = i

        else:
            stop_dist = abs(entry_px - stop_px)
            cur_px    = closes[i]

            # Stop hit?
            stopped = False
            if direction == "LONG"  and lows[i]  <= stop_px:
                stopped = True; cur_px = stop_px
            if direction == "SHORT" and highs[i] >= stop_px:
                stopped = True; cur_px = stop_px

            # Opposite signal exit?
            opp = (direction=="LONG"  and short_cond.iloc[i]) or \
                  (direction=="SHORT" and long_cond.iloc[i])

            # End of data
            eod = (i == n-2)

            if stopped or opp or eod:
                r = ((cur_px - entry_px) / stop_dist if direction=="LONG"
                     else (entry_px - cur_px) / stop_dist) if stop_dist > 0 else 0

                trades.append({
                    "bar":       entry_bar,
                    "direction": direction,
                    "entry":     entry_px,
                    "exit":      cur_px,
                    "stop":      stop_px,
                    "r":         round(r, 3),
                    "outcome":   "WIN" if r > 0 else "LOSS",
                    "exit_type": "STOP" if stopped else ("OPP" if opp else "EOD"),
                    "bars_held": i - entry_bar,
                    "timestamp": str(df["time"].iloc[entry_bar]),
                })
                in_trade = False

                # Re-enter opposite if signal active and not stopped
                if opp and not stopped and (i - last_bar >= cooldown):
                    direction = "SHORT" if direction == "LONG" else "LONG"
                    entry_px  = closes[i]
                    stop_px   = (entry_px - atr[i]*atr_mult if direction=="LONG"
                                 else entry_px + atr[i]*atr_mult)
                    entry_bar = i
                    in_trade  = True
                    last_bar  = i

    return trades


def calc_stats(trades, label=""):
    if not trades:
        return {"label":label,"count":0,"win_rate":0,"avg_r":0,
                "total_r":0,"wins":0,"losses":0,"best_r":0,"worst_r":0}
    wins = sum(1 for t in trades if t["outcome"]=="WIN")
    rs   = [t["r"] for t in trades]
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
    print(f"  Best/Worst:{s['best_r']} / {s['worst_r']}")


# ─────────────────────────────────────────────────────────────
# CONFIGURATIONS TO TEST
# ─────────────────────────────────────────────────────────────

CONFIGS = {
    "Original (RSI5, no filter)": {
        "rsi_length": 5, "long_rsi_thresh": 35, "short_rsi_thresh": 73,
        "cooldown": 0, "trend_filter": False, "atr_mult": 3.0,
    },
    "Fix1: RSI14 only": {
        "rsi_length": 14, "long_rsi_thresh": 35, "short_rsi_thresh": 73,
        "cooldown": 0, "trend_filter": False, "atr_mult": 3.0,
    },
    "Fix1+2: RSI14 + cooldown": {
        "rsi_length": 14, "long_rsi_thresh": 35, "short_rsi_thresh": 73,
        "cooldown": 14, "trend_filter": False, "atr_mult": 3.0,
    },
    "Fix1+2+3: RSI14 + cooldown + trend": {
        "rsi_length": 14, "long_rsi_thresh": 35, "short_rsi_thresh": 73,
        "cooldown": 14, "trend_filter": True, "atr_mult": 3.0,
    },
    "All fixes + tighter RSI (25/75)": {
        "rsi_length": 14, "long_rsi_thresh": 25, "short_rsi_thresh": 75,
        "cooldown": 14, "trend_filter": True, "atr_mult": 3.0,
    },
}

TIMEFRAMES = [
    ("15", "15m",  5000),
    ("60", "1h",   2000),
    ("240","4h",   1000),
    ("D",  "Daily",500),
]

# ─────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("\n" + "="*65)
    print("  BWAB V2 — FIXED STRATEGY BACKTEST")
    print("="*65)

    all_results = {}

    for tf_interval, tf_label, tf_bars in TIMEFRAMES:
        print(f"\n{'─'*65}")
        print(f"  TIMEFRAME: {tf_label}")
        print(f"{'─'*65}")
        print(f"  Fetching {tf_bars} bars...")

        try:
            df = fetch_bybit("BTCUSDT", tf_interval, tf_bars)
            print(f"  {len(df)} bars | {df['time'].iloc[0].date()} → {df['time'].iloc[-1].date()}")
        except Exception as e:
            print(f"  Fetch failed: {e}")
            continue

        tf_results = {}
        print(f"\n  {'Config':<38} {'Trades':>7} {'Win%':>7} {'Avg R':>7} {'Total R':>9}")
        print(f"  {'─'*72}")

        for cfg_label, cfg in CONFIGS.items():
            trades = run_backtest(df, cfg, cfg_label)
            s      = calc_stats(trades, cfg_label)
            tf_results[cfg_label] = s

            # Signal frequency
            close    = df["close"]
            rsi      = compute_rsi(close, cfg["rsi_length"])
            kst      = compute_kst(close)
            trix     = compute_trix(close)
            ma       = close.rolling(cfg.get("ma_length",50)).mean()
            lc = (rsi < cfg["long_rsi_thresh"]) & (kst < kst.shift(2))
            sc = (rsi > cfg["short_rsi_thresh"]) & (trix < trix.shift(1))
            if cfg.get("trend_filter"):
                lc = lc & (close > ma)
                sc = sc & (close < ma)
            sig_count = lc.sum() + sc.sum()

            marker = " ← original" if "Original" in cfg_label else ""
            print(f"  {cfg_label:<38} {s['count']:>7} {s['win_rate']:>6}%"
                  f" {s['avg_r']:>7} {s['total_r']:>9}{marker}")

        all_results[tf_label] = tf_results

    # ── Best config per timeframe ─────────────────────────
    print(f"\n{'='*65}")
    print("  BEST CONFIGURATION PER TIMEFRAME")
    print(f"{'='*65}")
    print(f"\n  {'TF':<8} {'Best Config':<38} {'WR':>7} {'Avg R':>7} {'Trades':>7}")
    print(f"  {'─'*72}")

    for tf_label, tf_results in all_results.items():
        if not tf_results:
            continue
        # Rank by: must have >= 10 trades, then by win_rate * avg_r
        valid = {k:v for k,v in tf_results.items() if v["count"] >= 10 and v["avg_r"] > 0}
        if not valid:
            valid = tf_results
        best_key = max(valid, key=lambda k: valid[k]["win_rate"] * max(valid[k]["avg_r"],0))
        b = valid[best_key]
        print(f"  {tf_label:<8} {best_key:<38} {b['win_rate']:>6}% {b['avg_r']:>7} {b['count']:>7}")

    # ── Daily deep dive ───────────────────────────────────
    if "Daily" in all_results:
        print(f"\n{'─'*65}")
        print("  DAILY TIMEFRAME — DETAILED RESULTS")
        print(f"{'─'*65}")
        for cfg_label, s in all_results["Daily"].items():
            print(f"\n  {cfg_label}")
            print_stats(s)

    # ── Verdict ───────────────────────────────────────────
    print(f"\n{'='*65}")
    print("  VERDICT")
    print(f"{'='*65}")

    # Find best overall result
    best_overall = {"wr": 0, "avgr": 0, "tf": "", "cfg": "", "trades": 0}
    for tf_label, tf_results in all_results.items():
        for cfg_label, s in tf_results.items():
            if s["count"] >= 10 and s["win_rate"] > best_overall["wr"] and s["avg_r"] > 0:
                best_overall = {
                    "wr": s["win_rate"], "avgr": s["avg_r"],
                    "tf": tf_label, "cfg": cfg_label, "trades": s["count"]
                }

    if best_overall["wr"] >= 65:
        verdict = "VIABLE — fixes transform BWAB into a usable strategy"
        recommendation = f"Use on {best_overall['tf']} with '{best_overall['cfg']}'"
    elif best_overall["wr"] >= 55:
        verdict = "MARGINAL — some improvement but still not strong enough for live use"
        recommendation = "Further optimisation needed before live deployment"
    else:
        verdict = "STILL UNDERPERFORMS — structural edge not strong enough on any timeframe"
        recommendation = "Do not add BWAB to live system in any configuration"

    print(f"\n  {verdict}")
    print(f"  Best result: {best_overall['wr']}% WR | {best_overall['avgr']} avg R"
          f" | {best_overall['trades']} trades | {best_overall['tf']} | {best_overall['cfg']}")
    print(f"  Recommendation: {recommendation}")
    print(f"\n{'='*65}\n")
