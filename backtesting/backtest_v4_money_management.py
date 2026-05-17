"""
backtest_v4_money_management.py
===========================================================
Phase 2: Money Management overlay test.

Runs 8 MM configurations on all 4 locked strategies:
  BTC_G  - BTCUSDT 15m  G_STRICT_SESSION
  BTC_L  - BTCUSDT 15m  L_G_B_71214
  ETH_G  - ETHUSDT 15m  G_STRICT_SESSION
  SPY_L  - SPY     1h   hours 13,14 Grade B

MM configs tested (entries never change):
  MM1  FIXED_1PCT         - 1% risk per trade, no throttle
  MM2  FIXED_2PCT         - 2% risk per trade, no throttle
  MM3  FIXED_3PCT         - 3% risk per trade, no throttle
  MM4  G_2PCT_L_1PCT      - G strat=2%, L strat=1%
  MM5  DRAWDOWN_THROTTLE  - after 2 losses cut risk 50%, restore at new equity high
  MM6  AFTER_LOSS_REDUCE  - each loss: -0.5% risk, floor at 0.5%
  MM7  AFTER_WIN_SCALE    - each win: +0.25% risk, cap at 3%
  MM8  PROP_FIRM_SAFE     - 1% risk, stop all trading if drawdown > 4%

Output: single comparison table + per-strategy breakdown + JSON
===========================================================
"""

import sys, os, json, time, warnings, requests
import numpy as np
import pandas as pd
warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from indicator_engine      import compute_pavp, compute_trend_speed, compute_atr
from volume_profile_engine import compute_volume_intelligence, compute_cvd_triangles
from wme_sweep_engine      import compute_wme_signal, get_wme_confidence_modifier

# -------------------------------------------------------------
# LOCKED STRATEGY DEFINITIONS
# -------------------------------------------------------------

STRATEGIES = [
    {
        "key":           "BTC_G",
        "symbol":        "BTCUSDT",
        "timeframe":     "15m",
        "source":        "bybit",
        "total_bars":    10000,
        "window":        120,
        "max_bars":      60,
        "cooldown":      20,
        "allowed_hours": [7, 12, 14, 18],
        "allowed_grades": None,          # A and B
        "mode":          "G",
    },
    {
        "key":           "BTC_L",
        "symbol":        "BTCUSDT",
        "timeframe":     "15m",
        "source":        "bybit",
        "total_bars":    10000,
        "window":        120,
        "max_bars":      60,
        "cooldown":      20,
        "allowed_hours": [7, 12, 14],
        "allowed_grades": ["B"],
        "mode":          "L",
    },
    {
        "key":           "ETH_G",
        "symbol":        "ETHUSDT",
        "timeframe":     "15m",
        "source":        "bybit",
        "total_bars":    10000,
        "window":        120,
        "max_bars":      60,
        "cooldown":      20,
        "allowed_hours": [7, 12, 14, 18],
        "allowed_grades": None,
        "mode":          "G",
    },
    {
        "key":           "SPY_L",
        "symbol":        "SPY",
        "timeframe":     "1h",
        "source":        "yahoo",
        "total_bars":    3500,
        "window":        80,
        "max_bars":      20,
        "cooldown":      10,
        "allowed_hours": [13, 14],
        "allowed_grades": ["B"],
        "mode":          "L",
    },
]

# -------------------------------------------------------------
# MM CONFIG DEFINITIONS
# -------------------------------------------------------------

MM_CONFIGS = [
    {"name": "MM1_FIXED_1PCT",
     "base_risk": 0.01, "throttle": False, "scale_wins": False,
     "scale_losses": False, "prop_firm": False},
    {"name": "MM2_FIXED_2PCT",
     "base_risk": 0.02, "throttle": False, "scale_wins": False,
     "scale_losses": False, "prop_firm": False},
    {"name": "MM3_FIXED_3PCT",
     "base_risk": 0.03, "throttle": False, "scale_wins": False,
     "scale_losses": False, "prop_firm": False},
    {"name": "MM4_G2_L1",
     "base_risk": None,  # set per mode
     "throttle": False, "scale_wins": False,
     "scale_losses": False, "prop_firm": False,
     "g_risk": 0.02, "l_risk": 0.01},
    {"name": "MM5_DRAWDOWN_THROTTLE",
     "base_risk": 0.02, "throttle": True,
     "throttle_losses": 2, "throttle_cut": 0.5,
     "scale_wins": False, "scale_losses": False, "prop_firm": False},
    {"name": "MM6_AFTER_LOSS_REDUCE",
     "base_risk": 0.02, "throttle": False,
     "scale_wins": False, "scale_losses": True,
     "loss_step": -0.005, "loss_floor": 0.005,
     "prop_firm": False},
    {"name": "MM7_AFTER_WIN_SCALE",
     "base_risk": 0.02, "throttle": False,
     "scale_wins": True, "win_step": 0.0025, "win_cap": 0.03,
     "scale_losses": False, "prop_firm": False},
    {"name": "MM8_PROP_FIRM_SAFE",
     "base_risk": 0.01, "throttle": False,
     "scale_wins": False, "scale_losses": False,
     "prop_firm": True, "max_drawdown_pct": 4.0},
]

# -------------------------------------------------------------
# SSL PATCH
# -------------------------------------------------------------

import requests as _req
_orig = _req.get
def _no_verify(url, **kw):
    kw.setdefault("verify", False)
    return _orig(url, **kw)
_req.get = _no_verify


# -------------------------------------------------------------
# DATA FETCHERS
# -------------------------------------------------------------

def fetch_bybit(symbol, interval, total_bars):
    bybit_interval = "15" if interval == "15m" else interval.replace("m","")
    url, bars, end_ms = "https://api.bybit.com/v5/market/kline", [], None
    print(f"  Fetching {total_bars} bars ({symbol} {interval}) from Bybit...")
    while len(bars) < total_bars:
        params = {"category":"linear","symbol":symbol,
                  "interval":bybit_interval,"limit":min(1000,total_bars-len(bars))}
        if end_ms: params["end"] = end_ms
        r = requests.get(url, params=params, timeout=20)
        r.raise_for_status()
        p = r.json()
        if p.get("retCode") != 0: raise RuntimeError(f"Bybit error: {p}")
        chunk = p.get("result",{}).get("list",[])
        if not chunk: break
        bars.extend(chunk)
        end_ms = min(int(row[0]) for row in chunk) - 1
        time.sleep(0.2)
        if len(chunk) < 1000: break
    if not bars: raise RuntimeError(f"No data from Bybit for {symbol}")
    df = pd.DataFrame(bars, columns=[
        "open_time","open","high","low","close","volume","turnover"])
    df["time"] = pd.to_datetime(df["open_time"].astype(float), unit="ms")
    for c in ["open","high","low","close","volume"]: df[c] = df[c].astype(float)
    df = (df[["time","open","high","low","close","volume"]]
          .drop_duplicates("time").sort_values("time")
          .tail(total_bars).reset_index(drop=True))
    print(f"  Got {len(df)} bars  ({df.time.iloc[0].date()} to {df.time.iloc[-1].date()})")
    return df


def fetch_yahoo(symbol, interval, total_bars):
    try: import yfinance as yf
    except ImportError: raise RuntimeError("pip install yfinance")
    print(f"  Fetching {symbol} {interval} from Yahoo Finance...")
    try:
        from curl_cffi.requests import Session
        session = Session(verify=False, impersonate="chrome110")
        ticker  = yf.Ticker(symbol, session=session)
    except ImportError:
        ticker = yf.Ticker(symbol)
    df = ticker.history(period="60d", interval=interval, auto_adjust=True)
    if df is None or df.empty:
        df = yf.download(symbol, period="60d", interval=interval, auto_adjust=True)
    if df is None or df.empty:
        raise RuntimeError(f"No data for {symbol}")
    df = df.reset_index()
    tc = "Datetime" if "Datetime" in df.columns else "Date"
    df = df.rename(columns={tc:"time","Open":"open","High":"high",
                             "Low":"low","Close":"close","Volume":"volume"})
    df["time"] = pd.to_datetime(df["time"]).dt.tz_localize(None)
    df = (df[["time","open","high","low","close","volume"]]
          .dropna().sort_values("time")
          .tail(total_bars).reset_index(drop=True))
    print(f"  Got {len(df)} bars  ({df.time.iloc[0].date()} to {df.time.iloc[-1].date()})")
    return df


def fetch_data(strat):
    if strat["source"] == "bybit":
        return fetch_bybit(strat["symbol"], strat["timeframe"], strat["total_bars"])
    return fetch_yahoo(strat["symbol"], strat["timeframe"], strat["total_bars"])


# -------------------------------------------------------------
# OUTCOME EVALUATOR
# -------------------------------------------------------------

def evaluate_outcome(direction, entry_price, stop_loss, exit_targets,
                     future_highs, future_lows, max_bars):
    is_long       = direction == "LONG"
    targets_hit   = []
    remaining_pct = 100.0
    stop_dist     = abs(entry_price - stop_loss)
    if stop_dist == 0: stop_dist = entry_price * 0.003

    for bar in range(min(max_bars, len(future_highs))):
        bh = float(future_highs[bar])
        bl = float(future_lows[bar])

        if is_long and bl <= stop_loss:
            partial_r = sum((t["price"]-entry_price)/stop_dist*(t["partial_pct"]/100)
                            for t in targets_hit)
            return {"outcome":"WIN" if partial_r>0.5 else "LOSS",
                    "bars_to_outcome":bar+1,
                    "r_multiple":round(partial_r-(remaining_pct/100),3)}

        if not is_long and bh >= stop_loss:
            partial_r = sum((entry_price-t["price"])/stop_dist*(t["partial_pct"]/100)
                            for t in targets_hit)
            return {"outcome":"WIN" if partial_r>0.5 else "LOSS",
                    "bars_to_outcome":bar+1,
                    "r_multiple":round(partial_r-(remaining_pct/100),3)}

        for tgt in exit_targets:
            if tgt["price"] in [t["price"] for t in targets_hit]: continue
            if ((is_long and bh>=tgt["price"]) or
                    (not is_long and bl<=tgt["price"])):
                targets_hit.append(tgt)
                remaining_pct -= tgt["partial_pct"]
                # Move stop to breakeven after second target hit
                if len(targets_hit) == 2:
                    stop_loss = entry_price

        if remaining_pct <= 5:
            r = sum((t["price"]-entry_price if is_long else entry_price-t["price"])
                    /stop_dist*(t["partial_pct"]/100) for t in targets_hit)
            return {"outcome":"WIN","bars_to_outcome":bar+1,"r_multiple":round(r,3)}

    final = float(future_highs[-1] if is_long else future_lows[-1])
    r     = round((final-entry_price if is_long else entry_price-final)/stop_dist,3)
    return {"outcome":"WIN" if r>=0 else "LOSS",
            "bars_to_outcome":max_bars,"r_multiple":r}


# -------------------------------------------------------------
# GENERATE RAW TRADE LOG (entries only - MM applied separately)
# -------------------------------------------------------------

def generate_trade_log(df, strat):
    """
    Runs the locked strategy and returns a raw trade log
    with r_multiple per trade. MM is applied on top later.
    """
    n      = len(df)
    closes = df["close"].values
    highs  = df["high"].values
    lows   = df["low"].values
    times  = df["time"].values

    WINDOW   = strat["window"]
    MAX_BARS = strat["max_bars"]
    COOLDOWN = strat["cooldown"]
    allowed_hours  = strat["allowed_hours"]
    allowed_grades = strat["allowed_grades"]
    mode           = strat["mode"]

    trade_log      = []
    last_trade_bar = -999

    for i in range(WINDOW, n - MAX_BARS - 1):
        if i - last_trade_bar < COOLDOWN:
            continue

        slice_df = df.iloc[i - WINDOW: i].copy().reset_index(drop=True)

        try:
            pavp     = compute_pavp(slice_df)
            atr_data = compute_atr(slice_df)
            vol_intel= compute_volume_intelligence(slice_df, pavp)
            swing    = vol_intel.get("swing_profiles", {})
            wme      = compute_wme_signal(slice_df, bar_tolerance=0)
            curr     = wme["current"]
        except Exception:
            continue

        has_long  = curr["long_entry"]
        has_short = curr["short_entry"]
        if not has_long and not has_short:
            continue

        direction = "LONG" if has_long else "SHORT"
        hour_now  = pd.Timestamp(times[i]).hour

        if hour_now not in allowed_hours:
            continue

        # SPY short restriction
        if strat["symbol"] == "SPY" and direction == "SHORT":
            continue

        in_lvn = swing.get("in_lvn", False)
        in_hvn = any(z["price_low"] <= float(closes[i-1]) <= z["price_high"]
                     for z in swing.get("hvn_zones", []))

        wme_mod    = get_wme_confidence_modifier(wme, in_lvn, in_hvn, i)
        speed_abs  = abs(curr["speed"])
        confidence = round(min(100.0, max(0.0,
                        55.0 + min(speed_abs*15, 20) + wme_mod["modifier"])), 1)
        grade      = "A" if confidence>=75 else "B" if confidence>=60 else "C"

        if grade == "C":
            continue
        if allowed_grades is not None and grade not in allowed_grades:
            continue

        entry_price  = float(closes[i])
        atr_val      = atr_data.get("atr_value", entry_price*0.002)
        stop_loss    = round(
            entry_price - atr_val*3.0 if direction=="LONG"
            else entry_price + atr_val*3.0, 6)
        exit_targets = vol_intel.get(
            "exit_targets_long" if direction=="LONG"
            else "exit_targets_short", [])

        result = evaluate_outcome(
            direction, entry_price, stop_loss, exit_targets,
            highs[i+1: i+1+MAX_BARS], lows[i+1: i+1+MAX_BARS], MAX_BARS)

        last_trade_bar = i
        trade_log.append({
            "bar_index":       i,
            "timestamp":       str(pd.Timestamp(times[i])),
            "hour_of_day":     hour_now,
            "direction":       direction,
            "grade":           grade,
            "confidence":      confidence,
            "in_lvn":          in_lvn,
            "r_multiple":      result["r_multiple"],
            "outcome":         result["outcome"],
            "bars_to_outcome": result["bars_to_outcome"],
            "mode":            mode,
        })

    return trade_log


# -------------------------------------------------------------
# MM SIMULATOR
# -------------------------------------------------------------

def simulate_mm(trade_log, mm_cfg, mode="G", starting_capital=1000.0):
    """
    Apply a money management configuration to a raw trade log.
    Returns equity curve and summary stats.
    """
    capital        = starting_capital
    peak           = starting_capital
    max_dd         = 0.0
    current_risk   = mm_cfg.get("base_risk", 0.02)
    consec_losses  = 0
    throttled      = False
    throttle_peak  = capital
    trading_halted = False

    # MM4: mode-based risk
    if mm_cfg["name"] == "MM4_G2_L1":
        current_risk = mm_cfg.get("g_risk", 0.02) if mode=="G" else mm_cfg.get("l_risk", 0.01)

    equity_curve = [round(capital, 2)]
    trade_results = []

    for t in trade_log:
        if trading_halted:
            break

        # Apply risk to get dollar P&L
        risk_dollars = capital * current_risk
        pnl          = risk_dollars * t["r_multiple"]
        capital     += pnl
        capital      = max(capital, 0.01)

        if capital > peak:
            peak          = capital
            throttled     = False        # restore risk at new equity high
            consec_losses = 0
            if mm_cfg.get("throttle"):
                current_risk = mm_cfg.get("base_risk", 0.02)

        # Drawdown
        dd = (peak - capital) / peak * 100
        if dd > max_dd:
            max_dd = dd

        # Prop firm halt
        if mm_cfg.get("prop_firm") and dd >= mm_cfg.get("max_drawdown_pct", 4.0):
            trading_halted = True

        # Track win/loss streak
        if t["outcome"] == "LOSS":
            consec_losses += 1
        else:
            consec_losses = 0

        # MM5 drawdown throttle
        if mm_cfg.get("throttle") and not throttled:
            if consec_losses >= mm_cfg.get("throttle_losses", 2):
                current_risk *= mm_cfg.get("throttle_cut", 0.5)
                throttled     = True

        # MM6 after-loss reduce
        if mm_cfg.get("scale_losses") and t["outcome"] == "LOSS":
            current_risk = max(
                mm_cfg.get("loss_floor", 0.005),
                current_risk + mm_cfg.get("loss_step", -0.005)
            )
        elif mm_cfg.get("scale_losses") and t["outcome"] == "WIN":
            # Restore by half step on win
            current_risk = min(
                mm_cfg.get("base_risk", 0.02),
                current_risk + abs(mm_cfg.get("loss_step", 0.005)) * 0.5
            )

        # MM7 after-win scale
        if mm_cfg.get("scale_wins") and t["outcome"] == "WIN":
            current_risk = min(
                mm_cfg.get("win_cap", 0.03),
                current_risk + mm_cfg.get("win_step", 0.0025)
            )
        elif mm_cfg.get("scale_wins") and t["outcome"] == "LOSS":
            current_risk = max(
                mm_cfg.get("base_risk", 0.02),
                current_risk - mm_cfg.get("win_step", 0.0025)
            )

        equity_curve.append(round(capital, 2))
        trade_results.append({
            **t,
            "capital_after": round(capital, 2),
            "risk_used":     round(current_risk, 4),
            "dd_pct":        round(dd, 2),
        })

    trades_taken = len(trade_results)
    wins         = sum(1 for t in trade_results if t["outcome"] == "WIN")

    return {
        "final":              round(capital, 2),
        "peak":               round(peak, 2),
        "max_drawdown_pct":   round(max_dd, 1),
        "total_return_pct":   round((capital-starting_capital)/starting_capital*100, 1),
        "trades_taken":       trades_taken,
        "wins":               wins,
        "win_rate":           round(wins/trades_taken*100, 1) if trades_taken else 0.0,
        "trading_halted":     trading_halted,
        "equity_curve":       equity_curve,
    }


# -------------------------------------------------------------
# LEVERAGE SIMULATION
# -------------------------------------------------------------

LEVERAGE_LEVELS = [1, 2, 5, 10, 20, 50, 100, 250, 500]

def simulate_leverage(trade_log, leverage, base_risk=0.02,
                      starting_capital=1000.0):
    """
    Simulates a fixed-risk MM config at a given leverage level.

    At leverage L:
    - Position size = capital * base_risk * L
    - Liquidation occurs if price moves 1/L against entry
    - If stop distance > liquidation distance, liquidation triggers first

    Returns per-trade equity and summary stats.
    """
    capital   = starting_capital
    peak      = starting_capital
    max_dd    = 0.0
    liq_count = 0  # number of liquidations

    equity_curve  = [round(capital, 2)]
    trade_results = []

    for t in trade_log:
        risk_dollars = capital * base_risk

        # At leverage L, actual R multiple is amplified
        # BUT capped at -1.0 if liquidation triggers first
        r_raw = t["r_multiple"]

        # Liquidation check:
        # Our stop is set at 3x ATR. If that stop distance (as fraction
        # of entry price) exceeds 1/leverage, liquidation hits first.
        # We approximate: if r_raw < -(1.0/leverage * leverage) = -1.0
        # liquidation = True when r_raw * leverage < -1.0
        leveraged_r = r_raw * leverage

        if leveraged_r <= -1.0:
            # Liquidated - lose entire margin
            leveraged_r = -1.0
            liq_count  += 1

        # Dollar PnL
        pnl      = risk_dollars * leveraged_r
        capital += pnl
        capital  = max(capital, 0.01)

        if capital > peak:
            peak = capital
        dd = (peak - capital) / peak * 100
        if dd > max_dd:
            max_dd = dd

        equity_curve.append(round(capital, 2))
        trade_results.append({
            **t,
            "leverage":       leverage,
            "leveraged_r":    round(leveraged_r, 3),
            "liquidated":     leveraged_r <= -1.0,
            "capital_after":  round(capital, 2),
        })

    n    = len(trade_results)
    wins = sum(1 for t in trade_results if t["leveraged_r"] > 0)

    return {
        "leverage":           leverage,
        "final":              round(capital, 2),
        "peak":               round(peak, 2),
        "max_drawdown_pct":   round(max_dd, 1),
        "total_return_pct":   round((capital-starting_capital)/starting_capital*100, 1),
        "trades_taken":       n,
        "wins":               wins,
        "win_rate":           round(wins/n*100, 1) if n else 0.0,
        "liquidations":       liq_count,
        "liq_rate_pct":       round(liq_count/n*100, 1) if n else 0.0,
        "equity_curve":       equity_curve,
    }


def run_leverage_comparison(raw_logs, starting_capital=1000.0, base_risk=0.02):
    """
    Runs leverage simulation across all strategies and all leverage levels.
    Prints a comparison table.
    """
    print(f"\n{'='*90}")
    print(f"  LEVERAGE COMPARISON  (${starting_capital:,.0f} start, {base_risk*100:.0f}% base risk per trade)")
    print(f"  Liquidation check included - position wiped if leveraged move < -100%")
    print(f"{'='*90}")

    all_lev_results = {}

    for strat in STRATEGIES:
        key  = strat["key"]
        tlog = raw_logs.get(key, [])
        if not tlog:
            continue

        print(f"\n  -- {key} ({strat['symbol']} {strat['timeframe']}) --")
        print(f"  {'Leverage':>10} {'Trades':>7} {'Win%':>6} "
              f"{'Final$':>9} {'Return%':>8} {'MaxDD%':>7} "
              f"{'Liqs':>6} {'LiqRate':>8}")
        print(f"  {'-'*76}")

        strat_lev = {}
        for lev in LEVERAGE_LEVELS:
            r = simulate_leverage(tlog, lev,
                                  base_risk=base_risk,
                                  starting_capital=starting_capital)
            strat_lev[lev] = r

            # Safety flag
            flag = ""
            if r["liq_rate_pct"] > 20:
                flag = " WARN HIGH LIQ RISK"
            elif r["total_return_pct"] < -50:
                flag = " FAIL ACCOUNT BLOWN"
            elif r["total_return_pct"] > 0 and r["liq_rate_pct"] == 0:
                flag = " OK SAFE"

            print(f"  {str(lev)+'x':>10} {r['trades_taken']:>7} "
                  f"{r['win_rate']:>5.1f}% "
                  f"${r['final']:>8,.2f} "
                  f"{r['total_return_pct']:>7.1f}% "
                  f"{r['max_drawdown_pct']:>6.1f}% "
                  f"{r['liquidations']:>6} "
                  f"{r['liq_rate_pct']:>6.1f}%"
                  f"{flag}")

        all_lev_results[key] = strat_lev

    # Sweet spot summary
    print(f"\n{'='*90}")
    print(f"  RECOMMENDED LEVERAGE PER STRATEGY")
    print(f"  (highest return with <10% liquidation rate)")
    print(f"{'='*90}")

    for strat in STRATEGIES:
        key = strat["key"]
        if key not in all_lev_results:
            continue

        best_lev    = 1
        best_return = -999.0
        best_r      = None

        for lev, r in all_lev_results[key].items():
            if r["liq_rate_pct"] < 10 and r["total_return_pct"] > best_return:
                best_return = r["total_return_pct"]
                best_lev    = lev
                best_r      = r

        if best_r is None:
            # No leverage level met the <10% liq threshold -- use 1x as safe default
            best_lev = 1
            best_r   = all_lev_results[key].get(1, {})
            print(f"  {key:<10}: No safe leverage found (all have >10% liq rate) -- defaulting to 1x")
        else:
            print(f"  {key:<10}: {best_lev}x leverage  "
                  f"-> ${best_r.get('final',0):,.2f} ({best_return:+.1f}%)  "
                  f"MaxDD={best_r.get('max_drawdown_pct',0):.1f}%  "
                  f"Liqs={best_r.get('liquidations',0)}")

    return all_lev_results


# -------------------------------------------------------------
# MAIN
# -------------------------------------------------------------

def main():
    STARTING_CAPITAL = 1000.0

    print(f"\n{'='*70}")
    print(f"  BACKTEST V4 - MONEY MANAGEMENT COMPARISON")
    print(f"  4 locked strategies x 8 MM configs = 32 simulations")
    print(f"  Starting capital: ${STARTING_CAPITAL:,.0f}")
    print(f"{'='*70}")

    # Step 1: Generate raw trade logs for all strategies
    raw_logs = {}
    t0 = time.time()

    for strat in STRATEGIES:
        key = strat["key"]
        print(f"\n  Fetching + generating trades for {key}...")
        try:
            df = fetch_data(strat)
        except Exception as e:
            print(f"  FAILED: {e}")
            raw_logs[key] = []
            continue

        tlog = generate_trade_log(df, strat)
        raw_logs[key] = tlog
        wins = sum(1 for t in tlog if t["outcome"]=="WIN")
        n    = len(tlog)
        print(f"  {key}: {n} trades  WR={(wins/n*100 if n else 0):.1f}%  "
              f"AvgR={(sum(t['r_multiple'] for t in tlog)/max(n,1)):.3f}")

    # Step 2: Apply all MM configs to all strategies
    all_results = {}

    for strat in STRATEGIES:
        key  = strat["key"]
        mode = strat["mode"]
        tlog = raw_logs.get(key, [])

        if not tlog:
            continue

        strat_results = {}
        for mm in MM_CONFIGS:
            result = simulate_mm(tlog, mm, mode=mode,
                                 starting_capital=STARTING_CAPITAL)
            strat_results[mm["name"]] = result

        all_results[key] = strat_results

    # Step 3: Print comparison table
    print(f"\n{'='*80}")
    print(f"  MONEY MANAGEMENT COMPARISON")
    print(f"{'='*80}")

    for strat in STRATEGIES:
        key = strat["key"]
        if key not in all_results:
            continue

        print(f"\n  -- {key} ({strat['symbol']} {strat['timeframe']} "
              f"Mode={strat['mode']}) --")
        print(f"  {'MM Config':<28} {'Trades':>6} {'Win%':>6} "
              f"{'Final$':>9} {'Return%':>8} {'MaxDD%':>7} {'Halted':>7}")
        print(f"  {'-'*72}")

        for mm in MM_CONFIGS:
            r = all_results[key].get(mm["name"], {})
            halted = "YES" if r.get("trading_halted") else "no"
            print(f"  {mm['name']:<28} {r.get('trades_taken',0):>6} "
                  f"{r.get('win_rate',0.0):>5.1f}% "
                  f"${r.get('final',STARTING_CAPITAL):>8,.2f} "
                  f"{r.get('total_return_pct',0.0):>7.1f}% "
                  f"{r.get('max_drawdown_pct',0.0):>6.1f}% "
                  f"{halted:>7}")

    # Step 4: Best MM per strategy
    print(f"\n{'='*80}")
    print(f"  BEST MM CONFIG PER STRATEGY")
    print(f"{'='*80}")

    for strat in STRATEGIES:
        key = strat["key"]
        if key not in all_results:
            continue

        best_name   = ""
        best_return = -999.0

        for mm in MM_CONFIGS:
            r = all_results[key].get(mm["name"], {})
            if r.get("trading_halted"):
                continue
            ret = r.get("total_return_pct", -999.0)
            if ret > best_return:
                best_return = ret
                best_name   = mm["name"]
                best_r      = r

        if best_name:
            print(f"  {key:<10}: {best_name:<28} "
                  f"-> ${best_r['final']:,.2f} ({best_return:+.1f}%) "
                  f"MaxDD={best_r['max_drawdown_pct']:.1f}%")

    # Step 5: Cross-strategy best combo
    print(f"\n{'='*80}")
    print(f"  RECOMMENDED PRODUCTION CONFIG")
    print(f"  (highest return across all strategies without prop firm halt)")
    print(f"{'='*80}")

    combined = {}
    for mm in MM_CONFIGS:
        total_ret = 0.0
        total_dd  = 0.0
        halted    = False
        count     = 0
        for key in all_results:
            r = all_results[key].get(mm["name"], {})
            if r.get("trading_halted"):
                halted = True
            total_ret += r.get("total_return_pct", 0.0)
            total_dd  += r.get("max_drawdown_pct", 0.0)
            count     += 1
        combined[mm["name"]] = {
            "avg_return": round(total_ret/max(count,1), 1),
            "avg_dd":     round(total_dd/max(count,1), 1),
            "any_halted": halted,
        }

    for mm_name, stats in sorted(combined.items(),
                                  key=lambda x: x[1]["avg_return"], reverse=True):
        halted_flag = " WARN HALTED" if stats["any_halted"] else ""
        print(f"  {mm_name:<28}: avg_return={stats['avg_return']:+.1f}%  "
              f"avg_dd={stats['avg_dd']:.1f}%{halted_flag}")

    print(f"\n  Total time: {time.time()-t0:.1f}s")
    print(f"  Breakeven win rate: 35.7% at 1.8R")

    # Step 6: Leverage comparison
    lev_results = run_leverage_comparison(raw_logs,
                                          starting_capital=STARTING_CAPITAL,
                                          base_risk=0.02)

    # Save JSON
    lev_serializable = {
        k: {str(lev): v for lev, v in lm.items()}
        for k, lm in lev_results.items()
    }
    output = {
        "strategies":      [s["key"] for s in STRATEGIES],
        "mm_configs":      [m["name"] for m in MM_CONFIGS],
        "leverage_levels": LEVERAGE_LEVELS,
        "results":         all_results,
        "combined_mm":     combined,
        "leverage":        lev_serializable,
    }
    with open("backtest_v4_results.json","w") as f:
        json.dump(output, f, indent=2)

    print("Saved: backtest_v4_results.json")
    print(f"Total time: {time.time()-t0:.1f}s")
    print(f"  Total time: {time.time()-t0:.1f}s")


if __name__ == "__main__":
    main()
