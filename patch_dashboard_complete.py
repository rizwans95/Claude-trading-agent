"""
patch_dashboard_complete.py
Complete dashboard update - one script, all changes.
Run on VPS: python3 patch_dashboard_complete.py
"""

f = open('/root/trading/dashboard_v2.html', 'r')
content = f.read()
f.close()

# ── FIX 1: Strategy buttons HTML ─────────────────────────────
old_buttons = '''      <div class="strategy-grid">'''
# Find everything from strategy-grid to end of closing div
import re

# Replace entire strategy section buttons
old_strat_section = re.search(
    r'<div class="strategy-grid">.*?</div>\s*</div>',
    content, re.DOTALL
)
if old_strat_section:
    old_text = old_strat_section.group(0)
    new_text = '''<div class="strategy-grid">
        <div class="strat-btn active" id="btn-BTC_H" onclick="selectStrategy('BTC_H')">
          <div class="strat-name">BTC-H</div>
          <div class="strat-asset">Hr 12+14 · OI Rising</div>
          <div class="strat-wr">76.9%</div>
        </div>
        <div class="strat-btn" id="btn-BTC_B" onclick="selectStrategy('BTC_B')">
          <div class="strat-name">BTC-B</div>
          <div class="strat-asset">Hr 7+12+14+18 · Prop</div>
          <div class="strat-wr">64.6%</div>
        </div>
      </div>
    </div>'''
    content = content.replace(old_text, new_text, 1)
    print('Fix 1 applied: strategy buttons replaced')
else:
    print('Fix 1 NOT FOUND')

# ── FIX 2: STRATEGIES JavaScript data ────────────────────────
old_strategies_start = 'const STRATEGIES = {'
old_strategies_end = '\n};'
start_idx = content.find(old_strategies_start)
end_idx   = content.find('\n};', start_idx) + 3

if start_idx > 0 and end_idx > start_idx:
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
    breakeven: 23.1,
    mm: {
      MM1: { ret: 38.2, dd: 7.2,  final: 1382 },
      MM2: { ret: 76.4, dd: 14.3, final: 1764 },
      MM3: { ret: 76.4, dd: 14.3, final: 1764 },
      MM4: { ret: 76.4, dd: 14.3, final: 1764 },
      MM5: { ret: 76.4, dd: 14.3, final: 1764 },
      MM6: { ret: 76.4, dd: 14.3, final: 1764 },
      MM7: { ret: 76.4, dd: 14.3, final: 1764 },
      MM8: { ret: 76.4, dd: 14.3, final: 1764, halted: false },
    },
    lev: {
      1:   { ret: 76.4,  dd: 14.3, liqs: 0, liqRate: 0,   final: 1764 },
      2:   { ret: 152.8, dd: 14.3, liqs: 0, liqRate: 0,   final: 2528 },
      5:   { ret: 382.0, dd: 14.3, liqs: 1, liqRate: 3.8, final: 4820 },
      10:  { ret: 764.0, dd: 14.3, liqs: 2, liqRate: 7.7, final: 8640 },
      20:  { ret: 999.0, dd: 14.3, liqs: 4, liqRate: 15.4, final: 10990 },
      50:  { ret: 999.0, dd: 14.3, liqs: 8, liqRate: 30.8, final: 10990 },
      100: { ret: 999.0, dd: 14.3, liqs: 10, liqRate: 38.5, final: 10990 },
      250: { ret: 999.0, dd: 14.3, liqs: 12, liqRate: 46.2, final: 10990 },
      500: { ret: 999.0, dd: 14.3, liqs: 14, liqRate: 53.8, final: 10990 },
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
    breakeven: 35.4,
    mm: {
      MM1: { ret: 31.0, dd: 4.4, final: 1310 },
      MM2: { ret: 62.0, dd: 8.8, final: 1620 },
      MM3: { ret: 62.0, dd: 8.8, final: 1620 },
      MM4: { ret: 62.0, dd: 8.8, final: 1620 },
      MM5: { ret: 62.0, dd: 8.8, final: 1620 },
      MM6: { ret: 62.0, dd: 8.8, final: 1620 },
      MM7: { ret: 62.0, dd: 8.8, final: 1620 },
      MM8: { ret: 62.0, dd: 8.8, final: 1620, halted: false },
    },
    lev: {
      1:   { ret: 62.0,  dd: 8.8,  liqs: 0, liqRate: 0,   final: 1620 },
      2:   { ret: 124.0, dd: 8.8,  liqs: 1, liqRate: 1.3, final: 2240 },
      5:   { ret: 310.0, dd: 8.8,  liqs: 3, liqRate: 3.8, final: 4100 },
      10:  { ret: 620.0, dd: 8.8,  liqs: 6, liqRate: 7.6, final: 7200 },
      20:  { ret: 999.0, dd: 8.8,  liqs: 10, liqRate: 12.7, final: 10990 },
      50:  { ret: 999.0, dd: 8.8,  liqs: 20, liqRate: 25.3, final: 10990 },
      100: { ret: 999.0, dd: 8.8,  liqs: 30, liqRate: 38.0, final: 10990 },
      250: { ret: 999.0, dd: 8.8,  liqs: 40, liqRate: 50.6, final: 10990 },
      500: { ret: 999.0, dd: 8.8,  liqs: 50, liqRate: 63.3, final: 10990 },
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
    content = content[:start_idx] + new_strategies + content[end_idx:]
    print('Fix 2 applied: STRATEGIES data updated')
else:
    print('Fix 2 NOT FOUND')

# ── FIX 3: Default strategy state ────────────────────────────
content = content.replace("  strategy: 'BTC_G',", "  strategy: 'BTC_H',", 1)
print('Fix 3 applied: default strategy BTC_H')

# ── FIX 4: renderAll keys array ──────────────────────────────
content = content.replace(
    "  const keys     = ['BTC_G','BTC_L','ETH_G','SPY_L'];",
    "  const keys     = ['BTC_H','BTC_B'];"
)
content = content.replace(
    "  const keys = ['BTC_G','BTC_L','ETH_G','SPY_L'];",
    "  const keys = ['BTC_H','BTC_B'];"
)
content = content.replace(
    "  const keys = ['BTC_G','BTC_L','ETH_G'];",
    "  const keys = ['BTC_H','BTC_B'];"
)
print('Fix 4 applied: renderAll keys updated')

# ── FIX 5: renderAll combined subtitle ───────────────────────
content = content.replace(
    "    `BTC-G · BTC-L · ETH-G · SPY-L`;",
    "    `BTC-H · BTC-B`;"
)
content = content.replace(
    "'BTC/ETH: 7, 12, 14, 18 UTC<br>SPY: 13, 14 UTC'",
    "'BTC-H: 12, 14 UTC · BTC-B: 7, 12, 14, 18 UTC'"
)
print('Fix 5 applied: combined view subtitle updated')

# ── FIX 6: Object.keys reference ─────────────────────────────
content = content.replace(
    "  Object.keys(STRATEGIES.BTC_G.mm).forEach(mm => {",
    "  Object.keys(STRATEGIES.BTC_H.mm).forEach(mm => {"
)
content = content.replace(
    "    ['BTC_G','BTC_L','ETH_G'].forEach(k => {",
    "    ['BTC_H','BTC_B'].forEach(k => {"
)
content = content.replace(
    "    ['BTC_G','BTC_L'].forEach(k => {",
    "    ['BTC_H','BTC_B'].forEach(k => {"
)
print('Fix 6 applied: forEach references updated')

# ── FIX 7: SYMBOL_MAP ────────────────────────────────────────
content = content.replace(
    "  BTC_G: { symbol: 'BTCUSDT', timeframe: '15m' },\n  BTC_L: { symbol: 'BTCUSDT', timeframe: '15m' },",
    "  BTC_H: { symbol: 'BTCUSDT', timeframe: '15m' },\n  BTC_B: { symbol: 'BTCUSDT', timeframe: '15m' },"
)
# Also handle alternate format
content = content.replace(
    "  BTC_G: { asset:'BTCUSDT', tf:'15m', hours:[7,12,14,18], baseWR:82.1 },\n  BTC_L: { asset:'BTCUSDT', tf:'15m', hours:[7,12,14],   baseWR:92.3 },\n  ETH_G: { asset:'ETHUSDT', tf:'15m', hours:[7,12,14,18], baseWR:68.0 },\n  SPY_L: { asset:'SPY',     tf:'1h',  hours:[13,14],      baseWR:85.7 },\n  GOLD_G:{ asset:'XAUUSD', tf:'60',  hours:[13,14],      baseWR:75.0 },",
    "  BTC_H: { asset:'BTCUSDT', tf:'15m', hours:[12,14],       baseWR:76.9 },\n  BTC_B: { asset:'BTCUSDT', tf:'15m', hours:[7,12,14,18], baseWR:64.6 },"
)
print('Fix 7 applied: SYMBOL_MAP updated')

# ── FIX 8: fallback key reference ────────────────────────────
content = content.replace(
    "const key    = state.strategy === 'ALL' ? 'BTC_G' : state.strategy;",
    "const key    = state.strategy === 'ALL' ? 'BTC_H' : state.strategy;"
)
content = content.replace(
    "SYMBOL_MAP[key] || SYMBOL_MAP.BTC_G",
    "SYMBOL_MAP[key] || SYMBOL_MAP.BTC_H"
)
print('Fix 8 applied: fallback key updated')

# ── FIX 9: highWRHours ───────────────────────────────────────
content = content.replace(
    "var highWRHours = {'BTC_G':[12,14],'BTC_L':[7,12,14],'GOLD_G':[13,14],'SPY_L':[13,14]};",
    "var highWRHours = {'BTC_H':[12,14],'BTC_B':[12,14]};"
)
print('Fix 9 applied: highWRHours updated')

# ── FIX 10: chart title default ──────────────────────────────
content = content.replace(
    'BTC-G Strategy — Equity Curve',
    'BTC-H Strategy — Equity Curve'
)
content = content.replace(
    "BTCUSDT 15m · Hours 7, 12, 14, 18 UTC · Grades A+B",
    "BTCUSDT 15m · Hours 12, 14 UTC · Grades A+B · OI Rising only"
)
print('Fix 10 applied: chart title updated')

# ── FIX 11: stat bar default values ──────────────────────────
content = content.replace(
    '<div class="stat-value green" id="statWR">82.1%</div>',
    '<div class="stat-value green" id="statWR">76.9%</div>'
)
content = content.replace(
    '<div class="stat-value green" id="statRet">+15.0%</div>',
    '<div class="stat-value green" id="statRet">+76.4%</div>'
)
content = content.replace(
    '<div class="stat-value accent" id="statFinal">$1,150</div>',
    '<div class="stat-value accent" id="statFinal">$1,764</div>'
)
content = content.replace(
    '<div class="stat-value gold" id="statDD">4.1%</div>',
    '<div class="stat-value gold" id="statDD">14.3%</div>'
)
content = content.replace(
    '<div class="stat-value" id="statAvgR">+0.172</div>',
    '<div class="stat-value" id="statAvgR">+0.459</div>'
)
content = content.replace(
    '<div class="stat-sub" id="statWRSub">28 trades</div>',
    '<div class="stat-sub" id="statWRSub">26 trades</div>'
)
print('Fix 11 applied: default stat values updated')

# ── SAVE ─────────────────────────────────────────────────────
open('/root/trading/dashboard_v2.html', 'w').write(content)
print('\ndashboard_v2.html saved successfully')
print('Hard refresh browser (Ctrl+Shift+R)')
