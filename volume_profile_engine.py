"""
volume_profile_engine.py
═══════════════════════════════════════════════════════════
Computes the volume profile intelligence that powers the
rebuilt strategy. Replaces the generic indicator scoring
with the actual signals used in visual trading.

Provides:
  1. Bull/Bear split volume profiles per swing leg
     - Separate VAH, VAL, POC for bullish and bearish volume
     - LVN (Low Volume Node) and HVN (High Volume Node) zones
  2. CVD triangle sequence detector
     - Replicates the plotshape() signals from CVD IQ
     - Tracks triangle appearance, cessation, confirming candle
  3. Historical POC tracker
     - Which prior PAVP POCs are still active (not yet crossed)
     - Distance from current price to each active POC
  4. Level-based exit target builder
     - Ordered list of take-profit levels based on profile structure
═══════════════════════════════════════════════════════════
"""

import numpy as np
import pandas as pd
from typing import Dict, Any, List, Tuple, Optional


# ─────────────────────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────────────────────

PROFILE_ROWS       = 50       # price bins per profile
VALUE_AREA_PCT     = 0.68     # 68% value area
LVN_THRESHOLD      = 0.25     # below 25% of avg row volume = LVN
HVN_THRESHOLD      = 1.8      # above 180% of avg row volume = HVN
TRIANGLE_COST_RANK = 90.0     # top 10% cost = triangle fires
MIN_TRIANGLE_BARS  = 2        # minimum triangle bars to count as sequence


# ─────────────────────────────────────────────────────────────
# UTILITY
# ─────────────────────────────────────────────────────────────

def _rma(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(alpha=1.0/period, adjust=False).mean()


def _build_volume_profile(
    highs:   np.ndarray,
    lows:    np.ndarray,
    volumes: np.ndarray,
    rows:    int = PROFILE_ROWS
) -> Tuple[np.ndarray, np.ndarray, float, float]:
    """
    Build a volume-at-price histogram from a set of bars.

    Returns:
        vol_bins   : volume at each price row (length=rows)
        price_rows : price at center of each row
        price_low  : bottom of profile range
        price_high : top of profile range
    """
    if len(highs) == 0:
        return np.zeros(rows), np.zeros(rows), 0.0, 0.0

    price_high = float(highs.max())
    price_low  = float(lows.min())

    if price_high <= price_low:
        return np.zeros(rows), np.zeros(rows), price_low, price_high

    step     = (price_high - price_low) / rows
    vol_bins = np.zeros(rows)

    for i in range(len(highs)):
        bar_h   = float(highs[i])
        bar_l   = float(lows[i])
        bar_vol = float(volumes[i])
        bar_range = bar_h - bar_l

        for row in range(rows):
            row_low  = price_low + row * step
            row_high = row_low + step
            overlap_low  = max(bar_l,  row_low)
            overlap_high = min(bar_h, row_high)
            if overlap_high > overlap_low:
                frac = (overlap_high - overlap_low) / bar_range if bar_range > 0 else 1.0 / rows
                vol_bins[row] += bar_vol * frac

    price_rows = np.array([price_low + (i + 0.5) * step for i in range(rows)])
    return vol_bins, price_rows, price_low, price_high


def _compute_value_area(
    vol_bins:   np.ndarray,
    price_rows: np.ndarray,
    price_low:  float,
    price_step: float,
    va_pct:     float = VALUE_AREA_PCT
) -> Tuple[float, float, float]:
    """
    Compute POC, VAH, VAL from a volume histogram.

    Returns:
        poc : Point of Control price
        vah : Value Area High price
        val : Value Area Low price
    """
    if vol_bins.sum() == 0:
        return float(price_rows.mean()), float(price_rows[-1]), float(price_rows[0])

    poc_idx  = int(np.argmax(vol_bins))
    poc      = float(price_rows[poc_idx])

    target   = vol_bins.sum() * va_pct
    captured = vol_bins[poc_idx]
    above    = poc_idx
    below    = poc_idx

    rows = len(vol_bins)
    while captured < target:
        vol_above = vol_bins[above + 1] if above + 1 < rows else 0.0
        vol_below = vol_bins[below - 1] if below - 1 >= 0  else 0.0
        if vol_above == 0 and vol_below == 0:
            break
        if vol_above >= vol_below:
            above    += 1
            captured += vol_above
        else:
            below    -= 1
            captured += vol_below

    vah = price_low + (above + 1.0) * price_step
    val = price_low + (below + 0.0) * price_step
    return poc, vah, val


def _classify_nodes(
    vol_bins:   np.ndarray,
    price_rows: np.ndarray,
    price_low:  float,
    price_step: float
) -> Tuple[List[Dict], List[Dict]]:
    """
    Classify each price row as LVN or HVN.

    Returns:
        lvn_zones : list of {price_low, price_high, volume}
        hvn_zones : list of {price_low, price_high, volume}
    """
    if vol_bins.sum() == 0:
        return [], []

    avg_vol = vol_bins[vol_bins > 0].mean() if (vol_bins > 0).any() else 1.0
    lvns, hvns = [], []

    for i, (vol, price) in enumerate(zip(vol_bins, price_rows)):
        row_low  = round(price_low + i * price_step, 8)
        row_high = round(row_low + price_step, 8)
        if vol < avg_vol * LVN_THRESHOLD:
            lvns.append({"price_low": row_low, "price_high": row_high, "volume": round(float(vol), 2)})
        elif vol > avg_vol * HVN_THRESHOLD:
            hvns.append({"price_low": row_low, "price_high": row_high, "volume": round(float(vol), 2)})

    return lvns, hvns


# ─────────────────────────────────────────────────────────────
# 1. BULL / BEAR SPLIT VOLUME PROFILES PER SWING
# ─────────────────────────────────────────────────────────────

def compute_swing_volume_profiles(
    df:             pd.DataFrame,
    atr_multiplier: float = 1.5,
    atr_period:     int   = 14
) -> Dict[str, Any]:
    """
    Detects swing legs via ATR-based ZigZag, then builds
    separate bull and bear volume profiles for the current
    (most recent) swing leg.

    Bull profile = volume from candles where close > open
    Bear profile = volume from candles where close < open

    Returns full profile data including LVN/HVN zones,
    separate VAH/VAL for bull and bear, and combined POC.
    """
    n       = len(df)
    high_s  = df["high"].values
    low_s   = df["low"].values
    close_s = df["close"].values
    open_s  = df["open"].values
    vol_s   = df["volume"].values

    # ATR for pivot detection
    prev_c = np.roll(close_s, 1)
    prev_c[0] = close_s[0]
    tr = np.maximum.reduce([
        high_s - low_s,
        np.abs(high_s - prev_c),
        np.abs(low_s  - prev_c)
    ])
    # RMA
    alpha = 1.0 / atr_period
    atr_arr = np.zeros(n)
    atr_arr[0] = tr[0]
    for i in range(1, n):
        atr_arr[i] = alpha * tr[i] + (1 - alpha) * atr_arr[i-1]

    # ZigZag pivot detection
    pivots = []   # (type, price, index)  type = "HIGH" or "LOW"
    direction = 0
    last_high = high_s[0]; last_high_idx = 0
    last_low  = low_s[0];  last_low_idx  = 0

    for i in range(1, n):
        thresh = atr_arr[i] * atr_multiplier

        if direction >= 0:
            if high_s[i] > last_high:
                last_high = high_s[i]; last_high_idx = i
            if low_s[i] <= last_high - thresh and last_high != high_s[0]:
                pivots.append(("HIGH", last_high, last_high_idx))
                direction   = -1
                last_low    = low_s[i]; last_low_idx = i

        if direction <= 0:
            if low_s[i] < last_low:
                last_low = low_s[i]; last_low_idx = i
            if high_s[i] >= last_low + thresh and last_low != low_s[0]:
                pivots.append(("LOW", last_low, last_low_idx))
                direction    = 1
                last_high    = high_s[i]; last_high_idx = i

    # Determine current swing leg boundaries
    if len(pivots) >= 2:
        swing_start_idx = pivots[-1][2]
        swing_end_idx   = n - 1
        swing_direction = 1 if pivots[-1][0] == "LOW" else -1
    elif len(pivots) == 1:
        swing_start_idx = pivots[-1][2]
        swing_end_idx   = n - 1
        swing_direction = 1 if pivots[-1][0] == "LOW" else -1
    else:
        swing_start_idx = max(0, n - 50)
        swing_end_idx   = n - 1
        swing_direction = 1

    leg_slice = slice(swing_start_idx, swing_end_idx + 1)
    leg_highs  = high_s[leg_slice]
    leg_lows   = low_s[leg_slice]
    leg_closes = close_s[leg_slice]
    leg_opens  = open_s[leg_slice]
    leg_vols   = vol_s[leg_slice]

    # Split into bull and bear bars
    is_bull = leg_closes > leg_opens
    is_bear = leg_closes < leg_opens

    bull_highs  = leg_highs[is_bull];  bull_lows  = leg_lows[is_bull];  bull_vols  = leg_vols[is_bull]
    bear_highs  = leg_highs[is_bear];  bear_lows  = leg_lows[is_bear];  bear_vols  = leg_vols[is_bear]

    price_high = float(leg_highs.max()) if len(leg_highs) > 0 else float(high_s[-1])
    price_low  = float(leg_lows.min())  if len(leg_lows)  > 0 else float(low_s[-1])
    price_step = (price_high - price_low) / PROFILE_ROWS if price_high > price_low else 0.001

    # Build bull profile
    bull_bins, bull_rows, bull_pl, bull_ph = _build_volume_profile(
        bull_highs, bull_lows, bull_vols, PROFILE_ROWS
    )
    bull_poc, bull_vah, bull_val = _compute_value_area(
        bull_bins, bull_rows, bull_pl,
        (bull_ph - bull_pl) / PROFILE_ROWS if bull_ph > bull_pl else 0.001
    )

    # Build bear profile
    bear_bins, bear_rows, bear_pl, bear_ph = _build_volume_profile(
        bear_highs, bear_lows, bear_vols, PROFILE_ROWS
    )
    bear_poc, bear_vah, bear_val = _compute_value_area(
        bear_bins, bear_rows, bear_pl,
        (bear_ph - bear_pl) / PROFILE_ROWS if bear_ph > bear_pl else 0.001
    )

    # Build combined profile for LVN/HVN
    all_bins, all_rows, all_pl, all_ph = _build_volume_profile(
        leg_highs, leg_lows, leg_vols, PROFILE_ROWS
    )
    combined_step = (all_ph - all_pl) / PROFILE_ROWS if all_ph > all_pl else 0.001
    combined_poc, combined_vah, combined_val = _compute_value_area(
        all_bins, all_rows, all_pl, combined_step
    )

    lvn_zones, hvn_zones = _classify_nodes(all_bins, all_rows, all_pl, combined_step)

    # Current price location vs key levels
    current_price = float(close_s[-1])

    def _in_lvn(price, lvns):
        return any(z["price_low"] <= price <= z["price_high"] for z in lvns)

    def _nearest_hvn_above(price, hvns):
        above = [z for z in hvns if z["price_low"] > price]
        return min(above, key=lambda z: z["price_low"]) if above else None

    def _nearest_hvn_below(price, hvns):
        below = [z for z in hvns if z["price_high"] < price]
        return max(below, key=lambda z: z["price_high"]) if below else None

    in_lvn_now = _in_lvn(current_price, lvn_zones)
    next_hvn_above = _nearest_hvn_above(current_price, hvn_zones)
    next_hvn_below = _nearest_hvn_below(current_price, hvn_zones)

    # Swing info
    last_swing_high = float(max((p[1] for p in pivots if p[0] == "HIGH"), default=high_s.max()))
    last_swing_low  = float(min((p[1] for p in pivots if p[0] == "LOW"),  default=low_s.min()))
    leg_bar_count   = swing_end_idx - swing_start_idx

    return {
        # Combined profile
        "poc":              round(combined_poc, 6),
        "vah":              round(combined_vah, 6),
        "val":              round(combined_val, 6),
        "profile_high":     round(price_high,   6),
        "profile_low":      round(price_low,    6),

        # Bull profile (resistance / take-profit for longs)
        "bull_poc":         round(bull_poc, 6),
        "bull_vah":         round(bull_vah, 6),
        "bull_val":         round(bull_val, 6),

        # Bear profile (support / take-profit for shorts)
        "bear_poc":         round(bear_poc, 6),
        "bear_vah":         round(bear_vah, 6),
        "bear_val":         round(bear_val, 6),

        # LVN / HVN zones
        "lvn_zones":        lvn_zones,
        "hvn_zones":        hvn_zones,
        "in_lvn":           in_lvn_now,
        "next_hvn_above":   next_hvn_above,
        "next_hvn_below":   next_hvn_below,

        # Swing structure
        "swing_direction":  swing_direction,
        "last_swing_high":  round(last_swing_high, 6),
        "last_swing_low":   round(last_swing_low,  6),
        "leg_bar_count":    leg_bar_count,
        "pivot_count":      len(pivots),
    }


# ─────────────────────────────────────────────────────────────
# 2. CVD TRIANGLE SEQUENCE DETECTOR
# ─────────────────────────────────────────────────────────────

def compute_cvd_triangles(
    df:               pd.DataFrame,
    lookback:         int   = 100,
    cost_rank_thresh: float = 75.0,
    swing_len:        int   = 3
) -> Dict[str, Any]:
    """
    Replicates the CVD IQ plotshape() triangle logic.

    UPWARD triangles (placed at BOTTOM of candles):
      = high cost during a downswing
      = bearish pressure exhausting
      Signal: last upward triangle bar = exhaustion complete → LONG entry on next bar

    DOWNWARD triangles (placed at TOP of candles):
      = high cost during an upswing
      = bullish pressure exhausting
      Signal: last downward triangle bar = exhaustion complete → SHORT entry on next bar

    Entry rule:
      - Triangles appeared on bar N (1 or more consecutive bars)
      - Bar N+1 has NO triangle of the same type
      - Bar N was the exhaustion peak
      - Enter on bar N+1 (the first bar WITHOUT a triangle after a run)

    primary_signal values:
      BULLISH_ENTRY  : upward triangles just stopped → enter LONG now
      BEARISH_ENTRY  : downward triangles just stopped → enter SHORT now
      BULLISH_BUILDING : upward triangles still appearing (not yet entry)
      BEARISH_BUILDING : downward triangles still appearing (not yet entry)
      NONE           : no active signal
    """
    close_s  = df["close"].values
    open_s   = df["open"].values
    volume_s = df["volume"].values
    n        = len(df)

    if n < 10:
        return {
            "primary_signal":    "NONE",
            "triangle_up_now":   False,
            "triangle_down_now": False,
            "up_seq_length":     0,
            "down_seq_length":   0,
            "bars_since_up":     999,
            "bars_since_down":   999,
        }

    # Bar direction
    bar_dir = np.sign(close_s - open_s)
    bar_dir[bar_dir == 0] = 1

    # Cost per tick
    tick_size     = np.maximum(close_s * 0.0001, 1e-8)
    bar_move      = np.abs(close_s - open_s)
    bar_ticks     = bar_move / tick_size
    delta         = volume_s * bar_dir
    cost_per_tick = np.where(bar_ticks > 0, np.abs(delta) / bar_ticks, 0.0)

    # Rolling percentile rank
    cost_ranks = np.full(n, 50.0)
    for i in range(min(lookback, n), n):
        window = cost_per_tick[max(0, i - lookback): i]
        if len(window) > 1:
            cost_ranks[i] = float((window < cost_per_tick[i]).sum() / len(window) * 100)

    # Swing direction
    swing_dir = np.sign(close_s - np.roll(close_s, swing_len))
    swing_dir[:swing_len] = 0

    is_high_cost = cost_ranks >= cost_rank_thresh

    # UPWARD triangle   = high cost + DOWNswing = bearish exhaustion → bullish reversal coming
    # DOWNWARD triangle = high cost + UPswing   = bullish exhaustion → bearish reversal coming
    triangle_up   = is_high_cost & (swing_dir < 0)   # bottom of candle, downtrend exhausting
    triangle_down = is_high_cost & (swing_dir > 0)   # top of candle, uptrend exhausting

    # ── Core signal logic ────────────────────────────────────
    # The entry is the FIRST BAR WITHOUT a triangle after a run of triangles.
    # We look at the last bar (current) and previous bars.

    curr_up   = bool(triangle_up[-1])
    curr_down = bool(triangle_down[-1])

    # Find last bar index where each triangle type fired
    up_indices   = np.where(triangle_up)[0]
    down_indices = np.where(triangle_down)[0]

    last_up_bar   = int(up_indices[-1])   if len(up_indices)   > 0 else -999
    last_down_bar = int(down_indices[-1]) if len(down_indices) > 0 else -999

    bars_since_up   = (n - 1) - last_up_bar   if last_up_bar   >= 0 else 999
    bars_since_down = (n - 1) - last_down_bar if last_down_bar >= 0 else 999

    # Count consecutive run length ending at last triangle bar
    def _run_length(arr, last_idx):
        if last_idx < 0:
            return 0
        count = 0
        i = last_idx
        while i >= 0 and arr[i]:
            count += 1
            i -= 1
        return count

    up_seq_len   = _run_length(triangle_up,   last_up_bar)
    down_seq_len = _run_length(triangle_down, last_down_bar)

    # Signal fires exactly when:
    #   bars_since == 1  (previous bar had triangle, current bar does not)
    #   AND the run had at least 1 bar (any triangle sequence qualifies)
    primary_signal = "NONE"

    if bars_since_up == 1 and up_seq_len >= 1:
        primary_signal = "BULLISH_ENTRY"    # upward triangles just stopped → LONG

    elif bars_since_down == 1 and down_seq_len >= 1:
        primary_signal = "BEARISH_ENTRY"    # downward triangles just stopped → SHORT

    elif curr_up:
        primary_signal = "BULLISH_BUILDING"  # upward triangles still firing

    elif curr_down:
        primary_signal = "BEARISH_BUILDING"  # downward triangles still firing

    return {
        "primary_signal":    primary_signal,
        "triangle_up_now":   curr_up,
        "triangle_down_now": curr_down,
        "up_seq_length":     up_seq_len,
        "down_seq_length":   down_seq_len,
        "bars_since_up":     int(bars_since_up),
        "bars_since_down":   int(bars_since_down),
    }


# ─────────────────────────────────────────────────────────────
# 3. HISTORICAL POC TRACKER
# ─────────────────────────────────────────────────────────────

def compute_historical_pocs(
    df:           pd.DataFrame,
    pivot_length: int   = 20,
    va_pct:       float = VALUE_AREA_PCT,
    max_pocs:     int   = 10
) -> Dict[str, Any]:
    """
    Identifies historical POCs from prior pivot-anchored
    volume profiles and determines which are still active
    (price has not yet crossed them).

    An active historical POC acts as a magnet / target level.
    A violated POC becomes a potential support/resistance flip.
    """
    n       = len(df)
    high_s  = df["high"].values
    low_s   = df["low"].values
    close_s = df["close"].values
    vol_s   = df["volume"].values

    # Find pivot points
    def _find_pivots(arr, length, ptype):
        pivots = []
        for i in range(length, n - length):
            window = arr[i - length: i + length + 1]
            center = arr[i]
            if ptype == "high" and center == window.max():
                pivots.append(i)
            elif ptype == "low" and center == window.min():
                pivots.append(i)
        return pivots

    ph = _find_pivots(high_s, pivot_length, "high")
    pl = _find_pivots(low_s,  pivot_length, "low")
    all_pivots = sorted(set(ph + pl))

    if len(all_pivots) < 2:
        return {
            "active_pocs":   [],
            "violated_pocs": [],
            "nearest_above": None,
            "nearest_below": None,
        }

    current_price = float(close_s[-1])
    pocs = []

    # Build profile between each consecutive pivot pair
    for k in range(len(all_pivots) - 1):
        x1 = all_pivots[k]
        x2 = all_pivots[k + 1]
        if x2 - x1 < 5:
            continue

        seg_high = high_s[x1:x2 + 1]
        seg_low  = low_s[x1:x2 + 1]
        seg_vol  = vol_s[x1:x2 + 1]

        bins, rows, pl_price, ph_price = _build_volume_profile(
            seg_high, seg_low, seg_vol, PROFILE_ROWS
        )
        step = (ph_price - pl_price) / PROFILE_ROWS if ph_price > pl_price else 0.001
        poc, vah, val = _compute_value_area(bins, rows, pl_price, step, va_pct)

        total_vol = float(seg_vol.sum())
        poc_vol   = float(bins[int(np.argmax(bins))]) if bins.sum() > 0 else 0.0

        # Has price crossed this POC since it was formed?
        subsequent_closes = close_s[x2:]
        if len(subsequent_closes) > 0:
            crossed = bool(
                (subsequent_closes.min() <= poc <= subsequent_closes.max()) or
                np.any(np.diff(np.sign(subsequent_closes - poc)) != 0)
            )
        else:
            crossed = False

        # Significance: POC volume relative to total leg volume
        significance = round(poc_vol / total_vol * 100, 1) if total_vol > 0 else 0.0

        pocs.append({
            "poc":          round(poc, 6),
            "vah":          round(vah, 6),
            "val":          round(val, 6),
            "bar_index":    x2,
            "total_volume": round(total_vol, 2),
            "significance": significance,
            "crossed":      crossed,
            "distance_pct": round((current_price - poc) / poc * 100, 4) if poc > 0 else 0.0,
        })

    # Sort by recency (most recent first)
    pocs = pocs[-max_pocs:][::-1]

    active   = [p for p in pocs if not p["crossed"]]
    violated = [p for p in pocs if p["crossed"]]

    # Nearest active POC above and below current price
    above = [p for p in active if p["poc"] > current_price]
    below = [p for p in active if p["poc"] < current_price]

    nearest_above = min(above, key=lambda p: p["poc"] - current_price) if above else None
    nearest_below = max(below, key=lambda p: current_price - p["poc"]) if below else None

    return {
        "active_pocs":   active,
        "violated_pocs": violated,
        "nearest_above": nearest_above,
        "nearest_below": nearest_below,
        "total_found":   len(pocs),
    }


# ─────────────────────────────────────────────────────────────
# 4. EXIT TARGET BUILDER
# ─────────────────────────────────────────────────────────────

def build_exit_targets(
    direction:        str,
    current_price:    float,
    swing_profiles:   Dict[str, Any],
    pavp:             Dict[str, Any],
    historical_pocs:  Dict[str, Any],
) -> List[Dict[str, Any]]:
    """
    Builds an ordered list of take-profit levels for a trade.

    For LONG: levels above current price, ordered ascending
    For SHORT: levels below current price, ordered descending

    Each level has:
        price       : the target price
        type        : what kind of level it is
        significance: how important (high = more likely to hold)
        partial_pct : suggested % of position to close here
    """
    targets = []

    is_long = direction == "LONG"

    def _add(price, level_type, significance, partial_pct):
        if price <= 0:
            return
        if is_long  and price <= current_price: return
        if not is_long and price >= current_price: return
        targets.append({
            "price":        round(float(price), 6),
            "type":         level_type,
            "significance": significance,
            "partial_pct":  partial_pct,
        })

    # PAVP levels
    _add(pavp.get("poc", 0),  "PAVP_POC", 80, 30)
    _add(pavp.get("vah", 0),  "PAVP_VAH", 70, 25)
    _add(pavp.get("val", 0),  "PAVP_VAL", 70, 25)

    # Swing profile levels
    if is_long:
        _add(swing_profiles.get("bull_val", 0), "BULL_VAL", 75, 20)
        _add(swing_profiles.get("bull_poc", 0), "BULL_POC", 85, 30)
        _add(swing_profiles.get("bull_vah", 0), "BULL_VAH", 80, 25)
        _add(swing_profiles.get("poc",      0), "SWING_POC",85, 30)
        _add(swing_profiles.get("vah",      0), "SWING_VAH",75, 25)
        nhvn = swing_profiles.get("next_hvn_above")
        if nhvn:
            _add(nhvn["price_low"], "HVN_ABOVE", 90, 35)
    else:
        _add(swing_profiles.get("bear_vah", 0), "BEAR_VAH", 75, 20)
        _add(swing_profiles.get("bear_poc", 0), "BEAR_POC", 85, 30)
        _add(swing_profiles.get("bear_val", 0), "BEAR_VAL", 80, 25)
        _add(swing_profiles.get("poc",      0), "SWING_POC",85, 30)
        _add(swing_profiles.get("val",      0), "SWING_VAL",75, 25)
        nhvn = swing_profiles.get("next_hvn_below")
        if nhvn:
            _add(nhvn["price_high"], "HVN_BELOW", 90, 35)

    # Historical POCs (high significance = large prior volume)
    hist_key = "nearest_above" if is_long else "nearest_below"
    hist_poc = historical_pocs.get(hist_key)
    if hist_poc and hist_poc.get("significance", 0) > 20:
        _add(hist_poc["poc"], "HISTORICAL_POC", int(hist_poc["significance"]), 40)

    # Also add all active POCs in direction
    for p in historical_pocs.get("active_pocs", [])[:5]:
        if p.get("significance", 0) > 15:
            _add(p["poc"], "HISTORICAL_POC", int(p["significance"]), 25)

    # Remove duplicates (within 0.1% of each other)
    seen = []
    deduped = []
    for t in targets:
        too_close = any(abs(t["price"] - s) / s < 0.001 for s in seen)
        if not too_close:
            seen.append(t["price"])
            deduped.append(t)

    # Sort: ascending for long, descending for short
    deduped.sort(key=lambda x: x["price"], reverse=not is_long)

    # Normalize partial_pct so total doesn't exceed 100%
    total_pct = sum(t["partial_pct"] for t in deduped)
    if total_pct > 0:
        for t in deduped:
            t["partial_pct"] = round(t["partial_pct"] / total_pct * 100, 1)

    return deduped


# ─────────────────────────────────────────────────────────────
# MASTER FUNCTION
# ─────────────────────────────────────────────────────────────

def compute_volume_intelligence(
    df:   pd.DataFrame,
    pavp: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Runs all volume profile intelligence computations.

    Args:
        df:   DataFrame [time, open, high, low, close, volume]
        pavp: Output of indicator_engine.compute_pavp()

    Returns:
        Complete volume intelligence snapshot for the strategy engine.
    """
    if len(df) < 30:
        return {
            "swing_profiles":  {},
            "cvd_triangles":   {"primary_signal": "NONE"},
            "historical_pocs": {"active_pocs": [], "violated_pocs": []},
            "exit_targets_long":  [],
            "exit_targets_short": [],
        }

    swing   = compute_swing_volume_profiles(df)
    tri     = compute_cvd_triangles(df)
    hist    = compute_historical_pocs(df)

    exit_long  = build_exit_targets("LONG",  float(df["close"].iloc[-1]), swing, pavp, hist)
    exit_short = build_exit_targets("SHORT", float(df["close"].iloc[-1]), swing, pavp, hist)

    return {
        "swing_profiles":     swing,
        "cvd_triangles":      tri,
        "historical_pocs":    hist,
        "exit_targets_long":  exit_long,
        "exit_targets_short": exit_short,
    }
