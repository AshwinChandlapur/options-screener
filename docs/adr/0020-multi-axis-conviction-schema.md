# ADR-0020: Multi-Axis Conviction Schema

- **Status**: Accepted
- **Date**: 2026-05-16
- **Supersedes (in part)**: [ADR-0017](0017-narrative-phase5-detector.md) §3 (10-state conviction taxonomy)

## Context

Phase 4 of the narrative platform introduced a 10-state categorical `conviction_state` field
classified by GPT-4o-mini (see [NARRATIVE_METHODOLOGY.md §3](../NARRATIVE_METHODOLOGY.md)):

```
researched_bull, researched_bear, emotional_bull, emotional_bear, uncertainty,
earnings_focused, product_thesis, ecosystem_thesis, institutional_watch, exit_signal
```

After three weeks of production data, two problems are clear:

1. **Downstream collapse.** Only three of the ten states are exposed as ratios in the
   `ticker_timeline` snapshot (`researched_bull_ratio`, `researched_bear_ratio`,
   `emotional_bull_ratio`). The detector's lifecycle stages 5 and 6 (Consensus / Saturation)
   are gated solely on `emotional_bull_ratio`. The remaining seven states only contribute as
   weights to a single scalar (`conviction_dd_norm`). Net effect: a stock whose 14-day signal
   pool is dominated by `product_thesis` or `earnings_focused` posts cannot reach Stage 5/6
   regardless of how loud or one-sided the narrative is.

2. **Axis conflation.** The 10 states mash together four independent axes:
   - **Direction** (bull / bear)
   - **Substance** (researched / emotional)
   - **Driver** (earnings / product / macro / flows / valuation / other)
   - **Position** (entering / holding / exiting / unstated)

   A single categorical label loses the orthogonal structure. Compound queries like
   "bullish researched product narratives" are impossible to express against the schema.
   The `exit_signal` state in particular conflates profit-taking on winners (bullish-historical,
   bearish-forward) with covering losers (bearish-historical, neutral-forward) — opposite
   signals collapsed to one bucket with weight `-0.5`.

## Decision

Replace the single 10-state `conviction_state` enum with a structured 4-axis object emitted
by the classifier:

```jsonc
{
  "direction":  "bull" | "bear",
  "substance":  "researched" | "emotional",
  "driver":     "earnings" | "product" | "macro" | "flows" | "valuation" | "other",
  "position":   "entering" | "holding" | "exiting" | "unstated",
  "confidence": 0.0-1.0
}
```

The new fields are persisted on each `signals` Cosmos document as
`conviction_direction`, `conviction_substance`, `conviction_driver`, `conviction_position`,
`conviction_confidence`.

### Backward compatibility

The legacy `conviction_state` field continues to be written, derived deterministically from
the axes:

| Axis combination | Legacy state |
|---|---|
| `(*, *, *, exiting)` | `exit_signal` |
| `(*, *, earnings, *)` | `earnings_focused` |
| `(*, *, product, *)` | `product_thesis` |
| `(*, *, macro, *)` | `ecosystem_thesis` |
| `(*, *, flows, *)` | `institutional_watch` |
| `(bull, researched, *, *)` | `researched_bull` |
| `(bear, researched, *, *)` | `researched_bear` |
| `(bull, emotional, *, *)` | `emotional_bull` |
| `(bear, emotional, *, *)` | `emotional_bear` |
| no axes set (legacy fallback) | `uncertainty` |

Precedence is top-to-bottom; the first matching row wins. This preserves all existing
aggregator computation (`_CONVICTION_WEIGHTS` lookup, three legacy ratios, `conviction_dd_norm`)
without backfill. Legacy docs already in Cosmos with only `conviction_state` continue to work.

### New aggregator outputs

`ticker_timeline` gains five new fields, all `float | None` (None until classifier has run):

| Field | Definition |
|---|---|
| `conviction_bull_share` | `(direction == "bull")` count / classified |
| `conviction_researched_share` | `(substance == "researched")` count / classified |
| `conviction_entering_share` | `(position == "entering")` count / classified |
| `conviction_exiting_share` | `(position == "exiting")` count / classified |
| `conviction_driver_top` | most-frequent non-`other` driver, or `"other"` if tied / sparse |

### Detector lifecycle changes

Stage 5 and Stage 6 trigger conditions broaden from the narrow `emotional_bull_ratio` to a
composite bullish-share measure that includes all bullish narratives regardless of driver:

| Stage | Old rule | New rule |
|---|---|---|
| 5 (Consensus) | `emotional_bull_ratio ≥ 0.50 ∧ gini < 0.30` | `bull_share ≥ 0.65 ∧ researched_share < 0.40 ∧ gini < 0.30` |
| 6 (Saturation) | `emotional_bull_ratio ≥ 0.65 ∧ gini_14d ≥ 0.55` | `bull_share ≥ 0.75 ∧ researched_share < 0.30 ∧ gini_14d ≥ 0.55` |

Rationale: Consensus is "lots of bulls" (direction), Saturation is "lots of bulls without
substance" (direction + low substance). The old rules conflated these by relying on a single
state that already encoded both axes.

A `bull_share` fallback path is included: if axis fields are absent (legacy docs), the
detector falls back to the legacy `emotional_bull_ratio` gate so historical data continues
to classify.

### Classifier prompt

Replace the 10-state precedence prompt with a 4-axis extraction prompt. Each axis is an
independent enum in the JSON schema (`strict: true`); the model cannot return a malformed
combination. The `confidence` field is the model's self-reported certainty across all four
axes jointly (single scalar, same semantics as before).

The classifier remains a single OpenAI call per signal — adding axes does not increase
request count, only output token budget (~10 extra tokens per response).

## Consequences

**Positive**:
- Compound queries against narrative shape become trivial (`bull ∧ researched ∧ product`).
- Lifecycle detection now responds to product/earnings/macro narratives, not only meme-style
  emotional bull runs.
- `exit_signal` ambiguity resolved — profit-taking on a winner is now `(bull, *, *, exiting)`,
  cutting a loser is `(bear, *, *, exiting)`.
- Frontend can render axis chips that summarise the *shape* of conviction without forcing
  the user to read 10 enum values.
- Forward-compatible: adding a new driver (e.g. `regulatory`) requires only a schema enum
  bump, not a re-labelling of the whole taxonomy.

**Negative**:
- One additional layer of complexity in the classifier output schema; tests need to assert
  on axis combinations rather than a single string.
- Two derived shares (`bull_share`, `researched_share`) overlap conceptually with the old
  ratios; documentation must make precedence clear.
- The `_CONVICTION_WEIGHTS` table is retained but now operates on derived legacy states; if
  the weights need tuning, the change can be expressed either in terms of axes or legacy
  states, which is a minor footgun for future authors.

**Neutral**:
- No Cosmos backfill required. Old docs keep working; new docs get richer data.
- Embedding generation (Phase 5) is unaffected — still operates on the rationale text only.

## Migration

1. Deploy new classifier worker. Existing classified docs are untouched; new
   classifications include both legacy and axis fields.
2. Deploy aggregator update simultaneously — it computes new shares opportunistically
   (None when axes absent) and continues to emit legacy three ratios for back-compat.
3. Detector reads new shares with legacy fallback; no flag flip needed.
4. Frontend ships axis chips behind a graceful null check.
5. Optional: a one-shot script can backfill axes for the most recent ~30 days of signals
   by re-running the classifier with `force=True`. Not required for correctness.

## References

- [NARRATIVE_METHODOLOGY.md §3](../NARRATIVE_METHODOLOGY.md) — conviction taxonomy (updated)
- [NARRATIVE_SYSTEM_DESIGN.md](../NARRATIVE_SYSTEM_DESIGN.md) — pipeline overview (updated)
- [ADR-0017](0017-narrative-phase5-detector.md) — original Phase 4/5 decisions
- [ADR-0018](0018-classifier-embedding-soft-fail.md) — classifier operational decisions
