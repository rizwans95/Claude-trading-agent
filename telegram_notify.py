"""
telegram_notify.py
Telegram notification system for Trading Agent V2.
"""
import requests, json, time, os

def load_config():
    if os.path.exists("config.json"):
        with open("config.json") as f:
            return json.load(f)
    return {}

def send(message, config=None):
    if config is None:
        config = load_config()
    token   = config.get("telegram_bot_token", "")
    chat_id = config.get("telegram_chat_id", "")
    if not token or not chat_id:
        return False
    try:
        url = "https://api.telegram.org/bot" + token + "/sendMessage"
        r   = requests.post(url, json={
            "chat_id":    chat_id,
            "text":       message,
            "parse_mode": "HTML",
        }, timeout=10)
        return r.status_code == 200
    except Exception as e:
        print("  [Telegram error] " + str(e))
        return False

def signal_detected(direction, grade, confidence, entry, stop,
                    tp1, tp2, tp3, strategy, hour, auto_executing=True, config=None):
    arrow  = "\U0001f4c8" if "LONG" in direction else "\U0001f4c9"
    action = "Executing automatically..." if auto_executing else "Armed - waiting for fill"
    msg = (
        arrow + " <b>SIGNAL DETECTED</b>\n"
        "<b>" + direction + "</b> | Grade: " + str(grade) + " | Conf: " + str(confidence) + "%\n"
        "Entry: <b>$" + f"{entry:,.2f}" + "</b> | Stop: $" + f"{stop:,.2f}" + "\n"
        "TP1: $" + f"{tp1:,.2f}" + " | TP2: $" + f"{tp2:,.2f}" + " | TP3: $" + f"{tp3:,.2f}" + "\n"
        "Strategy: " + str(strategy) + " | Hour: " + str(hour) + ":00 UTC\n"
        + action
    )
    send(msg, config)

def trade_executed(direction, symbol, entry, stop, tp1, tp2, tp3,
                   contracts, leverage, margin, config=None):
    arrow = "\U0001f4c8" if direction == "LONG" else "\U0001f4c9"
    msg = (
        "\U00002705 <b>TRADE EXECUTED</b>\n"
        + arrow + " <b>" + direction + " " + symbol + "</b> @ $" + f"{entry:,.2f}" + "\n"
        "Stop: $" + f"{stop:,.2f}" + "\n"
        "TP1: $" + f"{tp1:,.2f}" + " | TP2: $" + f"{tp2:,.2f}" + " | TP3: $" + f"{tp3:,.2f}" + "\n"
        "Contracts: " + str(contracts) + " | Leverage: " + str(leverage) + "x\n"
        "Margin: $" + f"{margin:.2f}" + " USDT"
    )
    send(msg, config)

def trade_failed(direction, symbol, reason, config=None):
    msg = (
        "\U0000274c <b>EXECUTION FAILED</b>\n"
        "Signal: " + direction + " " + symbol + "\n"
        "Reason: " + str(reason) + "\n"
        "Action: No position opened"
    )
    send(msg, config)

def signal_skipped_active_trade(direction, symbol, active_direction,
                                 active_entry, active_pnl, config=None):
    arrow = "\U0001f4c8" if "LONG" in direction else "\U0001f4c9"
    msg = (
        arrow + " <b>SIGNAL SKIPPED - Active Trade</b>\n"
        "New signal: <b>" + direction + " " + symbol + "</b>\n"
        "Reason: Already in " + active_direction + " @ $" + f"{active_entry:,.2f}" + "\n"
        "Current PnL: $" + f"{active_pnl:+.2f}" + " USDT\n"
        "Waiting for current trade to close first"
    )
    send(msg, config)

def signal_skipped_error(direction, symbol, reason, config=None):
    arrow = "\U0001f4c8" if "LONG" in direction else "\U0001f4c9"
    msg = (
        arrow + " <b>SIGNAL SKIPPED - Error</b>\n"
        "Signal: " + direction + " " + symbol + "\n"
        "Reason: " + str(reason) + "\n"
        "No position opened - check logs"
    )
    send(msg, config)

def no_trade(hour, confidence, regime, next_hour, config=None):
    msg = (
        "\U0001f4ca <b>SESSION HOUR " + f"{hour:02d}" + ":00 UTC</b>\n"
        "Signal: NO TRADE (score too low)\n"
        "Confidence: " + str(confidence) + "% | Regime: " + str(regime) + "\n"
        "Next session hour: " + f"{next_hour:02d}" + ":00 UTC"
    )
    send(msg, config)

def tp1_hit(symbol, tp1_price, new_stop, old_stop, pnl, config=None):
    msg = (
        "\U000026a1 <b>TP1 HIT - Stop to Breakeven</b>\n"
        + symbol + " | TP1: $" + f"{tp1_price:,.2f}" + "\n"
        "Stop: $" + f"{old_stop:,.2f}" + " to <b>$" + f"{new_stop:,.2f}" + "</b>\n"
        "Unrealised PnL: $" + f"{pnl:+.2f}" + " USDT"
    )
    send(msg, config)

def tp2_hit(symbol, tp2_price, new_stop, pnl, config=None):
    msg = (
        "\U000026a1 <b>TP2 HIT - Stop to TP1 Level</b>\n"
        + symbol + " | TP2: $" + f"{tp2_price:,.2f}" + "\n"
        "Stop locked at: <b>$" + f"{new_stop:,.2f}" + "</b>\n"
        "Unrealised PnL: $" + f"{pnl:+.2f}" + " USDT"
    )
    send(msg, config)

def trade_closed(symbol, direction, r, pnl, config=None):
    emoji  = "\U0001f3c6" if r > 0 else "\U0001f534"
    result = "WIN" if r > 0 else "LOSS"
    msg = (
        emoji + " <b>TRADE CLOSED - " + result + "</b>\n"
        + direction + " " + symbol + "\n"
        "Result: <b>" + f"{r:+.3f}" + "R</b>\n"
        "PnL: <b>$" + f"{pnl:+.2f}" + " USDT</b>"
    )
    send(msg, config)

def position_update(symbol, direction, entry, current_price,
                    pnl, stop, tp1, tp2, tp3, config=None):
    arrow  = "\U0001f4c8" if direction == "LONG" else "\U0001f4c9"
    pnl_em = "\U0001f7e2" if pnl >= 0 else "\U0001f534"
    tp1d   = abs(tp1 - current_price)
    stopd  = abs(current_price - stop)
    msg = (
        "\U0001f4ca <b>DAILY POSITION UPDATE</b>\n"
        + arrow + " " + direction + " " + symbol + "\n"
        "Entry: $" + f"{entry:,.2f}" + " | Now: $" + f"{current_price:,.2f}" + "\n"
        + pnl_em + " PnL: <b>$" + f"{pnl:+.2f}" + " USDT</b>\n"
        "Stop: $" + f"{stop:,.2f}" + " (" + f"{stopd:,.0f}" + " away)\n"
        "TP1: $" + f"{tp1:,.2f}" + " (" + f"{tp1d:,.0f}" + " away)\n"
        "TP2: $" + f"{tp2:,.2f}" + " | TP3: $" + f"{tp3:,.2f}" + "\n"
        "Status: Holding - monitoring"
    )
    send(msg, config)

def system_error(source, error_msg, config=None):
    t = time.strftime("%H:%M UTC", time.gmtime())
    msg = (
        "\U000026a0 <b>SYSTEM ERROR</b>\n"
        "Source: " + str(source) + "\n"
        "Error: " + str(error_msg)[:200] + "\n"
        "Time: " + t
    )
    send(msg, config)

def system_started(config=None):
    msg = (
        "\U0001f7e2 <b>LateNights Trading Agent V2</b>\n"
        "Monitor loop started\n"
        "Watching session hours: 07:00 / 12:00 / 14:00 / 18:00 UTC"
    )
    send(msg, config)
