# ADR-0025 — Swing screener precomputation

**Status:** Accepted  
**Date:** 2026-05-18  
**Supersedes:** —  
**Related:** [ADR-0024](0024-screener-precomputation.md) (CSP/CC/DITM precomputation)

---

## Context

The `GET /api/screener/swing/scan` endpoint runs `swing_service.run_scan()` live on every request. `run_scan` performs a two-stage pipeline: parallel OHLC fetch for the universe (~115 tickers), regime computation (SPY/VIX/breadth), and per-symbol scoring — taking 20–35 s depending on yfinance latency. The pattern is identical to the CSP/CC/DITM problem solved in ADR-0024.

ADR-0024 explicitly noted "Swing + EM in follow-up." This ADR executes that follow-up for swing.

---

## Decision

Apply the same precomputation pattern established in ADR-0024 to the swing screener:

1. **One new Cosmos container** — `screener_swing` (partition `/ticker`, TTL 24 h).  
2. **One new Container Apps Job** — `job-screener-swing` (cron `*/15 * * * *`, same image as the existing screener jobs, `STRATEGY=swing`).  
3. **Read path** — `result_store.get_swing_results()` replaces the live `run_scan` call in `GET /swing/scan`. Returns 503 on empty container (no silent live fallback).  
4. **Regime state** — stamped inside every per-ticker result doc (`doc["result"]["regime"]`), mirroring how DITM stamps macro context. The read path extracts regime from the first doc that has it.  
5. **LLM commentary skipped** — the worker does not call `get_batch_commentary`. Universe scans return scores/plans without narrative cards. Commentary remains available on the live `POST /swing` (custom-list) endpoint only.

### Doc shape

```json
{
  "id": "<ticker>",
  "ticker": "<ticker>",
  "computed_at": "<ISO UTC>",
  "result": {
    "data": { ...SwingResult fields... },
    "regime": { ...RegimeState fields... }
  },
  "error": null
}
```

---

## Alternatives considered

**A — Keep swing live, precompute only CSP/CC/DITM**  
Rejected. The latency profile is identical; users experience the same 25 s wait on the most-used tab.

**B — Pre-bake LLM commentary in worker**  
Deferred. Adds Azure OpenAI secrets to the worker, increases job duration, and commentary refreshes every 15 min even when scores don't change. Can be revisited if users request it.

---

## Consequences

- `GET /swing/scan` latency drops from ~25 s to <200 ms (point reads by partition key).  
- `POST /swing` (custom list) is unchanged — still runs live with commentary.  
- `job-screener-swing` runs in the same Container Apps environment using the same Docker image as the other screener jobs.  
- Swagger docs for `GET /swing/scan` updated to describe the precomputed source.  
- `docs/ARCHITECTURE.md` §3 updated.
