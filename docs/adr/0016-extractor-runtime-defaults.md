# ADR-0016: Extractor Runtime Defaults — Body-Only Gate, Receive Window, and `@latest` Starting Position

- **Status**: Accepted
- **Date**: 2026-05-13

## Context

Three runtime decisions were made while debugging `job-extractor` in Phase 2 that
have no prior written justification. Each affects correctness and cost in
non-obvious ways and needs to be documented for future operators.

### Decision 1: Body-only cost gate (`_MIN_BODY_LEN = 20`)

The original gate design (ADR-0013 §3) specified: skip if `score < 2 OR body < 50`.
This gate blocked all extraction when ingestion used Reddit RSS:
- RSS does not include `score` → always 0, all posts gated.
- RSS link posts have body = title text only (~30–60 chars), hitting the 50-char floor.

After switching ingestion to Arctic Shift API, a new problem surfaced: Arctic Shift
returns `score=1` for posts less than ~36 hours old due to archival lag (the real
vote count is not available until the post is indexed). Using score as a real-time
gate would incorrectly discard all recent posts.

**Decision**: Gate on body length only (`< 20` chars). Score-based filtering is
deferred to Phase 3 aggregation, where Arctic Shift's archived scores (available
with a lag) can be used reliably.

The 20-char floor retains posts with meaningful text (e.g. "NVDA is a buy here")
while discarding stub posts (e.g. "See title").

### Decision 2: Receive window (`RECEIVE_WINDOW_SECONDS = 25`)

`job-extractor` runs as a Container Apps Job on a 5-minute cron. The Event Hubs
SDK `receive()` call blocks indefinitely. The original implementation used
`time.sleep(30)` (hardcoded). The sleep value was not justified and could not be
overridden without a code change.

Cold-start budget breakdown for a fresh job pod:
- Container pull / start: ~3–5s
- DefaultAzureCredential (MI token): ~2–4s
- Key Vault secret fetch (3 secrets): ~3–6s
- AMQP connection to Event Hubs: ~2–4s
- **Total cold-start**: ~10–19s

With a 30s receive window and ~15s cold start, the effective receive time is ~15s.
Setting `RECEIVE_WINDOW_SECONDS=25` reduces job wall-clock time from ~40s to ~35s
while still leaving ~10s of actual receive time on cold starts and ~20s on warm
restarts. This is configurable without a code change for tuning.

### Decision 3: `starting_position = "@latest"` (default)

When there is no committed EH checkpoint for a partition, the `starting_position`
parameter controls where the consumer begins reading. Two options:

- `"-1"` (earliest): replay all retained events (up to 1 day for Basic SKU).
- `"@latest"` (newest): skip backlog; only process events arriving after connect.

`"@latest"` is the correct default for steady-state production: the cron runs
every 5 minutes and ingestion publishes approximately every 15 minutes, so there
is at most ~3 new events per run. Replaying the 1-day backlog on every restart
(e.g., after a deploy) would duplicate signal writes.

`"-1"` (full replay) is exposed as an opt-in via `EXTRACTOR_REPLAY_FROM_START=true`.

**Operator note — initial deploy**: On the very first deploy with no prior checkpoint,
`@latest` means the first extractor run will only see events that arrive *after* it
connects. Any backlog accumulated since ingestion started will be skipped. To catch
up on the backlog, run one job execution with `EXTRACTOR_REPLAY_FROM_START=true`
set as a Container Apps Job env var override, then revert to the default.

## Consequences

- **Positive**: Gate change unblocks extraction for all Arctic Shift posts (no silent
  zero-extraction runs due to score=1 archival lag).
- **Positive**: Receive window is operator-tunable without a redeploy.
- **Positive**: `@latest` default prevents duplicate writes on redeploy.
- **Negative**: Phase 3 aggregation must apply its own score filter against archived
  scores rather than relying on the extractor gate.
- **Negative**: Initial deploy requires a one-time operator action to replay backlog
  (see operator note above).

## Configuration Reference

| Env var | Default | Effect |
|---------|---------|--------|
| `RECEIVE_WINDOW_SECONDS` | `25` | Seconds the EH receive thread runs before `close()` |
| `EXTRACTOR_REPLAY_FROM_START` | `false` | If `true`, sets `starting_position="-1"` (full replay) |
