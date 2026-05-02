# ADR-0008: DITM v3 lean model

- **Status**: Accepted
- **Date**: 2026-05-02

## Context

ADR-0007 reduced CSP/CC scoring from 14 → 8 factors and resolved five Major findings
from the May 2026 quant-trader diagnostic. **DITM was excluded** from that refactor and
remained on its v2-era 13-factor model. A follow-up DITM-specific audit by the same
quant-trader (May 2026) surfaced 14 findings — three Critical, ten Major, one Minor — that
trace to the same diseases the v3 CSP/CC refactor was designed to cure, plus DITM-specific
gaps:

**Critical:**
1. **No leverage factor.** The headline DITM metric (`δ × S / mid`) is not scored
   anywhere. Capital Efficiency (5 pts) ignores delta and can rank a 0.95Δ call higher
   than a 0.70Δ call with worse leverage.
2. **HV Rank > 50 hard-gate** eliminates ~50% of the universe by construction —
   structurally crushing high-momentum AI names that are the natural DITM hunting
   ground (NVDA, AMD, TSLA, PLTR continuously sit above HV Rank 50 in trending tapes).
3. **Methodology drift.** `SCORING_REFERENCE.md` had zero DITM mentions; the only DITM
   documentation lived inline in `DitmInput.tsx`'s `SCORE_LEGEND` array.

**Major (selected):**
4. Extrinsic% (28) and Annualised Theta% (17) measure the same signal — for
   `θ_annual ≈ extrinsic / T`, the two are ~90% correlated and together control 45% of
   strike score.
5. Two HV-derived vol-cheapness factors (HV Rank ENV + IV Percentile Strike) both
   computed from `compute_iv_rank_percentile`.
6. Trend gate (P>SMA50>SMA200) and 52W Distance disagree — full alignment ≈ near 52W
   high, but 52W only awards 7 pts at 0–3% below high vs 12 at 3–10%. Mean-reversion
   logic in a momentum screener.
7. Real cliff at `cap_pct = 25%` (0 → 5 pts).
9. Earnings hard-gate (≤7d → ENV=0) ignores DTE — fatal for a 365-DTE LEAP where the
   earnings IV pop reverses in days and 358 days of thesis remain.
10. Macro-hold flag is display-only; sets a banner but does not touch scores.
11. Frontend tier colors (75/65/55/45) disagreed with documented tiers (80/65/50/35).
12. OI column was colored by the IV Percentile factor; IV factor had no dedicated
    column.

## Options Considered

1. **Calibrate v2 in place** — fix the cliff, lower the HV gate threshold, recalibrate
   theta thresholds.
   - Cons: leaves the missing leverage factor, the Theta/Extrinsic redundancy, and the
     vol-cheapness duplication. Same class of finding the v3 CSP/CC refactor was
     justified by. **Rejected.**

2. **Lean v3 refactor (chosen.)**
   - 13 factors → 10 (5 ENV + 5 Strike).
   - Adds the missing Leverage factor.
   - Drops Theta% (redundant with Extrinsic%), Capital Efficiency (replaced by
     Leverage), HV Rank ENV factor (duplicate of strike-side IV Percentile).
   - Removes the HV Rank > 50 hard gate and the Trend < 22 hard gate (Trend stays the
     highest-weighted ENV factor — failing alignment costs ~17 pts, doesn't zero ENV).
   - Earnings becomes a DTE-scaled penalty rather than a hard gate.
   - Macro hold becomes a 0.85× score multiplier rather than a display-only flag.
   - Documents DITM in `SCORING_REFERENCE.md` at the same time (project hard rule).
   - **Accepted.**

3. **Move DITM scoring to `services/scoring/` alongside CSP/CC.**
   - Pros: structural symmetry with v3 CSP/CC; the docstring in `scoring/env.py`
     already says DITM "intentionally not in this module yet."
   - Cons: increases blast radius of this commit; the inline-in-ditm_service.py
     placement is functional. Deferred to a follow-up cleanup commit.
   - **Deferred.**

## Decision

### v3 ENV (5 factors, 100 pts)

| Factor | Weight | Direction-aware? | Notes |
|--------|-------:|:-----------------:|-------|
| Trend Strength | 25 | yes (long-only) | P>SMA50>SMA200=25, P>SMA50=15, SMA50>SMA200=8, above-SMA200=4 |
| 200d Return | 25 | yes (long-only) | ≥25%=full, smooth ramp from 0 |
| 52W High Distance (FLIPPED) | 20 | yes (long-only) | ≤5%=20 (full), smooth taper to 0 at 30% |
| Weekly RSI(14) | 15 | yes (long-only) | sweet 50–65; 35–40 in strong trend = 9 (pullback entry) |
| Chain Liquidity | 15 | no | log10(median_OI) / log10(500) × 15 |

**Earnings penalty (DTE-scaled, audit #9):**

| Days to earnings | Penalty |
|------------------|---------|
| ≤ 7 | −15 × min(1, 30 / dte) |
| 8–14 | −7 × min(1, 30 / dte) |
| > 14 or none | 0 |

A 365-DTE LEAP with earnings in 5 days incurs ≈ −1.2 ENV. A 30-DTE position with
earnings in 5 days incurs the full −15.

**Hard gates removed:**
- HV Rank > 50 → ENV = 0  (audit #2)
- Trend < 22 pts → ENV = 0  (audit #6 + redundant with the 25-pt Trend factor)
- Earnings ≤ 7 days → ENV = 0  (audit #9 — replaced by DTE-scaled penalty above)

### v3 Strike (5 factors, 100 pts)

| Factor | Weight | Notes |
|--------|-------:|-------|
| Δ (delta position) | 20 | sweet 0.80–0.85; smooth ramps |
| **Leverage (NEW)** | 25 | `δ × price / mid` · sweet 2.5–3.5× · audit #1 |
| Extrinsic % | 25 | `<2% = full` · drops Theta% (audit #4 redundancy) |
| Bid-Ask Spread % | 20 | `≤2% = full` |
| IV Percentile | 10 | inverted (low = full credit · cheap vol for buyers) |

**Dropped:**
- Annualised Theta% (audit #4 — same signal as Extrinsic%, ~90% correlated)
- Capital Efficiency (audit #1 — replaced by Leverage which includes delta)
- HV Rank as ENV factor (audit #5 — duplicate of strike-side IV Percentile)

### Final blend and macro multiplier

```
final_score = (0.5 × env_score + 0.5 × strike_score) × macro_mult
macro_mult  = 0.85 if macro_hold else 1.0
```

`macro_hold` = `(VIX ≥ 25 AND vix_5d_change > 0) OR (SPY < SMA200)` — same gate as v2,
but the consequence is now a 15% score demotion instead of a display-only banner. The
banner is still rendered.

### Score tiers (aligned with CSP/CC v3)

| Score | Label | Color | Action |
|-------|-------|-------|--------|
| ≥ 75 | Strong | green | Take it, normal size |
| 65–74 | Solid | light-green | Take it, understand the weakness |
| 55–64 | Moderate | amber | Only with strong conviction |
| 45–54 | Weak | orange | Usually skip |
| < 45 | Avoid | red | Skip |

"Take it" threshold = **65** (same as CSP/CC). The v2 frontend used 75/65/55/45 in the
table but documented 80/65/50/35 in the legend (audit #11). Both are aligned to 75/65/55/45.

## Consequences

### Positive

- **Leverage thesis is now scored.** The single most important DITM metric drives 25%
  of strike score directly.
- **Universe coverage restored.** With the HV Rank > 50 hard gate removed, AI/momentum
  names now compete on score directly. Empirical change: NVDA at HV Rank 60 in a
  trending bull tape goes from ENV=0 to a typical ENV in the 60–80 range.
- **Redundancy eliminated.** Theta% and Cap Eff were measuring signals already captured
  by Extrinsic% and Leverage; HV Rank ENV was a duplicate of IV Percentile Strike.
- **Macro discipline is real.** A user scanning during a macro-hold regime now sees
  scores demoted 15% — consistent with the capital-preservation thesis that justifies
  the macro gate's existence.
- **DTE-aware earnings.** Long LEAPS no longer fail because of an earnings print 5 days
  out; short DTE positions still get full protection.
- **Methodology lockstep.** `SCORING_REFERENCE.md` now has a full DITM section that
  matches code 1:1.

### Negative

- **Score distribution shifts.** v2 top scores were typically 70–85 (gated names hit
  ENV=0; survivors clustered high). v3 top scores will spread wider with both Trend-
  failing and HV-elevated names earning partial scores. Tier thresholds may need a
  follow-up calibration after observing live output for a week.
- **No A/B period.** Hard cutover; no toggle to compare v2 and v3.
- **Theta is no longer surfaced as a scored factor.** The frontend column remains
  populated for diagnostic visibility (theta is computed regardless), but the user
  can't see "Theta is dragging this score down" in the breakdown anymore. Acceptable:
  Extrinsic% conveys the same information.

### Neutral

- **Final-score range unchanged** at [0, 100]. Final blend stays 0.5/0.5.
- **Universe selection unchanged.** Still scans the user-selected universe via
  `MOMENTUM_UNIVERSE` / curated lists. A future ADR may introduce a `_DITM_LIQUID`
  curated list (audit open question #4).

## Open Questions / Follow-up

1. **Backtest validation.** All score distribution claims above are theoretical. A
   2020–2025 walk-forward on the AI-buildout universe would size the actual impact.
2. **Move scoring to `services/scoring/`.** Deferred from this ADR. The `env.py` and
   `strike.py` docstrings can drop their "DITM intentionally not in this module yet"
   notes once that move happens.
3. **IV term-structure as a second vol factor.** The audit recommended replacing the
   duplicate HV percentile with IV term-structure or skew. v3 keeps a single vol
   factor (IV Percentile, 10 pts). A future ADR may add term-structure once the data
   layer captures front-month vs LEAP IV.
4. **Earnings density across LEAP life (audit #14).** A 365-DTE LEAP crosses 1–4
   earnings prints; v3 only sees the next one. Follow-up: sum
   `Σ 1/(days_i + 30)` weighted earnings density.
5. **`_DITM_LIQUID` curated universe.** The current `MOMENTUM_UNIVERSE` mixes liquid
   mega-caps with sub-$10B names where LEAPS are illiquid (RXRX, IONQ, S). A separate
   curated list would reduce the % of scans that fail on bid-ask alone.
6. **Tier recalibration.** Confirm the documented 75/65/55/45 thresholds match real
   v3 score distributions after a week of live output.

## References

- DITM quant-trader diagnostic (May 2026): captured in conversation transcript.
- [ADR-0007](0007-scoring-v3-lean-model.md) — CSP/CC v3 lean model.
- [SCORING_REFERENCE.md](../../SCORING_REFERENCE.md) — canonical methodology (now
  includes DITM section).
- [backend/services/ditm_service.py](../../backend/services/ditm_service.py) — v3
  implementation (inline; future ADR may move to `services/scoring/`).
