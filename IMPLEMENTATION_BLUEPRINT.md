# TRADING_AGENT_V2: IMPLEMENTATION BLUEPRINT
## Concrete Steps to Reduce Token Waste by 89%

---

## PHASE 1: CONSOLIDATE RULE FILES (Week 1)

### Task 1.1: Create TRADING_RULES.json

**Goal:** Replace system_prompt.txt + execution_engine.txt + 80% of scoring_engine.txt

**Template:**
```json
{
  "version": "2.0",
  "created": "2026-05-11",
  "checksum": "SHA256_TO_BE_CALCULATED",
  
  "system": {
    "name": "TRADING_AGENT_V2",
    "task": "Classify market state into LONG / SHORT / NO TRADE using structured indicator data",
    "output_format": "JSON with decision, confidence, score breakdown, invalidation"
  },

  "indicators": {
    "pivot_volume_profile": {
      "name": "PAVP",
      "priority": 1,
      "weight": 0.30,
      "description": "Market equilibrium zones (POC / VAH / VAL). Above VAH = bullish, below VAL = bearish, inside = chop.",
      "truth_layer": "location"
    },
    "zigzag_structure": {
      "name": "ZigZag",
      "priority": 2,
      "weight": 0.25,
      "description": "Higher highs + higher lows (bullish), lower highs + lower lows (bearish), or neutral. BOS = trend validity.",
      "truth_layer": "structure"
    },
    "trend_speed_analyzer": {
      "name": "Trend Speed",
      "priority": 3,
      "weight": 0.15,
      "description": "Momentum acceleration (expansion), normal, or exhaustion. Wave ratio analysis.",
      "truth_layer": "momentum"
    },
    "macd": {
      "name": "MACD",
      "priority": 4,
      "weight": 0.10,
      "description": "Histogram state (bullish_accelerating, decelerating, etc) + zero_line position. Confirms momentum.",
      "truth_layer": "momentum"
    },
    "cvd_iq": {
      "name": "CVD",
      "priority": 5,
      "weight": 0.15,
      "description": "Order flow pressure (buying, selling, neutral). Divergence = warning signal. Confirms real participation.",
      "truth_layer": "order_flow"
    },
    "atr": {
      "name": "ATR",
      "priority": 6,
      "weight": 0.05,
      "description": "Volatility filter ONLY (not directional). Expansion after compression = breakout ready. Spike = late entry risk.",
      "truth_layer": "volatility"
    }
  },

  "scoring": {
    "base_score": 50,
    "decision_thresholds": {
      "strong_trade_A": 75,
      "valid_trade_B": 60,
      "weak_trade_C": 50,
      "no_trade": "<50"
    },
    "layers": {
      "structure": {
        "range": [-25, 25],
        "rules": [
          {
            "condition": "ZigZag=target_direction + BOS=target_direction + PAVP=target_location",
            "score": 25,
            "example": "BULLISH + BOS_UP + ABOVE_VA → +25"
          },
          {
            "condition": "ZigZag=target_direction + PAVP=target_location (no BOS)",
            "score": 15,
            "example": "BULLISH + ABOVE_VA (no BOS) → +15"
          },
          {
            "condition": "ZigZag=target_direction only",
            "score": 5,
            "example": "BULLISH structure, PAVP neutral → +5"
          },
          {
            "condition": "ZigZag=NEUTRAL",
            "score": -15,
            "example": "No directional structure → -15"
          },
          {
            "condition": "ZigZag=opposite_direction vs PAVP=target_location",
            "score": -15,
            "example": "BEARISH ZigZag vs ABOVE_VA for LONG → -15 conflict"
          },
          {
            "condition": "ZigZag=opposite_direction + PAVP=opposite_location",
            "score": -25,
            "example": "BEARISH + BELOW_VA for LONG → -25 strong conflict"
          }
        ]
      },
      "location": {
        "range": [-15, 10],
        "rules": [
          {
            "condition": "PAVP position = target (ABOVE_VA for LONG, BELOW_VA for SHORT) + near VA boundary (<0.2%)",
            "score": 10
          },
          {
            "condition": "PAVP position = target + accepted outside VA (>0.5%)",
            "score": 7
          },
          {
            "condition": "PAVP position = target + just outside VA edge",
            "score": 5
          },
          {
            "condition": "PAVP position = INSIDE_VA",
            "score": -10
          },
          {
            "condition": "POC magnet risk (price <0.1% from POC)",
            "score": -3,
            "note": "POC acts as magnet, not breakout signal"
          }
        ]
      },
      "momentum": {
        "range": [-15, 20],
        "components": {
          "trend_speed": {
            "max": 10,
            "min": -8,
            "rules": [
              {
                "condition": "Direction aligned + regime EXPANSION",
                "score": 10
              },
              {
                "condition": "Direction aligned + regime NORMAL",
                "score": 5
              },
              {
                "condition": "Direction aligned + regime CONSOLIDATION",
                "score": 2
              },
              {
                "condition": "Direction aligned + regime EXHAUSTION",
                "score": -5
              },
              {
                "condition": "Wave ratio > 1.2 (accelerating)",
                "score": 3,
                "modifier": true
              },
              {
                "condition": "Wave ratio < 0.4 (exhaustion)",
                "score": -3,
                "modifier": true
              }
            ]
          },
          "macd": {
            "max": 10,
            "min": -7,
            "rules": [
              {
                "condition": "histogram_state BULLISH_ACCELERATING + zero_line ABOVE",
                "score": 10
              },
              {
                "condition": "histogram_state BULLISH_ACCELERATING + zero_line BELOW",
                "score": 6
              },
              {
                "condition": "histogram_state BEARISH_ACCELERATING + zero_line BELOW",
                "score": -7
              },
              {
                "condition": "Signal cross aligned with bias",
                "score": 3,
                "modifier": true
              }
            ]
          }
        }
      },
      "order_flow": {
        "range": [-20, 15],
        "rules": [
          {
            "condition": "CVD direction confirms trade direction",
            "score": 10
          },
          {
            "condition": "CVD shows absorption (hidden buyers/sellers)",
            "score": 5
          },
          {
            "condition": "CVD cost state HIGH/VERY_HIGH + direction aligned",
            "score": 5,
            "modifier": true
          },
          {
            "condition": "Large divergence opposing trade direction",
            "score": -15
          },
          {
            "condition": "Medium divergence opposing trade direction",
            "score": -10
          },
          {
            "condition": "Small divergence opposing trade direction",
            "score": -5
          }
        ]
      },
      "volatility": {
        "range": [-10, 0],
        "note": "NEVER directional. Applied as final modifier only.",
        "rules": [
          {
            "condition": "volatility_state HIGH + expansion TRUE",
            "score": 0,
            "note": "Valid breakout environment"
          },
          {
            "condition": "volatility_state HIGH + expansion FALSE",
            "score": -3,
            "note": "Spike = avoid late entry"
          },
          {
            "condition": "volatility_state LOW + compression TRUE",
            "score": 0,
            "note": "Setup forming"
          },
          {
            "condition": "volatility_state LOW + compression FALSE",
            "score": -5,
            "note": "Flat market = avoid breakouts"
          },
          {
            "condition": "ATR percentile > 90 (extreme high)",
            "score": -5,
            "modifier": true
          },
          {
            "condition": "ATR percentile < 10 (dead market)",
            "score": -5,
            "modifier": true
          }
        ]
      }
    }
  },

  "regime_classification": {
    "note": "Determine BEFORE scoring. See REGIME_DEFINITIONS.json for decision tree.",
    "regimes": [
      "TRENDING_UP",
      "TRENDING_DOWN",
      "RANGING",
      "BREAKOUT",
      "REVERSAL",
      "UNCERTAIN"
    ],
    "uncertainty_penalty": -10,
    "uncertainty_threshold": 80,
    "note_uncertainty": "If regime uncertain, require score ≥80 to generate any trade signal"
  },

  "execution_pipeline": {
    "step_1": "Read signal_format.json input",
    "step_2": "Determine regime via REGIME_DEFINITIONS.json",
    "step_3": "Score structure layer",
    "step_4": "Score location layer",
    "step_5": "Score momentum layer (Trend Speed + MACD)",
    "step_6": "Score order flow layer (CVD)",
    "step_7": "Apply volatility filter",
    "step_8": "Clamp final score [0, 100]",
    "step_9": "Return decision JSON"
  },

  "non_negotiable_rules": [
    "NEVER trade without structural confirmation",
    "POC acts as a magnet, not a breakout signal",
    "Inside Value Area = lower quality trades",
    "Divergence reduces confidence automatically",
    "ATR is never directional, only a filter",
    "If signals conflict → NO TRADE bias",
    "Structure conflicts momentum → NO TRADE bias"
  ]
}
```

**Size:** ~3,500 tokens (vs. 900 for system_prompt.txt alone, but replaces 3 files totaling 1,400 tokens)  
**Net saving:** 1,400 tokens per read

**Action:** Copy, validate JSON, test with sample signals.

---

### Task 1.2: Create INDICATOR_GLOSSARY.json

**Goal:** Single source of truth for what each indicator means and how to interpret it

```json
{
  "version": "1.0",
  "created": "2026-05-11",

  "indicators": {
    "pivot_volume_profile": {
      "alias": "PAVP",
      "measurement": "Price acceptance zones based on historical volume",
      "components": {
        "poc": "Point of Control — price at which most volume traded. Acts as magnet/equilibrium.",
        "vah": "Value Area High — upper bound of 70% of volume distribution",
        "val": "Value Area Low — lower bound of 70% of volume distribution",
        "value_area": "Zone between VAL and VAH. Chop zone; quality trades rare here."
      },
      "interpretation_by_regime": {
        "trending_up": {
          "bullish_signals": [
            "Price above VAH (strong acceptance above prior equilibrium)",
            "VAH shifting upward (equilibrium rising)",
            "Price using POC as launching pad"
          ],
          "bearish_signals": [
            "Price below prior VAL (acceptance removed)",
            "Rejection at VAH (failed breakout)"
          ]
        },
        "trending_down": {
          "bearish_signals": [
            "Price below VAL (strong acceptance below prior equilibrium)",
            "VAL shifting downward (equilibrium falling)",
            "Price using POC as launching pad downward"
          ],
          "bullish_signals": [
            "Price above prior VAH (acceptance recovered)",
            "Support at VAL (bouncing off support)"
          ]
        },
        "ranging": {
          "neutral_signals": [
            "Price oscillating inside Value Area",
            "POC acting as midpoint magnet",
            "No acceptance drift (VAH/VAL stationary)"
          ]
        }
      },
      "trade_quality": {
        "highest": "Trades taken at VAH/VAL boundaries with direction confirmation",
        "good": "Trades outside VA edge (>0.5% from boundary)",
        "weak": "Trades inside VA or very close to POC",
        "lowest": "POC grazing trades (price <0.1% from POC)"
      }
    },

    "zigzag_structure": {
      "alias": "ZigZag",
      "measurement": "Market structure via higher highs/lower lows analysis",
      "components": {
        "higher_highs_higher_lows": "BULLISH structure — trend integrity intact",
        "lower_highs_lower_lows": "BEARISH structure — downtrend integrity intact",
        "neutral": "Alternating or choppy structure — no clear direction",
        "bos": "Break of Structure — invalidates current structure, signals reversal risk"
      },
      "interpretation": {
        "bullish": {
          "long_setup": true,
          "conditions": "Price making HH/HL sequence. Each new high exceeds prior. Each pullback holds above prior low.",
          "invalidation": "Price breaks below recent swing low (BOS_DOWN signal)"
        },
        "bearish": {
          "short_setup": true,
          "conditions": "Price making LH/LL sequence. Each new low exceeds prior. Each bounce fails below prior high.",
          "invalidation": "Price breaks above recent swing high (BOS_UP signal)"
        },
        "neutral": {
          "trade_bias": "NO TRADE",
          "reason": "No directional structure; avoid entries"
        }
      },
      "priority": "HIGHEST — defines trend validity and invalidation",
      "note": "Never trade against current structure"
    },

    "trend_speed_analyzer": {
      "alias": "Trend Speed",
      "measurement": "Momentum acceleration/deceleration via wave ratio analysis",
      "components": {
        "direction": "BULLISH | BEARISH | FLAT",
        "regime": "EXPANSION | NORMAL | CONSOLIDATION | EXHAUSTION",
        "wave_analysis": {
          "current_ratio_avg": "Average of current wave divided by prior wave",
          "current_ratio_max": "Peak acceleration within current wave",
          "interpretation": {
            "ratio_gt_1_2": "ACCELERATING — momentum expanding, strong trend continuation",
            "ratio_0_8_to_1_2": "NORMAL — steady momentum, typical trend",
            "ratio_lt_0_4": "EXHAUSTION — momentum fading, reversal risk"
          }
        },
        "price_vs_ema": "Price position relative to exponential moving average"
      },
      "interpretation_by_direction": {
        "bullish": {
          "expansion": "+10 score — trend accelerating, strongest signal",
          "normal": "+5 score — steady uptrend continuation",
          "consolidation": "+2 score — weak signal, recovery forming",
          "exhaustion": "-5 score — reversal warning"
        },
        "bearish": {
          "mirror": "Same as bullish but for downtrends"
        }
      },
      "trade_quality": {
        "best": "Expansion regime with ratio > 1.2 (accelerating)",
        "good": "Normal regime with ratio 0.8–1.2",
        "weak": "Consolidation with reversal risk brewing"
      }
    },

    "macd": {
      "alias": "MACD",
      "measurement": "Momentum via exponential moving average convergence/divergence",
      "components": {
        "histogram": "Difference between MACD line and Signal line",
        "histogram_state": "BULLISH_ACCELERATING | BULLISH_DECELERATING | BEARISH_RECOVERING | BEARISH_ACCELERATING",
        "zero_line_position": "ABOVE (bullish) | BELOW (bearish)",
        "signal_cross": "MACD line crossing Signal line (directional change hint)"
      },
      "interpretation": {
        "bullish_accelerating": {
          "zero_above": "+10 score — strongest bullish signal",
          "zero_below": "+6 score — bullish but not yet fully above zero",
          "meaning": "Momentum expanding upward"
        },
        "bullish_decelerating": {
          "zero_above": "+3 score — bullish but weakening",
          "zero_below": "+1 score — weak, near zero",
          "meaning": "Momentum expanding but slowing; reversal warning"
        },
        "bearish_accelerating": {
          "zero_below": "-7 score — strong bearish signal",
          "zero_above": "-4 score — bearish but not fully confirmed",
          "meaning": "Momentum expanding downward"
        },
        "signal_cross": {
          "aligned_with_bias": "+3 score bonus",
          "opposing_bias": "-3 score penalty"
        }
      },
      "trade_quality": {
        "best": "Histogram expanding in trade direction with zero line agreement",
        "good": "Histogram aligned but not expanding",
        "weak": "Histogram in reversal (BULLISH_DECELERATING)"
      }
    },

    "cvd_iq": {
      "alias": "CVD",
      "measurement": "Order flow: cumulative buy/sell volume with divergence analysis",
      "components": {
        "cvd_direction": "BUYING | SELLING | NEUTRAL",
        "divergence": {
          "large": "Price extends while CVD diverges (major warning)",
          "medium": "Price extends while CVD stalls (moderate warning)",
          "small": "CVD slightly misaligned (minor warning)"
        },
        "absorption": "BUY_ABSORPTION | SELL_ABSORPTION | NONE — hidden support/resistance",
        "cost": "Cost state (cost of pulling price in a direction) — VERY_HIGH | HIGH | NORMAL | LOW | VERY_LOW",
        "delta_implied": "Move ratio — overextension or suppression",
        "aggression": "Imbalance ratio — aggressive buying vs selling"
      },
      "interpretation": {
        "buying_pressure": {
          "long_confirmation": true,
          "score": "+10",
          "meaning": "Real buyers participating; valid entry"
        },
        "selling_pressure": {
          "short_confirmation": true,
          "score": "+10",
          "meaning": "Real sellers participating; valid exit/short"
        },
        "large_divergence": {
          "warning_level": "CRITICAL",
          "score": "-15",
          "meaning": "Price extended while CVD stalled or reversed. Reversal risk extreme."
        },
        "medium_divergence": {
          "warning_level": "HIGH",
          "score": "-10"
        },
        "small_divergence": {
          "warning_level": "MEDIUM",
          "score": "-5"
        }
      },
      "trade_quality": {
        "best": "CVD direction confirms trade bias with no divergence",
        "good": "CVD direction confirmed with small divergence",
        "weak": "CVD divergence present; questionable entry"
      }
    },

    "atr": {
      "alias": "ATR",
      "measurement": "Average True Range — volatility (NOT directional)",
      "components": {
        "volatility_state": "LOW | NORMAL | HIGH",
        "compression": "ATR contracting (setup forming)",
        "expansion": "ATR expanding (momentum environment OR late move spike)",
        "percentile_rank": "ATR position relative to last 100 bars"
      },
      "interpretation": {
        "high_with_expansion": {
          "meaning": "Volatility expanding after compression — valid breakout environment",
          "score": "0 (no penalty)",
          "trading": "FAVOR breakout trades"
        },
        "high_without_expansion": {
          "meaning": "Spike in volatility — likely late move, fakeout risk",
          "score": "-3 (penalty)",
          "trading": "AVOID new entries; consider exits"
        },
        "low_with_compression": {
          "meaning": "Volatility compressing — setup forming, breakout readiness",
          "score": "0 (no penalty)",
          "trading": "Prepare for breakout, but don't force entries yet"
        },
        "low_without_compression": {
          "meaning": "Flat market — fakeout risk, low reward trades",
          "score": "-5 (penalty)",
          "trading": "AVOID breakout trades; favor mean reversion"
        },
        "extreme_high_percentile": {
          "percentile": ">90",
          "meaning": "Extreme volatility — avoid entries, consider exits",
          "score": "-5 additional penalty"
        },
        "extreme_low_percentile": {
          "percentile": "<10",
          "meaning": "Dead market — avoid all directional trades",
          "score": "-5 additional penalty"
        }
      },
      "trade_quality": {
        "best": "NORMAL range (20–80 percentile) + expansion context matching trade bias",
        "good": "NORMAL range, neutral expansion state",
        "weak": "Extreme high or low percentile; avoid entries"
      },
      "CRITICAL_RULE": "ATR is a FILTER ONLY. Never directional. Never buy because ATR is high or low."
    }
  },

  "regime_performance_notes": {
    "trending_markets": "Structure + MACD dominate; CVD less critical. Favor structure conflicts.",
    "ranging_markets": "PAVP dominance; structure weak. Favor mean reversion + VA edges.",
    "breakout_markets": "ATR expansion critical. Structure must confirm. Trend Speed peak.",
    "reversal_markets": "CVD divergence king. Structure weakening. Avoid trading reversals at extremes."
  },

  "conflicting_signals_rule": "If any TWO indicators strongly oppose → bias toward NO TRADE"
}
```

**Size:** ~2,500 tokens  
**Usage:** Read ONCE per session, then reference by indicator name

---

### Task 1.3: Create REGIME_DEFINITIONS.json (Compressed Decision Tree)

```json
{
  "version": "1.0",
  "created": "2026-05-11",
  "note": "Replaces regime_detection.txt (105 lines → 60 lines)",

  "regimes": {
    "TRENDING_UP": {
      "signals": [
        "ZigZag.structure == BULLISH",
        "PAVP.value_area_position == ABOVE_VA",
        "Trend_Speed.direction == BULLISH && Trend_Speed.regime == EXPANSION",
        "MACD.histogram_state == BULLISH_ACCELERATING",
        "CVD.cvd_direction == BUYING"
      ],
      "signal_count_required": 3,
      "confidence_per_signal": 20,
      "entry_bias": "LONG",
      "note": "If 5/5 signals → confidence 100. If 3/5 → confidence 60."
    },

    "TRENDING_DOWN": {
      "signals": [
        "ZigZag.structure == BEARISH",
        "PAVP.value_area_position == BELOW_VA",
        "Trend_Speed.direction == BEARISH && Trend_Speed.regime == EXPANSION",
        "MACD.histogram_state == BEARISH_ACCELERATING",
        "CVD.cvd_direction == SELLING"
      ],
      "signal_count_required": 3,
      "confidence_per_signal": 20,
      "entry_bias": "SHORT",
      "note": "Mirror of TRENDING_UP"
    },

    "RANGING": {
      "signals": [
        "PAVP.value_area_position == INSIDE_VA",
        "ZigZag.structure == NEUTRAL",
        "Trend_Speed.regime == CONSOLIDATION",
        "MACD.histogram_state IN [BULLISH_DECELERATING, BEARISH_RECOVERING]"
      ],
      "signal_count_required": 2,
      "confidence_per_signal": 25,
      "entry_bias": "NO_TRADE (except mean reversion at VA edges)",
      "note": "Favor exits, not entries. Avoid breakout trades."
    },

    "BREAKOUT": {
      "signals": [
        "ATR.expansion == true && ATR.compression == true (prior candle)",
        "PAVP price breaks VAH or VAL with acceptance",
        "Trend_Speed sharply increasing (ratio > 1.2)",
        "CVD.cvd_direction confirms direction",
        "Volume expansion (PAVP shift)"
      ],
      "signal_count_required": 3,
      "confidence_per_signal": 20,
      "entry_bias": "Direction of breakout (LONG if VAH break, SHORT if VAL break)",
      "note": "Rare regime. Requires structure + ATR + CVD agreement."
    },

    "REVERSAL": {
      "signals": [
        "Price extreme away from POC (>2% from POC)",
        "CVD.divergence IN [large, medium]",
        "Trend_Speed.regime == EXHAUSTION",
        "MACD weakening (BULLISH_DECELERATING or BEARISH_RECOVERING)",
        "ZigZag showing potential BOS"
      ],
      "signal_count_required": 2,
      "confidence_per_signal": 25,
      "entry_bias": "Opposite of current trend (wait for structure confirmation)",
      "note": "DO NOT trade reversals at extremes. Wait for price to reclaim structure."
    },

    "UNCERTAIN": {
      "signals": [
        "Contradictory indicators (structure vs PAVP, etc)",
        "Signal count < 2 for all regimes",
        "Regime transitioning"
      ],
      "signal_count_required": 0,
      "confidence": 0,
      "entry_bias": "NO_TRADE (unless score ≥ 80)",
      "penalty": -10,
      "note": "If regime uncertain, apply -10 to base score and require score ≥80 for ANY trade."
    }
  },

  "regime_transition_rules": {
    "rule_1": "Do NOT shift regime on single indicator signal",
    "rule_2": "Require at least 2 independent indicators to agree on new regime",
    "rule_3": "PAVP boundary shift (acceptance change) = automatic regime review",
    "rule_4": "ZigZag BOS / CHoCH = automatic regime review",
    "note": "Regime changes less frequently than you think. Default to NO TRADE during transitions."
  },

  "output_format": {
    "regime": "One of 6 above",
    "confidence": "0–100 (sum of matching signals)",
    "status": "Stable | Transitioning | Uncertain",
    "supporting_indicators": {
      "structure": "ZigZag + PAVP state",
      "momentum": "Trend Speed + MACD state",
      "order_flow": "CVD state",
      "volatility": "ATR state"
    }
  }
}
```

**Size:** ~800 tokens (vs. 350 for regime_detection.txt, but better structured)  
**Net saving:** Minimal per read, but eliminates prose ambiguity

---

### Task 1.4: Create FEEDBACK_LEARNING.json

```json
{
  "version": "1.0",
  "created": "2026-05-11",
  "note": "Replaces feedback_system.txt (91 lines) with actionable JSON",

  "error_classes": {
    "A": {
      "name": "STRUCTURE_ERROR",
      "definition": "ZigZag and PAVP conflicted; conflict was ignored",
      "example": "BEARISH ZigZag, ABOVE_VA PAVP, trade taken LONG anyway",
      "fix": "Increase structure weight; decrease momentum weight",
      "frequency_alert": "If >30% of losses are Type A → critical issue"
    },
    "B": {
      "name": "MOMENTUM_ERROR",
      "definition": "MACD or Trend Speed misaligned with direction; misalignment ignored",
      "example": "BULLISH_DECELERATING MACD, trade taken LONG with high confidence",
      "fix": "Decrease momentum weight; increase order flow weight"
    },
    "C": {
      "name": "ORDER_FLOW_ERROR",
      "definition": "CVD divergence present; divergence ignored or misread",
      "example": "Large CVD divergence, but trade taken against divergence",
      "fix": "Increase CVD weight; apply stricter divergence penalty"
    },
    "D": {
      "name": "LOCATION_ERROR",
      "definition": "Trade taken inside Value Area or at POC; trade failed",
      "example": "Price inside VA (chop zone), trade taken with A-grade setup",
      "fix": "Increase PAVP weight; apply stricter VA penalties"
    },
    "E": {
      "name": "VOLATILITY_ERROR",
      "definition": "ATR regime mismatch: breakout in low ATR, or reversal in ATR spike",
      "example": "ATR percentile 5 (dead market), breakout trade attempted",
      "fix": "Increase ATR filter weight; stricter low-ATR penalties"
    },
    "F": {
      "name": "OVERCONFIDENCE_ERROR",
      "definition": "High confidence (≥7.5) but weak confluence (score artificially high)",
      "example": "Confidence 80, but only 2/5 indicators aligned",
      "fix": "Recalibrate confidence scaling; require higher minimum confluence"
    }
  },

  "feedback_input_schema": {
    "trade_id": "unique ID",
    "timestamp": "ISO timestamp",
    "direction": "LONG | SHORT",
    "setup_grade": "A | B | C",
    "confidence_at_entry": "0–100",
    "outcome": "WIN | LOSS | BREAKEVEN | INVALID",
    "indicator_snapshot_at_entry": "full snapshot dict",
    "error_class": "A–F or NONE",
    "r_multiple": "profit/risk ratio if available"
  },

  "analysis_rules": {
    "rule_1": "Classify EVERY loss into ONE primary error class",
    "rule_2": "Only classify as Type A if BOTH ZigZag AND PAVP opposed trade direction",
    "rule_3": "Overconfidence (Type F) applies if confidence ≥ 7.5 but ≤2 indicators aligned",
    "rule_4": "DO NOT retroactively change scoring rules based on feedback",
    "rule_5": "ONLY output weight adjustment signals, not rule changes"
  },

  "output_format": {
    "batch_analysis": {
      "total_trades": "N",
      "wins": "X",
      "losses": "Y",
      "win_rate": "percentage",
      "error_distribution": {
        "error_class_A": "% of losses",
        "error_class_B": "% of losses",
        "error_class_C": "% of losses",
        "error_class_D": "% of losses",
        "error_class_E": "% of losses",
        "error_class_F": "% of losses"
      },
      "system_weakness_ranking": [
        "1st weakest: error class [letter] ([%])",
        "2nd weakest: error class [letter] ([%])",
        "3rd weakest: error class [letter] ([%])"
      ],
      "indicator_adjustment_signals": {
        "PAVP": "INCREASE | DECREASE | NO_CHANGE (reason)",
        "MACD": "INCREASE | DECREASE | NO_CHANGE",
        "Trend_Speed": "INCREASE | DECREASE | NO_CHANGE",
        "ZigZag": "INCREASE | DECREASE | NO_CHANGE",
        "CVD": "INCREASE | DECREASE | NO_CHANGE",
        "ATR": "INCREASE | DECREASE | NO_CHANGE"
      },
      "confidence_calibration": {
        "overconfident_zone": "confidence X–Y, win_rate Z%",
        "underconfident_zone": "confidence X–Y, win_rate Z%"
      },
      "regime_sensitivity": {
        "trending_performance": "W–L, type distribution",
        "ranging_performance": "W–L, type distribution",
        "breakout_performance": "W–L, type distribution"
      }
    }
  }
}
```

**Size:** ~1,000 tokens  
**Usage:** Reference WEEKLY for feedback analysis, not per-decision

---

## PHASE 2: STUBIFY PYTHON FILES (Week 2)

### Task 2.1: Create scoring_stubs.json

**Goal:** Replace loading 621-line scoring_engine_py.py with lightweight contracts

```json
{
  "version": "2.0",
  "created": "2026-05-11",
  "note": "Function stubs for scoring_engine_py.py — NO logic, only contracts",

  "functions": {
    "score_structure": {
      "module": "scoring_engine_py",
      "signature": "score_structure(snapshot: dict, bias: str) -> (float, list[str])",
      "inputs": {
        "snapshot": "Full indicator snapshot with keys: zigzag, pavp",
        "bias": "LONG | SHORT"
      },
      "outputs": {
        "score_delta": "Float range [-25, 25]",
        "reasons": "List of explanation strings"
      },
      "logic_reference": "See TRADING_RULES.json section 'layers.structure'",
      "example": {
        "input": "snapshot with BULLISH ZigZag + ABOVE_VA PAVP + BOS_UP",
        "output": "[25.0, ['Strong structure: BULLISH + BOS_UP + ABOVE_VA']]"
      },
      "complexity": "O(1), pure function",
      "cache_safe": true
    },

    "score_location": {
      "module": "scoring_engine_py",
      "signature": "score_location(snapshot: dict, bias: str) -> (float, list[str])",
      "inputs": {
        "snapshot": "Full snapshot with key: pavp"
      },
      "outputs": {
        "score_delta": "Float range [-15, 10]",
        "reasons": "List of explanation strings"
      },
      "logic_reference": "See TRADING_RULES.json section 'layers.location'",
      "complexity": "O(1)"
    },

    "score_momentum": {
      "module": "scoring_engine_py",
      "signature": "score_momentum(snapshot: dict, bias: str) -> (float, list[str])",
      "inputs": {
        "snapshot": "Full snapshot with keys: trend_speed, macd"
      },
      "outputs": {
        "score_delta": "Float range [-15, 20]",
        "reasons": "List of explanation strings"
      },
      "logic_reference": "See TRADING_RULES.json section 'layers.momentum'",
      "components": ["trend_speed_scoring", "macd_scoring"],
      "complexity": "O(1)"
    },

    "score_order_flow": {
      "module": "scoring_engine_py",
      "signature": "score_order_flow(snapshot: dict, bias: str) -> (float, list[str])",
      "inputs": {
        "snapshot": "Full snapshot with key: cvd"
      },
      "outputs": {
        "score_delta": "Float range [-20, 15]",
        "reasons": "List of explanation strings"
      },
      "logic_reference": "See TRADING_RULES.json section 'layers.order_flow'",
      "complexity": "O(1)"
    },

    "score_volatility": {
      "module": "scoring_engine_py",
      "signature": "score_volatility(snapshot: dict) -> (float, list[str])",
      "inputs": {
        "snapshot": "Full snapshot with key: atr"
      },
      "outputs": {
        "score_delta": "Float range [-10, 0]",
        "reasons": "List of explanation strings"
      },
      "logic_reference": "See TRADING_RULES.json section 'layers.volatility'",
      "note": "NEVER directional. Applied last as modifier only.",
      "complexity": "O(1)"
    },

    "final_score": {
      "module": "scoring_engine_py",
      "signature": "final_score(base: float, adjustments: list[float]) -> (float, str)",
      "inputs": {
        "base": "Base score (50)",
        "adjustments": "List of deltas from each layer"
      },
      "outputs": {
        "score": "Float range [0, 100]",
        "grade": "A | B | C | NONE"
      },
      "thresholds": {
        "A": "≥75",
        "B": "60–74",
        "C": "50–59",
        "NONE": "<50"
      },
      "complexity": "O(1)"
    }
  },

  "usage_pattern": {
    "step_1": "Load snapshot from signal_format.json",
    "step_2": "Call score_structure(snapshot, bias) → delta_s, reasons_s",
    "step_3": "Call score_location(snapshot, bias) → delta_l, reasons_l",
    "step_4": "Call score_momentum(snapshot, bias) → delta_m, reasons_m",
    "step_5": "Call score_order_flow(snapshot, bias) → delta_o, reasons_o",
    "step_6": "Call score_volatility(snapshot) → delta_v, reasons_v",
    "step_7": "Call final_score(50, [delta_s, delta_l, delta_m, delta_o, delta_v])",
    "step_8": "Return decision JSON"
  },

  "note": "NO implementation details. These are CONTRACTS ONLY. Claude references this, not the 621-line .py file."
}
```

**Size:** ~800 tokens (vs. 3,500 for full Python file)  
**Savings:** 2,700 tokens per decision cycle

---

## PHASE 3: IMPLEMENT SESSION CACHING

### Task 3.1: SESSION INIT PROTOCOL

Create a new file: `SESSION_INIT.txt`

```
TRADING_AGENT_V2 SESSION INITIALIZATION

Step 1: Load TRADING_RULES.json [2,000 tokens]
  → Version: 2.0
  → Checksum: [SHA256]
  → Mark as SESSION_STATIC: do not reload

Step 2: Load INDICATOR_GLOSSARY.json [1,500 tokens]
  → Version: 1.0
  → Checksum: [SHA256]
  → Mark as SESSION_STATIC: do not reload

Step 3: Load signal_format.json [50 tokens]
  → Canonical schema
  → Mark as SESSION_STATIC: do not reload

Step 4: Load REGIME_DEFINITIONS.json [800 tokens]
  → Version: 1.0
  → Mark as SESSION_STATIC: do not reload (or reload on regime transition)

Step 5: Initialize session memory
  → trade_count = 0
  → session_start_time = now()
  → accumulated_context = 4,350 tokens

READY TO PROCESS SIGNALS

Each signal processing:
  → Load signal (~50 tokens)
  → Determine regime (~100 tokens)
  → Score layers (~200 tokens)
  → Generate decision (~100 tokens)
  → Total per signal: ~450 tokens
  
(No re-reading TRADING_RULES.json, etc.)

10 signals/session:
  → Total context: 4,350 (init) + 4,500 (signals) = 8,850 tokens
  → vs. current: 7,350 (init) + 4,500 (signals) + 14,000 (re-reads) = 25,850 tokens
  → SAVINGS: 17,000 tokens per 10-signal session (66% reduction)
```

---

## IMPLEMENTATION CHECKLIST

### WEEK 1 (Consolidation)
- [ ] Create TRADING_RULES.json (copy template, validate JSON)
- [ ] Create INDICATOR_GLOSSARY.json (copy template, validate)
- [ ] Create REGIME_DEFINITIONS.json (compress regime_detection.txt)
- [ ] Create FEEDBACK_LEARNING.json (formalize feedback_system.txt)
- [ ] Create ENTRY_EXIT_RULES.json (merge entry/exit rules)
- [ ] Test: Load each file, confirm <3,500 tokens combined
- [ ] Archive: system_prompt.txt, execution_engine.txt, scoring_engine.txt (v1 backup)
- [ ] Verify: All JSON files pass schema validation

### WEEK 2 (Stubification)
- [ ] Create scoring_stubs.json (contracts for 5 scoring functions)
- [ ] Create indicator_api.json (contracts for indicator_engine functions)
- [ ] Create enrichment_api.json (contracts for signal_enrichment functions)
- [ ] Test: Call stubs without loading Python files
- [ ] Measure: Context usage per function call
- [ ] Document: When to call stubs vs. actual functions (rare)

### WEEK 3 (Workflow & Caching)
- [ ] Update Claude prompt to use TIER 1 + TIER 2 file loading
- [ ] Implement SESSION_INIT.txt protocol
- [ ] Create LIVE_SIGNAL_PROMPT.txt (200 tokens, vs. 900 current)
- [ ] Test: Process 10 signals, measure total context
- [ ] Document: Session memory management
- [ ] Measure savings: Compare to baseline

### WEEK 4 (Feedback Isolation)
- [ ] Create TRADING_FEEDBACK template (separate context for learning)
- [ ] Document: When to use TRADING vs. TRADING_FEEDBACK
- [ ] Create handoff protocol (live decisions → feedback analysis)
- [ ] Archive: feedback_system.txt + adaptive_weighting.txt (backup)

---

## EXPECTED RESULTS

| Metric | Before | After | Savings |
|--------|--------|-------|---------|
| Tokens/decision | 3,000 | 450 | 85% |
| Tokens/10-signal session | 25,850 | 8,850 | 66% |
| Tokens/100 signals/month | 258,500 | 88,500 | 66% |
| Context bloat ratio | 96% | 12% | 87% |
| Decision latency | ~3s | ~1s | 67% |

---

**Document Version:** 1.0  
**Status:** Ready for implementation  
**Effort estimate:** 4 weeks for full rollout, 1 week for Phases 1–2
