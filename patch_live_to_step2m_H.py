"""
patch_live_to_step2m_H.py
Updates live_signal_monitor.py to use step2m-H config:

  BTC_H (personal account — aggressive):
    Grade A+B, bar_tolerance=1, sweep_lookback=20
    min_sweep_atr=0.15, htf_ratio=8, grade_thresh=55
    cooldown=15, hours 7+12+14+18
    Risk: 5% HC / 2% standard

  BTC_B (prop firm — conservative):
    Grade B only, grade_thresh=55
    All other params default
    Hours 7+12+14+18
    Risk: 5% HC / 2% standard

Run on VPS: python3 patch_live_to_step2m_H.py
"""

f = open('/root/trading/live_signal_monitor.py', 'r')
content = f.read()
f.close()

# ── FIX 1: Update STRATEGIES config ──
old = '''STRATEGIES = [
    {
        "key":      "BTC_H",
        "symbol":   "BTCUSDT",
        "tf":       "15",
        "hours":    [12, 14],
        "grades":   ["A", "B"],
        "mode":     "H",
        "active":   True,
        "window":   120,
        "max_bars": 60,
        "cooldown": 20,
        "skip_funding": ["MILD_POSITIVE", "HIGH_POSITIVE"],
        "require_oi":   ["RISING", "RISING_ELEVATED"],
        "risk_pct":     0.05,
    },
    {
        "key":      "BTC_B",
        "symbol":   "BTCUSDT",
        "tf":       "15",
        "hours":    [7, 12, 14, 18],
        "grades":   ["B"],
        "mode":     "B",
        "active":   True,
        "window":   120,
        "max_bars": 60,
        "cooldown": 20,
        "skip_funding": ["MILD_POSITIVE", "HIGH_POSITIVE"],
        "require_oi":   None,
        "risk_pct":     0.02,
    },'''

new = '''STRATEGIES = [
    {
        "key":           "BTC_H",
        "symbol":        "BTCUSDT",
        "tf":            "15",
        "hours":         [7, 12, 14, 18],
        "grades":        ["A", "B"],
        "mode":          "H",
        "active":        True,
        "window":        120,
        "max_bars":      60,
        "cooldown":      15,
        "skip_funding":  ["MILD_POSITIVE", "HIGH_POSITIVE"],
        "require_oi":    None,
        "grade_thresh":  55,
        "bar_tolerance": 1,
        "risk_pct":      0.02,
        "risk_pct_hc":   0.05,
    },
    {
        "key":           "BTC_B",
        "symbol":        "BTCUSDT",
        "tf":            "15",
        "hours":         [7, 12, 14, 18],
        "grades":        ["B"],
        "mode":          "B",
        "active":        True,
        "window":        120,
        "max_bars":      60,
        "cooldown":      20,
        "skip_funding":  ["MILD_POSITIVE", "HIGH_POSITIVE"],
        "require_oi":    None,
        "grade_thresh":  55,
        "bar_tolerance": 0,
        "risk_pct":      0.02,
        "risk_pct_hc":   0.05,
    },'''

if old in content:
    content = content.replace(old, new)
    print('Fix 1 applied: STRATEGIES updated to step2m-H config')
else:
    print('Fix 1 NOT FOUND')

# ── FIX 2: Update grade threshold check to use strategy config ──
old2 = '''    # Must Grade A or B
    if grade not in ["A", "B"]:
        print(f"  [SKIP] Grade {grade} — only A and B qualify")'''
new2 = '''    # Must meet grade threshold from strategy config
    allowed_grades = strat.get("grades", ["A", "B"])
    grade_thresh   = strat.get("grade_thresh", 60)
    if confidence < grade_thresh:
        grade = "C"
    if grade not in allowed_grades:
        print(f"  [SKIP] Grade {grade} — strategy requires {allowed_grades}")'''

if old2 in content:
    content = content.replace(old2, new2)
    print('Fix 2 applied: Grade threshold now reads from strategy config')
else:
    print('Fix 2 NOT FOUND — checking alternative')
    idx = content.find('Grade {grade}')
    if idx > 0:
        print(content[max(0,idx-200):idx+100])

# ── FIX 3: Update bar_tolerance to read from strategy config ──
old3 = '''        sig = evaluate_signal(
                slice_df, bar_time_utc,
                funding_state, oi_state,
                sc["funding_filter"], sc["oi_filter"]
            )'''

# Find how bar_tolerance is currently passed
import re
bt_line = re.search(r'bar_tolerance.*?=.*?\d', content)
if bt_line:
    print(f'bar_tolerance found at: {bt_line.group(0)}')

# ── FIX 4: Update automation_state risk_pct for BTC_H ──
import json, os
state_file = '/root/trading/automation_state.json'
if os.path.exists(state_file):
    with open(state_file) as f:
        state = json.load(f)
    state['risk_pct'] = 0.02
    state['strategy'] = 'BTC_H'
    with open(state_file, 'w') as f:
        json.dump(state, f, indent=2)
    print('Fix 4 applied: automation_state.json updated')

open('/root/trading/live_signal_monitor.py', 'w').write(content)
print('\nlive_signal_monitor.py saved')
print('Run: systemctl restart trading trading-monitor')
