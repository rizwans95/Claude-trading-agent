"""
data_fetcher.py
═══════════════════════════════════════════════════════════
Free OHLCV data — no API key required.

Sources:
  Binance public REST API  → crypto pairs (BTC, ETH, SOL…)
  Yahoo Finance (yfinance) → stocks, ETFs, forex, crypto

Auto-detection: if the symbol looks like a crypto pair
(ends in USDT/BTC/ETH/BNB/USDC) → Binance, else → Yahoo.
Falls back to the other source on error.
═══════════════════════════════════════════════════════════
"""

import ssl
import warnings

import requests
import urllib3
import pandas as pd
from typing import Optional

# ── SSL resilience for Python 3.14+ on Windows ───────────────────────────────
# Python 3.14 ships without bundled CA certs on Windows, causing
# CERTIFICATE_VERIFY_FAILED on all outbound HTTPS. The market data sources
# used here are public and well-known; disabling cert verification is
# acceptable for read-only price data fetches.
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
warnings.filterwarnings("ignore", message="Unverified HTTPS request")

# Patch Python's default SSL context (for requests / urllib3 paths)
ssl._create_default_https_context = ssl._create_unverified_context  # type: ignore[attr-defined]

def _make_requests_session() -> requests.Session:
    s = requests.Session()
    s.verify = False
    return s

# ── curl_cffi session for yfinance ────────────────────────────────────────────
# yfinance ≥ 0.2.50 uses curl_cffi instead of requests. curl_cffi ignores the
# standard ssl._create_default_https_context patch. The fix is to pass a
# curl_cffi Session with verify=False and impersonate="chrome110" (mimics
# Chrome TLS fingerprint, bypasses Yahoo Finance rate-limiting on first fetch).
_CURL_SESSION = None

def _get_yf_session():
    """Return a shared curl_cffi session suitable for yfinance."""
    global _CURL_SESSION
    if _CURL_SESSION is None:
        try:
            from curl_cffi import requests as crequests
            _CURL_SESSION = crequests.Session(verify=False, impersonate="chrome110")
        except ImportError:
            pass          # curl_cffi not installed — yfinance will use its default
    return _CURL_SESSION

try:
    import yfinance as yf
    YFINANCE_AVAILABLE = True
except ImportError:
    YFINANCE_AVAILABLE = False

BINANCE_BASE = "https://api.binance.com/api/v3"

# Yahoo Finance interval → (yfinance interval, max lookback period)
# 5m futures data (ES=F) is often only available for ~5 days; keep short.
TF_YFINANCE: dict = {
    "1m":  ("1m",  "5d"),
    "5m":  ("5m",  "5d"),
    "15m": ("15m", "60d"),
    "30m": ("30m", "60d"),
    "1h":  ("60m", "180d"),
    "4h":  ("60m", "730d"),   # resampled from 1h — 2y for adequate backtest depth
    "1d":  ("1d",  "5y"),
}

# Binance kline interval strings
TF_BINANCE: dict = {
    "1m": "1m", "5m": "5m", "15m": "15m", "30m": "30m",
    "1h": "1h", "4h": "4h", "1d": "1d",
}

CRYPTO_SUFFIXES = {"USDT", "BTC", "ETH", "BNB", "USDC", "BUSD", "USD"}


def _is_crypto(symbol: str) -> bool:
    """Detect crypto by common quote-currency suffixes."""
    sym = symbol.upper().replace("/", "").replace("-", "").split("=")[0]
    return any(sym.endswith(s) for s in CRYPTO_SUFFIXES)


def _to_binance_sym(symbol: str) -> str:
    """Convert Yahoo-style ticker to Binance pair. BTC-USD → BTCUSDT."""
    sym = symbol.upper().replace("/", "").replace("-", "").split("=")[0]
    # Yahoo appends USD for crypto; Binance uses USDT
    if sym.endswith("USD") and not sym.endswith("USDT"):
        sym = sym + "T"
    return sym


# ─────────────────────────────────────────────────────────────
# BINANCE PUBLIC REST (crypto only, no auth)
# ─────────────────────────────────────────────────────────────

def fetch_binance(symbol: str, timeframe: str = "1h", bars: int = 200) -> pd.DataFrame:
    """
    Fetch OHLCV from Binance public REST API.
    Free, no API key. Crypto pairs only.
    Max 1000 bars per request.
    """
    interval = TF_BINANCE.get(timeframe, "1h")
    params = {
        "symbol":   _to_binance_sym(symbol),
        "interval": interval,
        "limit":    min(bars, 1000),
    }
    sess = _make_requests_session()
    resp = sess.get(f"{BINANCE_BASE}/klines", params=params, timeout=10)
    resp.raise_for_status()

    data = resp.json()
    if not data:
        raise ValueError(f"No Binance data for {symbol}")

    rows = [
        {
            "time":   pd.Timestamp(k[0], unit="ms"),
            "open":   float(k[1]),
            "high":   float(k[2]),
            "low":    float(k[3]),
            "close":  float(k[4]),
            "volume": float(k[5]),
        }
        for k in data
    ]
    return pd.DataFrame(rows)


# ─────────────────────────────────────────────────────────────
# YAHOO FINANCE (stocks, ETFs, forex, crypto — via yfinance)
# ─────────────────────────────────────────────────────────────

def fetch_yfinance(symbol: str, timeframe: str = "1h", bars: int = 200) -> pd.DataFrame:
    """
    Fetch OHLCV from Yahoo Finance via yfinance.
    Free, no API key. Supports stocks, ETFs, forex, crypto.
    """
    if not YFINANCE_AVAILABLE:
        raise ImportError("yfinance not installed. Run: pip install yfinance")

    interval, period = TF_YFINANCE.get(timeframe, ("60m", "730d"))

    yf_session = _get_yf_session()
    ticker = yf.Ticker(symbol, session=yf_session) if yf_session else yf.Ticker(symbol)
    df = ticker.history(period=period, interval=interval, auto_adjust=True)

    if df.empty:
        raise ValueError(f"No yfinance data for '{symbol}'. Check the ticker.")

    df = df.reset_index()
    df.columns = [c.lower() for c in df.columns]

    # Normalize time column name across yfinance versions
    for col in ("datetime", "date", "index"):
        if col in df.columns and col != "time":
            df = df.rename(columns={col: "time"})
            break

    df["time"] = pd.to_datetime(df["time"]).dt.tz_localize(None)
    df = df[["time", "open", "high", "low", "close", "volume"]].dropna()

    # Resample 4h from 1h data (yfinance has no native 4h interval)
    if timeframe == "4h":
        df = df.set_index("time")
        df = df.resample("4h").agg(
            {"open": "first", "high": "max", "low": "min",
             "close": "last", "volume": "sum"}
        ).dropna().reset_index()

    return df.tail(bars).reset_index(drop=True)


# ─────────────────────────────────────────────────────────────
# UNIFIED FETCHER
# ─────────────────────────────────────────────────────────────

def fetch_ohlcv(
    symbol:    str,
    timeframe: str = "1h",
    bars:      int = 200,
    source:    str = "auto",
) -> pd.DataFrame:
    """
    Fetch OHLCV from the best free source.

    source: 'auto' | 'binance' | 'yahoo'
      auto  → Binance for crypto symbols, Yahoo for everything else.
              Falls back to the other source on any error.
      binance → Binance only (crypto pairs must be in PAIR format, e.g. BTCUSDT)
      yahoo   → Yahoo Finance only

    Returns a DataFrame with columns:
        time (datetime64), open, high, low, close, volume (float64)
    Sorted ascending by time.
    """
    use_binance = (
        source == "binance"
        or (source == "auto" and _is_crypto(symbol))
    )

    if use_binance:
        try:
            return fetch_binance(symbol, timeframe, bars)
        except Exception as e:
            # Fallback to Yahoo (some crypto tickers like BTC-USD work there too)
            try:
                return fetch_yfinance(symbol, timeframe, bars)
            except Exception:
                raise ValueError(
                    f"Could not fetch {symbol} from Binance ({e}) or Yahoo Finance."
                )
    else:
        try:
            return fetch_yfinance(symbol, timeframe, bars)
        except Exception as e:
            try:
                return fetch_binance(symbol, timeframe, bars)
            except Exception:
                raise ValueError(
                    f"Could not fetch {symbol} from Yahoo Finance ({e}) or Binance."
                )
