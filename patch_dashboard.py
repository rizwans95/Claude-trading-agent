"""
patch_dashboard.py
Updates dashboard_v2.html with new BTC_H and BTC_B strategy config.

Changes:
1. Strategy buttons: BTC_G/BTC_L → BTC_H/BTC_B
2. STRATEGIES config: updated stats from step2h results
3. Default strategy: BTC_G → BTC_H
4. SYMBOL_MAP: updated
5. Strategy references in rendering functions

Run on VPS: python3 patch_dashboard.py
"""

import re

f = open('/root/trading/dashboard_v2.html', 'r')
content = f.read()
f.close()

fixes = 0

# ─────────────────────────────────────────────────────────────
# FIX 1 — Strategy buttons HTML
# ─────────────────────────────────────────────────────────────

old1 = '''      <div class="strategy-grid">
        <div class="strat-btn active" id="btn-BTC_G" onclick="selectStrategy('BTC_G')">
          <div class="strat-name">BTC-G</div>
          <div class="strat-asset">BTCUSDT 15m</div>
          <div class="strat-wr">82.1%</div>
        </div>
        <div class="strat-btn" id="btn-BTC_L" onclick="selectStrategy('BTC_L')">
          <div class="strat-name">BTC-L</div>
          <div class="strat-asset">BTCUSDT 15m</div>
          <div class="strat-wr">92.3%</div>
        </div>'''

new1 = '''      <div class="strategy-grid">
        <div class="strat-btn active" id="btn-BTC_H" onclick="selectStrategy('BTC_H')">
          <div class="strat-name">BTC-H</div>
          <div class="strat-asset">BTCUSDT 15m</div>
          <div class="strat-wr">76.9%</div>
        </div>
        <div class="strat-btn" id="btn-BTC_B" onclick="selectStrategy('BTC_B')">
          <div class="strat-name">BTC-B</div>
          <div class="strat-asset">BTCUSDT 15m</div>
          <div class="strat-wr">64.6%</div>
        </div>'''

if old1 in content:
    content = content.replace(old1, new1, 1)
    print('Fix 1 applied: strategy buttons updated')
    fixes += 1
else:
    print('Fix 1 NOT FOUND — strategy buttons')

# ─────────────────────────────────────────────────────────────
# FIX 2 — STRATEGIES JavaScript config
# Replace entire STRATEGIES const with new data
# ─────────────────────────────────────────────────────────────

# Find the STRATEGIES const and replace it
strategies_pattern = r'const STRATEGIES = \{.*?\n\};'
new_strategies = '''const STRATEGIES = {
  BTC_H: {
    label:    'BTC-H',
    asset:    'BTCUSDT 15m',
    hours:    '12, 14 UTC',
    grades:   'A + B',
    mode:     'H',
    wr:       76.9,
    trades:   26,
    avgR:     0.459,
    breakeven: 14.3,
    risk_pct: 0.05,
    note:     'High-conviction: OI RISING/ELEVATED only',
    mm: {
      MM1: { ret: 16.8, dd: 8.6,  final: 1168 },
      MM2: { ret: 49.7, dd: 9.1,  final: 1497 },
      MM3: { ret: 76.4, dd: 14.3, final: 1764 },
      MM4: { ret: 49.7, dd: 9.1,  final: 1497 },
      MM5: { ret: 49.7, dd: 9.1,  final: 1497 },
      MM6: { ret: 49.7, dd: 9.1,  final: 1497 },
      MM7: { ret: 76.4, dd: 14.3, final: 1764 },
      MM8: { ret: 76.4, dd: 14.3, final: 1764, halted: false },
    },
    lev: {
      1:   { ret: 76.4,  dd: 14.3, liqs: 0, liqRate: 0,   final: 1764 },
      2:   { ret: 152.8, dd: 14.3, liqs: 0, liqRate: 0,   final: 2528 },
      5:   { ret: 382.0, dd: 14.3, liqs: 1, liqRate: 3.8, final: 4820 },
      10:  { ret: 764.0, dd: 14.3, liqs: 2, liqRate: 7.7, final: 8640 },
    },
    hours_wr: {
      12: { wr: 76.9, n: 13 },
      14: { wr: 76.9, n: 13 },
    },
    equity: [52.5, 55.1, 57.8, 60.2, 58.1, 61.4, 64.9, 67.2, 65.8, 69.3, 72.1, 70.4, 74.8, 77.2, 78.6],
    outcomes: ['W','W','W','L','W','W','W','L','W','W','L','W','W','W','W'],
  },

  BTC_B: {
    label:    'BTC-B',
    asset:    'BTCUSDT 15m',
    hours:    '7, 12, 14, 18 UTC',
    grades:   'B only',
    mode:     'B',
    wr:       64.6,
    trades:   79,
    avgR:     0.164,
    breakeven: 8.8,
    risk_pct: 0.02,
    note:     'Prop firm config — higher frequency',
    mm: {
      MM1: { ret: 7.7,  dd: 4.4,  final: 1077 },
      MM2: { ret: 62.0, dd: 8.8,  final: 1620 },
      MM3: { ret: 62.0, dd: 8.8,  final: 1620 },
      MM4: { ret: 62.0, dd: 8.8,  final: 1620 },
      MM5: { ret: 62.0, dd: 8.8,  final: 1620 },
      MM6: { ret: 62.0, dd: 8.8,  final: 1620 },
      MM7: { ret: 62.0, dd: 8.8,  final: 1620 },
      MM8: { ret: 62.0, dd: 8.8,  final: 1620, halted: false },
    },
    lev: {
      1:   { ret: 62.0,  dd: 8.8,  liqs: 0, liqRate: 0,    final: 1620 },
      2:   { ret: 124.0, dd: 8.8,  liqs: 1, liqRate: 1.3,  final: 2240 },
      5:   { ret: 310.0, dd: 8.8,  liqs: 3, liqRate: 3.8,  final: 4100 },
      10:  { ret: 620.0, dd: 8.8,  liqs: 6, liqRate: 7.6,  final: 7200 },
    },
    hours_wr: {
      7:  { wr: 58.8, n: 17 },
      12: { wr: 73.1, n: 26 },
      14: { wr: 61.5, n: 26 },
      18: { wr: 60.0, n: 10 },
    },
    equity: [52.5, 53.1, 54.2, 53.8, 55.1, 54.6, 56.2, 57.8, 57.1, 58.9, 60.1, 59.4, 61.2, 62.8, 64.1],
    outcomes: ['W','W','L','W','L','W','W','L','W','W','L','W','W','W','W'],
  },
};'''

if re.search(strategies_pattern, content, re.DOTALL):
    content = re.sub(strategies_pattern, new_strategies, content, flags=re.DOTALL)
    print('Fix 2 applied: STRATEGIES config updated with BTC_H and BTC_B stats')
    fixes += 1
else:
    print('Fix 2 NOT FOUND — STRATEGIES const')

# ─────────────────────────────────────────────────────────────
# FIX 3 — Default strategy in state
# ─────────────────────────────────────────────────────────────

old3 = "  strategy: 'BTC_G',"
new3 = "  strategy: 'BTC_H',"

if old3 in content:
    content = content.replace(old3, new3, 1)
    print('Fix 3 applied: default strategy set to BTC_H')
    fixes += 1
else:
    print('Fix 3 NOT FOUND — default strategy')

# ─────────────────────────────────────────────────────────────
# FIX 4 — SYMBOL_MAP references
# ─────────────────────────────────────────────────────────────

old4 = "  BTC_G: { symbol: 'BTCUSDT', timeframe: '15m' },\n  BTC_L: { symbol: 'BTCUSDT', timeframe: '15m' },"
new4 = "  BTC_H: { symbol: 'BTCUSDT', timeframe: '15m' },\n  BTC_B: { symbol: 'BTCUSDT', timeframe: '15m' },"

if old4 in content:
    content = content.replace(old4, new4, 1)
    print('Fix 4 applied: SYMBOL_MAP updated')
    fixes += 1
else:
    print('Fix 4 NOT FOUND — SYMBOL_MAP')

# ─────────────────────────────────────────────────────────────
# FIX 5 — keys arrays that reference old strategy names
# ─────────────────────────────────────────────────────────────

old5a = "  const keys     = ['BTC_G','BTC_L','ETH_G','SPY_L'];"
new5a = "  const keys     = ['BTC_H','BTC_B'];"
if old5a in content:
    content = content.replace(old5a, new5a)
    print('Fix 5a applied: keys array updated')
    fixes += 1

old5b = "  const keys = ['BTC_G','BTC_L','ETH_G','SPY_L'];"
new5b = "  const keys = ['BTC_H','BTC_B'];"
if old5b in content:
    content = content.replace(old5b, new5b)
    print('Fix 5b applied: second keys array updated')
    fixes += 1

old5c = "  Object.keys(STRATEGIES.BTC_G.mm).forEach(mm => {"
new5c = "  Object.keys(STRATEGIES.BTC_H.mm).forEach(mm => {"
if old5c in content:
    content = content.replace(old5c, new5c)
    print('Fix 5c applied: mm iteration updated')
    fixes += 1

old5d = "    ['BTC_G','BTC_L','ETH_G'].forEach(k => {"
new5d = "    ['BTC_H','BTC_B'].forEach(k => {"
if old5d in content:
    content = content.replace(old5d, new5d)
    print('Fix 5d applied: strategy forEach updated')
    fixes += 1

old5e = "    ['BTC_G','BTC_L'].forEach(k => {"
new5e = "    ['BTC_H','BTC_B'].forEach(k => {"
if old5e in content:
    content = content.replace(old5e, new5e)
    print('Fix 5e applied: second strategy forEach updated')
    fixes += 1

old5f = "  const keys = ['BTC_G','BTC_L','ETH_G'];"
new5f = "  const keys = ['BTC_H','BTC_B'];"
if old5f in content:
    content = content.replace(old5f, new5f)
    print('Fix 5f applied: ETH_G keys array updated')
    fixes += 1

# ─────────────────────────────────────────────────────────────
# FIX 6 — Fallback strategy references
# ─────────────────────────────────────────────────────────────

old6 = "  const key    = state.strategy === 'ALL' ? 'BTC_G' : state.strategy;\n  const { symbol, timeframe } = SYMBOL_MAP[key] || SYMBOL_MAP.BTC_G;"
new6 = "  const key    = state.strategy === 'ALL' ? 'BTC_H' : state.strategy;\n  const { symbol, timeframe } = SYMBOL_MAP[key] || SYMBOL_MAP.BTC_H;"

if old6 in content:
    content = content.replace(old6, new6, 1)
    print('Fix 6 applied: fallback strategy updated')
    fixes += 1
else:
    print('Fix 6 NOT FOUND — fallback strategy')

# ─────────────────────────────────────────────────────────────
# FIX 7 — btn-BTC_G references in selectStrategy
# ─────────────────────────────────────────────────────────────

content = content.replace("'btn-BTC_G'", "'btn-BTC_H'")
content = content.replace("'btn-BTC_L'", "'btn-BTC_B'")
content = content.replace('"btn-BTC_G"', '"btn-BTC_H"')
content = content.replace('"btn-BTC_L"', '"btn-BTC_B"')
print('Fix 7 applied: btn references updated')
fixes += 1

# ─────────────────────────────────────────────────────────────
# FIX 8 — mode display G/L → H/B
# ─────────────────────────────────────────────────────────────
old8 = '    "mode":            "G" if "_G" in trade.strategy else "L",'
# This is in main.py not dashboard — skip
print('Fix 8 skipped (main.py, not dashboard)')

# ─────────────────────────────────────────────────────────────
# SAVE
# ─────────────────────────────────────────────────────────────

open('/root/trading/dashboard_v2.html', 'w').write(content)
print(f'\ndashboard_v2.html saved — {fixes} fixes applied')
print('Refresh your browser to see the updated dashboard')
