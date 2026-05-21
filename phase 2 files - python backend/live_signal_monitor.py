"""
live_signal_monitor.py
═══════════════════════════════════════════════════════════
Live signal monitor for Trading Agent V2.

What this does:
  1. Scans all active strategies for valid entry signals
  2. Fetches your live KuCoin balance (read-only)
  3. Calculates exact position sizes for spot AND futures
     at leverage levels: 2x, 5x, 10x, 20x, 50x, 100x
  4. Records every signal as a simulated trade in trade_log.csv
  5. On subsequent runs, checks if open trades hit stop or target
  6. Calculates running P&L, win rate, R multiples, equity curve
  7. Pushes updated CSV to GitHub automatically

Run modes:
  py live_signal_monitor.py --mode scan    # check for signals now
  py live_signal_monitor.py --mode watch   # scan every 15 min
  py live_signal_monitor.py --mode report  # show trade summary
  py live_signal_monitor.py --mode update  # update open trade outcomes only

Strategies monitored:
  BTC_G  — BTCUSDT 15m  hours 7,12,14,18 UTC  Grade A+B
  BTC_L  — BTCUSDT 15m  hours 7,12,14 UTC     Grade B only
  GOLD_G — XAUUSD  1h   hours 13,14 UTC        Grade A+B
  ETH_G  — ETHUSDT 15m  hours 7,12,14,18 UTC  ON HOLD
═══════════════════════════════════════════════════════════
"""

import sys, os, json, time, argparse, warnings, requests, subprocess
from datetime import datetime, timezone, timedelta
import numpy as np
import pandas as pd
warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from indicator_engine      import compute_pavp, compute_atr
from volume_profile_engine import compute_volume_intelligence, compute_cvd_triangles
from wme_sweep_engine      import compute_wme_signal, get_wme_confidence_modifier
from kucoin_balance        import get_balance

# SSL patch
import requests as _req
_orig = _req.get
def _no_verify(url, **kw):
    kw.setdefault("verify", False)
    return _orig(url, **kw)
_req.get = _no_verify

# ─────────────────────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────────────────────

TRADE_LOG_FILE  = "trade_log.csv"
GITHUB_REPO     = "https://github.com/rizwans95/Claude-trading-agent"
RISK_PCT        = 0.02          # 2% risk per trade
LEVERAGE_LEVELS = [2, 5, 10, 20, 50, 100]
MAX_LEVERAGE_BTC= 100           # KuCoin BTC futures max

STRATEGIES = [
    {
        "key":      "BTC_G",
        "symbol":   "BTCUSDT",
        "tf":       "15",
        "hours":    [7, 12, 14, 18],
        "grades":   None,
        "mode":     "G",
        "active":   True,
        "window":   120,
        "max_bars": 60,
        "cooldown": 20,
    },
    {
        "key":      "BTC_L",
        "symbol":   "BTCUSDT",
        "tf":       "15",
        "hours":    [7, 12, 14],
        "grades":   ["B"],
        "mode":     "L",
        "active":   True,
        "window":   120,
        "max_bars": 60,
        "cooldown": 20,
    },
    {
        "key":      "GOLD_G",
        "symbol":   "XAUUSD",
        "tf":       "60",
        "hours":    [13, 14],
        "grades":   None,
        "mode":     "G",
        "active":   True,
        "window":   85,
        "max_bars": 20,
        "cooldown": 8,
    },
    {
        "key":      "ETH_G",
        "symbol":   "ETHUSDT",
        "tf":       "15",
        "hours":    [7, 12, 14, 18],
        "grades":   None,
        "mode":     "G",
        "active":   False,   # ON HOLD — failed validation
        "window":   120,
        "max_bars": 60,
        "cooldown": 20,
    },
]

# CSV columns — in this exact order
CSV_COLUMNS = [
    "signal_id", "timestamp", "utc_hour", "strategy", "symbol",
    "timeframe", "mode", "direction", "grade", "confidence",
    "in_session", "wme_signal", "cvd_signal", "in_lvn",
    "entry_price",
    "stop_loss", "stop_pct",
    "target_1", "target_2", "target_3",
    "spot_qty",
    "fut_qty_2x", "fut_margin_2x",
    "fut_qty_5x", "fut_margin_5x",
    "fut_qty_10x", "fut_margin_10x",
    "fut_qty_20x", "fut_margin_20x",
    "fut_qty_50x", "fut_margin_50x",
    "fut_qty_100x", "fut_margin_100x",
    "account_balance", "balance_source",
    "status",           # OPEN / WIN / LOSS / TIMEOUT
    "exit_price", "exit_time",
    "r_multiple", "pnl_usdt",
    "running_wins", "running_losses", "running_wr",
    "running_equity", "notes",
]


# ─────────────────────────────────────────────────────────────
# DATA FETCH
# ─────────────────────────────────────────────────────────────

def fetch_bybit(symbol, interval, bars):
    url    = "https://api.bybit.com/v5/market/kline"
    params = {
        "category": "linear" if symbol != "XAUUSD" else "spot",
        "symbol":   symbol,
        "interval": interval,
        "limit":    bars,
    }
    # Gold not on Bybit — use Yahoo for Gold
    if symbol == "XAUUSD":
        return fetch_gold_yahoo(bars)

    r = requests.get(url, params=params, timeout=15)
    r.raise_for_status()
    p = r.json()
    if p.get("retCode") != 0:
        raise RuntimeError(f"Bybit: {p.get('retMsg')}")
    chunk = p.get("result", {}).get("list", [])
    if not chunk:
        raise RuntimeError(f"No data from Bybit for {symbol}")
    df = pd.DataFrame(chunk, columns=[
        "open_time","open","high","low","close","volume","turnover"])
    df["time"] = pd.to_datetime(df["open_time"].astype(float), unit="ms")
    for c in ["open","high","low","close","volume"]:
        df[c] = df[c].astype(float)
    return (df[["time","open","high","low","close","volume"]]
            .sort_values("time").reset_index(drop=True))


def fetch_gold_yahoo(bars=90):
    try:
        import yfinance as yf
        try:
            from curl_cffi.requests import Session
            session = Session(verify=False, impersonate="chrome110")
            ticker  = yf.Ticker("GC=F", session=session)
        except ImportError:
            ticker = yf.Ticker("GC=F")
        df = ticker.history(period="60d", interval="1h", auto_adjust=True)
        if df is None or df.empty:
            raise RuntimeError("No Gold data")
        df = df.reset_index()
        tc = "Datetime" if "Datetime" in df.columns else "Date"
        df = df.rename(columns={tc:"time","Open":"open","High":"high",
                                 "Low":"low","Close":"close","Volume":"volume"})
        df["time"] = pd.to_datetime(df["time"]).dt.tz_localize(None)
        return (df[["time","open","high","low","close","volume"]]
                .dropna().sort_values("time").tail(bars).reset_index(drop=True))
    except Exception as e:
        raise RuntimeError(f"Gold fetch failed: {e}")


def fetch_current_price(symbol):
    """Get the latest price for outcome checking."""
    if symbol == "XAUUSD":
        try:
            df = fetch_gold_yahoo(5)
            return float(df["close"].iloc[-1])
        except Exception:
            return None
    try:
        url    = "https://api.bybit.com/v5/market/tickers"
        params = {"category":"linear","symbol":symbol}
        r = requests.get(url, params=params, timeout=10)
        data = r.json()
        return float(data["result"]["list"][0]["lastPrice"])
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────
# POSITION SIZE CALCULATOR
# ─────────────────────────────────────────────────────────────

def calculate_positions(entry_price, stop_loss, balance_usdt, symbol):
    """
    Calculate spot and futures position sizes at all leverage levels.

    Risk per trade = 2% of balance
    Stop distance  = |entry - stop| / entry (as %)
    Position size  = risk_dollars / stop_distance_dollars
    """
    risk_dollars  = balance_usdt * RISK_PCT
    stop_dist_pct = abs(entry_price - stop_loss) / entry_price
    stop_dist_usd = entry_price * stop_dist_pct

    if stop_dist_usd == 0:
        stop_dist_usd = entry_price * 0.02  # fallback 2%

    # Spot: how many units to buy such that a stop hit loses risk_dollars
    spot_qty = round(risk_dollars / stop_dist_usd, 6)

    positions = {
        "spot_qty":   spot_qty,
        "stop_pct":   round(stop_dist_pct * 100, 3),
    }

    # Futures at each leverage level
    for lev in LEVERAGE_LEVELS:
        # At leverage L, margin needed = position_value / L
        # Position value = spot_qty * entry_price
        position_value = spot_qty * entry_price
        margin         = round(position_value / lev, 2)
        qty            = round(spot_qty, 6)

        positions[f"fut_qty_{lev}x"]    = qty
        positions[f"fut_margin_{lev}x"] = margin

    return positions


# ─────────────────────────────────────────────────────────────
# SIGNAL SCANNER
# ─────────────────────────────────────────────────────────────

def scan_strategy(strat, balance_info):
    """Scan one strategy for a valid entry signal."""
    win   = strat["window"]
    bars  = win + 5

    try:
        df = fetch_bybit(strat["symbol"], strat["tf"], bars)
    except Exception as e:
        return {"error": str(e), "strategy": strat["key"]}

    utc_now    = datetime.now(timezone.utc)
    utc_hour   = utc_now.hour
    in_session = utc_hour in strat["hours"]

    try:
        pavp     = compute_pavp(df)
        atr_data = compute_atr(df)
        vi       = compute_volume_intelligence(df, pavp)
        swing    = vi.get("swing_profiles", {})
        wme      = compute_wme_signal(df, bar_tolerance=0)
        curr     = wme["current"]
        tri      = compute_cvd_triangles(df, cost_rank_thresh=75.0)
    except Exception as e:
        return {"error": f"Indicator error: {e}", "strategy": strat["key"]}

    wme_long  = curr.get("long_entry",  False)
    wme_short = curr.get("short_entry", False)
    has_wme   = wme_long or wme_short
    direction = "LONG" if wme_long else "SHORT" if wme_short else "NONE"

    cvd_primary = tri.get("primary_signal", "NONE")
    in_lvn      = swing.get("in_lvn", False)

    # Confidence
    hvn_z  = swing.get("hvn_zones", [])
    cp     = float(df["close"].iloc[-1])
    in_hvn = any(z["price_low"]<=cp<=z["price_high"] for z in hvn_z)
    wm     = get_wme_confidence_modifier(wme, in_lvn, in_hvn, len(df)-1)
    spd    = abs(curr.get("speed", 0.0))
    conf   = round(min(100, max(0, 55+min(spd*15,20)+wm["modifier"])), 1)
    grade  = "A" if conf>=75 else "B" if conf>=60 else "C"

    # Valid trade check
    grade_ok = (strat["grades"] is None or grade in strat["grades"])
    valid    = has_wme and in_session and grade != "C" and grade_ok

    # Exit targets
    et = vi.get("exit_targets_long" if direction=="LONG"
                else "exit_targets_short", [])
    targets = [t["price"] for t in et[:3]]
    while len(targets) < 3:
        targets.append(None)

    # Stop loss
    atr_val = atr_data.get("atr_value", cp * 0.002)
    if direction == "LONG":
        stop = round(cp - atr_val * 3.0, 4)
    elif direction == "SHORT":
        stop = round(cp + atr_val * 3.0, 4)
    else:
        stop = None

    # Position sizes
    positions = {}
    if valid and stop:
        positions = calculate_positions(
            cp, stop,
            balance_info["balance_usdt"],
            strat["symbol"]
        )

    return {
        "strategy":     strat["key"],
        "symbol":       strat["symbol"],
        "timeframe":    strat["tf"] + ("m" if strat["tf"] != "60" else "h"),
        "mode":         strat["mode"],
        "timestamp":    utc_now.isoformat(),
        "utc_hour":     utc_hour,
        "in_session":   in_session,
        "has_signal":   has_wme,
        "direction":    direction,
        "confidence":   conf,
        "grade":        grade,
        "valid":        valid,
        "wme_signal":   has_wme,
        "cvd_signal":   cvd_primary,
        "in_lvn":       in_lvn,
        "entry_price":  round(cp, 4),
        "stop_loss":    stop,
        "targets":      targets,
        "positions":    positions,
        "balance":      balance_info["balance_usdt"],
        "balance_src":  balance_info["source"],
    }


# ─────────────────────────────────────────────────────────────
# TRADE LOG
# ─────────────────────────────────────────────────────────────

def load_log():
    if os.path.exists(TRADE_LOG_FILE):
        try:
            df = pd.read_csv(TRADE_LOG_FILE)
            return df
        except Exception:
            pass
    return pd.DataFrame(columns=CSV_COLUMNS)


def save_log(df):
    df.to_csv(TRADE_LOG_FILE, index=False, float_format="%.4f")


def next_signal_id(df):
    if len(df) == 0:
        return "SIG-001"
    last = df["signal_id"].iloc[-1]
    try:
        n = int(last.split("-")[1]) + 1
        return f"SIG-{n:03d}"
    except Exception:
        return f"SIG-{len(df)+1:03d}"


def running_stats(df):
    """Calculate running win rate and equity from closed trades."""
    closed = df[df["status"].isin(["WIN","LOSS"])]
    wins   = (closed["status"] == "WIN").sum()
    losses = (closed["status"] == "LOSS").sum()
    total  = wins + losses
    wr     = round(wins/total*100, 1) if total else 0.0

    # Equity curve (starting at 1000)
    equity = 1000.0
    for _, row in closed.iterrows():
        try:
            r = float(row["r_multiple"])
            equity += equity * RISK_PCT * r
            equity  = max(equity, 0.01)
        except Exception:
            pass

    return int(wins), int(losses), wr, round(equity, 2)


def record_signal(sig, df):
    """Add a new signal as an OPEN trade to the log."""
    pos  = sig.get("positions", {})
    targets = sig.get("targets", [None, None, None])

    wins, losses, wr, equity = running_stats(df)
    sig_id = next_signal_id(df)

    row = {
        "signal_id":       sig_id,
        "timestamp":       sig["timestamp"],
        "utc_hour":        sig["utc_hour"],
        "strategy":        sig["strategy"],
        "symbol":          sig["symbol"],
        "timeframe":       sig["timeframe"],
        "mode":            sig["mode"],
        "direction":       sig["direction"],
        "grade":           sig["grade"],
        "confidence":      sig["confidence"],
        "in_session":      sig["in_session"],
        "wme_signal":      sig["wme_signal"],
        "cvd_signal":      sig["cvd_signal"],
        "in_lvn":          sig["in_lvn"],
        "entry_price":     sig["entry_price"],
        "stop_loss":       sig["stop_loss"],
        "stop_pct":        pos.get("stop_pct", ""),
        "target_1":        targets[0],
        "target_2":        targets[1],
        "target_3":        targets[2],
        "spot_qty":        pos.get("spot_qty", ""),
        "fut_qty_2x":      pos.get("fut_qty_2x", ""),
        "fut_margin_2x":   pos.get("fut_margin_2x", ""),
        "fut_qty_5x":      pos.get("fut_qty_5x", ""),
        "fut_margin_5x":   pos.get("fut_margin_5x", ""),
        "fut_qty_10x":     pos.get("fut_qty_10x", ""),
        "fut_margin_10x":  pos.get("fut_margin_10x", ""),
        "fut_qty_20x":     pos.get("fut_qty_20x", ""),
        "fut_margin_20x":  pos.get("fut_margin_20x", ""),
        "fut_qty_50x":     pos.get("fut_qty_50x", ""),
        "fut_margin_50x":  pos.get("fut_margin_50x", ""),
        "fut_qty_100x":    pos.get("fut_qty_100x", ""),
        "fut_margin_100x": pos.get("fut_margin_100x", ""),
        "account_balance": sig["balance"],
        "balance_source":  sig["balance_src"],
        "status":          "OPEN",
        "exit_price":      "",
        "exit_time":       "",
        "r_multiple":      "",
        "pnl_usdt":        "",
        "running_wins":    wins,
        "running_losses":  losses,
        "running_wr":      wr,
        "running_equity":  equity,
        "notes":           "",
    }

    new_row = pd.DataFrame([row])
    df      = pd.concat([df, new_row], ignore_index=True)
    return df


def update_open_trades(df):
    """
    Check all OPEN trades and update their status if
    stop or target has been hit.
    """
    updated = 0
    open_trades = df[df["status"] == "OPEN"].copy()

    for idx, row in open_trades.iterrows():
        try:
            symbol    = row["symbol"]
            direction = row["direction"]
            entry     = float(row["entry_price"])
            stop      = float(row["stop_loss"])

            # Get targets
            targets = []
            for t_col in ["target_1","target_2","target_3"]:
                try:
                    v = row.get(t_col)
                    if v and str(v) not in ("","nan","None"):
                        targets.append(float(v))
                except Exception:
                    pass

            current_price = fetch_current_price(symbol)
            if not current_price:
                continue

            stop_dist = abs(entry - stop)
            if stop_dist == 0:
                stop_dist = entry * 0.02

            # Check stop
            if direction == "LONG"  and current_price <= stop:
                status    = "LOSS"
                r_mult    = -1.0
                exit_px   = stop
            elif direction == "SHORT" and current_price >= stop:
                status    = "LOSS"
                r_mult    = -1.0
                exit_px   = stop
            # Check nearest target
            elif targets and direction == "LONG" and current_price >= targets[0]:
                status  = "WIN"
                exit_px = targets[0]
                r_mult  = round((exit_px - entry) / stop_dist, 3)
            elif targets and direction == "SHORT" and current_price <= targets[0]:
                status  = "WIN"
                exit_px = targets[0]
                r_mult  = round((entry - exit_px) / stop_dist, 3)
            else:
                # Still open — check age
                try:
                    entry_ts = pd.Timestamp(row["timestamp"]).replace(tzinfo=timezone.utc)
                    age_h    = (datetime.now(timezone.utc) - entry_ts).total_seconds() / 3600
                    if age_h > 72:
                        status  = "TIMEOUT"
                        exit_px = current_price
                        r_mult  = round((current_price-entry if direction=="LONG"
                                         else entry-current_price) / stop_dist, 3)
                    else:
                        continue
                except Exception:
                    continue

            # Calculate P&L
            try:
                bal   = float(row["account_balance"])
                pnl   = round(bal * RISK_PCT * r_mult, 2)
            except Exception:
                pnl = ""

            df.loc[idx, "status"]     = status
            df.loc[idx, "exit_price"] = round(exit_px, 4)
            df.loc[idx, "exit_time"]  = datetime.now(timezone.utc).isoformat()
            df.loc[idx, "r_multiple"] = r_mult
            df.loc[idx, "pnl_usdt"]   = pnl
            updated += 1
            time.sleep(0.2)

        except Exception:
            continue

    # Recalculate running stats for all rows
    if updated > 0:
        for i in range(len(df)):
            wins, losses, wr, equity = running_stats(df.iloc[:i+1])
            df.loc[df.index[i], "running_wins"]   = wins
            df.loc[df.index[i], "running_losses"] = losses
            df.loc[df.index[i], "running_wr"]     = wr
            df.loc[df.index[i], "running_equity"] = equity

    return df, updated


# ─────────────────────────────────────────────────────────────
# GITHUB PUSH
# ─────────────────────────────────────────────────────────────

def push_to_github(message="Update trade log"):
    """Push trade_log.csv to GitHub via git."""
    try:
        subprocess.run(["git", "add", TRADE_LOG_FILE],
                       check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", message],
                       check=True, capture_output=True)
        result = subprocess.run(["git", "push"],
                                check=True, capture_output=True)
        print(f"  Pushed to GitHub: {GITHUB_REPO}")
        return True
    except subprocess.CalledProcessError as e:
        print(f"  GitHub push failed: {e.stderr.decode()[:200]}")
        return False


# ─────────────────────────────────────────────────────────────
# DISPLAY
# ─────────────────────────────────────────────────────────────

def print_signal(sig):
    """Print a signal with position sizing table."""
    pos  = sig.get("positions", {})
    bal  = sig["balance"]
    ep   = sig["entry_price"]
    sl   = sig["stop_loss"]
    tgts = sig.get("targets", [])

    print(f"\n  {'='*60}")
    print(f"  SIGNAL: {sig['strategy']}  {sig['direction']}  "
          f"Grade {sig['grade']}  Conf={sig['confidence']}%")
    print(f"  {'='*60}")
    print(f"  Symbol:      {sig['symbol']}")
    print(f"  Entry:       ${ep:,.4f}")
    print(f"  Stop:        ${sl:,.4f}  ({pos.get('stop_pct','?')}% away)")
    for i, t in enumerate(tgts[:3], 1):
        if t:
            print(f"  Target {i}:     ${t:,.4f}")
    print(f"  CVD signal:  {sig['cvd_signal']}")
    print(f"  In LVN:      {sig['in_lvn']}")
    print(f"  Balance:     ${bal:,.2f} USDT  [{sig['balance_src']}]")
    print(f"  Risk (2%):   ${bal*RISK_PCT:,.2f} USDT")

    print(f"\n  POSITION SIZES")
    print(f"  {'Type':<18} {'Qty':>12} {'Margin/Cost':>14}")
    print(f"  {'-'*46}")
    print(f"  {'Spot':<18} {pos.get('spot_qty',0):>12.6f} "
          f"  ${pos.get('spot_qty',0)*ep:>12,.2f}")
    for lev in LEVERAGE_LEVELS:
        qty    = pos.get(f"fut_qty_{lev}x",    0)
        margin = pos.get(f"fut_margin_{lev}x", 0)
        liq_dist = round(100/lev, 1)
        flag = " !!HIGH LIQ" if lev >= 50 else ""
        print(f"  {f'Futures {lev}x':<18} {qty:>12.6f}  "
              f"  ${margin:>12,.2f}  (liq ~{liq_dist}%){flag}")


def print_report(df):
    """Print summary of all trades."""
    print(f"\n{'='*70}")
    print(f"  TRADE LOG REPORT")
    print(f"  File: {TRADE_LOG_FILE}  |  Repo: {GITHUB_REPO}")
    print(f"{'='*70}")

    if len(df) == 0:
        print("  No trades logged yet.")
        return

    closed = df[df["status"].isin(["WIN","LOSS","TIMEOUT"])]
    open_t = df[df["status"] == "OPEN"]
    wins   = (closed["status"] == "WIN").sum()
    losses = (closed["status"] == "LOSS").sum()
    total  = wins + losses
    wr     = round(wins/total*100,1) if total else 0.0

    try:
        avg_r = round(pd.to_numeric(closed["r_multiple"],errors="coerce").mean(), 3)
        total_pnl = round(pd.to_numeric(closed["pnl_usdt"],errors="coerce").sum(), 2)
    except Exception:
        avg_r = 0.0; total_pnl = 0.0

    print(f"\n  Total signals:  {len(df)}")
    print(f"  Open trades:    {len(open_t)}")
    print(f"  Closed trades:  {len(closed)}")
    print(f"  Win rate:       {wr}%  ({wins}W / {losses}L)")
    print(f"  Avg R:          {avg_r:+.3f}")
    print(f"  Total P&L:      ${total_pnl:+,.2f} USDT")

    if len(df) > 0:
        last_equity = df["running_equity"].iloc[-1]
        try:
            print(f"  Running equity: ${float(last_equity):,.2f} USDT")
        except Exception:
            pass

    print(f"\n  RECENT TRADES (last 10):")
    print(f"  {'ID':<10} {'Time':<18} {'Strat':<8} {'Dir':<6} "
          f"{'Entry':>10} {'Status':<8} {'R':>6} {'P&L':>8}")
    print(f"  {'-'*72}")

    for _, row in df.tail(10).iterrows():
        ts  = str(row["timestamp"])[:16].replace("T"," ")
        r   = str(row.get("r_multiple",""))[:6]
        pnl = str(row.get("pnl_usdt",""))[:8]
        ep  = row.get("entry_price","")
        try: ep = f"${float(ep):,.2f}"
        except Exception: ep = str(ep)[:10]
        print(f"  {row['signal_id']:<10} {ts:<18} {row['strategy']:<8} "
              f"{row['direction']:<6} {ep:>10} {row['status']:<8} "
              f"{r:>6} {pnl:>8}")

    # Per-strategy breakdown
    print(f"\n  BY STRATEGY:")
    for strat in df["strategy"].unique():
        sub     = df[df["strategy"]==strat]
        cl      = sub[sub["status"].isin(["WIN","LOSS"])]
        sw      = (cl["status"]=="WIN").sum()
        sn      = len(cl)
        swr     = round(sw/sn*100,1) if sn else 0.0
        print(f"    {strat:<10}: {len(sub)} signals  {swr}% WR  ({sw}W/{sn-sw}L)")


# ─────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────

def scan_once():
    print(f"\n{'='*60}")
    print(f"  TRADING AGENT V2 — LIVE SIGNAL MONITOR")
    print(f"  {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"{'='*60}")

    # Fetch balance
    print("\n  Fetching account balance...")
    balance_info = get_balance()
    print(f"  Balance: ${balance_info['balance_usdt']:,.2f} USDT "
          f"[{balance_info['source']}]")

    # Load log
    df = load_log()

    # Update open trades first
    print("\n  Checking open trades...")
    df, updated = update_open_trades(df)
    if updated:
        print(f"  Updated {updated} trade(s).")
    else:
        print(f"  No updates ({(df['status']=='OPEN').sum()} open).")

    # Scan for new signals
    print("\n  Scanning strategies...")
    new_signals = []

    for strat in STRATEGIES:
        if not strat.get("active", True):
            print(f"  {strat['key']:<10}: ON HOLD")
            continue

        sig = scan_strategy(strat, balance_info)

        if "error" in sig:
            print(f"  {strat['key']:<10}: ERROR — {sig['error']}")
            continue

        in_s   = "IN SESSION" if sig["in_session"] else "out of session"
        signal = sig["direction"] if sig["has_signal"] else "no signal"
        valid  = " ** VALID TRADE **" if sig["valid"] else ""

        print(f"  {strat['key']:<10}: {in_s:<14}  {signal:<6}  "
              f"conf={sig['confidence']:>5.1f}%  grade={sig['grade']}  "
              f"WME={sig['wme_signal']}  CVD={sig['cvd_signal']}{valid}")

        if sig["valid"]:
            print_signal(sig)
            df = record_signal(sig, df)
            new_signals.append(sig)

    # Save and push
    save_log(df)

    if new_signals or updated:
        msg = f"Signal monitor update: {len(new_signals)} new, {updated} resolved"
        print(f"\n  Pushing to GitHub...")
        push_to_github(msg)
    else:
        print(f"\n  No changes — skipping GitHub push.")

    print(f"\n  Log: {TRADE_LOG_FILE}  ({len(df)} entries)")

    return new_signals


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", default="scan",
                        choices=["scan","watch","report","update"])
    args = parser.parse_args()

    if args.mode == "report":
        df = load_log()
        print_report(df)

    elif args.mode == "update":
        print("  Updating open trade outcomes...")
        df = load_log()
        df, updated = update_open_trades(df)
        save_log(df)
        print(f"  Updated {updated} trade(s).")
        if updated:
            push_to_github(f"Outcome update: {updated} trades resolved")

    elif args.mode == "scan":
        scan_once()

    elif args.mode == "watch":
        print(f"\n  Watch mode — scanning every 15 minutes.")
        print(f"  Press Ctrl+C to stop.\n")
        while True:
            try:
                scan_once()
                print(f"\n  Next scan in 15 minutes...")
                time.sleep(900)
            except KeyboardInterrupt:
                print("\n  Stopped.")
                break
            except Exception as e:
                print(f"\n  Error: {e}. Retrying in 5 minutes...")
                time.sleep(300)


if __name__ == "__main__":
    main()
