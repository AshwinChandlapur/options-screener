# ADR-0012: Swing hybrid (additive + multiplicative) scoring

- **Status**: Accepted
- **Date**: 2026-05-11

## Context

Swing v1.0 used a fully additive composite:

```
score = rr_pts + setup_pts + context_pts + institutional_pts
```

The May 2026 quant-trader audit identified two structural issues with this shape:

1. **Collinearity is invisible.** A high-RS, EMA-aligned setup picks up points in both
   "Setup" and "Context" buckets; the model can't tell whether its 80 is "broad
   strength" or "two correlated readings of the same trend".
2. **No way to express conditional severity.** A breakout on a hostile tape, into
   earnings, and 5% extended past its trigger should be punished *more than the sum*
   of its individual deductions — but additive math can only subtract a fixed amount
   from each bucket. The natural shape is multiplicative, so a 0.7 × 0.5 × 0.7 = 0.245
   compound haircut emerges automatically.

## Decision

Adopt a **hybrid** scoring shape:

```
raw   = rr_pts + setup_pts + context_pts + institutional_pts        # additive
final = raw × regime_factor × earnings_factor × extended_factor      # multiplicative
final = clamp(final, 0, 100)
```

Within a bucket, addition continues to express "more is better" with the existing
linear maps. Across buckets, multiplication expresses "any one of these can dominate
the verdict". The two domains are kept clean — within-bucket calibration changes don't
touch cross-bucket multipliers and vice versa.

### Multiplier sources

- `regime_factor`: from `RegimeState.multiplier`, range `[0.6, 1.0]` (ADR-0010).
- `earnings_factor`: from `earnings_factor(days_to_earnings)`, range `[0.5, 1.0]` (ADR-0011).
- `extended_factor`: `0.7` if the current price is > 3% past the structural trigger
  (`SetupTrigger.extended` in `risk.py`), else `1.0`.

All multipliers floor strictly above zero so a "punished" score is still debuggable
(you can read the `multipliers` dict in the response and see what dragged it down).
A multiplier of `0.0` would conflate "regime hostile" with "not enough data".

### Within-setup multipliers

In `services/swing/classifier.py`, the same shape applies *inside* a bucket:

- **Breakout**: `vol_factor = max(0.5, min(1.0, surge_ratio / 1.5))`.
- **Momentum**: `align_factor = max(0.6, min(1.0, ema_score / 7))`.
- **Retest**: `× 0.5` outside the [5, 20] bars-since-reclaim window.
- **Reversion**: hard floor — `if price < EMA200: return score=0`.

These are *within-bucket* multipliers — they shape the "Setup" points before the
cross-bucket composition runs. Documented separately because they're calibration
decisions, not architectural.

### Confidence tiers

`confidence` is computed against the **post-multiplier** score:
- `high`: final ≥ 75 AND rr ≥ 3.5 AND setup_score ≥ 70.
- `medium`: final ≥ 55.
- `speculative`: otherwise.

A regime/earnings haircut can demote a setup from `high` to `medium` to
`speculative` — the right behaviour for a tape-aware screener.

## Options Considered

1. **Keep purely additive, add new "Regime" and "Earnings" buckets.**
   - Cons: still can't express conditional severity. A 0-point Regime bucket and a
     0-point Earnings bucket sums to a 20-point deduction; the same situation
     multiplicatively is `0.6 × 0.5 = 0.3` → 70% off. The latter is what the audit
     actually wants.
   - **Rejected.**

2. **Pure multiplicative across all factors.**
   - Cons: loses the calibrated "60 R:R points + 30 Setup points + 20 Context points"
     budget that the existing factor maps depend on. Would require re-deriving every
     factor's distribution from first principles.
   - **Rejected.**

3. **Hybrid (chosen).**
   - **Accepted.**

## Consequences

- ✅ Regime and event-risk now have proportional, conditional impact instead of
  flat deductions.
- ✅ Existing within-bucket calibration is preserved.
- ✅ The `multipliers` dict in every response makes the haircut path debuggable.
- ⚠ The compound floor is `0.6 × 0.5 × 0.7 = 0.21`, so a fully-loaded 100 raw can
  drop as low as ~21. Acceptable for hostile-environment, near-earnings, chasing
  setups; should be visible in the UI so users understand why.
- ⚠ Two multipliers crossing each other can introduce non-linearity that's harder to
  explain than additive subtraction. Mitigated by exposing `raw_score`, `multipliers`,
  and `score` together in the response.
- ⚠ `SCORING_VERSION` bumped to `"2.0.0"` to invalidate any cached client state and
  force the result-cache to refresh.

## References

- `backend/services/scoring/swing.py` — `compute_swing_score`,
  `SWING_SCORER_VERSION="2.0.0"`
- `backend/services/swing/classifier.py` — within-setup multipliers
- `backend/services/swing/risk.py` — `SetupTrigger.extended`
- `backend/tests/unit/test_swing_event_risk.py::TestCompositeScoring`
- ADR-0010 (regime engine), ADR-0011 (event-risk scoring)
