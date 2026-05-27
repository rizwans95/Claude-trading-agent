"""
patch_session_hours.py
Fixes the session hours check in the monitor loop.

The monitor was reading in_session from the signal API which
only knows about BTC_H hours [12,14]. This caused BTC_B signals
at hours [7,18] to be skipped with "Outside session hours".

Fix: check session hours directly per active strategy,
not from the API response.

Run on VPS: python3 patch_session_hours.py
"""

f = open('/root/trading/kucoin_executor.py', 'r')
content = f.read()
f.close()

# ─────────────────────────────────────────────────────────────
# FIX — Replace in_session check with direct hour check
# ─────────────────────────────────────────────────────────────

old = '''    in_session= data.get("in_session", False)
    # Must be a real signal
    if "LONG" not in direction and "SHORT" not in direction:
        print(f"  [SKIP] No signal — {direction}")
        return
    # Must be in session
    if not in_session:
        print(f"  [SKIP] Outside session hours")
        return'''

new = '''    in_session= data.get("in_session", False)

    # Must be a real signal
    if "LONG" not in direction and "SHORT" not in direction:
        print(f"  [SKIP] No signal — {direction}")
        return

    # Check session hours per active strategy (not from API)
    h_now = int(time.strftime("%H", time.gmtime()))
    active_strategy = state.get("strategy", "BTC_H")
    STRATEGY_HOURS = {
        "BTC_H": [12, 14],
        "BTC_B": [7, 12, 14, 18],
        "BTC_G": [7, 12, 14, 18],
        "BTC_L": [7, 12, 14],
    }
    allowed_hours = STRATEGY_HOURS.get(active_strategy, [12, 14])
    if h_now not in allowed_hours:
        print(f"  [SKIP] Hour {h_now} not in {active_strategy} session hours {allowed_hours}")
        return'''

if old in content:
    content = content.replace(old, new, 1)
    print('Fix applied: session hours now checked per strategy')
else:
    print('NOT FOUND — checking alternative')
    # Show context around in_session
    idx = content.find('in_session= data.get("in_session", False)')
    if idx > 0:
        print('Found in_session at position', idx)
        print(content[idx:idx+300])

open('/root/trading/kucoin_executor.py', 'w').write(content)
print('kucoin_executor.py saved')
print('Run: systemctl restart trading-monitor')
