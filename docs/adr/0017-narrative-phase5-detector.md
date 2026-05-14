# ADR-0017: Narrative Phase 5 — Embedding Co-location and HDBSCAN Lifecycle Detector

- **Status**: Accepted
- **Date**: 2026-05-13

## Context

Phase 4 delivered `job-classifier`, a 30-minute cron that classifies each Reddit
signal into a conviction state (`researched_bull`, `emotional_bear`, etc.) using
`gpt-4o-mini` structured output. After Phase 4, the Cosmos `signals` container
holds `conviction_state`, `conviction_confidence`, and `classified_at` on every
processed document, and `ticker_timeline` holds aggregated conviction ratios
(`conviction_researched_bull_ratio`, `conviction_emotional_bull_ratio`, etc.).

Phase 5 adds the two capabilities needed to complete the narrative lifecycle model
defined in [NARRATIVE_METHODOLOGY.md §4](../NARRATIVE_METHODOLOGY.md#4-narrative-lifecycle):

1. **Semantic embeddings** — each signal must carry a dense vector representation
   so that thematically related posts can be grouped without relying on exact
   keyword overlap.
2. **HDBSCAN clustering + lifecycle assignment** — a new hourly job must cluster
   the 72-hour embedding window per ticker and assign a lifecycle stage (1–6) to
   each ticker.

Two architectural questions arose while designing Phase 5:

- **Where should embeddings be generated?** The classifier job already holds an
  OpenAI client and processes each signal in batch. Adding a separate embedder
  job would require a second cron, a second set of Key Vault secret reads, and a
  second OpenAI round-trip per post.
- **Where should embeddings be stored?** The Phase 2 Bicep already provisioned
  `excludedPaths: ['/embedding/?']` in the Cosmos `signals` indexing policy —
  a deliberate reservation for a dense vector field that would be fetched by ID,
  never range-queried.

These two questions are decided together below.

---

## Options considered

### Embedding generation

**Option A — Extend `job-classifier` to co-generate embeddings**

Each classify batch (up to 100 posts) also calls `text-embedding-3-small` for the
same posts. Both calls are issued in the same execution loop; the signal document
is written once with `conviction_state`, `conviction_confidence`, `classified_at`,
`embedding`, and `embedding_model`. One OpenAI round-trip adds a second API call
per batch but does not add a new cold-start, credential fetch, or Key Vault round.

Pros:
- One job, one cold-start, one set of secrets — lowest operational surface.
- Embedding and conviction state are always written atomically to the same document;
  no partial-state window where a signal is classified but not yet embedded.
- `job-classifier` already carries the OpenAI client; the delta is ~20 lines of
  Python.
- This is the approach ADR-0013 originally sketched (§3, "embedding merged with
  classifier").

Cons:
- `job-classifier` takes on a second responsibility; the embedding call adds
  ~100–300 ms per batch depending on token count.
- A failure in the embedding call must not block conviction-state storage — error
  handling must be written carefully.

**Option B — Add a separate `job-embedder` cron**

A dedicated 30-minute (or 1-hour) cron processes signals that have
`conviction_state IS NOT NULL AND embedding IS NULL`. Cleaner separation of
concerns; each job has one purpose.

Pros:
- Single-responsibility principle at the job level.
- Embedding model can be swapped without touching the classifier.

Cons:
- A new Container Apps Job adds ~$3–5/mo to the budget (compute + cron
  invocations), already tight at $150/mo total.
- Creates a temporal gap: a signal can be classified but not embedded for up to
  30–60 minutes, during which `job-narrative-detector` would skip it.
- Adds a third cron to the system before the budget review at Phase 6.

### Embedding storage

**Option C — Store on the `signals` document in Cosmos**

The `signals` container's indexing policy already excludes `/embedding/?` from
range indexing (pre-provisioned in Phase 2 Bicep, see `infra/modules/cosmos.bicep`).
Embeddings are fetched by point reads (by `id` + partition key `/ticker`), which
is how `job-narrative-detector` will retrieve them for the 72-hour window query.
No RU cost for the excluded path.

Pros:
- Zero new infrastructure.
- Atom of consistency: embedding travels with the document that produced it.
- Exclusion path already provisioned — no Bicep change needed.

Cons:
- A 1 536-float array at 4 bytes each ≈ 6 KB per document. At 10K signals/day
  that is ~60 MB/day additional Cosmos storage. Serverless storage is $0.25/GB/mo,
  so cost impact is negligible at current ingestion rates.

**Option D — Store in a separate vector database (e.g. Azure AI Search, Qdrant)**

Pros: native ANN search, richer similarity query API.

Cons: new Azure service, new managed identity, new secrets, new cost. The 72-hour
window per ticker is small enough (typically < 500 vectors per ticker) that
in-memory HDBSCAN after a single Cosmos query is faster than a round-trip to a
separate vector store. Deferred to Phase 5.1 if needed.

---

## Decision

**Option A + Option C.**

`job-classifier` is extended to also call `text-embedding-3-small` (batched, up
to 100 per API call) for each signal it classifies. The signal document is written
once with both the conviction state and the embedding. The embedding is stored
in the `signals` Cosmos document under the `embedding` key, alongside a new
`embedding_model` string field (initial value `"text-embedding-3-small"`) for
future model migration.

`job-narrative-detector` is a new `Microsoft.App/jobs` with a 1-hour cron. It:

1. Queries `signals` for the 72-hour window per ticker (partition scan within
   `/ticker`).
2. Loads the `embedding` arrays into memory as a NumPy matrix.
3. Runs HDBSCAN with `min_cluster_size=3`, `min_samples=1`, `metric="cosine"`.
4. Merges clusters whose centroids have cosine similarity > 0.82 into a single
   narrative thread. Noise points (HDBSCAN label `−1`) are excluded from lifecycle
   classification.
5. Applies the pure signal-side lifecycle rules from
   [NARRATIVE_METHODOLOGY.md §4](../NARRATIVE_METHODOLOGY.md#4-narrative-lifecycle)
   using fields already present on `ticker_timeline` (`tier1_pct`, `tier2_pct`,
   `dd_post_ratio`, `gini_14d`, `contributor_count_growth_7d`,
   `conviction_emotional_bull_ratio`). No LLM call is needed for stage assignment.
6. Writes `lifecycle_stage: int` (1–6) and `stage_confidence: float` to the
   ticker's `ticker_timeline` document.

The DiskANN vector index mentioned in the Phase 5 methodology note is deferred
(see "Follow-ups" below) — HDBSCAN runs in-memory after a single Cosmos query,
and the 72-hour window is small enough that query latency is not yet a bottleneck.

### `job-classifier` changes (summary)

| Field added to `signals` | Type | Notes |
|---|---|---|
| `embedding` | `list[float]` (1 536 dims) | Excluded from Cosmos index — pre-provisioned |
| `embedding_model` | `str` | `"text-embedding-3-small"` initially |

The classifier already has the Azure OpenAI client. The delta is a second
`client.embeddings.create()` call per batch. If the embedding call fails, the
signal is written with conviction state intact and `embedding: null`; the detector
skips null-embedding signals gracefully — they are processed on the next classifier
run.

### Infrastructure

No new Azure services are required. `job-narrative-detector` is provisioned as a
new `Microsoft.App/jobs` resource in the existing Container Apps environment:

| Parameter | Value |
|---|---|
| Cron | `0 * * * *` (hourly) |
| CPU | 0.5 |
| Memory | 1.0 Gi |
| Image | `ghcr.io/<org>/narrative-detector:latest` |

New Python dependencies added to `workers/detector/requirements.txt`:
`hdbscan`, `scikit-learn`, `numpy`. These are build-time dependencies only; the
container image carries them statically.

---

## Consequences

**Positive**

- Embeddings are generated in the same cron that already pays the cold-start cost —
  no additional infrastructure, no new managed identity, no new Key Vault secret.
- Embedding + conviction state are written atomically; `job-narrative-detector`
  always sees a coherent document.
- Phase 5 adds one job and two fields to an existing document. The Bicep delta is
  minimal (one new Job resource; no new services).
- The `embedding_model` field makes a future embedding model migration auditable
  and filterable: a `WHERE embedding_model = 'text-embedding-3-small'` query can
  batch-regenerate stale embeddings after a model upgrade.

**Negative**

- `job-classifier` now performs two OpenAI operations per batch. If
  `text-embedding-3-small` quota is exhausted, the entire classify-and-embed batch
  is delayed. Quota ceiling (`100K TPM` from the Phase 4 deployment) should be
  sufficient at current ingestion volumes, but this is a shared limit.
- Signals with a failed embedding call are skipped by the detector on the current
  run. Persistent embedding failures would cause lifecycle assignments to be based
  on a reduced signal set until the backfill run catches up.
- HDBSCAN in-memory on the 72-hour window is adequate today (~500 vectors per
  ticker at steady state). If ingestion scales to tens of thousands of signals per
  ticker per day, the memory footprint will require attention before Phase 6.

**Neutral**

- The Cosmos DiskANN vector index is not deployed at Phase 5 GA. The in-memory
  HDBSCAN path does not require it. DiskANN remains a Phase 5.1 option.
- `workers/detector/` is a new directory. The Dockerfile and Python project
  skeleton follow the same conventions as `workers/classifier/`.

---

## Follow-ups

- [ ] Extend `job-classifier` to batch-call `text-embedding-3-small` and store
  `embedding` + `embedding_model` on each signal document.
- [ ] Add `embedding IS NULL` guard in `job-narrative-detector` query to skip
  unembedded signals.
- [ ] Add Bicep resource for `job-narrative-detector` Container Apps Job with
  1-hour cron trigger.
- [ ] Add `workers/detector/requirements.txt` with `hdbscan`, `scikit-learn`,
  `numpy`.
- [ ] Validate Phase 5 acceptance gate: ≥7/10 known historical narratives correctly
  staged (nuclear energy 2023–2024, AI infrastructure 2023) before marking Phase 5
  complete.
- [ ] Phase 5.1 (deferred): register the Azure DiskANN preview feature on the
  subscription and evaluate whether the Cosmos vector index reduces detector query
  latency below the in-memory baseline. Required only if ingestion volume grows
  beyond ~2 000 signals/ticker/72h.
- [ ] Update [NARRATIVE_METHODOLOGY.md §8](../NARRATIVE_METHODOLOGY.md#8-phasing-and-milestones)
  Phase 5 bullet to reflect that pgvector/Postgres references no longer apply
  (Cosmos was chosen in ADR-0014) and that embeddings are stored on `signals`
  documents, not a separate container.
