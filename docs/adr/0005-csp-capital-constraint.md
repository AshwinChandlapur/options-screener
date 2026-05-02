# ADR-0005: CSP capital constraint as a pre-scoring gate

- **Status**: Accepted
- **Date**: 2026-05-01

## Context

Users running the CSP screener against the full universe often hold accounts with limited
buying power. Returning strikes they cannot trade (e.g. a $175 put requiring $17,500
collateral in a $5,000 account) adds noise and requires manual filtering on the frontend.

Two design questions shaped this decision:

1. **Where to apply the constraint** — as a post-scoring filter (drop results after scoring)
   or a pre-scoring gate (skip candidates before any scoring computation).
2. **Which endpoints to expose it on** — the custom `POST /csp` endpoint, the universe scan
   `GET /csp/scan`, or both.

## Options Considered

1. **Post-scoring filter in the router.**
   - Pros: simpler to implement; scoring logic stays unaware of capital.
   - Cons: wastes compute scoring strikes the user can never trade; distorts
     `best_csp_score` when better-scoring high-dollar strikes are silently omitted after
     the fact.
   - **Rejected.**

2. **Pre-scoring gate in the runner. (Chosen.)**
   - Pros: zero wasted scoring work; the result set is honest — every returned strike was
     genuinely evaluated under the user's constraint; cache hit rate improves because
     high-capital strikes are never computed.
   - Cons: `_process_expiration` in
     [backend/services/screener/runner.py](../../backend/services/screener/runner.py)
     gains a new parameter; CC and DITM must pass `None` explicitly.
   - **Accepted.**

3. **Frontend-only filtering.**
   - Pros: no backend changes.
   - Cons: full universe scored unnecessarily; large payloads over the wire for users with
     tight capital constraints; no way to guarantee parity between POST and GET endpoints.
   - **Rejected.**

## Decision

`max_capital` is threaded from both CSP endpoints into the screener runner's inner
`_process_expiration` loop as an optional pre-scoring gate. Before a strike is passed to
`_extract_candidate`, the runner checks
([runner.py, line 366](../../backend/services/screener/runner.py)):

```python
if max_capital is not None and sp * 100 > max_capital:
    continue
```

CC and DITM receive `max_capital=None` from their callers, so the guard is a no-op for
those strategies.

**Validation** is enforced at the API boundary:

- `POST /api/screener/csp` — Pydantic `field_validator` on `CspRequest.maxCapital`
  rejects values below 100.
- `GET /api/screener/csp/scan` — FastAPI `Query(..., ge=100)` on `max_capital`.

Both reject sub-$100 values, preventing nonsensical caps that would silently exclude every
strike.

**Cache key** for `/csp/scan` appends `max_capital` to the existing key pattern (see
[ADR-0004](0004-scan-result-caching.md)):

```python
cache_key = f"{universe_key}:{top_n}:{min_dte}:{max_dte}:{max_capital}"
```

Different capital constraints produce independent 30-minute TTL entries in
`csp_scan_cache` and never serve each other's results.

## Consequences

**Positive**

- Users with limited buying power see only actionable strikes; no manual post-processing
  required.
- No wasted scoring computation for skipped strikes.
- Cache entries are correctly scoped — a $5,000-constrained scan never returns stale
  unconstrained results.

**Trade-offs accepted**

- Adding `max_capital` to the cache key means each distinct capital value cold-starts its
  own cache entry. This is the correct trade-off (wrong cached results are worse than a
  cold miss), but it increases the number of cache entries under heavy parameterisation.
- `max_capital=None` stringifies as `"None"` in the cache key. This is consistent within
  a Python process. A hypothetical external cache that encodes absence differently would
  need a normalisation step.

**Neutral**

- CC and DITM are unaffected by design. If a capital gate is ever needed for those
  strategies, the runner already accepts `max_capital` — callers need only pass a value
  instead of `None`.

## Follow-ups

- [ ] Surface `maxCapital` in `frontend/src/components/CspInput.tsx` so users can set it
  from the UI rather than via raw API calls.
- [ ] Add `maxCapital` to the `useCsp` hook and the frontend localStorage cache key so
  client-side cached results are also capital-scoped (mirrors the backend key change).
- [ ] Reconsider whether `rf_rate` should also be excluded from the cache key for the
  same "changes at most once daily" reason documented in ADR-0004 — currently it is
  excluded; confirm this is still the intent after the `max_capital` addition.
