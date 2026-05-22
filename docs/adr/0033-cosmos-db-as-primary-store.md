# ADR-0033 — Cosmos DB as Sole Primary Store (Postgres/TimescaleDB Retired)

- **Status**: Accepted
- **Date**: 2026-05-21
- **Supersedes**: [ADR-0013](0013-narrative-intelligence-platform.md) §3 (architecture diagram shows Postgres/TimescaleDB)
- **Related code**: `backend/services/narrative/cosmos_client.py`, `workers/aggregator/cosmos_writer.py`, `workers/aggregator/cosmos_reader.py`, `workers/scorer/cosmos_client.py`, `workers/narrative-detector/cosmos_client.py`, `workers/screener/cosmos_client.py`

## Context

[ADR-0013](0013-narrative-intelligence-platform.md) specified Postgres/TimescaleDB
(hypertables, asyncpg connection pool) as the single relational store for the narrative
intelligence platform.  That decision was made before the Azure deployment topology was
finalised.

During implementation, three factors shifted the decision:

1. **Azure managed offering.** Azure Cosmos DB (NoSQL API) is available as a serverless
   PaaS with no infrastructure management cost.  Setting up TimescaleDB on Azure requires
   either Azure Database for PostgreSQL (managed Postgres) plus the TimescaleDB extension,
   or a self-managed VM — both adding operational overhead that is not justified at this
   stage.

2. **Partition-by-ticker access pattern.** The dominant query pattern is
   `SELECT … WHERE ticker = @t ORDER BY bucket_date DESC LIMIT N`.  Cosmos's
   partition-key-per-ticker design maps directly onto this, making single-partition
   point reads the hot path.  TimescaleDB's hypertable partitioning (by time) does not
   align as naturally.

3. **Schemaless evolution.** The narrative pipeline's `ticker_timeline` documents grew
   new fields (continuity fields, lifecycle state, ACS components) with each ADR cycle.
   Cosmos's schemaless model required no migration DDL; TimescaleDB would have required
   `ALTER TABLE … ADD COLUMN` for each new field.

Postgres/TimescaleDB was never provisioned in production.  The asyncpg pool and
TimescaleDB references in ADR-0013 were design-time artefacts.

## Decision

Cosmos DB (NoSQL API, serverless tier) is the **sole primary store** for the narrative
intelligence platform.  Postgres and TimescaleDB are retired from the design.

Container / schema:

| Container | Partition key | Primary access pattern |
|---|---|---|
| `signals` | `/ticker` | append-only write (extractor); range-scan (aggregator) |
| `ticker_timeline` | `/ticker` | upsert per `(ticker, bucket_date)`; point-read by scorer/detector |
| `narrative_cache` | `/ticker` | overwrite per ticker (screener pre-computation) |
| `signal_events` | `/ticker` | append (alert writer); scan (read service) |

## Consequences

**Positive:**
- Zero infrastructure management.  No Postgres server to patch, size, or replicate.
- Cosmos SDK handles retry and throttling transparently.
- Schemaless documents absorb future field additions without migration DDL.

**Negative / risks:**
- Cross-partition queries (`enable_cross_partition_query=True`) on large containers
  are expensive in RU terms.  Any query that must scan all partitions (e.g.
  `SELECT TOP 50 … ORDER BY acs DESC`) must be routed through the
  `narrative_cache` pre-computation to remain a single-partition point read.
- No referential integrity, no SQL joins.  Data consistency across containers is the
  application's responsibility.
- Hot partitions: `NVDA`, `TSLA`, `AAPL` accumulate far more signals than the median
  ticker.  Monitor per-partition RU consumption as corpus grows.
- Cosmos's eventual-consistency model means a document written by the aggregator may
  not be immediately visible to the scorer in the same 15-minute window.  This is
  accepted — staleness of one scorer cycle (≤15 min) is within design tolerance.

## Update to ADR-0013

ADR-0013 §3 architecture diagram references Postgres, `asyncpg`, and TimescaleDB
hypertables.  Those references are superseded by this ADR.  The rest of ADR-0013
(pipeline stages, worker responsibilities, lifecycle design) remains valid.
