# ADR-0011: Swing event-risk scoring

- **Status**: Accepted
- **Date**: 2026-05-11

## Context

The Swing screener (v1.0) treated earnings as a single boolean flag (`earnings_warning`)
shown in the UI. Scoring made no use of it. The May 2026 quant-trader audit flagged this
as a Major defect:

1. **Binary, not graduated.** "Earnings within 7 days" buckets a 7-day window with a
   1-day window — but the IV-crush risk profile of T-1 is qualitatively different from
   T-7.
2. **Hold window can cross earnings.** A breakout entered with a 5–15 day hold gets the
   full IV reset event right in the middle of the position. Nothing in the model
   noticed or trimmed.
3. **Reversion is uniquely hostile to earnings.** Mean-reversion edge collapses around
   binary catalysts; the additive composite still ranked these alongside trend
   continuations.

## Decision

Introduce a three-layer event-risk model:

### 1. Hard exclusions (gates)

In `swing_service.process_symbol`, before risk planning:

- **Any setup, days-to-earnings ≤ 1**: excluded with reason
  `"earnings in {dte}d (≤ 1d hard block)"`.
- **Reversion setup, days-to-earnings ≤ 7**: excluded with reason
  `"reversion + earnings in {dte}d (≤ 7d hard block)"`.

Constants live in `swing_service.py`: `EARNINGS_HARD_BLOCK_DAYS=1`,
`EARNINGS_REVERSION_BLOCK_DAYS=7`.

### 2. Graduated multiplier

`services.scoring.swing.earnings_factor(days_to_earnings)`:

| Days to earnings | Multiplier |
|------------------|-----------:|
| ≤ 3              | 0.50       |
| 4–7              | 0.75       |
| 8–14             | 0.90       |
| > 14, unknown    | 1.00       |

Applied multiplicatively in `compute_swing_score` alongside regime and extended
factors. Floors at 0.5 (never zero) so the model degrades, doesn't erase.

### 3. Hold-window trim

If `days_to_earnings < hold_max_days`, the hold window is trimmed to `dte - 1` and the
result tags `forced_short_hold=True`. The frontend renders this as a yellow indicator
on the hold cell. Trim never reduces below 1 day.

## Options Considered

1. **Keep binary flag, push earnings handling to UI.**
   - Cons: scoring still ranks T-2 setups equal to T-30 setups; users have to mentally
     discount, which is exactly what the screener exists to automate.
   - **Rejected.**

2. **Single hard-exclusion threshold (e.g., DTE ≤ 7 → drop).**
   - Cons: throws away medium-quality T-10 setups that simply need a haircut. The
     earlier model's behaviour, just calibrated more aggressively. Doesn't address
     hold-trim or reversion-specific risk.
   - **Rejected.**

3. **Graduated multiplier + setup-specific hard gates + hold trim (chosen).**
   - **Accepted.**

## Consequences

- ✅ Closer-to-earnings setups are penalised proportionally rather than censored.
- ✅ Reversion never fires into earnings; breakout/momentum/retest still allowed but
  haircut and trimmed.
- ✅ Hold-window output is now actionable (no "exit by Friday" plans straddling
  Wednesday earnings).
- ⚠ Adds three constants that must be calibrated together with the regime multiplier
  band — a setup with regime=0.6 + earnings=0.5 can drop a 90 raw to 27. This is
  intentional but should be reviewed if backtests show false-negative bias.
- ⚠ Hold-trim sets `forced_short_hold=True` but the screener does not enforce that the
  user actually exits — it's an advisory flag rendered in the UI.

## References

- `backend/services/scoring/swing.py` — `earnings_factor`, multiplier composition
- `backend/services/swing_service.py` — `EARNINGS_HARD_BLOCK_DAYS`,
  `EARNINGS_REVERSION_BLOCK_DAYS`, hold-trim logic
- `backend/tests/unit/test_swing_event_risk.py`
- `SCORING_REFERENCE.md` — Swing v2.0.0 section
- ADR-0010 (regime engine), ADR-0012 (hybrid scoring)
