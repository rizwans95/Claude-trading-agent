"""
patch_dashboard_buttons.py
Removes old strategy buttons (ETH, SPY, GOLD) and updates
the remaining buttons with correct BTC_H and BTC_B data.

Run on VPS: python3 patch_dashboard_buttons.py
"""

f = open('/root/trading/dashboard_v2.html', 'r')
content = f.read()
f.close()
fixes = 0

# ─────────────────────────────────────────────────────────────
# FIX 1 — Update BTC_H button (was BTC_G with 82.1%)
# ─────────────────────────────────────────────────────────────
# Find and replace the sw (win rate) display values
content = content.replace(
    '<div class="sw">82.1%</div>',
    '<div class="sw">76.9%</div>'
)
content = content.replace(
    '<div class="sw">92.3%</div>',
    '<div class="sw">64.6%</div>'
)
print('Fix 1 applied: win rate percentages updated')
fixes += 1

# ─────────────────────────────────────────────────────────────
# FIX 2 — Remove ETH_G button entirely
# ─────────────────────────────────────────────────────────────
import re

# Remove ETH_G button block
eth_pattern = r'<div class="strat-btn"[^>]*id="btn-ETH_G"[^>]*>.*?</div>\s*'
if re.search(eth_pattern, content, re.DOTALL):
    content = re.sub(eth_pattern, '', content, flags=re.DOTALL)
    print('Fix 2 applied: ETH_G button removed')
    fixes += 1
else:
    print('Fix 2 NOT FOUND — ETH_G button')

# ─────────────────────────────────────────────────────────────
# FIX 3 — Remove SPY_L button entirely
# ─────────────────────────────────────────────────────────────
spy_pattern = r'<div class="strat-btn"[^>]*id="btn-SPY_L"[^>]*>.*?</div>\s*'
if re.search(spy_pattern, content, re.DOTALL):
    content = re.sub(spy_pattern, '', content, flags=re.DOTALL)
    print('Fix 3 applied: SPY_L button removed')
    fixes += 1
else:
    print('Fix 3 NOT FOUND — SPY_L button')

# ─────────────────────────────────────────────────────────────
# FIX 4 — Remove GOLD_G button entirely
# ─────────────────────────────────────────────────────────────
gold_pattern = r'<div class="strat-btn"[^>]*id="btn-GOLD_G"[^>]*>.*?</div>\s*'
if re.search(gold_pattern, content, re.DOTALL):
    content = re.sub(gold_pattern, '', content, flags=re.DOTALL)
    print('Fix 4 applied: GOLD_G button removed')
    fixes += 1
else:
    print('Fix 4 NOT FOUND — GOLD_G button')

# ─────────────────────────────────────────────────────────────
# FIX 5 — Update SYMBOL_MAP / highWRHours references
# ─────────────────────────────────────────────────────────────
old5 = "  BTC_G: { asset:'BTCUSDT', tf:'15m', hours:[7,12,14,18], baseWR:82.1 },\n  BTC_L: { asset:'BTCUSDT', tf:'15m', hours:[7,12,14],   baseWR:92.3 },\n  ETH_G: { asset:'ETHUSDT', tf:'15m', hours:[7,12,14,18], baseWR:68.0 },\n  SPY_L: { asset:'SPY',     tf:'1h',  hours:[13,14],      baseWR:85.7 },\n  GOLD_G:{ asset:'XAUUSD', tf:'60',  hours:[13,14],      baseWR:75.0 },"
new5 = "  BTC_H: { asset:'BTCUSDT', tf:'15m', hours:[12,14],       baseWR:76.9 },\n  BTC_B: { asset:'BTCUSDT', tf:'15m', hours:[7,12,14,18], baseWR:64.6 },"
if old5 in content:
    content = content.replace(old5, new5)
    print('Fix 5 applied: asset map updated')
    fixes += 1
else:
    print('Fix 5 NOT FOUND — asset map')

# ─────────────────────────────────────────────────────────────
# FIX 6 — Update highWRHours
# ─────────────────────────────────────────────────────────────
old6 = "var highWRHours = {'BTC_G':[12,14],'BTC_L':[7,12,14],'GOLD_G':[13,14],'SPY_L':[13,14]};"
new6 = "var highWRHours = {'BTC_H':[12,14],'BTC_B':[12,14]};"
if old6 in content:
    content = content.replace(old6, new6)
    print('Fix 6 applied: highWRHours updated')
    fixes += 1
else:
    print('Fix 6 NOT FOUND — highWRHours')

# ─────────────────────────────────────────────────────────────
# FIX 7 — Add strategy info subtitle showing key config
# Replace strat-name divs with more useful info
# ─────────────────────────────────────────────────────────────
old7a = '<div class="strat-name">BTC-H</div>\n          <div class="strat-asset">BTCUSDT 15m</div>'
new7a = '<div class="strat-name">BTC-H</div>\n          <div class="strat-asset">Hr 12+14 | OI Rising</div>'
if old7a in content:
    content = content.replace(old7a, new7a)
    print('Fix 7a applied: BTC_H subtitle updated')
    fixes += 1

old7b = '<div class="strat-name">BTC-B</div>\n          <div class="strat-asset">BTCUSDT 15m</div>'
new7b = '<div class="strat-name">BTC-B</div>\n          <div class="strat-asset">Hr 7+12+14+18 | Prop</div>'
if old7b in content:
    content = content.replace(old7b, new7b)
    print('Fix 7b applied: BTC_B subtitle updated')
    fixes += 1

# ─────────────────────────────────────────────────────────────
# SAVE
# ─────────────────────────────────────────────────────────────
open('/root/trading/dashboard_v2.html', 'w').write(content)
print(f'\ndashboard_v2.html saved — {fixes} fixes applied')
print('Hard refresh browser (Ctrl+Shift+R) to see changes')
