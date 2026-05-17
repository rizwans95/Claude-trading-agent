"""
wme_sweep_engine.py
═══════════════════════════════════════════════════════════
Python replication of WME & VAL Filtered (Pine Script v6).

Computes:
  1. Adaptive trend line (dynamic EMA)
  2. Momentum wave engine (speed + acceleration)
  3. Liquidity sweep detection (circles)
  4. Retest cross signals (X marks)
  5. Expansion/exhaustion triangles
  6. HTF trend filter (using 4h via resampling)
  7. Combined WME entry signal

Entry conditions:
  - Liquidity sweep occurred within sweep_lookback bars
  - Cross AND triangle fire on same candle (Config A/C)
    OR within 1 bar (Config B)
  - Speed threshold met
  - HTF trend aligned

Does NOT use the indicator's volume profile for exits.
Exits remain handled by volume_profile_engine.py levels.
═══════════════════════════════════════════════════════════
"""

import numpy as np
import pandas as pd
from typing import Dict, Any, Tuple


# ─────────────────────────────────────────────────────────────
# CONSTANTS (matching PineScript defaults)
# ─────────────────────────────────────────────────────────────

BASE_TREND_LENGTH    = 34
ATR_LENGTH           = 14
VOLATILITY_LOOKBACK  = 100
WAVE_LOOKBACK        = 200
SWEEP_LOOKBACK       = 15
MIN_MOMENTUM         = 0.0
MIN_SWEEP_ATR_RATIO  = 0.3
HTF_BARS_RATIO       = 16    # 4h = 16x 15m bars


# ─────────────────────────────────────────────────────────────
# ATR
# ─────────────────────────────────────────────────────────────

def _compute_atr(df: pd.DataFrame, period: int = ATR_LENGTH) -> np.ndarray:
    high  = df["high"].values
    low   = df["low"].values
    close = df["close"].values
    prev  = np.roll(close, 1); prev[0] = close[0]
    tr    = np.maximum.reduce([high - low, np.abs(high - prev), np.abs(low - prev)])
    alpha = 1.0 / period
    atr   = np.zeros(len(df))
    atr[0] = tr[0]
    for i in range(1, len(df)):
        atr[i] = alpha * tr[i] + (1 - alpha) * atr[i-1]
    return atr


# ─────────────────────────────────────────────────────────────
# 1. ADAPTIVE TREND LINE
# ─────────────────────────────────────────────────────────────

def compute_adaptive_trend(df: pd.DataFrame) -> np.ndarray:
    """
    Replicates the dynamic EMA from WME.
    Speed adapts to volatility and price acceleration.
    """
    close  = df["close"].values
    n      = len(df)
    atr    = _compute_atr(df)

    # SMA of ATR for volatility baseline
    vol_sma = np.zeros(n)
    for i in range(n):
        start     = max(0, i - VOLATILITY_LOOKBACK + 1)
        vol_sma[i]= atr[start:i+1].mean()

    vol_factor     = np.where(vol_sma > 0, atr / vol_sma, 1.0)
    dynamic_length = np.maximum(BASE_TREND_LENGTH / vol_factor, 5.0)
    alpha_base     = 2.0 / (dynamic_length + 1)

    # Acceleration factor
    delta     = np.abs(close - np.roll(close, 1))
    delta[0]  = 0.0
    max_delta = np.zeros(n)
    for i in range(n):
        start        = max(0, i - WAVE_LOOKBACK + 1)
        max_delta[i] = delta[start:i+1].max()

    accel_factor = np.where(max_delta > 0, delta / max_delta, 0.0)
    alpha        = np.minimum(1.0, alpha_base * (1 + accel_factor * 5))

    # Adaptive EMA
    trend    = np.zeros(n)
    trend[0] = close[0]
    for i in range(1, n):
        trend[i] = alpha[i] * close[i] + (1 - alpha[i]) * trend[i-1]

    return trend


# ─────────────────────────────────────────────────────────────
# 2. MOMENTUM WAVE ENGINE
# ─────────────────────────────────────────────────────────────

def compute_momentum_wave(df: pd.DataFrame) -> Dict[str, np.ndarray]:
    """
    Replicates the Wave Momentum Engine.
    Returns speed, acceleration, and wave direction per bar.
    """
    close  = df["close"].values
    open_  = df["open"].values
    n      = len(df)
    atr    = _compute_atr(df)
    trend  = compute_adaptive_trend(df)

    momentum     = np.where(atr > 0, (close - open_) / atr, 0.0)
    speed        = np.zeros(n)
    acceleration = np.zeros(n)

    wave_momentum = 0.0
    wave_length   = 0

    for i in range(1, n):
        above_now  = close[i]   >= trend[i]
        above_prev = close[i-1] >= trend[i-1]
        cross      = above_now != above_prev

        if cross and wave_length > 0:
            wave_momentum = 0.0
            wave_length   = 0

        wave_momentum += momentum[i]
        wave_length   += 1
        speed[i]       = wave_momentum / max(wave_length, 1)

    acceleration[1:] = speed[1:] - speed[:-1]

    # Above/below trend
    above_trend = close >= trend

    # Bullish expansion: speed increasing AND above trend
    bullish_expansion  = (acceleration > 0) & above_trend
    # Bearish exhaustion: speed decreasing AND below trend
    bearish_exhaustion = (acceleration < 0) & ~above_trend

    return {
        "trend":               trend,
        "speed":               speed,
        "acceleration":        acceleration,
        "above_trend":         above_trend,
        "bullish_expansion":   bullish_expansion,
        "bearish_exhaustion":  bearish_exhaustion,
    }


# ─────────────────────────────────────────────────────────────
# 3. HTF TREND (4h approximation via rolling)
# ─────────────────────────────────────────────────────────────

def compute_htf_trend(df: pd.DataFrame, htf_bars: int = HTF_BARS_RATIO) -> np.ndarray:
    """
    Approximates 4h EMA(50) by using a 50 * htf_bars rolling EMA
    on the 15m data. htf_bars=16 means 4h = 16 x 15m bars.
    HTF EMA period = 50 * 16 = 800 bars.
    Returns: htf_bull (bool array) — True when 4h trend is bullish.
    """
    close    = df["close"].values
    htf_ema_period = 50 * htf_bars
    alpha    = 2.0 / (htf_ema_period + 1)
    htf_ema  = np.zeros(len(close))
    htf_ema[0] = close[0]
    for i in range(1, len(close)):
        htf_ema[i] = alpha * close[i] + (1 - alpha) * htf_ema[i-1]

    htf_bull = close >= htf_ema
    return htf_bull


# ─────────────────────────────────────────────────────────────
# 4. LIQUIDITY SWEEPS
# ─────────────────────────────────────────────────────────────

def compute_sweeps(
    df:       pd.DataFrame,
    lookback: int   = SWEEP_LOOKBACK,
    atr_arr:  np.ndarray = None,
    min_atr:  float = MIN_SWEEP_ATR_RATIO,
) -> Dict[str, np.ndarray]:
    """
    Detects liquidity sweeps — price briefly breaks a recent
    high/low but closes back inside.

    sweep_high (orange circle): above recent high, closed below
    sweep_low  (teal circle):   below recent low,  closed above

    Size filter: sweep must be at least min_atr * ATR in size.
    """
    high  = df["high"].values
    low   = df["low"].values
    close = df["close"].values
    n     = len(df)

    if atr_arr is None:
        atr_arr = _compute_atr(df)

    prev_high = np.zeros(n)
    prev_low  = np.zeros(n)

    for i in range(lookback, n):
        prev_high[i] = high[max(0, i - lookback): i].max()
        prev_low[i]  = low[max(0, i - lookback):  i].min()

    sweep_high = (
        (high > prev_high) &
        (close < prev_high) &
        ((high - prev_high) > atr_arr * min_atr)
    )
    sweep_low = (
        (low < prev_low) &
        (close > prev_low) &
        ((prev_low - low) > atr_arr * min_atr)
    )

    # Track bars since last sweep
    bars_since_sweep_high = np.full(n, 999)
    bars_since_sweep_low  = np.full(n, 999)

    last_sh = -999
    last_sl = -999
    for i in range(n):
        if sweep_high[i]: last_sh = i
        if sweep_low[i]:  last_sl = i
        bars_since_sweep_high[i] = i - last_sh if last_sh >= 0 else 999
        bars_since_sweep_low[i]  = i - last_sl if last_sl >= 0 else 999

    return {
        "sweep_high":           sweep_high,
        "sweep_low":            sweep_low,
        "bars_since_sweep_high":bars_since_sweep_high,
        "bars_since_sweep_low": bars_since_sweep_low,
    }


# ─────────────────────────────────────────────────────────────
# 5. RETEST CROSS SIGNALS
# ─────────────────────────────────────────────────────────────

def compute_retest_crosses(
    speed:               np.ndarray,
    htf_bull:            np.ndarray,
    bars_since_sweep_high: np.ndarray,
    bars_since_sweep_low:  np.ndarray,
    min_momentum:        float = MIN_MOMENTUM,
    sweep_lookback:      int   = SWEEP_LOOKBACK,
) -> Dict[str, np.ndarray]:
    """
    Replicates the X cross signals.

    longRetest  = recent sweep of lows  + speed > threshold + HTF bullish
    shortRetest = recent sweep of highs + speed < -threshold + HTF bearish
    """
    recent_sweep_low  = bars_since_sweep_low  < sweep_lookback
    recent_sweep_high = bars_since_sweep_high < sweep_lookback

    long_retest  = recent_sweep_low  & (speed >  min_momentum) & htf_bull
    short_retest = recent_sweep_high & (speed < -min_momentum) & ~htf_bull

    return {
        "long_retest":  long_retest,
        "short_retest": short_retest,
        "recent_sweep_low":  recent_sweep_low,
        "recent_sweep_high": recent_sweep_high,
    }


# ─────────────────────────────────────────────────────────────
# 6. COMBINED WME ENTRY SIGNAL
# ─────────────────────────────────────────────────────────────

def compute_wme_signal(
    df:           pd.DataFrame,
    bar_tolerance: int = 0,   # 0 = same candle only, 1 = within 1 bar
) -> Dict[str, Any]:
    """
    Computes the full WME entry signal.

    Entry rules:
      LONG:
        - sweep_low occurred within SWEEP_LOOKBACK bars
        - long_retest (cross) fires on bar N
        - bullish_expansion (triangle) fires on bar N (or N±tolerance)
        - HTF bullish (already in long_retest)

      SHORT:
        - sweep_high occurred within SWEEP_LOOKBACK bars
        - short_retest (cross) fires on bar N
        - bearish_exhaustion (triangle) fires on bar N (or N±tolerance)
        - HTF bearish (already in short_retest)

    Returns per-bar arrays and current-bar summary.
    """
    n      = len(df)
    atr    = _compute_atr(df)
    wave   = compute_momentum_wave(df)
    htf    = compute_htf_trend(df)
    sweeps = compute_sweeps(df, atr_arr=atr)
    cross  = compute_retest_crosses(
        wave["speed"], htf,
        sweeps["bars_since_sweep_high"],
        sweeps["bars_since_sweep_low"]
    )

    bull_exp  = wave["bullish_expansion"]
    bear_exh  = wave["bearish_exhaustion"]
    long_ret  = cross["long_retest"]
    short_ret = cross["short_retest"]

    # Entry arrays
    long_entry  = np.zeros(n, dtype=bool)
    short_entry = np.zeros(n, dtype=bool)

    for i in range(max(1, bar_tolerance), n):
        # Check cross on bar i
        if long_ret[i]:
            # Triangle on same bar or within tolerance
            tri_window = bull_exp[max(0, i - bar_tolerance): i + 1]
            if tri_window.any():
                long_entry[i] = True

        if short_ret[i]:
            tri_window = bear_exh[max(0, i - bar_tolerance): i + 1]
            if tri_window.any():
                short_entry[i] = True

    # Count sweeps in last 20 bars for confidence modifier
    sweep_low_count  = np.zeros(n, dtype=int)
    sweep_high_count = np.zeros(n, dtype=int)
    for i in range(20, n):
        sweep_low_count[i]  = sweeps["sweep_low"][i-20:i].sum()
        sweep_high_count[i] = sweeps["sweep_high"][i-20:i].sum()

    # Current bar summary
    curr = n - 1
    return {
        # Per-bar arrays
        "long_entry":          long_entry,
        "short_entry":         short_entry,
        "sweep_high":          sweeps["sweep_high"],
        "sweep_low":           sweeps["sweep_low"],
        "long_retest":         long_ret,
        "short_retest":        short_ret,
        "bullish_expansion":   bull_exp,
        "bearish_exhaustion":  bear_exh,
        "speed":               wave["speed"],
        "trend":               wave["trend"],
        "htf_bull":            htf,
        "sweep_low_count_20":  sweep_low_count,
        "sweep_high_count_20": sweep_high_count,

        # Current bar state
        "current": {
            "long_entry":        bool(long_entry[curr]),
            "short_entry":       bool(short_entry[curr]),
            "sweep_low_recent":  bool(cross["recent_sweep_low"][curr]),
            "sweep_high_recent": bool(cross["recent_sweep_high"][curr]),
            "speed":             round(float(wave["speed"][curr]), 4),
            "htf_bull":          bool(htf[curr]),
            "above_trend":       bool(wave["above_trend"][curr]),
            "bars_since_sweep_low":  int(sweeps["bars_since_sweep_low"][curr]),
            "bars_since_sweep_high": int(sweeps["bars_since_sweep_high"][curr]),
            "sweep_count_20":    int(sweep_low_count[curr] + sweep_high_count[curr]),
        }
    }


# ─────────────────────────────────────────────────────────────
# 7. CONFIDENCE MODIFIER
# ─────────────────────────────────────────────────────────────

def get_wme_confidence_modifier(
    wme:      Dict[str, Any],
    in_lvn:   bool,
    in_hvn:   bool,
    bar_idx:  int,
) -> Dict[str, Any]:
    """
    Returns a confidence modifier and notes based on WME context.

    Boosters:
      + LVN entry: +15 (price will accelerate)
      + Strong speed: +10 if abs(speed) > 1.0
      + Recent sweep (< 5 bars ago): +10

    Reducers:
      - HVN entry: -15 (price may stall)
      - Multiple sweeps (>2 in 20 bars): -10 (cascading, not reversal)
      - Weak speed (< 0.3): -10
    """
    curr      = wme["current"]
    speed     = abs(curr["speed"])
    n_sweeps  = curr["sweep_count_20"]
    bars_ago  = min(curr["bars_since_sweep_low"], curr["bars_since_sweep_high"])

    modifier  = 0
    notes     = []

    if in_lvn:
        modifier += 15
        notes.append("LVN entry — price will accelerate through thin zone")
    if in_hvn:
        modifier -= 15
        notes.append("HVN entry — price may stall in thick volume zone")
    if speed > 1.0:
        modifier += 10
        notes.append(f"Strong momentum speed {speed:.2f}")
    elif speed < 0.3:
        modifier -= 10
        notes.append(f"Weak momentum speed {speed:.2f}")
    if bars_ago < 5:
        modifier += 10
        notes.append(f"Very recent sweep ({bars_ago} bars ago)")
    if n_sweeps > 2:
        modifier -= 10
        notes.append(f"Multiple sweeps ({n_sweeps}) in 20 bars — may be trend not reversal")

    return {
        "modifier": modifier,
        "notes":    notes,
    }
