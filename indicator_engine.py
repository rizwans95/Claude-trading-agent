"""
indicator_engine.py
═══════════════════════════════════════════════════════════
Computes all six trading indicators from raw OHLCV data.

Replicates the logic of:
  - Zig-Zag Volume Profile [KioseffTrading]
  - Volume Profile, Pivot Anchored [DGT / dgtrd]
  - Trend Speed Analyzer [Zeiierman]
  - MACD (TradingView built-in)
  - CVD IQ [TradingIQ]
  - ATR (TradingView built-in)

No TradingView dependency. Works entirely from a pandas
DataFrame with columns: time, open, high, low, close, volume
═══════════════════════════════════════════════════════════
"""

import numpy as np
import pandas as pd
from typing import Dict, Any, List, Tuple, Optional


# ─────────────────────────────────────────────────────────────
# UTILITY HELPERS
# ─────────────────────────────────────────────────────────────

def _rma(series: pd.Series, period: int) -> pd.Series:
    """Wilder's RMA (same as Pine Script ta.rma)."""
    alpha = 1.0 / period
    return series.ewm(alpha=alpha, adjust=False).mean()


def _hma(series: pd.Series, period: int) -> pd.Series:
    """Hull Moving Average."""
    half = max(int(period / 2), 1)
    sqrt_p = max(int(np.sqrt(period)), 1)
    wma_half = series.ewm(span=half, adjust=False).mean()
    wma_full = series.ewm(span=period, adjust=False).mean()
    raw = 2 * wma_half - wma_full
    return raw.ewm(span=sqrt_p, adjust=False).mean()


def _percentile_rank(series: pd.Series, lookback: int = 100) -> pd.Series:
    """Rolling percentile rank (0–100)."""
    def _rank(x):
        if len(x) < 2:
            return 50.0
        return float((x[:-1] < x[-1]).sum() / (len(x) - 1) * 100)
    return series.rolling(lookback, min_periods=2).apply(_rank, raw=True)


# ─────────────────────────────────────────────────────────────
# 1. ATR
# ─────────────────────────────────────────────────────────────

def compute_atr(df: pd.DataFrame, period: int = 14) -> Dict[str, Any]:
    """
    Replicates TradingView built-in ATR (RMA smoothing).
    Returns dict with all signal_format.json atr fields.
    """
    high  = df["high"]
    low   = df["low"]
    close = df["close"]

    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low  - prev_close).abs()
    ], axis=1).max(axis=1)

    atr_series = _rma(tr, period)
    atr_val    = float(atr_series.iloc[-1])
    close_val  = float(close.iloc[-1])

    atr_pct    = round(atr_val / close_val * 100, 4) if close_val else 0.0
    pct_rank   = float(_percentile_rank(atr_series, 100).iloc[-1])

    if pct_rank >= 80:
        vol_state = "HIGH"
    elif pct_rank <= 20:
        vol_state = "LOW"
    else:
        vol_state = "NORMAL"

    # Compression: ATR contracting for 5+ consecutive bars
    recent_5 = atr_series.iloc[-6:].values
    compression = bool(
        len(recent_5) >= 6 and
        all(recent_5[i] < recent_5[i - 1] for i in range(1, 6))
    )

    # Expansion: ATR expanding for 3+ consecutive bars
    recent_3 = atr_series.iloc[-4:].values
    expansion = bool(
        len(recent_3) >= 4 and
        all(recent_3[i] > recent_3[i - 1] for i in range(1, 4))
    )

    return {
        "atr_value":        round(atr_val, 6),
        "atr_pct_of_price": atr_pct,
        "percentile_rank":  round(pct_rank, 1),
        "volatility_state": vol_state,
        "compression":      compression,
        "expansion":        expansion,
        "_series":          atr_series   # kept for internal use
    }


# ─────────────────────────────────────────────────────────────
# 2. MACD
# ─────────────────────────────────────────────────────────────

def compute_macd(
    df: pd.DataFrame,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9
) -> Dict[str, Any]:
    """
    Replicates TradingView built-in MACD exactly.
    Returns dict with all signal_format.json macd fields.
    """
    close      = df["close"]
    ma_fast    = close.ewm(span=fast,   adjust=False).mean()
    ma_slow    = close.ewm(span=slow,   adjust=False).mean()
    macd_line  = ma_fast - ma_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    histogram  = macd_line - signal_line

    hist_now  = float(histogram.iloc[-1])
    hist_prev = float(histogram.iloc[-2]) if len(histogram) > 1 else hist_now
    macd_now  = float(macd_line.iloc[-1])
    macd_prev = float(macd_line.iloc[-2]) if len(macd_line) > 1 else macd_now
    sig_now   = float(signal_line.iloc[-1])
    sig_prev  = float(signal_line.iloc[-2]) if len(signal_line) > 1 else sig_now

    # 4-state histogram classification
    if hist_now > 0 and hist_now > hist_prev:
        hist_state = "BULLISH_ACCELERATING"
    elif hist_now > 0 and hist_now <= hist_prev:
        hist_state = "BULLISH_DECELERATING"
    elif hist_now < 0 and hist_now > hist_prev:
        hist_state = "BEARISH_RECOVERING"
    else:
        hist_state = "BEARISH_ACCELERATING"

    hist_direction = "RISING" if hist_now > hist_prev else "FALLING"
    zero_line      = "ABOVE" if macd_now > 0 else "BELOW"

    # Signal line cross
    if macd_now > sig_now and macd_prev <= sig_prev:
        cross = "BULLISH_CROSS"
    elif macd_now < sig_now and macd_prev >= sig_prev:
        cross = "BEARISH_CROSS"
    else:
        cross = "NONE"

    return {
        "macd_line":           round(macd_now, 6),
        "signal_line":         round(sig_now, 6),
        "histogram":           round(hist_now, 6),
        "histogram_state":     hist_state,
        "histogram_direction": hist_direction,
        "zero_line_position":  zero_line,
        "signal_cross":        cross
    }


# ─────────────────────────────────────────────────────────────
# 3. ZIGZAG STRUCTURE
# ─────────────────────────────────────────────────────────────

def compute_zigzag(
    df: pd.DataFrame,
    atr_multiplier: float = 1.5
) -> Dict[str, Any]:
    """
    ATR-based ZigZag replicating KioseffTrading indicator logic.
    Returns dict with all signal_format.json zigzag fields.
    """
    atr_data  = compute_atr(df)
    atr_s     = atr_data["_series"]

    high_s = df["high"].values
    low_s  = df["low"].values

    pivots: List[Tuple[str, float, int]] = []
    direction = 0
    last_high = high_s[0]
    last_low  = low_s[0]
    last_high_idx = 0
    last_low_idx  = 0

    for i in range(1, len(df)):
        thresh = float(atr_s.iloc[i]) * atr_multiplier

        if direction >= 0:
            if high_s[i] > last_high:
                last_high     = high_s[i]
                last_high_idx = i
            if low_s[i] <= last_high - thresh and last_high != high_s[0]:
                pivots.append(("HIGH", last_high, last_high_idx))
                direction    = -1
                last_low     = low_s[i]
                last_low_idx = i

        if direction <= 0:
            if low_s[i] < last_low:
                last_low     = low_s[i]
                last_low_idx = i
            if high_s[i] >= last_low + thresh and last_low != low_s[0]:
                pivots.append(("LOW", last_low, last_low_idx))
                direction     = 1
                last_high     = high_s[i]
                last_high_idx = i

    # Structure classification
    def get_structure(pvts):
        if len(pvts) < 4:
            return "NEUTRAL"
        highs = [p[1] for p in pvts if p[0] == "HIGH"][-2:]
        lows  = [p[1] for p in pvts if p[0] == "LOW"][-2:]
        if len(highs) < 2 or len(lows) < 2:
            return "NEUTRAL"
        if highs[1] > highs[0] and lows[1] > lows[0]:
            return "BULLISH"
        if highs[1] < highs[0] and lows[1] < lows[0]:
            return "BEARISH"
        return "NEUTRAL"

    structure = get_structure(pivots)

    # Last swing levels
    swing_highs = [p for p in pivots if p[0] == "HIGH"]
    swing_lows  = [p for p in pivots if p[0] == "LOW"]
    last_swing_high = float(swing_highs[-1][1]) if swing_highs else float(df["high"].max())
    last_swing_low  = float(swing_lows[-1][1])  if swing_lows  else float(df["low"].min())

    # BOS detection
    current_high = float(df["high"].iloc[-1])
    current_low  = float(df["low"].iloc[-1])

    if current_high > last_swing_high:
        bos = "BOS_UP"
    elif current_low < last_swing_low:
        bos = "BOS_DOWN"
    else:
        bos = "NONE"

    # Current leg bar count
    if pivots:
        last_pivot_idx = pivots[-1][2]
        leg_bar_count  = len(df) - 1 - last_pivot_idx
    else:
        leg_bar_count  = len(df)

    return {
        "direction":       direction,
        "structure":       structure,
        "last_swing_high": round(last_swing_high, 6),
        "last_swing_low":  round(last_swing_low, 6),
        "bos_signal":      bos,
        "leg_bar_count":   leg_bar_count
    }


# ─────────────────────────────────────────────────────────────
# 4. PIVOT ANCHORED VOLUME PROFILE (PAVP)
# ─────────────────────────────────────────────────────────────

def compute_pavp(
    df: pd.DataFrame,
    pivot_length: int = 20,
    value_area_pct: float = 0.68,
    profile_rows: int = 25
) -> Dict[str, Any]:
    """
    Replicates Volume Profile, Pivot Anchored [DGT / dgtrd].
    Computes POC, VAH, VAL from volume distribution between pivots.
    Returns dict with all signal_format.json pavp fields.
    """
    high_s   = df["high"].values
    low_s    = df["low"].values
    close_s  = df["close"].values
    volume_s = df["volume"].values
    n        = len(df)

    # Detect pivot highs and lows (simplified ta.pivothigh/ta.pivotlow)
    def find_pivots(arr, length, pivot_type="high"):
        pivots = []
        for i in range(length, n - length):
            window = arr[i - length: i + length + 1]
            center = arr[i]
            if pivot_type == "high" and center == window.max():
                pivots.append(i)
            elif pivot_type == "low" and center == window.min():
                pivots.append(i)
        return pivots

    ph_indices = find_pivots(high_s, pivot_length, "high")
    pl_indices = find_pivots(low_s,  pivot_length, "low")

    # Combine and sort all pivot indices
    all_pivots = sorted(set(ph_indices + pl_indices))

    # Use the most recent complete profile window
    if len(all_pivots) >= 2:
        x1 = all_pivots[-2]
        x2 = all_pivots[-1]
    elif len(all_pivots) == 1:
        x1 = 0
        x2 = all_pivots[0]
    else:
        x1 = max(0, n - 50)
        x2 = n - 1

    # Profile range
    profile_high = float(high_s[x1:x2 + 1].max())
    profile_low  = float(low_s[x1:x2 + 1].min())
    traded_vol   = float(volume_s[x1:x2 + 1].sum())
    profile_bars = x2 - x1

    if profile_high <= profile_low or profile_bars == 0:
        # Fallback: use last 50 bars
        x1 = max(0, n - 50)
        profile_high = float(high_s[x1:].max())
        profile_low  = float(low_s[x1:].min())
        traded_vol   = float(volume_s[x1:].sum())
        profile_bars = n - x1

    price_step = (profile_high - profile_low) / profile_rows
    if price_step == 0:
        price_step = 0.0001

    # Build volume distribution
    vol_bins = np.zeros(profile_rows)

    for i in range(x1, min(x2 + 1, n)):
        bar_high = high_s[i]
        bar_low  = low_s[i]
        bar_vol  = volume_s[i]
        bar_range = bar_high - bar_low

        for row in range(profile_rows):
            row_low  = profile_low + row * price_step
            row_high = row_low + price_step

            overlap_low  = max(bar_low,  row_low)
            overlap_high = min(bar_high, row_high)

            if overlap_high > overlap_low:
                if bar_range > 0:
                    fraction = (overlap_high - overlap_low) / bar_range
                else:
                    fraction = 1.0 / profile_rows
                vol_bins[row] += bar_vol * fraction

    # POC = row with highest volume
    poc_idx = int(np.argmax(vol_bins))
    poc     = profile_low + (poc_idx + 0.5) * price_step

    # Value Area — expand from POC until VA% of total volume captured
    total_va_vol = vol_bins.sum() * value_area_pct
    captured     = vol_bins[poc_idx]
    above_idx    = poc_idx
    below_idx    = poc_idx

    while captured < total_va_vol:
        vol_above = vol_bins[above_idx + 1] if above_idx + 1 < profile_rows else 0
        vol_below = vol_bins[below_idx - 1] if below_idx - 1 >= 0 else 0

        if vol_above == 0 and vol_below == 0:
            break
        if vol_above >= vol_below:
            above_idx += 1
            captured  += vol_above
        else:
            below_idx -= 1
            captured  += vol_below

        if above_idx >= profile_rows - 1 and below_idx <= 0:
            break

    vah = profile_low + (above_idx + 1.0) * price_step
    val = profile_low + (below_idx + 0.0) * price_step

    # Derived fields
    close_now = float(close_s[-1])

    if close_now > vah:
        va_position = "ABOVE_VA"
    elif close_now < val:
        va_position = "BELOW_VA"
    else:
        va_position = "INSIDE_VA"

    poc_distance_pct = round((close_now - poc) / poc * 100, 4) if poc else 0.0

    profile_range = profile_high - profile_low
    va_width_pct  = round((vah - val) / profile_range * 100, 2) if profile_range > 0 else 0.0

    return {
        "poc":                 round(poc, 6),
        "vah":                 round(vah, 6),
        "val":                 round(val, 6),
        "poc_distance_pct":    poc_distance_pct,
        "value_area_position": va_position,
        "value_area_width_pct": va_width_pct,
        "profile_length_bars": profile_bars,
        "traded_volume":       round(traded_vol, 2)
    }


# ─────────────────────────────────────────────────────────────
# 5. TREND SPEED ANALYZER
# ─────────────────────────────────────────────────────────────

def compute_trend_speed(
    df: pd.DataFrame,
    max_length: int = 50,
    accel_multiplier: float = 5.0,
    lookback: int = 100
) -> Dict[str, Any]:
    """
    Replicates Trend Speed Analyzer [Zeiierman].
    Returns dict with all signal_format.json trend_speed fields.
    """
    close_s = df["close"]
    open_s  = df["open"]
    n       = len(df)

    # Dynamic EMA
    counts_diff      = close_s
    max_abs          = counts_diff.abs().rolling(200, min_periods=1).max().replace(0, 1)
    counts_diff_norm = (counts_diff + max_abs) / (2 * max_abs)
    dyn_length       = 5 + counts_diff_norm * (max_length - 5)

    delta     = counts_diff.diff().abs()
    max_delta = delta.rolling(200, min_periods=1).max().replace(0, 1)
    accel     = delta / max_delta

    alpha = (2 / (dyn_length + 1)) * (1 + accel * accel_multiplier)
    alpha = alpha.clip(upper=1.0).fillna(0.1)

    dyn_ema = pd.Series(np.nan, index=df.index)
    dyn_ema.iloc[0] = float(close_s.iloc[0])
    for i in range(1, n):
        a = float(alpha.iloc[i])
        dyn_ema.iloc[i] = a * float(close_s.iloc[i]) + (1 - a) * float(dyn_ema.iloc[i - 1])

    # Wave Speed System
    c = _rma(close_s, 10)
    o = _rma(open_s,  10)

    speed      = pd.Series(0.0, index=df.index)
    bull_legs: List[float] = []
    bear_legs: List[float] = []
    pos        = 0

    for i in range(1, n):
        bar_spd = float(c.iloc[i]) - float(o.iloc[i])
        speed.iloc[i] = float(speed.iloc[i - 1]) + bar_spd

        crossed_up   = float(close_s.iloc[i]) > float(dyn_ema.iloc[i]) and \
                       float(close_s.iloc[i - 1]) <= float(dyn_ema.iloc[i - 1])
        crossed_down = float(close_s.iloc[i]) < float(dyn_ema.iloc[i]) and \
                       float(close_s.iloc[i - 1]) >= float(dyn_ema.iloc[i - 1])

        if crossed_up:
            bear_legs.append(float(speed.iloc[i - 1]))
            speed.iloc[i] = bar_spd
            pos = 1
        elif crossed_down:
            bull_legs.append(float(speed.iloc[i - 1]))
            speed.iloc[i] = bar_spd
            pos = -1

    trendspeed_hma = _hma(speed, 5)

    speed_raw  = float(speed.iloc[-1])
    ts_hma_val = float(trendspeed_hma.iloc[-1])
    dyn_ema_val = float(dyn_ema.iloc[-1])
    close_now  = float(close_s.iloc[-1])
    close_prev = float(close_s.iloc[-2]) if n > 1 else close_now
    dyn_prev   = float(dyn_ema.iloc[-2]) if n > 1 else dyn_ema_val

    # Normalized speed (0–1 over lookback)
    min_s = float(speed.rolling(lookback, min_periods=1).min().iloc[-1])
    max_s = float(speed.rolling(lookback, min_periods=1).max().iloc[-1])
    speed_norm = (speed_raw - min_s) / (max_s - min_s) if max_s != min_s else 0.5

    # Direction
    if close_now > dyn_ema_val:
        direction = "BULLISH"
    elif close_now < dyn_ema_val:
        direction = "BEARISH"
    else:
        direction = "FLAT"

    # Price vs EMA crossing state
    just_crossed = (close_now > dyn_ema_val) != (close_prev > dyn_prev)
    if just_crossed:
        price_vs_ema = "CROSSING"
    elif close_now > dyn_ema_val:
        price_vs_ema = "ABOVE"
    else:
        price_vs_ema = "BELOW"

    # Wave analysis
    recent_bulls = bull_legs[-lookback:] if bull_legs else [0.0]
    recent_bears = bear_legs[-lookback:] if bear_legs else [0.0]

    bull_avg = float(np.mean(recent_bulls)) if recent_bulls else 0.0
    bear_avg = float(np.mean(recent_bears)) if recent_bears else 0.0
    bull_max = float(np.max(recent_bulls))  if recent_bulls else 0.0
    bear_max = float(np.min(recent_bears))  if recent_bears else 0.0  # negative

    # Current wave ratios
    if speed_raw > 0 and bull_avg > 0:
        current_ratio_avg = round(speed_raw / bull_avg, 4)
    elif speed_raw < 0 and bear_avg != 0:
        current_ratio_avg = round(speed_raw / abs(bear_avg), 4)
    else:
        current_ratio_avg = 0.0

    if speed_raw > 0 and bull_max > 0:
        current_ratio_max = round(speed_raw / bull_max, 4)
    elif speed_raw < 0 and bear_max != 0:
        current_ratio_max = round(speed_raw / abs(bear_max), 4)
    else:
        current_ratio_max = 0.0

    # Regime
    if current_ratio_avg > 1.2:
        regime = "EXPANSION"
    elif current_ratio_avg < 0.4:
        regime = "EXHAUSTION"
    elif abs(ts_hma_val) < 0.05 * abs(close_now):
        regime = "CONSOLIDATION"
    else:
        regime = "NORMAL"

    # Dominance
    if bull_avg > abs(bear_avg):
        dominance        = "BULLISH"
        dominance_factor = round(bull_avg / abs(bear_avg), 4) if bear_avg != 0 else 0.0
    elif abs(bear_avg) > bull_avg:
        dominance        = "BEARISH"
        dominance_factor = round(abs(bear_avg) / bull_avg, 4) if bull_avg != 0 else 0.0
    else:
        dominance        = "NEUTRAL"
        dominance_factor = 1.0

    return {
        "speed_raw":        round(speed_raw, 6),
        "speed_normalized": round(float(speed_norm), 4),
        "trendspeed_hma":   round(ts_hma_val, 6),
        "direction":        direction,
        "regime":           regime,
        "dyn_ema":          round(dyn_ema_val, 6),
        "price_vs_ema":     price_vs_ema,
        "wave_analysis": {
            "current_ratio_avg": current_ratio_avg,
            "current_ratio_max": current_ratio_max,
            "dominance":         dominance,
            "dominance_factor":  dominance_factor
        }
    }


# ─────────────────────────────────────────────────────────────
# 6. CVD IQ
# ─────────────────────────────────────────────────────────────

def compute_cvd(
    df: pd.DataFrame,
    div_length_small:  int = 2,
    div_length_medium: int = 5,
    div_length_large:  int = 10,
    ma_fast_len:       int = 50,
    ma_slow_len:       int = 200
) -> Dict[str, Any]:
    """
    Replicates CVD IQ [TradingIQ] using available OHLCV data.
    Note: Without tick or 1-min data, direction is approximated
    from close vs open (standard bar-level delta estimation).
    Returns dict with all signal_format.json cvd fields.
    """
    close_s  = df["close"]
    open_s   = df["open"]
    high_s   = df["high"]
    low_s    = df["low"]
    volume_s = df["volume"]
    n        = len(df)

    # Direction per bar: +1 bullish, -1 bearish
    direction = np.sign(close_s - open_s).replace(0, 1)

    # CVD = cumulative sum of (volume * direction)
    delta_series = volume_s * direction
    cvd_series   = delta_series.cumsum()

    current_cvd = float(cvd_series.iloc[-1])

    # CVD MAs
    cvd_ma_fast = cvd_series.ewm(span=ma_fast_len, adjust=False).mean()
    cvd_ma_slow = cvd_series.ewm(span=ma_slow_len, adjust=False).mean()
    ma_fast_val = float(cvd_ma_fast.iloc[-1])
    ma_slow_val = float(cvd_ma_slow.iloc[-1])

    # MA bias
    if current_cvd > ma_fast_val and ma_fast_val > ma_slow_val:
        ma_bias = "ABOVE"
    elif current_cvd < ma_fast_val and ma_fast_val < ma_slow_val:
        ma_bias = "BELOW"
    else:
        ma_bias = "CROSSING"

    # Aggression metrics (bar level)
    buy_vol  = float(volume_s.where(direction > 0, 0).iloc[-1])
    sell_vol = float(volume_s.where(direction < 0, 0).iloc[-1])
    total    = buy_vol + sell_vol if (buy_vol + sell_vol) > 0 else 1.0

    buy_pct   = round(buy_vol / total * 100, 2)
    sell_pct  = round(sell_vol / total * 100, 2)
    net_delta = round(buy_vol - sell_vol, 2)
    imbalance = round(buy_vol / sell_vol, 4) if sell_vol > 0 else 99.0

    if buy_vol > sell_vol:
        dominant_side = "BUY"
    elif sell_vol > buy_vol:
        dominant_side = "SELL"
    else:
        dominant_side = "BALANCED"

    # CVD direction from net delta over recent window
    recent_delta = float(delta_series.iloc[-5:].sum())
    total_recent = float(volume_s.iloc[-5:].sum())
    if total_recent > 0:
        buy_ratio = (recent_delta + total_recent) / (2 * total_recent)
        if buy_ratio > 0.55:
            cvd_direction = "BUYING"
        elif buy_ratio < 0.45:
            cvd_direction = "SELLING"
        else:
            cvd_direction = "NEUTRAL"
    else:
        cvd_direction = "NEUTRAL"

    # Cost per tick
    bar_move  = abs(float(close_s.iloc[-1]) - float(open_s.iloc[-1]))
    tick_size = float(close_s.iloc[-1]) * 0.0001  # approximate
    ticks     = bar_move / tick_size if tick_size > 0 else 1.0
    cost_per_tick = abs(net_delta) / ticks if ticks > 0 else 0.0

    # Cost state via rolling percentile
    cost_series = (delta_series.abs() / (
        (close_s - open_s).abs() / (close_s * 0.0001 + 1e-10)
    )).rolling(100, min_periods=10).apply(
        lambda x: float((x[:-1] < x[-1]).sum() / max(len(x) - 1, 1) * 100),
        raw=True
    )
    cost_rank = float(cost_series.iloc[-1]) if not cost_series.isna().iloc[-1] else 50.0

    if cost_rank >= 90:
        cost_state = "VERY_HIGH"
    elif cost_rank >= 75:
        cost_state = "HIGH"
    elif cost_rank <= 10:
        cost_state = "VERY_LOW"
    elif cost_rank <= 25:
        cost_state = "LOW"
    else:
        cost_state = "NORMAL"

    # Absorption detection
    close_now = float(close_s.iloc[-1])
    open_now  = float(open_s.iloc[-1])
    if buy_vol > sell_vol and close_now <= open_now:
        absorption = "BUY_ABSORPTION"
    elif sell_vol > buy_vol and close_now >= open_now:
        absorption = "SELL_ABSORPTION"
    else:
        absorption = "NONE"

    # Delta-implied close
    eps = 1e-10
    bar_delta_abs = abs(net_delta)
    bar_move_abs  = abs(close_now - open_now)
    equiv_history = (
        (close_s - open_s).abs() /
        (delta_series.abs() + eps)
    ).rolling(100, min_periods=10).median()
    med_equiv = float(equiv_history.iloc[-1]) if not equiv_history.isna().iloc[-1] else 1.0

    price_dir      = 1 if close_now >= open_now else -1
    expected_move  = price_dir * med_equiv * bar_delta_abs
    expected_close = round(float(open_now) + expected_move, 6)
    move_ratio     = round(bar_move_abs / (abs(expected_move) + eps), 4)

    # Divergence detection (simplified pivot-based)
    def detect_divergence(price_series, cvd_s, length):
        if len(price_series) < length * 2 + 1:
            return "NONE"
        # Recent pivot high in price
        price_arr = price_series.values
        cvd_arr   = cvd_s.values
        window    = min(length * 3, len(price_arr) - 1)

        recent_price = price_arr[-window:]
        recent_cvd   = cvd_arr[-window:]

        price_high_idx = np.argmax(recent_price)
        price_low_idx  = np.argmin(recent_price)
        cvd_at_phigh   = recent_cvd[price_high_idx]
        cvd_at_plow    = recent_cvd[price_low_idx]

        current_cvd_val = cvd_arr[-1]
        current_price   = price_arr[-1]

        # Bearish divergence: price at new high, CVD lower
        if current_price >= recent_price[price_high_idx] and current_cvd_val < cvd_at_phigh:
            return "BEARISH"
        # Bullish divergence: price at new low, CVD higher
        if current_price <= recent_price[price_low_idx] and current_cvd_val > cvd_at_plow:
            return "BULLISH"
        return "NONE"

    div_small  = detect_divergence(close_s, cvd_series, div_length_small)
    div_medium = detect_divergence(close_s, cvd_series, div_length_medium)
    div_large  = detect_divergence(close_s, cvd_series, div_length_large)

    return {
        "current_cvd":   round(current_cvd, 2),
        "cvd_direction": cvd_direction,
        "divergence": {
            "small":  div_small,
            "medium": div_medium,
            "large":  div_large
        },
        "aggression": {
            "buy_pct":         buy_pct,
            "sell_pct":        sell_pct,
            "net_delta":       net_delta,
            "imbalance_ratio": imbalance,
            "dominant_side":   dominant_side
        },
        "cost": {
            "cost_per_tick": round(cost_per_tick, 4),
            "cost_state":    cost_state
        },
        "absorption": {
            "type": absorption
        },
        "delta_implied": {
            "expected_close": expected_close,
            "move_ratio":     move_ratio
        },
        "ma": {
            "fast":    round(ma_fast_val, 2),
            "slow":    round(ma_slow_val, 2),
            "ma_bias": ma_bias
        }
    }


# ─────────────────────────────────────────────────────────────
# MASTER COMPUTE FUNCTION
# ─────────────────────────────────────────────────────────────

def compute_all_indicators(df: pd.DataFrame) -> Dict[str, Any]:
    """
    Runs all six indicator computations on a DataFrame.
    Returns a complete indicator_snapshot dict ready for
    the signal enrichment layer.

    Args:
        df: DataFrame with columns [time, open, high, low, close, volume]
            Minimum 50 bars recommended for reliable results.

    Returns:
        Dict matching signal_format.json indicator_snapshot structure.
    """
    if len(df) < 30:
        raise ValueError(f"Need at least 30 bars, got {len(df)}")

    atr_result = compute_atr(df)
    # Remove internal series before returning
    atr_output = {k: v for k, v in atr_result.items() if k != "_series"}

    return {
        "pavp":        compute_pavp(df),
        "zigzag":      compute_zigzag(df),
        "macd":        compute_macd(df),
        "trend_speed": compute_trend_speed(df),
        "cvd":         compute_cvd(df),
        "atr":         atr_output
    }
