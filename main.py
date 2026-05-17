from fastapi import FastAPI
from pydantic import BaseModel
from typing import Literal, Dict, Any
import json

app = FastAPI()

# -----------------------------
# INPUT SCHEMA (matches webhook spec)
# -----------------------------

class Signal(BaseModel):
    timestamp: str
    symbol: str
    timeframe: str

    price: Dict[str, float]

    pivot_volume_profile: Dict[str, Any]
    macd: Dict[str, Any]
    trend_speed: Dict[str, Any]
    zigzag_structure: Dict[str, Any]
    cvd_iq: Dict[str, Any]
    atr: Dict[str, Any]


# -----------------------------
# CORE DECISION ENGINE (RULE-BASED V1 PLACEHOLDER)
# Claude will later replace logic, but this is structural backbone
# -----------------------------

def basic_score(signal: Signal):

    score = 5.0
    reasons = []

    # --- STRUCTURE ---
    if signal.zigzag_structure["structure"] == "BULLISH":
        score += 1.5
        reasons.append("Bullish ZigZag structure")
    elif signal.zigzag_structure["structure"] == "BEARISH":
        score -= 1.5
        reasons.append("Bearish ZigZag structure")

    # --- PAVP LOCATION ---
    loc = signal.pivot_volume_profile["value_area_position"]
    if loc == "ABOVE_VA":
        score += 1.5
        reasons.append("Price above Value Area")
    elif loc == "BELOW_VA":
        score -= 1.5
        reasons.append("Price below Value Area")
    else:
        score -= 0.5
        reasons.append("Inside Value Area (chop zone)")

    # --- MACD MOMENTUM ---
    if signal.macd["histogram_direction"] == "RISING":
        score += 1.0
        reasons.append("MACD rising momentum")
    else:
        score -= 0.5

    # --- TREND SPEED ---
    if signal.trend_speed["direction"] == "BULLISH":
        score += 1.0
        reasons.append("Trend Speed bullish")
    elif signal.trend_speed["direction"] == "BEARISH":
        score -= 1.0
        reasons.append("Trend Speed bearish")

    # --- CVD ---
    if signal.cvd_iq["cvd_direction"] == "BUYING":
        score += 1.0
        reasons.append("Buy-side pressure confirmed")
    elif signal.cvd_iq["cvd_direction"] == "SELLING":
        score -= 1.0
        reasons.append("Sell-side pressure confirmed")

    # --- ATR FILTER ---
    if signal.atr["volatility_state"] == "LOW":
        score -= 0.5
        reasons.append("Low volatility (fakeout risk)")
    elif signal.atr["volatility_state"] == "HIGH":
        score -= 0.2

    # Clamp score
    score = max(0, min(10, score))

    # Decision
    if score >= 7.5:
        decision = "LONG SETUP"
        grade = "A"
    elif score >= 6.5:
        decision = "LONG SETUP"
        grade = "B"
    elif score >= 5.5:
        decision = "LONG SETUP"
        grade = "C"
    elif score <= 2.5:
        decision = "SHORT SETUP"
        grade = "A"
    else:
        decision = "NO TRADE"
        grade = "NONE"

    return {
        "decision": decision,
        "score": score,
        "grade": grade,
        "reasons": reasons
    }


# -----------------------------
# API ENDPOINT
# -----------------------------

@app.post("/signal")
def receive_signal(signal: Signal):

    result = basic_score(signal)

    return {
        "symbol": signal.symbol,
        "timeframe": signal.timeframe,
        "result": result
    }


# -----------------------------
# HEALTH CHECK
# -----------------------------

@app.get("/")
def root():
    return {"status": "Trading Agent V2 Running"}

from fastapi import FastAPI
from claude_signal_engine import ClaudeSignalEngine

app = FastAPI()

engine = ClaudeSignalEngine()


@app.post("/live-signal")
def live_signal(market_data: dict):

    signal = engine.generate_signal(market_data)

    return {
        "signal": signal
    }