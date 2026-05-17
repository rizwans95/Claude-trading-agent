"""
backtest_v3_multi_asset.py
═══════════════════════════════════════════════════════════
Cross-asset portability test.

Runs the two locked strategies (G and L) on three assets:
  1. BTCUSDT 15m  — baseline (locked in)
  2. ETHUSDT 15m  — crypto portability test
  3. SPY 5m       — US equity portability test (adjusted hours)

Session hours per asset:
  BTC/ETH: 7, 12, 14, 18 UTC  (London + NY sessions)
  SPY:     14, 15 UTC          (NY open + first hour)
           9:30–10:30am ET = 13:30–14:30 UTC → hours 13, 14

Output: single comparison table + per-asset JSON reports
═══════════════════════════════════════════════════════════
"""

import sys, os, json, time, warnings, requests
import numpy as np
import pandas as pd
warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from indicator_engine      import compute_pavp, compute_trend_speed, compute_atr
from volume_profile_engine import compute_volume_intelligence, compute_cvd_triangles
from wme_sweep_engine      import compute_wme_signal, get_wme_confidence_modifier

# ─────────────────────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────────────────────

WINDOW            = 120
MAX_BARS_IN_TRADE = 60
COOLDOWN          = 20
TOTAL_BARS        = 10000
STARTING_CAPITAL  = 1000.0
RISK_PER_TRADE    = 0.02

# SSL patch
import requests as _req
_orig = _req.get
def _no_verify(url, **kw):
    kw.setdefault("verify", False)
    return _orig(url, **kw)
_req.get = _no_verify


# ─────────────────────────────────────────────────────────────
# ASSET DEFINITIONS
# ─────────────────────────────────────────────────────────────

ASSETS = [
    {
        "name":          "BTC",
        "symbol":        "BTCUSDT",
        "timeframe":     "15m",
        "source":        "bybit",
        "total_bars":    10000,
        # Locked strategy hours
        "hours_G":       [7, 12, 14, 18],
        "hours_L":       [7, 12, 14],
        "description":   "Baseline — locked in strategy",
    },
    {
        "name":          "ETH",
        "symbol":        "ETHUSDT",
        "timeframe":     "15m",
        "source":        "bybit",
        "total_bars":    10000,
        # Same hours as BTC — crypto shares same session dynamics
        "hours_G":       [7, 12, 14, 18],
        "hours_L":       [7, 12, 14],
        "description":   "Crypto portability test — same hours as BTC",
    },
    {
        "name":          "SPY",
        "symbol":        "SPY",
        "timeframe":     "5m",
        "source":        "yahoo",
        "total_bars":    10000,
        # NY open = 13:30 UTC, first two hours = 13:30-15:30 UTC
        # Round to hours: 13, 14, 15 UTC
        "hours_G":       [13, 14, 15],
        "hours_L":       [13, 14],
        "description":   "US equity test — NY open hours (13-15 UTC)",
    },
]


# ─────────────────────────────────────────────────────────────
# DATA FETCHERS
# ─────────────────────────────────────────────────────────────

def fetch_bybit(symbol, interval, total_bars):
    bybit_interval = "15" if interval == "15m" else interval.replace("m", "")
    url  = "https://api.bybit.com/v5/market/kline"
    bars = []
    end_ms = None

    print(f"  Fetching {total_bars} bars ({symbol} {interval}) from Bybit...")

    while len(bars) < total_bars:
        params = {
            "category": "linear",
            "symbol":   symbol,
            "interval": bybit_interval,
            "limit":    min(1000, total_bars - len(bars)),
        }
        if end_ms is not None:
            params["end"] = end_ms

        r = requests.get(url, params=params, timeout=20)
        r.raise_for_status()
        payload = r.json()

        if payload.get("retCode") != 0:
            raise RuntimeError(f"Bybit error: {payload}")

        chunk = payload.get("result", {}).get("list", [])
        if not chunk:
            break

        bars.extend(chunk)
        end_ms = min(int(row[0]) for row in chunk) - 1
        time.sleep(0.2)
        if len(chunk) < 1000:
            break

    if not bars:
        raise RuntimeError(f"No data from Bybit for {symbol}")

    df = pd.DataFrame(bars, columns=[
        "open_time","open","high","low","close","volume","turnover"])
    df["time"] = pd.to_datetime(df["open_time"].astype(float), unit="ms")
    for c in ["open","high","low","close","volume"]:
        df[c] = df[c].astype(float)

    df = (df[["time","open","high","low","close","volume"]]
          .drop_duplicates("time").sort_values("time")
          .tail(total_bars).reset_index(drop=True))

    print(f"  Got {len(df)} bars  ({df.time.iloc[0].date()} to {df.time.iloc[-1].date()})")
    return df


def fetch_yahoo(symbol, interval, total_bars):
    """Fetch via yfinance for US equities."""
    try:
        import yfinance as yf
    except ImportError:
        raise RuntimeError("yfinance not installed. Run: pip install yfinance")

    print(f"  Fetching {symbol} {interval} from Yahoo Finance...")

    # Map interval
    yf_interval_map = {"5m": "5m", "15m": "15m", "1h": "1h", "4h": "4h"}
    yf_interval = yf_interval_map.get(interval, "5m")

    # Period needed to get enough bars
    bars_per_day = {"5m": 78, "15m": 26, "1h": 6.5}.get(interval, 78)
    days_needed  = max(int(total_bars / bars_per_day) + 10, 60)
    period       = f"{min(days_needed, 59)}d"  # Yahoo max 60d for intraday

    try:
        ticker = yf.Ticker(symbol)
        df     = ticker.history(period=period, interval=yf_interval, auto_adjust=True)
    except Exception as e:
        raise RuntimeError(f"Yahoo fetch failed: {e}")

    if df.empty:
        raise RuntimeError(f"No data from Yahoo for {symbol}")

    df = df.reset_index()
    time_col = "Datetime" if "Datetime" in df.columns else "Date"
    df = df.rename(columns={
        time_col: "time", "Open": "open", "High": "high",
        "Low": "low", "Close": "close", "Volume": "volume"
    })
    df["time"] = pd.to_datetime(df["time"]).dt.tz_localize(None)
    df = (df[["time","open","high","low","close","volume"]]
          .dropna().sort_values("time")
          .tail(total_bars).reset_index(drop=True))

    print(f"  Got {len(df)} bars  ({df.time.iloc[0].date()} to {df.time.iloc[-1].date()})")
    return df


def fetch_data(asset):
    if asset["source"] == "bybit":
        return fetch_bybit(asset["symbol"], asset["timeframe"], asset["total_bars"])
    elif asset["source"] == "yahoo":
        return fetch_yahoo(asset["symbol"], asset["timeframe"], asset["total_bars"])
    else:
        raise ValueError(f"Unknown source: {asset['source']}")


# ─────────────────────────────────────────────────────────────
# OUTCOME EVALUATOR
# ─────────────────────────────────────────────────────────────

def evaluate_outcome(direction, entry_price, stop_loss, exit_targets,
                     future_highs, future_lows, max_bars):
    is_long       = direction == "LONG"
    targets_hit   = []
    remaining_pct = 100.0
    exit_price    = entry_price
    stop_dist     = abs(entry_price - stop_loss)
    if stop_dist == 0:
        stop_dist = entry_price * 0.003

    for bar in range(min(max_bars, len(future_highs))):
        bh = float(future_highs[bar])
        bl = float(future_lows[bar])

        if is_long and bl <= stop_loss:
            partial_r = sum(
                (t["price"]-entry_price)/stop_dist*(t["partial_pct"]/100)
                for t in targets_hit)
            return {"outcome": "WIN" if partial_r > 0.5 else "LOSS",
                    "bars_to_outcome": bar+1, "targets_hit": targets_hit,
                    "r_multiple": round(partial_r - (remaining_pct/100), 3)}
        if not is_long and bh >= stop_loss:
            partial_r = sum(
                (entry_price-t["price"])/stop_dist*(t["partial_pct"]/100)
                for t in targets_hit)
            return {"outcome": "WIN" if partial_r > 0.5 else "LOSS",
                    "bars_to_outcome": bar+1, "targets_hit": targets_hit,
                    "r_multiple": round(partial_r - (remaining_pct/100), 3)}

        for tgt in exit_targets:
            if tgt["price"] in [t["price"] for t in targets_hit]:
                continue
            if ((is_long and bh >= tgt["price"]) or
                    (not is_long and bl <= tgt["price"])):
                targets_hit.append(tgt)
                remaining_pct -= tgt["partial_pct"]
                exit_price = tgt["price"]

        if remaining_pct <= 5:
            r = sum(
                (t["price"]-entry_price if is_long else entry_price-t["price"])
                /stop_dist*(t["partial_pct"]/100) for t in targets_hit)
            return {"outcome":"WIN","bars_to_outcome":bar+1,
                    "targets_hit":targets_hit,"r_multiple":round(r,3)}

    final = float(future_highs[-1] if is_long else future_lows[-1])
    r     = round((final-entry_price if is_long else entry_price-final)/stop_dist, 3)
    return {"outcome":"WIN" if r>=0 else "LOSS","bars_to_outcome":max_bars,
            "targets_hit":targets_hit,"r_multiple":r}


# ─────────────────────────────────────────────────────────────
# SINGLE CONFIG RUNNER
# ─────────────────────────────────────────────────────────────

def run_config(df, symbol, timeframe, config_name,
               bar_tolerance=0, require_cvd=False,
               allowed_grades=None, allowed_hours=None):
    n      = len(df)
    closes = df["close"].values
    highs  = df["high"].values
    lows   = df["low"].values
    times  = df["time"].values

    trade_log      = []
    no_trade_count = 0
    total_signals  = 0
    last_trade_bar = -999

    print(f"  [{config_name}] hours={allowed_hours} grades={allowed_grades}")

    for i in range(WINDOW, n - MAX_BARS_IN_TRADE - 1):
        total_signals += 1

        if i - last_trade_bar < COOLDOWN:
            continue

        slice_df = df.iloc[i - WINDOW: i].copy().reset_index(drop=True)

        try:
            pavp     = compute_pavp(slice_df)
            atr_data = compute_atr(slice_df)
            vol_intel= compute_volume_intelligence(slice_df, pavp)
            swing    = vol_intel.get("swing_profiles", {})
            wme      = compute_wme_signal(slice_df, bar_tolerance=bar_tolerance)
            curr     = wme["current"]
        except Exception:
            continue

        has_long  = curr["long_entry"]
        has_short = curr["short_entry"]

        if not has_long and not has_short:
            no_trade_count += 1
            continue

        direction = "LONG" if has_long else "SHORT"

        # Hour filter
        hour_now = pd.Timestamp(times[i]).hour
        if allowed_hours is not None and hour_now not in allowed_hours:
            no_trade_count += 1
            continue

        # CVD filter
        if require_cvd:
            try:
                tri = compute_cvd_triangles(slice_df, cost_rank_thresh=75.0)
                key = "bars_since_up" if direction == "LONG" else "bars_since_down"
                if tri.get(key, 999) > 1:
                    no_trade_count += 1
                    continue
            except Exception:
                no_trade_count += 1
                continue

        # Confidence and grade
        in_lvn  = swing.get("in_lvn", False)
        hvn_zones = swing.get("hvn_zones", [])
        current_price = float(closes[i-1])
        in_hvn  = any(z["price_low"] <= current_price <= z["price_high"]
                      for z in hvn_zones)

        wme_mod    = get_wme_confidence_modifier(wme, in_lvn, in_hvn, i)
        speed_abs  = abs(curr["speed"])
        confidence = round(min(100.0, max(0.0,
                        55.0 + min(speed_abs*15, 20) + wme_mod["modifier"])), 1)
        grade      = "A" if confidence >= 75 else "B" if confidence >= 60 else "C"

        if grade == "C":
            no_trade_count += 1
            continue

        if allowed_grades is not None and grade not in allowed_grades:
            no_trade_count += 1
            continue

        # Entry
        entry_price  = float(closes[i])
        atr_val      = atr_data.get("atr_value", entry_price * 0.002)
        stop_loss    = round(
            entry_price - atr_val*3.0 if direction=="LONG"
            else entry_price + atr_val*3.0, 6)
        exit_targets = vol_intel.get(
            "exit_targets_long" if direction=="LONG"
            else "exit_targets_short", [])

        result = evaluate_outcome(
            direction, entry_price, stop_loss, exit_targets,
            highs[i+1: i+1+MAX_BARS_IN_TRADE],
            lows[i+1:  i+1+MAX_BARS_IN_TRADE],
            MAX_BARS_IN_TRADE)

        last_trade_bar = i

        trade_log.append({
            "bar_index":          i,
            "timestamp":          str(pd.Timestamp(times[i])),
            "hour_of_day":        hour_now,
            "config":             config_name,
            "symbol":             symbol,
            "timeframe":          timeframe,
            "direction":          direction,
            "grade":              grade,
            "confidence":         confidence,
            "in_lvn":             in_lvn,
            "in_hvn":             in_hvn,
            "wme_speed":          round(float(curr["speed"]), 4),
            "wme_sweep_bars_ago": int(min(curr["bars_since_sweep_low"],
                                         curr["bars_since_sweep_high"])),
            "entry_price":        round(entry_price, 6),
            "stop_loss":          round(stop_loss, 6),
            "targets_hit":        len(result["targets_hit"]),
            "r_multiple":         result["r_multiple"],
            "outcome":            result["outcome"],
            "bars_to_outcome":    result["bars_to_outcome"],
        })

    wins = sum(1 for t in trade_log if t["outcome"] == "WIN")
    n_t  = len(trade_log)
    wr   = round(wins/n_t*100, 1) if n_t else 0.0
    avg_r= round(sum(t["r_multiple"] for t in trade_log)/max(n_t,1), 3)
    print(f"         Trades={n_t}  WR={wr}%  AvgR={avg_r}")

    return trade_log, total_signals, no_trade_count


# ─────────────────────────────────────────────────────────────
# REPORT BUILDER
# ─────────────────────────────────────────────────────────────

def build_report(trade_log, config_name, symbol, timeframe,
                 total_bars, total_signals, no_trade):
    df = pd.DataFrame(trade_log)
    n  = len(df)
    if n == 0:
        return {"config": config_name, "symbol": symbol,
                "total_trades": 0, "win_rate": 0.0, "avg_r": 0.0}

    wins = (df.outcome=="WIN").sum()

    def _grp(col, val):
        sub = df[df[col]==val]
        sn  = len(sub)
        sw  = (sub.outcome=="WIN").sum()
        return {"count": int(sn),
                "win_rate": round(sw/sn*100,1) if sn else 0.0,
                "avg_r": round(sub.r_multiple.mean(),3) if sn else 0.0}

    # Robustness splits
    split_70 = int(total_bars * 0.70)
    split_50 = int(total_bars * 0.50)

    def _stats(sub):
        sn = len(sub)
        if sn == 0: return {"count":0,"wins":0,"win_rate":0.0,"avg_r":0.0}
        sw = int((sub.outcome=="WIN").sum())
        return {"count":int(sn),"wins":sw,
                "win_rate":round(sw/sn*100,1),
                "avg_r":round(float(sub.r_multiple.mean()),3)}

    return {
        "config":        config_name,
        "symbol":        symbol,
        "timeframe":     timeframe,
        "total_bars":    total_bars,
        "total_signals": total_signals,
        "total_trades":  n,
        "wins":          int(wins),
        "win_rate":      round(wins/n*100, 1),
        "avg_r":         round(df.r_multiple.mean(), 3),
        "breakeven_wr":  "35.7% (at 1.8R)",
        "by_grade":      {g: _grp("grade",g) for g in ("A","B","C")},
        "by_direction":  {d: _grp("direction",d) for d in df.direction.unique()},
        "by_hour":       {str(h): _grp("hour_of_day",h)
                          for h in sorted(df.hour_of_day.unique())},
        "lvn_trades":    _grp("in_lvn", True),
        "robustness": {
            "first_70_pct":  _stats(df[df.bar_index < split_70]),
            "last_30_pct":   _stats(df[df.bar_index >= split_70]),
            "first_half":    _stats(df[df.bar_index < split_50]),
            "second_half":   _stats(df[df.bar_index >= split_50]),
        },
        "filter_effectiveness": {
            "total_signals": total_signals,
            "no_trade":      no_trade,
            "trades_taken":  n,
            "filter_rate":   round((total_signals-n)/max(total_signals,1)*100,1),
        }
    }


# ─────────────────────────────────────────────────────────────
# PNL SIMULATION
# ─────────────────────────────────────────────────────────────

def simulate_pnl(trade_log):
    capital = STARTING_CAPITAL
    peak    = STARTING_CAPITAL
    max_dd  = 0.0
    for t in trade_log:
        capital += capital * RISK_PER_TRADE * t["r_multiple"]
        capital  = max(capital, 0.01)
        if capital > peak: peak = capital
        dd = (peak - capital) / peak * 100
        if dd > max_dd: max_dd = dd
    return {
        "final":            round(capital, 2),
        "peak":             round(peak, 2),
        "max_drawdown_pct": round(max_dd, 1),
        "total_return_pct": round((capital-STARTING_CAPITAL)/STARTING_CAPITAL*100, 1),
    }


# ─────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────

def main():
    print(f"\n{'='*70}")
    print(f"  MULTI-ASSET PORTABILITY TEST")
    print(f"  BTC (baseline) vs ETH vs SPY")
    print(f"  Strategies: G_STRICT_SESSION and L_G_B_71214")
    print(f"{'='*70}")

    all_results = {}
    t0 = time.time()

    for asset in ASSETS:
        name      = asset["name"]
        symbol    = asset["symbol"]
        timeframe = asset["timeframe"]
        desc      = asset["description"]

        print(f"\n{'─'*60}")
        print(f"  ASSET: {name}  ({symbol} {timeframe})")
        print(f"  {desc}")
        print(f"{'─'*60}")

        # Fetch data
        try:
            df = fetch_data(asset)
        except Exception as e:
            print(f"  DATA FETCH FAILED: {e}")
            print(f"  Skipping {name}")
            continue

        if len(df) < WINDOW + MAX_BARS_IN_TRADE + 10:
            print(f"  Insufficient bars ({len(df)}). Skipping.")
            continue

        # Run G strategy
        g_name = f"{name}_G"
        g_log, g_sig, g_nt = run_config(
            df, symbol, timeframe, g_name,
            bar_tolerance  = 0,
            require_cvd    = False,
            allowed_grades = None,          # A and B
            allowed_hours  = asset["hours_G"],
        )
        g_report      = build_report(g_log, g_name, symbol, timeframe,
                                     len(df), g_sig, g_nt)
        g_report["pnl"] = simulate_pnl(g_log)
        all_results[g_name] = g_report

        pd.DataFrame(g_log).to_csv(f"{name}_G_trade_log.csv", index=False)
        with open(f"{name}_G_report.json","w") as f:
            json.dump(g_report, f, indent=2)

        # Run L strategy
        l_name = f"{name}_L"
        l_log, l_sig, l_nt = run_config(
            df, symbol, timeframe, l_name,
            bar_tolerance  = 0,
            require_cvd    = False,
            allowed_grades = ["B"],         # B only
            allowed_hours  = asset["hours_L"],
        )
        l_report      = build_report(l_log, l_name, symbol, timeframe,
                                     len(df), l_sig, l_nt)
        l_report["pnl"] = simulate_pnl(l_log)
        all_results[l_name] = l_report

        pd.DataFrame(l_log).to_csv(f"{name}_L_trade_log.csv", index=False)
        with open(f"{name}_L_report.json","w") as f:
            json.dump(l_report, f, indent=2)

    # ── Comparison Table ──────────────────────────────────────
    print(f"\n{'='*80}")
    print(f"  MULTI-ASSET COMPARISON  (${STARTING_CAPITAL:,.0f} start, {RISK_PER_TRADE*100:.0f}% risk/trade)")
    print(f"{'='*80}")
    print(f"  {'Config':<18} {'Asset':<6} {'Trades':>6} {'Win%':>6} "
          f"{'Avg R':>6} {'Final $':>9} {'Return%':>8} {'MaxDD%':>7} {'Robust':>8}")
    print(f"  {'-'*76}")

    for config_name, report in all_results.items():
        pnl    = report.get("pnl", {})
        rob    = report.get("robustness", {})
        # Robustness score: avg of first/second half win rates
        fh_wr  = rob.get("first_half",  {}).get("win_rate", 0)
        sh_wr  = rob.get("second_half", {}).get("win_rate", 0)
        rob_avg = f"{fh_wr:.0f}/{sh_wr:.0f}"

        strategy = "G" if "_G" in config_name else "L"
        asset    = config_name.replace("_G","").replace("_L","")

        # Flag BTC as baseline
        baseline = " ← BASELINE" if asset == "BTC" else ""

        print(f"  {config_name:<18} {asset:<6} {report['total_trades']:>6} "
              f"{report['win_rate']:>5.1f}% {report.get('avg_r',0.0):>6.3f} "
              f"${pnl.get('final',0):>8,.2f} "
              f"{pnl.get('total_return_pct',0):>7.1f}% "
              f"{pnl.get('max_drawdown_pct',0):>6.1f}% "
              f"{rob_avg:>8}{baseline}")

    print(f"\n  Breakeven win rate: 35.7%  (at 1.8R)")
    print(f"  Robustness = first_half WR / second_half WR")
    print(f"  Total time: {time.time()-t0:.1f}s")

    # Portability verdict
    print(f"\n{'='*80}")
    print(f"  PORTABILITY VERDICT")
    print(f"{'='*80}")
    for asset_cfg in ["ETH_G","ETH_L","SPY_G","SPY_L"]:
        if asset_cfg not in all_results:
            continue
        r   = all_results[asset_cfg]
        btc = all_results.get(asset_cfg.replace("ETH","BTC").replace("SPY","BTC"), {})
        wr  = r["win_rate"]
        btc_wr = btc.get("win_rate", 0)
        avg_r  = r.get("avg_r", 0)

        if wr >= 70 and avg_r > 0:
            verdict = "PORTABLE — strategy works on this asset"
        elif wr >= 55 and avg_r > 0:
            verdict = "MARGINAL — positive but needs further testing"
        elif wr >= 35.7 and avg_r >= 0:
            verdict = "WEAK — above breakeven but not reliable"
        else:
            verdict = "NOT PORTABLE — strategy does not transfer"

        gap = round(wr - btc_wr, 1)
        gap_str = f"({gap:+.1f}% vs BTC)"
        print(f"  {asset_cfg:<10}: {verdict} {gap_str}")

    with open("multi_asset_comparison.json","w") as f:
        json.dump(all_results, f, indent=2)
    print(f"\n  Files: 6 CSVs + 6 JSONs + multi_asset_comparison.json")


if __name__ == "__main__":
    main()
