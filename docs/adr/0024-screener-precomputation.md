# ADR-0024: Screener Precomputation via Background Container Apps Jobs

- Status: Accepted
- Date: 2026-05-18
- Supersedes: —
- Related: [ADR-0004](0004-scan-result-caching.md), [ADR-0007](0007-scoring-v3-lean-model.md), [ADR-0008](0008-ditm-v3-lean-model.md)

## Context

Universe scans for CSP, CC, and DITM each take 25–30 s. On the hot path:

1. The frontend calls `/api/screener/{strategy}/scan`.
2. The router fan-outs ~115 ticker calls to `yfinance` with concurrency capped at
   `Semaphore(10)`.
3. Results are sorted, sliced, and returned — all blocking the HTTP response.

A 30-minute in-process `ScanCache` (ADR-0004) limits re-scans, but the first
request after a cache miss (or cold-start after a Web App restart) always pays
the full 25–30 s penalty. Users see a spinner for a long time before the
screener loads.

The cache also has a correctness gap: the cache key embeds `top_n`,
`min_dte`, `max_dte`, and `max_capital`. Any variation in those parameters
causes a full re-scan even if the underlying ticker data is unchanged.

The Narrative platform already uses Container Apps Jobs to offload heavy
background computation (extractor, aggregator, ACS scorer). The same pattern
can absorb screener computation: a background job scores the universe every
15 minutes and writes per-ticker docs to Cosmos; the router reads precomputed
docs in milliseconds.

### Scope

This ADR covers **CSP, CC, and DITM** only. Swing and EM use fundamentally
different data (price-history regimes, not options chains) and will be
addressed in a follow-up ADR.

## Decision

### 1. Storage: one Cosmos container per strategy

Three new containers in the `narrative` Cosmos account (serverless billing):

| Container | Partition key | TTL |
|-----------|--------------|-----|
| `screener_csp` | `/ticker` | 86 400 s (24 h) |
| `screener_cc` | `/ticker` | 86 400 s (24 h) |
| `screener_ditm` | `/ticker` | 86 400 s (24 h) |

**Document shape** (per ticker, per strategy):

```json
{
  "id": "NVDA",
  "ticker": "NVDA",
  "computed_at": "2026-05-18T14:30:00Z",
  "result": { /* serialised CspResult / CcResult / DitmResult */ },
  "error": null
}
```

A ticker that fails during a run is stored with `result: null, error: "reason"`.
This prevents the read path from falling back to a live yfinance call, which
would reintroduce latency unpredictably.

The 24 h TTL is a hard ceiling on staleness; combined with the 15-min
cron schedule, normal staleness is ≤ 15 min during market hours. Docs auto-
expire after 24 h; if the job is down for > 24 h, the router returns 503.

**Why not the existing `scan_cache.py`?**  In-process memory does not survive
restarts, scale-out, or slot swaps. Cosmos is durable and shared across all
Web App instances (if scale-out is ever enabled).

**Why separate containers?** Each strategy has a different schema. Mixing them
under a single container with a `strategy` discriminator complicates queries and
makes per-strategy TTL tuning harder. The RU overhead is negligible at serverless
scale.

### 2. Background workers: three Container Apps Jobs

Three new scheduled jobs, one per strategy, sharing the same image:

| Job name | STRATEGY env | Cron | Replica timeout |
|----------|-------------|------|----------------|
| `job-screener-csp` | `csp` | `*/15 * * * *` | 840 s (14 min) |
| `job-screener-cc` | `cc` | `*/15 * * * *` | 840 s (14 min) |
| `job-screener-ditm` | `ditm` | `*/15 * * * *` | 840 s (14 min) |

The worker image is built from `workers/screener/Dockerfile` with the repo
root as build context (so it can `COPY backend/` into the image). The Docker
image is pushed to GHCR and referenced via the `screener{Csp,Cc,Ditm}Image`
Bicep params (same pattern as other jobs).

**Market-aware staleness skip**: the worker checks whether
`now - oldest_precomputed_doc` > threshold before scanning. Two thresholds
are configurable via env vars:

- `MIN_REFRESH_SECONDS_MARKET` (default 900 = 15 min) — during US equity
  market hours (Mon–Fri 09:30–16:00 ET).
- `MIN_REFRESH_SECONDS_OFF` (default 14400 = 4 h) — outside market hours
  and weekends.

If data is still fresh the job exits successfully without writing, saving
yfinance API calls and Cosmos RUs.

**DITM macro context**: `get_macro_context()` (SPY/VIX) is called once per
worker run and stamped onto every per-ticker DITM doc as `macro_pass`,
`vix_level`, `vix_5d_change`, `spy_above_sma200`. The router reads these
fields and reconstructs the `DitmResponse.macro_*` top-level fields from the
freshest doc. This is Option A ("stamp on every doc") — simpler than Option B
("separate macro doc + cross-partition join on read").

### 3. Read path: `result_store.py`

New module `backend/services/screener/result_store.py` with three functions:

```python
def get_csp_results(
    tickers: list[str],
    min_dte: int,
    max_dte: int,
    top_n: int,
    max_capital: float | None,
) -> tuple[list[CspResult], str | None, float | None]: ...

def get_cc_results(tickers, min_dte, max_dte, top_n) -> ...: ...
def get_ditm_results(tickers, min_dte, max_dte, top_n) -> ...: ...
```

Each returns `(rows, last_updated_at, oldest_age_s)`:

- `rows` — filtered (DTE window, capital cap), sorted by composite score,
  sliced to `top_n`. Row objects are the existing `CspResult` / `CcResult` /
  `DitmResult` dataclasses — the router's `_to_out()` functions are unchanged.
- `last_updated_at` — ISO timestamp of the most recently written doc in the
  result set, or `None` if the container is empty.
- `oldest_age_s` — seconds since the oldest doc was written; used to decide
  whether to surface a staleness warning in the UI.

The GET `/scan` routers call `result_store` instead of running live yfinance
scans. Custom-list POST endpoints are unchanged (they always run live).

**Fallback on empty container**: the router returns HTTP 503 with a clear
message. No silent fallback to live scan — that would reintroduce the latency
unpredictably and mask worker failures.

**DTE filtering**: precomputed docs contain all strikes for an expiration window
[`min_dte_default`, `max_dte_default`]. On read, `result_store` filters strikes
to the requested `[min_dte, max_dte]` window. Results that have no strikes
after filtering are excluded from the ranked list.

### 4. Frontend: last-updated badge

Each scan response gains a top-level `last_updated_at: string | null` field.
The results panel displays "Updated X min ago" below the scan button when the
value is present, using a `<time>` element that formats as a relative duration.

### 5. `scan_cache.py` scope reduction

The `ScanCache` singletons (`csp_scan_cache`, `cc_scan_cache`,
`ditm_scan_cache`) remain in place but are now used exclusively by the
custom-list POST endpoints. The GET scan endpoints no longer use them; Cosmos
is the durable cache.

## Consequences

**Good:**
- Universe scan latency drops from 25–30 s to ~200 ms after the first worker run.
- Cache is durable across Web App restarts and scale-out.
- Cache keys no longer embed `top_n` / `min_dte` / `max_dte` — any combination
  is served from the same set of precomputed docs.
- Worker failures are visible (docs go stale, TTL expires → 503 rather than
  slow but live).

**Neutral:**
- Three new Cosmos containers (~$0 at serverless scale; 3 × `upsert` × 115
  tickers every 15 min ≈ 30 RU/s average well below free-tier threshold).
- Worker image is built from repo root context — slightly larger CI step.

**Bad / trade-offs:**
- DTE window filtering on precomputed docs: if a user requests `min_dte=14`
  (below the worker's default `min_dte=30`), that expiration may not exist in
  the precomputed doc. Mitigated by using wide default DTE windows during
  computation (worker always scans `min_dte=14, max_dte=90` for CSP/CC and
  `min_dte=90, max_dte=730` for DITM), so any UI-selectable range is covered.
- First deploy gap: until the worker has run once, GET `/scan` returns 503.
  Resolved by manual job kick in the deploy runbook immediately after infra
  apply (Option A, single-PR approach).
- Custom-list POST endpoints (`/csp`, `/cc`, `/ditm`) are unchanged — they
  always run live. This is intentional: custom lists are user-driven, small,
  and do not benefit from precomputation.

## Alternatives considered

**A. Widen the existing in-process `ScanCache` TTL** — simple but does not
survive restarts, misses parameter-variation cache misses, and does not reduce
latency for the first request after any cache miss.

**B. Redis / Azure Cache for Redis** — persistent shared cache, but adds a
stateful service dependency, billing overhead, and operational complexity that
Cosmos (already present) avoids.

**C. Single `screener_results` container with `strategy` discriminator** —
simpler Bicep, but mixed schemas and no per-strategy TTL tuning. Rejected in
favour of separate containers.

**D. Two-deploy migration (workers populate first, then router cutover)** —
eliminates the first-deploy 503 window but requires a complex deploy sequence
and a feature-flag mechanism. Rejected in favour of manual job kick in the
runbook.

## Change log

| Date | Author | Note |
|------|--------|------|
| 2026-05-18 | Copilot | Initial draft — ADR-0024 accepted |
