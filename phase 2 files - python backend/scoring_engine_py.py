"""
scoring_engine_py.py  — V3
Changes vs V2:
  1. ATR removed from scoring — context tag only (classify_atr_context)
  2. Trend Speed as primary entry gate; MACD veto-only (no positive points)
  3. move_ratio < 0.5 -> +8 (leading signal); > 1.8 -> -10 hard penalty
  4. score_location returns 3-tuple (score, reasons, pavp_override)
  5. EXHAUSTION hard block before any scoring
"""

from typing import Dict, Any, List, Tuple

USE_MOVE_RATIO: bool = True


# ─────────────────────────────────────────────────────────────
# ATR CONTEXT (display only — not scored)
# ─────────────────────────────────────────────────────────────

def classify_atr_context(snapshot: Dict[str, Any]) -> str:
    atr = snapshot.get("atr", {})
    if atr.get("expansion",        False): return "EXPANDING"
    if atr.get("compression",      False): return "COMPRESSING"
    if atr.get("volatility_state", "NORMAL") == "HIGH": return "ELEVATED"
    return "NORMAL"


# ─────────────────────────────────────────────────────────────
# LAYER 1 — STRUCTURE  (ZigZag + PAVP + TS gate)
# ─────────────────────────────────────────────────────────────

def score_structure(snapshot: Dict[str, Any], bias: str) -> Tuple[float, List[str]]:
    zz   = snapshot.get("zigzag", {})
    pavp = snapshot.get("pavp",   {})

    structure = zz.get("structure",             "NEUTRAL")
    bos       = zz.get("bos_signal",            "NONE")
    va_pos    = pavp.get("value_area_position", "INSIDE_VA")

    score   = 0.0
    reasons = []

    is_bull     = bias == "LONG"
    tgt_struct  = "BULLISH"  if is_bull else "BEARISH"
    tgt_va      = "ABOVE_VA" if is_bull else "BELOW_VA"
    tgt_bos     = "BOS_UP"   if is_bull else "BOS_DOWN"
    conf_struct = "BEARISH"  if is_bull else "BULLISH"

    if structure == tgt_struct and bos == tgt_bos and va_pos == tgt_va:
        score = 25.0;  reasons.append(f"Strong structure: {structure} + {bos} + {va_pos}")
    elif structure == tgt_struct and va_pos == tgt_va:
        score = 15.0;  reasons.append(f"Aligned structure: {structure} + {va_pos} (no BOS)")
    elif structure == tgt_struct:
        score = 5.0;   reasons.append(f"ZigZag {structure} only — PAVP not aligned")
    elif structure == "NEUTRAL":
        score = -15.0; reasons.append("Structure NEUTRAL — no directional bias")
    elif structure == conf_struct and va_pos == tgt_va:
        score = -15.0; reasons.append(f"Conflict: ZigZag {structure} vs PAVP {va_pos}")
    elif structure == conf_struct:
        score = -25.0; reasons.append(f"Strong conflict: ZigZag {structure} opposes {bias}")

    # Trend Speed entry gate
    ts        = snapshot.get("trend_speed", {})
    ts_dir    = ts.get("direction", "FLAT")
    ts_hma    = ts.get("trendspeed_hma", 0.0)
    ratio_avg = ts.get("wave_analysis", {}).get("current_ratio_avg", 0.0)
    tgt_ts    = "BULLISH" if is_bull else "BEARISH"
    conf_ts   = "BEARISH" if is_bull else "BULLISH"

    if (ts_dir == tgt_ts and ratio_avg > 0.8
            and ((is_bull and ts_hma > 0) or (not is_bull and ts_hma < 0))):
        score += 5.0
        reasons.append(f"Trend Speed {tgt_ts} accelerating ({ratio_avg:.2f}x) — gate confirmed")

    if ts_dir == conf_ts:
        score -= 15.0
        reasons.append(f"Trend Speed {ts_dir} conflicts with {bias} — hard penalty")

    return score, reasons


# ─────────────────────────────────────────────────────────────
# LAYER 2 — LOCATION  (PAVP)  — 3-tuple return
# ─────────────────────────────────────────────────────────────

def score_location(
    snapshot: Dict[str, Any], bias: str
) -> Tuple[float, List[str], bool]:
    pavp = snapshot.get("pavp", {})
    cvd  = snapshot.get("cvd",  {})
    ts   = snapshot.get("trend_speed", {})

    va_pos       = pavp.get("value_area_position", "INSIDE_VA")
    poc_dist_pct = abs(pavp.get("poc_distance_pct", 1.0))
    va_width_pct = pavp.get("value_area_width_pct", 50.0)
    poc_dist_raw = pavp.get("poc_distance_pct", 1.0)

    score        = 0.0
    reasons      = []
    pavp_override = False

    tgt_va = "ABOVE_VA" if bias == "LONG" else "BELOW_VA"

    if va_pos == tgt_va:
        if poc_dist_pct < 0.2:
            score = 10.0; reasons.append(f"Clean VA boundary location ({va_pos})")
        elif poc_dist_pct > 0.5:
            score = 7.0;  reasons.append(f"Accepted outside VA ({va_pos})")
        else:
            score = 5.0;  reasons.append(f"Outside VA ({va_pos})")
    elif va_pos == "INSIDE_VA":
        score = -10.0; reasons.append("Inside Value Area — chop zone")

    if abs(poc_dist_raw) < 0.1:
        score -= 3.0; reasons.append("At POC — magnet zone, mean-reversion risk")

    if va_width_pct > 70:
        score -= 2.0; reasons.append(f"Wide VA ({va_width_pct:.1f}%) — extended range")

    # PAVP override: non-ideal location rescued by CVD div + TS acceleration
    if score <= 0:
        conf_div  = "BULLISH" if bias == "LONG" else "BEARISH"
        div       = cvd.get("divergence", {})
        has_cvd   = any(div.get(k, "NONE") == conf_div for k in ("small", "medium", "large"))
        tgt_ts    = "BULLISH" if bias == "LONG" else "BEARISH"
        ts_accel  = (ts.get("direction", "FLAT") == tgt_ts
                     and ts.get("wave_analysis", {}).get("current_ratio_avg", 0.0) > 0.8)
        if has_cvd and ts_accel:
            score         = 3.0
            pavp_override = True
            reasons.append("PAVP override: CVD div + TS acceleration rescue non-ideal location (+3)")

    return score, reasons, pavp_override


# ─────────────────────────────────────────────────────────────
# LAYER 3 — MOMENTUM  (TS base + MACD veto-only)
# ─────────────────────────────────────────────────────────────

def score_momentum(snapshot: Dict[str, Any], bias: str) -> Tuple[float, List[str]]:
    ts   = snapshot.get("trend_speed", {})
    macd = snapshot.get("macd",        {})
    wave = ts.get("wave_analysis",     {})

    ts_dir    = ts.get("direction",     "FLAT")
    ts_regime = ts.get("regime",        "NORMAL")
    price_ema = ts.get("price_vs_ema",  "CROSSING")
    dominance = wave.get("dominance",   "NEUTRAL")
    ratio_avg = wave.get("current_ratio_avg", 0.0)

    hist_state = macd.get("histogram_state",    "")
    zero_line  = macd.get("zero_line_position", "BELOW")
    signal_x   = macd.get("signal_cross",       "NONE")

    score   = 0.0
    reasons = []

    is_bull       = bias == "LONG"
    tgt_dir       = "BULLISH"              if is_bull else "BEARISH"
    conf_dir      = "BEARISH"              if is_bull else "BULLISH"
    tgt_hist_acc  = "BULLISH_ACCELERATING" if is_bull else "BEARISH_ACCELERATING"
    tgt_hist_dec  = "BULLISH_DECELERATING" if is_bull else "BEARISH_RECOVERING"
    tgt_cross     = "BULLISH_CROSS"        if is_bull else "BEARISH_CROSS"
    conf_cross    = "BEARISH_CROSS"        if is_bull else "BULLISH_CROSS"
    tgt_zero      = "ABOVE"                if is_bull else "BELOW"

    # Trend Speed base vote
    ts_vote = 0.0
    if ts_dir == tgt_dir:
        if   ts_regime == "EXPANSION":    ts_vote = 10.0; reasons.append(f"TS {tgt_dir} + EXPANSION")
        elif ts_regime == "NORMAL":       ts_vote =  6.0; reasons.append(f"TS {tgt_dir} + NORMAL")
        elif ts_regime == "CONSOLIDATION":ts_vote =  2.0; reasons.append(f"TS {tgt_dir} + CONSOLIDATION")
        elif ts_regime == "EXHAUSTION":   ts_vote = -5.0; reasons.append(f"TS {tgt_dir} + EXHAUSTION")
    elif ts_dir == "FLAT":
        reasons.append("TS FLAT — no conviction")
    else:
        ts_vote = -8.0; reasons.append(f"TS {ts_dir} opposes {bias}")

    if ratio_avg > 1.2: ts_vote += 2.0; reasons.append(f"Wave ratio {ratio_avg:.2f}x — accelerating")
    elif ratio_avg < 0.4: ts_vote -= 2.0; reasons.append(f"Wave ratio {ratio_avg:.2f}x — exhaustion")
    if price_ema == "CROSSING": ts_vote -= 2.0; reasons.append("EMA crossing — transition caution")
    if dominance == conf_dir:   ts_vote -= 2.0; reasons.append(f"Wave dominance {dominance} opposes {bias}")

    score += ts_vote

    # MACD veto-only: no positive contribution
    macd_mod = 0.0
    if hist_state == tgt_hist_acc:
        reasons.append(f"MACD {hist_state} — confirmed (no bonus, veto-only)")
    elif hist_state == tgt_hist_dec:
        reasons.append(f"MACD {hist_state} — weakening aligned (neutral)")
    elif hist_state in ("BEARISH_RECOVERING", "BULLISH_DECELERATING"):
        macd_mod = -3.0; reasons.append(f"MACD {hist_state} — opposing losing strength")
    else:
        macd_mod = -7.0 if zero_line != tgt_zero else -4.0
        reasons.append(f"MACD {hist_state} — disputes momentum direction")

    if signal_x == tgt_cross:
        reasons.append(f"MACD {signal_x} — confirming crossover (noted, no bonus)")
    elif signal_x == conf_cross:
        macd_mod -= 2.0; reasons.append(f"MACD {signal_x} — opposing crossover")

    score += macd_mod
    return score, reasons


# ─────────────────────────────────────────────────────────────
# LAYER 4 — ORDER FLOW  (CVD IQ)
# ─────────────────────────────────────────────────────────────

def score_order_flow(snapshot: Dict[str, Any], bias: str) -> Tuple[float, List[str]]:
    cvd  = snapshot.get("cvd", {})
    div  = cvd.get("divergence",    {})
    agg  = cvd.get("aggression",    {})
    cost = cvd.get("cost",          {})
    abs_ = cvd.get("absorption",    {})
    dim  = cvd.get("delta_implied", {})
    ma   = cvd.get("ma",            {})

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

    is_bull      = bias == "LONG"
    tgt_dir      = "BUYING"          if is_bull else "SELLING"
    tgt_abs      = "BUY_ABSORPTION"  if is_bull else "SELL_ABSORPTION"
    tgt_ma       = "ABOVE"           if is_bull else "BELOW"
    conf_div     = "BEARISH"         if is_bull else "BULLISH"

    # CVD direction
    if cvd_dir == tgt_dir:
        score += 10.0; reasons.append(f"CVD confirming {tgt_dir} pressure")
    elif cvd_dir == "NEUTRAL":
        reasons.append("CVD neutral — no order flow conviction")
    else:
        score -= 5.0; reasons.append(f"CVD {cvd_dir} opposes {bias}")

    # Divergence (highest scale)
    if   div_large == conf_div: score -= 15.0; reasons.append("LARGE CVD divergence opposing bias")
    elif div_med   == conf_div: score -= 10.0; reasons.append("MEDIUM CVD divergence opposing bias")
    elif div_small == conf_div: score -=  5.0; reasons.append("Small CVD divergence opposing bias")

    # Absorption
    if absorption == tgt_abs:
        score += 5.0; reasons.append(f"{absorption} — hidden strength confirmed")

    # Cost state
    if cost_state == "VERY_HIGH" and cvd_dir == tgt_dir:
        score += 5.0; reasons.append("Very High cost — strong participation")
    elif cost_state == "HIGH" and cvd_dir == tgt_dir:
        score += 2.0; reasons.append("High cost — solid participation")
    elif cost_state == "LOW":
        score -= 2.0; reasons.append("Low cost — weak participation")
    elif cost_state == "VERY_LOW":
        score -= 5.0; reasons.append("Very Low cost — suspicious move")

    # Move ratio (Change 3)
    if USE_MOVE_RATIO:
        if move_ratio < 0.5:
            score += 8.0
            reasons.append(f"Move ratio {move_ratio:.2f}x — hidden accumulation (+8)")
        elif move_ratio > 1.8:
            score -= 10.0
            reasons.append(f"Move ratio {move_ratio:.2f}x — severely overextended (-10)")
        elif move_ratio > 1.5:
            score -= 5.0
            reasons.append(f"Move ratio {move_ratio:.2f}x — overextended (-5)")
    else:
        if move_ratio > 1.5: score -= 5.0; reasons.append(f"Move ratio {move_ratio:.2f}x overextended")
        elif move_ratio < 0.5: score -= 3.0; reasons.append(f"Move ratio {move_ratio:.2f}x suppressed")

    # MA bias
    if ma_bias == tgt_ma:
        score += 2.0; reasons.append(f"CVD MA {ma_bias} supports {bias}")
    elif ma_bias != "CROSSING":
        score -= 2.0; reasons.append(f"CVD MA {ma_bias} opposes {bias}")

    # Imbalance
    if imbalance > 1.5 and cvd_dir == tgt_dir:
        score += 1.0; reasons.append(f"Imbalance {imbalance:.2f}x confirms {bias}")
    elif imbalance < 0.67 and cvd_dir != tgt_dir:
        score -= 1.0; reasons.append(f"Imbalance {imbalance:.2f}x opposes {bias}")

    return score, reasons


# ─────────────────────────────────────────────────────────────
# REGIME CLASSIFIER (ATR + PAVP only — no circular inputs)
# ─────────────────────────────────────────────────────────────

def classify_regime_from_context(snapshot: Dict[str, Any]) -> str:
    atr  = snapshot.get("atr",  {})
    pavp = snapshot.get("pavp", {})
    vol_state   = atr.get("volatility_state", "NORMAL")
    compression = atr.get("compression", False)
    expansion   = atr.get("expansion",   False)
    pct_rank    = atr.get("percentile_rank", 50.0)
    va_pos      = pavp.get("value_area_position", "INSIDE_VA")
    if not compression and expansion and vol_state in ("NORMAL","HIGH") and va_pos != "INSIDE_VA":
        return "BREAKOUT"
    if pct_rank > 90:
        return "EXHAUSTION"
    if va_pos == "INSIDE_VA" and vol_state in ("LOW","NORMAL"):
        return "RANGING"
    if va_pos != "INSIDE_VA" and vol_state in ("NORMAL","HIGH") and pct_rank <= 90:
        return "TRENDING"
    return "UNCERTAIN"


def determine_bias(snapshot: Dict[str, Any]) -> str:
    zz   = snapshot.get("zigzag", {})
    pavp = snapshot.get("pavp",   {})
    structure = zz.get("structure",            "NEUTRAL")
    va_pos    = pavp.get("value_area_position","INSIDE_VA")
    if structure == "BULLISH": return "LONG"
    if structure == "BEARISH": return "SHORT"
    if va_pos == "ABOVE_VA":   return "LONG"
    if va_pos == "BELOW_VA":   return "SHORT"
    return "LONG"


# ─────────────────────────────────────────────────────────────
# CALIBRATION
# ─────────────────────────────────────────────────────────────

_CALIBRATION_TABLE = {
    (75,100): (58,"A","High-alignment setup. Estimated edge ~+8% above random. Not calibrated from live data yet."),
    (60, 75): (52,"B","Moderate alignment. Edge marginal. Execution quality determines outcome."),
    (50, 60): (48,"C","Weak alignment. Near breakeven. Consider skipping."),
    (0,  50): (40,"NONE","No tradeable setup. Negative expected value."),
}

def get_calibration_note(score: float, regime: str) -> Dict[str, Any]:
    for (lo, hi), (wr, grade, note) in _CALIBRATION_TABLE.items():
        if lo <= score < hi or (hi == 100 and score == 100):
            rn = {"RANGING":"Momentum signals less reliable — location matters more.",
                  "EXHAUSTION":"Reduce position size regardless of grade.",
                  "BREAKOUT":"Momentum signals carry higher weight than location."}.get(regime,"")
            return {"win_rate_estimate":wr,"grade":grade,
                    "note":note+(" "+rn if rn else ""),"calibrated":False,"sample_size":0}
    return {"win_rate_estimate":45,"grade":"NONE",
            "note":"Score out of range — check pipeline.","calibrated":False,"sample_size":0}


# ─────────────────────────────────────────────────────────────
# GRADE / DECISION / RISK HELPERS
# ─────────────────────────────────────────────────────────────

def score_to_grade(score: float) -> str:
    if score >= 75: return "A"
    if score >= 60: return "B"
    if score >= 50: return "C"
    return "NONE"

def score_to_decision(score: float, bias: str) -> str:
    if score >= 50:
        return f"{bias} SETUP" if bias in ("LONG","SHORT") else "NO TRADE"
    return "NO TRADE"

def determine_risk_state(grade: str, regime: str, snapshot: Dict[str,Any]) -> str:
    cvd  = snapshot.get("cvd",{})
    ts   = snapshot.get("trend_speed",{})
    atr  = snapshot.get("atr",{})
    div_any    = any(v!="NONE" for v in cvd.get("divergence",{}).values())
    exhaustion = ts.get("regime") == "EXHAUSTION"
    high_atr   = atr.get("volatility_state") == "HIGH"
    uncertain  = regime == "UNCERTAIN"
    if grade == "A" and not div_any and not exhaustion and not uncertain: return "LOW"
    if grade in ("C",) or div_any or exhaustion or uncertain or high_atr: return "HIGH"
    return "MEDIUM"

def build_invalidation(bias: str, snapshot: Dict[str,Any]) -> str:
    zz   = snapshot.get("zigzag",{})
    pavp = snapshot.get("pavp",  {})
    sl   = zz.get("last_swing_low",  0.0)
    sh   = zz.get("last_swing_high", 0.0)
    val  = pavp.get("val", 0.0)
    vah  = pavp.get("vah", 0.0)
    if bias == "LONG":
        return f"Invalidated below swing low ({sl:.4f}) or VAL ({val:.4f})"
    if bias == "SHORT":
        return f"Invalidated above swing high ({sh:.4f}) or VAH ({vah:.4f})"
    return "No trade active."

def build_entry_logic(bias: str, snapshot: Dict[str,Any]) -> str:
    zz   = snapshot.get("zigzag",{})
    pavp = snapshot.get("pavp",  {})
    bos  = zz.get("bos_signal","NONE")
    poc  = pavp.get("poc",0.0)
    vah  = pavp.get("vah",0.0)
    val  = pavp.get("val",0.0)
    if bias == "LONG":
        if bos == "BOS_UP":
            return f"Enter on BOS retest or POC ({poc:.4f}) holding above VAH ({vah:.4f})"
        return f"Enter on hold above VAH ({vah:.4f}) with CVD buying"
    if bias == "SHORT":
        if bos == "BOS_DOWN":
            return f"Enter on BOS retest or POC ({poc:.4f}) holding below VAL ({val:.4f})"
        return f"Enter on hold below VAL ({val:.4f}) with CVD selling"
    return "Wait for structural clarity"


# ─────────────────────────────────────────────────────────────
# MASTER SCORING FUNCTION
# ─────────────────────────────────────────────────────────────

def score_signal(enriched_signal: Dict[str, Any]) -> Dict[str, Any]:
    if enriched_signal.get("failsafe"):
        return {
            "bias":"NO TRADE","confidence":0,"setup_grade":"NONE",
            "risk_state":"HIGH","regime":"UNCERTAIN","atr_context":"NORMAL",
            "blocked_by_exhaustion":False,"pavp_override":False,
            "score_breakdown":{"structure":0,"location":0,"momentum":0,"order_flow":0,"total":0},
            "reasoning":{"structure":"Signal validation failed",
                         "location":enriched_signal.get("failsafe_reason","Unknown error"),
                         "momentum":"N/A","order_flow":"N/A"},
            "key_conflicts":["Incomplete signal data"],
            "entry_logic":"No trade. Fix data pipeline.","invalidation":"N/A",
            "calibration":get_calibration_note(0.0,"UNCERTAIN"),
        }

    snapshot   = enriched_signal.get("indicator_snapshot", {})
    context    = enriched_signal.get("derived_context",    {})
    exec_input = enriched_signal.get("execution_input",    {})

    regime_ctx = context.get("regime","")
    regime = regime_ctx if (regime_ctx and regime_ctx != "UNCERTAIN") \
             else classify_regime_from_context(snapshot)

    allow_long  = exec_input.get("allow_long",    True)
    allow_short = exec_input.get("allow_short",   True)
    force_nt    = exec_input.get("force_no_trade", False)

    atr_context = classify_atr_context(snapshot)

    # EXHAUSTION hard block
    _ts     = snapshot.get("trend_speed", {})
    _ratio  = _ts.get("wave_analysis", {}).get("current_ratio_avg", 0.0)
    _regime = _ts.get("regime", "NORMAL")
    if not force_nt and _regime == "EXHAUSTION" and 0.0 < _ratio < 0.4:
        return {
            "bias":"NO TRADE","confidence":0.0,"setup_grade":"NONE",
            "risk_state":"HIGH","regime":regime,"atr_context":atr_context,
            "blocked_by_exhaustion":True,"pavp_override":False,
            "score_breakdown":{"structure":0,"location":0,"momentum":0,"order_flow":0,"total":0},
            "reasoning":{"structure":"Blocked — TS EXHAUSTION","location":"N/A",
                         "momentum":f"TS EXHAUSTION (ratio {_ratio:.2f}x < 0.4)","order_flow":"N/A"},
            "key_conflicts":[f"TS EXHAUSTION ratio {_ratio:.2f}x — trade blocked"],
            "entry_logic":"Wait for TS to rebuild above 0.4x average","invalidation":"N/A",
            "calibration":get_calibration_note(0.0, regime),
        }

    base = 40.0 if regime == "UNCERTAIN" else 50.0
    bias = determine_bias(snapshot)
    if bias == "LONG"  and not allow_long:  bias = "SHORT"
    if bias == "SHORT" and not allow_short: bias = "LONG"

    s_struct, r_struct              = score_structure(snapshot, bias)
    s_loc,    r_loc,  pavp_override = score_location(snapshot, bias)
    s_mom,    r_mom                 = score_momentum(snapshot, bias)
    s_flow,   r_flow                = score_order_flow(snapshot, bias)

    total_raw = base + s_struct + s_loc + s_mom + s_flow
    total     = max(0.0, min(100.0, total_raw))

    if force_nt: total = 0.0
    if regime == "UNCERTAIN" and total < 80: total = min(total, 45.0)

    grade    = score_to_grade(total)
    decision = score_to_decision(total, bias) if not force_nt else "NO TRADE"
    risk     = determine_risk_state(grade, regime, snapshot)
    calib    = get_calibration_note(total, regime)

    conflicts = []
    cvd_dir   = snapshot.get("cvd",        {}).get("cvd_direction",   "NEUTRAL")
    ts_dir    = snapshot.get("trend_speed", {}).get("direction",       "FLAT")
    zz_struct = snapshot.get("zigzag",      {}).get("structure",       "NEUTRAL")
    hist_st   = snapshot.get("macd",        {}).get("histogram_state", "")
    div_large = snapshot.get("cvd",         {}).get("divergence",      {}).get("large","NONE")

    if bias == "LONG":
        if cvd_dir   == "SELLING":              conflicts.append("CVD SELLING vs LONG")
        if ts_dir    == "BEARISH":              conflicts.append("TS BEARISH vs LONG")
        if zz_struct == "BEARISH":              conflicts.append("ZigZag BEARISH vs LONG")
        if hist_st   == "BEARISH_ACCELERATING": conflicts.append("MACD BEARISH_ACCELERATING vs LONG")
        if div_large == "BEARISH":              conflicts.append("LARGE bearish CVD divergence")
    else:
        if cvd_dir   == "BUYING":              conflicts.append("CVD BUYING vs SHORT")
        if ts_dir    == "BULLISH":             conflicts.append("TS BULLISH vs SHORT")
        if zz_struct == "BULLISH":             conflicts.append("ZigZag BULLISH vs SHORT")
        if hist_st   == "BULLISH_ACCELERATING":conflicts.append("MACD BULLISH_ACCELERATING vs SHORT")
        if div_large == "BULLISH":             conflicts.append("LARGE bullish CVD divergence")

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
        "atr_context": atr_context,
        "blocked_by_exhaustion": False,
        "pavp_override": pavp_override,
        "score_breakdown": {
            "structure":  round(s_struct, 1),
            "location":   round(s_loc,    1),
            "momentum":   round(s_mom,    1),
            "order_flow": round(s_flow,   1),
            "total":      round(total,    1),
        },
        "reasoning": {
            "structure":  " | ".join(r_struct) or "No structural signal",
            "location":   " | ".join(r_loc)    or "No location signal",
            "momentum":   " | ".join(r_mom)    or "No momentum signal",
            "order_flow": " | ".join(r_flow)   or "No order flow signal",
        },
        "key_conflicts": conflicts if conflicts else ["None — signals aligned"],
        "entry_logic":   build_entry_logic(bias, snapshot),
        "invalidation":  build_invalidation(bias, snapshot),
        "calibration":   calib,
    }
