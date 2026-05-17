"""
scoring_engine.py
═══════════════════════════════════════════════════════════
Implements the full 0–100 scoring pipeline from
scoring_engine.txt and execution_engine.txt.

Input:  enriched signal dict (output of signal_enrichment.py)
Output: trade decision dict matching output_schema.txt
═══════════════════════════════════════════════════════════
"""

from typing import Dict, Any, List, Tuple


# ─────────────────────────────────────────────────────────────
# LAYER 1 — STRUCTURE (ZigZag + PAVP)
# ─────────────────────────────────────────────────────────────

def score_structure(snapshot: Dict[str, Any], bias: str) -> Tuple[float, List[str]]:
    """
    Returns (score_delta, reasons).
    bias: "LONG" or "SHORT"
    """
    zz   = snapshot.get("zigzag", {})
    pavp = snapshot.get("pavp", {})

    structure = zz.get("structure", "NEUTRAL")
    bos       = zz.get("bos_signal", "NONE")
    va_pos    = pavp.get("value_area_position", "INSIDE_VA")

    score   = 0.0
    reasons = []

    is_bullish_bias = bias == "LONG"
    target_struct   = "BULLISH" if is_bullish_bias else "BEARISH"
    target_va       = "ABOVE_VA" if is_bullish_bias else "BELOW_VA"
    target_bos      = "BOS_UP"   if is_bullish_bias else "BOS_DOWN"
    conflict_struct = "BEARISH"  if is_bullish_bias else "BULLISH"

    if structure == target_struct and bos == target_bos and va_pos == target_va:
        score = 25.0
        reasons.append(f"Strong structure: {structure} + {bos} + {va_pos}")

    elif structure == target_struct and va_pos == target_va:
        score = 15.0
        reasons.append(f"Aligned structure: {structure} + {va_pos} (no BOS)")

    elif structure == target_struct:
        score = 5.0
        reasons.append(f"ZigZag {structure} structure only — PAVP not aligned")

    elif structure == "NEUTRAL":
        score = -15.0
        reasons.append("Structure NEUTRAL — no directional bias")

    elif structure == conflict_struct and va_pos == target_va:
        score = -15.0
        reasons.append(f"Conflict: ZigZag {structure} vs PAVP {va_pos}")

    elif structure == conflict_struct:
        score = -25.0
        reasons.append(f"Strong conflict: ZigZag {structure} opposes {bias} bias")

    return score, reasons


# ─────────────────────────────────────────────────────────────
# LAYER 2 — LOCATION (PAVP)
# ─────────────────────────────────────────────────────────────

def score_location(snapshot: Dict[str, Any], bias: str) -> Tuple[float, List[str]]:
    pavp = snapshot.get("pavp", {})

    va_pos       = pavp.get("value_area_position", "INSIDE_VA")
    poc_dist_pct = abs(pavp.get("poc_distance_pct", 1.0))
    va_width_pct = pavp.get("value_area_width_pct", 50.0)

    score   = 0.0
    reasons = []

    target_va = "ABOVE_VA" if bias == "LONG" else "BELOW_VA"

    if va_pos == target_va:
        if poc_dist_pct < 0.2:
            score = 10.0
            reasons.append(f"Price near VA boundary — clean location ({va_pos})")
        elif poc_dist_pct > 0.5:
            score = 7.0
            reasons.append(f"Price accepted outside VA ({va_pos})")
        else:
            score = 5.0
            reasons.append(f"Price outside VA ({va_pos})")
    elif va_pos == "INSIDE_VA":
        score = -10.0
        reasons.append("Price inside Value Area — chop zone")

    # POC magnet penalty
    poc_dist = pavp.get("poc_distance_pct", 1.0)
    if abs(poc_dist) < 0.1:
        score -= 3.0
        reasons.append("Price at POC — magnet zone, mean-reversion risk")

    # Wide VA penalty
    if va_width_pct > 70:
        score -= 2.0
        reasons.append(f"Wide Value Area ({va_width_pct:.1f}%) — extended range")

    return score, reasons


# ─────────────────────────────────────────────────────────────
# LAYER 3 — MOMENTUM (Trend Speed + MACD)
# ─────────────────────────────────────────────────────────────

def score_momentum(snapshot: Dict[str, Any], bias: str) -> Tuple[float, List[str]]:
    ts   = snapshot.get("trend_speed", {})
    macd = snapshot.get("macd", {})
    wave = ts.get("wave_analysis", {})

    ts_dir     = ts.get("direction", "FLAT")
    ts_regime  = ts.get("regime", "NORMAL")
    price_ema  = ts.get("price_vs_ema", "CROSSING")
    dominance  = wave.get("dominance", "NEUTRAL")
    ratio_avg  = wave.get("current_ratio_avg", 0.0)
    ratio_max  = wave.get("current_ratio_max", 0.0)

    hist_state = macd.get("histogram_state", "")
    zero_line  = macd.get("zero_line_position", "BELOW")
    signal_x   = macd.get("signal_cross", "NONE")

    score   = 0.0
    reasons = []

    target_dir      = "BULLISH" if bias == "LONG" else "BEARISH"
    conflict_dir    = "BEARISH" if bias == "LONG" else "BULLISH"
    target_hist_acc = "BULLISH_ACCELERATING" if bias == "LONG" else "BEARISH_ACCELERATING"
    target_hist_dec = "BULLISH_DECELERATING" if bias == "LONG" else "BEARISH_RECOVERING"
    target_cross    = "BULLISH_CROSS"        if bias == "LONG" else "BEARISH_CROSS"
    conflict_cross  = "BEARISH_CROSS"        if bias == "LONG" else "BULLISH_CROSS"
    target_zero     = "ABOVE"               if bias == "LONG" else "BELOW"

    # --- Trend Speed ---
    if ts_dir == target_dir:
        if ts_regime == "EXPANSION":
            score += 10.0
            reasons.append(f"Trend Speed {target_dir} + EXPANSION regime")
        elif ts_regime == "NORMAL":
            score += 5.0
            reasons.append(f"Trend Speed {target_dir} + NORMAL regime")
        elif ts_regime == "CONSOLIDATION":
            score += 2.0
            reasons.append(f"Trend Speed {target_dir} but CONSOLIDATION")
        elif ts_regime == "EXHAUSTION":
            score -= 5.0
            reasons.append(f"Trend Speed {target_dir} but EXHAUSTION — caution")

    elif ts_dir == "FLAT":
        reasons.append("Trend Speed FLAT — no directional conviction")

    else:  # Opposing direction
        score -= 5.0
        reasons.append(f"Trend Speed {ts_dir} opposes {bias} bias")

    # Wave ratio modifiers
    if ratio_avg > 1.2:
        score += 3.0
        reasons.append(f"Wave ratio {ratio_avg:.2f}x — accelerating beyond average")
    elif ratio_avg < 0.4:
        score -= 3.0
        reasons.append(f"Wave ratio {ratio_avg:.2f}x — exhaustion warning")

    if ratio_max > 1.0:
        score += 2.0
        reasons.append("Current wave exceeding historical max — breakout momentum")

    # EMA crossing penalty
    if price_ema == "CROSSING":
        score -= 3.0
        reasons.append("Price crossing dynamic EMA — transition, avoid entry")

    # Opposing dominance
    if dominance == conflict_dir:
        score -= 3.0
        reasons.append(f"Wave dominance {dominance} opposes {bias} bias")

    # --- MACD ---
    if hist_state == target_hist_acc:
        if zero_line == target_zero:
            score += 10.0
            reasons.append(f"MACD {hist_state} above zero — full momentum confirm")
        else:
            score += 6.0
            reasons.append(f"MACD {hist_state} below zero — building momentum")

    elif hist_state == target_hist_dec:
        if zero_line == target_zero:
            score += 3.0
            reasons.append(f"MACD {hist_state} — weakening, caution")
        else:
            score += 1.0
            reasons.append(f"MACD {hist_state} below zero — minimal support")

    elif hist_state in ("BEARISH_RECOVERING", "BULLISH_DECELERATING"):
        score -= 2.0
        reasons.append(f"MACD {hist_state} — opposing momentum weakening")

    else:  # Full opposing acceleration
        if zero_line != target_zero:
            score -= 7.0
            reasons.append(f"MACD {hist_state} — strong momentum opposition")
        else:
            score -= 4.0
            reasons.append(f"MACD {hist_state} above zero — moderate opposition")

    # Signal cross bonus/penalty
    if signal_x == target_cross:
        score += 3.0
        reasons.append(f"MACD {signal_x} — fresh entry signal")
    elif signal_x == conflict_cross:
        score -= 3.0
        reasons.append(f"MACD {signal_x} — opposing crossover")

    return score, reasons


# ─────────────────────────────────────────────────────────────
# LAYER 4 — ORDER FLOW (CVD IQ)
# ─────────────────────────────────────────────────────────────

def score_order_flow(snapshot: Dict[str, Any], bias: str) -> Tuple[float, List[str]]:
    cvd  = snapshot.get("cvd", {})
    div  = cvd.get("divergence",   {})
    agg  = cvd.get("aggression",   {})
    cost = cvd.get("cost",         {})
    abs_ = cvd.get("absorption",   {})
    dim  = cvd.get("delta_implied", {})
    ma   = cvd.get("ma",           {})

    cvd_dir    = cvd.get("cvd_direction", "NEUTRAL")
    div_small  = div.get("small",  "NONE")
    div_med    = div.get("medium", "NONE")
    div_large  = div.get("large",  "NONE")
    absorption = abs_.get("type", "NONE")
    cost_state = cost.get("cost_state", "NORMAL")
    move_ratio = dim.get("move_ratio", 1.0)
    imbalance  = agg.get("imbalance_ratio", 1.0)
    ma_bias    = ma.get("ma_bias", "CROSSING")

    score   = 0.0
    reasons = []

    target_dir    = "BUYING"       if bias == "LONG" else "SELLING"
    target_abs    = "BUY_ABSORPTION" if bias == "LONG" else "SELL_ABSORPTION"
    target_ma     = "ABOVE"        if bias == "LONG" else "BELOW"
    conflict_div  = "BEARISH"      if bias == "LONG" else "BULLISH"

    # CVD direction
    if cvd_dir == target_dir:
        score += 10.0
        reasons.append(f"CVD confirming {target_dir} pressure")
    elif cvd_dir == "NEUTRAL":
        reasons.append("CVD neutral — no order flow conviction")
    else:
        score -= 5.0
        reasons.append(f"CVD {cvd_dir} opposes {bias} bias")

    # Divergence (only apply highest active scale)
    if div_large == conflict_div:
        score -= 15.0
        reasons.append("LARGE CVD divergence opposing bias — high-risk warning")
    elif div_med == conflict_div:
        score -= 10.0
        reasons.append("MEDIUM CVD divergence opposing bias")
    elif div_small == conflict_div:
        score -= 5.0
        reasons.append("Small CVD divergence opposing bias")

    # Absorption
    if absorption == target_abs:
        score += 5.0
        reasons.append(f"{absorption} detected — hidden strength confirmed")

    # Cost state
    if cost_state == "VERY_HIGH" and cvd_dir == target_dir:
        score += 5.0
        reasons.append("Very High cost — price moving on strong participation")
    elif cost_state == "HIGH" and cvd_dir == target_dir:
        score += 2.0
        reasons.append("High cost — solid participation")
    elif cost_state == "LOW":
        score -= 2.0
        reasons.append("Low cost — weak participation")
    elif cost_state == "VERY_LOW":
        score -= 5.0
        reasons.append("Very Low cost — suspicious/thin move")

    # Move ratio
    if move_ratio > 1.5:
        score -= 5.0
        reasons.append(f"Move ratio {move_ratio:.2f}x — price overextended vs delta")
    elif move_ratio < 0.5:
        score -= 3.0
        reasons.append(f"Move ratio {move_ratio:.2f}x — price suppressed vs delta")

    # MA bias
    if ma_bias == target_ma:
        score += 2.0
        reasons.append(f"CVD MA bias {ma_bias} — supports {bias}")
    elif ma_bias == "CROSSING":
        pass  # neutral
    else:
        score -= 2.0
        reasons.append(f"CVD MA bias {ma_bias} — opposes {bias}")

    # Imbalance ratio
    if imbalance > 1.5 and cvd_dir == target_dir:
        score += 1.0
        reasons.append(f"Buy/sell imbalance {imbalance:.2f}x — confirms {bias}")
    elif imbalance < 0.67 and cvd_dir != target_dir:
        score -= 1.0
        reasons.append(f"Imbalance {imbalance:.2f}x — opposes {bias}")

    return score, reasons


# ─────────────────────────────────────────────────────────────
# LAYER 5 — VOLATILITY (ATR — filter only)
# ─────────────────────────────────────────────────────────────

def score_volatility(snapshot: Dict[str, Any], regime: str) -> Tuple[float, List[str]]:
    atr = snapshot.get("atr", {})

    vol_state   = atr.get("volatility_state", "NORMAL")
    compression = atr.get("compression", False)
    expansion   = atr.get("expansion", False)
    pct_rank    = atr.get("percentile_rank", 50.0)

    score   = 0.0
    reasons = []
    notes   = []

    if vol_state == "HIGH":
        if expansion:
            notes.append("ATR HIGH + expanding — valid momentum environment, widen stops")
        else:
            score -= 3.0
            reasons.append("ATR HIGH spike without expansion — avoid late entry")

    elif vol_state == "LOW":
        if compression:
            notes.append("ATR LOW + compressing — breakout readiness forming")
            if regime == "BREAKOUT":
                score -= 10.0
                reasons.append("LOW ATR in breakout context — high fakeout risk")
        else:
            score -= 5.0
            reasons.append("ATR LOW flat — dead market, avoid breakouts")

    # Extreme percentile penalties
    if pct_rank > 90:
        score -= 5.0
        reasons.append(f"ATR percentile {pct_rank:.0f}% — extreme volatility spike")
    elif pct_rank < 10:
        score -= 5.0
        reasons.append(f"ATR percentile {pct_rank:.0f}% — dead market")

    if expansion:
        notes.append("ATR expanding — momentum environment confirmed")

    all_reasons = reasons + notes
    return score, all_reasons


# ─────────────────────────────────────────────────────────────
# FINAL DECISION
# ─────────────────────────────────────────────────────────────

def determine_bias(snapshot: Dict[str, Any]) -> str:
    """Determine primary bias direction from structure."""
    zz   = snapshot.get("zigzag", {})
    pavp = snapshot.get("pavp", {})

    structure = zz.get("structure", "NEUTRAL")
    va_pos    = pavp.get("value_area_position", "INSIDE_VA")

    if structure == "BULLISH":
        return "LONG"
    if structure == "BEARISH":
        return "SHORT"
    # Fallback to VA position
    if va_pos == "ABOVE_VA":
        return "LONG"
    if va_pos == "BELOW_VA":
        return "SHORT"
    return "LONG"  # default for scoring — will likely result in NO TRADE


def score_to_grade(score: float) -> str:
    if score >= 75:
        return "A"
    if score >= 60:
        return "B"
    if score >= 50:
        return "C"
    return "NONE"


def score_to_decision(score: float, bias: str) -> str:
    if score >= 50:
        return f"{bias} SETUP" if bias in ("LONG", "SHORT") else "NO TRADE"
    return "NO TRADE"


def determine_risk_state(
    grade: str,
    regime: str,
    snapshot: Dict[str, Any]
) -> str:
    cvd      = snapshot.get("cvd", {})
    ts       = snapshot.get("trend_speed", {})
    atr      = snapshot.get("atr", {})
    div_any  = any(
        v != "NONE" for v in cvd.get("divergence", {}).values()
    )
    exhaustion = ts.get("regime") == "EXHAUSTION"
    high_atr   = atr.get("volatility_state") == "HIGH"
    uncertain  = regime == "UNCERTAIN"

    if grade == "A" and not div_any and not exhaustion and not uncertain:
        return "LOW"
    if grade in ("C",) or div_any or exhaustion or uncertain or high_atr:
        return "HIGH"
    return "MEDIUM"


def build_invalidation(bias: str, snapshot: Dict[str, Any]) -> str:
    zz   = snapshot.get("zigzag", {})
    pavp = snapshot.get("pavp", {})

    swing_low  = zz.get("last_swing_low", 0.0)
    swing_high = zz.get("last_swing_high", 0.0)
    val        = pavp.get("val", 0.0)
    vah        = pavp.get("vah", 0.0)

    if bias == "LONG":
        return (
            f"Invalidated if price closes below last swing low "
            f"({swing_low:.4f}) or breaks below VAL ({val:.4f})"
        )
    if bias == "SHORT":
        return (
            f"Invalidated if price closes above last swing high "
            f"({swing_high:.4f}) or reclaims VAH ({vah:.4f})"
        )
    return "No trade active. Re-evaluate when structure clarifies."


def build_entry_logic(bias: str, snapshot: Dict[str, Any]) -> str:
    zz   = snapshot.get("zigzag", {})
    pavp = snapshot.get("pavp", {})
    bos  = zz.get("bos_signal", "NONE")
    poc  = pavp.get("poc", 0.0)
    vah  = pavp.get("vah", 0.0)
    val  = pavp.get("val", 0.0)

    if bias == "LONG":
        if bos == "BOS_UP":
            return f"Enter on retest of BOS level or POC ({poc:.4f}) holding above VAH ({vah:.4f})"
        return f"Enter on confirmed hold above VAH ({vah:.4f}) with CVD buying confirmation"
    if bias == "SHORT":
        if bos == "BOS_DOWN":
            return f"Enter on retest of BOS level or POC ({poc:.4f}) holding below VAL ({val:.4f})"
        return f"Enter on confirmed hold below VAL ({val:.4f}) with CVD selling confirmation"
    return "Wait for structural clarity before entry"


# ─────────────────────────────────────────────────────────────
# MASTER SCORING FUNCTION
# ─────────────────────────────────────────────────────────────

def score_signal(enriched_signal: Dict[str, Any]) -> Dict[str, Any]:
    """
    Runs the full scoring pipeline on an enriched signal.

    Args:
        enriched_signal: Output of signal_enrichment.enrich_signal()

    Returns:
        Trade decision dict matching output_schema.txt
    """
    # Failsafe pass-through
    if enriched_signal.get("failsafe"):
        return {
            "bias":        "NO TRADE",
            "confidence":  0,
            "setup_grade": "NONE",
            "risk_state":  "HIGH",
            "regime":      "UNCERTAIN",
            "score_breakdown": {
                "structure": 0, "location": 0, "momentum": 0,
                "order_flow": 0, "volatility": 0, "total": 0
            },
            "reasoning": {
                "structure":  "Signal validation failed",
                "location":   enriched_signal.get("failsafe_reason", "Unknown error"),
                "momentum":   "N/A",
                "order_flow": "N/A",
                "volatility": "N/A"
            },
            "key_conflicts":  ["Incomplete signal data"],
            "entry_logic":    "No trade. Fix data pipeline.",
            "invalidation":   "N/A"
        }

    snapshot    = enriched_signal.get("indicator_snapshot", {})
    context     = enriched_signal.get("derived_context", {})
    exec_input  = enriched_signal.get("execution_input", {})

    regime      = context.get("regime", "UNCERTAIN")
    allow_long  = exec_input.get("allow_long", True)
    allow_short = exec_input.get("allow_short", True)
    force_nt    = exec_input.get("force_no_trade", False)

    # Base score — penalise uncertain regime
    base = 40.0 if regime == "UNCERTAIN" else 50.0

    # Determine scoring direction
    bias = determine_bias(snapshot)
    if bias == "LONG" and not allow_long:
        bias = "SHORT"
    if bias == "SHORT" and not allow_short:
        bias = "LONG"

    # Run all layers
    s_struct,  r_struct  = score_structure(snapshot, bias)
    s_loc,     r_loc     = score_location(snapshot, bias)
    s_mom,     r_mom     = score_momentum(snapshot, bias)
    s_flow,    r_flow    = score_order_flow(snapshot, bias)
    s_vol,     r_vol     = score_volatility(snapshot, regime)

    total_raw = base + s_struct + s_loc + s_mom + s_flow + s_vol
    total     = max(0.0, min(100.0, total_raw))

    # Force no trade override
    if force_nt:
        total = 0.0

    # Uncertain regime requires 80+ to trade
    if regime == "UNCERTAIN" and total < 80:
        total = min(total, 45.0)

    grade    = score_to_grade(total)
    decision = score_to_decision(total, bias) if not force_nt else "NO TRADE"
    risk     = determine_risk_state(grade, regime, snapshot)

    # Key conflicts
    conflicts = []
    cvd_dir   = snapshot.get("cvd", {}).get("cvd_direction", "NEUTRAL")
    ts_dir    = snapshot.get("trend_speed", {}).get("direction", "FLAT")
    zz_struct = snapshot.get("zigzag", {}).get("structure", "NEUTRAL")
    hist_st   = snapshot.get("macd", {}).get("histogram_state", "")
    div_large = snapshot.get("cvd", {}).get("divergence", {}).get("large", "NONE")

    if bias == "LONG":
        if cvd_dir == "SELLING":
            conflicts.append("CVD showing SELLING pressure vs LONG bias")
        if ts_dir == "BEARISH":
            conflicts.append("Trend Speed BEARISH vs LONG bias")
        if zz_struct == "BEARISH":
            conflicts.append("ZigZag structure BEARISH vs LONG bias")
        if hist_st == "BEARISH_ACCELERATING":
            conflicts.append("MACD BEARISH_ACCELERATING vs LONG bias")
        if div_large == "BEARISH":
            conflicts.append("LARGE bearish CVD divergence — high priority warning")
    else:
        if cvd_dir == "BUYING":
            conflicts.append("CVD showing BUYING pressure vs SHORT bias")
        if ts_dir == "BULLISH":
            conflicts.append("Trend Speed BULLISH vs SHORT bias")
        if zz_struct == "BULLISH":
            conflicts.append("ZigZag structure BULLISH vs SHORT bias")
        if hist_st == "BULLISH_ACCELERATING":
            conflicts.append("MACD BULLISH_ACCELERATING vs SHORT bias")
        if div_large == "BULLISH":
            conflicts.append("LARGE bullish CVD divergence — high priority warning")

    # Apply NO TRADE rule: 3+ layer conflicts
    if len(conflicts) >= 3:
        total    = min(total, 45.0)
        decision = "NO TRADE"
        grade    = "NONE"

    return {
        "bias":        decision,
        "confidence":  round(total, 1),
        "setup_grade": grade,
        "risk_state":  risk,
        "regime":      regime,

        "score_breakdown": {
            "structure":  round(s_struct, 1),
            "location":   round(s_loc,    1),
            "momentum":   round(s_mom,    1),
            "order_flow": round(s_flow,   1),
            "volatility": round(s_vol,    1),
            "total":      round(total,    1)
        },

        "reasoning": {
            "structure":  " | ".join(r_struct)  or "No structural signal",
            "location":   " | ".join(r_loc)     or "No location signal",
            "momentum":   " | ".join(r_mom)     or "No momentum signal",
            "order_flow": " | ".join(r_flow)    or "No order flow signal",
            "volatility": " | ".join(r_vol)     or "ATR NORMAL — no volatility adjustment"
        },

        "key_conflicts":  conflicts if conflicts else ["None — signals aligned"],
        "entry_logic":    build_entry_logic(bias, snapshot),
        "invalidation":   build_invalidation(bias, snapshot)
    }
