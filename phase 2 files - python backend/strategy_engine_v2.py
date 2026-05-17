"""
strategy_engine_v2.py
═══════════════════════════════════════════════════════════
Rebuilt strategy engine based on actual visual trading logic.

Replaces the scoring system entirely with condition-based
pattern matching that mirrors how trades are evaluated visually.

Decision flow:
  1. CONTEXT: Are we near a key level? (VAL/VAH/POC/LVN)
  2. SIGNAL:  Has the CVD triangle sequence completed?
              (triangles appear → stop → confirming candle)
  3. FILTER:  Does Trend Speed confirm direction?
  4. ENTRY:   All three aligned = take the trade
  5. TARGETS: Level-based exits, not ATR multiples
═══════════════════════════════════════════════════════════
"""

from typing import Dict, Any, List, Optional, Tuple


# ─────────────────────────────────────────────────────────────
# PROXIMITY HELPERS
# ─────────────────────────────────────────────────────────────

def _near(price: float, level: float, pct: float = 0.003) -> bool:
    """True if price is within pct% of level."""
    if level <= 0:
        return False
    return abs(price - level) / level <= pct


def _at_or_below(price: float, level: float, pct: float = 0.004) -> bool:
    """True if price has touched or gone slightly below level."""
    if level <= 0:
        return False
    return price <= level * (1 + pct)


def _at_or_above(price: float, level: float, pct: float = 0.004) -> bool:
    """True if price has touched or gone slightly above level."""
    if level <= 0:
        return False
    return price >= level * (1 - pct)


# ─────────────────────────────────────────────────────────────
# CONTEXT EVALUATION
# ─────────────────────────────────────────────────────────────

def evaluate_location_context(
    current_price:  float,
    swing:          Dict[str, Any],
    pavp:           Dict[str, Any],
    historical_pocs: Dict[str, Any],
    direction:      str,
) -> Dict[str, Any]:
    """
    Determines whether price is at or near a key level
    that makes a trade worth watching.

    For LONG: price near VAL (bull or combined), active POC support, or in/below LVN
    For SHORT: price near VAH (bull or combined), active POC resistance, or in/above LVN

    Returns:
        at_key_level    : bool — is price at a tradeable location?
        level_type      : what kind of level
        level_price     : the actual level price
        level_strength  : 0–100, how significant is this level
        in_lvn          : is price currently inside an LVN?
        lvn_target      : the next HVN after this LVN (the acceleration target)
    """
    is_long = direction == "LONG"

    at_key   = False
    l_type   = "NONE"
    l_price  = 0.0
    l_strength = 0

    # Check all relevant levels in priority order
    # For LONG: VAL levels are entries, VAH levels are targets
    # For SHORT: VAH levels are entries, VAL levels are targets

    checks = []

    if is_long:
        checks = [
            (swing.get("bull_val", 0),  "BULL_VAL",    90),
            (swing.get("val",      0),  "SWING_VAL",   85),
            (pavp.get("val",       0),  "PAVP_VAL",    80),
            (swing.get("bear_val", 0),  "BEAR_VAL",    75),
            (swing.get("poc",      0),  "SWING_POC",   70),
            (pavp.get("poc",       0),  "PAVP_POC",    65),
        ]
    else:
        checks = [
            (swing.get("bear_vah", 0),  "BEAR_VAH",    90),
            (swing.get("vah",      0),  "SWING_VAH",   85),
            (pavp.get("vah",       0),  "PAVP_VAH",    80),
            (swing.get("bull_vah", 0),  "BULL_VAH",    75),
            (swing.get("poc",      0),  "SWING_POC",   70),
            (pavp.get("poc",       0),  "PAVP_POC",    65),
        ]

    for level, name, strength in checks:
        if level <= 0:
            continue
        if is_long and _at_or_below(current_price, level):
            at_key    = True
            l_type    = name
            l_price   = level
            l_strength = strength
            break
        elif not is_long and _at_or_above(current_price, level):
            at_key    = True
            l_type    = name
            l_price   = level
            l_strength = strength
            break

    # Check historical POCs
    if not at_key:
        hist_key = "nearest_below" if is_long else "nearest_above"
        hist_poc = historical_pocs.get(hist_key)
        if hist_poc and hist_poc.get("significance", 0) > 20:
            poc_price = hist_poc["poc"]
            if is_long and _at_or_below(current_price, poc_price, 0.005):
                at_key    = True
                l_type    = "HISTORICAL_POC"
                l_price   = poc_price
                l_strength = min(95, int(hist_poc["significance"]))
            elif not is_long and _at_or_above(current_price, poc_price, 0.005):
                at_key    = True
                l_type    = "HISTORICAL_POC"
                l_price   = poc_price
                l_strength = min(95, int(hist_poc["significance"]))

    # LVN context
    in_lvn = swing.get("in_lvn", False)
    if is_long:
        lvn_target = swing.get("next_hvn_above")
    else:
        lvn_target = swing.get("next_hvn_below")

    # If in LVN, the context is valid regardless of level proximity
    # (price is accelerating through a thin zone)
    if in_lvn and not at_key:
        at_key    = True
        l_type    = "LVN_ACCELERATION"
        l_price   = current_price
        l_strength = 85

    return {
        "at_key_level":   at_key,
        "level_type":     l_type,
        "level_price":    round(l_price, 6),
        "level_strength": l_strength,
        "in_lvn":         in_lvn,
        "lvn_target":     lvn_target,
    }


# ─────────────────────────────────────────────────────────────
# SIGNAL EVALUATION
# ─────────────────────────────────────────────────────────────

def evaluate_cvd_signal(
    cvd_triangles: Dict[str, Any],
    direction:     str,
) -> Dict[str, Any]:
    """
    Evaluates whether the CVD triangle sequence has produced
    a valid entry signal in the given direction.

    For LONG:
      - DOWN triangles appeared (bearish pressure) near VAL
      - DOWN triangles stopped
      - First GREEN candle = CONFIRMING signal

    For SHORT:
      - UP triangles appeared (bullish pressure) near VAH
      - UP triangles stopped
      - First RED candle = CONFIRMING signal

    Returns:
        signal_valid    : bool — is this a tradeable CVD signal?
        signal_state    : CONFIRMING / STOPPED / APPEARING / NONE
        signal_strength : 0–100
        sequence_length : how many triangle bars were in the sequence
    """
    primary = cvd_triangles.get("primary_signal", "NONE")
    is_long = direction == "LONG"

    # Confirming signals
    if is_long and primary == "BULLISH_REVERSAL":
        seq = cvd_triangles.get("down_sequence", {})
        return {
            "signal_valid":    True,
            "signal_state":    "CONFIRMING",
            "signal_strength": min(95, 60 + seq.get("sequence_length", 0) * 5),
            "sequence_length": seq.get("sequence_length", 0),
        }
    if not is_long and primary == "BEARISH_REVERSAL":
        seq = cvd_triangles.get("up_sequence", {})
        return {
            "signal_valid":    True,
            "signal_state":    "CONFIRMING",
            "signal_strength": min(95, 60 + seq.get("sequence_length", 0) * 5),
            "sequence_length": seq.get("sequence_length", 0),
        }

    # Setup (stopped but not yet confirmed)
    if is_long and primary == "BULLISH_SETUP":
        seq = cvd_triangles.get("down_sequence", {})
        return {
            "signal_valid":    False,
            "signal_state":    "STOPPED",
            "signal_strength": 40,
            "sequence_length": seq.get("sequence_length", 0),
        }
    if not is_long and primary == "BEARISH_SETUP":
        seq = cvd_triangles.get("up_sequence", {})
        return {
            "signal_valid":    False,
            "signal_state":    "STOPPED",
            "signal_strength": 40,
            "sequence_length": seq.get("sequence_length", 0),
        }

    # Pressure building (triangles still forming — not yet entry)
    if is_long and primary == "BEARISH_PRESSURE":
        return {"signal_valid": False, "signal_state": "APPEARING",
                "signal_strength": 20, "sequence_length": 0}
    if not is_long and primary == "BULLISH_PRESSURE":
        return {"signal_valid": False, "signal_state": "APPEARING",
                "signal_strength": 20, "sequence_length": 0}

    return {"signal_valid": False, "signal_state": "NONE",
            "signal_strength": 0, "sequence_length": 0}


# ─────────────────────────────────────────────────────────────
# TREND FILTER
# ─────────────────────────────────────────────────────────────

def evaluate_trend_filter(
    trend_speed:    Dict[str, Any],
    direction:      str,
) -> Dict[str, Any]:
    """
    Trend Speed confirmation filter.

    For LONG:  TS direction BULLISH or transitioning (CROSSING)
               TS regime not EXHAUSTION
    For SHORT: TS direction BEARISH or transitioning
               TS regime not EXHAUSTION

    Returns:
        trend_aligned  : bool
        trend_strength : 0–100
        allow_reentry  : bool (TS still alive for re-entry)
        blocking_reason: str if blocked
    """
    ts_dir    = trend_speed.get("direction",    "FLAT")
    ts_regime = trend_speed.get("regime",       "NORMAL")
    ratio_avg = trend_speed.get("wave_analysis", {}).get("current_ratio_avg", 0.0)
    price_ema = trend_speed.get("price_vs_ema", "CROSSING")
    is_long   = direction == "LONG"
    tgt_dir   = "BULLISH" if is_long else "BEARISH"
    conf_dir  = "BEARISH" if is_long else "BULLISH"

    # Hard block: exhaustion
    if ts_regime == "EXHAUSTION" and ratio_avg < 0.4:
        return {
            "trend_aligned":   False,
            "trend_strength":  0,
            "allow_reentry":   False,
            "blocking_reason": f"TS EXHAUSTION (ratio {ratio_avg:.2f}x)",
        }

    # Hard block: directly opposing trend with strong conviction
    if ts_dir == conf_dir and ts_regime == "EXPANSION":
        return {
            "trend_aligned":   False,
            "trend_strength":  10,
            "allow_reentry":   False,
            "blocking_reason": f"TS strongly {conf_dir} — opposes {direction}",
        }

    aligned  = ts_dir == tgt_dir or price_ema == "CROSSING"
    strength = 0

    if ts_dir == tgt_dir:
        if ts_regime == "EXPANSION":    strength = 90
        elif ts_regime == "NORMAL":     strength = 70
        elif ts_regime == "CONSOLIDATION": strength = 50
        elif ts_regime == "EXHAUSTION": strength = 20
    elif price_ema == "CROSSING":
        strength = 45  # transitioning — allowed but weaker
    else:
        strength = 15

    if ratio_avg > 1.2: strength = min(100, strength + 10)

    # Allow re-entry if trend still alive
    allow_reentry = (ts_dir == tgt_dir and ts_regime in ("NORMAL","EXPANSION","CONSOLIDATION"))

    return {
        "trend_aligned":   aligned,
        "trend_strength":  strength,
        "allow_reentry":   allow_reentry,
        "blocking_reason": "",
    }


# ─────────────────────────────────────────────────────────────
# MASTER DECISION ENGINE
# ─────────────────────────────────────────────────────────────

def evaluate_trade(
    current_price:   float,
    direction:       str,
    swing_profiles:  Dict[str, Any],
    pavp:            Dict[str, Any],
    historical_pocs: Dict[str, Any],
    cvd_triangles:   Dict[str, Any],
    trend_speed:     Dict[str, Any],
    exit_targets:    List[Dict[str, Any]],
    is_reentry:      bool = False,
) -> Dict[str, Any]:
    """
    Master decision function.

    Requires ALL THREE conditions:
      1. Price at a key level (VAL/VAH/POC/LVN)
      2. CVD triangle sequence completed (confirming candle)
      3. Trend Speed aligned (not exhausted, not opposing)

    For re-entry after an exit:
      Same conditions, but Trend Speed requirement relaxed
      (just needs to be alive, not necessarily expanding)

    Returns full decision dict.
    """
    # Evaluate all three conditions
    location = evaluate_location_context(
        current_price, swing_profiles, pavp, historical_pocs, direction
    )
    cvd_signal = evaluate_cvd_signal(cvd_triangles, direction)
    trend      = evaluate_trend_filter(trend_speed, direction)

    # Decision logic
    take_trade = False
    reason     = "NO TRADE"
    confidence = 0.0

    if not location["at_key_level"]:
        reason = f"Price not at key level (nearest: {location['level_type']})"

    elif not cvd_signal["signal_valid"]:
        reason = f"CVD signal not confirmed (state: {cvd_signal['signal_state']})"

    elif not trend["trend_aligned"]:
        reason = f"Trend Speed not aligned: {trend['blocking_reason']}"

    else:
        take_trade = True
        reason     = (
            f"ENTRY: {location['level_type']} + "
            f"CVD {cvd_signal['signal_state']} (seq={cvd_signal['sequence_length']}) + "
            f"TS {trend['trend_strength']}%"
        )

        # Confidence from three inputs
        loc_weight  = 0.35
        cvd_weight  = 0.40
        trend_weight = 0.25

        confidence = (
            location["level_strength"]  * loc_weight +
            cvd_signal["signal_strength"] * cvd_weight +
            trend["trend_strength"]     * trend_weight
        )
        confidence = round(min(100.0, confidence), 1)

    # Grade from confidence
    if not take_trade:
        grade = "NONE"
    elif confidence >= 75:
        grade = "A"
    elif confidence >= 60:
        grade = "B"
    else:
        grade = "C"

    # Stop loss: just below/above the key level that triggered entry
    entry_level = location["level_price"] if location["level_price"] > 0 else current_price
    stop_buffer = current_price * 0.003  # 0.3% buffer

    if direction == "LONG":
        stop_loss = round(entry_level - stop_buffer, 6)
    else:
        stop_loss = round(entry_level + stop_buffer, 6)

    # Primary target: first exit target
    primary_target = exit_targets[0]["price"] if exit_targets else round(
        current_price * (1.005 if direction == "LONG" else 0.995), 6
    )

    return {
        "take_trade":    take_trade,
        "direction":     direction,
        "confidence":    confidence,
        "grade":         grade,
        "reason":        reason,
        "is_reentry":    is_reentry,

        "location":      location,
        "cvd_signal":    cvd_signal,
        "trend":         trend,

        "entry_price":   round(current_price, 6),
        "stop_loss":     stop_loss,
        "primary_target": primary_target,
        "exit_targets":  exit_targets,

        "allow_reentry": trend["allow_reentry"],
    }


# ─────────────────────────────────────────────────────────────
# DIRECTION DETECTOR
# ─────────────────────────────────────────────────────────────

def determine_trade_direction(
    current_price:  float,
    swing_profiles: Dict[str, Any],
    pavp:           Dict[str, Any],
    cvd_triangles:  Dict[str, Any],
    trend_speed:    Dict[str, Any],
) -> str:
    """
    Determines whether to look for a LONG or SHORT setup.

    Logic:
    - If CVD primary signal is BULLISH_REVERSAL or BULLISH_SETUP → LONG
    - If CVD primary signal is BEARISH_REVERSAL or BEARISH_SETUP → SHORT
    - Otherwise use price location vs key levels
    """
    primary = cvd_triangles.get("primary_signal", "NONE")

    if primary in ("BULLISH_REVERSAL", "BULLISH_SETUP", "BULLISH_PRESSURE"):
        return "LONG"
    if primary in ("BEARISH_REVERSAL", "BEARISH_SETUP", "BEARISH_PRESSURE"):
        return "SHORT"

    # Fallback: price location
    ts_dir = trend_speed.get("direction", "FLAT")
    if ts_dir == "BULLISH":
        return "LONG"
    if ts_dir == "BEARISH":
        return "SHORT"

    va_pos = pavp.get("value_area_position", "INSIDE_VA")
    if va_pos == "BELOW_VA":
        return "LONG"
    if va_pos == "ABOVE_VA":
        return "SHORT"

    return "LONG"  # default


# ─────────────────────────────────────────────────────────────
# FULL EVALUATION ENTRY POINT
# ─────────────────────────────────────────────────────────────

def run_strategy(
    current_price:    float,
    vol_intelligence: Dict[str, Any],
    pavp:             Dict[str, Any],
    trend_speed:      Dict[str, Any],
    is_reentry:       bool = False,
) -> Dict[str, Any]:
    """
    Single entry point for the strategy engine.

    Args:
        current_price    : latest close price
        vol_intelligence : output of volume_profile_engine.compute_volume_intelligence()
        pavp             : output of indicator_engine.compute_pavp()
        trend_speed      : output of indicator_engine.compute_trend_speed()
        is_reentry       : True if evaluating a re-entry after a prior exit

    Returns:
        Complete trade decision dict.
    """
    swing   = vol_intelligence.get("swing_profiles",  {})
    tri     = vol_intelligence.get("cvd_triangles",   {"primary_signal": "NONE"})
    hist    = vol_intelligence.get("historical_pocs", {"active_pocs": []})
    et_long = vol_intelligence.get("exit_targets_long",  [])
    et_short= vol_intelligence.get("exit_targets_short", [])

    direction = determine_trade_direction(current_price, swing, pavp, tri, trend_speed)
    exit_tgts = et_long if direction == "LONG" else et_short

    decision = evaluate_trade(
        current_price   = current_price,
        direction       = direction,
        swing_profiles  = swing,
        pavp            = pavp,
        historical_pocs = hist,
        cvd_triangles   = tri,
        trend_speed     = trend_speed,
        exit_targets    = exit_tgts,
        is_reentry      = is_reentry,
    )

    return decision
