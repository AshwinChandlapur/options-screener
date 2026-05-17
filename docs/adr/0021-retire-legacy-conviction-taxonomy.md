# ADR-0021 — Retire the legacy 10-state conviction taxonomy

**Status:** Accepted
**Date:** 2026-05-16
**Supersedes:** none
**Amends:** [ADR-0020](0020-multi-axis-conviction-schema.md), [ADR-0019](0019-narrative-phase6-scorer.md)

## Context

[ADR-0020](0020-multi-axis-conviction-schema.md) introduced a 4-axis
conviction schema (`direction`, `substance`, `driver`, `position`) and kept
the legacy 10-state `conviction_state` field as a derived, additive, dual-write
back-compat layer so existing Cosmos documents and the Component D scorer kept
working without coordinated re-deployment.

The narrative platform is pre-production. There is no real downstream consumer
that depends on the legacy 10-state vocabulary. Keeping the dual-write costs:

- two write paths in the classifier (`conviction_state` + axis fields)
- five legacy ratio rollups in the aggregator
  (`conviction_researched_bull_ratio`, `conviction_researched_bear_ratio`,
   `conviction_emotional_bull_ratio`, `conviction_dd_norm`, `conviction_classified_14d`)
- a fallback branch in the detector's Stage 5/6 lifecycle rules
- five legacy fields on `TickerTimelineSnapshot` / `TickerDetail`
- a duplicated "What are people saying?" section in the UI that re-presents
  the same information as the new "Conviction axes" section
- the `conv_norm ∈ [-0.5, 1.0]` term in Component D, which is a weighted mean
  of the 10 state weights and now has no source of truth (the 10 states no
  longer exist as a primary classification)

These layers pay for themselves only in a backfill scenario we will not face.

## Decision

Delete the legacy taxonomy in one sweep:

1. **Classifier** stops writing `conviction_state`. The job-fetch gate becomes
   `WHERE NOT IS_DEFINED(c.conviction_direction)`. Existing `signals`
   documents that carry only `conviction_state` are re-classified on the next
   cron — a small one-time OpenAI cost, acceptable for a pre-prod system.

2. **Aggregator** drops the five legacy ratios and adds two **joint shares**
   computed from axis pairs:
   - `conviction_bull_researched_share` — fraction of classified 14d signals
     with `direction=bull ∧ substance=researched`
   - `conviction_bear_researched_share` — fraction with
     `direction=bear ∧ substance=researched`

   The four existing axis marginal shares (`conviction_bull_share`,
   `conviction_researched_share`, `conviction_entering_share`,
   `conviction_exiting_share`) and `conviction_driver_top` remain — the
   marginals drive the detector, the joints drive the scorer.

3. **Detector** Stage 5 and Stage 6 rules use the axis path only — no
   `emotional_bull_ratio` fallback. Tickers without axis-classified signals
   simply do not advance past their catch-all stage. This is the correct
   behaviour: the lifecycle gate should not fire on data we do not have.

4. **Scorer Component D** is rewritten as

       D = min(0.6·s_br + 0.2·s_Br, 1) × D_max

   where `s_br = conviction_bull_researched_share` and
   `s_Br = conviction_bear_researched_share`. The `conv_norm` term is
   dropped. The 0.6 / 0.2 weights are preserved verbatim from the original
   formula — the joints carry the same semantic the legacy ratios carried
   (the dominant term is "researched bull majority"; bear-research is rewarded
   as healthy debate). No `D_max` recalibration is required.

5. **Backend `TickerDetail`** drops the five legacy ratio fields. The
   `dominant_signal` string vocabulary changes from 10 state names to four
   compound axis labels: `bull_researched`, `bull_emotional`,
   `bear_researched`, `bear_emotional`, plus `unknown` when no axis data.
   The `_dominant_from_doc` fallback is computed from the four marginal axis
   shares.

6. **Frontend** drops the "What are people saying?" section from
   `TickerDetailPanel`. The "Conviction axes" section becomes the sole
   conviction view. `SIGNAL_LABELS` updated to the four compound labels.
   `ScoreLegend` Component D card updated to the new formula.

7. **Docs** §3 of `NARRATIVE_METHODOLOGY.md` becomes single-section (no
   legacy derivation table); §5.1 Component D updated; the field reference
   in `NARRATIVE_SYSTEM_DESIGN.md` lists axis shares only.

## Consequences

**Positive**
- One classification vocabulary; one set of rollup fields; one detector
  code path; one Component D formula. Roughly 200 lines of conditional code
  removed.
- The `conv_norm` term — a weighted average of 10 state weights that was
  always hard to explain to humans — is gone. Component D is now a sum of
  two interpretable shares.
- The detail-panel duplication ("What are people saying?" vs "Conviction
  axes") is resolved.

**Negative / accepted**
- Existing pre-axis `signals` documents are re-classified on the next
  classifier run (OpenAI cost: ~$0.0001 per signal × current backlog ≈
  negligible).
- Tickers whose `ticker_timeline` doc carried only legacy ratios will not
  advance past Stage 0 / catch-all Stage 1 until the next aggregator run
  rewrites the doc with axis shares. Self-healing within 15 minutes (the
  aggregator cron interval).
- Any external script or notebook that reads
  `conviction_researched_bull_ratio` etc. directly from Cosmos will break.
  No such consumer exists in this repo.

**Rollback**
- Revert this commit. Aggregator will resume writing legacy ratios on the
  next run. Tickers re-aggregated in the meantime will be missing legacy
  ratios for at most one 15-min cycle.

## Validation

Full test suite passes (backend unit + worker units) with the legacy fields
removed and Component D regression test updated to assert
`D = 20 × min(0.6·s_br + 0.2·s_Br, 1)`.
