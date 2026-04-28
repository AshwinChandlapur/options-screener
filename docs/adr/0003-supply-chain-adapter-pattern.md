# ADR-0003: Supply-Chain Adapter Pattern

- **Status**: Accepted
- **Date**: 2026-04-28

## Context

The supply-chain feature shipped as a single 630-line module ([backend/services/supply_chain_service.py](../../backend/services/supply_chain_service.py)) that mixed five concerns in one file: SEC HTTP fetch, BeautifulSoup text extraction, three Azure OpenAI calls with their full system prompts, dataclass assembly, and a public `get_supply_chain` orchestrator. Module-level state — the ticker→CIK cache, an implicit per-call `httpx.Client` re-creation, the Azure client constructed inside each LLM helper — was scattered across the file. Three near-identical LLM call helpers (`_call_llm`, `_call_industry_llm`, `_call_verifier_llm`) returned untyped `dict` payloads that the orchestrator field-filtered into `CompanyNode` instances; a malformed JSON shape silently degraded to a row with empty fields rather than failing.

The frontend mirror ([frontend/src/components/SupplyChainView.tsx](../../frontend/src/components/SupplyChainView.tsx), 543 lines) bundled the input form, ReactFlow canvas, ~180-line `makeNodes` graph builder, segment-lane layout math, source-badge styling, legend overlay, metadata strip, and right-side detail panel into one component. There was no test runner configured.

Backend test coverage of the package was zero. The integration baseline added in Phase 0 ([backend/tests/integration/test_supply_chain_baseline.py](../../backend/tests/integration/test_supply_chain_baseline.py)) pinned six characterization fixtures but exercised only the orchestrator's deterministic path — no per-adapter coverage.

The structural problems mirrored the screener pre-[ADR-0002](0002-unified-screener-service.md) state: layered responsibilities collapsed into one module, untyped dict payloads at internal boundaries, no seam for unit tests to inject fakes, and no place to add cross-cutting concerns (retry policy, response validation, parallel I/O) without reaching into the orchestrator.

Phase 0 captured six characterization fixtures (3 baseline tickers × 2 failure scenarios) at [backend/tests/fixtures/supply_chain/](../../backend/tests/fixtures/supply_chain/) so any consolidation could be proven non-regressing on the deterministic path before merge.

## Options Considered

1. **Status quo — leave the module as a single file and add tests around `get_supply_chain` only.**
   - Pros: zero refactor risk; orchestrator behaviour is what the router actually exercises.
   - Cons: no seam for unit-testing the SEC HTTP layer or the LLM layer in isolation; cross-cutting concerns (retries, validation, parallel 8-K fetch) still have to be added inside one file; the next contributor pays the same comprehension cost.

2. **Inheritance — `BaseSupplyChainExtractor` with overridable `_extract_filing` / `_enrich_industry` / `_verify` hooks.**
   - Pros: familiar pattern; one class to read.
   - Cons: rejected for the same reason inheritance was rejected in ADR-0002 — shared state via `self` blurs which inputs each pass actually needs; subclass hooks can quietly mutate base behaviour; pure-function testability is harder. There is also only one extractor in flight; inheritance solves a polymorphism problem we don't have.

3. **Composition via two named adapter classes — `SecDataClient` (HTTP, retry, parallel fetch) and `LlmSupplyChainExtractor` (three typed passes), composed by a `pipeline.get_supply_chain` orchestrator. (Chosen.)**
   - Pros: each adapter owns one external boundary; the orchestrator's signature names its dependencies and accepts injected fakes; cross-cutting concerns (tenacity retry, Pydantic validation, `ThreadPoolExecutor` for parallel 8-K fetch) live on the adapter that owns them; pure helpers (text extraction) sit in their own module with zero dependencies.
   - Cons: more files (5 instead of 1); the orchestrator now has to plumb adapters via keyword-only parameters.

4. **Code generation / declarative manifest.**
   - Rejected. There is exactly one supply-chain pipeline; declaring it in YAML buys nothing and breaks IDE navigation.

## Decision

Decompose `supply_chain_service.py` into a layered package mirroring the screener pattern from ADR-0002.

### Package layout

[backend/services/supply_chain/](../../backend/services/supply_chain/):

- [types.py](../../backend/services/supply_chain/types.py) — frozen `CompanyNode`, `SupplyChainGraph`, `EightKFetchResult`. Pydantic models for the three LLM result shapes (`LlmCompanyEntry`, `LlmFilingResult`, `LlmIndustryResult`, `LlmVerifierResult`). Pydantic uses `extra="ignore"` so future prompt extensions don't crash old code.
- [text_extraction.py](../../backend/services/supply_chain/text_extraction.py) — pure `extract_10k_relevant_text(html, max_chars)` and `extract_8k_text(html, max_chars)`. Module-level regex constants. Zero I/O, unit-testable without mocks.
- [sec_client.py](../../backend/services/supply_chain/sec_client.py) — `SecDataClient` wraps a single `httpx.Client` (connection-pooled, thread-safe for concurrent sync use) with `get_company_tickers`, `get_filings_index`, `get_latest_10k`, `get_recent_8ks`, `fetch_filing_text`, `fetch_8k_text`, and `fetch_8ks_parallel`. Lifecycle managed via `__enter__` / `__exit__` / `close()`. Tenacity retry decorator (`@_sec_retry`) on every external call: 3 attempts, exponential backoff 0.5–4 s, retries only `httpx.TransportError` / `httpx.TimeoutException` (HTTP status errors are surfaced unchanged so the orchestrator can map them to `ValueError`). Process-wide singleton via `get_default_client()`.
- [llm_extractor.py](../../backend/services/supply_chain/llm_extractor.py) — `LlmSupplyChainExtractor` owns one `AzureOpenAI` client and exposes one method per LLM pass: `extract_filing(...)`, `enrich_industry(...)`, `verify(...)`. Each method runs `model_validate` on the response and re-raises `pydantic.ValidationError` as `RuntimeError` so the router can map it to a 5xx. `verify()` short-circuits to a typed empty result when there are zero candidates (no API call). Process-wide singleton via `get_default_extractor()`.
- [pipeline.py](../../backend/services/supply_chain/pipeline.py) — `get_supply_chain(ticker, force_refresh=False, enrich_industry=True, *, sec_client=None, llm=None)` composes the adapters. Pure helpers (`_node_key`, `_to_company_node`, `_merge_industry`) handle dedup + source-tag plumbing. Industry-pass and verifier-pass exceptions degrade gracefully: industry failure → filing-only graph (`enrichment_used=["filing"]`); verifier failure → unverified industry pool (`enrichment_used=["filing","industry"]`).

The legacy module ([backend/services/supply_chain_service.py](../../backend/services/supply_chain_service.py)) is now a 14-line shim re-exporting `get_supply_chain` so the router import path is unchanged.

### Frontend layout

[frontend/src/components/SupplyChain/](../../frontend/src/components/SupplyChain/):

- `layout.ts` — pure constants + helpers (`groupBySegment`, `buildColumnLayout`, `focalY`, `competitorRow`, `edgeStrokeFor`, `SOURCE_COLORS`). Zero React imports so the math is unit-testable in vitest's default node environment.
- `nodes.tsx` — JSX `nodeLabel` and CSS-only `nodeStyle` helpers.
- `SourceBadge.tsx` — shared provenance pill.
- `useSupplyChainGraph.tsx` — hook that composes `layout` + `nodes` into ReactFlow `Node[]` / `Edge[]` arrays via `useMemo`.
- `Legend.tsx`, `MetadataBar.tsx`, `NodeDetailPanel.tsx` — extracted overlay / header / sidebar.

The shell at `SupplyChainView.tsx` shrank from 543 to ~190 lines and now owns only the form, `selected`-row state, and ReactFlow canvas wiring.

### Cross-cutting concerns added during the migration

- **Retry policy**. Every SEC HTTP call is wrapped with `tenacity` (added as `tenacity>=9.0.0` to `backend/requirements.txt`). 3 attempts, exponential backoff. Cap matches the SEC 10 req/s rate limit safely.
- **LLM response validation**. Each Azure response is validated against the corresponding Pydantic model. Malformed JSON or shape mismatches now raise `RuntimeError` instead of silently returning a row with empty fields.
- **Parallel 8-K fetch**. `SecDataClient.fetch_8ks_parallel` uses `ThreadPoolExecutor(max_workers=4)`. Per-item failures are caught and counted, never silenced — the count is surfaced on `SupplyChainGraph.eight_k_failed_count` so the UI can flag a partial corpus. `httpx.Client` is thread-safe for concurrent sync use; the adapter shares one client across worker threads.
- **Frontend test runner**. Vitest 2.1 added as a devDependency and configured via the existing `vite.config.ts`. New `npm test` script. CI gate (`.github/workflows/quality.yml`) runs `npm test` before `npm run build`.

### Migration

Delivered in five phases on top of the Phase 0 safety net, each ending green on the six characterization fixtures plus the per-phase test additions. Bisectable commits within Phase 1 to keep individual diffs reviewable.

- **Phase 0** (`4e90e32`) — capture fixtures + baseline test (safety net).
- **Phase 1a** (`12fc15c`) — `types.py` + `text_extraction.py` + delegate.
- **Phase 1b** (`a8abaef`) — `SecDataClient` + delegates.
- **Phase 1c** (`0604705`) — `LlmSupplyChainExtractor` + Pydantic models.
- **Phase 1d** (`37cf818`) — `pipeline.py` + shim + fake-injection mocks.
- **Phase 2a** (`1aa17ba`) — reviewer carry-overs (no behaviour change).
- **Phase 2b** (`912dcc2`) — tenacity retries.
- **Phase 2c** (`46bd49f`) — parallel 8-K + `eight_k_failed_count` (controlled fixture rebaseline).
- **Phase 3** (`faeba92`) — 52 backend unit tests across 4 files (`test_supply_chain_text_extraction.py`, `test_supply_chain_sec_client.py`, `test_supply_chain_llm_extractor.py`, `test_supply_chain_pipeline.py`). Backend test count: 108 → 160.
- **Phase 4** (`56184de`) — frontend decomposition + vitest (15 layout tests).
- **Phase 5** (this ADR) — docs lockstep.

Bit-for-bit parity was preserved on the six deterministic fixtures from Phase 0 through the end of Phase 2b. Phase 2c performed a controlled fixture rebaseline because the new `eight_k_failed_count` field changed `dataclasses.asdict` output by design.

## Consequences

### Positive

- Each external boundary (SEC, Azure OpenAI) lives in one named adapter; cross-cutting concerns (retry, validation, concurrency) attach to the adapter that owns them, not the orchestrator.
- The orchestrator declares its dependencies in its signature; tests inject fakes via keyword-only parameters; the integration suite uses the same fake-injection seam as the unit suite.
- Malformed LLM responses now fail loudly. The screener-derived behaviour of silently dropping fields is gone.
- `eight_k_failed_count` exposes partial-corpus cases the legacy code hid; the UI surfaces it as a warning chip.
- Frontend pure logic (`layout.ts`) is tested directly without a DOM, mirroring the backend's `text_extraction.py` shape.
- Layering preserved per repo convention: adapters never import FastAPI types; `pipeline.get_supply_chain` raises `ValueError` / `RuntimeError`; the router maps them to HTTP. Architecture review at the end of each sub-phase confirmed this and signed off the carry-overs.
- The next contributor adding (for example) a persistent cache or a web-search-grounded verifier pass has an obvious place to put it.

### Negative

- More files. Five backend modules where there was one. Six frontend files where there was one. Reading the orchestrator now requires three jumps (`pipeline.py` → `sec_client.py` → `llm_extractor.py`).
- The fake-injection seam is a public surface contract the test suite depends on (`get_default_client` / `get_default_extractor` are monkeypatched). Renaming or restructuring those entry points needs a fixture review.
- Pydantic validation moved a class of failures from "silent degradation" to "5xx response". This is the right behaviour but is a user-visible change for any caller that was tolerating malformed extractor output.
- Slightly higher import-time cost for the Azure OpenAI client construction-on-demand in `LlmSupplyChainExtractor._client`; we deliberately construct a fresh client per call rather than caching across the parallel 8-K worker pool to avoid sharing client state across threads.

### Out of scope (deferred)

- Persistent cache. `force_refresh=True` is documented but a no-op. Recommendation: file-based JSON cache keyed on `(ticker, accession)` under `backend/cache/supply_chain/` with a TTL. Decide in a follow-up ADR.
- Tier-2 supplier expansion (focal → TSMC → ASML). Recursive expansion bounded by token cost; not needed for the current screener use cases.
- Web-search-grounded verifier. The current verifier is itself an LLM and can drop false positives but cannot truly verify. Grounding each industry candidate against a search snippet is a separate ADR.
- Edge magnitude encoding. `cost_pct` / `revenue_pct` could drive edge width / opacity when present.

## References

- [docs/SUPPLY_CHAIN.md](../SUPPLY_CHAIN.md) — methodology + post-refactor file map.
- [ADR-0002](0002-unified-screener-service.md) — sibling adapter-pattern decision for the screener runner.
- Phase 0 fixtures: [backend/tests/fixtures/supply_chain/](../../backend/tests/fixtures/supply_chain/).
- Phase 0 baseline test: [backend/tests/integration/test_supply_chain_baseline.py](../../backend/tests/integration/test_supply_chain_baseline.py).
