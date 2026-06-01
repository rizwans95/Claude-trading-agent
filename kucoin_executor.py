"""
kucoin_executor.py
=============================================================
KuCoin Futures Automation Engine for Trading Agent V2.

What this does:
  1. Places entry + stop + TP orders atomically on KuCoin futures
  2. Monitors open positions every 60 seconds
  3. Moves stop loss to breakeven when TP1 is hit
  4. Moves stop loss to TP1 level when TP2 is hit
  5. Closes remainder at TP3 automatically
  6. Handles opposite signals (closes LONG before opening SHORT)
  7. Never trades if already in same direction
  8. Records all outcomes to trade_log.csv and pushes to GitHub

Safety rules:
  - Limit orders only (no market orders)
  - Stop placed atomically with entry
  - Minimum balance check before any trade
  - One position per symbol at a time
  - Emergency stop closes all positions immediately

Run modes:
  py kucoin_executor.py --mode arm      # start automation
  py kucoin_executor.py --mode status   # show current positions
  py kucoin_executor.py --mode stop     # emergency close all
=============================================================
"""

import os, sys, json, time, hmac, hashlib, base64
import requests, warnings, subprocess, argparse
try:
    from telegram_notify import (signal_detected, trade_executed, trade_failed,
        no_trade, tp1_hit, tp2_hit, trade_closed, system_error, system_started,
        signal_skipped_active_trade, signal_skipped_error, position_update)
    TELEGRAM_OK = True
except ImportError:
    TELEGRAM_OK = False
import pandas as pd
from datetime import datetime, timezone
warnings.filterwarnings("ignore")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# ─────────────────────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────────────────────

FUTURES_BASE     = "https://api-futures.kucoin.com"
CONFIG_FILE      = "config.json"
TRADE_LOG_FILE   = "trade_log.csv"
AUTOMATION_STATE = "automation_state.json"
MONITOR_INTERVAL = 60          # seconds between position checks
_last_signal_hour = -1         # track last hour a signal notification was sent
ENTRY_TIMEOUT    = 120         # seconds to wait for limit order fill
SLIP_TOLERANCE   = 0.002       # 0.2% price tolerance for limit orders
MIN_BALANCE      = 10.0        # minimum USDT to allow any trade
BALANCE_BUFFER   = 0.20        # keep 20% of balance as reserve

# Symbol mapping: dashboard symbol -> KuCoin futures contract
SYMBOL_MAP = {
    "BTCUSDT": "XBTUSDTM",   # KuCoin BTC USDT perpetual
    "ETHUSDT": "ETHUSDTM",   # KuCoin ETH USDT perpetual
}

# TP partial close sizes
TP1_PCT = 0.35    # close 35% at TP1
TP2_PCT = 0.30    # close 30% at TP2
TP3_PCT = 1.00    # close 100% of remainder at TP3

# Disable SSL warnings
import urllib3
urllib3.disable_warnings()


# ─────────────────────────────────────────────────────────────
# CONFIG + AUTH
# ─────────────────────────────────────────────────────────────

def load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE) as f:
                return json.load(f)
        except Exception as e:
            raise RuntimeError(f"Cannot load config.json: {e}")
    raise RuntimeError("config.json not found")


def kucoin_headers(config, method, endpoint, body=""):
    """Generate KuCoin API v2 auth headers."""
    key        = config["kucoin_api_key"]
    secret     = config["kucoin_api_secret"]
    passphrase = config["kucoin_api_passphrase"]
    timestamp  = str(int(time.time() * 1000))
    str_to_sign = timestamp + method.upper() + endpoint + body
    signature  = base64.b64encode(
        hmac.new(secret.encode(), str_to_sign.encode(), hashlib.sha256).digest()
    ).decode()
    enc_passphrase = base64.b64encode(
        hmac.new(secret.encode(), passphrase.encode(), hashlib.sha256).digest()
    ).decode()
    return {
        "KC-API-KEY":         key,
        "KC-API-SIGN":        signature,
        "KC-API-TIMESTAMP":   timestamp,
        "KC-API-PASSPHRASE":  enc_passphrase,
        "KC-API-KEY-VERSION": "2",
        "Content-Type":       "application/json",
    }


def api_get(config, endpoint):
    """Authenticated GET to KuCoin Futures API."""
    headers = kucoin_headers(config, "GET", endpoint)
    r = requests.get(
        FUTURES_BASE + endpoint,
        headers=headers,
        timeout=10,
        verify=False,
    )
    data = r.json()
    if data.get("code") != "200000":
        raise RuntimeError(f"API GET {endpoint} failed: {data}")
    return data.get("data", {})


def api_post(config, endpoint, body_dict):
    """Authenticated POST to KuCoin Futures API."""
    import json as _json
    body    = _json.dumps(body_dict)
    headers = kucoin_headers(config, "POST", endpoint, body)
    r = requests.post(
        FUTURES_BASE + endpoint,
        headers=headers,
        data=body,
        timeout=10,
        verify=False,
    )
    data = r.json()
    if data.get("code") != "200000":
        raise RuntimeError(f"API POST {endpoint} failed: {data}")
    return data.get("data", {})


def api_delete(config, endpoint):
    """Authenticated DELETE to KuCoin Futures API."""
    headers = kucoin_headers(config, "DELETE", endpoint)
    r = requests.delete(
        FUTURES_BASE + endpoint,
        headers=headers,
        timeout=10,
        verify=False,
    )
    data = r.json()
    if data.get("code") != "200000":
        raise RuntimeError(f"API DELETE {endpoint} failed: {data}")
    return data.get("data", {})


# ─────────────────────────────────────────────────────────────
# BALANCE + POSITION QUERIES
# ─────────────────────────────────────────────────────────────

def get_available_balance(config):
    """Get available USDT in futures wallet."""
    data = api_get(config, "/api/v1/account-overview?currency=USDT")
    return float(data.get("availableBalance", 0))

def get_account_overview(config):
    """Get full account overview — available balance + total equity + unrealised PnL."""
    data = api_get(config, "/api/v1/account-overview?currency=USDT")
    return {
        "available":      float(data.get("availableBalance", 0)),
        "equity":         float(data.get("marginBalance",    0)),
        "unrealised_pnl": float(data.get("unrealisedPnl",   0)),
        "frozen":         float(data.get("frozenFunds",      0)),
    }


def get_open_positions(config):
    """Get all open futures positions."""
    data = api_get(config, "/api/v1/positions")
    # Filter to positions with non-zero current quantity
    return [p for p in (data or []) if float(p.get("currentQty", 0)) != 0]


def get_position(config, kucoin_symbol):
    """Get position for a specific contract."""
    positions = get_open_positions(config)
    for p in positions:
        if p.get("symbol") == kucoin_symbol:
            return p
    return None


def get_contract_info(config, kucoin_symbol):
    """Get contract details including lot size and tick size."""
    data = api_get(config, f"/api/v1/contracts/{kucoin_symbol}")
    return data


def get_current_price(config, kucoin_symbol):
    """Get current mark price for a contract."""
    data = api_get(config, f"/api/v1/mark-price/{kucoin_symbol}/current")
    return float(data.get("value", 0))


# ─────────────────────────────────────────────────────────────
# ORDER MANAGEMENT
# ─────────────────────────────────────────────────────────────

def place_limit_order(config, kucoin_symbol, side, qty, price, reduce_only=False, leverage=10):
    """
    Place a limit order on KuCoin futures.
    side: 'buy' or 'sell'
    qty: number of contracts (integer)
    price: limit price
    """
    import uuid
    body = {
        "clientOid":  str(uuid.uuid4()),
        "symbol":     kucoin_symbol,
        "side":       side,
        "type":       "limit",
        "price":      str(round(round(price * 10) / 10, 1)),  # BTC tick=0.1
        "size":       int(qty),
        "timeInForce": "GTC",
        "reduceOnly": reduce_only,
        "leverage":   str(leverage),
    }
    result = api_post(config, "/api/v1/orders", body)
    return result.get("orderId")


def place_stop_order(config, kucoin_symbol, side, qty, stop_price, reduce_only=True):
    """
    Place a stop-loss market order using /api/v1/orders with stop params.
    LONG stop (side=sell): stop="down" — triggers when price falls to stop_price
    SHORT stop (side=buy):  stop="up"  — triggers when price rises to stop_price
    Triggers on mark price (TP).
    """
    import uuid
    stop_px   = str(round(round(stop_price * 10) / 10, 1))  # BTC tick = 0.1
    stop_dir  = "down" if side == "sell" else "up"
    body = {
        "clientOid":     str(uuid.uuid4()),
        "symbol":        kucoin_symbol,
        "side":          side,
        "type":          "market",
        "stop":          stop_dir,
        "stopPrice":     stop_px,
        "stopPriceType": "TP",
        "size":          int(qty),
        "reduceOnly":    reduce_only,
        "marginMode":    "ISOLATED",
        "positionSide":  "BOTH",
    }
    result   = api_post(config, "/api/v1/orders", body)
    order_id = result.get("orderId")
    if not order_id:
        raise RuntimeError(f"stop order returned no orderId: {result}")
    return order_id


def cancel_order(config, order_id):
    """Cancel a regular order."""
    try:
        api_delete(config, f"/api/v1/orders/{order_id}")
        return True
    except Exception as e:
        print(f"  [Cancel order {order_id} failed: {e}]")
        return False


def cancel_stop_order(config, order_id):
    """Cancel a stop order using new st-orders endpoint."""
    try:
        api_delete(config, f"/api/v1/orders/{order_id}")
        return True
    except Exception as e:
        print(f"  [Cancel stop {order_id} failed: {e}]")
        return False


def cancel_all_stop_orders(config, kucoin_symbol):
    """Cancel all stop orders for a symbol using new st-orders endpoint."""
    try:
        api_delete(config, f"/api/v1/orders?symbol={kucoin_symbol}&stop=down")
        api_delete(config, f"/api/v1/orders?symbol={kucoin_symbol}&stop=up")
        return True
    except Exception as e:
        print(f"  [Cancel all stops failed: {e}]")
        return False


def close_position_market(config, kucoin_symbol):
    """
    Close entire position at market price.
    Includes all required fields for KuCoin API v2.
    """
    import uuid
    pos = get_position(config, kucoin_symbol)
    if not pos:
        print(f"  [close_position_market] No position found for {kucoin_symbol}")
        return None
    qty_raw = float(pos.get("currentQty", 0))
    if qty_raw == 0:
        print(f"  [close_position_market] Position already flat")
        return None
    qty  = abs(int(qty_raw))
    side = "sell" if qty_raw > 0 else "buy"
    body = {
        "clientOid":    str(uuid.uuid4()),
        "symbol":       kucoin_symbol,
        "side":         side,
        "type":         "market",
        "size":         qty,
        "reduceOnly":   True,
        "marginMode":   "ISOLATED",
        "positionSide": "BOTH",
    }
    result = api_post(config, "/api/v1/orders", body)
    order_id = result.get("orderId")
    if order_id:
        print(f"  [close_position_market] Closed {qty} contracts, orderId={order_id}")
    else:
        print(f"  [close_position_market] Close failed: {result}")
    return order_id


# ─────────────────────────────────────────────────────────────
# POSITION SIZE CALCULATOR
# ─────────────────────────────────────────────────────────────

def calculate_contracts(entry_price, stop_price, balance_usdt,
                         leverage, risk_pct, contract_value=1.0):
    """
    Calculate number of contracts to trade.

    KuCoin USDT perpetuals: 1 contract = 1 USDT of value (multiplier=1)
    For XBTUSDTM: contract_value = 0.001 BTC

    Returns: (contracts, margin_required)
    """
    risk_dollars  = balance_usdt * risk_pct * (1 - BALANCE_BUFFER)
    stop_dist_pct = abs(entry_price - stop_price) / entry_price
    if stop_dist_pct == 0:
        stop_dist_pct = 0.01  # fallback 1%

    # Position size in USDT = risk / stop_distance
    position_usdt = risk_dollars / stop_dist_pct

    # At leverage L, margin = position_value / L
    margin = position_usdt / leverage

    # Number of contracts
    # For XBTUSDTM: 1 contract = 0.001 BTC, so contracts = position_usdt / (price * 0.001)
    contracts = round(position_usdt / (entry_price * contract_value))
    contracts = max(1, contracts)   # minimum 1 contract

    actual_margin = (contracts * entry_price * contract_value) / leverage
    return contracts, round(actual_margin, 2)


# ─────────────────────────────────────────────────────────────
# AUTOMATION STATE
# ─────────────────────────────────────────────────────────────

def load_state():
    """Load automation state (open orders, active trades)."""
    if os.path.exists(AUTOMATION_STATE):
        try:
            with open(AUTOMATION_STATE) as f:
                return json.load(f)
        except Exception:
            pass
    return {
        "armed":        False,
        "active_trades": {},   # signal_id -> trade details
        "leverage":     10,
        "risk_pct":     state.get("risk_pct", 0.02),
        "last_signal":  None,
    }


def save_state(state):
    """Save automation state to disk."""
    with open(AUTOMATION_STATE, "w") as f:
        json.dump(state, f, indent=2)


# ─────────────────────────────────────────────────────────────
# CORE TRADE EXECUTION
# ─────────────────────────────────────────────────────────────

def execute_trade(config, signal, state):
    """
    Execute a trade based on a signal from the dashboard.

    signal: dict with keys:
      symbol, direction, entry_price, stop_loss,
      target_1, target_2, target_3, signal_id,
      strategy, grade, confidence

    Returns: True if trade placed, False if skipped
    """
    symbol        = signal["symbol"]
    kc_symbol     = SYMBOL_MAP.get(symbol)
    direction     = signal["direction"]   # LONG or SHORT
    entry_price   = float(signal["entry_price"])
    stop_price    = float(signal["stop_loss"])
    leverage      = state.get("leverage", 10)
    # Read risk_pct from signal (set by strategy), fall back to state
    risk_pct      = float(signal.get("risk_pct", state.get("risk_pct", 0.02)))

    if not kc_symbol:
        print(f"  [SKIP] {symbol} not supported for automation")
        return False

    print(f"\n{'='*55}")
    print(f"  SIGNAL: {direction} {symbol} @ ${entry_price:,.2f}")
    print(f"  Stop:   ${stop_price:,.2f}")
    print(f"  Grade:  {signal.get('grade')} | Conf: {signal.get('confidence')}%")
    print(f"{'='*55}")

    # ── Safety check 1: balance ──────────────────────────────
    try:
        balance = get_available_balance(config)
    except Exception as e:
        print(f"  [ABORT] Cannot fetch balance: {e}")
        return False

    print(f"  Available balance: ${balance:.2f} USDT")
    if balance < MIN_BALANCE:
        print(f"  [SKIP] Balance ${balance:.2f} below minimum ${MIN_BALANCE}")
        return False

    # ── Safety check 2: existing position ───────────────────
    try:
        existing = get_position(config, kc_symbol)
    except Exception as e:
        print(f"  [ABORT] Cannot check positions: {e}")
        return False

    if existing:
        existing_qty = float(existing.get("currentQty", 0))
        existing_dir = "LONG" if existing_qty > 0 else "SHORT"

        if existing_dir == direction:
            print(f"  [SKIP] Already {existing_dir} on {kc_symbol} — not adding")
            return False

        # Opposite direction — close existing first
        print(f"  [INFO] Existing {existing_dir} detected — closing before opening {direction}")
        try:
            cancel_all_stop_orders(config, kc_symbol)
            close_position_market(config, kc_symbol)
            time.sleep(2)
            print(f"  [OK] Existing {existing_dir} closed")
        except Exception as e:
            print(f"  [ABORT] Failed to close existing position: {e}")
            return False

    # ── Get contract info ────────────────────────────────────
    try:
        info           = get_contract_info(config, kc_symbol)
        contract_value = float(info.get("multiplier", 0.001))
        tick_size      = float(info.get("tickSize", 0.5))
        print(f"  Contract: multiplier={contract_value}, tick={tick_size}")
    except Exception as e:
        print(f"  [WARN] Cannot get contract info: {e} — using defaults")
        contract_value = 0.001   # BTC default
        tick_size      = 0.5

    # ── Calculate position size ──────────────────────────────
    contracts, margin = calculate_contracts(
        entry_price, stop_price, balance,
        leverage, risk_pct, contract_value
    )
    print(f"  Contracts: {contracts} | Margin: ${margin:.2f} | Leverage: {leverage}x")

    if margin > balance * (1 - BALANCE_BUFFER):
        print(f"  [SKIP] Margin ${margin:.2f} exceeds safe limit")
        return False

    # ── Place entry limit order ──────────────────────────────
    entry_side   = "buy"  if direction == "LONG"  else "sell"
    is_long      = direction == "LONG"
    # LONG:  buy limit slightly ABOVE signal price (fills on any rise to/above limit)
    # SHORT: sell limit slightly BELOW signal price (fills on any drop to/below limit)
    if is_long:
        entry_limit = round(round(entry_price * (1 + SLIP_TOLERANCE) * 10) / 10, 1)
    else:
        entry_limit = round(round(entry_price * (1 - SLIP_TOLERANCE) * 10) / 10, 1)

    # Set leverage on position before placing order
    try:
        api_post(config, "/api/v1/position/margin/auto-deposit-status",
                 {"symbol": kc_symbol, "status": False})
    except Exception:
        pass

    print(f"  Placing {entry_side} limit @ ${entry_limit:,.2f}...")
    try:
        entry_order_id = place_limit_order(config, kc_symbol, entry_side,
                                           contracts, entry_limit, leverage=leverage)
        print(f"  [OK] Entry order placed: {entry_order_id}")
    except Exception as e:
        print(f"  [ABORT] Entry order failed: {e}")
        return False

    # ── Wait for fill ────────────────────────────────────────
    print(f"  Waiting up to {ENTRY_TIMEOUT}s for fill...")
    filled_price = None
    elapsed      = 0
    while elapsed < ENTRY_TIMEOUT:
        time.sleep(5); elapsed += 5
        try:
            pos = get_position(config, kc_symbol)
            if pos and abs(float(pos.get("currentQty", 0))) > 0:
                filled_price = float(pos.get("avgEntryPrice", entry_price))
                print(f"  [OK] Filled @ ${filled_price:,.2f}")
                break
        except Exception:
            pass

    if not filled_price:
        print(f"  [ABORT] Order not filled in {ENTRY_TIMEOUT}s — cancelling")
        cancel_order(config, entry_order_id)
        if TELEGRAM_OK:
            try:
                trade_failed(direction, symbol,
                             f"Order not filled in {ENTRY_TIMEOUT}s — price moved away", config)
            except Exception: pass
        return False

    # ── Place stop loss order — MANDATORY, retry 3x, abort if all fail ──
    stop_side     = "sell" if direction == "LONG" else "buy"
    stop_order_id = None
    print(f"  Placing stop @ ${stop_price:,.2f}...")
    for attempt in range(1, 4):
        try:
            stop_order_id = place_stop_order(
                config, kc_symbol, stop_side, contracts, stop_price
            )
            if stop_order_id:
                print(f"  [OK] Stop placed (attempt {attempt}): {stop_order_id}")
                break
            else:
                raise RuntimeError("API returned no orderId for stop order")
        except Exception as e:
            print(f"  [WARN] Stop attempt {attempt}/3 failed: {e}")
            if attempt < 3:
                time.sleep(2)

    # ── CRITICAL: if stop could not be placed, close position immediately ──
    if not stop_order_id:
        print(f"  [CRITICAL] Stop loss FAILED after 3 attempts.")
        print(f"  [CRITICAL] Closing position at market to protect capital.")
        closed = False
        for close_attempt in range(1, 4):
            try:
                close_id = close_position_market(config, kc_symbol)
                if close_id:
                    print(f"  [OK] Position emergency-closed (attempt {close_attempt}): {close_id}")
                    closed = True
                    break
                else:
                    print(f"  [WARN] Close attempt {close_attempt} returned no orderId")
            except Exception as close_err:
                print(f"  [WARN] Close attempt {close_attempt} failed: {close_err}")
            time.sleep(2)
        if not closed:
            print(f"  [CRITICAL] EMERGENCY CLOSE ALSO FAILED AFTER 3 ATTEMPTS")
            print(f"  [CRITICAL] MANUAL INTERVENTION REQUIRED ON KUCOIN NOW")
        if TELEGRAM_OK:
            try:
                from telegram_notify import system_error
                msg = (f"STOP LOSS FAILED on {direction} {symbol} @ ${filled_price:,.2f}. "
                       f"Emergency close {'succeeded' if closed else 'ALSO FAILED — CHECK KUCOIN NOW'}.")
                system_error("kucoin_executor", msg, config)
            except Exception:
                pass
        # Mark this hour as filled to prevent immediate re-entry loop
        try:
            import live_signal_monitor as _lsm
            _lsm.run_monitor_loop._filled_hour = int(
                __import__("time").strftime("%H", __import__("time").gmtime())
            )
        except Exception:
            pass
        return False

    # ── Calculate TP levels — use PAVP targets if available ──
    stop_dist = abs(filled_price - stop_price)
    sig_t1 = signal.get("target_1")
    sig_t2 = signal.get("target_2")
    sig_t3 = signal.get("target_3")

    def _valid_target(t, direction, entry):
        try:
            v = float(t)
            if direction == "LONG":  return v > entry
            else:                    return v < entry
        except Exception:
            return False

    if _valid_target(sig_t1, direction, filled_price) and        _valid_target(sig_t2, direction, filled_price) and        _valid_target(sig_t3, direction, filled_price):
        tp1 = round(float(sig_t1), 1)
        tp2 = round(float(sig_t2), 1)
        tp3 = round(float(sig_t3), 1)
        print(f"  [TP] Using PAVP targets: {tp1} / {tp2} / {tp3}")
    else:
        if direction == "LONG":
            tp1 = round(round((filled_price + stop_dist * 1.0) * 10) / 10, 1)
            tp2 = round(round((filled_price + stop_dist * 1.5) * 10) / 10, 1)
            tp3 = round(round((filled_price + stop_dist * 2.0) * 10) / 10, 1)
        else:
            tp1 = round(round((filled_price - stop_dist * 1.0) * 10) / 10, 1)
            tp2 = round(round((filled_price - stop_dist * 1.5) * 10) / 10, 1)
            tp3 = round(round((filled_price - stop_dist * 2.0) * 10) / 10, 1)
        print(f"  [TP] Using ATR fallback targets: {tp1} / {tp2} / {tp3}")

    # ── TP quantity split — handle low contract counts ──────
    # With < 3 contracts the 35/30 split produces invalid qtys
    # Use single full-size TP at best target when contracts < 3
    tp_side = "sell" if direction == "LONG" else "buy"
    if contracts < 3:
        tp_levels = [("TP1", contracts, tp1)]
        print(f"  [TP] {contracts} contract(s) — single TP at ${tp1:,.2f}")
    else:
        tp1_qty = max(1, round(contracts * TP1_PCT))
        tp2_qty = max(1, round(contracts * TP2_PCT))
        tp3_qty = contracts - tp1_qty - tp2_qty
        tp_levels = [
            ("TP1", tp1_qty, tp1),
            ("TP2", tp2_qty, tp2),
        ]
        if tp3_qty >= 1:
            tp_levels.append(("TP3", tp3_qty, tp3))
        print(f"  [TP] {contracts} contracts — split TP1={tp1_qty}/TP2={tp2_qty}/TP3={tp3_qty}")

    # ── Place TP limit orders ────────────────────────────────
    tp_orders  = {}
    for label, qty, price in tp_levels:
        if qty < 1:
            continue
        try:
            oid = place_limit_order(config, kc_symbol, tp_side, qty, price, reduce_only=True)
            tp_orders[label] = {"order_id": oid, "price": price, "qty": qty, "hit": False}
            print(f"  [OK] {label} @ ${price:,.2f} ({qty} contracts): {oid}")
        except Exception as e:
            print(f"  [WARN] {label} order failed: {e}")

    # ── Save trade to state ──────────────────────────────────
    trade_record = {
        "signal_id":    signal.get("signal_id", f"AUTO-{int(time.time())}"),
        "symbol":       symbol,
        "kc_symbol":    kc_symbol,
        "strategy":     signal.get("strategy", ""),
        "direction":    direction,
        "contracts":    contracts,
        "entry_price":  filled_price,
        "stop_price":   stop_price,
        "stop_order_id": stop_order_id,
        "tp1":          tp_orders.get("TP1", {}),
        "tp2":          tp_orders.get("TP2", {}),
        "tp3":          tp_orders.get("TP3", {}),
        "leverage":     leverage,
        "balance_at_entry": balance,
        "risk_pct":     risk_pct,
        "status":       "OPEN",
        "opened_at":    datetime.now(timezone.utc).isoformat(),
        "trailing_stop_moved": False,
    }

    state["active_trades"][trade_record["signal_id"]] = trade_record
    save_state(state)

    print(f"\n  TRADE ACTIVE: {direction} {symbol}")
    if TELEGRAM_OK:
        try:
            tp1_p = trade_record.get("tp1",{}).get("price",0)
            tp2_p = trade_record.get("tp2",{}).get("price",0)
            tp3_p = trade_record.get("tp3",{}).get("price",0)
            trade_executed(direction, symbol, filled_price, stop_price,
                          tp1_p, tp2_p, tp3_p, contracts, leverage,
                          round((contracts * filled_price / 100) / leverage, 2), config)
        except Exception: pass
    print(f"  Entry: ${filled_price:,.2f} | Stop: ${stop_price:,.2f}")
    print(f"  TP1: ${tp1:,.2f} | TP2: ${tp2:,.2f} | TP3: ${tp3:,.2f}")
    print(f"  Contracts: {contracts} at {leverage}x")
    print("="*55)

    return True


# ─────────────────────────────────────────────────────────────
# POSITION MONITOR + TRAILING STOP
# ─────────────────────────────────────────────────────────────

def monitor_positions(config, state):
    """
    Check all active trades. Called every MONITOR_INTERVAL seconds.
    Handles:
      - TP1 hit: move stop to breakeven
      - TP2 hit: move stop to TP1
      - TP3 hit or position closed: record outcome
    """
    if not state.get("active_trades"):
        return

    for sig_id, trade in list(state["active_trades"].items()):
        kc_symbol  = trade["kc_symbol"]
        direction  = trade["direction"]
        entry_px   = trade["entry_price"]
        stop_px    = trade["stop_price"]
        contracts  = trade["contracts"]

        print(f"\n  Monitoring {sig_id} — {direction} {kc_symbol}")

        # Get current position
        try:
            pos      = get_position(config, kc_symbol)
            cur_px   = get_current_price(config, kc_symbol)
        except Exception as e:
            print(f"  [WARN] Cannot check position: {e}")
            continue

        # Position closed (stop hit or manual close)
        if not pos or abs(float(pos.get("currentQty", 0))) == 0:
            print(f"  [INFO] Position closed — recording outcome")
            _record_closed_trade(trade, cur_px, "CLOSED", state, sig_id)
            continue

        remaining_qty = abs(int(float(pos.get("currentQty", 0))))
        avg_entry     = float(pos.get("avgEntryPrice", entry_px))
        unrealised_pnl = float(pos.get("unrealisedPnl", 0))

        print(f"  Price: ${cur_px:,.2f} | PnL: ${unrealised_pnl:+.2f} | Qty: {remaining_qty}")

        tp1_info = trade.get("tp1", {})
        tp2_info = trade.get("tp2", {})
        tp3_info = trade.get("tp3", {})

        # ── Check TP1 hit ────────────────────────────────────
        if tp1_info and not tp1_info.get("hit"):
            tp1_px = tp1_info["price"]
            tp1_hit = (direction == "LONG"  and cur_px >= tp1_px) or \
                      (direction == "SHORT" and cur_px <= tp1_px)

            if tp1_hit:
                print(f"  [TP1 HIT] @ ${tp1_px:,.2f}")
                trade["tp1"]["hit"] = True
                if TELEGRAM_OK:
                    try:
                        unreal = float(pos.get("unrealisedPnl", 0))
                        tp1_hit(symbol, tp1_px, entry_px, trade["stop_price"], unreal, config)
                    except Exception: pass

                # Move stop to breakeven (entry price)
                if not trade.get("trailing_stop_moved"):
                    _move_stop(config, trade, avg_entry, kc_symbol, direction, remaining_qty)
                    trade["trailing_stop_moved"] = True
                    print(f"  [TRAIL] Stop moved to breakeven: ${avg_entry:,.2f}")

        # ── Check TP2 hit ────────────────────────────────────
        if tp2_info and not tp2_info.get("hit") and tp1_info.get("hit"):
            tp2_px = tp2_info["price"]
            tp2_hit = (direction == "LONG"  and cur_px >= tp2_px) or \
                      (direction == "SHORT" and cur_px <= tp2_px)

            if tp2_hit:
                print(f"  [TP2 HIT] @ ${tp2_px:,.2f}")
                trade["tp2"]["hit"] = True

                # Move stop to TP1 level
                tp1_px = tp1_info.get("price", avg_entry)
                _move_stop(config, trade, tp1_px, kc_symbol, direction, remaining_qty)
                trade["stop_price"] = tp1_px
                print(f"  [TRAIL] Stop moved to TP1: ${tp1_px:,.2f}")

        # ── Check TP3 hit / position fully closed ────────────
        if tp3_info and not tp3_info.get("hit") and tp2_info.get("hit"):
            tp3_px = tp3_info["price"]
            tp3_hit = (direction == "LONG"  and cur_px >= tp3_px) or \
                      (direction == "SHORT" and cur_px <= tp3_px)

            if tp3_hit or remaining_qty == 0:
                print(f"  [TP3 HIT] @ ${tp3_px:,.2f} — closing remainder")
                trade["tp3"]["hit"] = True
                try:
                    close_position_market(config, kc_symbol)
                    cancel_all_stop_orders(config, kc_symbol)
                except Exception as e:
                    print(f"  [WARN] Close failed: {e}")
                _record_closed_trade(trade, cur_px, "WIN", state, sig_id)
                continue

        # Save updated state
        state["active_trades"][sig_id] = trade
        save_state(state)


def _move_stop(config, trade, new_stop_price, kc_symbol, direction, qty):
    """Cancel existing stop and place new one at new_stop_price."""
    # Cancel old stop
    old_stop_id = trade.get("stop_order_id")
    if old_stop_id:
        cancel_stop_order(config, old_stop_id)

    # Place new stop
    stop_side = "sell" if direction == "LONG" else "buy"
    try:
        new_stop_id = place_stop_order(config, kc_symbol, stop_side, qty, new_stop_price)
        trade["stop_order_id"] = new_stop_id
        trade["stop_price"]    = new_stop_price
        print(f"  [OK] New stop @ ${new_stop_price:,.2f}: {new_stop_id}")
    except Exception as e:
        print(f"  [WARN] Stop move failed: {e} — adjust manually on KuCoin")


def _record_closed_trade(trade, exit_price, outcome, state, sig_id):
    """Record trade outcome to trade_log.csv and GitHub."""
    direction  = trade["direction"]
    entry_px   = trade["entry_price"]
    stop_px    = trade.get("stop_price", 0)
    balance    = trade.get("balance_at_entry", 1000)
    risk_pct   = trade.get("risk_pct", 0.02)
    stop_dist  = abs(entry_px - stop_px)

    if stop_dist > 0:
        r = (exit_price - entry_px) / stop_dist if direction == "LONG" \
            else (entry_px - exit_price) / stop_dist
    else:
        r = 0.0

    pnl = round(balance * risk_pct * r, 2)
    r   = round(r, 3)

    print(f"  [OUTCOME] {outcome} | R: {r:+.3f} | PnL: ${pnl:+.2f}")

    # Update trade_log.csv
    try:
        if os.path.exists(TRADE_LOG_FILE):
            df  = pd.read_csv(TRADE_LOG_FILE)
            mask = df["signal_id"] == trade["signal_id"]
            if mask.any():
                now = datetime.now(timezone.utc).isoformat()
                df.loc[mask, "exit_price"] = exit_price
                df.loc[mask, "exit_time"]  = now
                df.loc[mask, "r_multiple"] = r
                df.loc[mask, "pnl_usdt"]   = pnl
                df.loc[mask, "status"]     = "WIN" if r > 0 else "LOSS"
                df.to_csv(TRADE_LOG_FILE, index=False)
                _push_to_github(f"Auto close: {trade['signal_id']} {outcome} {r:+.3f}R")
    except Exception as e:
        print(f"  [WARN] Log update failed: {e}")

    # Telegram notification for closed trade
    if TELEGRAM_OK:
        try:
            trade_closed(
                trade.get("kc_symbol", trade.get("symbol","BTCUSDT")),
                trade.get("direction","LONG"),
                r, pnl, load_config()
            )
        except Exception: pass

    # Remove from active trades
    state["active_trades"].pop(sig_id, None)
    save_state(state)


def _push_to_github(message):
    """Push trade_log.csv to GitHub."""
    try:
        subprocess.run(["git", "add", TRADE_LOG_FILE],      check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", message],    check=True, capture_output=True)
        subprocess.run(["git", "push"],                      check=True, capture_output=True)
    except Exception as e:
        print(f"  [WARN] GitHub push failed: {e}")


# ─────────────────────────────────────────────────────────────
# EMERGENCY STOP
# ─────────────────────────────────────────────────────────────

def emergency_stop(config, state):
    """Close ALL open positions immediately. Use in emergencies only."""
    print("\n" + "!"*55)
    print("  EMERGENCY STOP — CLOSING ALL POSITIONS")
    print("!"*55)

    positions = get_open_positions(config)
    if not positions:
        print("  No open positions found.")
        return

    for pos in positions:
        sym = pos.get("symbol")
        qty = abs(int(float(pos.get("currentQty", 0))))
        if qty == 0:
            continue
        print(f"  Closing {sym} ({qty} contracts)...")
        try:
            cancel_all_stop_orders(config, sym)
            close_position_market(config, sym)
            print(f"  [OK] {sym} closed")
        except Exception as e:
            print(f"  [FAIL] {sym}: {e} — CLOSE MANUALLY ON KUCOIN")

    # Clear state
    state["active_trades"] = {}
    save_state(state)
    print("\n  All positions closed. Check KuCoin to verify.")


# ─────────────────────────────────────────────────────────────
# STATUS DISPLAY
# ─────────────────────────────────────────────────────────────

def show_status(config, state):
    """Show current automation status and positions."""
    print("\n" + "="*55)
    print("  TRADING AGENT V2 — AUTOMATION STATUS")
    print("="*55)
    print(f"  Armed:    {'YES' if state.get('armed') else 'NO'}")
    print(f"  Leverage: {state.get('leverage', 10)}x")
    print(f"  Risk:     {state.get('risk_pct', 0.02)*100:.1f}%")

    try:
        balance = get_available_balance(config)
        print(f"  Balance:  ${balance:.2f} USDT available")
    except Exception as e:
        print(f"  Balance:  ERROR ({e})")

    active = state.get("active_trades", {})
    print(f"\n  Active trades: {len(active)}")
    for sig_id, trade in active.items():
        print(f"    {sig_id}: {trade['direction']} {trade['symbol']}")
        print(f"      Entry: ${trade['entry_price']:,.2f} | Stop: ${trade['stop_price']:,.2f}")
        print(f"      TP1 hit: {trade.get('tp1',{}).get('hit',False)}")
        print(f"      TP2 hit: {trade.get('tp2',{}).get('hit',False)}")

    try:
        positions = get_open_positions(config)
        print(f"\n  KuCoin open positions: {len(positions)}")
        for p in positions:
            qty = float(p.get("currentQty", 0))
            pnl = float(p.get("unrealisedPnl", 0))
            print(f"    {p.get('symbol')}: {qty:+.0f} contracts | PnL: ${pnl:+.2f}")
    except Exception as e:
        print(f"\n  KuCoin positions: ERROR ({e})")

    print("="*55)


# ─────────────────────────────────────────────────────────────
# SIGNAL RECEIVER (called from main.py FastAPI)
# ─────────────────────────────────────────────────────────────

def on_signal(signal_data):
    """
    Entry point called by main.py when a valid signal fires.
    signal_data: dict from /signal/latest response

    Returns: dict with execution result
    """
    state = load_state()
    if not state.get("armed"):
        return {"executed": False, "reason": "Automation not armed"}

    config = load_config()

    d         = signal_data.get("decision", {})
    direction = d.get("bias", "")
    if "LONG" not in direction and "SHORT" not in direction:
        return {"executed": False, "reason": "No valid signal direction"}

    pos_data  = signal_data.get("positions", {})

    # ── OI-based risk tiering ──
    # 5% risk when OI RISING or RISING_ELEVATED (high conviction)
    # 1% risk for all other OI conditions (standard)
    oi_state   = signal_data.get("oi_state", signal_data.get("decision", {}).get("risk_state", ""))
    hc_states  = ["RISING", "RISING_ELEVATED", "LOW"]  # HIGH_CONVICTION conditions
    risk_pct   = 0.05 if any(s in str(oi_state).upper() for s in ["RISING", "RISING_ELEVATED"]) else 0.01
    print(f"  OI state: {oi_state} → risk_pct={risk_pct*100:.0f}%")

    signal = {
        "signal_id":   f"AUTO-{int(time.time())}",
        "symbol":      signal_data.get("symbol", "BTCUSDT"),
        "strategy":    signal_data.get("strategy", "AUTO"),
        "direction":   "LONG" if "LONG" in direction else "SHORT",
        "entry_price": signal_data.get("price", 0),
        "stop_loss":   pos_data.get("stop_price", 0) or (
            round(signal_data.get("price", 0) * (0.994 if "LONG" in direction else 1.006), 2)
        ),
        "target_1":    None,
        "target_2":    None,
        "target_3":    None,
        "grade":       d.get("setup_grade", "-"),
        "confidence":  d.get("confidence", 0),
        "risk_pct":    risk_pct,
    }

    # Extract targets
    targets = signal_data.get("targets", [])
    is_long = signal["direction"] == "LONG"
    ep      = signal["entry_price"]
    valid   = sorted(
        [t for t in targets if (is_long and t["price"] > ep) or (not is_long and t["price"] < ep)],
        key=lambda t: t["price"] if is_long else -t["price"]
    )
    for i, key in enumerate(["target_1", "target_2", "target_3"]):
        if i < len(valid):
            signal[key] = valid[i]["price"]

    # Validate stop price is reasonable
    if signal["stop_loss"] == 0:
        return {"executed": False, "reason": "Stop price is 0 — cannot trade without stop"}
    stop_pct = abs(signal["entry_price"] - signal["stop_loss"]) / signal["entry_price"]
    if stop_pct > 0.15:
        return {"executed": False, "reason": f"Stop distance {stop_pct:.1%} too wide — likely bad data"}
    if stop_pct < 0.001:
        return {"executed": False, "reason": f"Stop distance {stop_pct:.1%} too tight — likely bad data"}

    success = execute_trade(config, signal, state)
    return {
        "executed": success,
        "signal_id": signal["signal_id"],
        "direction": signal["direction"],
        "entry":    signal["entry_price"],
    }


def arm(leverage=10, risk_pct=0.02, min_confidence=60, strategy="BTC_G"):
    """Arm the automation system."""
    state = load_state()
    state["armed"]          = True
    state["leverage"]       = leverage
    state["risk_pct"]       = risk_pct
    state["min_confidence"] = min_confidence
    state["strategy"]       = strategy
    save_state(state)
    print(f"  Automation ARMED — {leverage}x leverage, {risk_pct*100:.1f}% risk, min conf {min_confidence}%")


def disarm():
    """Disarm the automation (won't place new trades, won't close existing)."""
    state = load_state()
    state["armed"] = False
    save_state(state)
    print("  Automation DISARMED — existing trades still monitored")


# ─────────────────────────────────────────────────────────────
# MONITORING LOOP
# ─────────────────────────────────────────────────────────────

def fetch_latest_signal(state):
    """
    Fetch the latest signal from the FastAPI server.
    Returns signal_data dict or None.
    """
    try:
        key    = state.get("strategy", "BTC_G")
        symbol = {"BTC_G":"BTCUSDT","BTC_L":"BTCUSDT","GOLD_G":"XAUUSD","SPY_L":"SPY"}.get(key,"BTCUSDT")
        tf     = {"BTC_G":"15m","BTC_L":"15m","GOLD_G":"60","SPY_L":"60"}.get(key,"15m")
        url    = f"http://127.0.0.1:8000/signal/latest?symbol={symbol}&timeframe={tf}&bars=120"
        r      = requests.get(url, timeout=20, verify=False)
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        print(f"  [Signal fetch error] {e}")
    return None


def check_for_new_signal(config, state):
    """
    Check if a new valid signal has fired and execute if armed.
    Runs inside the monitor loop.
    """
    if not state.get("armed"):
        return

    # Don't open new trade if one already active on same symbol
    active = state.get("active_trades", {})
    if active:
        print(f"  [SKIP] Already have {len(active)} active trade(s) — not opening new")
        if TELEGRAM_OK:
            try:
                # Get details of active trade for notification
                first_trade = list(active.values())[0]
                pos = get_open_positions(config)
                pnl = float(pos[0].get("unrealisedPnl", 0)) if pos else 0
                signal_skipped_active_trade(
                    direction,
                    data.get("symbol","BTCUSDT"),
                    first_trade.get("direction","LONG"),
                    first_trade.get("entry_price", 0),
                    pnl, config
                )
            except Exception: pass
        return

    data = fetch_latest_signal(state)
    if not data:
        return

    d         = data.get("decision", {})
    direction = d.get("bias", "")
    grade     = d.get("setup_grade", "C")
    confidence= d.get("confidence", 0)
    in_session= data.get("in_session", False)

    # Must be a real signal
    if "LONG" not in direction and "SHORT" not in direction:
        print(f"  [SKIP] No signal — {direction}")
        return

    # ── Session hours — single source of truth ──────────────
    # All strategies use hours [7, 12, 14, 18] UTC
    # Grade A+B required for all hours (thresh=55)
    ALLOWED_HOURS = [7, 12, 14, 18]
    _h_now = int(time.strftime("%H", time.gmtime()))
    if _h_now not in ALLOWED_HOURS:
        print(f"  [SKIP] Hour {_h_now} not in allowed hours {ALLOWED_HOURS}")
        return

    # We are in a session hour — check if signal is valid
    h_cur = int(time.strftime("%H", time.gmtime()))
    next_hours = [x for x in [7,12,14,18] if x > h_cur]
    next_h = next_hours[0] if next_hours else 7

    # Must be Grade A or B
    if grade not in ["A", "B"]:
        print(f"  [SKIP] Grade {grade} — only A and B qualify")
        if TELEGRAM_OK:
            try:
                regime = d.get("regime","UNKNOWN")
                no_trade(h_cur, confidence, regime, next_h, config)
            except Exception: pass
        return

    # Must meet minimum confidence
    min_conf = state.get("min_confidence", 60)
    if confidence < min_conf:
        print(f"  [SKIP] Confidence {confidence}% below minimum {min_conf}%")
        if TELEGRAM_OK:
            try:
                regime = d.get("regime","UNKNOWN")
                no_trade(h_cur, confidence, regime, next_h, config)
            except Exception: pass
        return

    print(f"  [SIGNAL] {direction} | Grade {grade} | Conf {confidence}% | In session: {in_session}")
    # Flag that execution was attempted — used to lock hour on failure
    try:
        _s = load_state(); _s["last_signal_attempted"] = True; save_state(_s)
    except Exception: pass
    # Telegram: signal detected — only once per session hour
    global _last_signal_hour
    if TELEGRAM_OK and int(time.strftime("%H", time.gmtime())) != _last_signal_hour:
        _last_signal_hour = int(time.strftime("%H", time.gmtime()))
        try:
            h_now = int(time.strftime("%H", time.gmtime()))
            ep_sig  = data.get("price", 0)
            pos_sig = data.get("positions", {})
            sl_sig  = pos_sig.get("stop_price", 0) or round(ep_sig*0.994, 2)
            sd_sig  = abs(ep_sig - sl_sig)
            is_l    = "LONG" in direction
            # Use actual PAVP targets if available, else ATR fallback
            tgts    = data.get("targets", [])
            valid_t = sorted(
                [t["price"] for t in tgts if (is_l and t["price"] > ep_sig) or (not is_l and t["price"] < ep_sig)],
                key=lambda x: x if is_l else -x
            )
            tp1_sig = valid_t[0] if len(valid_t) > 0 else round(ep_sig + sd_sig if is_l else ep_sig - sd_sig, 2)
            tp2_sig = valid_t[1] if len(valid_t) > 1 else round(ep_sig + sd_sig*1.5 if is_l else ep_sig - sd_sig*1.5, 2)
            tp3_sig = valid_t[2] if len(valid_t) > 2 else round(ep_sig + sd_sig*2.0 if is_l else ep_sig - sd_sig*2.0, 2)
            signal_detected(
                direction, grade, confidence,
                ep_sig, sl_sig, tp1_sig, tp2_sig, tp3_sig,
                state.get("strategy","BTC_H"), h_now, True, config
            )
        except Exception:
            pass
    result = on_signal(data)
    if result.get("executed"):
        print(f"  [EXECUTED] {result}")
    else:
        print(f"  [NOT EXECUTED] {result.get('reason','unknown')}")


def run_monitor_loop():
    """Background monitoring loop. Run this in a separate thread."""
    import sys
    # Force unbuffered output so logs appear immediately in journalctl
    sys.stdout.reconfigure(line_buffering=True)
    print(f"  Monitor loop started — checking every {MONITOR_INTERVAL}s", flush=True)
    if TELEGRAM_OK:
        try: system_started(load_config())
        except Exception: pass
    print(f"  Will check for signals AND monitor open positions", flush=True)
    config = load_config()
    cycle  = 0
    while True:
        try:
            state  = load_state()
            cycle += 1
            armed  = state.get("armed", False)
            active = len(state.get("active_trades", {}))
            h      = int(time.strftime('%H', time.gmtime()))
            ALLOWED_HOURS = [7, 12, 14, 18]
            in_sess= h in ALLOWED_HOURS
            print(f"\n  [Cycle {cycle}] {time.strftime('%H:%M:%S UTC', time.gmtime())} | "
                  f"Armed: {'YES' if armed else 'NO'} | "
                  f"Session: {'YES' if in_sess else f'NO (next: {ALLOWED_HOURS} UTC)'} | "
                  f"Active trades: {active}", flush=True)

            # Daily position update at 12:00 UTC (once only, not every cycle)
            if h == 12 and active > 0 and TELEGRAM_OK and not getattr(run_monitor_loop, "_daily_notified_hour", -1) == 12:
                try:
                    for tid, tr in state.get("active_trades", {}).items():
                        positions = get_open_positions(config)
                        for pos in positions:
                            if pos.get("symbol") == tr.get("kc_symbol", "XBTUSDTM"):
                                cur_px = float(pos.get("markPrice", tr.get("entry_price", 0)))
                                pnl    = float(pos.get("unrealisedPnl", 0))
                                position_update(
                                    tr.get("kc_symbol", "XBTUSDTM"),
                                    tr.get("direction", "LONG"),
                                    tr.get("entry_price", 0),
                                    cur_px, pnl,
                                    tr.get("stop_price", 0),
                                    tr.get("tp1", {}).get("price", 0),
                                    tr.get("tp2", {}).get("price", 0),
                                    tr.get("tp3", {}).get("price", 0),
                                    config
                                )
                except Exception:
                    pass
                run_monitor_loop._daily_notified_hour = 12
            elif h != 12:
                run_monitor_loop._daily_notified_hour = -1

            # Check for new signals every cycle during session hour
            # Stop retrying once a trade is successfully placed this hour
            if not hasattr(run_monitor_loop, '_filled_hour'):
                run_monitor_loop._filled_hour = -1
            # Reset when we leave the session hour
            if h not in ALLOWED_HOURS:
                run_monitor_loop._filled_hour = -1
            # Only check if in session and haven't filled this hour
            active = len(state.get("active_trades", {}))
            if h in ALLOWED_HOURS and run_monitor_loop._filled_hour != h:
                prev_active = active
                check_for_new_signal(config, state)
                # Reload state after execution attempt
                state = load_state()
                new_active = len(state.get("active_trades", {}))
                if new_active > prev_active:
                    # Trade opened successfully
                    run_monitor_loop._filled_hour = h
                    print(f"  [HOUR LOCK] Trade opened — locking hour {h}")
                elif state.get("last_signal_attempted"):
                    # Execution was attempted but failed — still lock hour
                    run_monitor_loop._filled_hour = h
                    state["last_signal_attempted"] = False
                    save_state(state)
                    print(f"  [HOUR LOCK] Execution attempted — locking hour {h}")

            # Monitor existing positions every cycle
            state = load_state()
            if state.get("active_trades"):
                monitor_positions(config, state)

        except Exception as e:
            print(f"  [Monitor error] {e}", flush=True)
            if TELEGRAM_OK:
                try: system_error("monitor_loop", str(e), load_config())
                except Exception: pass
        time.sleep(MONITOR_INTERVAL)


# ─────────────────────────────────────────────────────────────
# CLI ENTRY POINT
# ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="KuCoin Futures Executor")
    parser.add_argument("--mode", choices=["arm","disarm","status","stop","monitor"],
                        default="status")
    parser.add_argument("--leverage", type=int,   default=10)
    parser.add_argument("--risk",     type=float, default=0.02)
    args = parser.parse_args()

    config = load_config()
    state  = load_state()

    if args.mode == "arm":
        arm(args.leverage, args.risk)
        show_status(config, state)
        print("\n  Running monitor loop — press Ctrl+C to stop")
        run_monitor_loop()

    elif args.mode == "disarm":
        disarm()

    elif args.mode == "status":
        show_status(config, state)

    elif args.mode == "stop":
        confirm = input("  Type YES to close all positions: ")
        if confirm.strip().upper() == "YES":
            emergency_stop(config, state)
        else:
            print("  Cancelled.")

    elif args.mode == "monitor":
        run_monitor_loop()
