"""
paper_trade_logger.py
═══════════════════════════════════════════════════════════
Paper trading logger for all 4 locked strategies.

Runs continuously (or on-demand). Every time a valid signal
fires during a session hour, it logs it to a CSV and JSON
file for later comparison against backtest predictions.

Tracks:
  - Entry signal details (direction, grade, confidence)
  - WME signal state at entry
  - Hypothetical entry price
  - Forward price at +1h, +4h, +24h (outcome tracking)
  - Whether the signal was in-session or out-of-session

Run modes:
  python paper_trade_logger.py --mode scan   # one-pass check
  python paper_trade_logger.py --mode watch  # continuous (every 15min)
  python paper_trade_logger.py --mode report # show logged trades
═══════════════════════════════════════════════════════════
"""

import sys, os, json, time, argparse, warnings, requests
from datetime import datetime, timezone
import numpy as np
import pandas as pd
warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from indicator_engine      import compute_pavp, compute_atr
from volume_profile_engine import compute_volume_intelligence, compute_cvd_triangles
from wme_sweep_engine      import compute_wme_signal, get_wme_confidence_modifier

# SSL patch
import requests as _req
_orig = _req.get
def _no_verify(url, **kw):
    kw.setdefault("verify", False)
    return _orig(url, **kw)
_req.get = _no_verify

LOG_FILE  = "paper_trade_log.csv"
JSON_FILE = "paper_trade_log.json"

STRATEGIES = [
    {"key":"BTC_G","symbol":"BTCUSDT","tf":"15","hours":[7,12,14,18],"grades":None,  "mode":"G"},
    {"key":"BTC_L","symbol":"BTCUSDT","tf":"15","hours":[7,12,14],   "grades":["B"], "mode":"L"},
    {"key":"ETH_G","symbol":"ETHUSDT","tf":"15","hours":[7,12,14,18],"grades":None,  "mode":"G"},
]
# SPY excluded from paper trader (no Bybit source)

WINDOW = 120


def fetch_bybit(symbol, interval="15", bars=150):
    url    = "https://api.bybit.com/v5/market/kline"
    params = {"category":"linear","symbol":symbol,"interval":interval,"limit":bars}
    r = requests.get(url, params=params, timeout=15)
    r.raise_for_status()
    p = r.json()
    if p.get("retCode") != 0: raise RuntimeError(f"Bybit: {p}")
    chunk = p["result"]["list"]
    if not chunk: raise RuntimeError("No data")
    df = pd.DataFrame(chunk, columns=[
        "open_time","open","high","low","close","volume","turnover"])
    df["time"] = pd.to_datetime(df["open_time"].astype(float), unit="ms")
    for c in ["open","high","low","close","volume"]: df[c] = df[c].astype(float)
    return df[["time","open","high","low","close","volume"]].sort_values("time").reset_index(drop=True)


def scan_signal(strat):
    """
    Fetch latest bars and evaluate signal for one strategy.
    Returns signal dict or None if no trade.
    """
    try:
        df = fetch_bybit(strat["symbol"], strat["tf"], bars=WINDOW+5)
    except Exception as e:
        return {"error": str(e), "strategy": strat["key"]}

    utc_now  = datetime.now(timezone.utc)
    utc_hour = utc_now.hour
    in_session = utc_hour in strat["hours"]

    try:
        pavp     = compute_pavp(df)
        atr_data = compute_atr(df)
        vi       = compute_volume_intelligence(df, pavp)
        swing    = vi.get("swing_profiles",{})
        wme      = compute_wme_signal(df, bar_tolerance=0)
        curr     = wme["current"]
        tri      = compute_cvd_triangles(df, cost_rank_thresh=75.0)
    except Exception as e:
        return {"error": f"Indicator error: {e}", "strategy": strat["key"]}

    has_signal = curr["long_entry"] or curr["short_entry"]
    direction  = "LONG" if curr["long_entry"] else "SHORT" if curr["short_entry"] else "NONE"

    in_lvn = swing.get("in_lvn", False)
    hvn_z  = swing.get("hvn_zones",[])
    cp     = float(df["close"].iloc[-1])
    in_hvn = any(z["price_low"]<=cp<=z["price_high"] for z in hvn_z)

    wm   = get_wme_confidence_modifier(wme, in_lvn, in_hvn, len(df)-1)
    spd  = abs(curr["speed"])
    conf = round(min(100, max(0, 55+min(spd*15,20)+wm["modifier"])),1)
    grade= "A" if conf>=75 else "B" if conf>=60 else "C"

    pavp_data = pavp or {}

    return {
        "timestamp":      utc_now.isoformat(),
        "utc_hour":       utc_hour,
        "strategy":       strat["key"],
        "symbol":         strat["symbol"],
        "timeframe":      strat["tf"] + "m",
        "mode":           strat["mode"],
        "in_session":     in_session,
        "has_signal":     has_signal,
        "direction":      direction,
        "confidence":     conf,
        "grade":          grade,
        "valid_trade":    (has_signal and in_session and grade != "C"
                           and (strat["grades"] is None or grade in strat["grades"])),
        "wme_long":       curr["long_entry"],
        "wme_short":      curr["short_entry"],
        "wme_speed":      round(float(curr["speed"]),4),
        "wme_htf_bull":   curr["htf_bull"],
        "wme_sweep_bars": int(min(curr["bars_since_sweep_low"],
                                  curr["bars_since_sweep_high"])),
        "cvd_primary":    tri.get("primary_signal","NONE"),
        "in_lvn":         in_lvn,
        "in_hvn":         in_hvn,
        "entry_price":    cp,
        "pavp_poc":       round(float(pavp_data.get("poc",0)),4),
        "pavp_vah":       round(float(pavp_data.get("vah",0)),4),
        "pavp_val":       round(float(pavp_data.get("val",0)),4),
        "bull_val":       round(float(swing.get("bull_val",0)),4),
        "bear_vah":       round(float(swing.get("bear_vah",0)),4),
        # Forward prices filled in later
        "price_1h":       None,
        "price_4h":       None,
        "price_24h":      None,
        "outcome_1h":     None,
        "outcome_4h":     None,
        "outcome_24h":    None,
        "notes":          "",
    }


def load_log():
    if os.path.exists(JSON_FILE):
        with open(JSON_FILE) as f:
            return json.load(f)
    return []


def save_log(entries):
    with open(JSON_FILE,"w") as f:
        json.dump(entries, f, indent=2, default=str)
    df = pd.DataFrame(entries)
    df.to_csv(LOG_FILE, index=False)


def update_forward_prices(entries):
    """
    For any entry without forward prices, try to fetch them now.
    """
    updated = 0
    now_ms  = datetime.now(timezone.utc).timestamp() * 1000

    for e in entries:
        if not e.get("valid_trade"):
            continue

        entry_ts = pd.Timestamp(e["timestamp"]).timestamp() * 1000
        entry_px = e["entry_price"]

        for label, hours_ahead in [("1h",1),("4h",4),("24h",24)]:
            if e.get(f"price_{label}") is not None:
                continue

            target_ms = entry_ts + hours_ahead * 3600 * 1000
            if target_ms > now_ms:
                continue  # not yet

            # Fetch the bar at that time
            try:
                sym = next(s["symbol"] for s in STRATEGIES if s["key"]==e["strategy"])
                tf  = next(s["tf"]     for s in STRATEGIES if s["key"]==e["strategy"])
                url    = "https://api.bybit.com/v5/market/kline"
                params = {"category":"linear","symbol":sym,"interval":tf,
                          "limit":1,"start":int(target_ms)-60000,"end":int(target_ms)+60000}
                r = requests.get(url, params=params, timeout=10)
                p = r.json()
                chunk = p.get("result",{}).get("list",[])
                if chunk:
                    fwd_px = float(chunk[0][4])  # close
                    e[f"price_{label}"] = round(fwd_px, 4)
                    # Outcome based on direction
                    direction = e["direction"]
                    if direction == "LONG":
                        e[f"outcome_{label}"] = "WIN" if fwd_px > entry_px else "LOSS"
                    elif direction == "SHORT":
                        e[f"outcome_{label}"] = "WIN" if fwd_px < entry_px else "LOSS"
                    updated += 1
                time.sleep(0.1)
            except Exception:
                pass

    return updated


def print_report(entries):
    valid = [e for e in entries if e.get("valid_trade")]
    print(f"\n{'='*70}")
    print(f"  PAPER TRADE REPORT")
    print(f"  Total scans: {len(entries)}  |  Valid signals: {len(valid)}")
    print(f"{'='*70}")

    if not valid:
        print("  No valid signals logged yet.")
        return

    print(f"\n  {'Time':<22} {'Strat':<8} {'Dir':<6} {'Conf':>5} "
          f"{'Gr':>3} {'Price':>10} {'1h':>6} {'4h':>6} {'24h':>6}")
    print(f"  {'-'*68}")

    for e in valid[-30:]:  # last 30
        ts   = e["timestamp"][:16].replace("T"," ")
        o1   = e.get("outcome_1h","?") or "?"
        o4   = e.get("outcome_4h","?") or "?"
        o24  = e.get("outcome_24h","?") or "?"
        col1 = "G" if o1=="WIN" else "L" if o1=="LOSS" else "?"
        col4 = "G" if o4=="WIN" else "L" if o4=="LOSS" else "?"
        col24= "G" if o24=="WIN" else "L" if o24=="LOSS" else "?"
        print(f"  {ts:<22} {e['strategy']:<8} {e['direction']:<6} "
              f"{e['confidence']:>5.1f} {e['grade']:>3} "
              f"{e['entry_price']:>10.2f} {col1:>6} {col4:>6} {col24:>6}")

    # Win rate summary
    for tf in ["1h","4h","24h"]:
        outcomes = [e.get(f"outcome_{tf}") for e in valid
                    if e.get(f"outcome_{tf}") is not None]
        if not outcomes: continue
        wins = outcomes.count("WIN")
        wr   = round(wins/len(outcomes)*100,1)
        print(f"\n  {tf} outcome: {wins}W/{len(outcomes)-wins}L = {wr}% WR  (n={len(outcomes)})")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", default="scan",
                        choices=["scan","watch","report"])
    args = parser.parse_args()

    if args.mode == "report":
        entries = load_log()
        print_report(entries)
        return

    if args.mode == "scan":
        print(f"\n{'='*60}")
        print(f"  PAPER TRADE SCANNER — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
        print(f"{'='*60}")

        entries = load_log()
        new_signals = []

        for strat in STRATEGIES:
            sig = scan_signal(strat)
            if "error" in sig:
                print(f"  {strat['key']}: ERROR — {sig['error']}")
                continue

            status = "IN SESSION" if sig["in_session"] else "out of session"
            signal = sig["direction"] if sig["has_signal"] else "no signal"
            valid  = "VALID TRADE" if sig["valid_trade"] else ""

            print(f"  {strat['key']:<8} {status:<14} {signal:<6} "
                  f"conf={sig['confidence']:>5.1f}% grade={sig['grade']} "
                  f"WME={sig['wme_long'] or sig['wme_short']} "
                  f"CVD={sig['cvd_primary']}  {valid}")

            entries.append(sig)
            if sig["valid_trade"]:
                new_signals.append(sig)

        # Update forward prices for old entries
        updated = update_forward_prices(entries)
        if updated:
            print(f"\n  Updated {updated} forward prices from previous signals.")

        save_log(entries)
        print(f"\n  Logged {len(new_signals)} new valid signal(s).")
        print(f"  Total entries: {len(entries)}")
        print(f"  Files: {LOG_FILE}, {JSON_FILE}")

        if new_signals:
            print(f"\n  NEW VALID SIGNALS:")
            for s in new_signals:
                print(f"    {s['strategy']}: {s['direction']} @ ${s['entry_price']:.2f}  "
                      f"conf={s['confidence']}%  CVD={s['cvd_primary']}")

    elif args.mode == "watch":
        print(f"\n  Watching mode — scanning every 15 minutes.")
        print(f"  Press Ctrl+C to stop.\n")
        while True:
            try:
                os.system("python paper_trade_logger.py --mode scan")
                print(f"\n  Next scan in 15 minutes...")
                time.sleep(900)
            except KeyboardInterrupt:
                print("\n  Stopped.")
                break


if __name__ == "__main__":
    main()
