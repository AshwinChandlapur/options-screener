# Scoring Reference (CSP & CC)

> Single source of truth for the screener scoring system. Every weight/threshold listed here
> is mirrored in code by the constants `ENV_WEIGHTS` and `STRIKE_WEIGHTS` in
> `backend/services/technical_service.py`. The frontend `SCORE_LEGEND` arrays in
> `CspInput.tsx` / `CcInput.tsx` mirror this document.

## Final score formula

```
final_score = 0.4 × env_score + 0.6 × strike_score
```

Tiers (unchanged from previous revision; recalibration deferred):

| Tier     | Range  | Color  | Meaning |
|----------|--------|--------|---------|
| Strong   | ≥ 70   | green  | All signals aligned, chain liquid, executable |
| Moderate | 45–69  | amber  | Most signals ok, some weakness in env or execution |
| Weak     | < 45   | red    | Poor IV env, execution risk, earnings overlap, or illiquid chain |

Both `env_score` and `strike_score` cap at 100, so `final_score` ∈ [0, 100].

---

## ENV score (max 100)

`compute_env_score(..., direction='csp'|'cc', dte=int|None, iv_stale=False)` in
`backend/services/technical_service.py`.

| Factor             | Weight | CSP / CC differs? |
|--------------------|-------:|:-----------------:|
| HV Rank            |  22    | no                |
| IV / HV Ratio      |  28    | no                |
| SMA Alignment      |  15    | no                |
| 52W High Distance  |  10    | **yes**           |
| RSI(14)            |  10    | **yes**           |
| Chain Median OI    |   8    | no                |
| DTE Sweet Spot     |   7    | no                |
| Earnings in DTE    | −15    | no (penalty)      |
| **Total**          | **100**| (Earnings is a deductible penalty) |

### HV Rank (22 pts)

> **Note:** Previously labeled "IV Rank" in the UI but always computed from 30-day HV ranked
> over 252 days (true ATM IV history is not stored). Renamed to "HV Rank" to reflect what is
> actually measured. Behavior unchanged besides rescale.

```
hv_rank = (HV_today − HV_min_252) / (HV_max_252 − HV_min_252) × 100
HV      = std(log(Closeₜ / Closeₜ₋₁), 30d) × √252
```

| Bucket       | Pts            |
|--------------|----------------|
| < 20         | 0              |
| 20–40        | linear → 6.6   |
| 40–60        | linear → 13.2  |
| 60–80        | linear → 18.33 |
| ≥ 80         | 22             |

### IV / HV Ratio (28 pts)

```
iv_hv_ratio = yfinance_IV / HV_30d
```

| Bucket    | Pts            |
|-----------|----------------|
| < 0.8     | 0              |
| 0.8–0.9   | linear → 2.8   |
| 0.9–1.1   | linear → 6.72  |
| 1.1–1.4   | linear → 14    |
| 1.4–1.7   | linear → 22.4  |
| ≥ 1.7     | 28             |

**Stale-IV flag (changed):** previously the trigger was `IV < 0.15`, which silently treated
legitimately low-vol names (KO, T-bills, utilities) as stale. New trigger:

```
iv_stale = (IV is NaN) or (IV ≤ 0.01)
```

When `iv_stale=True`, IV/HV pts are forced to 0 and the row is annotated with
`iv_stale: true` in the API response so the UI can surface the warning.

### SMA Alignment (15 pts)

| Condition                    | Pts |
|------------------------------|-----|
| Price > SMA50 > SMA200       | 15  |
| Price > SMA50 only           |  9  |
| SMA50 > SMA200 only          |  5  |
| else                         |  0  |

### 52W High Distance (10 pts) — direction-aware

```
dist       = (Closeₜ − max(Close, 252d)) / max(Close, 252d) × 100
pct_below  = abs(min(dist, 0))
```

**CSP curve** (rewards proximity to the high — uptrend):

| pct_below     | Pts            |
|---------------|----------------|
| ≤ 5%          | 10             |
| 5–10%         | linear → 7.33  |
| 10–20%        | linear → 4.67  |
| 20–30%        | linear → 2     |
| > 30%         | 0              |

**CC curve** (rewards mild consolidation, penalizes near-high — assignment risk):

| pct_below     | Pts            |
|---------------|----------------|
| ≤ 5%          | 4              |
| 5–15%         | linear 4 → 10  |
| 15–25%        | linear 10 → 6  |
| 25–35%        | linear 6 → 2   |
| > 35%         | 0              |

### RSI(14) (10 pts) — direction-aware

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
| 42–62         | 10                 |
| 35–42         | linear → 6         |
| 62–75         | linear → 0         |
| 30–35         | 2                  |
| < 30 or > 75  | 0                  |

**CC curve** (sweet spot shifted lower, ceiling decay steeper — overheated names blow
through call strikes):

| Bucket        | Pts                |
|---------------|--------------------|
| 38–58         | 10                 |
| 30–38         | linear 4 → 10      |
| 58–70         | linear 10 → 0      |
| < 30 or > 70  | 0                  |

### Chain Median OI (8 pts)

Median open interest across the working-delta band (puts: 0.10 < |Δ| < 0.40, calls
0.10 < Δ < 0.40):

```
pts = min(log10(OI) / log10(5000), 1.0) × 8
```

Effectively a circuit-breaker for illiquid chains; saturates near 8 for any liquid name.

### DTE Sweet Spot (7 pts) — new

Theta decay accelerates non-linearly approaching expiry; the 30–45 DTE band balances
gamma exposure against decay rate.

| DTE                            | Pts |
|--------------------------------|----:|
| 30 ≤ DTE ≤ 45                  | 7.0 |
| 21–30 or 45–60                 | 4.2 |
| 14–21 or 60–75                 | 2.1 |
| < 14 or > 75 (or unknown)      | 0   |

### Earnings in DTE (−15 pts)

```
earnings_within_dte = 0 ≤ (earnings_date − today).days ≤ DTE
```

If true, subtract 15 from the env score (can produce negative env contributions).

---

## CSP Strike score (max 100)

`compute_csp_strike_score(..., credit=float|None)` in `backend/services/technical_service.py`.

| Factor             | Weight |
|--------------------|-------:|
| Delta              |  15    |
| Dist vs Support    |  18    |
| Exp Move Buffer    |  20    |
| % OTM from Spot    |   9    |
| Bid-Ask Spread     |  23    |
| OI / Volume        |   5    |
| Annualized ROC     |  10    |
| **Total**          | **100**|

### Delta (15 pts)

Black-Scholes put delta. Sweet spot is −0.20 → −0.25 (≈ 20–25% ITM probability).

| Δ band                 | Pts    |
|------------------------|--------|
| −0.20 → −0.25          | 15     |
| ±1 absolute band       | 10     |
| −0.10 → −0.15          | 5      |
| < −0.30                | 5.83   |

### Dist vs Support (18 pts)

Volume-profile support, 6-month (126-day) lookback. Distance = nearest support level
below strike.

| Condition                              | Pts        |
|----------------------------------------|------------|
| ≤ 5% below strike                      | 18 → 10    |
| 5–10% below strike                     | 10 → 0     |
| > 10% below strike                     | 0          |
| All support above strike (uptrend)     | 7 (bonus)  |

### Exp Move Buffer (20 pts)

```
EM             = S × σ × √(DTE/365)
EM_lower       = S − EM
sigmas_outside = (EM_lower − strike) / EM
```

| sigmas_outside       | Pts |
|----------------------|----:|
| ≥ 0.2σ outside       | 20  |
| 0 to 0.2σ outside    | 13  |
| −0.1 to 0σ           |  5  |
| deeper inside        |  0  |

### % OTM from Spot (9 pts)

```
otm_pct = (S − K) / S × 100
```

| Bucket    | Pts  |
|-----------|------|
| ≥ 15%     | 9    |
| ≥ 10%     | 6.75 |
| ≥ 5%      | 4.5  |
| ≥ 2%      | 1.5  |
| < 2%      | 0    |

### Bid-Ask Spread (23 pts)

```
spread_pct = (ask − bid) / mid × 100   where mid = (bid + ask) / 2
```

| Bucket    | Pts   |
|-----------|-------|
| ≤ 1%      | 23    |
| ≤ 3%      | 15.33 |
| ≤ 5%      | 8.52  |
| ≤ 8%      | 2.13  |
| > 8%      | 0     |

### OI / Volume (5 pts)

Per-strike circuit-breaker. Uses volume during US market hours (9:30–16:00 ET weekday),
otherwise falls back to openInterest.

| Bucket    | Pts |
|-----------|----:|
| ≥ 1000    | 5   |
| ≥ 500     | 3.5 |
| ≥ 200     | 2   |
| < 200     | 0   |

### Annualized ROC (10 pts) — new

```
capital_per_share = strike − credit
ROC               = (credit / capital_per_share) × (365 / DTE) × 100
```

| ROC %       | Pts            |
|-------------|----------------|
| ≥ 30%       | 10             |
| 20–30%      | linear 7 → 10  |
| 12–20%      | linear 4 → 7   |
| 6–12%       | linear 1 → 4   |
| < 6%        | 0              |

The API response exposes the raw value as `roc_annualized`. The curve is provisional —
plan to recalibrate once a basket of real strikes has been observed.

---

## CC Strike score (max 100)

`compute_cc_strike_score(..., credit=float|None)` in `backend/services/technical_service.py`.

| Factor               | Weight |
|----------------------|-------:|
| Delta                |  15    |
| Dist vs Resistance   |  18    |
| Exp Move Buffer      |  20    |
| % OTM from Spot      |   9    |
| Bid-Ask Spread       |  23    |
| OI / Volume          |   5    |
| Annualized ROC       |  10    |
| **Total**            | **100**|

### Delta (15 pts)

Black-Scholes call delta. Sweet spot is +0.20 → +0.25.

| Δ band                 | Pts    |
|------------------------|--------|
| +0.20 → +0.25          | 15     |
| ±1 absolute band       | 10     |
| +0.10 → +0.15          | 5      |
| > +0.30                | 5.83   |

### Dist vs Resistance (18 pts) — unchanged

Volume-profile resistance, 6-month (126-day) lookback.

```
nearest_R = min(resistances above current price)
gap_pct   = (nearest_R − strike) / strike × 100   (negative = R below strike)
```

| Condition                                   | Pts        |
|---------------------------------------------|------------|
| gap ≤ −20% (uncharted territory)            | 3          |
| −20% < gap ≤ −10%                           | 3 → 18     |
| −10% < gap ≤ 0%                             | 18         |
| All R ≤ strike, gap within 10% (ceiling stack) | +5 bonus |
| 0% < gap ≤ 5%                               | 18 → 10    |
| 5% < gap ≤ 10%                              | 10 → 0     |
| gap > 10%                                   | 0          |

### Exp Move Buffer (20 pts)

```
EM             = S × σ × √(DTE/365)
EM_upper       = S + EM
sigmas_outside = (strike − EM_upper) / EM
```

Same tier table as CSP but oriented to upside ceiling.

### % OTM from Spot (9 pts)

```
otm_pct = (K − S) / S × 100
```

Same tier table as CSP.

### Bid-Ask Spread (23 pts)

Same as CSP.

### OI / Volume (5 pts)

Same as CSP.

### Annualized ROC (10 pts) — new

CC capital basis = current price (simplification — does not track per-position cost basis).

```
capital_per_share = current_price − credit
ROC               = (credit / capital_per_share) × (365 / DTE) × 100
```

Tier table same as CSP.

---

## What changed from the prior revision

1. **HV Rank rename** — was "IV Rank" but always derived from HV; now honestly labeled.
   Rescaled 30 → 22 to make room for new factors.
2. **IV / HV bumped** — 25 → 28; gives the genuine IV-vs-realized signal more weight.
3. **Stale-IV trigger fixed** — `IV < 0.15` (false positive on KO etc.) → `NaN or ≤ 0.01`.
   Now also surfaces an `iv_stale` flag in the API response.
4. **52W direction-aware** — CSP keeps reward-near-high curve (rescaled 15 → 10).
   CC gets a smooth-ramp consolidation curve (4 → 10 → 6 → 2 → 0) so near-high names
   correctly score lower for call selling.
5. **RSI direction-aware** — CC sweet spot moves from 42–62 to 38–58, ceiling decay
   sharpens (10 → 0 over 12 RSI pts vs 13).
6. **DTE Sweet Spot** — new 7-pt env factor rewarding the 30–45 DTE band where theta
   acceleration peaks.
7. **Chain OI bumped** — 5 → 8; gives small-cap chains more discrimination room.
8. **Δ rescale** — 18 → 15.
9. **Bid-Ask rescale** — 27 → 23.
10. **% OTM rescale** — 12 → 9.
11. **Annualized ROC** — new 10-pt strike factor; previously the strike score scored
    safety and execution but never the actual yield.

ENV totals to exactly 100 (22+28+15+10+10+8+7). Strike totals to exactly 100
(15+18+20+9+23+5+10). Confirmed by `assert sum(ENV_WEIGHTS.values()) == 100` and
`assert sum(STRIKE_WEIGHTS.values()) == 100` in the smoke test.
