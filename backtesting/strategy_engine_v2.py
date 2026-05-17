"""
strategy_engine_v2.py  — V2
================================================================
Extends the V3 scoring pipeline with a fifth scoring layer:
Layer 5 — VOLUME PROFILE  (max +16 / min -13)

V3 layers (unchanged):
  1. Structure   (ZigZag + PAVP + TS gate)
  2. Location    (PAVP value-area position)
  3. Momentum    (Trend Speed + MACD veto)
  4. Order Flow  (CVD IQ + move_ratio)

V2 addition:
  5. Volume Profile (HVN/LVN proximity, naked POC, VA skew)
     +8  price at LVN — low-resistance vacuum, fast move expected
     -5  price at HVN — congestion, expect chop / rejection
     +5  price near LVN (<0.5%) and in trade direction
     -3  price near HVN (<0.5%)
     -3  naked POC within 0.5% acting as magnet against trade
     +3  VA volume skew confirms bias
     -3  VA volume skew opposes bias
     +3  VP va_position confirms bias
     -5  VP va_position opposes bias

Public API
----------
compute_v2_decision(df, symbol, timeframe, vp_window) -> dict

Returns a V3 decision dict enriched with V2 fields:
    vp_score, vp_reasons, at_hvn, at_lvn,
    naked_poc_distance, va_position_v2,
    cvd_move_ratio (convenience — pulled from snap),
    score_breakdown["volume_profile"] added,
    score_breakdown["total"] re-computed from V2 total.
================================================================
"""

import os
import sys
from typing import Dict, Any, Tuple, List, Optional

# ── path: phase-2 backend (indicator / scoring / enrichment) ─
_HERE   = os.path.dirname(os.path.abspath(__file__))
_PHASE2 = os.path.normpath(os.path.join(_HERE, "..", "phase 2 files - python backend"))
if os.path.isdir(_PHASE2) and _PHASE2 not in sys.path:
    sys.path.insert(0, _PHASE2)
# ── path: backtesting dir (volume_profile_engine) ────────────
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import pandas as pd

from indicator_engine      import compute_all_indicators
from signal_enrichment     import enrich_signal
from scoring_engine_py     import score_signal, score_to_grade, score_to_decision
from volume_profile_engine import compute_volume_intelligence


# ─────────────────────────────────────────────────────────────
# LAYER 5 — VOLUME PROFILE SCORING
# ─────────────────────────────────────────────────────────────

def _score_volume_profile(
    vp_ctx: Dict[str, Any],
    bias:   str,
) -> Tuple[float, List[str]]:
    """
    Score the VP context against the trade bias.

    Args:
        vp_ctx : Output of compute_volume_profile_context()
        bias   : 'LONG' or 'SHORT'

    Returns:
        (score_delta, reasons_list)
    """
    score   = 0.0
    reasons: List[str] = []

    is_bull = (bias == "LONG")

    va_pos   = vp_ctx.get("va_position",          "INSIDE_VA")
    tgt_va   = "ABOVE_VA" if is_bull else "BELOW_VA"
    opp_va   = "BELOW_VA" if is_bull else "ABOVE_VA"

    vol_skew = vp_ctx.get("volume_skew", "NEUTRAL")
    tgt_skew = "BULLISH"  if is_bull else "BEARISH"
    opp_skew = "BEARISH"  if is_bull else "BULLISH"

    at_hvn   = vp_ctx.get("at_hvn",               False)
    at_lvn   = vp_ctx.get("at_lvn",               False)
    hvn_dist = vp_ctx.get("nearest_hvn_dist_pct", 999.0)
    lvn_dist = vp_ctx.get("nearest_lvn_dist_pct", 999.0)

    naked_poc_dist: Optional[float] = vp_ctx.get("naked_poc_dist_pct")

    # ── HVN / LVN at price ───────────────────────────────────
    if at_lvn:
        score += 8.0
        reasons.append("VP: price at LVN — low-resistance vacuum (+8)")
    elif at_hvn:
        score -= 5.0
        reasons.append("VP: price at HVN — congestion / rejection risk (-5)")
    elif lvn_dist < 0.5:
        score += 5.0
        reasons.append(f"VP: near LVN ({lvn_dist:.2f}%) — vacuum catalyst (+5)")
    elif hvn_dist < 0.5:
        score -= 3.0
        reasons.append(f"VP: near HVN ({hvn_dist:.2f}%) — congestion risk (-3)")

    # ── Naked POC magnet ─────────────────────────────────────
    if naked_poc_dist is not None:
        dist_abs         = abs(naked_poc_dist)
        npoc_is_below    = naked_poc_dist < 0   # close < naked POC → POC is above

        if dist_abs < 0.5:
            if is_bull and not npoc_is_below:
                # Naked POC is below price → magnet pulling down against LONG
                score -= 3.0
                reasons.append(
                    f"VP: naked POC {dist_abs:.2f}% below — downward magnet (-3)"
                )
            elif not is_bull and npoc_is_below:
                # Naked POC is above price → magnet pulling up against SHORT
                score -= 3.0
                reasons.append(
                    f"VP: naked POC {dist_abs:.2f}% above — upward magnet (-3)"
                )
            elif is_bull and npoc_is_below:
                # Naked POC above price for LONG → acts as upside target
                score += 2.0
                reasons.append(
                    f"VP: naked POC {dist_abs:.2f}% above — magnetic upside target (+2)"
                )
            else:
                # Naked POC below price for SHORT → downside target
                score += 2.0
                reasons.append(
                    f"VP: naked POC {dist_abs:.2f}% below — magnetic downside target (+2)"
                )

    # ── VA position (VP engine is more granular than PAVP) ───
    if va_pos == tgt_va:
        score += 3.0
        reasons.append(f"VP: {va_pos} confirms {bias} (+3)")
    elif va_pos == opp_va:
        score -= 5.0
        reasons.append(f"VP: {va_pos} opposes {bias} (-5)")
    # INSIDE_VA: neutral — PAVP layer already penalises chop

    # ── Volume skew ──────────────────────────────────────────
    if vol_skew == tgt_skew:
        score += 3.0
        reasons.append(f"VP: volume skew {vol_skew} confirms {bias} (+3)")
    elif vol_skew == opp_skew:
        score -= 3.0
        reasons.append(f"VP: volume skew {vol_skew} opposes {bias} (-3)")

    return score, reasons


# ─────────────────────────────────────────────────────────────
# PUBLIC: V2 DECISION
# ─────────────────────────────────────────────────────────────

def compute_v2_decision(
    df:        pd.DataFrame,
    symbol:    str = "BTCUSDT",
    timeframe: str = "15m",
    vp_window: int = 100,
) -> Dict[str, Any]:
    """
    Full V2 signal pipeline:
      1. Run all V3 indicators (compute_all_indicators)
      2. Enrich + score through V3 pipeline (score_signal)
      3. Compute VP context (compute_volume_profile_context)
      4. Score Layer 5 (VP)
      5. Merge: re-compute total, grade, confidence from V3 + VP

    Args:
        df        : OHLCV slice.  Should be at least vp_window bars.
                    Passed directly to compute_all_indicators and
                    compute_volume_profile_context.
        symbol    : Symbol string for enrichment metadata.
        timeframe : Timeframe string for enrichment metadata.
        vp_window : Lookback for volume profile (default 100).

    Returns:
        V2 decision dict — all V3 fields plus:
          vp_score          float
          vp_reasons        list[str]
          at_hvn            bool
          at_lvn            bool
          naked_poc_distance float | None
          va_position_v2    str
          cvd_move_ratio    float
          score_breakdown["volume_profile"]  float  (new key)
          score_breakdown["total"]           float  (updated)
          confidence / setup_grade / bias    (updated from V2 total)
    """
    # ── 1. V3 indicators ─────────────────────────────────────
    snap = compute_all_indicators(df)

    price_row = df.iloc[-1]
    price = {
        "open":  float(price_row["open"]),
        "high":  float(price_row["high"]),
        "low":   float(price_row["low"]),
        "close": float(price_row["close"]),
    }

    enriched = enrich_signal(
        indicator_snapshot = snap,
        symbol    = symbol,
        timeframe = timeframe,
        price     = price,
        volume    = float(price_row["volume"]),
    )

    # ── 2. V3 scoring ─────────────────────────────────────────
    v3 = score_signal(enriched)

    # Convenience: pull cvd_move_ratio from snap
    cvd_move_ratio = float(
        snap.get("cvd", {}).get("delta_implied", {}).get("move_ratio", 1.0)
    )

    # ── 3. VP context ─────────────────────────────────────────
    vp_ctx: Dict[str, Any] = {}
    try:
        vp_ctx = compute_volume_intelligence(df, window=vp_window)
    except Exception as exc:
        vp_ctx = {
            "va_position": "UNKNOWN", "volume_skew": "NEUTRAL",
            "at_hvn": False, "at_lvn": False,
            "naked_poc_dist_pct": None,
            "_error": str(exc),
        }

    # ── 4. If V3 already blocked, attach VP context and return
    if v3.get("blocked_by_exhaustion") or "NO TRADE" in str(v3.get("bias", "")):
        v3.update({
            "vp_score":           0.0,
            "vp_reasons":         ["V3 blocked — VP layer not applied"],
            "at_hvn":             vp_ctx.get("at_hvn",              False),
            "at_lvn":             vp_ctx.get("at_lvn",              False),
            "naked_poc_distance": vp_ctx.get("naked_poc_dist_pct"),
            "va_position_v2":     vp_ctx.get("va_position",         "UNKNOWN"),
            "cvd_move_ratio":     cvd_move_ratio,
        })
        return v3

    # ── 5. VP scoring layer ──────────────────────────────────
    bias_str = "LONG" if "LONG" in str(v3.get("bias", "LONG")) else "SHORT"
    vp_score, vp_reasons = _score_volume_profile(vp_ctx, bias_str)

    # ── 6. Merge totals ───────────────────────────────────────
    v3_sb    = v3.get("score_breakdown", {})
    v3_total = float(v3_sb.get("total", 0.0))

    v2_total = max(0.0, min(100.0, v3_total + vp_score))
    v2_grade = score_to_grade(v2_total)
    v2_bias  = score_to_decision(v2_total, bias_str)

    v3.update({
        # Re-derived V2 fields
        "bias":        v2_bias,
        "setup_grade": v2_grade,
        "confidence":  round(v2_total, 1),
        # VP layer fields
        "vp_score":    round(vp_score, 2),
        "vp_reasons":  vp_reasons,
        "at_hvn":      vp_ctx.get("at_hvn",              False),
        "at_lvn":      vp_ctx.get("at_lvn",              False),
        "naked_poc_distance": vp_ctx.get("naked_poc_dist_pct"),
        "va_position_v2":     vp_ctx.get("va_position",  "UNKNOWN"),
        "cvd_move_ratio":     cvd_move_ratio,
        # Updated score_breakdown
        "score_breakdown": {
            **v3_sb,
            "volume_profile": round(vp_score, 2),
            "total":          round(v2_total, 2),
        },
    })

    return v3

# Alias for backtest_v2.py compatibility
run_strategy = compute_v2_decision
