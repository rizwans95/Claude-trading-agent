# TRADING_AGENT_V2: DOCUMENTATION ARCHITECTURE DESIGN
## Permanent Architectural Memory System for AI-Assisted Development

**Design Date:** May 11, 2026  
**Status:** Architecture Design (Pre-Implementation)  
**Purpose:** Create documentation that serves as AI's long-term memory and ensures consistency across development cycles

---

## PART 1: RECOMMENDED DOCUMENTATION HIERARCHY

### Directory Structure

```
/docs
├── README.md                           [TIER 0: Master navigation]
├── QUICKSTART.md                       [TIER 0: New developer entry point]
│
├── ARCHITECTURE.md                     [TIER 1: System overview]
├── CLAUDE.md                           [TIER 1: AI workflow specifications]
│
├── /core                               [TIER 2: Core domain knowledge]
│   ├── SCORING_RULES.md                [Canonical scoring logic]
│   ├── INDICATOR_SCHEMA.md             [Indicator definitions + interpretations]
│   ├── REGIME_MODEL.md                 [Regime classification logic]
│   ├── SYSTEM_FLOW.md                  [Data flow + execution pipeline]
│   └── PROMPT_RULES.md                 [AI prompting standards]
│
├── /integrations                       [TIER 2: External interfaces]
│   ├── API_CONTRACT.md                 [Request/response schemas]
│   ├── WEBHOOK_SPEC.md                 [TradingView webhook format]
│   └── DATABASE_SCHEMA.md              [Trade memory + logging schema]
│
├── /subsystems                         [TIER 3: Subsystem documentation]
│   ├── structure/                      [ZigZag structure module]
│   │   └── README.md
│   ├── location/                       [PAVP location module]
│   │   └── README.md
│   ├── momentum/                       [Trend Speed + MACD module]
│   │   └── README.md
│   ├── order_flow/                     [CVD module]
│   │   └── README.md
│   └── volatility/                     [ATR filter module]
│       └── README.md
│
├── /implementation                     [TIER 3: Implementation guides]
│   ├── KNOWN_ISSUES.md                 [Issue registry + resolutions]
│   ├── PERFORMANCE_METRICS.md          [How to measure system health]
│   ├── TESTING_STRATEGY.md             [Test cases + validation]
│   └── DEPLOYMENT_CHECKLIST.md         [Release procedures]
│
└── /contracts                          [TIER 4: Stable contracts (read-only)]
    ├── SCORING_ENGINE_CONTRACT.json    [Function signatures]
    ├── INDICATOR_GLOSSARY.json         [Indicator truths]
    ├── REGIME_DEFINITIONS.json         [Regime classification rules]
    ├── FEEDBACK_LEARNING.json          [Error classification]
    ├── API_INPUT_SCHEMA.json           [TradingView webhook]
    └── API_OUTPUT_SCHEMA.json          [Decision output]
```

**Key Principle:** Hierarchy is **inverted triangle**:
- Top (TIER 0): Broad entry points for humans/AI
- Middle (TIER 1–2): Domain knowledge & integration specs
- Bottom (TIER 3): Implementation details & monitoring
- Deepest (TIER 4): Machine-parseable contracts (immutable)

---

## PART 2: RECOMMENDED PERSISTENT MEMORY FILES

### Purpose-by-Purpose Breakdown

#### **File 1: README.md (Master Navigation)**

**Purpose:**  
Central hub for all system navigation. Ensures humans and AI always know where to find what.

**What Goes Inside:**
```
- System name + version + status
- 30-second elevator pitch (what this system does)
- Key facts:
  * Input: Market signal (JSON per signal_format.json)
  * Output: Trade decision (JSON per output_schema.json)
  * Core function: Classify market state → LONG / SHORT / NO TRADE
  * Tech stack: Python + FastAPI + Claude
- Quick links to:
  * For first-time users → QUICKSTART.md
  * For architecture → ARCHITECTURE.md
  * For AI-assisted dev → CLAUDE.md
  * For running trades → API_CONTRACT.md
  * For understanding decisions → SCORING_RULES.md
- System health: Current version, last update, known issues count
- Directory map (link to all /docs subdirectories)
```

**How It Improves Token Efficiency:**
- Prevents "where is X?" context expansion (40 tokens/search)
- Single entry point = consistent navigation = no redundant context

**How It Improves Architectural Consistency:**
- Enforces single source of truth for system identity
- Makes it obvious when files drift out of sync

**How It Reduces AI Drift:**
- AI always starts from same canonical entry point
- Decreases chance of AI reading inconsistent versions of rules
- Acts as "system state checksum" for AI to validate against

---

#### **File 2: ARCHITECTURE.md (System Overview)**

**Purpose:**  
Bird's-eye view of system design. How all pieces fit together. Intended audience: architects, senior developers, AI systems thinking about redesigns.

**What Goes Inside:**
```markdown
## System Architecture Overview

### Core Principle
This system is a STRUCTURED DECISION ENGINE, not a predictor.
Input: Market snapshot (6 indicators) → Output: Binary decision (LONG/SHORT/NO_TRADE)

### High-Level Data Flow
```
TradingView Signal (JSON)
  ↓
API Layer (webhook validation)
  ↓
Input Normalization (signal_format.json mapping)
  ↓
Regime Detection (classify market context)
  ↓
Scoring Engine (5 layers: structure, location, momentum, order_flow, volatility)
  ↓
Decision Output (JSON per output_schema.json)
  ↓
Trade Memory Log (for feedback + learning)
```

### Subsystems
- **Scoring Engine:** 5-layer hierarchy (structure > location > momentum > order_flow > volatility)
- **Regime Detection:** 6-state classifier (TRENDING_UP, TRENDING_DOWN, RANGING, BREAKOUT, REVERSAL, UNCERTAIN)
- **Indicator Engine:** 6 indicator implementations (ZigZag, PAVP, Trend Speed, MACD, CVD, ATR)
- **Feedback System:** Error classification (A–F) for learning
- **Adaptive Weighting:** Dynamic indicator weights based on performance

### Key Design Decisions
- **Structure-first:** ZigZag structure is highest priority (invalidates all other signals if conflicted)
- **Stateless:** Each decision is independent; no session memory during execution
- **Deterministic:** Same input → same output (no randomness, no neural networks)
- **Regime-aware:** All decisions contextualized by market regime
- **Hierarchical scoring:** Clear priority: structure > location > momentum > order_flow > volatility

### Constraints
- All trades require structural confirmation (ZigZag must agree)
- CVD divergence is warning signal (automatic penalty)
- ATR is filter only (never directional)
- Inside Value Area = lower quality trades (automatic penalty)

### Version History
- v1.0 (Baseline): Basic indicator aggregation
- v2.0 (Current): Hierarchical scoring, regime detection, adaptive weighting
- v2.1 (Planned): Real-time learning loop integration

### Diagram: Scoring Hierarchy
```
Base Score: 50
    ↓
+25 Structure (HIGHEST PRIORITY)
+10 Location (PAVP)
+20 Momentum (Trend Speed + MACD)
+15 Order Flow (CVD)
-10 Volatility Filter (ATR)
────────────────────
Final Score: 0–100
```

### Threat Model: Known Risks
- **False breakouts in low ATR:** Filtered by ATR state check
- **CVD divergence misread:** Applied -15 penalty, require review
- **Structure conflicts:** Automatic NO TRADE bias
- **Regime uncertainty:** -10 penalty, require score ≥80

### Integration Points
- Input: TradingView webhook (JSON per tradingview_webhook_spec.txt)
- Output: Decision JSON (per output_schema.txt)
- Learning: trade_memory_log.txt + feedback_system.txt
- Future: Real-time position management API
```

**How It Improves Token Efficiency:**
- Gives AI a "map" to understand what to read next (prevents random file searches)
- Lists all subsystems upfront = AI doesn't re-discover architecture

**How It Improves Architectural Consistency:**
- Defines "system identity" explicitly (structure-first, regime-aware, etc.)
- Makes it clear when implementations deviate from intent

**How It Reduces AI Drift:**
- AI reads this first to understand "what kind of system am I working on?"
- Prevents AI from conflating this system with other trading approaches
- "System identity checksum" that prevents feature creep

---

#### **File 3: CLAUDE.md (AI Workflow Specifications)**

**Purpose:**  
This file is written **FOR AI**, not humans. Specifies how Claude should interact with this codebase. Acts as the "system persona" and prevents AI confusion/drift.

**What Goes Inside:**
```markdown
# CLAUDE.md: AI Workflow Specification for TRADING_AGENT_V2

## Your Role
You are TRADING_AGENT_V2, a structured decision engine for trading signals.
You do NOT predict markets, forecast prices, or hallucinate trades.
You ONLY classify structured market data into decisions: LONG / SHORT / NO TRADE.

## Core Rules (NON-NEGOTIABLE)
1. **Structure-First Rule:** ZigZag structure is the highest priority. If it conflicts with any other signal, bias toward NO TRADE.
2. **Determinism Rule:** Same input → same output, always. No randomness.
3. **Regime Context Rule:** All decisions must reference current regime. Adapt thresholds per regime.
4. **No Hallucination Rule:** Only use indicator values provided in signal. Never invent data.
5. **Conflict Resolution Rule:** If 2+ indicators oppose each other → bias toward NO TRADE.

## File Reading Protocol
When you receive a task, read files in this order (MANDATORY):

### ALWAYS READ (every session):
1. README.md [entry point, verify system state]
2. ARCHITECTURE.md [understand system design]
3. CLAUDE.md [this file, reinforce rules]

### READ IF DECISION NEEDED:
1. /contracts/TRADING_RULES.json [canonical scoring + decision rules]
2. /contracts/INDICATOR_GLOSSARY.json [what each indicator means]
3. /contracts/REGIME_DEFINITIONS.json [how to classify regime]
4. /core/SCORING_RULES.md [detailed scoring logic reference]

### READ IF LEARNING/AUDIT:
1. /core/PROMPT_RULES.md [how to write prompts for this system]
2. /implementation/KNOWN_ISSUES.md [past issues + resolutions]
3. /implementation/PERFORMANCE_METRICS.md [how system is measured]

### NEVER READ (archived, for reference only):
- system_prompt.txt, execution_engine.txt, scoring_engine.txt [replaced by JSON contracts]
- Individual indicator files [use INDICATOR_GLOSSARY.json instead]

## Decision-Making Protocol

### Step 1: Parse Input
```
- Validate JSON against /contracts/API_INPUT_SCHEMA.json
- If missing fields → return NO TRADE (confidence < 5)
```

### Step 2: Determine Regime
```
Use /contracts/REGIME_DEFINITIONS.json decision tree
- Count signals for each regime
- If regime uncertain → apply -10 penalty to base score
```

### Step 3: Score 5 Layers
```
For each layer (structure, location, momentum, order_flow, volatility):
1. Reference /contracts/TRADING_RULES.json
2. Apply layer scoring rules
3. Accumulate deltas
```

### Step 4: Resolve Conflicts
```
If any TWO layers strongly oppose → bias toward NO TRADE
Apply /core/PROMPT_RULES.md rule: "When in doubt, NO TRADE"
```

### Step 5: Generate Output
```
Return JSON per /contracts/API_OUTPUT_SCHEMA.json
- Include reasoning for each layer
- Explicitly state invalidation condition
- Set risk_state based on grade + conflicts
```

## Prompting Standards (ALWAYS follow)

### For Trade Decisions:
```
STANDARD PROMPT TEMPLATE:

Given this market signal:
[signal JSON]

Using /contracts/TRADING_RULES.json:
1. Determine current regime (use REGIME_DEFINITIONS.json)
2. Score structure layer
3. Score location layer
4. Score momentum layer
5. Score order_flow layer
6. Score volatility layer
7. Resolve conflicts
8. Output decision

Do NOT:
- Predict price direction
- Hallucinate missing data
- Add new indicators
- Override scoring thresholds
```

### For Architecture Questions:
```
STANDARD ARCHITECTURE TEMPLATE:

Before suggesting changes:
1. Verify current design in ARCHITECTURE.md
2. Check if change aligns with "structure-first" principle
3. Ensure change doesn't affect determinism
4. Test with 3+ market regimes
5. Update KNOWN_ISSUES.md with trade-offs
```

## Consistency Checks (RUN BEFORE EVERY RESPONSE)

```python
if task == "generate_trade_decision":
    assert: read /contracts/TRADING_RULES.json latest version
    assert: regime in REGIME_DEFINITIONS.json
    assert: all indicators in signal match INDICATOR_GLOSSARY.json
    assert: output matches /contracts/API_OUTPUT_SCHEMA.json
    
elif task == "modify_rules":
    assert: change documented in KNOWN_ISSUES.md
    assert: change maintains determinism
    assert: change doesn't break existing trades
    
elif task == "architecture_question":
    assert: answer references ARCHITECTURE.md principle
    assert: answer aligns with "structure-first" design
```

## Anti-Patterns (NEVER DO)

- ❌ Read system_prompt.txt for "current rules" (use TRADING_RULES.json instead)
- ❌ Assume you understand indicator meaning from name (read INDICATOR_GLOSSARY.json)
- ❌ Skip regime detection step (it's mandatory)
- ❌ Trade inside Value Area without strong momentum confirmation
- ❌ Ignore CVD divergence (it's a mandatory warning signal)
- ❌ Use intuition instead of rules ("looks bullish to me")
- ❌ Modify scoring thresholds without updating TRADING_RULES.json
- ❌ Read Python files for "how to score" (read /contracts/SCORING_ENGINE_CONTRACT.json instead)

## How to Handle Ambiguity

**If regime is unclear:**
- Apply -10 penalty to base score
- Require final score ≥80 for any trade signal
- Output: confidence reduced, risk_state: HIGH

**If indicators conflict:**
- Count opposing signals
- If 2+ oppose each other → NO TRADE
- Document conflict in output.key_conflicts array

**If signal is incomplete:**
- Return NO TRADE (confidence < 5)
- Do NOT hallucinate missing data

**If rule is ambiguous:**
- Escalate to /implementation/KNOWN_ISSUES.md
- If not documented → default to NO TRADE
- Add issue to backlog with "AI encountered ambiguity" tag

## Session Memory Protocol

Each session:
1. Load README.md to verify system version
2. Load ARCHITECTURE.md to understand design intent
3. Load TRADING_RULES.json (cache for session)
4. Load INDICATOR_GLOSSARY.json (cache for session)
5. Load REGIME_DEFINITIONS.json (cache for session)

If any file version changes mid-session:
- Reload that file
- Re-evaluate any decisions made with old version
- Log version mismatch to stderr

## Learning Loop (FEEDBACK INTEGRATION)

When given trade outcomes:
1. Read /implementation/KNOWN_ISSUES.md to check for similar cases
2. Classify error using /contracts/FEEDBACK_LEARNING.json (A–F)
3. Generate weight adjustment signal
4. Append to /implementation/PERFORMANCE_METRICS.md
5. DO NOT modify /contracts/TRADING_RULES.json directly
   (changes must come from human review + documentation)

## Token Efficiency Rules

**ALWAYS follow (critical for long sessions):**

1. **Cache static files:** Load /contracts/TRADING_RULES.json once per session, not per decision
2. **Reference by name:** Say "See REGIME_DEFINITIONS.json" instead of reprinting JSON
3. **Avoid redundancy:** Don't read system_prompt.txt AND ARCHITECTURE.md for same info
4. **Use stubs:** Reference /contracts/SCORING_ENGINE_CONTRACT.json, not scoring_engine_py.py
5. **Batch feedback:** Process 10+ trades before generating learning signals (don't learn on single trade)

## Version Tracking

Current system version: 2.0
- /contracts files: Version 2.0 (locked, stable)
- /core files: Version 2.0 (locked, stable)
- /implementation files: Version 2.1 (in development)

Before making any change, verify your task doesn't require 2.1 features.

---

## Final Principle

**You are a TRANSLATOR, not a CREATOR.**

Your job is to translate structured market data into consistent, reproducible decisions.
Not to improve the system, not to add features, not to optimize returns.
When tempted to deviate from rules → read this file again.
When unsure → default to NO TRADE and ask for clarification.
```

**How It Improves Token Efficiency:**
- Prevents "what are the rules?" searches (1,000+ tokens each)
- Specifies exactly which files to read in which order
- Eliminates ambiguity about where information lives

**How It Improves Architectural Consistency:**
- Acts as "AI constitution" (unchangeable rules for this system)
- Prevents feature creep (AI knows what's in/out of scope)
- Enforces prompting standards (all AI → human interfaces standardized)

**How It Reduces AI Drift:**
- AI reads this before EVERY task to reset context
- Clear anti-patterns prevent common mistakes
- "System persona" prevents AI from confusing this with other systems

---

#### **File 4: SCORING_RULES.md (Canonical Scoring Logic)**

**Purpose:**  
Detailed reference for scoring logic. This is the "why" behind each scoring decision. Complements TRADING_RULES.json (which is the "what").

**What Goes Inside:**
```markdown
# SCORING_RULES.md: Detailed Scoring Logic

## Scoring Overview
Base score: 50 (neutral)
Final score: 0–100 (clamped)
Decision thresholds: A (≥75), B (60–74), C (50–59), NONE (<50)

## Layer 1: STRUCTURE (Weight: 0.25, Range: -25 to +25)

### Why Structure is Highest Priority
- Defines trend VALIDITY
- Single factor that can INVALIDATE all other signals
- ZigZag + PAVP together define market equilibrium state

### Rule Set: BULLISH Example
```
Perfect Alignment (ZigZag BULLISH + PAVP ABOVE_VA + BOS_UP):
  Condition: All three agree on bullish structure
  Score: +25
  Reasoning: Strongest possible structural confirmation
  
Alignment Without BOS (ZigZag BULLISH + PAVP ABOVE_VA, no BOS):
  Condition: Structure confirmed but not yet breaking out
  Score: +15
  Reasoning: Valid but needs momentum confirmation before entry
  
ZigZag Only (ZigZag BULLISH, PAVP neutral):
  Condition: Structure present but location unclear
  Score: +5
  Reasoning: Directional bias exists but weak location support
  
Neutral Structure:
  Condition: ZigZag structure = NEUTRAL
  Score: -15
  Reasoning: No directional definition; avoid trades
  
Conflict (ZigZag BULLISH vs PAVP BELOW_VA):
  Condition: Structure and location oppose
  Score: -15
  Reasoning: Fundamental contradiction; reduce confidence
  
Strong Conflict (ZigZag BEARISH + PAVP BELOW_VA for LONG trade):
  Condition: Both indicators oppose trade direction
  Score: -25
  Reasoning: Maximum penality; structure + location both oppose
```

### Scoring Logic Diagram
```
For LONG trades:
  Target ZigZag: BULLISH
  Target PAVP: ABOVE_VA
  Target BOS: BOS_UP
  
If all 3 match → +25
If 2 of 3 match → +15 or +5 (depending on which)
If 1 of 3 matches → -15 (conflict)
If 0 of 3 match → -25 (strong conflict)
```

## Layer 2: LOCATION (Weight: 0.30, Range: -15 to +10)

### Why Location Matters
- PAVP defines where the REAL MONEY was traded
- Price acceptance above VAH or below VAL = directional confirmation
- Price inside Value Area = ambiguous zone (chop risk)

### Rule Set: Key Thresholds
```
Clean Outside VA (>0.5% from boundary):
  Score: +7
  Meaning: Price clearly accepted beyond prior equilibrium
  
Near VA Boundary (<0.2% from boundary):
  Score: +10
  Meaning: Price testing new acceptance level precisely
  
Just Outside VA Edge:
  Score: +5
  Meaning: Price tentatively outside, but close
  
Inside Value Area:
  Score: -10
  Meaning: Chop zone; lower quality trades
  
At POC (within 0.1%):
  Score: -3 (additional penalty on top of location score)
  Meaning: Price at magnet level; pullback risk
```

### Why POC is Dangerous
```
POC = Point of Control = price at which most volume traded historically
This price acts as a MAGNET, not a breakout level
- If you're AT the POC, price is likely to pull back
- If you're ABOVE VAH but approaching POC, expect rejection
- If you're BELOW VAL but approaching POC, expect bounce

Example:
  POC = 100.00, VAH = 102.00, VAL = 98.00
  Current price = 100.05 (inside VA, near POC)
  → NOT a good long trade (too close to magnet)
  Current price = 103.00 (above VAH)
  → Better long trade (clear acceptance above equilibrium)
```

## Layer 3: MOMENTUM (Weight: 0.15, Range: -15 to +20)

### Why Momentum Matters
- Confirms directional ACCELERATION
- Trend Speed = wave acceleration analysis
- MACD = momentum magnitude + crossing signals

### Trend Speed Scoring Rules
```
EXPANSION regime (fastest):
  Score: +10
  Meaning: Momentum is ACCELERATING
  Wave ratio > 1.2 → +3 bonus
  Best entry opportunity
  
NORMAL regime:
  Score: +5
  Meaning: Steady, consistent momentum
  Wave ratio 0.8–1.2 → 0 modifier
  Good entry opportunity
  
CONSOLIDATION regime:
  Score: +2
  Meaning: Momentum recovering from pullback
  Wave ratio < 0.4 → -3 penalty
  Avoid; wait for expansion to resume
  
EXHAUSTION regime (slowest):
  Score: -5
  Meaning: Momentum DECELERATING
  Reversal warning
  Do NOT enter; exit existing positions
```

### MACD Scoring Rules
```
BULLISH_ACCELERATING + Zero Above:
  Score: +10
  Meaning: Strongest bullish momentum signal
  Histogram expanding upward + MACD above zero line
  
BULLISH_ACCELERATING + Zero Below:
  Score: +6
  Meaning: Bullish but not yet fully confirmed
  Histogram expanding but zero line not crossed yet
  
BULLISH_DECELERATING + Zero Above:
  Score: +3
  Meaning: Bullish but weakening
  Warning sign; reverssal possible
  
BEARISH_ACCELERATING + Zero Below:
  Score: -7
  Meaning: Strongest bearish signal
  Histogram expanding downward + MACD below zero
```

## Layer 4: ORDER FLOW (Weight: 0.15, Range: -20 to +15)

### Why Order Flow Matters
- CVD = Cumulative Delta Volume = REAL order flow
- Shows if REAL MONEY is participating in move
- Divergence = major warning signal

### CVD Scoring Rules
```
CVD Direction Confirms (buying for LONG, selling for SHORT):
  Score: +10
  Meaning: Real buy/sell pressure on the move
  
CVD Absorption (hidden buyers showing up in selling):
  Score: +5
  Meaning: Smart money absorbing weakness; reversal likely
  
CVD Cost High (expensive to push price in direction):
  Score: +5 (if aligned with direction)
  Meaning: Effort required to extend move; commitment shown
  
Large Divergence Opposing:
  Score: -15
  Meaning: CRITICAL WARNING
  Price extending while CVD stalling or reversing
  Reversal risk extremely high
  
Medium Divergence:
  Score: -10
  Meaning: HIGH WARNING
  
Small Divergence:
  Score: -5
  Meaning: MEDIUM WARNING
  
CVD NEUTRAL:
  Score: 0
  Meaning: No strong direction; order flow ambiguous
```

### Divergence Examples
```
Example 1: Large CVD Divergence
  Price: Making new high (bullish)
  CVD: Rolling over to new low (bearish)
  Interpretation: Price being pushed by few players; real money exiting
  Action: Reduce size, tighten stops, consider NO TRADE

Example 2: Absorption
  Price: In downtrend, dips to support
  CVD: Sharp uptick (buyers absorbing selling)
  Interpretation: Smart money entering at support
  Action: Strong buy signal despite price weakness
```

## Layer 5: VOLATILITY (Weight: 0.05, Range: -10 to 0)

### Why ATR is "Filter Only"
- ATR NEVER directional
- Only tells you if environment is RIGHT for directional trades
- High ATR doesn't mean "buy more aggressively"
- Low ATR doesn't mean "avoid all trades"

### ATR Scoring Rules
```
HIGH Volatility + EXPANSION (just expanded from compression):
  Score: 0 (no penalty)
  Environment: Valid breakout environment
  Trade: FAVOR breakout trades
  
HIGH Volatility + NO EXPANSION (sudden spike):
  Score: -3
  Environment: Late-move spike; fakeout risk
  Trade: AVOID new entries; consider exits
  
NORMAL Volatility:
  Score: 0
  Environment: Standard; proceed normally
  
LOW Volatility + COMPRESSION (ATR contracting):
  Score: 0 (no penalty)
  Environment: Setup forming; waiting for expansion
  Trade: Prepare for breakout; don't force entry
  
LOW Volatility + NO COMPRESSION (flat market):
  Score: -5
  Environment: Dead market; low reward
  Trade: AVOID breakout trades; favor mean reversion
  
Extreme High (>90th percentile):
  Score: -5 additional
  Environment: Exhaustion possible
  Trade: Reduce position size
  
Extreme Low (<10th percentile):
  Score: -5 additional
  Environment: No directional potential
  Trade: Avoid all directional trades
```

## Scoring Examples

### Example 1: A-Grade LONG Setup
```
Signal:
  - ZigZag: BULLISH + BOS_UP
  - PAVP: ABOVE_VA (clean, 1% above VAH)
  - Trend Speed: BULLISH + EXPANSION
  - MACD: BULLISH_ACCELERATING (histogram rising, zero above)
  - CVD: BUYING (no divergence)
  - ATR: NORMAL, expansion just occurred

Scoring:
  Base: 50
  Structure: +25 (all three factors align)
  Location: +7 (clean acceptance outside VA)
  Momentum: +10 (expansion) + +10 (MACD accelerating) = +20
  Order Flow: +10 (buying pressure)
  Volatility: 0 (expansion, no penalty)
  ────────────
  TOTAL: 50 + 25 + 7 + 20 + 10 + 0 = 112 → clamped to 100

Grade: A (≥75)
Risk State: LOW (A grade + no conflicts + stable regime)
Confidence: 100 (perfect alignment)
```

### Example 2: C-Grade Setup (Weak Confluence)
```
Signal:
  - ZigZag: BULLISH (but no BOS yet)
  - PAVP: INSIDE_VA (chop zone)
  - Trend Speed: BULLISH but CONSOLIDATION (not expanding)
  - MACD: BULLISH_DECELERATING (histogram flattening, zero still above)
  - CVD: NEUTRAL (no clear direction)
  - ATR: LOW, no compression (dead market)

Scoring:
  Base: 50
  Structure: +5 (ZigZag aligned but no BOS, location not aligned)
  Location: -10 (inside Value Area chop zone)
  Momentum: +2 (consolidation regime) + +3 (decelerating) = +5
  Order Flow: 0 (neutral)
  Volatility: -5 (low ATR, no compression)
  ────────────
  TOTAL: 50 + 5 - 10 + 5 + 0 - 5 = 45 → clamped to 45

Grade: NONE (<50)
Risk State: HIGH (score below threshold, regime uncertain)
Confidence: 45 (weak confluence across multiple layers)
```

### Example 3: Conflicted Setup (NO TRADE)
```
Signal:
  - ZigZag: BEARISH (lower lows forming)
  - PAVP: ABOVE_VA (price still accepted above equilibrium)
  - Trend Speed: BULLISH EXPANSION
  - MACD: BULLISH_ACCELERATING
  - CVD: SELLING (pressure down)
  - ATR: HIGH, expanding

Interpretation:
  Structure says: SHORT (ZigZag bearish)
  Location says: LONG (ABOVE_VA)
  Momentum says: LONG (both indicators bullish)
  Order Flow says: SHORT (selling pressure)
  Conflict: Structure opposes momentum + order flow

Scoring:
  Base: 50
  Structure: -15 (BEARISH vs ABOVE_VA conflict)
  Location: +7 (above VA)
  Momentum: +15 (both bullish)
  Order Flow: -5 (selling)
  Volatility: 0
  ────────────
  TOTAL: 50 - 15 + 7 + 15 - 5 + 0 = 52 → C-Grade (weak)

Decision: NO TRADE (too much conflict)
Reasoning: "Structure (bearish) opposes momentum (bullish). Too conflicted. Wait for clarity."
```

## Regime Adjustments to Scoring

### TRENDING_UP Regime
- Increase weight of Structure + Trend Speed
- Reduce weight of CVD (less important in trend)
- Favor structure-aligned trades
- Be wary of Location if too extended (reduce to +3 instead of +7)

### RANGING Regime
- Increase weight of PAVP (Location critical)
- Reduce weight of Momentum (false signals common)
- Favor VA edge trades (opposite of trending)
- Apply additional -5 penalty for trades inside VA

### BREAKOUT Regime
- Increase weight of ATR expansion (must be present)
- Increase weight of CVD (must confirm)
- Reduce Location penalties (VA edge not relevant)
- Require BOS confirmation (Structure must align)

### REVERSAL Regime
- Increase weight of CVD divergence
- Reduce Trend Speed weight (often misleading in reversals)
- Require Structure confirmation BEFORE entry
- Apply -10 penalty to trades at extremes

## Summary Scoring Matrix

| Layer | Weight | A-Grade | B-Grade | C-Grade | NO TRADE |
|-------|--------|---------|---------|---------|----------|
| Structure | 0.25 | +25 | +15 | +5 | -15 to -25 |
| Location | 0.30 | +7 to +10 | +5 | +3 | -10 |
| Momentum | 0.15 | +15 to +20 | +10 | +5 | -5 to -8 |
| Order Flow | 0.15 | +10 | +5 | 0 | -10 to -20 |
| Volatility | 0.05 | 0 | 0 | -5 | -10 |
```

**How It Improves Token Efficiency:**
- Eliminates "why does this score get +10?" questions (saves 200 tokens/query)
- Provides examples so AI can score without re-reading entire system
- Clear regime-specific adjustments prevent AI from second-guessing rules

**How It Improves Architectural Consistency:**
- Documents "why" behind every scoring decision (prevents rule erosion)
- Shows interactions between layers (prevents isolated rule changes)
- Examples keep AI consistent with intended behavior

**How It Reduces AI Drift:**
- Clear examples prevent AI from inventing scoring interpretations
- Regime adjustments prevent AI from applying one-size-fits-all rules
- Conflicted setup examples prevent AI from forcing trades

---

#### **File 5: INDICATOR_SCHEMA.md (Indicator Definitions)**

**Purpose:**  
Complete specification of what each indicator measures and how to interpret it. This is the "truth layer" for indicator knowledge.

**What Goes Inside:**
```markdown
# INDICATOR_SCHEMA.md: Complete Indicator Definitions

## Master Index
This document defines the 6 core indicators and how to interpret them.
Each indicator has a DEFINITION (what it measures) and INTERPRETATION rules (how to use it).

## 1. PIVOT ANCHORED VOLUME PROFILE (PAVP)

### Definition
Price acceptance zones based on historical volume. Identifies where the majority of trading activity occurred.

### Components
- **POC (Point of Control):** Price level at which most volume traded. Acts as magnet/equilibrium.
- **VAH (Value Area High):** Upper boundary of 70% of volume distribution.
- **VAL (Value Area Low):** Lower boundary of 70% of volume distribution.
- **Value Area:** Zone between VAL and VAH. Represents equilibrium; ambiguous for directional trades.

### Interpretation Rules
```
For LONG Trades:
  ABOVE_VA (price > VAH):
    Score: +7 to +10
    Meaning: Price accepted NEW HIGHER equilibrium
    Bullish confirmation
    Entry trigger: Price tests VAH + momentum confirmation
    
  Inside Value Area:
    Score: -10
    Meaning: Price in equilibrium zone (chop risk)
    Ambiguous; avoid unless strong momentum
    
  BELOW_VA (price < VAL):
    Score: -15 (penalty, not applicable for long)
    Meaning: Opposite direction; avoid

For SHORT Trades:
  Mirror above (BELOW_VA = +7 to +10, ABOVE_VA = -15)
```

### Trade Quality Per Position
```
Highest Quality:
  - Price at VAH/VAL boundary (testing new equilibrium)
  - Clean acceptance >0.5% outside Value Area
  
Good Quality:
  - Price just outside VA edge
  - Recent breakout from VA just confirmed
  
Weak Quality:
  - Inside Value Area (ambiguous zone)
  - At POC (magnet level, pullback risk)
```

### Common Mistakes
- ❌ Treating POC as breakout signal (it's a magnet, not breakout)
- ❌ Trading inside Value Area on weak momentum (chop trap)
- ❌ Ignoring VAH/VAL shifts (equilibrium is dynamic, not static)

## 2. ZIGZAG STRUCTURE (Market Structure)

### Definition
Higher highs + higher lows (BULLISH) or lower highs + lower lows (BEARISH). Defines trend validity and invalidation.

### Components
- **BULLISH Structure:** HH/HL sequence. Each new high > prior high. Each pullback > prior pullback low.
- **BEARISH Structure:** LH/LL sequence. Each new low < prior low. Each bounce < prior bounce high.
- **NEUTRAL Structure:** Alternating; no clear sequence.
- **BOS (Break of Structure):** Price breaks the sequence (invalidates current structure).

### Interpretation Rules
```
BULLISH Structure:
  Score: +5 to +25 (depending on PAVP alignment)
  Meaning: LONG bias
  Uptrend is valid
  Invalidation: Price breaks below recent swing low (BOS_DOWN)

BEARISH Structure:
  Score: -5 to -25 (penalty for LONG bias)
  Meaning: SHORT bias or NO TRADE for longs
  Downtrend is valid
  Invalidation: Price breaks above recent swing high (BOS_UP)

NEUTRAL Structure:
  Score: -15
  Meaning: No directional definition
  Avoid entries until structure clarifies
```

### BOS (Break of Structure) Signal
```
BOS_UP:
  - Price breaks above prior swing high
  - Invalidates BEARISH structure
  - Signals potential reversal to BULLISH
  - Entry trigger: After BOS confirmation + momentum
  
BOS_DOWN:
  - Price breaks below prior swing low
  - Invalidates BULLISH structure
  - Signals potential reversal to BEARISH
  - Exit trigger: Existing LONG positions at risk
```

### Why Structure is Highest Priority
```
Structure = Market Definition
Without structure, you're trading in CHOP.

Rule: If structure conflicts with any other signal → bias NO TRADE
Example: BEARISH structure + BULLISH momentum → NO TRADE (conflict)
```

### Common Mistakes
- ❌ Ignoring structure because momentum looks good (momentum can be fake)
- ❌ Trading BOS immediately without confirmation (fakeouts common)
- ❌ Using old structure (must update structure as new highs/lows form)

## 3. TREND SPEED ANALYZER

### Definition
Momentum acceleration/deceleration via wave ratio analysis. Answers: "Is momentum SPEEDING UP or SLOWING DOWN?"

### Components
- **Direction:** BULLISH / BEARISH / FLAT
- **Regime:** EXPANSION (accelerating) / NORMAL (steady) / CONSOLIDATION (recovering) / EXHAUSTION (decelerating)
- **Wave Ratio:** Current wave size / Prior wave size
- **Price vs EMA:** Price position relative to exponential moving average

### Interpretation Rules
```
EXPANSION Regime:
  Wave Ratio > 1.2 (accelerating):
    Score: +10
    Meaning: STRONGEST momentum signal
    Momentum SPEEDING UP
    Best entry environment
    
NORMAL Regime:
  Wave Ratio 0.8-1.2 (steady):
    Score: +5
    Meaning: Good momentum but not accelerating
    Typical trend continuation
    
CONSOLIDATION Regime:
  Wave Ratio declining but direction still valid:
    Score: +2
    Meaning: Momentum weakening temporarily
    Pullback forming; recovery coming (if structure holds)
    
EXHAUSTION Regime:
  Wave Ratio < 0.4 (decelerating rapidly):
    Score: -5
    Meaning: Momentum FAILING
    Reversal risk very high
    Exit immediately
```

### Price vs EMA
```
Price ABOVE EMA (for LONG):
  Score: 0 (neutral modifier)
  Meaning: Price above moving average trend
  
Price CROSSING EMA:
  Score: -3
  Meaning: Trend about to change
  Warning signal
  
Price BELOW EMA (for LONG):
  Score: -3 (penalty, not applicable for long)
  Meaning: Wrong side of trend
```

### Why This Matters
```
You can have BULLISH structure but EXHAUSTION momentum.
In that case: Structure is valid BUT momentum is FADING.
Action: Wait for consolidation to complete before entering.

You can have BULLISH structure + EXPANSION momentum.
In that case: BEST entry environment (structure + momentum aligned)
Action: Enter with HIGH confidence
```

### Common Mistakes
- ❌ Entering on exhaustion (thinking momentum will return)
- ❌ Ignoring consolidation (thinking downtrend is coming)
- ❌ Using outdated wave ratios (must recalculate as new waves form)

## 4. MACD (Momentum Indicator)

### Definition
Moving Average Convergence/Divergence. Shows momentum magnitude and crossing signals.

### Components
- **Histogram:** Difference between MACD line and Signal line
- **Histogram State:** BULLISH_ACCELERATING / BULLISH_DECELERATING / BEARISH_RECOVERING / BEARISH_ACCELERATING
- **Zero Line Position:** ABOVE (bullish environment) / BELOW (bearish environment)
- **Signal Cross:** MACD line crossing Signal line (momentum shift)

### Interpretation Rules
```
BULLISH_ACCELERATING:
  Histogram rising + Zero above:
    Score: +10
    Meaning: STRONGEST bullish momentum
    Momentum expanding upward
    
BULLISH_ACCELERATING:
  Histogram rising + Zero below:
    Score: +6
    Meaning: Bullish but not yet fully confirmed
    Zero line about to cross above
    
BULLISH_DECELERATING:
  Histogram falling but still above zero:
    Score: +3
    Meaning: Bullish but weakening
    Reversal warning; exit if stops hit
    
BEARISH_ACCELERATING:
  Histogram falling + Zero below:
    Score: -7 (penalty for long)
    Meaning: STRONGEST bearish momentum
    Momentum expanding downward
```

### Signal Crossing
```
When MACD crosses Signal line:
  If aligned with trade bias: +3 score bonus
  If opposing trade bias: -3 score penalty
  Meaning: Crossing = momentum shift signal
```

### Why MACD Matters
```
MACD CONFIRMS or DENIES momentum.
- If structure says BULLISH but MACD says BEARISH_ACCELERATING → CONFLICT (NO TRADE)
- If structure says BULLISH and MACD says BULLISH_ACCELERATING → ALIGNED (STRONG TRADE)
```

### Common Mistakes
- ❌ Trading MACD divergences without structure confirmation
- ❌ Ignoring histogram state (just looking at line positions)
- ❌ Using MACD as standalone predictor (must confirm with structure)

## 5. CVD IQ (Order Flow Indicator)

### Definition
Cumulative Delta Volume = Real order flow. Shows if REAL MONEY is participating in the move.

### Components
- **CVD Direction:** BUYING / SELLING / NEUTRAL
- **Divergence:** Large / Medium / Small (price extends while CVD diverges)
- **Absorption:** BUY_ABSORPTION / SELL_ABSORPTION (smart money absorbing weakness)
- **Cost:** Cost of pulling price in direction (VERY_HIGH to VERY_LOW)

### Interpretation Rules
```
CVD BUYING (for LONG):
  Score: +10
  Meaning: Real buy pressure on the move
  Confirms REAL participation (not just price printing)
  
CVD SELLING (for SHORT):
  Score: +10
  Meaning: Real sell pressure on the move
  
LARGE DIVERGENCE (opposing trade):
  Score: -15 (CRITICAL WARNING)
  Meaning: Price extending while CVD stalling/reversing
  Reversal risk EXTREMELY HIGH
  Example: Price making new high while CVD rolling over
  
MEDIUM DIVERGENCE:
  Score: -10
  Meaning: Price extending but CVD weak
  
SMALL DIVERGENCE:
  Score: -5
  Meaning: Minor misalignment; caution advised
  
BUY_ABSORPTION:
  Score: +5
  Meaning: Smart money (institutions) absorbing selling
  Hidden support forming
  Bullish despite current price weakness
  
COST (High):
  Score: +5 (if aligned with direction)
  Meaning: Effort required to extend move; real commitment
```

### Why CVD Matters
```
CVD = "Are real players participating or is this a fake move?"

Without CVD confirmation:
- Price can extend on low volume (fakeout risk)
- No confirmation that anyone cares about this move

With CVD confirmation:
- Real money backing the move
- Pullbacks are likely reversals (buyers absorbing)
- Move has legs to continue
```

### Divergence Examples
```
BULLISH Trade + Large Divergence:
  Price: New high
  CVD: Rolling over to new low
  Interpretation: Price being pushed UP by few players; REAL money EXITING
  Action: REDUCE SIZE, TIGHTEN STOPS, CONSIDER NO TRADE
  
BULLISH Trade + Buy Absorption:
  Price: Testing support (weakness)
  CVD: Sharp uptick (buyers stepping in)
  Interpretation: Smart money absorbing selling; reversal likely
  Action: STRONG ENTRY SIGNAL despite price weakness
```

### Common Mistakes
- ❌ Ignoring CVD divergence (treating it as minor warning)
- ❌ Overweighting divergence if other signals strong (divergence = critical)
- ❌ Not understanding absorption (thinking weakness = sell signal)

## 6. ATR (Average True Range)

### Definition
Volatility measure. Shows environment fitness for DIRECTIONAL trades. NOT a directional indicator.

### Components
- **Volatility State:** LOW / NORMAL / HIGH
- **Compression:** ATR contracting (setup forming)
- **Expansion:** ATR expanding (momentum environment forming)
- **Percentile Rank:** ATR position relative to last 100 bars (0–100)

### Interpretation Rules (Critical: ATR FILTERS ONLY)
```
HIGH Volatility + EXPANSION (just expanded):
  Score: 0 (no penalty)
  Environment: VALID BREAKOUT environment
  Trading: FAVOR breakout trades
  Meaning: Volatility expanding from compression
  
HIGH Volatility + NO EXPANSION (sudden spike):
  Score: -3
  Environment: Late-move spike; fakeout risk
  Trading: AVOID new entries; CONSIDER EXITS
  Meaning: Volatility spiked suddenly (exhaustion signal)
  
NORMAL Volatility:
  Score: 0
  Environment: Standard; proceed normally
  
LOW Volatility + COMPRESSION (ATR contracting):
  Score: 0 (no penalty)
  Environment: SETUP FORMING
  Trading: Prepare for breakout; DON'T force entry
  Meaning: Volatility contracting before expansion (accumulation)
  
LOW Volatility + NO COMPRESSION (flat market):
  Score: -5
  Environment: Dead market; low reward
  Trading: AVOID BREAKOUT trades; FAVOR mean reversion
  Meaning: Flat market with no setup forming
  
Extreme High (>90th percentile):
  Score: -5 additional
  Environment: Exhaustion likely
  Trading: Reduce position size
  
Extreme Low (<10th percentile):
  Score: -5 additional
  Environment: No directional potential
  Trading: Avoid all directional trades
```

### Why ATR is "Filter Only"
```
HIGH ATR does NOT mean "buy more aggressively"
LOW ATR does NOT mean "sell all positions"

ATR is ENVIRONMENT check, not DIRECTION check.

It answers: "Is this environment suitable for directional trades?"
- YES: Normal to High ATR in expansion = good environment
- NO: Dead low ATR = bad environment
- MIXED: High ATR but no expansion = late-move risk (bad)
```

### Common Mistakes
- ❌ Using ATR as directional signal (HIGH ATR = buy)
- ❌ Trading low ATR thinking "mean reversion is coming"
- ❌ Ignoring compression/expansion flags (context matters)

## Indicator Summary Matrix

| Indicator | Measures | Priority | Range | Use Case |
|-----------|----------|----------|-------|----------|
| PAVP | Price acceptance zones | HIGHEST | -15 to +10 | WHERE is price (equilibrium) |
| ZigZag | Market structure | HIGHEST | -25 to +25 | TREND VALIDITY (HH/HL vs LH/LL) |
| Trend Speed | Momentum acceleration | HIGH | -8 to +10 | ACCELERATION (speeding up/down) |
| MACD | Momentum magnitude | HIGH | -7 to +10 | MAGNITUDE (strength of momentum) |
| CVD | Order flow participation | MEDIUM-HIGH | -20 to +15 | CONFIRMATION (real money backing) |
| ATR | Volatility environment | MEDIUM | -10 to 0 | FILTER (is environment suitable) |

## Indicator Conflict Resolution

If two indicators strongly oppose each other:
1. **Structure vs Location:** Trust structure (highest priority)
2. **Momentum vs Order Flow:** Trust order flow (CVD = real money)
3. **Any vs ATR:** Remember ATR filters only (not directional)
4. **More than 2 conflicts:** Bias toward NO TRADE

## Regime-Specific Interpretations

### TRENDING Regime
- Trend Speed + MACD dominate
- PAVP less important (acceptance not critical in trend)
- CVD confirmation valuable but not critical
- Structure must align (non-negotiable)

### RANGING Regime
- PAVP dominates (VA edges = trade levels)
- Trend Speed unreliable (mean reversion plays)
- CVD important (absorption signals reversal)
- Structure neutral (range = no structure)

### BREAKOUT Regime
- ATR expansion critical (must be present)
- CVD must confirm (breakout on low CVD = fake)
- PAVP relevant (VA acceptance = confirmation)
- Trend Speed = acceleration check (must accelerate)

### REVERSAL Regime
- CVD divergence = king indicator
- Trend Speed exhaustion = critical signal
- Structure weakening = entry signal (after confirmation)
- PAVP = reversal zone marker (extremes often reverse)
```

**How It Improves Token Efficiency:**
- Single source of truth for "what does this indicator mean?"
- Prevents "what does CVD divergence really mean?" searches (saves 300 tokens)
- Examples prevent misinterpretation

**How It Improves Architectural Consistency:**
- All AI agents interpret indicators identically
- Prevents "I thought MACD X meant Y" inconsistencies
- Clear conflict resolution rules

**How It Reduces AI Drift:**
- AI can't invent indicator meanings (they're defined here)
- Examples prevent indicator misuse
- Regime-specific rules prevent one-size-fits-all interpretations

---

#### **File 6: REGIME_MODEL.md (Market State Classification)**

**Purpose:**  
Complete guide to regime classification. Explains how to recognize each regime and what it means for trading.

**What Goes Inside:**
```markdown
# REGIME_MODEL.md: Market State Classification

## Overview
A regime is the CURRENT MARKET CONTEXT. All trading rules are contextualized by regime.

6 Regimes:
1. TRENDING_UP
2. TRENDING_DOWN
3. RANGING
4. BREAKOUT
5. REVERSAL
6. UNCERTAIN

## Regime 1: TRENDING_UP

### Definition
Market making higher highs + higher lows with consistent upward structure.

### Identification Signals
```
Count these signals:
✓ ZigZag.structure = BULLISH
✓ PAVP.value_area_position = ABOVE_VA
✓ Trend_Speed.direction = BULLISH + regime = EXPANSION
✓ MACD.histogram_state = BULLISH_ACCELERATING
✓ CVD.cvd_direction = BUYING

If ≥3 signals → TRENDING_UP confirmed
Confidence = signal_count × 20 (max 100)
```

### What It Means For Trading
```
- Entries: LONG bias (structure defines uptrend)
- Exits: Only at major support levels
- Invalidation: Structure break (BOS_DOWN = exit all)
- Risk: Trend exhaustion (watch Trend Speed for decelerating)
```

### Scoring Adjustment
```
In TRENDING_UP:
- Increase Structure weight
- Increase Trend Speed weight
- Decrease PAVP importance (acceptance not critical in established trend)
- Keep CVD weight normal
```

### Common Pitfalls
- ❌ Shorting in established uptrend (structure says LONG)
- ❌ Overtrading (temptation to add on every pullback)
- ❌ Ignoring structure weakening (Trend Speed exhaustion without BOS = exit signal)

## Regime 2: TRENDING_DOWN

### Definition
Market making lower highs + lower lows with consistent downward structure.

### Identification Signals
(Mirror of TRENDING_UP, all bearish)

### What It Means For Trading
```
- Entries: SHORT bias (structure defines downtrend)
- Exits: Only at major resistance levels
- Invalidation: Structure break (BOS_UP = exit all)
- Risk: Trend exhaustion
```

## Regime 3: RANGING

### Definition
Price oscillating inside Value Area without clear structure directional bias.

### Identification Signals
```
Count these signals:
✓ PAVP.value_area_position = INSIDE_VA
✓ ZigZag.structure = NEUTRAL (alternating HH/LH or HL/LL)
✓ Trend_Speed.regime = CONSOLIDATION
✓ MACD.histogram_state = BULLISH_DECELERATING or BEARISH_RECOVERING
✓ CVD neutral or divergent

If ≥2 signals → RANGING confirmed
Confidence = signal_count × 20
```

### What It Means For Trading
```
- Entries: AVOID directional trades (chop trap)
- Exits: Close existing trending trades (structure weakening)
- Alternative: VA edge mean reversion (buy VAL, sell VAH)
- Risk: Breakout whipsaws (ranges end with strong moves)
```

### Scoring Adjustment
```
In RANGING:
- Increase PAVP weight (VA edges = trade levels)
- Decrease Trend Speed weight (false signals common)
- Decrease MACD weight (crossovers too frequent)
- Increase CVD weight (absorption signals reversals)
```

### Common Pitfalls
- ❌ Trading inside VA on momentum (sure way to get chopped)
- ❌ Ignoring range boundaries (VA edges = hard limits)
- ❌ Staying in range trades too long (ranges have expiration)

## Regime 4: BREAKOUT

### Definition
Market escaping equilibrium with momentum + volume confirmation.

### Identification Signals
```
Count these signals:
✓ ATR.expansion = true (volatility just increased)
✓ Price breaking VAH or VAL with acceptance (>0.5% move outside)
✓ Trend_Speed sharply increasing (ratio > 1.2)
✓ CVD.cvd_direction confirms direction
✓ Volume expansion (PAVP acceptance shift)

If ≥3 signals → BREAKOUT confirmed (rare!)
Confidence = signal_count × 20
```

### What It Means For Trading
```
- Entries: BREAKOUT trades in direction of break (VAH break = LONG, VAL break = SHORT)
- Structure confirmation: MANDATORY (ZigZag must show new trend direction)
- Risk: Fake breakouts (low CVD volume = fakeout)
- Follow-up: Trend establishment (breakout becomes trend 1–2 candles later)
```

### Scoring Adjustment
```
In BREAKOUT:
- Increase ATR expansion weight (must be present)
- Increase CVD weight (must confirm volume)
- Decrease PAVP weight (VA boundaries no longer relevant)
- Keep Structure weight high (must confirm new direction)
```

### Common Pitfalls
- ❌ Trading breakout without CVD confirmation (fakeouts common)
- ❌ Ignoring ATR compression (if no compression pre-breakout, not a setup)
- ❌ Trading breakout immediately (wait for structure confirmation)

## Regime 5: REVERSAL

### Definition
Market reaching exhaustion and showing early signs of direction change.

### Identification Signals
```
Count these signals:
✓ Price extreme away from POC (>2% extended)
✓ CVD.divergence = large or medium (opposing trend)
✓ Trend_Speed.regime = EXHAUSTION (wave ratio < 0.4)
✓ MACD weakening (BULLISH_DECELERATING or BEARISH_RECOVERING)
✓ ZigZag showing potential BOS setup
✓ ATR extreme high (>90th percentile)

If ≥2 signals → REVERSAL risk recognized
Confidence = signal_count × 20
```

### What It Means For Trading
```
- Entries: DO NOT trade reversals at extremes (wait for structure confirmation)
- Structure confirmation: REQUIRED (wait for BOS or new structure)
- Risk: Continuation (fake reversal signals common)
- Opportunity: Position for reversal (don't enter until structure confirms)
```

### Scoring Adjustment
```
In REVERSAL:
- Increase CVD weight (divergence = king)
- Increase Trend Speed weight (exhaustion detection critical)
- Decrease momentum weight (MACD unreliable in reversals)
- Increase Structure weight (new structure must confirm reversal)
```

### Common Pitfalls
- ❌ Trading reversal at extreme (likely to get run over)
- ❌ Ignoring CVD divergence (most important reversal signal)
- ❌ Entering before structure confirms (fake reversals trap traders)

## Regime 6: UNCERTAIN

### Definition
Indicators contradict each other; market state undefined.

### Identification Signals
```
Count signals for each regime:
If no regime has ≥3 signals → UNCERTAIN
If signals count 2/5 for MULTIPLE regimes → UNCERTAIN
If ZigZag structure opposes other indicators → UNCERTAIN
```

### What It Means For Trading
```
- Entries: AVOID (or require score ≥80 to trade)
- Risk: HIGHEST (ambiguous market = highest whipsaw risk)
- Action: Wait for clarity (regime transition usually resolves within 1–3 candles)
- Confidence penalty: -10 applied to base score
```

### Scoring Adjustment
```
In UNCERTAIN:
- Apply -10 penalty to base score
- Require final score ≥80 for ANY trade signal
- Reduce all weights (no structure guides you)
- Bias toward NO TRADE (default in ambiguity)
```

### When to Exit UNCERTAIN
- Wait for 3+ signals to align on one regime
- Exit all positions if structure breaks (becomes TRENDING opposite direction)
- Avoid new entries until regime clarifies

## Regime Transitions

### How Regimes Change
```
NOT: Single indicator changes
REQUIRES: At least 2 independent indicators changing

Example valid transition:
  TRENDING_UP → RANGING
  When: Structure becomes NEUTRAL (BOS not yet) AND Trend Speed falls to CONSOLIDATION
  
Example valid transition:
  RANGING → BREAKOUT
  When: ATR expansion occurs AND price breaks VA boundary with CVD confirmation
```

### Transition Signals
```
ZigZag BOS = automatic regime review (major transition signal)
PAVP boundary shift = regime transition possible (acceptance changing)
Trend Speed regime change = regime transition confirmation signal
CVD divergence spike = possible reversal/transition incoming
```

## Scoring Per Regime

### TRENDING_UP Scoring
```
Base: 50
+ Structure (if BULLISH): +15 to +25
+ Location (ABOVE_VA): +5 to +10
+ Momentum (expansion): +10 to +15
+ Order Flow (buying): +5 to +10
- Volatility filter: 0 to -5
= Typical A-grade: 75–90
```

### RANGING Scoring
```
Base: 50
+ Structure: +5 (NEUTRAL structure weak)
+ Location (VA edge): +7 to +10
+ Momentum: +2 to +5 (reduced importance)
+ Order Flow (absorption): +5 to +10
- Volatility: 0 to -3
= Typical C-grade: 55–65 (mean reversion plays)
```

### BREAKOUT Scoring
```
Base: 50
+ Structure (new direction confirmation): +10 to +25
+ Location (clear of VA): +5 to +10
+ Momentum (sharp): +15 to +20
+ Order Flow (volume): +5 to +10
- Volatility: 0 (expansion required, so no penalty)
= Typical A-grade: 80–95
```

### REVERSAL Scoring
```
Base: 50
+ Structure (new structure forming): +10 to +25
+ Location: +5 (less important)
+ Momentum: +2 to +5 (weak in reversals)
+ Order Flow (divergence absorbed): +5 to +10
+ CVD penalty if divergence: -10 to -20
= Typical C-grade: 45–60 (wait for confirmation before trade)
```

## Summary: Regime Decision Tree

```
START: Market state unknown

STEP 1: Count BULLISH signals (5 total)
  IF count ≥3 → potential TRENDING_UP

STEP 2: Count BEARISH signals
  IF count ≥3 → potential TRENDING_DOWN

STEP 3: Count RANGING signals
  IF count ≥2 → potential RANGING

STEP 4: Check for BREAKOUT signals
  IF ATR expansion + CVD confirmation + volume shift → BREAKOUT

STEP 5: Check for REVERSAL signals
  IF CVD divergence large + Trend Speed exhaustion → REVERSAL risk

STEP 6: Finalize
  IF one regime clear winner → apply that regime
  IF multiple signals contradict → UNCERTAIN (apply -10 penalty)
  IF signals scattered → UNCERTAIN (default: NO TRADE)

CONFIDENCE = signal_count × 20 (max 100)
```

## Regime Stability Indicator

```
Stable Regime:
- Clear winner (4–5 signals aligned)
- Structure holds (no BOS risk)
- Confidence ≥80

Transitioning Regime:
- 2–3 signals aligned
- Structure weakening or changing
- Confidence 50–79

Uncertain Regime:
- Signals conflicting
- Structure ambiguous
- Confidence <50, apply -10 penalty
```
```

**How It Improves Token Efficiency:**
- Single source for "what regime are we in?" (saves 200 tokens/query)
- Decision tree prevents AI from re-deriving regime classification
- Clear regime transitions prevent ambiguous state discussions

**How It Improves Architectural Consistency:**
- All AI agents classify regimes identically
- Prevents "wait, what regime is this?" confusion
- Clear scoring adjustments per regime

**How It Reduces AI Drift:**
- Regime classification rules are explicit (not subjective)
- Regime transitions defined rigorously (prevents "drift" into new regimes)
- Transition rules prevent flip-flopping between regimes

---

#### **File 7: SYSTEM_FLOW.md (Data Pipeline)**

**Purpose:**  
Complete specification of data flow from input to output. Shows how signal moves through system, what transformations occur at each step.

**What Goes Inside:**
```markdown
# SYSTEM_FLOW.md: Data Pipeline Specification

## High-Level Flow

```
INPUT: Market Signal (JSON)
  ↓
VALIDATION: Check schema
  ↓
REGIME DETECTION: Classify market context
  ↓
SCORING PIPELINE: 5-layer scoring
  ├─ Structure layer
  ├─ Location layer
  ├─ Momentum layer
  ├─ Order Flow layer
  └─ Volatility filter
  ↓
DECISION GENERATION: Convert score to trade decision
  ↓
OUTPUT: Trade decision (JSON)
```

## Step-by-Step Process

### STEP 1: INPUT VALIDATION

**Input Schema:**
```json
{
  "timestamp": "ISO-8601",
  "symbol": "string",
  "timeframe": "string",
  
  "price": {object with OHLC},
  "pivot_volume_profile": {object},
  "macd": {object},
  "trend_speed": {object},
  "zigzag_structure": {object},
  "cvd_iq": {object},
  "atr": {object}
}
```

**Validation Rules:**
```
- All required fields present (no missing keys)
- All numeric values reasonable (no NaN, no extreme outliers)
- All string enums valid (BULLISH/BEARISH/NEUTRAL, etc.)

If validation fails:
  → Return NO TRADE (confidence < 5)
  → Log error with signal ID
```

### STEP 2: REGIME DETECTION

**Input:** Full indicator snapshot

**Process:**
1. Count signals for each regime type
2. Determine regime with highest signal count
3. Calculate confidence (count × 20)
4. Apply regime-specific adjustments to scoring

**Output:**
```json
{
  "regime": "TRENDING_UP | TRENDING_DOWN | RANGING | BREAKOUT | REVERSAL | UNCERTAIN",
  "confidence": 0-100,
  "status": "Stable | Transitioning | Uncertain",
  "signal_breakdown": {
    "trending_up_count": int,
    "trending_down_count": int,
    "ranging_count": int,
    "breakout_count": int,
    "reversal_count": int
  }
}
```

### STEP 3: STRUCTURE LAYER SCORING

**Input:** ZigZag + PAVP indicators + trade direction bias

**Process:**
1. Identify ZigZag structure
2. Check PAVP location
3. Check for BOS signal
4. Apply scoring rules from TRADING_RULES.json

**Output:** (score_delta: -25 to +25, reasons: list[str])

### STEP 4: LOCATION LAYER SCORING

**Input:** PAVP indicator + trade direction

**Process:**
1. Determine PAVP position (ABOVE_VA / BELOW_VA / INSIDE_VA)
2. Calculate distance from POC
3. Calculate distance from VA boundary
4. Apply scoring rules

**Output:** (score_delta: -15 to +10, reasons: list[str])

### STEP 5: MOMENTUM LAYER SCORING

**Input:** Trend Speed + MACD + trade direction

**Process:**
1. Score Trend Speed component (regime + wave ratio)
2. Score MACD component (histogram state + zero position)
3. Check for signal crosses
4. Apply scoring rules

**Output:** (score_delta: -15 to +20, reasons: list[str])

### STEP 6: ORDER FLOW LAYER SCORING

**Input:** CVD indicator + trade direction

**Process:**
1. Score CVD direction component
2. Score divergence penalty (if present)
3. Score absorption (if present)
4. Score cost state
5. Apply scoring rules

**Output:** (score_delta: -20 to +15, reasons: list[str])

### STEP 7: VOLATILITY FILTER

**Input:** ATR indicator

**Process:**
1. Determine volatility state (LOW / NORMAL / HIGH)
2. Check compression/expansion flags
3. Check percentile rank (extreme detection)
4. Apply filter rules (NO POSITIVE CONTRIBUTION, filter only)

**Output:** (score_delta: -10 to 0, reasons: list[str])

### STEP 8: SCORE AGGREGATION

**Input:** All layer deltas

**Process:**
1. Start with base score: 50
2. Add all layer deltas
3. Clamp to [0, 100]
4. Apply regime penalty if uncertain (-10)

```
Final Score = Clamp(50 + structure + location + momentum + order_flow + volatility, 0, 100)

If regime == UNCERTAIN:
  Final Score = Clamp(Final Score - 10, 0, 100)
```

**Output:**
```json
{
  "base_score": 50,
  "layer_deltas": {
    "structure": float,
    "location": float,
    "momentum": float,
    "order_flow": float,
    "volatility": float
  },
  "regime_adjustment": 0 or -10,
  "final_score": float
}
```

### STEP 9: DECISION MAPPING

**Input:** Final score + regime + conflicts

**Process:**
1. Map score to grade (A/B/C/NONE)
2. Determine direction bias from regime
3. Set confidence from score
4. Determine risk state

```
if final_score ≥75: grade = 'A'
elif final_score ≥60: grade = 'B'
elif final_score ≥50: grade = 'C'
else: grade = 'NONE'

direction = regime.primary_direction or 'NO_TRADE'

risk_state:
  if grade == 'A' and no_conflicts: risk = 'LOW'
  elif grade == 'B' or any_conflict: risk = 'MEDIUM'
  else: risk = 'HIGH'
```

### STEP 10: CONFLICT RESOLUTION

**Input:** Layer scores + regime

**Process:**
1. Identify opposing signals (score deltas >5 points apart)
2. Count conflicts
3. Apply NO TRADE bias if conflicts exist

```
if count(conflicts) ≥2:
  direction = 'NO TRADE'
  confidence -= 20
```

### STEP 11: OUTPUT GENERATION

**Input:** All scores + conflicts + regime

**Process:**
1. Generate one-line reasoning per layer
2. List key conflicts
3. Define entry logic (specific entry trigger)
4. Define invalidation (specific exit trigger)

**Output:**
```json
{
  "bias": "LONG | SHORT | NO TRADE",
  "confidence": 0-100,
  "setup_grade": "A | B | C | NONE",
  "risk_state": "LOW | MEDIUM | HIGH",
  "regime": "...",
  "score_breakdown": {...},
  "reasoning": {...},
  "key_conflicts": [list],
  "entry_logic": "string",
  "invalidation": "string",
  "timestamp": "ISO-8601"
}
```

## Data Transformations

### Indicator → Signal Score Mapping

```
Indicator Raw Value → Interpreted Signal → Score Contribution

Example (MACD):
  Raw: histogram = 0.0050, zero_above = true, histogram_direction = RISING
  → Signal: BULLISH_ACCELERATING with zero above
  → Score: +10 (confidence 100)

Example (Trend Speed):
  Raw: wave_ratio_avg = 1.35, direction = BULLISH
  → Signal: BULLISH + EXPANSION + accelerating
  → Score: +10 (confidence 100)
```

### Multiple Signals → Layer Score Mapping

```
Structure Layer:
  Signal 1 (ZigZag): BULLISH → contributes +5
  Signal 2 (PAVP): ABOVE_VA → contributes +15
  Signal 3 (BOS): BOS_UP → contributes +5
  Combined: All three align → total +25

Location Layer:
  Signal 1 (PAVP): ABOVE_VA → contributes +7
  Signal 2 (POC): at POC → contributes -3
  Combined: +7 - 3 = +4
```

## Error Handling & Failsafes

### Incomplete Input
```
if any_field_missing:
  return {
    "bias": "NO TRADE",
    "confidence": 0,
    "setup_grade": "NONE",
    "reason": "Incomplete signal. Missing: [field]"
  }
```

### Extreme Outliers
```
if value_outside_reasonable_range:
  flag_as_suspicious
  apply_reduced_weight
  log_outlier_alert
```

### Conflicting Inputs
```
if structure_opposes_location:
  score_structure_higher (weight 0.25)
  reduce_location_weight (weight 0.30 → 0.20)
  output.key_conflicts.append("Structure vs Location opposition")
  
if momentum_opposes_order_flow:
  trust_order_flow (real money > indicators)
  reduce_momentum_weight
```

## Example: Full Pipeline Execution

### Input Signal
```json
{
  "timestamp": "2026-05-11T14:30:00Z",
  "symbol": "EURUSD",
  "timeframe": "1H",
  
  "price": {"open": 1.0900, "high": 1.0925, "low": 1.0895, "close": 1.0920},
  "pivot_volume_profile": {
    "poc": 1.0900,
    "vah": 1.0915,
    "val": 1.0885,
    "value_area_position": "ABOVE_VA"
  },
  "macd": {
    "histogram": 0.0035,
    "histogram_direction": "RISING",
    "zero_line_position": "ABOVE"
  },
  "trend_speed": {
    "direction": "BULLISH",
    "regime": "EXPANSION",
    "wave_analysis": {"current_ratio_avg": 1.28}
  },
  "zigzag_structure": {
    "structure": "BULLISH",
    "break_of_structure": "BOS_UP"
  },
  "cvd_iq": {
    "cvd_direction": "BUYING",
    "divergence": "NONE"
  },
  "atr": {
    "volatility_state": "NORMAL",
    "compression": false,
    "expansion": true
  }
}
```

### Processing Steps

1. **Validation:** ✓ All fields present, values reasonable
2. **Regime Detection:**
   - Trending Up signals: ZigZag(BULLISH), PAVP(ABOVE_VA), Trend Speed(BULLISH+EXPANSION), MACD(BULLISH_ACCEL), CVD(BUYING)
   - Count: 5/5 → TRENDING_UP (confidence 100, status Stable)

3. **Structure Layer:** BULLISH + ABOVE_VA + BOS_UP → +25

4. **Location Layer:** ABOVE_VA + >0.5% away → +7

5. **Momentum Layer:** EXPANSION(+10) + BULLISH_ACCEL(+10) → +20

6. **Order Flow Layer:** BUYING(+10) + NO_DIVERGENCE → +10

7. **Volatility Filter:** NORMAL + EXPANSION → 0

8. **Aggregation:**
   - Base: 50
   - Deltas: 25 + 7 + 20 + 10 + 0 = 62
   - Total: 50 + 62 = 112 → clamped to 100
   - Regime adjustment: 0 (stable)
   - Final: 100

9. **Decision Mapping:**
   - Grade: A (≥75)
   - Direction: LONG (trending up)
   - Confidence: 100
   - Risk: LOW (A grade, no conflicts, stable regime)

10. **Conflict Check:** None (all signals aligned)

11. **Output:**
```json
{
  "bias": "LONG",
  "confidence": 100,
  "setup_grade": "A",
  "risk_state": "LOW",
  "regime": "TRENDING_UP",
  "score_breakdown": {
    "structure": 25,
    "location": 7,
    "momentum": 20,
    "order_flow": 10,
    "volatility": 0,
    "total": 100
  },
  "reasoning": {
    "structure": "BULLISH structure confirmed with BOS_UP and price ABOVE_VA",
    "location": "Price accepted cleanly above Value Area High",
    "momentum": "EXPANSION regime with BULLISH_ACCELERATING MACD",
    "order_flow": "Strong buying pressure, no divergence",
    "volatility": "Normal ATR, expansion environment valid"
  },
  "key_conflicts": [],
  "entry_logic": "On next pullback to 1.0915 (VAH) with buying confirmation",
  "invalidation": "Closes below 1.0900 (prior swing low) or breaks structure",
  "timestamp": "2026-05-11T14:30:00Z"
}
```

## Performance Characteristics

**Processing Time:** <100ms per signal
**Token Usage:** ~300 tokens per decision (in optimized pipeline)
**Determinism:** Same input → identical output (100% consistency)
**Statelessness:** Each decision independent (no session memory)
```

**How It Improves Token Efficiency:**
- Shows AI the EXACT pipeline to follow (no searching needed)
- Example walkthrough prevents "how do I apply these rules?" confusion

**How It Improves Architectural Consistency:**
- All decisions processed identically (pipeline is canonical)
- Prevents "I'll do it a different way" deviations

**How It Reduces AI Drift:**
- AI knows exact order of operations (structure → location → momentum → order_flow → volatility)
- No room for "I'll weight this differently"
- Example execution prevents interpretation drift

---

#### **File 8: PROMPT_RULES.md (AI Interaction Standards)**

**Purpose:**  
Standards for how humans should prompt AI, and how AI should format responses. Ensures consistent human-AI interaction.

**What Goes Inside:**
```markdown
# PROMPT_RULES.md: AI Interaction Standards

## Standard Prompt Templates

### Template 1: Trade Decision Request
```
Given this market signal [paste JSON]:

1. Use REGIME_DEFINITIONS.json to determine current regime
2. Use TRADING_RULES.json to score 5 layers
3. Resolve conflicts if any
4. Output decision per output_schema.json

Do NOT:
- Predict price direction beyond this signal
- Hallucinate missing data
- Suggest new indicators
```

### Template 2: System Change Request
```
Before implementing change:

1. State current behavior (reference specific rule in TRADING_RULES.json)
2. State proposed behavior (with explicit rule change)
3. Justify change (reference PERFORMANCE_METRICS.md or KNOWN_ISSUES.md)
4. List affected decisions (what trades would change)
5. Test with 3+ market regimes

If any step cannot be completed → do not implement
```

### Template 3: Bug Report
```
Observed behavior: [describe what happened]
Expected behavior: [describe what should happen]
Signal input: [paste JSON]
Regime: [what regime was system in]
Current version: [of TRADING_RULES.json]

Add to KNOWN_ISSUES.md with [AI_ENCOUNTERED] tag
```

## Response Format Standards

### For Trade Decisions
```
Always return JSON matching output_schema.json:
{
  "bias": "LONG | SHORT | NO TRADE",
  "confidence": X,
  "setup_grade": "A | B | C | NONE",
  ...
}

Include reasoning for each layer (one sentence each)
List any conflicts explicitly
Define entry logic and invalidation with specific levels
```

### For Architecture Questions
```
1. Reference ARCHITECTURE.md principle
2. Explain current behavior
3. If suggesting change: document in KNOWN_ISSUES.md first
4. List trade-offs (what breaks if this changes)
5. Require human approval before implementation
```

### For Learning/Feedback Analysis
```
1. Classify errors using FEEDBACK_LEARNING.json (A–F)
2. Summarize error distribution (% of each type)
3. Suggest indicator weight adjustments (do NOT implement directly)
4. Identify regime-specific patterns
5. Document findings in PERFORMANCE_METRICS.md
```

## Anti-Patterns (NEVER DO)

- ❌ Ask "do you think this will go up?" (system doesn't predict, only classifies)
- ❌ "Ignore the rules this time" (rules are why system works)
- ❌ "Add a new indicator" (would require TRADING_RULES.json rewrite)
- ❌ "What does your gut tell you?" (system has no intuition, only rules)
- ❌ Paste trade outcome and ask "why did this lose?" without error classification
- ❌ "Can you find a better entry level?" (system only classifies current state)
- ❌ Modify rules mid-decision ("actually, ignore that CVD divergence")

## Consistency Checks (HUMAN MUST VERIFY)

Before accepting any AI-generated decision:
1. Is output JSON valid? (can parse it)
2. Does regime match indicators? (verify decision tree)
3. Does score match thresholds? (verify grade assignment)
4. Are conflicts listed if present? (verify conflict detection)
5. Is invalidation specific? (mentions actual price levels)

Before accepting any AI-generated rule change:
1. Is change documented in KNOWN_ISSUES.md?
2. Is change tested in 3+ market regimes?
3. Does change maintain determinism?
4. Does change align with "structure-first" principle?
5. Have affected trades been backtested?

## Token-Efficient Prompting

### BAD (Inefficient)
```
"Read system_prompt.txt, execution_engine.txt, and scoring_engine.txt.
Then analyze this signal: [JSON].
What's your decision?"

Problems:
- Forces AI to load 3 redundant files
- AI re-derives rules from scratch
- 3,000+ tokens wasted
```

### GOOD (Efficient)
```
"Using TRADING_RULES.json v2.0 and signal [JSON]:
Score and output decision.

Do not explain rules; reference them by name if needed."

Benefits:
- AI loads one source of truth
- Output is concise
- ~300 tokens
```

## Versioning in Prompts

Always specify version:
```
"Using TRADING_RULES.json v2.0..."
"Using INDICATOR_GLOSSARY.json v1.0..."
```

If version changes during session:
```
"Version update: switch to TRADING_RULES.json v2.1.
Re-evaluate last 5 signals with new rules."
```

## Long-Session Protocol

For sessions with 10+ signals:
```
SESSION INIT:
1. Load TRADING_RULES.json v2.0 [cache for session]
2. Load INDICATOR_GLOSSARY.json v1.0 [cache for session]
3. Load REGIME_DEFINITIONS.json v1.0 [cache for session]

Per signal: reference cached rules, do not reload

SESSION END:
1. Log all decisions to trade_memory_log.txt
2. Archive session summary
3. Confirm all 6 required files are latest version
```

## Ambiguity Resolution

When prompt is ambiguous:
```
AI response:
"Clarification needed:
- Is regime input or should I detect it? (assuming I'll detect)
- Should I include full reasoning or condensed? (assuming condensed)
- Proceeding with assumptions above. Correct me if needed."
```

## File Reference Standards

When referencing files in prompts/responses:
- Use exact file name: "See INDICATOR_GLOSSARY.json" (not "check indicator doc")
- Use section name: "See REGIME_MODEL.md: Regime 3: RANGING" (not "read the regime section")
- Use version: "TRADING_RULES.json v2.0" (not "current rules")
- Use line/section number if applicable: "SCORING_RULES.md, Layer 1" (not "the structure layer")

## Decision Confidence Calibration

When AI outputs confidence level:
```
Confidence should map to:
  90–100: All 5 signals aligned, stable regime, A-grade setup
  70–89: 4/5 signals aligned, stable regime, A-grade or B-grade
  50–69: 3/5 signals aligned, or B-grade with conflicts
  30–49: 2/5 signals aligned, or C-grade
  10–29: 1/5 signals aligned, mostly conflicted
  <10: NO TRADE

If AI outputs confidence 85 but only 3/5 signals aligned → question it
```

## Change Request Protocol

If requesting rule change:
```
HUMAN REQUEST:
"I want to increase CVD weight because it's been more accurate recently."

REQUIRED AI RESPONSE:
1. "Current CVD weight: 0.15 (from ADAPTIVE_WEIGHTING.txt)"
2. "Current win rate for CVD signals: [check PERFORMANCE_METRICS.md]"
3. "Proposed change: CVD weight 0.15 → 0.20 (reason: better recent performance)"
4. "Trade-off: Would reduce Structure weight 0.25 → 0.20 to maintain 1.0 total"
5. "Testing: Backtesting on last 50 trades shows [X% improvement]"
6. "Recommendation: Approve / Reject / Investigate further"

HUMAN APPROVAL REQUIRED before implementing
```

## Learning Loop Standards

When AI proposes learning adjustments:
```
AI OUTPUT:
"Error analysis (50 trades):
- Type A errors (structure): 8% of losses
- Type B errors (momentum): 24% of losses
- Type C errors (location): 12% of losses
- Type D errors (CVD): 40% of losses
- Type E errors (volatility): 8% of losses
- Type F errors (overconfidence): 8% of losses

Recommendation:
- Increase CVD weight (highest error source)
- Increase Location penalties in RANGING regime
- Review overconfidence calibration"

HUMAN REVIEW REQUIRED:
1. Verify error classifications (sample 5 errors for manual check)
2. Approve weight changes in ADAPTIVE_WEIGHTING.txt
3. Update TRADING_RULES.json if thresholds change
4. Test changes on new signals before deploying
```
```

**How It Improves Token Efficiency:**
- Standardized prompt formats prevent "how do I ask this?" confusion
- Templates ensure AI knows exactly what to do
- Response standards prevent verbose explanations

**How It Improves Architectural Consistency:**
- All AI interactions follow same protocol
- Prevents "I'll answer this differently" variations
- Human-AI interface is standardized

**How It Reduces AI Drift:**
- Anti-patterns explicitly listed (prevents common mistakes)
- Consistency checks ensure AI doesn't hallucinate
- Change protocol requires documentation (prevents sneaky rule changes)

---

## PART 3: RECOMMENDED STABLE CONTRACTS

These files are **immutable reference documents** (read-only in normal operation):

```
/docs/contracts/
├── TRADING_RULES.json (v2.0)        [Core decision logic]
├── INDICATOR_GLOSSARY.json (v1.0)   [Indicator definitions]
├── REGIME_DEFINITIONS.json (v1.0)   [Regime classification rules]
├── FEEDBACK_LEARNING.json (v1.0)    [Error classification]
├── API_INPUT_SCHEMA.json (v1.0)     [Request format]
├── API_OUTPUT_SCHEMA.json (v1.0)    [Response format]
└── SCORING_ENGINE_CONTRACT.json (v2.0)  [Function specifications]
```

**Immutability Rule:**
These files are updated ONLY through explicit human decision documented in KNOWN_ISSUES.md.

**Checksums:**
Each file includes SHA256 checksum. Changes require checksum recalculation and documentation.

---

## PART 4: RECOMMENDED ARCHITECTURAL SUMMARY FILES

These are **compressed abstracts** of larger documentation:

1. **ARCHITECTURE_SUMMARY.md** (1 page)
   - System purpose + high-level flow
   - 6 subsystems in 1 sentence each
   - Key constraints + principles
   - Threat model overview
   - Version + status

2. **SCORING_SUMMARY.md** (1 page)
   - 5 layers + their ranges
   - Grade thresholds
   - Conflict resolution rules
   - Regime adjustments (one sentence each)

3. **REGIME_SUMMARY.md** (1 page)
   - 6 regimes + identification signals
   - Decision tree (compressed)
   - Scoring adjustments per regime
   - Transition rules

---

## PART 5: RECOMMENDED AI NAVIGATION FILES

These guide AI behavior:

1. **CLAUDE.md** (this system's "constitution")
   - Core rules (non-negotiable)
   - File reading protocol (order matters)
   - Decision-making steps
   - Anti-patterns
   - Version tracking
   - Token efficiency rules

2. **AI_NAVIGATION.md** (quick reference)
   - "If you need to [find X], read [file Y]"
   - Decision trees for different questions
   - File dependency map
   - Checksum validation protocol

---

## PART 6: RECOMMENDED SCHEMA FILES

JSON schemas (machine-readable contracts):

```
/docs/contracts/
├── TRADING_RULES.json        [Scoring + decision rules]
├── INDICATOR_GLOSSARY.json   [Indicator specs]
├── REGIME_DEFINITIONS.json   [Regime classification]
├── FEEDBACK_LEARNING.json    [Error classes A–F]
├── API_INPUT_SCHEMA.json     [TradingView webhook format]
└── API_OUTPUT_SCHEMA.json    [Decision output format]
```

Each includes:
- version
- created date
- checksum (SHA256)
- JSON schema validation rules
- Examples

---

## PART 7: RECOMMENDED LONG-TERM DOCUMENTATION STRATEGY

### Phase 1: Foundation (Weeks 1–2)
Create:
- README.md
- ARCHITECTURE.md
- CLAUDE.md (AI constitution)
- All 8 core files (SCORING_RULES.md, INDICATOR_SCHEMA.md, etc.)

### Phase 2: Contracts (Weeks 3–4)
Convert all rules to JSON:
- TRADING_RULES.json
- INDICATOR_GLOSSARY.json
- REGIME_DEFINITIONS.json
- All 7 schema files

### Phase 3: Subsystems (Weeks 5–6)
Create subsystem READMEs:
- /subsystems/structure/README.md
- /subsystems/location/README.md
- /subsystems/momentum/README.md
- /subsystems/order_flow/README.md
- /subsystems/volatility/README.md

### Phase 4: Implementation (Week 7)
Create:
- KNOWN_ISSUES.md (issue registry)
- TESTING_STRATEGY.md (test cases)
- PERFORMANCE_METRICS.md (health checks)
- DEPLOYMENT_CHECKLIST.md (release process)

### Phase 5: Maintenance (Ongoing)
- Update KNOWN_ISSUES.md as issues discovered
- Update PERFORMANCE_METRICS.md weekly
- Update CLAUDE.md as AI interaction patterns emerge
- Version and archive old documentation

### Documentation Maintenance Schedule

**Weekly:**
- Update KNOWN_ISSUES.md (new issues)
- Update PERFORMANCE_METRICS.md (weekly performance summary)

**Monthly:**
- Review CLAUDE.md (update if new AI patterns discovered)
- Archive old sessions (move trade_memory_log.txt to /archive)
- Generate performance report

**Quarterly:**
- Major review of all /core documentation
- Consider version updates (v2.0 → v2.1)
- Consolidate learnings into ARCHITECTURE.md

**Annually:**
- Full system audit
- Major documentation revision
- Archive year's issues and lessons learned

---

## DOCUMENTATION QUALITY METRICS

### Completeness
- [ ] Every indicator has definition + interpretation rules
- [ ] Every regime has identification + scoring rules
- [ ] Every layer has examples
- [ ] Every decision has invalidation condition
- [ ] Every file has checksum + version

### Consistency
- [ ] No contradictions between files (cross-check system_flow against scoring_rules)
- [ ] All examples are internally consistent
- [ ] All versions align
- [ ] All checksums verified

### Clarity
- [ ] Every rule has 1+ examples
- [ ] Every technical term defined
- [ ] No circular references (document doesn't reference itself for definition)
- [ ] Glossary provided (INDICATOR_GLOSSARY.json is master reference)

### Token Efficiency
- [ ] AI knows exactly which files to read (CLAUDE.md protocol)
- [ ] No redundant files (6 TXT files consolidated to 2 JSON)
- [ ] Summaries provided (abstracts for quick reference)
- [ ] Contracts available (JSON schemas for parsing)

---

## CONCLUSION

This documentation architecture creates:

1. **Permanent Architectural Memory:** System design captured in machine-readable format
2. **AI-Assisted Development:** Files structured for efficient Claude Code interaction
3. **Consistency Enforcement:** Standards + contracts prevent drift
4. **Token Efficiency:** Consolidated files, summaries, and contracts reduce context overhead
5. **Long-Term Maintainability:** Clear versioning, checksums, and archival strategy

By implementing this system, you ensure that:
- New AI sessions have complete context immediately
- Rules don't drift due to repeated readings of inconsistent files
- System design is explicit and self-documenting
- Changes are traceable and reversible
- Architectural decisions are recorded and justified
