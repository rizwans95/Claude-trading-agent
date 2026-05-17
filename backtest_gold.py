"""
backtest_gold.py
═══════════════════════════════════════════════════════════
Gold (XAUUSD) portability test.

Data source: Yahoo Finance (GC=F futures, 1h bars, 2yr period)
Strategy:    G_STRICT_SESSION adapted for Gold session hours

Gold session hours (UTC):
  8:00  = London open (peak Gold volume)
  13:00 = NY open
  14:00 = NY first hour
  19:00 = London close / NY mid-session

Compares to BTC_G baseline at same hours.
═══════════════════════════════════════════════════════════
"""

import sys, os, json, time, warnings
import numpy as np
import pandas as pd
warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from indicator_engine      import compute_pavp, compute_atr
from volume_profile_engine import compute_volume_intelligence
from wme_sweep_engine      import compute_wme_signal, get_wme_confidence_modifier

WINDOW            = 80
MAX_BARS_IN_TRADE = 20
COOLDOWN          = 8
STARTING_CAPITAL  = 1000.0
RISK_PER_TRADE    = 0.02

GOLD_HOURS_G = [8, 13, 14, 19]
GOLD_HOURS_L = [8, 13, 14]


def fetch_gold():
    try:
        import yfinance as yf
    except ImportError:
        raise RuntimeError("pip install yfinance")

    print("  Fetching XAUUSD (GC=F) 1h from Yahoo Finance...")
    try:
        from curl_cffi.requests import Session
        session = Session(verify=False, impersonate="chrome110")
        ticker  = yf.Ticker("GC=F", session=session)
    except ImportError:
        ticker = yf.Ticker("GC=F")

    df = ticker.history(period="730d", interval="1h", auto_adjust=True)
    if df is None or df.empty:
        df = yf.download("GC=F", period="730d", interval="1h", auto_adjust=True)
    if df is None or df.empty:
        raise RuntimeError("No Gold data from Yahoo")

    df = df.reset_index()
    tc = "Datetime" if "Datetime" in df.columns else "Date"
    df = df.rename(columns={tc:"time","Open":"open","High":"high",
                             "Low":"low","Close":"close","Volume":"volume"})
    df["time"] = pd.to_datetime(df["time"]).dt.tz_localize(None)
    df = (df[["time","open","high","low","close","volume"]]
          .dropna().sort_values("time").reset_index(drop=True))
    print(f"  Got {len(df)} bars  ({df.time.iloc[0].date()} to {df.time.iloc[-1].date()})")
    return df


def evaluate_outcome(direction, entry_price, stop_loss, exit_targets,
                     future_highs, future_lows, max_bars):
    is_long = direction == "LONG"
    targets_hit=[]; remaining_pct=100.0
    stop_dist = abs(entry_price-stop_loss) or entry_price*0.003

    for bar in range(min(max_bars, len(future_highs))):
        bh=float(future_highs[bar]); bl=float(future_lows[bar])
        if is_long and bl<=stop_loss:
            pr=sum((t["price"]-entry_price)/stop_dist*(t["partial_pct"]/100) for t in targets_hit)
            return {"outcome":"WIN" if pr>0.5 else "LOSS","r":round(pr-(remaining_pct/100),3)}
        if not is_long and bh>=stop_loss:
            pr=sum((entry_price-t["price"])/stop_dist*(t["partial_pct"]/100) for t in targets_hit)
            return {"outcome":"WIN" if pr>0.5 else "LOSS","r":round(pr-(remaining_pct/100),3)}
        for tgt in exit_targets:
            if tgt["price"] in [t["price"] for t in targets_hit]: continue
            if (is_long and bh>=tgt["price"]) or (not is_long and bl<=tgt["price"]):
                targets_hit.append(tgt); remaining_pct-=tgt["partial_pct"]
        if remaining_pct<=5:
            r=sum((t["price"]-entry_price if is_long else entry_price-t["price"])
                  /stop_dist*(t["partial_pct"]/100) for t in targets_hit)
            return {"outcome":"WIN","r":round(r,3)}
    final=float(future_highs[-1] if is_long else future_lows[-1])
    r=round((final-entry_price if is_long else entry_price-final)/stop_dist,3)
    return {"outcome":"WIN" if r>=0 else "LOSS","r":r}


def run_config(df, config_name, allowed_hours, allowed_grades=None):
    n=len(df); closes=df["close"].values; highs=df["high"].values
    lows=df["low"].values; times=df["time"].values
    trades=[]; last=-999

    for i in range(WINDOW, n-MAX_BARS_IN_TRADE-1):
        if i-last < COOLDOWN: continue
        slice_df=df.iloc[i-WINDOW:i].copy().reset_index(drop=True)

        try:
            pavp    =compute_pavp(slice_df)
            atr_data=compute_atr(slice_df)
            vi      =compute_volume_intelligence(slice_df, pavp)
            swing   =vi.get("swing_profiles",{})
            wme     =compute_wme_signal(slice_df, bar_tolerance=0)
            curr    =wme["current"]
        except Exception: continue

        if not curr["long_entry"] and not curr["short_entry"]: continue
        direction="LONG" if curr["long_entry"] else "SHORT"

        hour_now=pd.Timestamp(times[i]).hour
        if hour_now not in allowed_hours: continue

        in_lvn=swing.get("in_lvn",False)
        hvn_z=swing.get("hvn_zones",[])
        cp=float(closes[i-1])
        in_hvn=any(z["price_low"]<=cp<=z["price_high"] for z in hvn_z)

        wm=get_wme_confidence_modifier(wme,in_lvn,in_hvn,i)
        spd=abs(curr["speed"])
        conf=round(min(100,max(0,55+min(spd*15,20)+wm["modifier"])),1)
        grade="A" if conf>=75 else "B" if conf>=60 else "C"

        if grade=="C": continue
        if allowed_grades and grade not in allowed_grades: continue

        ep=float(closes[i])
        atr=atr_data.get("atr_value",ep*0.002)
        sl=round(ep-atr*3 if direction=="LONG" else ep+atr*3,6)
        et=vi.get("exit_targets_long" if direction=="LONG" else "exit_targets_short",[])

        res=evaluate_outcome(direction,ep,sl,et,
                             highs[i+1:i+1+MAX_BARS_IN_TRADE],
                             lows[i+1:i+1+MAX_BARS_IN_TRADE],MAX_BARS_IN_TRADE)
        last=i
        trades.append({"direction":direction,"grade":grade,"hour":hour_now,
                        "outcome":res["outcome"],"r":res["r"],"price":ep})

    n_t=len(trades); wins=sum(1 for t in trades if t["outcome"]=="WIN")
    wr=round(wins/n_t*100,1) if n_t else 0.0
    avgr=round(sum(t["r"] for t in trades)/max(n_t,1),3)

    # P&L simulation
    capital=STARTING_CAPITAL; peak=STARTING_CAPITAL; max_dd=0.0
    for t in trades:
        capital+=capital*RISK_PER_TRADE*t["r"]
        capital=max(capital,0.01)
        if capital>peak: peak=capital
        dd=(peak-capital)/peak*100
        if dd>max_dd: max_dd=dd

    return {
        "config":   config_name,
        "symbol":   "XAUUSD",
        "timeframe":"1h",
        "trades":   n_t,
        "wins":     wins,
        "win_rate": wr,
        "avg_r":    avgr,
        "final":    round(capital,2),
        "return_pct":round((capital-STARTING_CAPITAL)/STARTING_CAPITAL*100,1),
        "max_dd":   round(max_dd,1),
        "by_hour":  {str(h): {
            "count": sum(1 for t in trades if t["hour"]==h),
            "win_rate": round(sum(1 for t in trades if t["hour"]==h and t["outcome"]=="WIN")
                              /max(sum(1 for t in trades if t["hour"]==h),1)*100,1)
        } for h in allowed_hours},
        "by_direction": {
            d: {"count":sum(1 for t in trades if t["direction"]==d),
                "win_rate":round(sum(1 for t in trades if t["direction"]==d and t["outcome"]=="WIN")
                                 /max(sum(1 for t in trades if t["direction"]==d),1)*100,1)}
            for d in ["LONG","SHORT"]
        },
    }


def main():
    print(f"\n{'='*65}")
    print(f"  GOLD (XAUUSD) PORTABILITY TEST")
    print(f"  Yahoo Finance 1h · 2-year period")
    print(f"  G hours: {GOLD_HOURS_G} UTC")
    print(f"  L hours: {GOLD_HOURS_L} UTC")
    print(f"{'='*65}")

    t0 = time.time()

    try:
        df = fetch_gold()
    except Exception as e:
        print(f"\n  FETCH FAILED: {e}")
        print(f"  Try: pip install yfinance curl_cffi")
        return

    if len(df) < WINDOW+MAX_BARS_IN_TRADE+10:
        print(f"  Insufficient bars ({len(df)}). Exiting.")
        return

    results = {}
    for name, hours, grades in [
        ("GOLD_G", GOLD_HOURS_G, None),
        ("GOLD_L", GOLD_HOURS_L, ["B"]),
    ]:
        print(f"\n  Running {name}...")
        r = run_config(df, name, hours, grades)
        results[name] = r
        print(f"  {name}: {r['trades']} trades  WR={r['win_rate']}%  "
              f"AvgR={r['avg_r']}  Final=${r['final']:,.2f} ({r['return_pct']:+.1f}%)")

    # Comparison table
    print(f"\n{'='*65}")
    print(f"  GOLD vs BTC COMPARISON  (${STARTING_CAPITAL:,.0f} start, {RISK_PER_TRADE*100:.0f}% risk)")
    print(f"{'='*65}")
    print(f"  {'Config':<12} {'TF':<5} {'Trades':>7} {'Win%':>6} "
          f"{'Avg R':>6} {'Final$':>9} {'Ret%':>7} {'DD%':>6}")
    print(f"  {'-'*60}")

    btc_baseline = [
        ("BTC_G", "15m", 28, 82.1, 0.172, 1149.76, 15.0, 4.1),
        ("BTC_L", "15m", 13, 92.3, 0.334, 1137.24, 13.7, 1.0),
    ]
    for row in btc_baseline:
        print(f"  {row[0]:<12} {row[1]:<5} {row[2]:>7} {row[3]:>5.1f}% "
              f"{row[4]:>6.3f} ${row[5]:>8,.2f} {row[6]:>6.1f}% {row[7]:>5.1f}%  <- BTC baseline")

    for name, r in results.items():
        print(f"  {name:<12} {'1h':<5} {r['trades']:>7} {r['win_rate']:>5.1f}% "
              f"{r['avg_r']:>6.3f} ${r['final']:>8,.2f} {r['return_pct']:>6.1f}% {r['max_dd']:>5.1f}%  <- GOLD")

    # Hour breakdown
    print(f"\n  GOLD_G by hour:")
    for h, s in results.get("GOLD_G",{}).get("by_hour",{}).items():
        bar="#"*int(s["win_rate"]/5)
        print(f"    Hour {h} UTC: {s['win_rate']:>5.1f}% WR  (n={s['count']})  {bar}")

    # Verdict
    print(f"\n{'='*65}")
    print(f"  PORTABILITY VERDICT")
    print(f"{'='*65}")
    for name, r in results.items():
        wr  = r["win_rate"]
        ret = r["return_pct"]
        if wr >= 70 and ret > 0:
            verdict = "PORTABLE -- strategy works on Gold"
        elif wr >= 55 and ret > 0:
            verdict = "MARGINAL -- positive but needs more testing"
        elif wr >= 35.7:
            verdict = "WEAK -- above breakeven but not reliable"
        else:
            verdict = "NOT PORTABLE -- does not transfer to Gold"
        print(f"  {name}: {verdict}")

    # Save
    with open("gold_backtest_results.json","w") as f:
        json.dump(results, f, indent=2)
    print(f"\n  Time: {time.time()-t0:.1f}s")
    print(f"  Saved: gold_backtest_results.json")


if __name__ == "__main__":
    main()
