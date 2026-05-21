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
    try:
        from live_signal_monitor import calculate_positions
        d   = decision
        bias= d.get("bias","")
        sb  = d.get("score_breakdown",{})
        atr_val = snapshot.get("atr",{}).get("atr_value", float(latest["close"]) * 0.002)
        ep  = float(latest["close"])
        if "LONG" in bias:
            sl = round(ep - atr_val * 3.0, 4)
        elif "SHORT" in bias:
            sl = round(ep + atr_val * 3.0, 4)
        else:
            sl = round(ep * 0.98, 4)
        positions = calculate_positions(ep, sl, balance_usdt, symbol)
        positions["stop_price"] = sl
    except Exception as e:
        positions = {"error": str(e)}

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
