# Supply Chain — Methodology & Architecture

> How the Supply Chain tab turns a single ticker into a sourced, segment-aware DAG of suppliers, customers, and competitors.

---

## 1. End-to-end flow

```
User ticker (e.g. AAPL)
        │
        ▼
[Frontend] SupplyChainView.tsx → useSupplyChain hook
        │   GET /api/supply-chain?ticker=AAPL&enrich=filing+industry
        ▼
[Backend] routers/supply_chain.py  (rate-limited 3/min)
        │
        ▼
[services/supply_chain/pipeline.get_supply_chain]
   1. SecDataClient: ticker → CIK → latest 10-K + recent 8-Ks
      • retries on transport errors (tenacity, 3 attempts, 0.5–4 s backoff)
      • 8-K corpus fetched in parallel (ThreadPoolExecutor, max 4 workers)
   2. text_extraction: Item 1 / 2 / 7 / 8 slices (cap 600 KB) + 8-K text (cap 30 KB each)
   3. LlmSupplyChainExtractor.extract_filing  (gpt-4.1, T=0.1)
   4. LlmSupplyChainExtractor.enrich_industry (T=0.2, opt-in)
   5. LlmSupplyChainExtractor.verify          (T=0.0, short-circuits on empty candidates)
   6. Pipeline merge + dedupe by ticker (case-insensitive) or name fallback
   7. Pydantic validation at every LLM-pass boundary; failures → RuntimeError
        │
        ▼
SupplyChainGraph (incl. eight_k_failed_count) → JSON
        │
        ▼
[Frontend] components/SupplyChain/* → useSupplyChainGraph(data) → ReactFlow DAG
   • focal node (center)
   • suppliers (left lane)   • customers (right lane)
   • competitors (bottom row)
   • multi-segment companies → vertical lanes per reportable segment
   • MetadataBar surfaces partial-corpus warning when eight_k_failed_count > 0
```

---

## 2. Data sourcing

### SEC pipeline ([backend/services/supply_chain/sec_client.py](../backend/services/supply_chain/sec_client.py))
- **Ticker → CIK** via `https://www.sec.gov/files/company_tickers.json` (cached on the `SecDataClient` instance).
- **Latest 10-K** via `https://data.sec.gov/submissions/CIK{cik}.json`.
- **Recent 8-Ks**: up to **8 filings** filed on/after the 10-K date, fetched in parallel via `ThreadPoolExecutor(max_workers=4)`. Per-URL failures are counted, not silenced — the count is surfaced on `SupplyChainGraph.eight_k_failed_count` so the UI can flag a partial corpus.
- **User-Agent header required** by SEC (set per `SEC_USER_AGENT` env).
- **Retry policy**: every external HTTP call is wrapped with `tenacity` — 3 attempts, exponential backoff 0.5 → 4 s, retries only `httpx.TransportError` / `httpx.TimeoutException`. HTTP status errors propagate unchanged so the orchestrator can raise `ValueError` (404) or surface 5xx without re-trying. `httpx.Client` is shared across worker threads (thread-safe for concurrent sync use).

### Text extraction ([backend/services/supply_chain/text_extraction.py](../backend/services/supply_chain/text_extraction.py))
Pure module — zero I/O, unit-tested without mocks. Targets only:
| Section | Why |
|---|---|
| Item 1 — Business | Names suppliers, customers, segments |
| Item 1A — Risk Factors | "If Foundry X reduces capacity…" → dependency signals |
| Item 7 — MD&A | Segment-level customer concentration commentary |

- BeautifulSoup strips scripts/styles.
- Regex locates section boundaries (`item\s*1\b[.\s]\s*business`, etc.).
- Whitespace collapsed; total cap **600 KB** (trims from start, since front matter is noise).
- 8-Ks capped at **30 KB each**, prefixed with `--- 8-K filed YYYY-MM-DD ---`.

---

## 3. Three-pass LLM pipeline

All passes use **Azure OpenAI `gpt-4.1`**, `response_format={"type": "json_object"}`, and live on [LlmSupplyChainExtractor](../backend/services/supply_chain/llm_extractor.py). Each pass validates the response against a Pydantic model defined in [types.py](../backend/services/supply_chain/types.py) — `pydantic.ValidationError` is wrapped as `RuntimeError` so the router maps it to a 5xx instead of silently dropping fields.

| Pass | Purpose | Temp | Source tag |
|---|---|---|---|
| **1. Filing extraction** | Pull relationships **named in filings** | 0.1 | `10-K` or `8-K` |
| **2. Industry augmentation** | Add publicly-known relationships **not** in filings | 0.2 | `industry` |
| **3. Verifier audit** | Drop unsupported industry entries, recalibrate confidence | 0.0 | (filters Pass 2) |

### Pass 1 — schema (excerpt)
```json
{
  "segments": ["Intelligent Cloud", "Productivity & Business Processes"],
  "concentration_note": "No single customer accounted for >10% of net revenue.",
  "suppliers": [
    {
      "name": "Taiwan Semiconductor Manufacturing",
      "ticker": "TSM",
      "relationship": "Foundry / chip fab",
      "cost_pct": null,
      "segment": "Intelligent Cloud",
      "source": "10-K",
      "notes": "5nm capacity for AI accelerators"
    }
  ],
  "customers": [...],
  "competitors": [...],
  "summary": "..."
}
```
Hard caps: **15 suppliers, 15 customers, 10 competitors**.

### Pass 2 — industry augmentation
Sees the focal company name, segments, and Pass-1 lists (compact JSON). Asked to ADD only relationships it can credibly cite:
- 0.9+ → textbook / officially announced multi-year
- 0.7–0.89 → widely reported, multiple sources
- 0.5–0.69 → sector-typical, not specifically confirmed
- < 0.5 → omit

Each addition must include `notes` citing a basis. Caps: 15 / 15 / **5** competitors.

### Pass 3 — verifier audit
Auditor persona, T=0. **Short-circuits to a typed empty result with no API call when there are zero candidates** (keeps the pipeline call site uniform). When candidates are present, reviews each Pass-2 candidate:
- DROP if no credible public basis. "When in doubt, DROP."
- DROP if final confidence < 0.6.
- ADJUST confidence downward if overstated.
- IMPROVE `notes` citation; never invent a citation.
- Cannot ADD entries or change `name`/`ticker`/`relationship`.

Returns the surviving subset plus a one-line `audit_summary`.

### Merge & dedupe
```python
seen = {(name.lower(), ticker.lower()) for n in filing_nodes}
for cand in verified_industry:
    key = (cand.name.lower(), cand.ticker.lower())
    if key not in seen:
        out.append(cand); seen.add(key)
```
Filing source always wins over industry on collision.

---

## 4. Data shapes

### Backend ([backend/services/supply_chain/types.py](../backend/services/supply_chain/types.py))
```python
SourceTag = Literal["10-K", "8-K", "industry"]

@dataclass
class CompanyNode:
    name: str
    ticker: Optional[str]
    relationship: str
    revenue_pct: Optional[float]   # customers only
    cost_pct: Optional[float]      # suppliers only
    notes: str
    source: SourceTag
    segment: Optional[str]
    confidence: Optional[float]    # populated only when source == "industry"

@dataclass
class SupplyChainGraph:
    ticker: str
    company_name: str
    filing_date: str               # 10-K date
    accession: str                 # SEC accession (audit trail)
    suppliers: list[CompanyNode]
    customers: list[CompanyNode]
    competitors: list[CompanyNode]
    summary: str
    cached: bool
    eight_k_count: int
    eight_k_dates: list[str]
    segments: list[str]
    concentration_note: str        # verbatim from 10-K
    enrichment_used: list[str]     # ["filing"], ["filing","industry"], or ["filing","verified","industry"]
    eight_k_failed_count: int      # 0 unless one or more 8-K fetches failed (added 2026-04-28)

# Pydantic models for the LLM pass responses. extra="ignore" so future
# prompt extensions don't crash old code.
class LlmCompanyEntry(BaseModel): ...
class LlmFilingResult(BaseModel): ...
class LlmIndustryResult(BaseModel): ...
class LlmVerifierResult(BaseModel): ...

@dataclass(frozen=True)
class EightKFetchResult:
    successful: list[tuple[dict, str]]
    failed_count: int
```

### Frontend ([frontend/src/types/supplyChain.ts](frontend/src/types/supplyChain.ts))
TypeScript mirror of the dataclass — exact same field names so JSON deserializes 1:1.

---

## 5. Graph topology

```
       SUPPLIERS               FOCAL                 CUSTOMERS
       (left lanes)            (center)              (right lanes)
                                  │
   ┌───────────┐                  │                ┌───────────┐
   │ TSM (10-K)│ ───────────────► │ ──────────────►│ AAPL (10-K)│
   └───────────┘                  │                └───────────┘
                              ┌───┴────┐
   ┌───────────┐              │ MSFT   │           ┌───────────┐
   │ NVDA (8-K)│ ───solid───► │        │ ──────►   │ AMZN (IND)│
   └───────────┘              └───┬────┘           └───────────┘
                                  │
                                  │ (dashed = inferred)
                                  ▼
                            COMPETITORS
                          ┌────┐ ┌────┐ ┌────┐
                          │AMD │ │INTC│ │GOOG│
                          └────┘ └────┘ └────┘
```

### Edge / node styling by source
| Source | Border | Edge | Badge |
|---|---|---|---|
| `10-K` | solid gray (`#94a3b8`) | solid gray | `10-K` |
| `8-K` | solid blue (`#60a5fa`) | solid blue | `8-K` |
| `industry` | **dashed** amber (`#fbbf24`) | dashed amber | `INF` |

### Multi-segment layout
When `segments.length >= 2`, suppliers and customers are bucketed into **vertical lanes per reportable segment**, plus a `Cross-segment` lane for nodes with no explicit segment attribution. Layout math is centralised in [frontend/src/components/SupplyChain/layout.ts](../frontend/src/components/SupplyChain/layout.ts) (no React deps — unit-tested directly under vitest):

```ts
FOCAL_X = 600        SUPPLIER_X = 100      CUSTOMER_X = 1100
NODE_VSPACE = 78     LANE_GAP = 60         LANE_HEADER_H = 28
COMP_HSPACE = 180    // competitors row at bottom
```

ReactFlow handles pan / zoom / fit-to-view (`fitView`, `minZoom=0.2`, `maxZoom=2`).

---

## 6. Per-node signals

| Field | Suppliers | Customers | Competitors |
|---|:-:|:-:|:-:|
| `name`, `ticker`, `relationship`, `notes`, `source` | ✓ | ✓ | ✓ |
| `segment` (reportable) | ✓ | ✓ | ✓ |
| `cost_pct` (% of focal COGS) | ✓ | — | — |
| `revenue_pct` (% of focal revenue) | — | ✓ | — |
| `confidence` (0..1) | only if `source=industry` | only if `source=industry` | only if `source=industry` |

`cost_pct` / `revenue_pct` are extracted only when the filing explicitly discloses them (e.g. "represents 22% of net sales"). Most modern 10-Ks now redact specific customer percentages, so these are sparse.

---

## 7. API contract

`GET /api/supply-chain`

| Param | Type | Default | Notes |
|---|---|---|---|
| `ticker` | string | required | regex `^[A-Za-z\.\-]+$`, max 10 chars |
| `refresh` | bool | `false` | bypass cache (cache layer is currently a no-op) |
| `enrich` | string | `filing+industry` | `filing` only, or `filing+industry` to trigger Passes 2–3 |

Response: `SupplyChainResponse` (Pydantic model — flat serialization of `SupplyChainGraph`).

### HTTP error codes
| Code | Cause |
|---|---|
| 400 | Invalid ticker pattern |
| 404 | Ticker not in SEC ticker map; no 10-K filing found |
| 429 | Rate limit (3/min per IP) |
| 500 | LLM call / JSON parse failure |
| 503 | Azure OpenAI not configured |

### Rate limits
- Global default: 60/min, 600/hour per IP.
- This endpoint: **3/min** (each call is 20–30 s of LLM work).

---

## 8. Validation & fallbacks

1. **Required env**: `AZURE_OPENAI_KEY`, `AZURE_OPENAI_ENDPOINT`, `SEC_USER_AGENT`.
2. **Ticker resolution** — fail-fast with 404 if not in SEC map.
3. **Filing length** — warn if Item 1 slice < 5 KB (probably bad parse) and fall back to full-text.
4. **JSON enforcement** — Azure `json_object` mode + `json.loads` raises on malformed output.
5. **Pydantic validation** — each LLM-pass response is `model_validate`d against [types.py](../backend/services/supply_chain/types.py); shape mismatches raise `RuntimeError` (router maps to 5xx). `extra="ignore"` on the models so future prompt extensions don't crash old code.
6. **Tenacity retry** — every SEC HTTP call retries 3× with 0.5–4 s exponential backoff on `httpx.TransportError` / `httpx.TimeoutException`. HTTP status errors are not retried (callers convert 4xx into `ValueError`).
7. **Graceful degradation** — if Pass 2 fails, skip industry enrichment and return filing-only graph (`enrichment_used = ["filing"]`). If Pass 3 fails, fall back to unverified Pass-2 output and log a warning (`enrichment_used = ["filing","industry"]`).
8. **Partial 8-K corpus** — per-URL fetch failures from `fetch_8ks_parallel` are counted on `SupplyChainGraph.eight_k_failed_count` rather than silenced. The frontend `MetadataBar` surfaces the count as a warning chip.
9. **Hard caps** applied after merge (`suppliers[:15]`, etc.) to bound payload size.

---

## 9. Frontend UX

- **Refresh button** → re-fetch with `refresh=true`.
- **Industry knowledge toggle** → switches between `enrich=filing` and `enrich=filing+industry`.
- **Legend overlay** (top-right): explains source color/dash conventions.
- **Detail panel** (click a node):
  - Ticker, name, source badge, segment, confidence (if inferred)
  - Relationship description
  - `cost_pct` or `revenue_pct` (if disclosed)
  - Full `notes` text
- **Metadata bar**: filing date, accession, 8-K count + dates, concentration note, segment list, `enrichment_used` provenance chips.

---

## 10. Files

### Backend ([backend/services/supply_chain/](../backend/services/supply_chain/))

| File | Purpose |
|---|---|
| [types.py](../backend/services/supply_chain/types.py) | `CompanyNode`, `SupplyChainGraph`, `EightKFetchResult`, plus the four Pydantic LLM-result models |
| [text_extraction.py](../backend/services/supply_chain/text_extraction.py) | Pure `extract_10k_relevant_text` / `extract_8k_text` helpers |
| [sec_client.py](../backend/services/supply_chain/sec_client.py) | `SecDataClient` HTTP adapter + tenacity retry + parallel 8-K fetch |
| [llm_extractor.py](../backend/services/supply_chain/llm_extractor.py) | `LlmSupplyChainExtractor` with the three system prompts and Pydantic validation |
| [pipeline.py](../backend/services/supply_chain/pipeline.py) | `get_supply_chain` orchestrator + merge / dedup helpers |
| [supply_chain_service.py](../backend/services/supply_chain_service.py) | 14-line shim re-exporting `get_supply_chain` (legacy import path) |
| [routers/supply_chain.py](../backend/routers/supply_chain.py) | FastAPI route, response model, rate limit |

### Frontend ([frontend/src/components/SupplyChain/](../frontend/src/components/SupplyChain/))

| File | Purpose |
|---|---|
| [layout.ts](../frontend/src/components/SupplyChain/layout.ts) | Pure layout math (constants, lane builder, focal-Y, competitor row, edge stroke). Unit-tested in [layout.test.ts](../frontend/src/components/SupplyChain/__tests__/layout.test.ts) |
| [nodes.tsx](../frontend/src/components/SupplyChain/nodes.tsx) | `nodeLabel` JSX + `nodeStyle` CSS |
| [SourceBadge.tsx](../frontend/src/components/SupplyChain/SourceBadge.tsx) | Provenance pill |
| [useSupplyChainGraph.tsx](../frontend/src/components/SupplyChain/useSupplyChainGraph.tsx) | Hook composing `layout` + `nodes` into ReactFlow `Node[]` / `Edge[]` |
| [Legend.tsx](../frontend/src/components/SupplyChain/Legend.tsx) | Top-right overlay legend |
| [MetadataBar.tsx](../frontend/src/components/SupplyChain/MetadataBar.tsx) | Header strip; surfaces `eight_k_failed_count` as a warning chip |
| [NodeDetailPanel.tsx](../frontend/src/components/SupplyChain/NodeDetailPanel.tsx) | Right sidebar shown when a node is clicked |
| [SupplyChainView.tsx](../frontend/src/components/SupplyChainView.tsx) | Top-level shell (form, state, ReactFlow canvas) |
| [types/supplyChain.ts](../frontend/src/types/supplyChain.ts) | TypeScript contract mirroring `SupplyChainGraph` |
| [hooks/useSupplyChain.ts](../frontend/src/hooks/useSupplyChain.ts) | Fetch hook with error / loading state |

---

## 11. Known limitations / future work

1. **No persistent cache** — `cached` is always `false`; every request re-runs the full pipeline. `force_refresh=True` is documented but a no-op pending a follow-up ADR. Recommendation: file-based JSON cache keyed on `(ticker, accession)` under `backend/cache/supply_chain/` with a TTL.
2. **Modern 10-Ks rarely disclose customer %** — `revenue_pct` / `cost_pct` are mostly null. Could enrich from Bloomberg-style supplier-relationship datasets if licensed.
3. **Industry pass is recall-bounded by the model's training cutoff.** Recent partnerships (post training) won't appear unless they showed up in 8-Ks.
4. **Verifier is itself an LLM** — drops false positives well but cannot truly *verify*. A future pass could ground each industry candidate against a web-search snippet.
5. **No tier-2 expansion** — graph is one hop deep (focal → direct supplier). Multi-tier (focal → TSMC → ASML) would require recursive expansion and is bounded by token cost.
6. **Edges carry no flow magnitude** — width / opacity could encode `cost_pct` or `revenue_pct` when present.

## 12. Architecture decision records

- [ADR-0003: Supply-Chain Adapter Pattern](adr/0003-supply-chain-adapter-pattern.md) — rationale for the package decomposition, retry / validation / parallel-fetch additions, and the frontend split.
