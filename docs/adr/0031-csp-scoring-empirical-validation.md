# ADR-0031: CSP Scoring v3.3 — Empirical Validation (Phase 2 of May 2026 Audit)

- **Status**: Accepted
- **Date**: 2026-05-20
- **Builds on**: [ADR-0029](0029-scoring-v33-ivp-factor.md) (IVP factor swap), Phase 1 audit fixes (commit `fe62a12`)

## Context

The May 2026 CSP audit graded the scoring system **3.5 / 10**, with the headline
finding:

> *Every weight in the scoring function is a prior with no posterior. Until a
> backtest exists that demonstrates monotonic relationship between score and
> realised outcome, the system is calibrated on aesthetics.*

The audit also flagged **HIGH-4**: that the three trend-cluster factors —
`env_Tr` (15 pts), `env_SMA` (5 pts), `env_SLP` (5 pts) — might be triple-counting
the same underlying signal, with predicted pairwise $|r| \ge 0.6$.

Phase 1 of the audit (Section A–F) was completed in commit `fe62a12`: six
"stop-the-bleeding" fixes covering broken test coverage, the silent stale-IV
zeroing bug, IVP curve calibration, and DITM/CC parity. Phase 2 (Section G) is
the empirical validation work: prove or falsify the monotonicity claim, the
factor-independence claim, and intraday rank stability.

## Decision

Build three empirical tools, run them, and accept their verdicts as the v3.3
calibration baseline.

### Tools

| Script | Purpose | Mode |
|---|---|---|
| [scripts/backtest_csp.py](../../scripts/backtest_csp.py) | Synthetic Black–Scholes walk-forward over the live `MOMENTUM_UNIVERSE` | Offline (yfinance OHLCV) |
| [scripts/csp_factor_correlation.py](../../scripts/csp_factor_correlation.py) | Pearson factor matrix from the backtest's per-trade sub-scores | Offline (reads ledger CSV) |
| [scripts/csp_rank_stability.py](../../scripts/csp_rank_stability.py) | Intraday Spearman rank stability via the live `process_symbol` path | Live (requires market hours) |

### Backtest methodology (documented in script docstring)

The backtest is a **synthetic, walk-forward, single-strike-per-week** sampler.
At each Monday between `scan_start` and `scan_end`, for each ticker:

1. Pull live OHLCV history; compute `HV(30)` as IV proxy (no historical chain
   data available via yfinance).
2. Build a synthetic strike grid at `[0.85, 0.875, 0.90, 0.925, 0.95, 0.975] × spot`.
3. Price each strike with Black–Scholes (rf = 4.5%, dte = 35).
4. Apply production hard filters: delta ∈ [−0.35, −0.10], strike < spot × 1.02.
5. Score each survivor with the **live** v3.3 scoring functions
   (`compute_env_score`, `_score_delta_symmetric`, `_score_roc`).
6. Pick the max-final-score strike.
7. Roll forward 35 calendar days and resolve:
   $\text{pnl} = 100 \cdot (\text{premium} - \max(0, K - S_T))$,
   $\text{ROC}_{ann} = \frac{\text{pnl}}{100 \cdot (K - \text{premium})} \cdot \frac{365}{\text{dte}} \cdot 100$.

#### Scoring adjustments (necessary)

The backtest scores on **ENV + Δ + ROC only**, omitting two strike-side factors
that require live chain data:

- **BA (Bid–Ask, 25 pts)** — synthetic prices have no spread; would always score 0.
- **LQ (Liquidity, 15 pts)** — no chain → no OI / volume → would always score 0.

Strike score is renormalised to keep the 60-pt strike weight intact:
$$
\text{strike\_score} = (\text{Δ}_\text{pts} + \text{ROC}_\text{pts}) \cdot \frac{100}{60}
$$
Final score retains the production weighting: $\text{final} = 0.4 \cdot \text{ENV} + 0.6 \cdot \text{strike}$.

#### Acknowledged limitations

1. **Bull-regime sample.** 2024-01 → 2026-04 was a strong-trend bull market;
   results overweight regimes that favour short puts.
2. **HV(30) as IV proxy.** HV typically *understates* real IV in calm regimes
   (vol risk premium). Real premium income is likely **higher** than the
   backtest reports, not lower — so the directional findings are conservative.
3. **Survivorship bias.** Today's `MOMENTUM_UNIVERSE` was applied at *every*
   backtest date — names that would have been delisted/removed mid-period
   are not modelled.
4. **No friction.** BA / LQ omitted; assumes mid-fill at synthetic price.
5. **No assignment economics modelled.** ROC formula treats assignment as a
   marked-to-market loss; in practice CSP sellers often hold and recover
   over weeks.

### Factor correlation methodology

The backtest's `Trade` dataclass captures all eight per-factor sub-scores
(`env_IVP`, `env_Tr`, `env_SMA`, `env_SLP`, `env_RSI`, `env_OI`, `strike_Delta`,
`strike_ROC`). [scripts/csp_factor_correlation.py](../../scripts/csp_factor_correlation.py)
computes the full Pearson matrix and tests the HIGH-4 hypothesis at the audit's
$|r| \ge 0.6$ threshold.

### Rank stability methodology

Production-style rankings change with every live scan (IV, mid, OI all move).
[scripts/csp_rank_stability.py](../../scripts/csp_rank_stability.py) captures
snapshots at user-chosen times via the live `services.csp_service.process_symbol`
path (the *same* entry point the API uses), then reports pairwise Spearman
correlation across snapshots. Audit pass threshold: $\rho \ge 0.85$ between any
two snapshots taken on the same trading day.

## Empirical Findings (2026-05-20 run)

### Headline: Monotonicity — **PASS**

12,751 trades across 109 tickers, 2024-01-16 → 2026-04-06:

| Score bucket | n | Mean realised ROC | Median | Win rate | Assign rate |
|---|---:|---:|---:|---:|---:|
| 0–50 | 18 | **−6.3%** | +3.4% | 77.8% | 27.8% |
| 50–65 | 1,243 | **−4.2%** | +11.3% | 79.1% | 24.4% |
| 65–75 (tradeable) | 4,460 | **+3.0%** | +15.7% | 79.9% | 24.4% |
| 75–85 | 5,304 | **+13.4%** | +20.7% | 83.7% | 21.1% |
| 85–100 | 1,726 | **+15.6%** | +22.7% | 86.6% | 19.2% |

- **Strict monotonicity:** YES (−6.3 → −4.2 → +3.0 → +13.4 → +15.6).
- **Spearman ρ(score, realised_ROC) = +0.266**, p ≈ 0 (n = 12,751).
- **65-cutoff Δ:** mean ROC ≥ 65 = +9.7%, < 65 = −4.3%, **Δ = +14.0%**.

The 65-point production cutoff carries real signal: above it the screener has
positive expected ROC; below it, expected ROC is negative.

### HIGH-4 (trend triple-counting) — **NOT CONFIRMED**

Pairwise correlations within the trend cluster (12,751-row Pearson):

| Pair | $r$ | Audit threshold ($\ge 0.6$) |
|---|---:|---|
| `env_Tr` × `env_SMA` | **+0.578** | below |
| `env_Tr` × `env_SLP` | **+0.584** | below |
| `env_SMA` × `env_SLP` | **+0.373** | below |

The three trend factors are positively but moderately correlated. None exceeds
the audit's $|r| \ge 0.6$ "triple-count" threshold. The trend cluster carries
materially independent variance and does *not* collapse to a single factor.

#### Other factor correlations (confirmation findings)

| Pair | $r$ | Reading |
|---|---:|---|
| `env_IVP` × `env_Tr` | −0.06 | IVP is independent of trend |
| `env_IVP` × `env_SMA` | −0.00 | IVP is independent of SMA |
| `env_IVP` × `env_SLP` | −0.08 | IVP is independent of SLP |
| `env_IVP` × `env_RSI` | −0.03 | IVP is independent of RSI |
| `env_Tr` × `env_RSI` | +0.09 | RSI is independent of trend |
| `env_SMA` × `env_RSI` | +0.00 | RSI is independent of SMA |
| `env_SLP` × `env_RSI` | +0.08 | RSI is independent of SLP |
| `strike_Delta` × `strike_ROC` | +0.05 | Δ and ROC measure different things |
| `env_OI` | (N/A) | Zero variance in backtest (no chain data) |

The v3.3 IVP factor swap (ADR-0029) **achieved its independence goal**: IVP is
the most uncorrelated single factor in the system. Max |corr| with any
non-self factor = 0.08.

The negative correlation between trend factors and strike_ROC (~−0.20 to −0.23)
is interpretable: high-trend / low-IVP names have *less* room to fall, so
delta-equivalent strikes price tighter and yield lower annualised ROC.

### Rank stability — **TOOLING DELIVERED, RUN PENDING**

[scripts/csp_rank_stability.py](../../scripts/csp_rank_stability.py) supports
three modes: `capture` (one-shot), `compare` (post-hoc Spearman matrix), and
`loop` (N captures auto-spaced then auto-compare). Smoke-tested 2026-05-20
09:57 ET on five tickers; full run requires scheduled execution during US
market hours and is deferred to the next trading session.

## Consequences

### Positive

- **The audit's #1 finding is empirically refuted.** The scoring function has a
  posterior, and it is positive: the 65-cutoff threshold carries +14.0% mean
  ROC of separation on a 12,751-trade sample.
- **The HIGH-4 remediation is not required.** No structural change to the
  Tr/SMA/SLP factor cluster is justified by the data. The three factors are
  independent enough to keep their separate weights.
- **The v3.3 IVP swap is validated.** IVP is the cleanest independent signal
  in the system, exactly as ADR-0029 predicted.
- **Reusable infrastructure.** All three scripts are CLI-driven and re-runnable.
  They form the empirical baseline for future scoring changes — any ADR that
  modifies a weight must re-run the backtest and report the delta.

### Negative

- **Bull-regime confound.** All findings are conditional on the 2024–2026
  trend regime. A subsequent ADR should re-run the backtest at minimum over
  a 2008–2009 or 2022 drawdown window before relying on the +ROC results for
  bear-market sizing.
- **No BA/LQ in backtest.** The two friction factors carry 40 pts (66.7%) of
  the strike score in production. Their predictive value is *not* validated
  here; the backtest only validates that the **non-friction** subset (ENV + Δ
  + ROC) is monotone.
- **No earnings-penalty validation.** Trades through earnings are not flagged
  in the backtest; the −15-pt earnings penalty is unchanged but unvalidated.

### Neutral

- The backtest ledger CSV (`csp_backtest_full.csv`, ~2 MB, 12,751 rows) is
  reproducible from the script and is **gitignored**. Anyone reviewing this
  ADR should regenerate locally; results should match deterministically given
  the same universe + date range.

## Follow-ups

- [ ] Run `csp_rank_stability.py loop --captures 4 --interval-min 90 --limit 40`
  during a normal trading day. Append findings to this ADR as an addendum.
- [ ] Re-run the backtest over a bear-regime window (e.g. 2007-10 → 2009-06
  using a representative subset of names that existed throughout).
- [ ] Add a **friction-aware** backtest variant once historical option-chain
  data is available (Polygon or similar) so BA / LQ can be evaluated.
- [ ] Add a `scoring_version` filter to the backtest so older v3.2 / v3.1
  ledger artefacts cannot accidentally contaminate v3.3 analyses.
- [ ] Wire the backtest as a CI smoke check that runs monthly and alerts on
  monotonicity regression — the audit's headline test should run forever.

## References

- Audit (full text): see `/memories/session/plan-master.md` (Section G).
- [ADR-0007](0007-scoring-v3-lean-model.md) — original lean model.
- [ADR-0029](0029-scoring-v33-ivp-factor.md) — IVP factor swap that this work validates.
- [SCORING_REFERENCE.md](../../SCORING_REFERENCE.md) — live weight table.
