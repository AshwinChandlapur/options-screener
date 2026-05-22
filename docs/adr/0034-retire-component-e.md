# ADR-0034 — Retire Component E (Market Confirmation) from ACS

- **Status**: Accepted
- **Date**: 2026-05-21
- **Supersedes**: [ADR-0019](0019-narrative-phase6-scorer.md) §5 (Component E design)
- **Related code**: `workers/scorer/scorer.py`, `workers/scorer/main.py`, `workers/scorer/market_confirmation.py`

## Context

ACS Component E was specified in ADR-0019 as "market confirmation" — three normalized
sub-signals that could reward tickers showing external market-side validation of their
Reddit narrative:

| Sub-signal | Weight | Source |
|---|---|---|
| `rs_14d_norm` | 6/15 | 14-day sector-relative price return |
| `opt_ratio_norm` | 5/15 | call volume / call open-interest (nearest expiry) |
| `institutional_norm` | 4/15 | net institutional buying from 13F filings |

Component E defaulted to 0 in all production runs because `market_confirmation.py` was
never wired into a Key Vault secret that enabled it.  However, the design intention was
to populate it in Phase 6.1.

An adversarial audit (May 2026) identified three structural problems that make this
design invalid as currently specified:

### 1 — Causal contamination via `rs_14d_norm`

`rs_14d_norm = clip((r_ticker_14d − r_sector_14d) / 0.20, 0, 1)` is the standard
Jegadeesh-Titman 14-day price momentum factor — the highest-weight sub-signal in
Component E.  This is the same variable that the ACS is intended to predict.  Using the
trailing 14-day return as an input when the forward 30-day return is the outcome creates
a direct channel from the lagged dependent variable into the model.  Any positive IC
produced by the ACS on a backtest would need to be decomposed to determine how much is
explained by this price-momentum sub-signal before any narrative-specific alpha claim
could be made.

### 2 — 13F data staleness

`institutional_13f_norm` reads yfinance `institutional_holders`, which reflects the most
recent 13F SEC filing.  13F filings are due 45 days after each quarter-end, so data can
be anywhere from 1 day to 135 days stale.  An institution that bought shares five months
ago cannot "confirm" a narrative that formed on Reddit in the past 14 days.

### 3 — Options call ratio as a reactive signal

`opt_ratio_norm` measures call volume relative to call open interest over the nearest
expiry.  Options activity is well-documented to be partly reactive to price breakouts
(investors buy calls after a stock moves).  Without a temporal primacy test, this
sub-signal is as likely to lag price as to lead it.

## Decision

Component E is **permanently retired** from the ACS formula.

The 15 points previously allocated to E are redistributed proportionally to the four
remaining components, preserving total max-ACS = 100:

| Component | Old max | New max |
|---|---|---|
| A (attention persistence) | 25 | 30 |
| B (contributor quality)   | 20 | 25 |
| C (narrative strength)    | 20 | 25 |
| D (thesis quality)        | 20 | 20 |
| **Total** | **85** (80 + 5 deferred E) | **100** |

`market_confirmation.py` is retained in the repository as reference for any future
Component E redesign but is no longer called from `main.py`.

## Conditions for Reinstating a Market-Confirmation Component

A future Component E may be reinstated if **all** of the following hold:

1. The replacement signal is **not** a lagged version of the forward return variable
   (i.e. not trailing price return over the prediction horizon or a subset of it).
2. A temporal lead-lag test demonstrates that the candidate sub-signal at T-14 is
   statistically more predictive of return_{T→T+30} than the same signal at T
   (proving it leads rather than confirms price).
3. The signal is sourced from genuinely contemporaneous data (e.g., real-time
   options flow delta, dark-pool print ratio, SEC Form 4 insider buys within 7 days).
4. An ADR documents the causal theory, empirical validation methodology, and IC
   decomposition result.

## Consequences

- **Immediate:** ACS scores will increase slightly for all tickers because the 15 E
  points are redistributed to A/B/C/D.  Historical ACS values in `ticker_timeline`
  documents are therefore not comparable to post-deployment ACS values.  The
  `acs_scored_at` timestamp will indicate which regime each document belongs to.
- **Operational:** `get_market_confirmation()` is no longer called in the scorer's
  hot path, eliminating 3 yfinance network calls per ticker per scorer run.  This
  reduces scorer wall-clock time and yfinance dependency.
- **Testing:** Tests that assert on `components["E"]` must be updated to expect
  `components` to contain only keys `A`, `B`, `C`, `D`.
