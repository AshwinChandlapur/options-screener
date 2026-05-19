# ADR-0027: Alert Detection Platform

- **Status**: Accepted
- **Date**: 2026-05-19
- **Related**: [ADR-0019](0019-narrative-phase6-scorer.md), [ADR-0023](0023-emerging-continuity-fields.md), [ADR-0013](0013-narrative-intelligence-platform.md)

## Context

Phase 6 introduced the ACS score and the Emerging tab — a point-in-time view
of which tickers are in lifecycle stages 1–3. What it does not do is notify
users when something materially changes: a ticker entering stage 2 for the
first time, a thesis moving into its peak-conviction stage 3 window, or an ACS
that jumps sharply overnight.

The gap matters because the screener is checked periodically, not monitored
continuously. Without alerts, users miss the 24–48 h window where a newly
emerging narrative is actionable (stage 2 entry) before it becomes crowded
(stage 3 → 4).

Three capabilities are needed:

1. **Detection** — pure logic that evaluates today's state against recent
   history and produces typed alert records.
2. **Persistence** — a Cosmos container that survives the scorer worker's
   stateless execution and allows the FastAPI read service to surface recent
   alerts without re-running detection.
3. **Delivery** — a `/api/narrative/alerts` FastAPI endpoint and a front-end
   panel that renders alerts in reverse-chronological order.

A related correctness gap also surfaced during implementation: a prior
`lifecycle_stage = None` (detector run missed for a day) coerced to `0` via
`or 0`, causing `stage_2_entry` and `stage_3_entry` to fire even when a ticker
had been in that stage for multiple days. The fix is documented here alongside
the carry-forward pattern established in [ADR-0023](0023-emerging-continuity-fields.md).

---

## Options considered

### 1. Inline detection in the FastAPI handler (rejected)

Each call to `/api/narrative/alerts` would fetch recent `ticker_timeline` docs
and run detection on the fly. Simple to implement; no new container.

**Problems:**
- Detection re-runs on every request — any alert with a multi-day history
  window requires fetching history for every ticker in the universe on every
  poll.
- Output is not persisted; alerts that occurred between the previous and current
  poll window could be missed if history docs have already TTL-expired.
- Adds business logic to the router layer, violating the layering rules in
  [copilot-instructions.md](../../.github/copilot-instructions.md).

**Status:** Rejected.

### 2. Separate alert-detection worker (rejected)

A new Container Apps Job that reads `ticker_timeline` and writes `alerts` on a
dedicated schedule.

**Problems:**
- Adds another cron job to maintain. At current scale the scorer already reads
  every ticker's timeline history; a separate job would duplicate that read fan-out.
- Creates an ordering dependency: the alert worker must run after the scorer.
  Either enforce it (complex) or accept a one-cycle lag (incorrect stage transitions).

**Status:** Rejected.

### 3. Detection inside the scorer worker, alerts written to a separate container (accepted)

`detect_alerts()` is a pure function called from
[workers/scorer/scorer.py](../../workers/scorer/scorer.py) after `compute_acs()`
completes for a ticker. It receives `today_stage`, `today_acs`, `bucket_date`,
and the same `history` list the scorer already holds in memory — no extra reads.
Results are written to the `alerts` Cosmos container.

**Benefits:**
- No extra Cosmos reads — history already in memory.
- No ordering dependency — detection is part of the same scorer run.
- Pure function: trivially testable, fully deterministic.
- Scorer already owns `lifecycle_stage` and `acs`; putting detection there
  avoids coupling a separate worker to those fields.

**Status:** Accepted.

---

## Decision

### Alert types

Three alert types are defined in the initial platform:

| `alert_type` | Condition | Threshold |
|---|---|---|
| `stage_2_entry` | `today_stage == 2 AND effective_prior_stage != 2` | — |
| `stage_3_entry` | `today_stage == 3 AND effective_prior_stage != 3` | — |
| `acs_rising_fast` | `today_acs − prior_acs >= 15` | Δ ≥ 15 ACS points (one day) |

Stage-4, -5, -6 do not produce entry alerts — these are late/declining stages
with no actionable entry signal.

### Alert schema

Each alert document written to the `alerts` container:

```json
{
  "id": "{ticker}_{alert_type}_{bucket_date}",
  "ticker": "NVDA",
  "alert_type": "stage_2_entry",
  "triggered_at": "2026-05-19T06:02:00Z",
  "bucket_date": "2026-05-19",
  "payload": {
    "prev_stage": 1,
    "curr_stage": 2,
    "acs": 54.3
  }
}
```

`id` is the Cosmos document id and the idempotency key. Because `bucket_date`
is part of the key, the scorer can re-run (e.g. after a crash) without
duplicating alerts: Cosmos upsert on the same `id` is a no-op.

### Cosmos container: `alerts`

- **Partition key:** `/ticker` — all alerts for a ticker land on one logical
  partition; the `query_alerts()` read is cross-partition but bounded by a
  short lookback window.
- **TTL:** 30 days (`defaultTtl: 2592000`). Alerts older than 30 days expire
  automatically with no application-level cleanup.
- **Indexed fields:** `ticker`, `alert_type`, `triggered_at`, `bucket_date`.
- **Defined in:** [infra/modules/cosmos.bicep](../../infra/modules/cosmos.bicep).

### `effective_prior_stage` carry-forward

The original `prior_stage = int(prior.get("lifecycle_stage") or 0) if prior else None`
had a correctness bug: if `history[0].lifecycle_stage` is `None` (the hourly
detector run had not yet executed on the prior day), the coercion `None or 0 → 0`
produced `prior_stage = 0`. With `today_stage = 2`, the condition `2 != 0` fires
a `stage_2_entry` alert even for tickers that have been at stage 2 for a week.

The fix mirrors the carry-forward established in ADR-0023 for `stage_streak_days`:

```python
effective_prior_stage: int | None = None
for _h in history:
    _ps = _h.get("lifecycle_stage")
    if _ps is not None:
        effective_prior_stage = int(_ps)
        break
```

This walks history from most-recent backwards and returns the first non-`None`
stage. `prior_acs` is **not** carry-forwarded — the `acs_rising_fast` alert is
an explicit one-day delta check and must stay pinned to `history[0]`.

### FastAPI read endpoint

`GET /api/narrative/alerts?limit=50&lookback_days=3`

Implemented in [backend/services/narrative/cosmos_client.py](../../backend/services/narrative/cosmos_client.py)
via `query_alerts()`. Non-fatal: returns `[]` on any Cosmos error so the alerts
panel degrades gracefully rather than crashing the page. Cross-partition query
bounded by `triggered_at >= @cutoff`.

---

## Consequences

### Positive

- Users are notified of actionable stage transitions without polling the
  Emerging tab manually.
- Detection is fully deterministic: same `history` → same alerts. Re-runs
  after a crash produce no duplicates.
- 84 unit tests cover `detect_alerts()`: stage entry transitions, carry-forward
  edge cases, spike thresholds, empty history
  ([workers/scorer/tests/test_alerts.py](../../workers/scorer/tests/test_alerts.py)).
- False positive rate for stage entry alerts on tickers with missed detector
  runs is now zero — the carry-forward matches the streak carry-forward in
  ADR-0023 so both signals remain consistent.

### Negative

- Each scorer run now performs one Cosmos upsert per alert fired. At typical
  rates (0–3 alerts per run across the full universe) the RU cost is negligible.
- The `alerts` container adds one more resource to the Bicep template and the
  Cosmos provisioning scope.

### Accepted risks

- Alerts are keyed on `bucket_date`, not calendar time. Two scorer runs on the
  same day produce the same alert `id`; the second upsert is a no-op. This
  means an alert fires **once per bucket date** regardless of how many times
  the scorer runs. Accepted — one alert per trading day per condition is the
  intended semantics.

---

## Follow-ups

- [ ] Add alert suppression for tickers in the "new ticker" window (first N
  scorer runs) to avoid `acs_rising_fast` on cold-start ACS ramps.
- [ ] Add `stage_4_exit` alert type once users request "ticker left the
  emerging window" signals.
- [ ] Evaluate push delivery (Azure Event Grid / web-socket) once polling
  latency becomes a user complaint.
