"""
kucoin_balance.py
═══════════════════════════════════════════════════════════
KuCoin read-only balance fetcher.

Primary:  KuCoin API (read-only key, no trading permissions)
Fallback: config.json if API fails

To get your KuCoin read-only API key:
  1. Log in to KuCoin
  2. Go to Account > API Management
  3. Create API Key
  4. Set permissions: General (read only) — do NOT enable trading
  5. Copy API Key, Secret, and Passphrase into config.json

config.json format:
{
  "kucoin_api_key":        "your-api-key",
  "kucoin_api_secret":     "your-api-secret",
  "kucoin_api_passphrase": "your-passphrase",
  "fallback_balance_usdt": 1000.0
}
═══════════════════════════════════════════════════════════
"""

import os
import json
import hmac
import hashlib
import base64
import time
import requests
import warnings
warnings.filterwarnings("ignore")

CONFIG_FILE = "config.json"

# SSL patch
import requests as _req
_orig = _req.get
def _no_verify(url, **kw):
    kw.setdefault("verify", False)
    return _orig(url, **kw)
_req.get = _no_verify


def load_config():
    """Load config.json if it exists."""
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def _kucoin_headers(api_key, api_secret, api_passphrase, method, endpoint, body=""):
    """Generate KuCoin API authentication headers."""
    timestamp   = str(int(time.time() * 1000))
    str_to_sign = timestamp + method.upper() + endpoint + body
    signature   = base64.b64encode(
        hmac.new(api_secret.encode(), str_to_sign.encode(), hashlib.sha256).digest()
    ).decode()
    passphrase  = base64.b64encode(
        hmac.new(api_secret.encode(), api_passphrase.encode(), hashlib.sha256).digest()
    ).decode()
    return {
        "KC-API-KEY":         api_key,
        "KC-API-SIGN":        signature,
        "KC-API-TIMESTAMP":   timestamp,
        "KC-API-PASSPHRASE":  passphrase,
        "KC-API-KEY-VERSION": "2",
        "Content-Type":       "application/json",
    }


def fetch_kucoin_balance(config):
    """
    Fetch USDT balance from KuCoin Futures account.
    Returns (balance_usdt, source) where source is 'kucoin_api' or 'fallback'.
    """
    api_key        = config.get("kucoin_api_key", "")
    api_secret     = config.get("kucoin_api_secret", "")
    api_passphrase = config.get("kucoin_api_passphrase", "")

    if not api_key or not api_secret or not api_passphrase:
        fallback = config.get("fallback_balance_usdt", 1000.0)
        return fallback, "config_fallback"

    # Try KuCoin Futures balance first
    try:
        endpoint = "/api/v1/account-overview?currency=USDT"
        headers  = _kucoin_headers(api_key, api_secret, api_passphrase,
                                    "GET", endpoint)
        url = "https://api-futures.kucoin.com" + endpoint
        r   = requests.get(url, headers=headers, timeout=10)
        r.raise_for_status()
        data = r.json()

        if data.get("code") == "200000":
            balance = float(data["data"].get("availableBalance", 0))
            if balance > 0:
                return balance, "kucoin_futures"

    except Exception as e:
        print(f"  [KuCoin Futures API error: {e}]")

    # Try KuCoin Spot balance as fallback
    try:
        endpoint = "/api/v1/accounts?type=trade&currency=USDT"
        headers  = _kucoin_headers(api_key, api_secret, api_passphrase,
                                    "GET", endpoint)
        url = "https://api.kucoin.com" + endpoint
        r   = requests.get(url, headers=headers, timeout=10)
        r.raise_for_status()
        data = r.json()

        if data.get("code") == "200000":
            accounts = data.get("data", [])
            for acc in accounts:
                if acc.get("currency") == "USDT":
                    balance = float(acc.get("available", 0))
                    if balance > 0:
                        return balance, "kucoin_spot"

    except Exception as e:
        print(f"  [KuCoin Spot API error: {e}]")

    # Final fallback to config
    fallback = config.get("fallback_balance_usdt", 1000.0)
    print(f"  [Using config fallback balance: ${fallback:,.2f}]")
    return fallback, "config_fallback"


def get_balance():
    """
    Main entry point. Returns dict with balance info.
    """
    config  = load_config()
    balance, source = fetch_kucoin_balance(config)

    return {
        "balance_usdt": round(balance, 2),
        "source":       source,
        "timestamp":    time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()),
    }


if __name__ == "__main__":
    result = get_balance()
    print(f"\nBalance: ${result['balance_usdt']:,.2f} USDT")
    print(f"Source:  {result['source']}")
    print(f"Time:    {result['timestamp']}")
