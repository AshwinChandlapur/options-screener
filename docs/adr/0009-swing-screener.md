# ADR-0009: Swing Trading Screener

**Status:** Accepted
**Date:** 2024
**Deciders:** owner

## Context

The screener has three options strategies (CSP / CC / DITM) plus an EM Rank
view, all of which sell or buy options against a directional opinion. None of
them serves the use case "I just want to find a 3–21 day directional equity
trade I'd put real risk on." A swing screener fills that gap with pure cash
equity setups.

The earlier swing prototype (deleted prior to this ADR) was a single-axis
RSI/EMA momentum filter. It produced too many low-quality candidates and gave
the trader no way to distinguish a tight base breakout from an oversold bounce.
Both have different geometry and different exit windows.

## Decision

Build a swing screener with **four named setup types**, each scored
independently, with a deterministic R:R-driven composite score on top.

### Setup taxonomy

| Setup     | Window  | Signature                                                |
|-----------|---------|----------------------------------------------------------|
| Breakout  | 5–10d   | tight base + volume surge + structure-high reclaim       |
| Momentum  | 7–14d   | EMA stack + ADX trend + RS leadership + MACD inflection  |
| Reversion | 3–7d    | oversold (RSI/Stoch) + positive divergence + Fib hold    |
| Retest    | 10–21d  | prior breakout level retested + new base above the level |

Each detector returns a 0–100 score with a list of human-readable drivers.
`classify_setup` picks the highest as `best_setup`.

### Risk model

Stops are **ATR-anchored** (`1.5 × ATR14` below entry, or the recent
10-bar swing low — whichever is tighter). Targets are setup-specific
R-multiples:

| Setup     | R-multiple |
|-----------|-----------:|
| Breakout  | 3.0×       |
| Momentum  | 2.75×      |
| Reversion | 2.5×       |
| Retest    | 3.25×      |

**Hard gate: R:R < 2.5 → exclude.** A stop greater than 50% below entry is
treated as structurally invalid (rejected).

### Hard gates (applied before scoring)

| Gate                    | Threshold     |
|-------------------------|--------------:|
| Min price               | $5            |
| Min ADV $               | $5,000,000    |
| Min OHLC history        | 60 bars       |
| Min setup score         | 40            |
| Min R:R                 | 2.5           |

### Composite scoring (`SWING_SCORER_VERSION = "1.0.0"`)

```
swing_score = R:R(40) + setup(30) + context(20) + institutional(10)
```

| Bucket         | Max | Components                                          |
|----------------|----:|-----------------------------------------------------|
| R:R            |  40 | piecewise: 2.5→0, 3.0→25, 4.0→35, 5.0+→40           |
| Setup          |  30 | `best_setup` score × 0.30                           |
| Context        |  20 | RS vs SPY (10) + EMA alignment (10)                 |
| Institutional  |  10 | A/D line slope (5) + held % institutions (5)        |

Confidence tiers:
- **High**: score ≥ 75 AND R:R ≥ 3.5 AND setup_score ≥ 70
- **Medium**: score ≥ 55
- **Speculative**: otherwise

### Earnings handling

Earnings ≤ 10 days = **⚠ flag only** (warning, not exclusion). Swing traders
sometimes want pre-earnings setups; the decision is left to the user. The
warning surfaces both in the table row (⚠ next to ticker) and in the expanded
detail panel.

### Universe

A new `swing_eligible` universe (~160 names) is added to
`backend/services/universe.py`. All tickers were statically vetted for:
- ≥ $500M market cap
- ≥ 500K average daily volume

This is curated, not algorithmic — same discipline as the existing
`stable_csp` universe. It is the default for the Swing tab.

### Institutional signals — proxies only

We do not (and will not) integrate dark-pool feeds. Institutional positioning
is proxied by:
1. **A/D line slope** (20-day money flow) — readily computable from OHLC.
2. **Held % institutions** — single snapshot from `yfinance .info`.

Dropped from scope: call OI delta tracking (would require a daily snapshot
store, and the signal value is marginal next to the two above).

### LLM commentary

A single batched Azure OpenAI call generates 1–2 sentence narratives + risk
notes for the **top 3** scoring candidates. Blinded to numerical scores — the
LLM receives only setup type, drivers, geometry. Best-effort: scan still
returns ranked candidates if the LLM call fails or Azure is unconfigured.

## Layering

Strict, per the repo's architecture rules:

```
routers/swing.py
  └─ services/swing_service.py        (orchestration: process_symbol, run_scan)
      ├─ services/swing/indicators.py (pure technical primitives)
      ├─ services/swing/classifier.py (4 detectors + classify_setup)
      ├─ services/swing/risk.py       (build_risk_plan)
      ├─ services/scoring/swing.py    (compute_swing_score)
      ├─ services/data_service.py     (get_ohlc, get_ticker_info)
      └─ services/swing_insight_service.py (batched LLM commentary)
```

`services/swing/indicators.py` holds swing-specific indicators only.
Indicators shared with CSP/CC/DITM (RSI, MACD, Bollinger, SMA trend, IV
percentile) remain in `services/indicators.py`.

## Consequences

**Positive**
- One new tab, no impact on options screeners.
- Hard gates eliminate the noise category that killed the previous prototype.
- Four named setups give the trader an explicit thesis to disagree with.
- ATR-anchored stops + per-setup R-multiples make exits mechanical.

**Negative**
- Adds ~160-name universe to the rotating yfinance fetch surface. Cache TTL
  (30 minutes via `swing_scan_cache`) mitigates.
- A/D line slope is a coarse proxy for institutional flow; users should treat
  the institutional bucket as a tie-breaker, not a primary signal.
- LLM commentary cost: 1 extra Azure call per scan (top-3 batch); negligible
  vs CSP insight calls which are per-row on demand.

## Follow-ups

- Characterization tests on a fixed fixture set (deferred — current 30 unit
  tests cover indicators, scoring, risk, classifier).
- ROC backtest harness for the four setups (out of scope for this ADR).
