"""
gold_signal_monitor.py
═══════════════════════════════════════════════════════════════
GOLD Live Signal Monitor — XAUUSD A-15m FVG Strategy

Fetches live XAUUSD 15m data from Yahoo Finance every 15 min.
Detects bullish FVG + EMA stack signals.
Paper trades: tracks SL/TP hits on live price feed.
Sends Telegram notifications identical to BTC system.
Logs all signals and outcomes to gold_trade_log.csv.

Config:
  Symbol:  XAUUSD (GOLD)
  TF:      15m
  Hours:   2, 4, 9, 11, 16, 17, 23 UTC
  Risk:    1.5% (paper)
  Max TP:  5R from daily pivot high
  Max hold: 32 bars (8 hours)

Run:
  python3 gold_signal_monitor.py          # scan once
  python3 gold_signal_monitor.py --watch  # loop every 15 min

Requires: pip install yfinance pandas requests --break-system-packages
═══════════════════════════════════════════════════════════════
"""

import os, sys, json, time, argparse, warnings
from datetime import datetime, timezone, timedelta
import pandas as pd
import numpy as np
warnings.filterwarnings("ignore")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    import yfinance as yf
except ImportError:
    os.system("pip install yfinance --break-system-packages -q")
    import yfinance as yf

try:
    from telegram_notify import send
except ImportError:
    def send(msg, config=None): print(f"[TELEGRAM] {msg}")

# ─────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────

SYMBOL        = "GC=F"          # Yahoo Finance GOLD futures (primary working ticker)
SYMBOL_LABEL  = "XAUUSD"
TF            = "15m"
ALLOWED_HOURS = [2, 4, 9, 11, 16, 17, 23]
EMA_LENGTHS   = [5, 9, 15, 21]
MAX_WAIT      = 15              # bars to wait for retest
MAX_HOLD      = 32              # max bars to hold (8 hours)
MAX_TP_R      = 5.0
PAPER_RISK    = 0.015           # 1.5% paper risk
PAPER_BALANCE = 10000.0         # hypothetical balance

LOG_FILE      = "gold_trade_log.csv"
STATE_FILE    = "gold_monitor_state.json"

LOG_COLUMNS = [
    "signal_id","timestamp","utc_hour","symbol","direction",
    "entry_price","stop_loss","take_profit","fvg_top","fvg_bottom",
    "r_to_tp","status","exit_price","exit_time","r_multiple",
    "pnl_usd","bars_held","notes"
]

# ─────────────────────────────────────────────────────────────
# DATA FETCHER
# ─────────────────────────────────────────────────────────────

def fetch_gold_15m(bars=200):
    """Fetch last N 15m bars of XAUUSD from Yahoo Finance."""
    # GC=F (gold futures) is the working ticker — try it first
    tickers = ["GC=F", "XAUUSD=X", "GLD"]
    df = None
    used_ticker = None
    for ticker in tickers:
        try:
            _df = yf.download(ticker, period="5d", interval="15m",
                              progress=False, auto_adjust=True)
            if _df is not None and len(_df) >= 50:
                df = _df
                used_ticker = ticker
                break
        except Exception as e:
            print(f"  [WARN] Ticker {ticker} failed: {e}")
            continue
    try:
        if df is None or len(df) < 50:
            print(f"  [ERROR] All gold tickers failed or returned insufficient data")
            return None

        # Flatten MultiIndex columns (yfinance returns tuples like ('Close','GC=F'))
        df = df.reset_index()
        flat_cols = []
        for col in df.columns:
            if isinstance(col, tuple):
                flat_cols.append(col[0].lower())
            else:
                flat_cols.append(str(col).lower())
        df.columns = flat_cols

        # Rename datetime/date column to time
        for col in ["datetime","date"]:
            if col in df.columns:
                df = df.rename(columns={col:"time"})

        df["time"] = pd.to_datetime(df["time"], utc=True)
        df = df[["time","open","high","low","close","volume"]].dropna()
        df = df.drop_duplicates("time").sort_values("time").reset_index(drop=True)
        return df.tail(bars).reset_index(drop=True)
    except Exception as e:
        print(f"  [ERROR] fetch_gold_15m processing: {e}")
        return None


def get_current_price():
    """Get current XAUUSD spot price."""
    # Try fast_info first for each ticker
    for t in ["GC=F", "XAUUSD=X"]:
        try:
            info = yf.Ticker(t).fast_info
            price = float(info.last_price)
            if price and price > 100:  # sanity check — gold > $100
                return price
        except Exception:
            continue
    # Fallback — use last close from OHLCV data
    df = fetch_gold_15m(5)
    if df is not None and len(df) > 0:
        return float(df["close"].iloc[-1])
    return None

# ─────────────────────────────────────────────────────────────
# INDICATORS
# ─────────────────────────────────────────────────────────────

def compute_emas(df):
    for l in EMA_LENGTHS:
        df[f"ema{l}"] = df["close"].ewm(span=l, adjust=False).mean()
    return df


def ema_bullish(df, i):
    if i < 1: return False
    e5,e9,e15,e21 = df["ema5"].iloc[i],df["ema9"].iloc[i],df["ema15"].iloc[i],df["ema21"].iloc[i]
    p5,p9,p15,p21 = df["ema5"].iloc[i-1],df["ema9"].iloc[i-1],df["ema15"].iloc[i-1],df["ema21"].iloc[i-1]
    return (e5>e9>e15>e21) and (e5>p5) and (e9>p9) and (e15>p15) and (e21>p21)


def get_daily_tp(df, bar_index, entry_price, stop_loss):
    """Get nearest daily pivot high above entry."""
    stop_dist = abs(entry_price - stop_loss)
    max_tp    = entry_price + stop_dist * MAX_TP_R
    lookback  = max(0, bar_index - 50*4)
    sl = df.iloc[lookback:bar_index].copy()
    if len(sl) < 10:
        return round(entry_price + stop_dist * 3.0, 2)
    sl = sl.set_index("time")
    dh = sl["high"].resample("1D").max().dropna()
    dc = sl["close"].resample("1D").last().dropna()
    if len(dh) < 3:
        return round(min(entry_price + stop_dist * 3.0, max_tp), 2)
    pivots = [(float(dh.iloc[j]), float(dc.iloc[j]))
              for j in range(1, len(dh)-1)
              if dh.iloc[j]>dh.iloc[j-1] and dh.iloc[j]>dh.iloc[j+1]]
    above = sorted([(h,c) for h,c in pivots if h>entry_price], key=lambda x:x[0])
    if not above:
        return round(min(entry_price + stop_dist * 3.0, max_tp), 2)
    tp = above[0][1]
    return round(min(tp, max_tp) if tp > entry_price else entry_price + stop_dist * 3.0, 2)

# ─────────────────────────────────────────────────────────────
# TRADE LOG
# ─────────────────────────────────────────────────────────────

def load_log():
    if os.path.exists(LOG_FILE):
        # Read all as object to avoid dtype inference issues
        df = pd.read_csv(LOG_FILE, dtype=object)
        for col in LOG_COLUMNS:
            if col not in df.columns:
                df[col] = None
        # Convert numeric columns explicitly — leave string cols as-is
        for col in ["entry_price","stop_loss","take_profit","fvg_top","fvg_bottom",
                    "r_to_tp","r_multiple","pnl_usd","exit_price","utc_hour","bars_held"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        return df
    return pd.DataFrame(columns=LOG_COLUMNS)


def save_log(df):
    df.to_csv(LOG_FILE, index=False)


def next_signal_id(df):
    if len(df) == 0: return "GOLD-0001"
    last = df["signal_id"].dropna().tolist()
    if not last: return "GOLD-0001"
    try:
        n = int(last[-1].split("-")[-1]) + 1
    except Exception:
        n = len(df) + 1
    return f"GOLD-{n:04d}"

# ─────────────────────────────────────────────────────────────
# STATE (persist FVG between runs)
# ─────────────────────────────────────────────────────────────

def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return {"monitor_state":"SCAN","fvg_top":None,"fvg_bottom":None,
            "fvg_timestamp":None,"open_trade":None}


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2, default=str)

# ─────────────────────────────────────────────────────────────
# TELEGRAM — GOLD FLAVOURED
# ─────────────────────────────────────────────────────────────

def load_config():
    if os.path.exists("config.json"):
        with open("config.json") as f:
            return json.load(f)
    return {}


def tg_signal_detected(entry, stop, tp, fvg_top, fvg_bottom, hour, config):
    stop_dist = abs(entry - stop)
    r_to_tp   = round((tp - entry) / stop_dist, 2) if stop_dist > 0 else 0
    msg = (
        "🏅 <b>GOLD SIGNAL DETECTED</b>\n"
        "<b>LONG XAUUSD</b> | FVG Retest\n"
        "Entry: <b>$" + f"{entry:,.2f}" + "</b> | Stop: $" + f"{stop:,.2f}" + "\n"
        "TP: $" + f"{tp:,.2f}" + f" ({r_to_tp:.1f}R)\n"
        "FVG Zone: $" + f"{fvg_bottom:,.2f}" + " → $" + f"{fvg_top:,.2f}" + "\n"
        "Hour: " + str(hour) + ":00 UTC | Paper trading active"
    )
    send(msg, config)


def tg_trade_closed(signal_id, entry, exit_price, r, pnl, config):
    emoji  = "🏆" if r > 0 else "🔴"
    result = "WIN" if r > 0 else "LOSS"
    msg = (
        emoji + " <b>GOLD PAPER TRADE CLOSED — " + result + "</b>\n"
        "LONG XAUUSD | " + signal_id + "\n"
        "Entry: $" + f"{entry:,.2f}" + " → Exit: $" + f"{exit_price:,.2f}" + "\n"
        "Result: <b>" + f"{r:+.3f}" + "R</b> | PnL: <b>$" + f"{pnl:+.2f}" + "</b>"
    )
    send(msg, config)


def tg_no_signal(hour, reason, config):
    msg = (
        "📊 <b>GOLD SESSION " + f"{hour:02d}" + ":00 UTC</b>\n"
        "Signal: NO TRADE\n"
        "Reason: " + reason + "\n"
        "Next session: " + str(next_allowed_hour(hour)) + ":00 UTC"
    )
    send(msg, config)


def tg_system_started(config):
    hours_str = ", ".join(str(h) for h in ALLOWED_HOURS)
    msg = (
        "🟡 <b>GOLD Signal Monitor Started</b>\n"
        "Strategy: XAUUSD 15m FVG Retest\n"
        "Session hours: " + hours_str + " UTC\n"
        "Paper trading: ON | Risk: 1.5%"
    )
    send(msg, config)


def next_allowed_hour(current_hour):
    future = [h for h in ALLOWED_HOURS if h > current_hour]
    return future[0] if future else ALLOWED_HOURS[0]

# ─────────────────────────────────────────────────────────────
# UPDATE OPEN TRADES
# ─────────────────────────────────────────────────────────────

def update_open_trades(df, config):
    """Check if any open paper trades hit SL or TP."""
    open_trades = df[df["status"] == "OPEN"]
    if len(open_trades) == 0:
        return df, 0

    current_price = get_current_price()
    if current_price is None:
        return df, 0

    updated = 0
    for idx, trade in open_trades.iterrows():
        try:
            entry     = float(trade["entry_price"])
            sl        = float(trade["stop_loss"])
            tp        = float(trade["take_profit"])
            bars_held = int(float(trade["bars_held"] or 0)) + 1
        except Exception as e:
            print(f"  [WARN] Could not parse trade {trade.get('signal_id')}: {e}")
            continue

        df.at[idx, "bars_held"] = float(bars_held)

        # Check SL hit
        if current_price <= sl:
            r   = -1.0
            pnl = round(PAPER_BALANCE * PAPER_RISK * r, 2)
            df.at[idx, "status"]     = "LOSS"
            df.at[idx, "exit_price"] = sl
            df.at[idx, "exit_time"] = str(datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"))
            df.at[idx, "r_multiple"] = r
            df.at[idx, "pnl_usd"] = pnl
            df.at[idx, "notes"] = str(f"SL hit @ {current_price:.2f}")
            tg_trade_closed(trade["signal_id"], entry, sl, r, pnl, config)
            print(f"  [LOSS] {trade['signal_id']} SL hit @ {current_price:.2f}")
            updated += 1

        # Check TP hit
        elif current_price >= tp:
            sd  = abs(entry - sl)
            r   = round((tp - entry) / sd, 3) if sd > 0 else 0
            pnl = round(PAPER_BALANCE * PAPER_RISK * r, 2)
            df.at[idx, "status"]     = "WIN"
            df.at[idx, "exit_price"] = tp
            df.at[idx, "exit_time"] = str(datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"))
            df.at[idx, "r_multiple"] = r
            df.at[idx, "pnl_usd"] = pnl
            df.at[idx, "notes"] = str(f"TP hit @ {current_price:.2f}")
            tg_trade_closed(trade["signal_id"], entry, tp, r, pnl, config)
            print(f"  [WIN]  {trade['signal_id']} TP hit @ {current_price:.2f} ({r:+.2f}R)")
            updated += 1

        # Max hold timeout
        elif bars_held >= MAX_HOLD:
            sd  = abs(entry - sl)
            r   = round((current_price - entry) / sd, 3) if sd > 0 else 0
            pnl = round(PAPER_BALANCE * PAPER_RISK * r, 2)
            status = "WIN" if r > 0 else "LOSS"
            df.at[idx, "status"] = str(status)
            df.at[idx, "exit_price"] = current_price
            df.at[idx, "exit_time"] = str(datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"))
            df.at[idx, "r_multiple"] = r
            df.at[idx, "pnl_usd"] = pnl
            df.at[idx, "notes"] = str(f"Max hold timeout @ {current_price:.2f}")
            tg_trade_closed(trade["signal_id"], entry, current_price, r, pnl, config)
            print(f"  [TIMEOUT] {trade['signal_id']} closed @ {current_price:.2f} ({r:+.2f}R)")
            updated += 1

    return df, updated

# ─────────────────────────────────────────────────────────────
# SIGNAL SCANNER
# ─────────────────────────────────────────────────────────────

def scan_for_signal(df_price, monitor_state, config):
    """
    State machine: SCAN → WAITING → (fire trade)
    Persists FVG across scans via monitor_state.
    """
    df = compute_emas(df_price.copy())
    n  = len(df)
    if n < 10: return monitor_state, None

    now_utc = datetime.now(timezone.utc)
    current_hour = now_utc.hour
    state = monitor_state.get("monitor_state", "SCAN")

    # ── If in WAITING state ──
    if state == "WAITING":
        fvg_top      = monitor_state.get("fvg_top")
        fvg_bottom   = monitor_state.get("fvg_bottom")
        fvg_timestamp= monitor_state.get("fvg_timestamp")

        if fvg_top is None or fvg_bottom is None:
            monitor_state["monitor_state"] = "SCAN"
            return monitor_state, None

        # Check timeout — how many 15m bars since FVG
        if fvg_timestamp:
            fvg_dt  = pd.Timestamp(fvg_timestamp, tz="UTC")
            bars_elapsed = int((now_utc - fvg_dt).total_seconds() / 900)
            if bars_elapsed > MAX_WAIT:
                print(f"  FVG timed out after {bars_elapsed} bars")
                monitor_state["monitor_state"] = "SCAN"
                monitor_state["fvg_top"] = monitor_state["fvg_bottom"] = monitor_state["fvg_timestamp"] = None
                return monitor_state, None

        # EMA must still be bullish
        if not ema_bullish(df, n-1):
            print(f"  EMA stack broken — resetting FVG")
            monitor_state["monitor_state"] = "SCAN"
            monitor_state["fvg_top"] = monitor_state["fvg_bottom"] = monitor_state["fvg_timestamp"] = None
            return monitor_state, None

        bar_low   = float(df["low"].iloc[-1])
        bar_close = float(df["close"].iloc[-1])

        # FVG invalidated
        if bar_close <= fvg_bottom:
            print(f"  FVG invalidated — close below FVG bottom")
            monitor_state["monitor_state"] = "SCAN"
            monitor_state["fvg_top"] = monitor_state["fvg_bottom"] = monitor_state["fvg_timestamp"] = None
            return monitor_state, None

        # Check retest
        wick_in_zone = bar_low <= fvg_top
        close_above  = bar_close > fvg_bottom
        hour_ok      = current_hour in ALLOWED_HOURS

        if wick_in_zone and close_above and hour_ok:
            entry     = bar_close
            stop_loss = round(fvg_bottom * 0.999, 2)
            stop_dist = entry - stop_loss
            if stop_dist <= 0:
                monitor_state["monitor_state"] = "SCAN"
                return monitor_state, None
            take_profit = get_daily_tp(df, n-1, entry, stop_loss)
            r_to_tp     = round((take_profit - entry) / stop_dist, 2)

            signal = {
                "timestamp":   now_utc.strftime("%Y-%m-%d %H:%M"),
                "utc_hour":    current_hour,
                "direction":   "LONG",
                "entry_price": entry,
                "stop_loss":   stop_loss,
                "take_profit": take_profit,
                "fvg_top":     fvg_top,
                "fvg_bottom":  fvg_bottom,
                "r_to_tp":     r_to_tp,
            }

            print(f"  ✅ GOLD FVG RETEST SIGNAL!")
            print(f"     Entry={entry:.2f}  SL={stop_loss:.2f}  TP={take_profit:.2f}  R={r_to_tp:.1f}R")

            tg_signal_detected(entry, stop_loss, take_profit,
                               fvg_top, fvg_bottom, current_hour, config)

            monitor_state["monitor_state"] = "SCAN"
            monitor_state["fvg_top"] = monitor_state["fvg_bottom"] = monitor_state["fvg_timestamp"] = None
            return monitor_state, signal

        print(f"  Waiting for retest... wick_in={wick_in_zone} close_ok={close_above} hour_ok={hour_ok}")
        return monitor_state, None

    # ── SCAN state — look for new FVG ──
    if state == "SCAN":
        if not ema_bullish(df, n-1):
            print(f"  EMA stack not bullish — no setup")
            return monitor_state, None

        # Bullish FVG: high[n-3] < low[n-1]
        if n < 4:
            return monitor_state, None

        h2 = float(df["high"].iloc[-3])
        li = float(df["low"].iloc[-1])

        if h2 < li:
            fvg_bottom = h2; fvg_top = li
            print(f"  📐 FVG detected: {fvg_bottom:.2f} → {fvg_top:.2f} | Waiting for retest...")
            monitor_state["monitor_state"] = "WAITING"
            monitor_state["fvg_top"]       = fvg_top
            monitor_state["fvg_bottom"]    = fvg_bottom
            monitor_state["fvg_timestamp"] = str(now_utc)
        else:
            print(f"  No FVG — EMA bullish but no gap")

    return monitor_state, None

# ─────────────────────────────────────────────────────────────
# SCAN ONCE
# ─────────────────────────────────────────────────────────────

def scan_once(config):
    now_utc = datetime.now(timezone.utc)
    print(f"\n  [{now_utc.strftime('%Y-%m-%d %H:%M UTC')}] GOLD Monitor scan")
    print(f"  {'─'*50}")

    # ── Sync state with CSV on startup ──────────────────────
    # If CSV has OPEN trade but state lost it, restore from CSV
    df           = load_log()
    monitor_state = load_state()
    open_in_csv  = df[df["status"] == "OPEN"]
    if len(open_in_csv) > 0 and monitor_state.get("open_trade") is None:
        row = open_in_csv.iloc[-1]
        monitor_state["open_trade"] = {
            "signal_id":   str(row["signal_id"]),
            "entry_price": float(row["entry_price"]),
            "stop_loss":   float(row["stop_loss"]),
            "take_profit": float(row["take_profit"]),
        }
        save_state(monitor_state)
        print(f"  [SYNC] Restored open_trade from CSV: {row['signal_id']}")

    # Update open trades first
    df, updated = update_open_trades(df, config)
    if updated > 0:
        save_log(df)
        # Clear open_trade from state if it was closed
        still_open = df[df["status"] == "OPEN"]
        if len(still_open) == 0:
            monitor_state = load_state()
            monitor_state["open_trade"] = None
            save_state(monitor_state)
        print(f"  Updated {updated} open trade(s)")

    # Check if we already have an open trade
    open_count = len(df[df["status"] == "OPEN"])
    if open_count > 0:
        open_trade = df[df["status"] == "OPEN"].iloc[-1]
        print(f"  Open trade: {open_trade['signal_id']} LONG @ "
              f"{float(open_trade['entry_price']):.2f} "
              f"SL={float(open_trade['stop_loss']):.2f} "
              f"TP={float(open_trade['take_profit']):.2f}")
        return

    # Fetch live data
    print(f"  Fetching XAUUSD 15m data...")
    df_price = fetch_gold_15m(200)
    if df_price is None:
        print(f"  ERROR: Could not fetch price data")
        return

    price = float(df_price["close"].iloc[-1])
    print(f"  XAUUSD: ${price:,.2f} | {len(df_price)} bars | Hour: {now_utc.hour}:00 UTC")

    # Load state and scan
    monitor_state = load_state()
    print(f"  Monitor state: {monitor_state.get('monitor_state','SCAN')}")

    monitor_state, signal = scan_for_signal(df_price, monitor_state, config)
    save_state(monitor_state)

    # Record signal if fired
    if signal:
        df = load_log()
        sid = next_signal_id(df)
        sd  = signal["entry_price"] - signal["stop_loss"]
        new_row = {
            "signal_id":   sid,
            "timestamp":   signal["timestamp"],
            "utc_hour":    signal["utc_hour"],
            "symbol":      SYMBOL_LABEL,
            "direction":   "LONG",
            "entry_price": signal["entry_price"],
            "stop_loss":   signal["stop_loss"],
            "take_profit": signal["take_profit"],
            "fvg_top":     signal["fvg_top"],
            "fvg_bottom":  signal["fvg_bottom"],
            "r_to_tp":     signal["r_to_tp"],
            "status":      "OPEN",
            "exit_price":  None,
            "exit_time":   None,
            "r_multiple":  None,
            "pnl_usd":     None,
            "bars_held":   0,
            "notes":       f"FVG retest | SL dist={sd:.2f}",
        }
        df = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)
        save_log(df)
        print(f"  Logged: {sid}")
    else:
        # Print summary
        closed = df[df["status"].isin(["WIN","LOSS"])]
        if len(closed) > 0:
            wins = len(closed[closed["status"]=="WIN"])
            wr   = round(wins/len(closed)*100, 1)
            avgr = round(closed["r_multiple"].mean(), 3)
            print(f"  Track record: {len(closed)} trades | WR={wr}% | AvgR={avgr:+.3f}")

# ─────────────────────────────────────────────────────────────
# REPORT
# ─────────────────────────────────────────────────────────────

def print_report():
    df = load_log()
    closed = df[df["status"].isin(["WIN","LOSS"])]
    print(f"\n{'='*55}")
    print(f"  GOLD Paper Trade Report")
    print(f"{'='*55}")
    print(f"  Total signals: {len(df)}")
    print(f"  Closed:        {len(closed)}")
    print(f"  Open:          {len(df[df['status']=='OPEN'])}")
    if len(closed) > 0:
        wins = len(closed[closed["status"]=="WIN"])
        wr   = round(wins/len(closed)*100, 1)
        avgr = round(closed["r_multiple"].mean(), 3)
        equity = PAPER_BALANCE
        for _, t in closed.iterrows():
            equity += equity * PAPER_RISK * float(t["r_multiple"])
        ret = round((equity-PAPER_BALANCE)/PAPER_BALANCE*100, 1)
        print(f"  Win rate:      {wr}%")
        print(f"  Avg R:         {avgr:+.3f}")
        print(f"  Return:        {ret:+.1f}%")
        print(f"  Final equity:  ${equity:,.2f}")
    print(f"{'='*55}\n")
    if len(df) > 0:
        print(df[["signal_id","timestamp","direction","entry_price",
                  "stop_loss","take_profit","status","r_multiple","pnl_usd"]].to_string(index=False))

# ─────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--watch",  action="store_true", help="Loop every 15 min")
    parser.add_argument("--report", action="store_true", help="Show trade report")
    args = parser.parse_args()

    config = load_config()

    if args.report:
        print_report()
        return

    if args.watch:
        print(f"\n  GOLD Signal Monitor — Watch Mode")
        print(f"  Scanning every 15 minutes | Hours: {ALLOWED_HOURS}")
        tg_system_started(config)
        while True:
            try:
                scan_once(config)
                print(f"\n  Next scan in 15 minutes...")
                time.sleep(900)
            except KeyboardInterrupt:
                print("\n  Stopped.")
                break
            except Exception as e:
                print(f"\n  Error: {e}")
                time.sleep(300)
    else:
        scan_once(config)


if __name__ == "__main__":
    main()
