"""
backtest_eth_validation.py
═══════════════════════════════════════════════════════════
ETH_G validation across 3 independent time windows.

Window 1: Most recent 3000 bars  (newest data)
Window 2: 3001-6000 bars back    (middle period)
Window 3: 6001-10000 bars back   (oldest period)

If win rate is consistent (within 15%) across all three,
ETH_G is validated and safe to trade.
If variance is >15%, ETH_G is not ready.

Also runs BTC_G on same windows as control — BTC should
be consistent since it's already validated.
═══════════════════════════════════════════════════════════
"""

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

WINDOW            = 120
MAX_BARS_IN_TRADE = 60
COOLDOWN          = 20

CONFIGS = [
    {
        "key":    "ETH_G",
        "symbol": "ETHUSDT",
        "hours":  [7, 12, 14, 18],
        "grades": None,
    },
    {
        "key":    "BTC_G",
        "symbol": "BTCUSDT",
        "hours":  [7, 12, 14, 18],
        "grades": None,
    },
]


def fetch_bybit(symbol, total_bars=10000):
    url, bars, end_ms = "https://api.bybit.com/v5/market/kline", [], None
    print(f"  Fetching {total_bars} bars ({symbol} 15m) from Bybit...")
    while len(bars) < total_bars:
        params = {"category":"linear","symbol":symbol,
                  "interval":"15","limit":min(1000,total_bars-len(bars))}
        if end_ms: params["end"] = end_ms
        r = requests.get(url, params=params, timeout=20)
        r.raise_for_status()
        p = r.json()
        if p.get("retCode") != 0: raise RuntimeError(f"Bybit: {p}")
        chunk = p.get("result",{}).get("list",[])
        if not chunk: break
        bars.extend(chunk)
        end_ms = min(int(row[0]) for row in chunk) - 1
        time.sleep(0.2)
        if len(chunk) < 1000: break
    df = pd.DataFrame(bars, columns=[
        "open_time","open","high","low","close","volume","turnover"])
    df["time"] = pd.to_datetime(df["open_time"].astype(float), unit="ms")
    for c in ["open","high","low","close","volume"]: df[c] = df[c].astype(float)
    df = (df[["time","open","high","low","close","volume"]]
          .drop_duplicates("time").sort_values("time")
          .tail(total_bars).reset_index(drop=True))
    print(f"  Got {len(df)} bars  ({df.time.iloc[0].date()} to {df.time.iloc[-1].date()})")
    return df


def evaluate_outcome(direction, entry_price, stop_loss, exit_targets,
                     future_highs, future_lows, max_bars):
    is_long = direction == "LONG"
    targets_hit = []; remaining_pct = 100.0
    stop_dist = abs(entry_price - stop_loss) or entry_price * 0.003

    for bar in range(min(max_bars, len(future_highs))):
        bh = float(future_highs[bar]); bl = float(future_lows[bar])
        if is_long and bl <= stop_loss:
            pr = sum((t["price"]-entry_price)/stop_dist*(t["partial_pct"]/100) for t in targets_hit)
            return {"outcome":"WIN" if pr>0.5 else "LOSS","r":round(pr-(remaining_pct/100),3)}
        if not is_long and bh >= stop_loss:
            pr = sum((entry_price-t["price"])/stop_dist*(t["partial_pct"]/100) for t in targets_hit)
            return {"outcome":"WIN" if pr>0.5 else "LOSS","r":round(pr-(remaining_pct/100),3)}
        for tgt in exit_targets:
            if tgt["price"] in [t["price"] for t in targets_hit]: continue
            if (is_long and bh>=tgt["price"]) or (not is_long and bl<=tgt["price"]):
                targets_hit.append(tgt); remaining_pct -= tgt["partial_pct"]
        if remaining_pct <= 5:
            r = sum((t["price"]-entry_price if is_long else entry_price-t["price"])
                    /stop_dist*(t["partial_pct"]/100) for t in targets_hit)
            return {"outcome":"WIN","r":round(r,3)}
    final = float(future_highs[-1] if is_long else future_lows[-1])
    r = round((final-entry_price if is_long else entry_price-final)/stop_dist,3)
    return {"outcome":"WIN" if r>=0 else "LOSS","r":r}


def run_window(df, cfg, label):
    n = len(df); closes=df["close"].values; highs=df["high"].values
    lows=df["low"].values; times=df["time"].values
    trades=[]; last=-999

    for i in range(WINDOW, n-MAX_BARS_IN_TRADE-1):
        if i - last < COOLDOWN: continue
        slice_df = df.iloc[i-WINDOW:i].copy().reset_index(drop=True)
        try:
            pavp     = compute_pavp(slice_df)
            atr_data = compute_atr(slice_df)
            vi       = compute_volume_intelligence(slice_df, pavp)
            swing    = vi.get("swing_profiles",{})
            wme      = compute_wme_signal(slice_df, bar_tolerance=0)
            curr     = wme["current"]
        except Exception: continue

        if not curr["long_entry"] and not curr["short_entry"]: continue
        direction = "LONG" if curr["long_entry"] else "SHORT"
        hour_now  = pd.Timestamp(times[i]).hour
        if hour_now not in cfg["hours"]: continue

        in_lvn = swing.get("in_lvn", False)
        hvn_z  = swing.get("hvn_zones",[])
        cp     = float(closes[i-1])
        in_hvn = any(z["price_low"]<=cp<=z["price_high"] for z in hvn_z)

        wm  = get_wme_confidence_modifier(wme, in_lvn, in_hvn, i)
        spd = abs(curr["speed"])
        conf= round(min(100, max(0, 55+min(spd*15,20)+wm["modifier"])),1)
        grade="A" if conf>=75 else "B" if conf>=60 else "C"

        if grade=="C": continue
        if cfg["grades"] and grade not in cfg["grades"]: continue

        ep   = float(closes[i])
        atr  = atr_data.get("atr_value", ep*0.002)
        sl   = round(ep-atr*3 if direction=="LONG" else ep+atr*3, 6)
        et   = vi.get("exit_targets_long" if direction=="LONG" else "exit_targets_short",[])

        res = evaluate_outcome(direction, ep, sl, et,
                               highs[i+1:i+1+MAX_BARS_IN_TRADE],
                               lows[i+1:i+1+MAX_BARS_IN_TRADE], MAX_BARS_IN_TRADE)
        last = i
        trades.append({"direction":direction,"grade":grade,"outcome":res["outcome"],
                        "r":res["r"],"hour":hour_now})

    n_t  = len(trades)
    wins = sum(1 for t in trades if t["outcome"]=="WIN")
    wr   = round(wins/n_t*100,1) if n_t else 0.0
    avgr = round(sum(t["r"] for t in trades)/max(n_t,1),3)
    return {"label":label,"trades":n_t,"wins":wins,"win_rate":wr,"avg_r":avgr}


def main():
    print(f"\n{'='*65}")
    print(f"  ETH_G VALIDATION — 3 INDEPENDENT TIME WINDOWS")
    print(f"  BTC_G as control (should be consistent)")
    print(f"{'='*65}")

    all_results = {}
    t0 = time.time()

    for cfg in CONFIGS:
        sym = cfg["symbol"]
        print(f"\n  Fetching {sym}...")
        try:
            df = fetch_bybit(sym, total_bars=10000)
        except Exception as e:
            print(f"  FAILED: {e}"); continue

        n = len(df)
        windows = [
            ("Window 1 (newest)",  df.iloc[max(0,n-3000):].reset_index(drop=True)),
            ("Window 2 (middle)",  df.iloc[max(0,n-6000):max(0,n-3000)].reset_index(drop=True)),
            ("Window 3 (oldest)",  df.iloc[max(0,n-10000):max(0,n-6000)].reset_index(drop=True)),
        ]

        results = []
        for label, win_df in windows:
            if len(win_df) < WINDOW + MAX_BARS_IN_TRADE + 10:
                print(f"  {label}: insufficient bars ({len(win_df)})")
                continue
            print(f"  Running {cfg['key']} on {label} ({len(win_df)} bars)...")
            r = run_window(win_df, cfg, label)
            results.append(r)
            print(f"    {label}: {r['trades']} trades  WR={r['win_rate']}%  AvgR={r['avg_r']}")

        all_results[cfg["key"]] = results

    # Print verdict
    print(f"\n{'='*65}")
    print(f"  VALIDATION VERDICT")
    print(f"{'='*65}")

    for key, results in all_results.items():
        if not results: continue
        wrs   = [r["win_rate"] for r in results if r["trades"] >= 5]
        if not wrs:
            print(f"\n  {key}: INSUFFICIENT TRADES across windows")
            continue

        wr_range = max(wrs) - min(wrs)
        avg_wr   = round(sum(wrs)/len(wrs), 1)

        print(f"\n  {key}:")
        for r in results:
            bar = "#" * int(r["win_rate"]/5)
            print(f"    {r['label']:<22}: {r['win_rate']:>5.1f}% WR  "
                  f"(n={r['trades']})  AvgR={r['avg_r']:>+.3f}  {bar}")

        print(f"    {'Average WR':<22}: {avg_wr}%")
        print(f"    {'WR Range':<22}: {wr_range:.1f}% "
              f"({'CONSISTENT' if wr_range<=15 else 'INCONSISTENT'})")

        if key == "ETH_G":
            if wr_range <= 15 and avg_wr >= 60:
                print(f"\n  ETH_G VERDICT: VALIDATED -- consistent across windows")
                print(f"  Safe to add to active roster.")
            elif wr_range <= 15 and avg_wr >= 35.7:
                print(f"\n  ETH_G VERDICT: MARGINAL -- consistent but low win rate")
                print(f"  Trade only with MM1 (1% risk) until more data accumulates.")
            else:
                print(f"\n  ETH_G VERDICT: NOT VALIDATED -- too much variance")
                print(f"  Keep on hold. Do not trade with real money.")

    # Save
    with open("eth_validation_results.json","w") as f:
        json.dump(all_results, f, indent=2)
    print(f"\n  Time: {time.time()-t0:.1f}s")
    print(f"  Saved: eth_validation_results.json")


if __name__ == "__main__":
    main()
