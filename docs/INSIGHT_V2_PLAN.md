# AI Insight v2 — Regime-Aware Cycle-Adjusted Value Bands

- **Status**: Planned (not yet implemented)
- **Replaces**: current `screener_insight_service.py` (v1 — screener echo)
- **Date**: 2026-05-06

---

## Problem with v1

The current AI insight repeats what the score already says. It receives the score
breakdown and news, and produces ENTER/WAIT/SKIP with `env_flag` / `strike_flag`
sentences that paraphrase the numbers. It adds no independent analysis.

A trader evaluating a CSP on IREN at $50 needs to know one thing the score cannot
tell them: **"Am I okay owning 100 shares at $50?"** That requires regime-conditioned
thinking — not a factor summary.

---

## Solution: Cycle-Adjusted Value Bands

For any ticker, the LLM:
1. Identifies the 1–2 primary regime drivers (from business description + news)
2. Assesses current regime from headlines + 1-day price action
3. Produces Bear / Normal / Bull **dollar value bands** anchored on 52W history + current price
4. Evaluates the specific CSP strike against those bands
5. A rule-based VIX overlay adjusts the bands and provides a market-regime gate

The result is a framework that generalises across all tickers without any hardcoded
per-stock logic. A BTC miner identifies BTC price + AI capex as drivers. A consumer
staple identifies consumer spending + commodity input costs. The LLM derives this
from `longBusinessSummary` + recent headlines.

---

## Architecture

### Data flow

```
POST /api/screener/csp/insight
  → InsightRequestIn (adds iv_percentile)
    → get_ticker_info(symbol)          ← new in data_service.py
    → get_news(symbol)                 ← existing
    → _compute_1d_change(symbol)       ← existing
    → get_ohlc("^VIX", "5d")          ← new in data_service.py / get_ticker_info
    → get_insight(InsightRequest)
        → _build_user_prompt(...)      ← rewritten
        → Azure OpenAI gpt-4.1        ← same model
        → InsightResult (new shape)
  → InsightResultOut (new Pydantic shape)
  → frontend InsightPanel             ← redesigned
```

---

## Backend Changes

### 1. `backend/services/data_service.py` — add `get_ticker_info()`

New public function (never raises — graceful fallback on all fields):

```python
def get_ticker_info(symbol: str) -> dict:
    """
    Returns a dict with company profile + market context.
    Fields: sector, industry, business_summary, 52w_high, 52w_low, vix_current, vix_regime.
    All fields default to None / "Unknown" on failure.
    """
```

- `sector`, `industry`, `business_summary` (≤ 300 chars) — from `yf.Ticker(symbol).info`
- `52w_high`, `52w_low` — from `info["fiftyTwoWeekHigh"]` / `info["fiftyTwoWeekLow"]`
- `vix_current` — last close from `get_ohlc("^VIX", "5d")`
- `vix_regime` — derived:
  - `< 15` → `"Calm"`
  - `15–25` → `"Normal"`
  - `25–35` → `"Elevated"`
  - `> 35` → `"Panic"`

---

### 2. `backend/services/screener_insight_service.py` — full redesign

#### `InsightRequest` — add `iv_percentile` field

```python
@dataclass(frozen=True)
class InsightRequest:
    symbol: str
    price: float
    strike: float
    premium: float
    dte: int
    expiration: str
    env_score: float
    strike_score: float
    final_score: float
    env_detail: str
    strike_detail: str
    roc_annualized: Optional[float]
    rsi: float
    iv_hv_ratio: Optional[float]      # kept for back-compat — NOT in user prompt
    iv_percentile: Optional[float]    # v3.3 — replaces iv_hv_ratio in prompt
    dist_from_52w_high_pct: float
```

#### `InsightResult` — replace `env_flag` + `strike_flag` with regime fields

```python
@dataclass(frozen=True)
class InsightResult:
    verdict: Literal["ENTER", "WAIT", "SKIP"]
    confidence: float
    summary: str
    regime_drivers: str      # "BTC price + AI data center capex"
    current_regime: str      # "Mid-cycle — BTC ~$82K, recovering from Jan lows"
    stock_cycle: str         # "Bear" | "Normal" | "Bull"  ← for matrix highlight
    bear_band: str           # "$15–$35"
    normal_band: str         # "$40–$65"
    bull_band: str           # "$80–$120+"  (open-ended bull is acceptable)
    strike_context: str      # "Strike $50 sits at the floor of Normal — solid if mid-cycle holds"
    key_risk: str            # single sentence
```

#### `_SYSTEM_PROMPT` — 5-step regime framework

```
You are an expert options trader specialising in Cash-Secured Puts (CSP).

Your task is NOT to rephrase the screener score. Your task is to produce an
independent, regime-conditioned framework for evaluating whether the trader
should be comfortable owning 100 shares at the strike price.

Follow these five steps exactly:

STEP 1 — IDENTIFY REGIME DRIVERS
  From the ticker profile (sector, industry, business_summary) and recent headlines,
  identify 1–2 primary drivers that determine this stock's valuation cycle.
  Examples: "BTC price + AI capex", "consumer spending + commodity costs",
  "interest rates + credit spreads", "oil price + refining margins".
  Output → regime_drivers (10 words max)

STEP 2 — ASSESS CURRENT REGIME
  From recent headlines and 1-day price change, classify current cycle state:
  Bear (stress/contraction), Normal (stable/ranging), or Bull (expansion/momentum).
  Output → current_regime (15 words max), stock_cycle ("Bear" | "Normal" | "Bull")

STEP 3 — PRODUCE VALUE BANDS
  Produce three non-overlapping dollar bands:
  - bear_band:   floor anchored on 52W low, ceiling below normal floor
  - normal_band: must bracket the current price (i.e. low < current_price < high)
  - bull_band:   floor at normal ceiling, open-ended top acceptable ("$X+")
  Format: "$X–$Y" or "$X+" for open-ended. Integer dollar values only. No overlap.
  Output → bear_band, normal_band, bull_band

STEP 4 — EVALUATE THE STRIKE
  Compare the strike to the three bands.
  Describe where the strike sits relative to the Normal band floor and the bear band ceiling.
  State what assignment at this strike means in the bear scenario.
  Output → strike_context (20 words max)

STEP 5 — VERDICT
  ENTER:  Strike sits in lower half of Normal band or below; regime is Normal or Bull;
          VIX regime supports entry (not Panic).
  WAIT:   Regime is uncertain, or stock is at the top of Normal band, or VIX is Panic
          and stock_cycle is not Bull.
  SKIP:   Strike is above the Normal band floor (assignment at a price above bear band
          ceiling — you'd be catching a falling knife in a regime shift).

Rules:
  - Reason ONLY from data you are given. Do not invent facts.
  - All band values must be integers.
  - summary: 2–3 sentences on the regime + verdict rationale.
  - key_risk: one sentence — the single scenario causing maximum loss.
  - confidence: 0.0–1.0 reflecting how clear the verdict is given available data.
```

#### `_build_user_prompt` — new payload structure

```json
{
  "symbol": "IREN",
  "current_price": 60.98,
  "one_day_change_pct": -1.2,
  "ticker_profile": {
    "sector": "Technology",
    "industry": "Computer Hardware",
    "business_summary": "IREN Ltd operates bitcoin mining and AI data center...",
    "52w_high": 18.62,
    "52w_low": 4.83
  },
  "market_context": {
    "vix": 18.3,
    "vix_regime": "Normal"
  },
  "trade": {
    "strike": 50.0,
    "premium": 2.90,
    "dte": 30,
    "expiration": "2026-06-05",
    "breakeven": 47.10,
    "otm_pct": -18.0
  },
  "scores": {
    "final": 46.0,
    "env": 23.0,
    "strike": 61.0
  },
  "env_factors": [...],
  "strike_factors": [...],
  "supporting_data": {
    "rsi_14": 71.3,
    "iv_percentile": 50,
    "dist_from_52w_high_pct": -20.2,
    "roc_annualized_pct": 70.6
  },
  "recent_headlines": [...]
}
```

Note: `iv_hv_ratio` removed from prompt. `iv_percentile` added.

#### `_ENV_MAX` / `_ENV_LABELS` — sync with v3.3

```python
_ENV_MAX    = {"IVP": 35, "Tr": 15, "SMA": 5, "SLP": 5, "RSI": 20, "OI": 20}
_ENV_LABELS = {
    "IVP": "IV Percentile",
    "Tr":  "52W High Distance",
    "SMA": "SMA50/200 Alignment",
    "SLP": "SMA50 10d Slope",
    "RSI": "RSI(14)",
    "OI":  "Chain Liquidity",
}
```

#### `_RESPONSE_SCHEMA` — updated strict schema

10 required fields: `verdict`, `confidence`, `summary`, `regime_drivers`, `current_regime`,
`stock_cycle`, `bear_band`, `normal_band`, `bull_band`, `strike_context`, `key_risk`.
`stock_cycle` enum: `["Bear", "Normal", "Bull"]`.
`additionalProperties: false`, `strict: true`.

#### `max_tokens` bump: `600 → 900`

---

### 3. `backend/routers/csp.py`

#### `InsightRequestIn` — add `iv_percentile`

```python
class InsightRequestIn(BaseModel):
    # ... existing fields ...
    iv_percentile: Optional[float] = None   # v3.3 scored ENV factor
```

#### `InsightResultOut` — replace old flags with regime fields

```python
class InsightResultOut(BaseModel):
    verdict: str
    confidence: float
    summary: str
    regime_drivers: str
    current_regime: str
    stock_cycle: str
    bear_band: str
    normal_band: str
    bull_band: str
    strike_context: str
    key_risk: str
```

#### Handler — pass `iv_percentile`, map new fields

---

## Frontend Changes

### 4. `frontend/src/types/insight.ts`

```typescript
export interface InsightResult {
  verdict: InsightVerdict
  confidence: number
  summary: string
  regime_drivers: string
  current_regime: string
  stock_cycle: 'Bear' | 'Normal' | 'Bull'
  bear_band: string
  normal_band: string
  bull_band: string
  strike_context: string
  key_risk: string
}

export interface InsightRequest {
  // ... existing fields ...
  iv_percentile: number | null   // v3.3
}
```

Remove `env_flag`, `strike_flag`.

---

### 5. `frontend/src/hooks/useInsight.ts`

Add `iv_percentile` to the POST body construction.

---

### 6. `frontend/src/components/CspTable.tsx` — redesign `InsightPanel`

#### Layout

```
┌─────────────────────────────────────────────────────────────────────┐
│  [ENTER]  78% conf  ·  BTC price + AI capex  ·  Mid-cycle ~$82K    │
├─────────────────────────────────────────────────────────────────────┤
│  VIX 18.3 · Normal market                                           │
│                                                                     │
│  ┌──────────────┬──────────────────────┬───────────────────────┐   │
│  │   Bear       │      Normal          │        Bull           │   │
│  │  $15–$35     │    $40–$65  ←$50     │       $80–$120+       │   │
│  └──────────────┴──────────────────────┴───────────────────────┘   │
│     ← highlighted cell based on stock_cycle × vix_regime           │
│                                                                     │
│  Strike $50 sits at the floor of Normal — solid if mid-cycle holds.│
│  Key risk: BTC drops below $60K reprices IREN toward bear band.    │
│                                                                     │
│  [summary paragraph — 2-3 sentences]                               │
│                                                                     │
│  * AI-estimated ranges, not fundamental valuations                  │
└─────────────────────────────────────────────────────────────────────┘
```

#### VIX × cycle matrix — rule-based band adjustment (pure frontend, no LLM)

The LLM returns base bands. Frontend applies multipliers per cell:

| | VIX <15 Calm | VIX 15–25 Normal | VIX 25–35 Elevated | VIX >35 Panic |
|---|---|---|---|---|
| **Bear** | −10% | base | +5% | −15% |
| **Normal** | −5% | **base** | +10% | −10% |
| **Bull** | base | base | +15% | −20% |

`current_regime` cell (`stock_cycle` row × `vix_regime` column) is highlighted with a
gold border. Strike is shown as a `▼$50` pin on the relevant row in the matrix.

#### Band parsing helper

```typescript
// Parse "$40–$65" → { low: 40, high: 65 }
// Parse "$80+" → { low: 80, high: null }
function parseBand(band: string): { low: number; high: number | null }
```

#### Behaviour

- Panel hidden by default. Rendered only when `insight` is present in state.
- `+ AI` button triggers the POST (existing behaviour — no change to trigger).
- `env_flag` / `strike_flag` rows removed. Replaced by the regime/band display.
- Disclaimer line at bottom: *"* AI-estimated ranges, not fundamental valuations"*

---

## Verification Checklist

1. `python -c "from services.screener_insight_service import get_insight; print('ok')"` — import clean
2. `python -c "from services.data_service import get_ticker_info; print(get_ticker_info('IREN'))"` — returns dict with all 7 keys
3. Manual POST to `/api/screener/csp/insight` with IREN payload — all 10 `InsightResult` fields present; `bear_band`, `normal_band`, `bull_band` match `"$X–$Y"` format; `stock_cycle` is one of `Bear/Normal/Bull`
4. `npm run build` — zero TypeScript errors (no stale `env_flag`/`strike_flag` refs)
5. `grep -r "env_flag\|strike_flag" frontend/src` — zero results
6. Visual check: VIX matrix renders, current cell highlighted, strike pin appears on correct row

---

## Decisions

| Decision | Choice |
|---|---|
| Band generation | LLM generates base bands; frontend applies VIX multipliers — no 12-cell LLM output |
| Open-ended bull band | `"$80+"` format is acceptable |
| `iv_hv_ratio` in InsightRequest | Kept for back-compat, NOT included in user prompt JSON |
| CC insight endpoint | Out of scope for this change |
| `get_ticker_info` failure | Silent fallback — LLM still runs with news + scores |
| Panel trigger | Collapsed by default; "+ AI" button triggers POST (no change to trigger mechanism) |
| VIX source | `get_ohlc("^VIX", "5d")` last close — same pattern as existing price fetches |
| Matrix highlight | CSS gold border on `stock_cycle` row × `vix_regime` column cell |
| Disclaimer | Small italic line: "AI-estimated ranges, not fundamental valuations" |
| `max_tokens` | 600 → 900 |
| SCORING_VERSION reference in prompt | `_ENV_MAX`/`_ENV_LABELS` updated to v3.3 (IVP replaces IH) |

---

## Files Touched

| File | Change type |
|---|---|
| `backend/services/data_service.py` | Add `get_ticker_info()` |
| `backend/services/screener_insight_service.py` | Full rewrite of prompt, dataclasses, schema, builder |
| `backend/routers/csp.py` | `InsightRequestIn` + `InsightResultOut` updates + handler |
| `frontend/src/types/insight.ts` | New fields, remove old flags |
| `frontend/src/hooks/useInsight.ts` | Add `iv_percentile` to POST payload |
| `frontend/src/components/CspTable.tsx` | Redesign `InsightPanel`, add matrix renderer, `parseBand()` |
