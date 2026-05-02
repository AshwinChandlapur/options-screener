# Scoring Reference (CSP & CC) — v3

> Single source of truth for the screener scoring system. Every weight and threshold listed
> here is mirrored in code by `ENV_WEIGHTS` and `STRIKE_WEIGHTS` in
> [backend/services/scoring/config.py](backend/services/scoring/config.py). The frontend
> `SCORE_LEGEND` arrays in [CspInput.tsx](frontend/src/components/CspInput.tsx) and
> [CcInput.tsx](frontend/src/components/CcInput.tsx) mirror this document.

> **v3 lean model** (May 2026, see [ADR-0007](docs/adr/0007-scoring-v3-lean-model.md)) reduced
> the model from 14 factors to 8 to remove correlated and inert signals identified by the
> quant-trader diagnostic. Dropped: HV Rank, SMA Alignment, DTE Sweet Spot, EM Buffer, %OTM,
> S/R Distance. The fields `em_buffer_pct`, `dist_pct`, and `otm_pct` are still computed and
> returned in the response payload for diagnostic visibility but contribute 0 to the score.

## Final score formula

```
final_score = 0.4 × env_score + 0.6 × strike_score
```

Tiers (unchanged from v2):

| Tier     | Range  | Color  | Meaning |
|----------|--------|--------|---------|
| Strong   | ≥ 70   | green  | All signals aligned, chain liquid, executable |
| Moderate | 45–69  | amber  | Most signals ok, some weakness in env or execution |
| Weak     | < 45   | red    | Poor IV env, execution risk, earnings overlap, or illiquid chain |

Both `env_score` and `strike_score` cap at 100, so `final_score` ∈ [0, 100].

---

## ENV score (max 100)

`compute_env_score(..., direction='csp'|'cc', iv_stale=False)` in
[backend/services/scoring/env.py](backend/services/scoring/env.py).

| Factor             | Weight | CSP / CC differs? |
|--------------------|-------:|:-----------------:|
| IV / HV Ratio      |  35    | no                |
| Trend (52W dist)   |  25    | **yes**           |
| RSI(14)            |  20    | **yes**           |
| Chain Median OI    |  20    | no                |
| Earnings in DTE    | −15    | no (penalty)      |
| **Total**          | **100**| (Earnings is a deductible penalty) |

### IV / HV Ratio (35 pts)

```
iv_hv_ratio = yfinance_IV / HV_30d
```

Measures whether options are priced rich or cheap relative to actual recent movement.
IV > HV is the seller's edge. Recalibrated in v3 from 28 → 35 pts: with HV Rank dropped,
IV/HV becomes the primary volatility signal.

| Bucket      | Pts                |
|-------------|--------------------|
| < 0.8       | 0                  |
| 0.8–1.0     | linear → 5         |
| 1.0–1.1     | linear 5 → 12.5    |
| 1.1–1.2     | linear 12.5 → 22.5 |
| 1.2–1.3     | linear 22.5 → 35   |
| ≥ 1.3       | 35                 |

**Stale-IV flag:** trigger is `(IV is NaN) or (IV ≤ 0.01)`. When `iv_stale=True`, IV/HV pts
are forced to 0 and the row is annotated with `iv_stale: true` in the API response.

### Trend / 52W High Distance (25 pts) — direction-aware

Replaces the v2 SMA Alignment (15) + 52W (10) into a single direction-aware Trend factor.
SMA was a redundant signal under the lean model; the 52W direction-aware curve captures
the same trend information with smooth math.

```
dist       = (Closeₜ − max(Close, 252d)) / max(Close, 252d) × 100
pct_below  = abs(min(dist, 0))
```

**CSP curve** (rewards strength near the 52W high — uptrend reduces put assignment risk):

| pct_below     | Pts                          |
|---------------|------------------------------|
| ≤ 5%          | 25                           |
| 5–10%         | linear 25 → 18.333           |
| 10–20%        | linear 18.333 → 11.667       |
| 20–30%        | linear 11.667 → 0            |
| > 30%         | 0                            |

**CC curve** (penalizes near-high — assignment risk; rewards 5–15% consolidation):

| pct_below     | Pts                          |
|---------------|------------------------------|
| ≤ 5%          | **0** (was 4 in v2 — fix #5) |
| 5–15%         | linear 0 → 25                |
| 15–25%        | linear 25 → 10               |
| 25–35%        | linear 10 → 0                |
| > 35%         | 0                            |

> **Cliff fix #5:** in v2 the CC ≤5% bucket paid 4 pts (40% of the factor cap), which
> contradicted the assignment-risk thesis for call writers. v3 zeroes this bucket so
> near-52W-high names are correctly penalized.

### RSI(14) (20 pts) — direction-aware

Wilder-smoothed RSI(14):

```
delta    = Close.diff()
avg_gain = EWM(alpha=1/14) of gains
avg_loss = EWM(alpha=1/14) of losses
RSI      = 100 − 100 / (1 + avg_gain / avg_loss)
```

**CSP curve:**

| Bucket        | Pts                |
|---------------|--------------------|
| 42–62         | 20                 |
| 35–42         | linear 0 → 20      |
| 62–75         | linear 20 → 0      |
| < 35 or > 75  | 0                  |

> **Cliff fix #2:** v2 awarded a flat 2 pts for RSI 30–35, then jumped to 6 pts at exactly
> RSI=35 (a 4-pt cliff). v3 removes the 30–35 floor so the 35–42 ramp starts continuously
> at 0.

**CC curve** (sweet spot shifted lower; ceiling extended from 70 to 75):

| Bucket        | Pts                |
|---------------|--------------------|
| 38–58         | 20                 |
| 30–38         | linear 0 → 20      |
| 58–75         | linear 20 → 0      |
| < 30 or > 75  | 0                  |

> **Audit finding #8:** the v2 CC ceiling decay 58→70 was knife-edged, sending NVDA-style
> momentum names with RSI 72 to 0 pts. v3 extends the upper bound to 75 so AAPL/MSFT-style
> names in normal trends earn meaningful points.

### Chain Median OI (20 pts)

Median open interest across the working-delta band (puts: 0.10 < |Δ| < 0.40, calls
0.10 < Δ < 0.40):

```
pts = min(log10(OI) / log10(5000), 1.0) × 20
```

A circuit-breaker for illiquid chains. Saturates near 20 for any liquid name; gives
small-cap chains partial credit on a log scale. Rescaled from 8 in v2.

### Earnings penalty (−15 pts)

Binary flag — `True` if the company's next earnings announcement falls within the option's
DTE window. Applied as a flat deduction on top of the env score.

```
earnings_within_dte = True if 0 ≤ (earnings_date − today).days ≤ DTE
Source: yfinance calendarEvents.earnings
```

---

## CSP Strike score (max 100)

`compute_csp_strike_score(...)` in [backend/services/scoring/strike.py](backend/services/scoring/strike.py).

| Factor               | Weight |
|----------------------|-------:|
| Δ (delta position)   |  20    |
| Bid-Ask Spread %     |  30    |
| OI / Volume (per strike) | 15 |
| Annualized ROC       |  35    |
| **Total**            | **100**|

### Δ (delta position) — 20 pts

Symmetric bell around `ideal_delta = -0.225`. The CSP delta gate
`delta_range=(-0.35, -0.10)` is enforced by the candidate filter upstream; this factor
awards points based on offset from the ideal.

```
offset = abs(delta - (-0.225))
```

| offset            | Pts |
|-------------------|----:|
| ≤ 0.025  (Δ in [-0.25, -0.20]) | 20 |
| ≤ 0.075  (gate inner band)     | 13 |
| ≤ 0.125  (gate outer band)     |  7 |
| outside the gate               |  0 |

> **Audit fix #7:** v2 awarded the aggressive wing (Δ < -0.30) 5.83 pts but the conservative
> wing (-0.15 < Δ ≤ -0.10) only 5.0 pts despite equal distance from the ideal. v3 is
> symmetric — both wings score equally at the same offset.

### Bid-Ask Spread % — 30 pts

```
spread_pct = (ask − bid) / mid × 100   where mid = (bid + ask) / 2
```

Lower spread = better execution. Wide spreads erode realized premium on entry and every roll.

| Bucket    | Pts            |
|-----------|----------------|
| ≤ 1%      | 30             |
| 1–3%      | linear 30 → 20 |
| 3–5%      | linear 20 → 11 |
| 5–8%      | linear 11 → 3  |
| > 8%      | 0              |

Rescaled from 23 in v2.

### OI / Volume (per strike) — 15 pts

Per-strike circuit-breaker. Uses volume during US market hours (9:30–16:00 ET weekday),
otherwise falls back to openInterest.

| Bucket      | Pts            |
|-------------|----------------|
| ≥ 1000      | 15             |
| 500–1000    | linear 10.5 → 15 |
| 200–500     | linear 6 → 10.5 |
| 100–200     | linear 0 → 6   |
| < 100       | 0              |

Rescaled from 5 in v2.

### Annualized ROC — 35 pts

```
capital_per_share = strike − credit
ROC               = (credit / capital_per_share) × (365 / DTE) × 100
```

For CSP, capital at risk = strike − credit (cash secured minus premium received).

| ROC %       | Pts                  |
|-------------|----------------------|
| ≥ 20%       | 35                   |
| 14–20%      | linear 24.5 → 35     |
| 8–14%       | linear 14 → 24.5     |
| 4–8%        | linear 3.5 → 14      |
| 2–4%        | linear 0 → 3.5       |
| < 2%        | 0                    |

> **Cliff fix #6:** v2 awarded a flat 1 pt at ROC = 4 then jumped to 0 below. v3 adds a
> 2–4% ramp for continuous behavior.

The API response exposes the raw value as `roc_annualized`.

---

## CC Strike score (max 100)

`compute_cc_strike_score(...)` in [backend/services/scoring/strike.py](backend/services/scoring/strike.py).

| Factor               | Weight |
|----------------------|-------:|
| Δ (delta position)   |  20    |
| Bid-Ask Spread %     |  30    |
| OI / Volume          |  15    |
| Annualized ROC       |  35    |
| **Total**            | **100**|

### Divergences from CSP

The CC scorer uses the **same 8-factor structure, the same weights, and the same curves**
as CSP. Only two inputs differ:

1. **Δ ideal**: `+0.225` (sign-flipped from CSP). Symmetric bell math is identical.
2. **ROC capital basis**: `current_price − credit`. The CC writer's capital at risk is the
   value of the underlying held to write the call, not the strike. The ROC scoring curve
   is otherwise identical to CSP.

All other factors (Bid-Ask, OI/Volume) are direction-agnostic and share the same code path
via shared helpers in `strike.py`.

---

## Diagnostic-only fields (not scored in v3)

The following fields continue to be computed and returned in the API response so the
frontend table columns remain populated, but they contribute **0 to the score**:

| Field           | What it shows |
|-----------------|---------------|
| `em_buffer_pct` | (0.5×EM-referenced) sigmas_outside × 100. Positive = strike outside the 0.5σ boundary. |
| `otm_pct`       | Raw `(S − K) / S × 100` for CSP, `(K − S) / S × 100` for CC. |
| `dist_pct`      | `None` in v3 (S/R was dropped). Kept as nullable field for response back-compat. |

These are visible in the strike table for context but do not influence ranking. ADR-0007
captures the rationale.

---

## Hard filters (gate before scoring)

These constraints filter candidates *before* scoring, not as scored factors:

| Filter | Source | Effect |
|--------|--------|--------|
| Delta gate | `CSP_CONFIG.delta_range = (-0.35, -0.10)` / `CC_CONFIG.delta_range = (0.10, 0.35)` | Strikes outside the gate are excluded |
| DTE window | User-supplied `min_dte` / `max_dte` | Expirations outside the window skipped |
| Capital gate (CSP only) | User-supplied `max_capital` | Strikes requiring `strike × 100 > max_capital` skipped (after OI aggregation — see [ADR-0005](docs/adr/0005-csp-capital-constraint.md)) |
| Stale IV | IV is NaN or ≤ 0.01 | IV/HV pts forced to 0 (row not dropped, just flagged) |

> **Future work** (not in v3): a hard filter on EM Buffer (reject candidates with
> `sigmas_outside < 0` against the 0.5×EM boundary) was considered to replace the dropped
> EM Buffer scored factor. Deferred — the delta gate already excludes most strikes that
> would fail this check at the configured ideal_delta. See ADR-0007 § Open questions.

---

## Endpoints accepting `max_capital` (CSP only)

| Endpoint                    | Parameter shape                                            |
|-----------------------------|------------------------------------------------------------|
| `POST /api/screener/csp`    | JSON body field `maxCapital` (float, optional)             |
| `GET /api/screener/csp/scan`| Query parameter `max_capital` (float, optional, `ge=100`)  |

The `/csp/scan` cache key includes `max_capital`:

```
cache_key = "{universe}:{top_n}:{min_dte}:{max_dte}:{max_capital}"
```

See [ADR-0004](docs/adr/0004-scan-result-caching.md) and
[ADR-0005](docs/adr/0005-csp-capital-constraint.md) for design rationale.

---

## What changed in v3 (vs v2)

ADR-0007 captures the full rationale; this is the changelog summary.

**Dropped factors (6 total):**
1. **HV Rank (22 pts)** — correlated with IV/HV; structurally undervalued low-vol names
   (KO, PG, JNJ).
2. **SMA Alignment (15 pts)** — collapsed into Trend; redundant signal.
3. **DTE Sweet Spot (7 pts)** — should be a hard filter (already enforced via min/max DTE),
   not a scored factor.
4. **EM Buffer (20 pts)** — deterministic at the configured ideal_delta; inert signal that
   added redundancy with Δ and %OTM. Still computed for diagnostic display.
5. **%OTM from Spot (9 pts)** — deterministic function of Δ and IV; redundant with Δ.
   Still computed for diagnostic display.
6. **S/R Distance (18 pts)** — fragile swing-detection heuristic; high implementation cost
   for low signal value.

**Rescaled factors:**
- IV/HV: 28 → 35 (primary vol signal)
- Trend (52W direction-aware): 10 → 25 (absorbs SMA's role)
- RSI: 10 → 20
- Chain OI: 8 → 20
- Δ: 15 → 20 (now symmetric — fixes audit #7)
- Bid-Ask: 23 → 30
- OI/Volume: 5 → 15
- ROC: 10 → 35

**Cliff fixes (in surviving curves):**
- **#2 RSI-CSP**: removed 30–35 floor of 2 pts that created a 4-pt cliff at RSI=35.
- **#5 CC ≤5% near-high**: dropped from 4 pts to 0 (full penalty for assignment risk).
- **#6 ROC**: added 2–4% ramp to remove the small cliff at ROC=4.
- **#8 CC RSI ceiling**: extended upper bound from 70 to 75 for smoother decay.

**Audit-driven fixes:**
- **#7 Δ asymmetry**: aggressive and conservative wings now score equally at the same
  offset from ideal.
- **44%-redundancy stack**: removing EM Buffer + %OTM eliminates the v2 issue where Δ +
  EM + %OTM all measured the same delta-position signal at the configured ideal_delta.

**Weight integrity:** ENV totals 100 (35+25+20+20). Strike totals 100 (20+30+15+35).
