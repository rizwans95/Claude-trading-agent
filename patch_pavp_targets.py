"""
patch_pavp_targets.py
Fixes PAVP target passing from signal to executor.
Run on VPS: python3 patch_pavp_targets.py
"""
import os

# ─────────────────────────────────────────────────────────────
# FIX A — Add target fields to TradeRecord model in main.py
# ─────────────────────────────────────────────────────────────

f = open('/root/trading/main.py', 'r')
content = f.read()
f.close()

old_a = '    balance:     Optional[float] = 0.0'
new_a = '''    balance:     Optional[float] = 0.0
    target_1:    Optional[float] = None
    target_2:    Optional[float] = None
    target_3:    Optional[float] = None'''

if old_a in content:
    content = content.replace(old_a, new_a)
    print('Fix A applied: target fields added to TradeRecord')
else:
    print('Fix A NOT FOUND — already applied or structure changed')

# ─────────────────────────────────────────────────────────────
# FIX B — Use targets in the row dict in main.py
# ─────────────────────────────────────────────────────────────

old_b = '        "target_1":        "",\n        "target_2":        "",\n        "target_3":        "",'
new_b = '        "target_1":        trade.target_1 or "",\n        "target_2":        trade.target_2 or "",\n        "target_3":        trade.target_3 or "",'

if old_b in content:
    content = content.replace(old_b, new_b)
    print('Fix B applied: targets now read from TradeRecord')
else:
    print('Fix B NOT FOUND — already applied or structure changed')

open('/root/trading/main.py', 'w').write(content)
print('main.py saved')

# ─────────────────────────────────────────────────────────────
# FIX C — Use PAVP targets in executor, fall back to ATR
# ─────────────────────────────────────────────────────────────

f = open('/root/trading/kucoin_executor.py', 'r')
content = f.read()
f.close()

old_c = '''    # ── Calculate TP levels ──────────────────────────────────
    stop_dist = abs(filled_price - stop_price)
    if direction == "LONG":
        tp1 = round(round((filled_price + stop_dist * 1.0) * 10) / 10, 1)
        tp2 = round(round((filled_price + stop_dist * 1.5) * 10) / 10, 1)
        tp3 = round(round((filled_price + stop_dist * 2.0) * 10) / 10, 1)
    else:
        tp1 = round(round((filled_price - stop_dist * 1.0) * 10) / 10, 1)
        tp2 = round(round((filled_price - stop_dist * 1.5) * 10) / 10, 1)
        tp3 = round(round((filled_price - stop_dist * 2.0) * 10) / 10, 1)'''

new_c = '''    # ── Calculate TP levels — use PAVP targets if available ──
    stop_dist = abs(filled_price - stop_price)
    sig_t1 = signal.get("target_1")
    sig_t2 = signal.get("target_2")
    sig_t3 = signal.get("target_3")

    def _valid_target(t, direction, entry):
        try:
            v = float(t)
            if direction == "LONG":
                return v > entry
            else:
                return v < entry
        except Exception:
            return False

    if (_valid_target(sig_t1, direction, filled_price) and
            _valid_target(sig_t2, direction, filled_price) and
            _valid_target(sig_t3, direction, filled_price)):
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
        print(f"  [TP] Using ATR fallback targets: {tp1} / {tp2} / {tp3}")'''

if old_c in content:
    content = content.replace(old_c, new_c)
    print('Fix C applied: executor now uses PAVP targets with ATR fallback')
else:
    print('Fix C NOT FOUND — already applied or structure changed')

open('/root/trading/kucoin_executor.py', 'w').write(content)
print('kucoin_executor.py saved')

print('\nAll patches complete. Run: systemctl restart trading')
