"""
main.py
═══════════════════════════════════════════════════════════
Trading Agent V2 — FastAPI Server

Endpoints:
  GET  /              → health check
  POST /signal        → full pipeline: OHLCV → indicators → score → decision
  POST /signal/raw    → accepts pre-computed indicator snapshot → score → decision
  POST /signal/live   → Claude-powered live signal (requires ANTHROPIC_API_KEY)
  GET  /backtest      → run backtest on historical.csv
═══════════════════════════════════════════════════════════
"""

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
import pandas as pd
import json
import os

from indicator_engine   import compute_all_indicators
from signal_enrichment  import enrich_signal
from scoring_engine_py  import score_signal
from data_loader        import DataLoader

app = FastAPI(
    title       = "Trading Agent V2",
    description = "Systematic multi-indicator trading decision engine",
    version     = "2.0.0"
)

# Allow dashboard to connect from any origin (local dev)
app.add_middleware(
    CORSMiddleware,
    allow_origins     = ["*"],
    allow_methods     = ["*"],
    allow_headers     = ["*"],
)


# ─────────────────────────────────────────────────────────────
# SCHEMAS
# ─────────────────────────────────────────────────────────────

class OHLCVBar(BaseModel):
    time:   str
    open:   float
    high:   float
    low:    float
    close:  float
    volume: float


class OHLCVRequest(BaseModel):
    symbol:    str            = "UNKNOWN"
    timeframe: str            = "1h"
    bars:      List[OHLCVBar]
    allow_long:     bool = True
    allow_short:    bool = True
    force_no_trade: bool = False


class RawSignalRequest(BaseModel):
    symbol:             str = "UNKNOWN"
    timeframe:          str = "1h"
    indicator_snapshot: Dict[str, Any]
    price:              Optional[Dict[str, float]] = None
    volume:             Optional[float]            = 0.0
    allow_long:         bool = True
    allow_short:        bool = True
    force_no_trade:     bool = False


# ─────────────────────────────────────────────────────────────
# HEALTH CHECK
# ─────────────────────────────────────────────────────────────

@app.get("/")
def root():
    return {
        "status":  "Trading Agent V2 Running",
        "version": "2.0.0",
        "endpoints": [
            "POST /signal        — OHLCV bars → full pipeline → decision",
            "POST /signal/raw    — pre-computed snapshot → decision",
            "POST /signal/live   — Claude AI powered signal",
            "GET  /backtest      — backtest on historical.csv"
        ]
    }


# ─────────────────────────────────────────────────────────────
# ENDPOINT 1 — FULL PIPELINE (OHLCV → INDICATORS → SCORE)
# ─────────────────────────────────────────────────────────────

@app.post("/signal")
def full_pipeline_signal(request: OHLCVRequest):
    """
    Full pipeline endpoint.
    Accepts raw OHLCV bars, computes all indicators,
    enriches the signal, runs scoring, returns decision.

    Minimum 30 bars required. 100+ bars recommended.
    """
    if len(request.bars) < 30:
        raise HTTPException(
            status_code = 422,
            detail = f"Minimum 30 bars required, got {len(request.bars)}"
        )

    # Build DataFrame
    df = pd.DataFrame([b.dict() for b in request.bars])
    df["time"] = pd.to_datetime(df["time"])
    df = df.sort_values("time").reset_index(drop=True)

    # Latest bar price snapshot
    latest = df.iloc[-1]
    price  = {
        "open":  float(latest["open"]),
        "high":  float(latest["high"]),
        "low":   float(latest["low"]),
        "close": float(latest["close"])
    }

    # Run pipeline
    try:
        indicator_snapshot = compute_all_indicators(df)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Indicator computation failed: {str(e)}")

    enriched = enrich_signal(
        indicator_snapshot = indicator_snapshot,
        symbol             = request.symbol,
        timeframe          = request.timeframe,
        price              = price,
        volume             = float(latest["volume"]),
        allow_long         = request.allow_long,
        allow_short        = request.allow_short,
        force_no_trade     = request.force_no_trade
    )

    decision = score_signal(enriched)

    return {
        "symbol":              request.symbol,
        "timeframe":           request.timeframe,
        "signal_id":           enriched.get("signal_id"),
        "timestamp":           enriched.get("timestamp"),
        "indicator_snapshot":  indicator_snapshot,
        "derived_context":     enriched.get("derived_context"),
        "decision":            decision
    }


# ─────────────────────────────────────────────────────────────
# ENDPOINT 2 — RAW SNAPSHOT (pre-computed indicators → score)
# ─────────────────────────────────────────────────────────────

@app.post("/signal/raw")
def raw_snapshot_signal(request: RawSignalRequest):
    """
    Accepts a pre-computed indicator snapshot
    (e.g. from TradingView webhook) and runs scoring.

    Use this when TradingView sends indicator values directly.
    """
    enriched = enrich_signal(
        indicator_snapshot = request.indicator_snapshot,
        symbol             = request.symbol,
        timeframe          = request.timeframe,
        price              = request.price,
        volume             = request.volume or 0.0,
        allow_long         = request.allow_long,
        allow_short        = request.allow_short,
        force_no_trade     = request.force_no_trade
    )

    decision = score_signal(enriched)

    return {
        "symbol":          request.symbol,
        "timeframe":       request.timeframe,
        "signal_id":       enriched.get("signal_id"),
        "timestamp":       enriched.get("timestamp"),
        "derived_context": enriched.get("derived_context"),
        "decision":        decision
    }


# ─────────────────────────────────────────────────────────────
# ENDPOINT 3 — CLAUDE LIVE SIGNAL
# ─────────────────────────────────────────────────────────────

@app.post("/signal/live")
def claude_live_signal(request: OHLCVRequest):
    """
    Claude-powered signal using the full system prompt.
    Requires ANTHROPIC_API_KEY environment variable.
    Falls back to rule-based scoring if key not set.
    """
    api_key = os.getenv("ANTHROPIC_API_KEY")

    if not api_key:
        # Fallback to rule-based
        return full_pipeline_signal(request)

    if len(request.bars) < 30:
        raise HTTPException(
            status_code=422,
            detail=f"Minimum 30 bars required, got {len(request.bars)}"
        )

    df = pd.DataFrame([b.dict() for b in request.bars])
    df["time"] = pd.to_datetime(df["time"])
    df = df.sort_values("time").reset_index(drop=True)

    latest = df.iloc[-1]
    price  = {
        "open":  float(latest["open"]),
        "high":  float(latest["high"]),
        "low":   float(latest["low"]),
        "close": float(latest["close"])
    }

    try:
        indicator_snapshot = compute_all_indicators(df)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Indicator computation failed: {str(e)}")

    enriched = enrich_signal(
        indicator_snapshot = indicator_snapshot,
        symbol             = request.symbol,
        timeframe          = request.timeframe,
        price              = price,
        volume             = float(latest["volume"]),
        allow_long         = request.allow_long,
        allow_short        = request.allow_short,
        force_no_trade     = request.force_no_trade
    )

    # Load system prompt
    try:
        with open("system_prompt.txt", "r") as f:
            system_prompt = f.read()
    except FileNotFoundError:
        system_prompt = "You are a systematic trading decision engine. Return ONLY valid JSON."

    import anthropic
    client = anthropic.Anthropic(api_key=api_key)

    user_message = f"""
Evaluate this market signal and return a trade decision.

SIGNAL DATA:
{json.dumps(enriched, indent=2, default=str)}

Return ONLY valid JSON matching the output schema exactly.
"""

    try:
        response = client.messages.create(
            model      = "claude-sonnet-4-20250514",
            max_tokens = 1000,
            system     = system_prompt,
            messages   = [{"role": "user", "content": user_message}]
        )
        claude_decision = json.loads(response.content[0].text)
    except Exception:
        # Fallback to rule-based if Claude fails
        claude_decision = score_signal(enriched)

    return {
        "symbol":             request.symbol,
        "timeframe":          request.timeframe,
        "signal_id":          enriched.get("signal_id"),
        "timestamp":          enriched.get("timestamp"),
        "indicator_snapshot": indicator_snapshot,
        "derived_context":    enriched.get("derived_context"),
        "decision":           claude_decision,
        "engine":             "claude"
    }


# ─────────────────────────────────────────────────────────────
# ENDPOINT 4 — BACKTEST
# ─────────────────────────────────────────────────────────────

@app.get("/backtest")
def run_backtest(
    data_path:  str = "historical.csv",
    window:     int = 100,
    step:       int = 1
):
    """
    Runs the scoring engine across historical data.
    Returns per-bar decisions for dashboard visualization.

    Args:
        data_path: Path to CSV file (time, open, high, low, close, volume)
        window:    Number of bars to use per signal computation
        step:      Step size between signals (1 = every bar)
    """
    try:
        loader = DataLoader(data_path)
        df     = loader.load()
    except Exception as e:
        raise HTTPException(status_code=404, detail=f"Data load failed: {str(e)}")

    if len(df) < window:
        raise HTTPException(
            status_code=422,
            detail=f"Need at least {window} bars in CSV, got {len(df)}"
        )

    results = []

    for i in range(window, len(df), step):
        slice_df = df.iloc[i - window: i].copy().reset_index(drop=True)
        latest   = slice_df.iloc[-1]

        price = {
            "open":  float(latest["open"]),
            "high":  float(latest["high"]),
            "low":   float(latest["low"]),
            "close": float(latest["close"])
        }

        try:
            snapshot = compute_all_indicators(slice_df)
            enriched = enrich_signal(
                indicator_snapshot = snapshot,
                symbol             = "BACKTEST",
                timeframe          = "historical",
                price              = price,
                volume             = float(latest["volume"])
            )
            decision = score_signal(enriched)
        except Exception:
            continue

        results.append({
            "timestamp":  str(latest["time"]),
            "close":      float(latest["close"]),
            "bias":       decision["bias"],
            "confidence": decision["confidence"],
            "grade":      decision["setup_grade"],
            "regime":     decision["regime"],
            "score":      decision["score_breakdown"]
        })

    return {
        "total_bars":    len(df),
        "signals_run":   len(results),
        "window":        window,
        "results":       results
    }


# ─────────────────────────────────────────────────────────────
# ENDPOINT 5 — LIVE SIGNAL (self-fetching, dashboard-friendly)
# ─────────────────────────────────────────────────────────────

@app.get("/signal/latest")
def live_latest_signal(
    symbol:    str = "BTCUSDT",
    timeframe: str = "15m",
    bars:      int = 120,
):
    """
    Fetches live OHLCV bars from Bybit, runs the full pipeline,
    and returns a decision. Designed for the dashboard to call
    without needing to manage data fetching.

    Also checks whether the current UTC hour is a session hour
    for the selected strategy and includes that in the response.
    """
    import requests as req
    import time

    # Session hours per strategy
    SESSION_HOURS = {
        "BTCUSDT_G": [7, 12, 14, 18],
        "BTCUSDT_L": [7, 12, 14],
        "ETHUSDT_G": [7, 12, 14, 18],
        "SPY_L":     [13, 14],
    }

    # Map timeframe to Bybit interval
    tf_map = {"1m":"1","3m":"3","5m":"5","15m":"15","30m":"30",
              "1h":"60","2h":"120","4h":"240","1d":"D"}
    bybit_interval = tf_map.get(timeframe, "15")

    # Fetch from Bybit
    try:
        url    = "https://api.bybit.com/v5/market/kline"
        params = {
            "category": "linear",
            "symbol":   symbol,
            "interval": bybit_interval,
            "limit":    bars,
        }
        r = req.get(url, params=params, timeout=10, verify=False)
        r.raise_for_status()
        payload = r.json()

        if payload.get("retCode") != 0:
            raise HTTPException(status_code=502,
                detail=f"Bybit error: {payload.get('retMsg')}")

        raw = payload["result"]["list"]
        if not raw:
            raise HTTPException(status_code=502, detail="No data from Bybit")

        df = pd.DataFrame(raw, columns=[
            "open_time","open","high","low","close","volume","turnover"])
        df["time"] = pd.to_datetime(df["open_time"].astype(float), unit="ms")
        for c in ["open","high","low","close","volume"]:
            df[c] = df[c].astype(float)
        df = (df[["time","open","high","low","close","volume"]]
              .sort_values("time").reset_index(drop=True))

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502,
            detail=f"Data fetch failed: {str(e)}")

    if len(df) < 30:
        raise HTTPException(status_code=422,
            detail=f"Insufficient bars: {len(df)}")

    # Run pipeline
    latest = df.iloc[-1]
    price  = {
        "open":  float(latest["open"]),
        "high":  float(latest["high"]),
        "low":   float(latest["low"]),
        "close": float(latest["close"])
    }

    try:
        snapshot = compute_all_indicators(df)
    except Exception as e:
        raise HTTPException(status_code=500,
            detail=f"Indicator error: {str(e)}")

    enriched = enrich_signal(
        indicator_snapshot = snapshot,
        symbol             = symbol,
        timeframe          = timeframe,
        price              = price,
        volume             = float(latest["volume"]),
    )
    decision = score_signal(enriched)

    # Session hour check
    import datetime
    utc_hour     = datetime.datetime.utcnow().hour
    strat_key    = f"{symbol}_G"
    in_session   = utc_hour in SESSION_HOURS.get(strat_key, [])
    session_hours= SESSION_HOURS.get(strat_key, [])

    # WME signal (if available)
    try:
        from wme_sweep_engine      import compute_wme_signal
        from volume_profile_engine import compute_volume_intelligence
        pavp      = snapshot.get("pavp", {})
        vol_intel = compute_volume_intelligence(df, pavp)
        wme       = compute_wme_signal(df)
        wme_signal= wme["current"]
    except Exception:
        wme_signal = {}

    # KuCoin balance + position sizing
    balance_usdt  = 1000.0
    balance_source = "fallback"
    try:
        from kucoin_balance import get_balance
        bal_info      = get_balance()
        balance_usdt  = bal_info["balance_usdt"]
        balance_source = bal_info["source"]
    except Exception:
        pass

    # Position sizing
    risk_dollars = balance_usdt * 0.02
    positions    = {}
    # Define sl and ep before try so except block can access them
    ep  = float(latest["close"])
    atr_val = snapshot.get("atr",{}).get("atr_value", ep * 0.002)
    bias = decision.get("bias","")
    if "LONG" in bias:
        sl = round(ep - atr_val * 3.0, 4)
    elif "SHORT" in bias:
        sl = round(ep + atr_val * 3.0, 4)
    else:
        sl = round(ep * 0.98, 4)
    try:
        from live_signal_monitor import calculate_positions
        positions = calculate_positions(ep, sl, balance_usdt, symbol)
        positions["stop_price"] = sl
    except Exception as e:
        # Always preserve stop_price even on error so dashboard can show levels
        stop_pct_val = round(abs(ep - sl) / ep * 100, 3) if ep > 0 else 0
        positions = {"error": str(e), "stop_price": sl, "stop_pct": stop_pct_val}

    # Exit targets
    targets = []
    try:
        pavp_data = snapshot.get("pavp", {})
        targets = [
            {"level": "PAVP POC", "price": round(float(pavp_data.get("poc", 0)), 4)},
            {"level": "PAVP VAH", "price": round(float(pavp_data.get("vah", 0)), 4)},
            {"level": "PAVP VAL", "price": round(float(pavp_data.get("val", 0)), 4)},
        ]
        targets = [t for t in targets if t["price"] > 0]
    except Exception:
        pass

    return {
        "symbol":         symbol,
        "timeframe":      timeframe,
        "timestamp":      str(latest["time"]),
        "price":          float(latest["close"]),
        "bars_fetched":   len(df),
        "utc_hour":       utc_hour,
        "in_session":     in_session,
        "session_hours":  session_hours,
        "decision":       decision,
        "wme": {
            "long_entry":        wme_signal.get("long_entry",  False),
            "short_entry":       wme_signal.get("short_entry", False),
            "sweep_low_recent":  wme_signal.get("sweep_low_recent",  False),
            "sweep_high_recent": wme_signal.get("sweep_high_recent", False),
            "speed":             wme_signal.get("speed", 0.0),
            "htf_bull":          wme_signal.get("htf_bull", False),
        },
        "score_breakdown": decision.get("score_breakdown", {}),
        "key_conflicts":   decision.get("key_conflicts",   []),
        "entry_logic":     decision.get("entry_logic",     ""),
        "invalidation":    decision.get("invalidation",    ""),
        "balance": {
            "usdt":   balance_usdt,
            "source": balance_source,
            "risk_2pct": round(risk_dollars, 2),
        },
        "positions":  positions,
        "targets":    targets,
    }

# ─────────────────────────────────────────────────────────────
# ENDPOINT 6 — RECORD TRADE (from dashboard Take/Skip buttons)
# ─────────────────────────────────────────────────────────────

from pydantic import BaseModel
from typing import Optional

class TradeRecord(BaseModel):
    action:      str        # TOOK or SKIPPED
    strategy:    str
    symbol:      str
    timeframe:   str
    direction:   str
    grade:       str
    confidence:  float
    entry_price: float
    stop_loss:   float
    leverage:    str
    signal_ts:   str
    notes:       Optional[str] = ""
    balance:     Optional[float] = 0.0

@app.post("/trade/record")
def record_trade(trade: TradeRecord):
    """
    Records a trade or skip from the dashboard into trade_log.csv.
    Called when user clicks Take or Skip on the live signal panel.
    """
    import csv, os, subprocess
    from datetime import datetime, timezone

    log_file = "trade_log.csv"
    status   = "OPEN" if trade.action == "TOOK" else "SKIPPED"

    # Generate signal ID
    next_id = "SIG-001"
    if os.path.exists(log_file):
        try:
            existing = pd.read_csv(log_file)
            if len(existing) > 0:
                last = str(existing["signal_id"].iloc[-1])
                n    = int(last.split("-")[1]) + 1
                next_id = f"SIG-{n:03d}"
        except Exception:
            pass

    # Stop distance and position sizing
    stop_dist = abs(trade.entry_price - trade.stop_loss)
    stop_pct  = round(stop_dist / trade.entry_price * 100, 3) if trade.entry_price else 0
    risk_usd  = trade.balance * 0.02
    spot_qty  = round(risk_usd / stop_dist, 6) if stop_dist > 0 else 0

    def fut_qty(lev):
        return spot_qty

    def fut_margin(lev):
        return round(spot_qty * trade.entry_price / lev, 2) if lev > 0 else 0

    now = datetime.now(timezone.utc).isoformat()

    row = {
        "signal_id":       next_id,
        "timestamp":       now,
        "utc_hour":        datetime.now(timezone.utc).hour,
        "strategy":        trade.strategy,
        "symbol":          trade.symbol,
        "timeframe":       trade.timeframe,
        "mode":            "G" if "_G" in trade.strategy else "L",
        "direction":       trade.direction,
        "grade":           trade.grade,
        "confidence":      trade.confidence,
        "in_session":      True,
        "wme_signal":      True,
        "cvd_signal":      "",
        "in_lvn":          False,
        "entry_price":     trade.entry_price,
        "stop_loss":       trade.stop_loss,
        "stop_pct":        stop_pct,
        "target_1":        "",
        "target_2":        "",
        "target_3":        "",
        "spot_qty":        spot_qty,
        "fut_qty_2x":      fut_qty(2),
        "fut_margin_2x":   fut_margin(2),
        "fut_qty_5x":      fut_qty(5),
        "fut_margin_5x":   fut_margin(5),
        "fut_qty_10x":     fut_qty(10),
        "fut_margin_10x":  fut_margin(10),
        "fut_qty_20x":     fut_qty(20),
        "fut_margin_20x":  fut_margin(20),
        "fut_qty_50x":     fut_qty(50),
        "fut_margin_50x":  fut_margin(50),
        "fut_qty_100x":    fut_qty(100),
        "fut_margin_100x": fut_margin(100),
        "account_balance": trade.balance,
        "balance_source":  "kucoin",
        "status":          status,
        "exit_price":      "",
        "exit_time":       "",
        "r_multiple":      "",
        "pnl_usdt":        "",
        "running_wins":    0,
        "running_losses":  0,
        "running_wr":      0.0,
        "running_equity":  trade.balance,
        "notes":           f"{trade.leverage}x — {trade.notes}" if trade.action=="TOOK" else trade.notes,
    }

    # Append to CSV
    file_exists = os.path.exists(log_file)
    with open(log_file, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(row.keys()))
        if not file_exists:
            writer.writeheader()
        writer.writerow(row)

    # Push to GitHub
    try:
        subprocess.run(["git", "add", log_file], check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m",
                       f"Trade log: {next_id} {status} {trade.strategy} {trade.direction}"],
                      check=True, capture_output=True)
        subprocess.run(["git", "push"], check=True, capture_output=True)
        github_pushed = True
    except Exception:
        github_pushed = False

    return {
        "success":       True,
        "signal_id":     next_id,
        "status":        status,
        "github_pushed": github_pushed,
    }

# ─────────────────────────────────────────────────────────────
# ENDPOINT 7 — TRADE REVIEW + STRATEGY IMPROVEMENT ANALYSIS
# ─────────────────────────────────────────────────────────────

@app.get("/trade/review")
def trade_review():
    """
    Reads trade_log.csv and returns a full performance analysis.
    Used by the dashboard review panel and strategy improvement engine.
    Returns stats broken down by:
      - Overall win rate, avg R, equity curve
      - Per strategy
      - Per direction (LONG/SHORT)
      - Per grade (A/B)
      - Per hour
      - Per leverage used
      - TOOK vs SKIPPED comparison (did skipping hurt or help?)
    """
    import os
    log_file = "trade_log.csv"

    if not os.path.exists(log_file):
        return {"error": "No trade log found", "total_trades": 0}

    try:
        df = pd.read_csv(log_file)
    except Exception as e:
        return {"error": str(e), "total_trades": 0}

    if len(df) == 0:
        return {"total_trades": 0, "message": "No trades logged yet"}

    # Separate taken vs skipped
    taken   = df[df["status"].isin(["OPEN","WIN","LOSS","TIMEOUT"])]
    skipped = df[df["status"] == "SKIPPED"]
    closed  = df[df["status"].isin(["WIN","LOSS"])]

    wins   = (closed["status"] == "WIN").sum()
    losses = (closed["status"] == "LOSS").sum()
    total  = wins + losses
    wr     = round(float(wins)/int(total)*100, 1) if total else 0.0

    try:
        avg_r     = round(pd.to_numeric(closed["r_multiple"], errors="coerce").mean(), 3)
        total_pnl = round(pd.to_numeric(closed["pnl_usdt"],   errors="coerce").sum(), 2)
    except Exception:
        avg_r = 0.0; total_pnl = 0.0

    def grp_stats(sub):
        cl  = sub[sub["status"].isin(["WIN","LOSS"])]
        n   = len(cl)
        w   = (cl["status"]=="WIN").sum()
        r   = pd.to_numeric(cl["r_multiple"], errors="coerce").mean()
        return {
            "total":    len(sub),
            "closed":   n,
            "wins":     int(w),
            "losses":   int(n-w),
            "win_rate": round(float(w)/n*100,1) if n else 0.0,
            "avg_r":    round(float(r),3) if n and not pd.isna(r) else 0.0,
        }

    # Per strategy
    by_strategy = {}
    for s in taken["strategy"].dropna().unique():
        by_strategy[s] = grp_stats(taken[taken["strategy"]==s])

    # Per grade
    by_grade = {}
    for g in ["A","B","C"]:
        sub = taken[taken["grade"]==g]
        if len(sub): by_grade[g] = grp_stats(sub)

    # Per direction — handle "LONG SETUP", "SHORT SETUP" etc
    by_direction = {}
    for d in ["LONG","SHORT"]:
        sub = taken[taken["direction"].str.contains(d, na=False)]
        if len(sub): by_direction[d] = grp_stats(sub)

    # Per hour
    by_hour = {}
    try:
        for h in sorted(taken["utc_hour"].dropna().unique()):
            try:
                sub = taken[taken["utc_hour"]==h]
                if len(sub): by_hour[str(int(h))] = grp_stats(sub)
            except Exception:
                pass
    except Exception:
        pass

    # TOOK vs SKIPPED comparison
    # For skipped trades, we check if forward price moved in signal direction
    skipped_analysis = {
        "total_skipped": len(skipped),
        "note": "Forward price tracking for skipped trades requires re-running update mode"
    }

    # Equity curve
    equity = []
    try:
        bal0    = taken["account_balance"].dropna().iloc[0] if len(taken) else 1000.0
        capital = float(bal0) if not pd.isna(bal0) else 1000.0
    except Exception:
        capital = 1000.0
    for _, row in closed.iterrows():
        try:
            r = float(row["r_multiple"])
            if not pd.isna(r):
                capital += capital * 0.02 * r
                capital  = max(capital, 0.01)
        except Exception:
            pass
        equity.append(round(capital, 2))

    # Strategy improvement signals
    improvements = []

    if total >= 10:
        # Grade A underperforming grade B
        ga = by_grade.get("A", {})
        gb = by_grade.get("B", {})
        if ga.get("win_rate",0) < gb.get("win_rate",0) - 10:
            improvements.append({
                "finding": "Grade A underperforms Grade B",
                "detail":  f"A={ga.get('win_rate',0)}% vs B={gb.get('win_rate',0)}% WR",
                "action":  "Consider switching to Grade B only (L mode) for this strategy"
            })

        # Specific hours underperforming
        bad_hours = [h for h,s in by_hour.items()
                     if s.get("win_rate",100) < 35.7 and s.get("closed",0) >= 3]
        if bad_hours:
            improvements.append({
                "finding": f"Hours {bad_hours} consistently below breakeven",
                "detail":  f"These hours have <35.7% WR across 3+ trades",
                "action":  "Consider adding hour filter to skip these windows"
            })

        # LONG vs SHORT imbalance
        dl = by_direction.get("LONG",  {})
        ds = by_direction.get("SHORT", {})
        if ds.get("closed",0) >= 3 and ds.get("win_rate",100) < 40:
            improvements.append({
                "finding": "SHORT trades underperforming",
                "detail":  f"SHORT WR={ds.get('win_rate',0)}% vs LONG WR={dl.get('win_rate',0)}%",
                "action":  "Consider disabling SHORT entries until SHORT WR improves"
            })

        # Avg R too low
        if 0 < avg_r < 0.1:
            improvements.append({
                "finding": "Avg R is positive but very low",
                "detail":  f"Current avg R = {avg_r}. Wins are too small relative to risk.",
                "action":  "Review exit targets — consider holding to TP2/TP3 more often"
            })

    # Recursively convert all numpy/pandas types to plain Python for JSON safety
    import math, numpy as np
    def _sanitize(obj):
        if isinstance(obj, dict):
            return {k: _sanitize(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [_sanitize(v) for v in obj]
        if isinstance(obj, (np.integer,)):
            return int(obj)
        if isinstance(obj, (np.floating,)):
            f = float(obj)
            return None if (math.isnan(f) or math.isinf(f)) else f
        if isinstance(obj, (np.bool_,)):
            return bool(obj)
        if isinstance(obj, float):
            return None if (math.isnan(obj) or math.isinf(obj)) else obj
        return obj

    result = {
        "total_signals":    len(df),
        "taken":            len(taken),
        "skipped":          len(skipped),
        "open":             len(taken[taken["status"]=="OPEN"]),
        "closed":           int(total),
        "wins":             int(wins),
        "losses":           int(losses),
        "win_rate":         wr,
        "avg_r":            avg_r,
        "total_pnl_usdt":   total_pnl,
        "breakeven_wr":     35.7,
        "by_strategy":      by_strategy,
        "by_grade":         by_grade,
        "by_direction":     by_direction,
        "by_hour":          by_hour,
        "skipped_analysis": skipped_analysis,
        "equity_curve":     equity,
        "improvement_signals": improvements,
        "data_quality": {
            "min_trades_for_analysis": 10,
            "sufficient_data":         bool(total >= 10),
            "note": "Improvement signals require 10+ closed trades" if total < 10
                     else f"Based on {total} closed trades"
        }
    }
    return _sanitize(result)


@app.post("/trade/close")
def close_trade(payload: dict):
    """
    Close an open trade — record exit price, which TP was hit, calculate R and P&L.
    Called from the dashboard Close Trade button.
    """
    import os
    log_file = "trade_log.csv"

    signal_id  = payload.get("signal_id")
    exit_price = float(payload.get("exit_price", 0))
    tp_hit     = payload.get("tp_hit", "manual")  # TP1, TP2, TP3, STOP, manual
    notes      = payload.get("notes", "")

    if not os.path.exists(log_file):
        return {"error": "No trade log found"}

    try:
        df = pd.read_csv(log_file)
    except Exception as e:
        return {"error": str(e)}

    mask = (df["signal_id"] == signal_id) & (df["status"] == "OPEN")
    if not mask.any():
        return {"error": f"No open trade found with ID {signal_id}"}

    idx = df[mask].index[0]
    row = df.loc[idx]

    entry_price = float(row["entry_price"])
    stop_loss   = float(row["stop_loss"])
    direction   = str(row["direction"])
    balance     = float(row["account_balance"]) if row["account_balance"] else 1000.0

    stop_dist = abs(entry_price - stop_loss)
    if stop_dist == 0:
        stop_dist = entry_price * 0.02

    if "LONG" in direction:
        r_multiple = round((exit_price - entry_price) / stop_dist, 3)
    else:
        r_multiple = round((entry_price - exit_price) / stop_dist, 3)

    pnl_usdt = round(balance * 0.02 * r_multiple, 2)
    status   = "WIN" if r_multiple > 0 else "LOSS"

    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()

    df.loc[idx, "exit_price"] = exit_price
    df.loc[idx, "exit_time"]  = now
    df.loc[idx, "r_multiple"] = r_multiple
    df.loc[idx, "pnl_usdt"]   = pnl_usdt
    df.loc[idx, "status"]     = status
    df.loc[idx, "notes"]      = str(row.get("notes","")) + f" | Exit: {tp_hit}" + (f" — {notes}" if notes else "")

    # Recalculate running stats
    closed = df[df["status"].isin(["WIN","LOSS"])]
    wins   = (closed["status"]=="WIN").sum()
    losses = (closed["status"]=="LOSS").sum()
    total  = wins + losses
    wr     = round(wins/total*100,1) if total else 0.0

    capital = balance
    for _, r in closed.iterrows():
        try:
            capital += capital * 0.02 * float(r["r_multiple"])
            capital  = max(capital, 0.01)
        except Exception:
            pass

    df.loc[idx, "running_wins"]   = int(wins)
    df.loc[idx, "running_losses"] = int(losses)
    df.loc[idx, "running_wr"]     = wr
    df.loc[idx, "running_equity"] = round(capital, 2)

    df.to_csv(log_file, index=False, float_format="%.4f")

    # Push to GitHub
    github_pushed = False
    try:
        import subprocess
        subprocess.run(["git","add",log_file], check=True, capture_output=True)
        subprocess.run(["git","commit","-m",
                       f"Trade close: {signal_id} {status} {r_multiple:+.3f}R"],
                      check=True, capture_output=True)
        subprocess.run(["git","push"], check=True, capture_output=True)
        github_pushed = True
    except Exception:
        pass

    return {
        "success":       True,
        "signal_id":     signal_id,
        "status":        status,
        "r_multiple":    r_multiple,
        "pnl_usdt":      pnl_usdt,
        "running_wr":    wr,
        "github_pushed": github_pushed,
    }

# ─────────────────────────────────────────────────────────────
# ENDPOINT 9 — OPEN TRADES (for live status banner)
# ─────────────────────────────────────────────────────────────

@app.get("/trade/open")
def get_open_trades():
    """
    Returns all currently OPEN trades from trade_log.csv.
    Used by the dashboard to show the open trade status banner.
    """
    import os
    log_file = "trade_log.csv"
    if not os.path.exists(log_file):
        return {"open_trades": [], "count": 0}
    try:
        df = pd.read_csv(log_file)
    except Exception as e:
        return {"error": str(e), "open_trades": [], "count": 0}

    open_df = df[df["status"] == "OPEN"].copy()
    if len(open_df) == 0:
        return {"open_trades": [], "count": 0}

    trades = []
    for _, row in open_df.iterrows():
        import math
        def _sfo(v):
            try:
                f=float(v); return None if (math.isnan(f) or math.isinf(f)) else f
            except: return None
        trades.append({
            "signal_id":       str(row.get("signal_id",  "") or ""),
            "timestamp":       str(row.get("timestamp",  "") or "")[:16].replace("T", " "),
            "strategy":        str(row.get("strategy",   "") or ""),
            "symbol":          str(row.get("symbol",     "") or ""),
            "direction":       str(row.get("direction",  "") or ""),
            "grade":           str(row.get("grade",      "") or ""),
            "entry_price":     _sfo(row.get("entry_price")) or 0.0,
            "stop_loss":       _sfo(row.get("stop_loss"))   or 0.0,
            "target_1":        _sfo(row.get("target_1")),
            "target_2":        _sfo(row.get("target_2")),
            "target_3":        _sfo(row.get("target_3")),
            "leverage":        str(row.get("notes","")).split("x")[0].strip() if "x" in str(row.get("notes","")) else "?",
            "account_balance": _sfo(row.get("account_balance")) or 1000.0,
        })

    return {"open_trades": trades, "count": len(trades)}


# ─────────────────────────────────────────────────────────────
# ENDPOINT 10 — TRADE HISTORY (last N trades for table)
# ─────────────────────────────────────────────────────────────

@app.get("/trade/history")
def get_trade_history(limit: int = 20):
    """
    Returns the last N trades from trade_log.csv for the
    trade history table in the dashboard.
    """
    import os
    log_file = "trade_log.csv"
    if not os.path.exists(log_file):
        return {"trades": [], "total": 0}
    try:
        df = pd.read_csv(log_file)
    except Exception as e:
        return {"error": str(e), "trades": [], "total": 0}

    total = len(df)
    recent = df.tail(limit).copy()
    trades = []
    for _, row in recent.iterrows():
        import math
        def _sfh(v):
            try:
                f=float(v); return None if (math.isnan(f) or math.isinf(f)) else f
            except: return None
        trades.append({
            "signal_id":   str(row.get("signal_id",  "") or ""),
            "timestamp":   str(row.get("timestamp",  "") or "")[:16].replace("T", " "),
            "strategy":    str(row.get("strategy",   "") or ""),
            "direction":   str(row.get("direction",  "") or ""),
            "grade":       str(row.get("grade",      "") or ""),
            "entry_price": _sfh(row.get("entry_price")) or 0.0,
            "exit_price":  _sfh(row.get("exit_price")),
            "status":      str(row.get("status",     "") or ""),
            "r_multiple":  _sfh(row.get("r_multiple")),
            "pnl_usdt":    _sfh(row.get("pnl_usdt")),
            "running_wr":  _sfh(row.get("running_wr")),
        })

    return {"trades": trades, "total": total}

# ─────────────────────────────────────────────────────────────
# ENDPOINT 11 — TRADE STATUS (open trades with live P&L)
# ─────────────────────────────────────────────────────────────

@app.get("/trade/status")
def get_trade_status():
    """
    Returns open trades enriched with current market price and P&L.
    Used by the improved close trade flow.
    """
    import os, requests as req
    log_file = "trade_log.csv"
    if not os.path.exists(log_file):
        return {"open_trades": [], "count": 0}
    try:
        df = pd.read_csv(log_file)
    except Exception as e:
        return {"error": str(e), "open_trades": [], "count": 0}

    open_df = df[df["status"] == "OPEN"].copy()
    if len(open_df) == 0:
        return {"open_trades": [], "count": 0}

    trades = []
    for _, row in open_df.iterrows():
        symbol    = str(row.get("symbol", "BTCUSDT"))
        direction = str(row.get("direction", "LONG"))
        entry_px  = float(row.get("entry_price", 0) or 0)
        stop_px   = float(row.get("stop_loss",   0) or 0)
        bal       = float(row.get("account_balance", 1000) or 1000)

        # Fetch current price
        cur_px = None
        try:
            if symbol != "XAUUSD":
                url = "https://api.bybit.com/v5/market/tickers"
                r   = req.get(url, params={"category":"linear","symbol":symbol},
                              timeout=5, verify=False)
                cur_px = float(r.json()["result"]["list"][0]["lastPrice"])
        except Exception:
            pass

        # Calculate live R and P&L
        r_live = None; pnl_live = None; status_hint = "OPEN"
        stop_dist = abs(entry_px - stop_px)

        def safe_float_val(v):
            import math
            try:
                f = float(v)
                return None if math.isnan(f) or math.isinf(f) else f
            except:
                return None

        t1 = safe_float_val(row.get("target_1"))
        t2 = safe_float_val(row.get("target_2"))
        t3 = safe_float_val(row.get("target_3"))

        # Try to get actual PnL from KuCoin position
        kucoin_pnl  = None
        kucoin_mark = None
        try:
            from kucoin_executor import load_config, get_open_positions, SYMBOL_MAP
            cfg = load_config()
            kc_sym = SYMBOL_MAP.get(symbol, "XBTUSDTM")
            positions = get_open_positions(cfg)
            for pos in positions:
                if pos.get("symbol") == kc_sym:
                    kucoin_pnl  = float(pos.get("unrealisedPnl", 0) or 0)
                    kucoin_mark = float(pos.get("markPrice", 0) or 0)
                    if kucoin_mark > 0:
                        cur_px = kucoin_mark
                    break
        except Exception:
            pass

        if cur_px and stop_dist > 0:
            is_long = "LONG" in direction
            if is_long:
                r_live = (cur_px - entry_px) / stop_dist
                if cur_px <= stop_px:
                    status_hint = "LIKELY LOSS — at/below stop"
                elif t1 and cur_px >= t1:
                    status_hint = "AT TP1 — consider partial close"
                elif t2 and cur_px >= t2:
                    status_hint = "AT TP2 — consider close"
                elif t3 and cur_px >= t3:
                    status_hint = "AT TP3 — full close"
                elif r_live > 0:
                    status_hint = "IN PROFIT — holding"
                else:
                    status_hint = "BELOW ENTRY — watch stop"
            else:
                r_live = (entry_px - cur_px) / stop_dist
                if cur_px >= stop_px:
                    status_hint = "LIKELY LOSS — at/above stop"
                elif t1 and cur_px <= t1:
                    status_hint = "AT TP1 — consider partial close"
                elif r_live > 0:
                    status_hint = "IN PROFIT — holding"
                else:
                    status_hint = "ABOVE ENTRY — watch stop"

            # Use actual KuCoin PnL if available, otherwise theoretical
            if kucoin_pnl is not None:
                pnl_live = round(kucoin_pnl, 4)
            else:
                pnl_live = round(bal * 0.02 * r_live, 2)
            r_live = round(r_live, 3)

        import math
        def clean(v):
            if v is None: return None
            try:
                f = float(v)
                return None if math.isnan(f) or math.isinf(f) else f
            except: return None

        trades.append({
            "signal_id":   str(row.get("signal_id", "")),
            "timestamp":   str(row.get("timestamp", ""))[:16].replace("T"," "),
            "strategy":    str(row.get("strategy", "")),
            "symbol":      symbol,
            "direction":   direction,
            "grade":       str(row.get("grade", "")),
            "entry_price": clean(entry_px) or 0,
            "stop_loss":   clean(stop_px)  or 0,
            "target_1":    clean(t1),
            "target_2":    clean(t2),
            "target_3":    clean(t3),
            "current_price": clean(cur_px),
            "r_live":      clean(r_live),
            "pnl_live":    clean(pnl_live),
            "status_hint": status_hint,
            "balance":     clean(bal) or 1000,
        })

    return {"open_trades": trades, "count": len(trades)}

# ─────────────────────────────────────────────────────────────
# AUTOMATION ENDPOINTS
# ─────────────────────────────────────────────────────────────

@app.post("/auto/arm")
def arm_automation(payload: dict):
    """Arm the automation system."""
    try:
        from kucoin_executor import arm, load_state
        leverage = int(payload.get("leverage", 10))
        risk_pct = float(payload.get("risk_pct", 0.02))
        arm(leverage, risk_pct)
        state = load_state()
        return {"armed": True, "leverage": leverage, "risk_pct": risk_pct}
    except Exception as e:
        return {"error": str(e), "armed": False}


@app.post("/auto/disarm")
def disarm_automation():
    """Disarm automation — stops new trades, keeps monitoring existing."""
    try:
        from kucoin_executor import disarm, load_state
        disarm()
        state = load_state()
        return {"armed": False, "active_trades": len(state.get("active_trades", {}))}
    except Exception as e:
        return {"error": str(e)}


@app.get("/auto/status")
def automation_status():
    """Get automation status and active trades."""
    try:
        from kucoin_executor import load_state, load_config, get_available_balance, get_open_positions
        state  = load_state()
        config = load_config()
        available = 0
        equity    = 0
        unrealised= 0
        positions = []
        balance_error = None
        try:
            from kucoin_executor import get_account_overview
            overview  = get_account_overview(config)
            available = overview["available"]
            equity    = overview["equity"]
            unrealised= overview["unrealised_pnl"]
            positions = get_open_positions(config)
        except Exception as be:
            balance_error = str(be)
            try:
                available = get_available_balance(config)
            except Exception:
                pass
        return {
            "armed":            state.get("armed", False),
            "leverage":         state.get("leverage", 10),
            "risk_pct":         state.get("risk_pct", 0.02),
            "active_trades":    len(state.get("active_trades", {})),
            "trade_ids":        list(state.get("active_trades", {}).keys()),
            "kucoin_balance":   round(available, 2),
            "kucoin_equity":    round(equity, 2),
            "kucoin_unrealised":round(unrealised, 4),
            "kucoin_positions": len(positions),
            "balance_error":    balance_error,
        }
    except Exception as e:
        return {"error": str(e), "armed": False}


@app.post("/auto/emergency_stop")
def emergency_stop_all():
    """Emergency close all positions."""
    try:
        from kucoin_executor import emergency_stop, load_state, load_config
        config = load_config()
        state  = load_state()
        emergency_stop(config, state)
        return {"success": True, "message": "All positions closed"}
    except Exception as e:
        return {"error": str(e), "success": False}


@app.post("/auto/execute")
def auto_execute(signal_data: dict):
    """Manually trigger execution of a signal (used by dashboard)."""
    try:
        from kucoin_executor import on_signal
        result = on_signal(signal_data)
        return result
    except Exception as e:
        return {"executed": False, "error": str(e)}

@app.post("/auto/handoff")
def handoff_trade(payload: dict):
    """
    Register a manually entered trade with the automation executor.
    The executor will then manage TP orders and trailing stops for it.
    """
    try:
        from kucoin_executor import load_state, load_config, save_state,             get_open_positions, place_limit_order, place_stop_order, SYMBOL_MAP
        import time
        from datetime import datetime, timezone

        state  = load_state()
        config = load_config()

        signal_id   = payload.get("signal_id", f"MANUAL-{int(time.time())}")
        direction   = payload.get("direction", "LONG")
        kc_symbol   = payload.get("kc_symbol", "XBTUSDTM")
        entry_price = float(payload.get("entry_price", 0))
        stop_price  = float(payload.get("stop_price",  0))
        tp1_price   = payload.get("tp1")
        tp2_price   = payload.get("tp2")
        tp3_price   = payload.get("tp3")
        leverage    = int(payload.get("leverage", 10))

        if not entry_price or not stop_price:
            return {"success": False, "error": "Entry and stop price required"}

        # Get actual position from KuCoin to get contract count
        contracts = 1
        try:
            positions = get_open_positions(config)
            for pos in positions:
                if pos.get("symbol") == kc_symbol:
                    contracts = abs(int(float(pos.get("currentQty", 1))))
                    break
        except Exception:
            pass

        # Build TP order records (don't place new orders — user already has theirs)
        tp1_info = {"price": float(tp1_price), "hit": False, "qty": max(1, round(contracts*0.35))} if tp1_price else {}
        tp2_info = {"price": float(tp2_price), "hit": False, "qty": max(1, round(contracts*0.30))} if tp2_price else {}
        tp3_info = {"price": float(tp3_price), "hit": False, "qty": contracts} if tp3_price else {}

        # Register in automation state
        trade_record = {
            "signal_id":         signal_id,
            "symbol":            SYMBOL_MAP.get("BTCUSDT", "BTCUSDT"),
            "kc_symbol":         kc_symbol,
            "strategy":          "MANUAL_HANDOFF",
            "direction":         direction,
            "contracts":         contracts,
            "entry_price":       entry_price,
            "stop_price":        stop_price,
            "stop_order_id":     None,   # user manages stop manually
            "tp1":               tp1_info,
            "tp2":               tp2_info,
            "tp3":               tp3_info,
            "leverage":          leverage,
            "balance_at_entry":  state.get("last_balance", 1000),
            "risk_pct":          state.get("risk_pct", 0.02),
            "status":            "OPEN",
            "opened_at":         datetime.now(timezone.utc).isoformat(),
            "trailing_stop_moved": False,
            "manual_handoff":    True,
        }

        state["active_trades"][signal_id] = trade_record
        save_state(state)

        return {
            "success":   True,
            "signal_id": signal_id,
            "contracts": contracts,
            "message":   f"Trade registered — executor will monitor TPs and move stops"
        }

    except Exception as e:
        return {"success": False, "error": str(e)}

@app.post("/trade/correct")
def correct_trade(payload: dict):
    """Correct a wrongly recorded trade outcome."""
    import os, math
    log_file = "trade_log.csv"
    signal_id  = payload.get("signal_id")
    exit_price = float(payload.get("exit_price", 0))

    if not os.path.exists(log_file):
        return {"error": "No trade log found"}
    try:
        df = pd.read_csv(log_file)
    except Exception as e:
        return {"error": str(e)}

    mask = df["signal_id"] == signal_id
    if not mask.any():
        return {"error": f"Trade {signal_id} not found"}

    idx       = df[mask].index[0]
    row       = df.loc[idx]
    entry     = float(row["entry_price"])
    stop      = float(row["stop_loss"])
    direction = str(row["direction"])
    balance   = float(row["account_balance"]) if not pd.isna(row["account_balance"]) else 1000.0

    stop_dist  = abs(entry - stop) or entry * 0.02
    is_long    = "LONG" in direction
    r_multiple = round((exit_price - entry) / stop_dist if is_long
                       else (entry - exit_price) / stop_dist, 3)
    pnl_usdt   = round(balance * 0.02 * r_multiple, 2)
    status     = "WIN" if r_multiple > 0 else "LOSS"

    from datetime import datetime, timezone
    df.loc[idx, "exit_price"]  = exit_price
    df.loc[idx, "r_multiple"]  = r_multiple
    df.loc[idx, "pnl_usdt"]    = pnl_usdt
    df.loc[idx, "status"]      = status
    df.loc[idx, "exit_time"]   = datetime.now(timezone.utc).isoformat()
    df.to_csv(log_file, index=False)

    return {
        "success":    True,
        "signal_id":  signal_id,
        "r_multiple": r_multiple,
        "pnl_usdt":   pnl_usdt,
        "status":     status,
    }
