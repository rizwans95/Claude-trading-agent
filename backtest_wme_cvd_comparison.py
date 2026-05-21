# backtest_wme_cvd_comparison.py
# -----------------------------------------------------------
# Compares three filter configurations on BTCUSDT 15m and
# ETHUSDT 15m to isolate the contribution of WME and CVD.
#
# Config A - A_WME_ONLY:
#   Entry when WME fires (grade A or B). No CVD requirement.
#
# Config B - B_WME_AND_CVD:
#   Entry when WME fires AND CVD direction aligns.
#
# Config C - C_CVD_ONLY:
#   Entry on CVD direction alone. WME not required.
#   Direction is determined by CVD sign.
#   Lets us see how much edge comes from CVD in isolation.
#
# CVD approximated from OHLCV bars:
#   delta = volume * ((close - low) - (high - close)) / (high - low + eps)
#   CVD   = rolling sum of delta over last CVD_LOOKBACK bars
# -----------------------------------------------------------

import sys, os, json, time, warnings, requests
import numpy as np
import pandas as pd
warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from indicator_engine      import compute_pavp, compute_atr
from volume_profile_engine import compute_volume_intelligence
from wme_sweep_engine      import compute_wme_signal, get_wme_confidence_modifier

# SSL patch
import requests as _req
_orig = _req.get
def _no_verify(url, **kw):
    kw.setdefault("verify", False)
    return _orig(url, **kw)
_req.get = _no_verify

# -------------------------------------------------------
# CONSTANTS
# -------------------------------------------------------

WINDOW            = 120
MAX_BARS_IN_TRADE = 60
COOLDOWN          = 20
CVD_LOOKBACK      = 20
TOTAL_BARS        = 10000
BYBIT_INTERVAL    = "15"    # bybit_interval: minutes per bar

SYMBOLS = ["BTCUSDT", "ETHUSDT"]
HOURS   = [7, 12, 14, 18]

A_WME_ONLY    = "A_WME_ONLY"
B_WME_AND_CVD = "B_WME_AND_CVD"
C_CVD_ONLY    = "C_CVD_ONLY"

CONFIGS = [
    {"key": A_WME_ONLY,    "require_wme": True,  "require_cvd": False},
    {"key": B_WME_AND_CVD, "require_wme": True,  "require_cvd": True},
    {"key": C_CVD_ONLY,    "require_wme": False,  "require_cvd": True},
]


# -------------------------------------------------------
# DATA FETCH
# -------------------------------------------------------

def fetch_bybit(symbol, total_bars=TOTAL_BARS, bybit_interval=BYBIT_INTERVAL):
    url, bars, end_ms = "https://api.bybit.com/v5/market/kline", [], None
    print(f"  Fetching {total_bars} bars ({symbol} {bybit_interval}m) from Bybit...")
    while len(bars) < total_bars:
        params = {"category": "linear", "symbol": symbol,
                  "interval": bybit_interval,
                  "limit": min(1000, total_bars - len(bars))}
        if end_ms:
            params["end"] = end_ms
        r = requests.get(url, params=params, timeout=20)
        r.raise_for_status()
        p = r.json()
        if p.get("retCode") != 0:
            raise RuntimeError(f"Bybit error: {p}")
        chunk = p.get("result", {}).get("list", [])
        if not chunk:
            break
        bars.extend(chunk)
        end_ms = min(int(row[0]) for row in chunk) - 1
        time.sleep(0.2)
        if len(chunk) < 1000:
            break

    df = pd.DataFrame(bars, columns=[
        "open_time", "open", "high", "low", "close", "volume", "turnover"])
    df["time"] = pd.to_datetime(df["open_time"].astype(float), unit="ms")
    for c in ["open", "high", "low", "close", "volume"]:
        df[c] = df[c].astype(float)
    df = (df[["time", "open", "high", "low", "close", "volume"]]
          .drop_duplicates("time").sort_values("time")
          .tail(total_bars).reset_index(drop=True))
    print(f"  Got {len(df)} bars  ({df.time.iloc[0].date()} to {df.time.iloc[-1].date()})")
    return df


# -------------------------------------------------------
# CVD
# -------------------------------------------------------

def compute_cvd_series(df, lookback=CVD_LOOKBACK):
    high   = df["high"].values
    low    = df["low"].values
    close  = df["close"].values
    volume = df["volume"].values
    hl  = high - low
    eps = np.where(hl == 0, 1e-10, hl)
    delta = volume * ((close - low) - (high - close)) / eps

    n   = len(df)
    cvd = np.zeros(n)
    for i in range(lookback, n):
        cvd[i] = delta[i - lookback: i].sum()
    return cvd


def cvd_aligned(direction, cvd_value):
    if direction == "LONG":
        return cvd_value > 0
    return cvd_value < 0


# -------------------------------------------------------
# OUTCOME EVALUATOR
# -------------------------------------------------------

def evaluate_outcome(direction, entry_price, stop_loss, exit_targets,
                     future_highs, future_lows, max_bars):
    is_long = direction == "LONG"
    targets_hit = []
    remaining_pct = 100.0
    stop_dist = abs(entry_price - stop_loss) or entry_price * 0.003

    for bar in range(min(max_bars, len(future_highs))):
        bh = float(future_highs[bar])
        bl = float(future_lows[bar])
        if is_long and bl <= stop_loss:
            pr = sum((t["price"] - entry_price) / stop_dist * (t["partial_pct"] / 100)
                     for t in targets_hit)
            return {"outcome": "WIN" if pr > 0.5 else "LOSS", "r": round(pr - (remaining_pct / 100), 3)}
        if not is_long and bh >= stop_loss:
            pr = sum((entry_price - t["price"]) / stop_dist * (t["partial_pct"] / 100)
                     for t in targets_hit)
            return {"outcome": "WIN" if pr > 0.5 else "LOSS", "r": round(pr - (remaining_pct / 100), 3)}
        for tgt in exit_targets:
            if tgt["price"] in [t["price"] for t in targets_hit]:
                continue
            if (is_long and bh >= tgt["price"]) or (not is_long and bl <= tgt["price"]):
                targets_hit.append(tgt)
                remaining_pct -= tgt["partial_pct"]
        if remaining_pct <= 5:
            r = sum((t["price"] - entry_price if is_long else entry_price - t["price"])
                    / stop_dist * (t["partial_pct"] / 100) for t in targets_hit)
            return {"outcome": "WIN", "r": round(r, 3)}

    final = float(future_highs[-1] if is_long else future_lows[-1])
    r = round((final - entry_price if is_long else entry_price - final) / stop_dist, 3)
    return {"outcome": "WIN" if r >= 0 else "LOSS", "r": r}


# -------------------------------------------------------
# BACKTEST RUNNER
# -------------------------------------------------------

def run_backtest(df, require_wme, require_cvd):
    """
    require_wme=True,  require_cvd=False -> A_WME_ONLY
    require_wme=True,  require_cvd=True  -> B_WME_AND_CVD
    require_wme=False, require_cvd=True  -> C_CVD_ONLY
    """
    n      = len(df)
    closes = df["close"].values
    highs  = df["high"].values
    lows   = df["low"].values
    times  = df["time"].values

    cvd_series = compute_cvd_series(df, lookback=CVD_LOOKBACK)

    trades = []
    last   = -999
    cvd_filtered = 0
    wme_filtered = 0

    for i in range(WINDOW, n - MAX_BARS_IN_TRADE - 1):
        if i - last < COOLDOWN:
            continue

        hour_now = pd.Timestamp(times[i]).hour
        if hour_now not in HOURS:
            continue

        slice_df = df.iloc[i - WINDOW: i].copy().reset_index(drop=True)
        try:
            pavp     = compute_pavp(slice_df)
            atr_data = compute_atr(slice_df)
            vi       = compute_volume_intelligence(slice_df, pavp)
            swing    = vi.get("swing_profiles", {})
            wme      = compute_wme_signal(slice_df, bar_tolerance=0)
            curr     = wme["current"]
        except Exception:
            continue

        # WME path: direction from WME signal
        if require_wme:
            if not curr["long_entry"] and not curr["short_entry"]:
                continue
            direction = "LONG" if curr["long_entry"] else "SHORT"

            in_lvn = swing.get("in_lvn", False)
            hvn_z  = swing.get("hvn_zones", [])
            cp     = float(closes[i - 1])
            in_hvn = any(z["price_low"] <= cp <= z["price_high"] for z in hvn_z)

            wm    = get_wme_confidence_modifier(wme, in_lvn, in_hvn, i)
            spd   = abs(curr["speed"])
            conf  = round(min(100, max(0, 55 + min(spd * 15, 20) + wm["modifier"])), 1)
            grade = "A" if conf >= 75 else "B" if conf >= 60 else "C"

            if grade == "C":
                continue

            if require_cvd and not cvd_aligned(direction, cvd_series[i]):
                cvd_filtered += 1
                continue

        # CVD-only path: direction from CVD sign, no WME requirement
        else:
            cvd_val = cvd_series[i]
            if cvd_val == 0:
                continue
            direction = "LONG" if cvd_val > 0 else "SHORT"
            grade = "B"     # no WME confidence score available

        ep  = float(closes[i])
        atr = atr_data.get("atr_value", ep * 0.002)
        sl  = round(ep - atr * 3 if direction == "LONG" else ep + atr * 3, 6)
        et  = vi.get("exit_targets_long" if direction == "LONG" else "exit_targets_short", [])

        res = evaluate_outcome(direction, ep, sl, et,
                               highs[i + 1: i + 1 + MAX_BARS_IN_TRADE],
                               lows[i + 1: i + 1 + MAX_BARS_IN_TRADE],
                               MAX_BARS_IN_TRADE)
        last = i
        trades.append({
            "direction": direction,
            "grade":     grade,
            "outcome":   res["outcome"],
            "r":         res["r"],
            "hour":      hour_now,
            "cvd":       round(float(cvd_series[i]), 2),
        })

    n_t  = len(trades)
    wins = sum(1 for t in trades if t["outcome"] == "WIN")
    wr   = round(wins / n_t * 100, 1) if n_t else 0.0
    avgr = round(sum(t["r"] for t in trades) / max(n_t, 1), 3)

    return {
        "trades":       n_t,
        "wins":         wins,
        "win_rate":     wr,
        "avg_r":        avgr,
        "cvd_filtered": cvd_filtered,
    }


# -------------------------------------------------------
# MAIN
# -------------------------------------------------------

def main():
    print(f"\n{'='*65}")
    print(f"  WME vs WME+CVD vs CVD-ONLY COMPARISON")
    print(f"  Symbols       : {SYMBOLS}")
    print(f"  Interval      : {BYBIT_INTERVAL}m  (bybit_interval)")
    print(f"  Config A: {A_WME_ONLY}    -- WME only, no CVD")
    print(f"  Config B: {B_WME_AND_CVD} -- WME + CVD aligned")
    print(f"  Config C: {C_CVD_ONLY}    -- CVD only, no WME")
    print(f"  CVD lookback  : {CVD_LOOKBACK} bars")
    print(f"{'='*65}")

    t0 = time.time()
    all_results = {}

    for symbol in SYMBOLS:
        print(f"\n  Fetching {symbol}...")
        try:
            df = fetch_bybit(symbol, bybit_interval=BYBIT_INTERVAL)
        except Exception as e:
            print(f"  FAILED: {e}")
            continue

        sym_results = {}
        for cfg in CONFIGS:
            key         = cfg["key"]
            require_wme = cfg["require_wme"]
            require_cvd = cfg["require_cvd"]
            print(f"  Running {key} on {symbol}...")
            r = run_backtest(df, require_wme=require_wme, require_cvd=require_cvd)
            sym_results[key] = r
            extra = f"  (CVD filtered: {r['cvd_filtered']})" if require_cvd and require_wme else ""
            print(f"    {key}: {r['trades']} trades  WR={r['win_rate']}%  "
                  f"AvgR={r['avg_r']}{extra}")

        all_results[symbol] = sym_results

    # Comparison table
    print(f"\n{'='*65}")
    print(f"  COMPARISON TABLE")
    print(f"{'='*65}")
    print(f"  {'Symbol':<10} {'Config':<18} {'Trades':>7} {'WR%':>6} {'AvgR':>7}")
    print(f"  {'-'*55}")

    for symbol, sym_res in all_results.items():
        a = sym_res.get(A_WME_ONLY, {})
        b = sym_res.get(B_WME_AND_CVD, {})
        c = sym_res.get(C_CVD_ONLY, {})
        for label, res in [(A_WME_ONLY, a), (B_WME_AND_CVD, b), (C_CVD_ONLY, c)]:
            print(f"  {symbol:<10} {label:<18} {res.get('trades',0):>7} "
                  f"{res.get('win_rate',0):>5.1f}% {res.get('avg_r',0):>+7.3f}")
        ab_delta = round(b.get("win_rate", 0) - a.get("win_rate", 0), 1)
        ac_delta = round(c.get("win_rate", 0) - a.get("win_rate", 0), 1)
        print(f"  {'':<10} {'B vs A (WR delta)':<18} {'':>7} {ab_delta:>+5.1f}%")
        print(f"  {'':<10} {'C vs A (WR delta)':<18} {'':>7} {ac_delta:>+5.1f}%")
        print()

    # Verdict
    print(f"{'='*65}")
    print(f"  VERDICT")
    print(f"{'='*65}")

    for symbol, sym_res in all_results.items():
        a = sym_res.get(A_WME_ONLY, {})
        b = sym_res.get(B_WME_AND_CVD, {})
        c = sym_res.get(C_CVD_ONLY, {})
        wr_a = a.get("win_rate", 0); n_a = a.get("trades", 0)
        wr_b = b.get("win_rate", 0); n_b = b.get("trades", 0)
        wr_c = c.get("win_rate", 0); n_c = c.get("trades", 0)
        ab   = round(wr_b - wr_a, 1)
        ac   = round(wr_c - wr_a, 1)

        print(f"\n  {symbol}:")
        print(f"    {A_WME_ONLY:<20}: {wr_a:.1f}% WR  (n={n_a})")
        print(f"    {B_WME_AND_CVD:<20}: {wr_b:.1f}% WR  (n={n_b})  [{ab:+.1f}% vs A]")
        print(f"    {C_CVD_ONLY:<20}: {wr_c:.1f}% WR  (n={n_c})  [{ac:+.1f}% vs A]")

        # B verdict
        if n_b < 5:
            b_verdict = "B: INSUFFICIENT TRADES -- inconclusive"
        elif ab >= 5 and wr_b >= 65:
            b_verdict = f"B: CVD HELPS (+{ab}% WR) -- WME+CVD better than WME alone"
        elif ab >= 2:
            b_verdict = f"B: CVD MARGINAL (+{ab}% WR) -- minor gain, worth monitoring"
        elif ab >= -2:
            b_verdict = f"B: CVD NEUTRAL ({ab:+.1f}% WR) -- no meaningful edge added"
        else:
            b_verdict = f"B: CVD HURTS ({ab:+.1f}% WR) -- removing good WME trades, keep A"

        # C verdict
        if n_c < 5:
            c_verdict = "C: INSUFFICIENT TRADES -- CVD-only inconclusive"
        elif wr_c >= 65 and ac >= 0:
            c_verdict = f"C: CVD STANDALONE WORKS ({wr_c:.1f}% WR) -- has independent edge"
        elif wr_c >= 55:
            c_verdict = f"C: CVD MARGINAL STANDALONE ({wr_c:.1f}% WR) -- needs more testing"
        else:
            c_verdict = f"C: CVD ALONE INSUFFICIENT ({wr_c:.1f}% WR) -- needs WME confirmation"

        print(f"    {b_verdict}")
        print(f"    {c_verdict}")

    # Save
    output = {
        "run_date":      pd.Timestamp.utcnow().isoformat(),
        "bybit_interval": BYBIT_INTERVAL,
        "cvd_lookback":  CVD_LOOKBACK,
        "hours":         HOURS,
        "results":       all_results,
    }
    with open("wme_cvd_comparison_results.json", "w") as f:
        json.dump(output, f, indent=2)

    print(f"\n  Time: {time.time() - t0:.1f}s")
    print(f"  Saved: wme_cvd_comparison_results.json")


if __name__ == "__main__":
    main()
