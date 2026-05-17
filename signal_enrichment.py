"""
signal_enrichment.py
═══════════════════════════════════════════════════════════
Takes raw indicator outputs and enriches them with:
  - Regime detection
  - Derived context fields
  - Signal validation / failsafe checks

Sits between indicator_engine.py and scoring_engine.py
in the pipeline.
═══════════════════════════════════════════════════════════
"""

from typing import Dict, Any, Tuple
import uuid
from datetime import datetime, timezone


# ─────────────────────────────────────────────────────────────
# REGIME DETECTION
# ─────────────────────────────────────────────────────────────

def detect_regime(snapshot: Dict[str, Any]) -> Tuple[str, int]:
    """
    Classifies active market regime from indicator snapshot.
    Returns (regime_string, confidence_count).

    Regimes: TRENDING_UP | TRENDING_DOWN | RANGING |
             BREAKOUT | REVERSAL | UNCERTAIN
    """
    zz   = snapshot.get("zigzag", {})
    pavp = snapshot.get("pavp", {})
    ts   = snapshot.get("trend_speed", {})
    macd = snapshot.get("macd", {})
    cvd  = snapshot.get("cvd", {})
    atr  = snapshot.get("atr", {})

    structure  = zz.get("structure", "NEUTRAL")
    va_pos     = pavp.get("value_area_position", "INSIDE_VA")
    ts_dir     = ts.get("direction", "FLAT")
    ts_regime  = ts.get("regime", "NORMAL")
    hist_state = macd.get("histogram_state", "")
    cvd_dir    = cvd.get("cvd_direction", "NEUTRAL")
    compression = atr.get("compression", False)
    expansion   = atr.get("expansion", False)
    ratio_avg   = ts.get("wave_analysis", {}).get("current_ratio_avg", 0.0)
    div_medium  = cvd.get("divergence", {}).get("medium", "NONE")
    div_large   = cvd.get("divergence", {}).get("large", "NONE")

    # TRENDING UP
    trending_up_signals = [
        structure == "BULLISH",
        va_pos == "ABOVE_VA",
        ts_dir == "BULLISH" and ts_regime == "EXPANSION",
        hist_state == "BULLISH_ACCELERATING",
        cvd_dir == "BUYING"
    ]
    trending_up_count = sum(trending_up_signals)

    # TRENDING DOWN
    trending_dn_signals = [
        structure == "BEARISH",
        va_pos == "BELOW_VA",
        ts_dir == "BEARISH" and ts_regime == "EXPANSION",
        hist_state == "BEARISH_ACCELERATING",
        cvd_dir == "SELLING"
    ]
    trending_dn_count = sum(trending_dn_signals)

    # RANGING
    ranging_signals = [
        va_pos == "INSIDE_VA",
        structure == "NEUTRAL",
        ts_regime == "CONSOLIDATION",
        hist_state in ("BULLISH_DECELERATING", "BEARISH_RECOVERING")
    ]
    ranging_count = sum(ranging_signals)

    # BREAKOUT
    breakout_signals = [
        compression and expansion,
        va_pos in ("ABOVE_VA", "BELOW_VA") and ratio_avg > 1.2,
        ts_regime == "EXPANSION" and ratio_avg > 1.2,
        cvd_dir in ("BUYING", "SELLING")
    ]
    breakout_count = sum(breakout_signals)

    # REVERSAL / EXHAUSTION
    reversal_signals = [
        ts_regime == "EXHAUSTION",
        div_medium != "NONE" or div_large != "NONE",
        hist_state in ("BULLISH_DECELERATING", "BEARISH_RECOVERING"),
        ratio_avg < 0.4
    ]
    reversal_count = sum(reversal_signals)

    # Pick winning regime
    counts = {
        "TRENDING_UP":   trending_up_count,
        "TRENDING_DOWN": trending_dn_count,
        "RANGING":       ranging_count,
        "BREAKOUT":      breakout_count,
        "REVERSAL":      reversal_count
    }

    top_regime = max(counts, key=counts.get)
    top_count  = counts[top_regime]

    # Require at least 2 agreeing signals for a firm regime
    if top_count < 2:
        return "UNCERTAIN", top_count

    return top_regime, top_count


# ─────────────────────────────────────────────────────────────
# BIAS DERIVATION
# ─────────────────────────────────────────────────────────────

def derive_structure_bias(snapshot: Dict[str, Any]) -> str:
    zz  = snapshot.get("zigzag", {})
    pavp = snapshot.get("pavp", {})

    structure = zz.get("structure", "NEUTRAL")
    bos       = zz.get("bos_signal", "NONE")
    va_pos    = pavp.get("value_area_position", "INSIDE_VA")

    if structure == "BULLISH" and va_pos == "ABOVE_VA":
        return "STRONG_BULLISH"
    if structure == "BULLISH":
        return "BULLISH"
    if structure == "BEARISH" and va_pos == "BELOW_VA":
        return "STRONG_BEARISH"
    if structure == "BEARISH":
        return "BEARISH"
    return "NEUTRAL"


def derive_momentum_bias(snapshot: Dict[str, Any]) -> str:
    ts   = snapshot.get("trend_speed", {})
    macd = snapshot.get("macd", {})

    ts_dir     = ts.get("direction", "FLAT")
    ts_regime  = ts.get("regime", "NORMAL")
    hist_state = macd.get("histogram_state", "")

    bullish_momentum = (
        ts_dir == "BULLISH" and
        hist_state in ("BULLISH_ACCELERATING", "BULLISH_DECELERATING")
    )
    bearish_momentum = (
        ts_dir == "BEARISH" and
        hist_state in ("BEARISH_ACCELERATING", "BEARISH_RECOVERING")
    )

    if bullish_momentum and ts_regime == "EXPANSION":
        return "STRONG_BULLISH"
    if bullish_momentum:
        return "BULLISH"
    if bearish_momentum and ts_regime == "EXPANSION":
        return "STRONG_BEARISH"
    if bearish_momentum:
        return "BEARISH"
    if ts_regime == "EXHAUSTION":
        return "EXHAUSTION"
    return "NEUTRAL"


def derive_order_flow_bias(snapshot: Dict[str, Any]) -> str:
    cvd = snapshot.get("cvd", {})

    direction   = cvd.get("cvd_direction", "NEUTRAL")
    div_large   = cvd.get("divergence", {}).get("large",  "NONE")
    div_medium  = cvd.get("divergence", {}).get("medium", "NONE")
    absorption  = cvd.get("absorption", {}).get("type", "NONE")
    cost_state  = cvd.get("cost", {}).get("cost_state", "NORMAL")

    if div_large != "NONE":
        return f"DIVERGENCE_LARGE_{div_large}"
    if div_medium != "NONE":
        return f"DIVERGENCE_MEDIUM_{div_medium}"
    if absorption != "NONE":
        return absorption
    if direction == "BUYING" and cost_state in ("VERY_HIGH", "HIGH"):
        return "STRONG_BUYING"
    if direction == "SELLING" and cost_state in ("VERY_HIGH", "HIGH"):
        return "STRONG_SELLING"
    return direction


def derive_volatility_bias(snapshot: Dict[str, Any]) -> str:
    atr = snapshot.get("atr", {})

    state       = atr.get("volatility_state", "NORMAL")
    compression = atr.get("compression", False)
    expansion   = atr.get("expansion", False)
    rank        = atr.get("percentile_rank", 50.0)

    if state == "HIGH" and expansion:
        return "HIGH_EXPANDING"
    if state == "HIGH" and not expansion:
        return "HIGH_SPIKE"
    if state == "LOW" and compression:
        return "LOW_COMPRESSING"
    if state == "LOW":
        return "LOW_FLAT"
    if rank > 90:
        return "EXTREME_HIGH"
    if rank < 10:
        return "EXTREME_LOW"
    return "NORMAL"


# ─────────────────────────────────────────────────────────────
# VALIDATION / FAILSAFE
# ─────────────────────────────────────────────────────────────

REQUIRED_INDICATOR_KEYS = {
    "pavp":        ["poc", "vah", "val", "value_area_position"],
    "zigzag":      ["structure", "bos_signal", "last_swing_high", "last_swing_low"],
    "macd":        ["macd_line", "histogram", "histogram_state"],
    "trend_speed": ["direction", "regime", "wave_analysis"],
    "cvd":         ["cvd_direction", "divergence", "aggression"],
    "atr":         ["atr_value", "volatility_state"]
}


def validate_snapshot(snapshot: Dict[str, Any]) -> Tuple[bool, str]:
    """
    Validates that all required indicator fields are present.
    Returns (is_valid, reason_if_invalid).
    """
    for indicator, fields in REQUIRED_INDICATOR_KEYS.items():
        if indicator not in snapshot:
            return False, f"Missing indicator block: {indicator}"
        for field in fields:
            if field not in snapshot[indicator]:
                return False, f"Missing field: {indicator}.{field}"
    return True, "OK"


# ─────────────────────────────────────────────────────────────
# MASTER ENRICHMENT FUNCTION
# ─────────────────────────────────────────────────────────────

def enrich_signal(
    indicator_snapshot: Dict[str, Any],
    symbol:    str = "",
    timeframe: str = "",
    price:     Dict[str, float] = None,
    volume:    float = 0.0,
    allow_long:  bool = True,
    allow_short: bool = True,
    force_no_trade: bool = False
) -> Dict[str, Any]:
    """
    Takes raw indicator outputs and builds a complete
    signal_format.json payload ready for the scoring engine.

    Args:
        indicator_snapshot: Output of indicator_engine.compute_all_indicators()
        symbol:     Trading symbol string
        timeframe:  Timeframe string
        price:      Dict with open/high/low/close
        volume:     Current bar volume
        allow_long: Whether long trades are permitted
        allow_short: Whether short trades are permitted
        force_no_trade: Force NO TRADE output regardless

    Returns:
        Complete signal dict matching signal_format.json
    """
    # Validate inputs
    is_valid, reason = validate_snapshot(indicator_snapshot)
    if not is_valid:
        return _failsafe_signal(symbol, timeframe, reason)

    # Regime detection
    regime, regime_confidence = detect_regime(indicator_snapshot)

    # Bias derivation
    structure_bias  = derive_structure_bias(indicator_snapshot)
    momentum_bias   = derive_momentum_bias(indicator_snapshot)
    order_flow_bias = derive_order_flow_bias(indicator_snapshot)
    volatility_bias = derive_volatility_bias(indicator_snapshot)

    return {
        "signal_id": str(uuid.uuid4())[:8],
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "symbol":    symbol,
        "timeframe": timeframe,

        "market_snapshot": {
            "price":  price or {"open": 0.0, "high": 0.0, "low": 0.0, "close": 0.0},
            "volume": volume
        },

        "indicator_snapshot": indicator_snapshot,

        "derived_context": {
            "regime":          regime,
            "structure_bias":  structure_bias,
            "momentum_bias":   momentum_bias,
            "order_flow_bias": order_flow_bias,
            "volatility_bias": volatility_bias
        },

        "execution_input": {
            "allow_long":     allow_long,
            "allow_short":    allow_short,
            "force_no_trade": force_no_trade
        }
    }


def _failsafe_signal(symbol: str, timeframe: str, reason: str) -> Dict[str, Any]:
    """Returns a NO TRADE failsafe when validation fails."""
    return {
        "signal_id": str(uuid.uuid4())[:8],
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "symbol":    symbol,
        "timeframe": timeframe,
        "failsafe":  True,
        "failsafe_reason": reason,
        "market_snapshot": {"price": {}, "volume": 0.0},
        "indicator_snapshot": {},
        "derived_context": {
            "regime":          "UNCERTAIN",
            "structure_bias":  "NEUTRAL",
            "momentum_bias":   "NEUTRAL",
            "order_flow_bias": "NEUTRAL",
            "volatility_bias": "NORMAL"
        },
        "execution_input": {
            "allow_long":     False,
            "allow_short":    False,
            "force_no_trade": True
        }
    }
