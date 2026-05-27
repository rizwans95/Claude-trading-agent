"""
patch_risk_and_strategy.py
Fixes two blocking issues in kucoin_executor.py:

1. Risk % hardcoded at 0.02 in execute_trade (line 357/389)
   Fix: read risk_pct from signal if available, else from state

2. Strategy logged as "AUTO" (line 899)
   Fix: read strategy from live_signal_monitor signal data

Run on VPS: python3 patch_risk_and_strategy.py
"""

f = open('/root/trading/kucoin_executor.py', 'r')
content = f.read()
f.close()

# ─────────────────────────────────────────────────────────────
# FIX 1 — Risk % hardcoded in execute_trade default signal dict
# Line 357: "risk_pct": 0.02
# ─────────────────────────────────────────────────────────────

old1 = '        "risk_pct":     0.02,'
new1 = '        "risk_pct":     state.get("risk_pct", 0.02),'

if old1 in content:
    content = content.replace(old1, new1, 1)
    print('Fix 1 applied: risk_pct reads from state in signal default')
else:
    print('Fix 1 NOT FOUND')

# ─────────────────────────────────────────────────────────────
# FIX 2 — Risk % in execute_trade reads from state only
# Line 389: risk_pct = state.get("risk_pct", 0.02)
# Fix: also check signal for risk_pct (set by strategy config)
# ─────────────────────────────────────────────────────────────

old2 = '    risk_pct      = state.get("risk_pct", 0.02)'
new2 = '    # Read risk_pct from signal (set by strategy), fall back to state\n    risk_pct      = float(signal.get("risk_pct", state.get("risk_pct", 0.02)))'

if old2 in content:
    content = content.replace(old2, new2, 1)
    print('Fix 2 applied: execute_trade reads risk_pct from signal first')
else:
    print('Fix 2 NOT FOUND')

# ─────────────────────────────────────────────────────────────
# FIX 3 — Strategy logged as "AUTO" in on_signal
# Line 899: "strategy": "AUTO"
# Fix: read from signal_data which carries the strategy key
# ─────────────────────────────────────────────────────────────

old3 = '        "strategy":    "AUTO",'
new3 = '        "strategy":    signal_data.get("strategy", "AUTO"),'

if old3 in content:
    content = content.replace(old3, new3, 1)
    print('Fix 3 applied: strategy now read from signal_data')
else:
    print('Fix 3 NOT FOUND')

open('/root/trading/kucoin_executor.py', 'w').write(content)
print('\nkucoin_executor.py saved.')
print('Run: systemctl restart trading')
