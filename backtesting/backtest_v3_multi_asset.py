"""
backtest_v3_multi_asset.py
═══════════════════════════════════════════════════════════
Multi-asset portability test.
  Assets   : BTCUSDT (Binance 15m), ETHUSDT (Binance 15m), SPY (Yahoo 15m)
  Configs  : G_STRICT_SESSION, L_G_B_71214
  Purpose  : Verify that WME Sweep + Volume Profile edge is not
             BTC-specific — i.e. same signal logic fires on crypto
             and equities with similar quality metrics.

Output files
  BTC_G_report.json   BTC_L_report.json
  ETH_G_report.json   ETH_L_report.json
  SPY_G_report.json   SPY_L_report.json
  multi_asset_comparison.json
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
# SSL patch (Windows / Python 3.14 TLS issue)
# ─────────────────────────────────────────────────────────────
import requests as _req
_orig_get = _req.get
def _get_no_verify(url, **kw):
    kw.setdefault("verify", False)
    return _orig_get(url, **kw)
_req.get = _get_no_verify

# ─────────────────────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────────────────────
WINDOW            = 120
MAX_BARS_IN_TRADE = 60
COOLDOWN          = 20
TOTAL_BARS        = 10000   # crypto; Yahoo returns whatever is available
STARTING_CAPITAL  = 1000.0
RISK_PER_TRADE    = 0.02    # 2% compounding

# Two winning configs from the final_quality_safe_runner run
# G = G_STRICT_SESSION  (bar_tol=0, hours 7/12/14/18, A or B grade)
# L = L_G_B_71214       (bar_tol=1, hours 7/12/14,    B-only grade)
CONFIGS = [
    {
        "key":           "G",
        "label":         "G_STRICT_SESSION",
        "bar_tolerance": 0,
        "require_cvd":   False,
        "session_hours": [7, 12, 14, 18],
        "grade_mode":    "A_or_B",    # accept grade A and B
    },
    {
        "key":           "L",
        "label":         "L_G_B_71214",
        "bar_tolerance": 1,
        "require_cvd":   False,
        "session_hours": [7, 12, 14],
        "grade_mode":    "B_only",    # accept grade B only (not A)
    },
]

ASSETS = [
    {"tag": "BTC", "symbol": "BTCUSDT", "source": "binance", "interval": "15m"},
    {"tag": "ETH", "symbol": "ETHUSDT", "source": "binance", "interval": "15m"},
    {"tag": "SPY", "symbol": "SPY",     "source": "yahoo",   "interval": "15m"},
]


# ─────────────────────────────────────────────────────────────
# DATA FETCH — BINANCE
# ─────────────────────────────────────────────────────────────
def fetch_binance(symbol="BTCUSDT", interval="15m", total_bars=TOTAL_BARS):
    url, bars, end_ms = "https://api.binance.com/api/v3/klines", [], None
    print(f"  Fetching {total_bars} bars ({symbol} {interval}) from Binance ...")
    while len(bars) < total_bars:
        params = {"symbol": symbol, "interval": interval,
                  "limit": min(1000, total_bars - len(bars))}
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
        bars   = chunk + bars
        end_ms = int(chunk[0][0]) - 1
        time.sleep(0.2)
        if len(chunk) < 1000:
            break

    df = pd.DataFrame(bars, columns=[
        "open_time","open","high","low","close","volume",
        "close_time","qav","num_trades","tbbav","tbqav","ignore"])
    df["time"] = pd.to_datetime(df["open_time"].astype(float), unit="ms")
    for c in ["open", "high", "low", "close", "volume"]:
        df[c] = df[c].astype(float)
    df = (df[["time","open","high","low","close","volume"]]
          .sort_values("time").reset_index(drop=True))
    print(f"  Got {len(df)} bars  "
          f"({df.time.iloc[0].date()} to {df.time.iloc[-1].date()})")
    return df


# ─────────────────────────────────────────────────────────────
# DATA FETCH — YAHOO FINANCE  (SPY / equities)
# ─────────────────────────────────────────────────────────────
def fetch_yahoo(ticker="SPY", interval="15m", period="60d"):
    """
    Fetch OHLCV via yfinance and normalise to the same schema used by
    fetch_binance.  Yahoo's free tier allows ~60 days of 15-minute data.
    """
    try:
        import yfinance as yf
    except ImportError:
        raise ImportError("yfinance not installed -- run: pip install yfinance")

    # SSL bypass for Windows environments with certificate issues
    import ssl
    ssl._create_default_https_context = ssl._create_unverified_context

    # yfinance >=0.2.37 uses curl_cffi — pass an impersonate session if available
    try:
        from curl_cffi import requests as cffi_req
        _yf_session = cffi_req.Session(impersonate="chrome110", verify=False)
    except ImportError:
        _yf_session = None   # let yfinance use its default

    print(f"  Fetching {ticker} {interval} (period={period}) from Yahoo Finance ...")
    if _yf_session is not None:
        tk  = yf.Ticker(ticker, session=_yf_session)
    else:
        tk  = yf.Ticker(ticker)
    raw = tk.history(period=period, interval=interval, auto_adjust=True)
    if raw.empty:
        raise ValueError(f"No data returned for {ticker}")

    df = raw.reset_index()
    # Rename to unified schema
    time_col = "Datetime" if "Datetime" in df.columns else "Date"
    df = df.rename(columns={
        time_col:  "time",
        "Open":    "open",
        "High":    "high",
        "Low":     "low",
        "Close":   "close",
        "Volume":  "volume",
    })
    df = df[["time", "open", "high", "low", "close", "volume"]].copy()
    # Strip timezone so Timestamp comparisons work uniformly
    df["time"] = pd.to_datetime(df["time"]).dt.tz_localize(None)
    df = df.dropna().sort_values("time").reset_index(drop=True)
    print(f"  Got {len(df)} bars  "
          f"({df.time.iloc[0].date()} to {df.time.iloc[-1].date()})")
    return df


# ─────────────────────────────────────────────────────────────
# OUTCOME EVALUATOR  (level-based exits, identical to v3)
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

        # Stop hit
        if is_long and bl <= stop_loss:
            partial_r = sum(
                (t["price"] - entry_price) / stop_dist * (t["partial_pct"] / 100)
                for t in targets_hit)
            return {"outcome":       "WIN" if partial_r > 0.5 else "LOSS",
                    "bars_to_outcome": bar + 1,
                    "targets_hit":   targets_hit,
                    "exit_price":    round(stop_loss, 6),
                    "r_multiple":    round(partial_r - (remaining_pct / 100), 3)}
        if not is_long and bh >= stop_loss:
            partial_r = sum(
                (entry_price - t["price"]) / stop_dist * (t["partial_pct"] / 100)
                for t in targets_hit)
            return {"outcome":       "WIN" if partial_r > 0.5 else "LOSS",
                    "bars_to_outcome": bar + 1,
                    "targets_hit":   targets_hit,
                    "exit_price":    round(stop_loss, 6),
                    "r_multiple":    round(partial_r - (remaining_pct / 100), 3)}

        # Targets
        for tgt in exit_targets:
            if tgt["price"] in [t["price"] for t in targets_hit]:
                continue
            if ((is_long and bh >= tgt["price"]) or
                    (not is_long and bl <= tgt["price"])):
                targets_hit.append(tgt)
                remaining_pct -= tgt["partial_pct"]
                exit_price     = tgt["price"]

        if remaining_pct <= 5:
            r = sum(
                (t["price"] - entry_price if is_long else entry_price - t["price"])
                / stop_dist * (t["partial_pct"] / 100)
                for t in targets_hit)
            return {"outcome": "WIN", "bars_to_outcome": bar + 1,
                    "targets_hit": targets_hit,
                    "exit_price": round(exit_price, 6),
                    "r_multiple": round(r, 3)}

    # Timeout
    final = float(future_highs[-1] if is_long else future_lows[-1])
    r     = round((final - entry_price if is_long else entry_price - final)
                  / stop_dist, 3)
    return {"outcome":       "WIN" if r >= 0 else "LOSS",
            "bars_to_outcome": max_bars,
            "targets_hit":   targets_hit,
            "exit_price":    round(final, 6),
            "r_multiple":    r}


# ─────────────────────────────────────────────────────────────
# SINGLE ASSET × SINGLE CONFIG BACKTEST
# ─────────────────────────────────────────────────────────────
def run_config(df, symbol, timeframe, asset_tag, cfg):
    config_name   = cfg["label"]
    bar_tolerance = cfg["bar_tolerance"]
    require_cvd   = cfg["require_cvd"]
    session_hours = cfg["session_hours"]
    grade_mode    = cfg["grade_mode"]   # "A_or_B" | "B_only"

    n      = len(df)
    closes = df["close"].values
    highs  = df["high"].values
    lows   = df["low"].values
    times  = df["time"].values

    trade_log      = []
    no_trade_count = 0
    total_signals  = 0
    last_trade_bar = -999

    print(f"\n    [{asset_tag} | {config_name}] "
          f"tol={bar_tolerance}  cvd={require_cvd}  "
          f"hours={session_hours}  grade={grade_mode}")

    for i in range(WINDOW, n - MAX_BARS_IN_TRADE - 1):

        total_signals += 1

        # Session-hour filter
        bar_hour = pd.Timestamp(times[i]).hour
        if bar_hour not in session_hours:
            no_trade_count += 1
            continue

        # Cooldown
        if i - last_trade_bar < COOLDOWN:
            continue

        slice_df = df.iloc[i - WINDOW: i].copy().reset_index(drop=True)

        try:
            pavp      = compute_pavp(slice_df)
            atr_data  = compute_atr(slice_df)
            vol_intel = compute_volume_intelligence(slice_df, pavp)
            swing     = vol_intel.get("swing_profiles", {})
            wme       = compute_wme_signal(slice_df, bar_tolerance=bar_tolerance)
            curr      = wme["current"]
        except Exception:
            continue

        has_long  = curr["long_entry"]
        has_short = curr["short_entry"]
        if not has_long and not has_short:
            no_trade_count += 1
            continue

        direction = "LONG" if has_long else "SHORT"

        # Optional CVD filter
        if require_cvd:
            try:
                tri = compute_cvd_triangles(slice_df, cost_rank_thresh=75.0)
                check_key = "bars_since_up" if direction == "LONG" else "bars_since_down"
                if tri.get(check_key, 999) != 1:
                    no_trade_count += 1
                    continue
            except Exception:
                no_trade_count += 1
                continue

        # Location context
        in_lvn    = swing.get("in_lvn", False)
        hvn_zones = swing.get("hvn_zones", [])
        current_price = float(closes[i - 1])
        in_hvn = any(
            z["price_low"] <= current_price <= z["price_high"]
            for z in hvn_zones
        )

        # Confidence / grade
        wme_mod   = get_wme_confidence_modifier(wme, in_lvn, in_hvn, i)
        base_conf = 55.0
        speed_abs = abs(curr["speed"])
        base_conf += min(speed_abs * 15, 20)
        base_conf += wme_mod["modifier"]
        confidence = round(min(100.0, max(0.0, base_conf)), 1)
        grade      = "A" if confidence >= 75 else "B" if confidence >= 60 else "C"

        # Grade filter
        if grade_mode == "A_or_B" and grade == "C":
            no_trade_count += 1
            continue
        if grade_mode == "B_only" and grade != "B":
            no_trade_count += 1
            continue

        # Entry / stop
        entry_price = float(closes[i])
        atr_val     = atr_data.get("atr_value", entry_price * 0.002)
        stop_loss   = (round(entry_price - atr_val * 3.0, 6) if direction == "LONG"
                       else round(entry_price + atr_val * 3.0, 6))

        # Exit targets from volume profile
        tgt_key      = "exit_targets_long" if direction == "LONG" else "exit_targets_short"
        exit_targets = vol_intel.get(tgt_key, [])

        # Simulate
        result = evaluate_outcome(
            direction, entry_price, stop_loss, exit_targets,
            highs[i + 1: i + 1 + MAX_BARS_IN_TRADE],
            lows[i + 1:  i + 1 + MAX_BARS_IN_TRADE],
            MAX_BARS_IN_TRADE,
        )

        last_trade_bar = i

        trade_log.append({
            "bar_index":          i,
            "timestamp":          str(pd.Timestamp(times[i])),
            "hour_of_day":        bar_hour,
            "config":             config_name,
            "asset":              asset_tag,
            "symbol":             symbol,
            "timeframe":          timeframe,
            "direction":          direction,
            "grade":              grade,
            "confidence":         confidence,
            "in_lvn":             bool(in_lvn),
            "in_hvn":             bool(in_hvn),
            "wme_speed":          round(float(curr["speed"]), 4),
            "wme_sweep_bars_ago": int(min(curr["bars_since_sweep_low"],
                                         curr["bars_since_sweep_high"])),
            "wme_sweep_count_20": int(curr["sweep_count_20"]),
            "htf_bull":           bool(curr["htf_bull"]),
            "entry_price":        round(entry_price, 6),
            "stop_loss":          round(stop_loss, 6),
            "targets_hit":        len(result["targets_hit"]),
            "r_multiple":         result["r_multiple"],
            "outcome":            result["outcome"],
            "bars_to_outcome":    result["bars_to_outcome"],
        })

    wins = sum(1 for t in trade_log if t["outcome"] == "WIN")
    n_t  = len(trade_log)
    wr   = round(wins / n_t * 100, 1) if n_t else 0.0
    avg_r = round(sum(t["r_multiple"] for t in trade_log) / max(n_t, 1), 3)

    print(f"      -> Trades: {n_t}  WR: {wr}%  Avg R: {avg_r}")
    return trade_log, total_signals, no_trade_count


# ─────────────────────────────────────────────────────────────
# REPORT BUILDER
# ─────────────────────────────────────────────────────────────
def build_report(trade_log, config_name, asset_tag, symbol, timeframe,
                 total_bars, total_signals, no_trade_count):
    df = pd.DataFrame(trade_log)
    n  = len(df)
    if n == 0:
        return {"config": config_name, "asset": asset_tag,
                "symbol": symbol, "total_trades": 0,
                "win_rate": 0.0, "avg_r": 0.0}

    wins = (df.outcome == "WIN").sum()
    wr   = round(wins / n * 100, 1)
    avg_r = round(df.r_multiple.mean(), 3)

    def _grp(col, val):
        sub = df[df[col] == val]
        sn  = len(sub)
        sw  = (sub.outcome == "WIN").sum()
        return {"count":    int(sn),
                "win_rate": round(sw / sn * 100, 1) if sn else 0.0,
                "avg_r":    round(sub.r_multiple.mean(), 3) if sn else 0.0}

    return {
        "config":        config_name,
        "asset":         asset_tag,
        "symbol":        symbol,
        "timeframe":     timeframe,
        "total_bars":    total_bars,
        "total_signals": total_signals,
        "total_trades":  n,
        "wins":          int(wins),
        "win_rate":      wr,
        "avg_r":         avg_r,
        "breakeven_wr":  "35.7% (at 1.8R)",
        "by_grade": {g: _grp("grade", g) for g in ("A", "B", "C")},
        "by_direction": {
            d: _grp("direction", d)
            for d in sorted(df.direction.unique())
        },
        "lvn_trades": _grp("in_lvn", True),
        "hvn_trades": _grp("in_hvn", True),
        "by_hour": {
            str(h): _grp("hour_of_day", h)
            for h in sorted(df.hour_of_day.unique())
        },
        "filter_effectiveness": {
            "total_signals": total_signals,
            "no_trade":      no_trade_count,
            "trades_taken":  n,
            "filter_rate":   round((total_signals - n) / max(total_signals, 1) * 100, 1),
        },
    }


# ─────────────────────────────────────────────────────────────
# PNL SIMULATOR  (compounding, 2% risk/trade)
# ─────────────────────────────────────────────────────────────
def simulate_pnl(trade_log):
    capital = STARTING_CAPITAL
    peak    = STARTING_CAPITAL
    max_dd  = 0.0
    for t in trade_log:
        risk_dollars = capital * RISK_PER_TRADE
        capital     += risk_dollars * t["r_multiple"]
        capital      = max(capital, 0.01)
        if capital > peak:
            peak = capital
        dd = (peak - capital) / peak * 100
        if dd > max_dd:
            max_dd = dd
    total_return = round((capital - STARTING_CAPITAL) / STARTING_CAPITAL * 100, 1)
    return {
        "final":            round(capital, 2),
        "peak":             round(peak, 2),
        "max_drawdown_pct": round(max_dd, 1),
        "total_return_pct": total_return,
    }


# ─────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────
def main():
    print(f"\n{'='*70}")
    print(f"  TRADING AGENT V3 - MULTI-ASSET PORTABILITY TEST")
    print(f"  Assets : BTC, ETH (Binance 15m) | SPY (Yahoo 15m)")
    print(f"  Configs: G_STRICT_SESSION, L_G_B_71214")
    print(f"{'='*70}")

    # 1. Fetch all data
    dfs = {}
    for asset in ASSETS:
        tag = asset["tag"]
        print(f"\n[{tag}] Fetching data ...")
        try:
            if asset["source"] == "binance":
                dfs[tag] = fetch_binance(asset["symbol"], asset["interval"], TOTAL_BARS)
            else:
                dfs[tag] = fetch_yahoo(asset["symbol"], asset["interval"])
        except Exception as e:
            print(f"  ERROR fetching {tag}: {e}")
            dfs[tag] = None

    # 2. Run all asset x config combinations
    all_reports = {}   # key: "{TAG}_{cfg_key}"
    t0 = time.time()

    for asset in ASSETS:
        tag    = asset["tag"]
        df     = dfs.get(tag)
        if df is None or len(df) < WINDOW + MAX_BARS_IN_TRADE + 10:
            print(f"\n  [{tag}] Insufficient data - skipping")
            continue

        for cfg in CONFIGS:
            run_key = f"{tag}_{cfg['key']}"
            print(f"\n  Running {run_key} ...")

            tlog, tsig, ntrade = run_config(
                df, asset["symbol"], asset["interval"], tag, cfg
            )

            report = build_report(
                tlog, cfg["label"], tag, asset["symbol"], asset["interval"],
                len(df), tsig, ntrade,
            )
            pnl = simulate_pnl(tlog)
            report["pnl"] = pnl
            all_reports[run_key] = report

            # Save per-asset-config files
            csv_path  = f"{tag}_{cfg['key']}_trade_log.csv"
            json_path = f"{tag}_{cfg['key']}_report.json"
            pd.DataFrame(tlog).to_csv(csv_path, index=False)
            with open(json_path, "w") as f:
                json.dump(report, f, indent=2)
            print(f"      Saved: {csv_path}  {json_path}")

    print(f"\n  Total elapsed: {time.time() - t0:.1f}s")

    # 3. Comparison table
    print(f"\n{'='*80}")
    print(f"  MULTI-ASSET COMPARISON  (starting capital: ${STARTING_CAPITAL:,.0f})")
    print(f"{'='*80}")
    print(f"  {'Key':<12} {'Asset':>5} {'Config':<20} {'Bars':>6} {'Trades':>6} "
          f"{'WR%':>6} {'AvgR':>6} {'Ret%':>7} {'MaxDD%':>7}")
    print(f"  {'-'*76}")

    for asset in ASSETS:
        tag = asset["tag"]
        for cfg in CONFIGS:
            run_key = f"{tag}_{cfg['key']}"
            rpt     = all_reports.get(run_key)
            if rpt is None:
                continue
            pnl = rpt.get("pnl", {})
            print(
                f"  {run_key:<12} {tag:>5} {cfg['label']:<20} "
                f"{rpt['total_bars']:>6} {rpt['total_trades']:>6} "
                f"{rpt['win_rate']:>5.1f}% {rpt['avg_r']:>6.3f} "
                f"{pnl.get('total_return_pct', 0):>6.1f}% "
                f"{pnl.get('max_drawdown_pct', 0):>6.1f}%"
            )

    print(f"\n  Breakeven WR : 35.7%  (at 1.8R)")
    print(f"  Risk / trade : 2% compounding")

    # 4. Portability verdict
    print(f"\n{'-'*80}")
    print(f"  PORTABILITY VERDICT")
    print(f"{'-'*80}")

    passing   = []
    failing   = []
    for run_key, rpt in all_reports.items():
        n  = rpt["total_trades"]
        wr = rpt["win_rate"]
        ar = rpt["avg_r"]
        # Minimum bar for portability: >=5 trades AND WR >= 45% AND avg R >= -0.3
        if n >= 5 and wr >= 45.0 and ar >= -0.3:
            passing.append(run_key)
        else:
            failing.append(run_key)

    if passing:
        print(f"  PASS ({len(passing)}): {', '.join(passing)}")
    if failing:
        print(f"  FAIL ({len(failing)}): {', '.join(failing)}")

    # Overall verdict
    n_assets_passing = len(set(k.split("_")[0] for k in passing))
    if n_assets_passing >= 3:
        verdict = "FULLY PORTABLE - signal fires on all 3 asset classes"
    elif n_assets_passing == 2:
        verdict = "PARTIALLY PORTABLE - 2 of 3 asset classes pass"
    else:
        verdict = "LIMITED PORTABILITY - edge may be crypto-specific"
    print(f"\n  >> {verdict}")
    print(f"{'-'*80}")

    # 5. Save master comparison JSON
    with open("multi_asset_comparison.json", "w") as f:
        json.dump(all_reports, f, indent=2)
    print(f"\n  Saved: multi_asset_comparison.json")
    print(f"  Individual reports: BTC_G, BTC_L, ETH_G, ETH_L, SPY_G, SPY_L")


if __name__ == "__main__":
    main()
