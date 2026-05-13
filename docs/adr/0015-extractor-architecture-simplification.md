# ADR-0015: Extractor Architecture Simplification — Remove Dead EH Hub and Cosmos Container

- **Status**: Accepted
- **Date**: 2026-05-13

## Context

The original ADR-0013 design routed extracted signals through a second Event Hubs
topic (`ticker-events`) so that downstream consumers (aggregator, classifier) could
subscribe independently. It also allocated a Cosmos DB `raw-posts` container as a
dedup source for ingestion.

After Phase 2 shipped, two facts became clear:

1. **`ticker-events` hub is never written to.** The extractor writes signals directly
   to Cosmos DB `signals`. No code produces to `ticker-events`. It is dead infra
   consuming namespace quota on the Basic SKU (which limits total hubs per namespace).

2. **`raw-posts` Cosmos container is never written to.** Ingestion writes raw posts to
   Blob Storage (`reddit-raw` container) for durability and dedup. The Cosmos
   `raw-posts` container was provisioned speculatively and was always empty.

## Decision

1. **Remove the `ticker-events` Event Hubs topic** from `infra/modules/eventhubs.bicep`.
   Downstream phases (Phase 3 aggregator) will poll Cosmos DB `signals` directly
   rather than consuming from an EH topic. This removes the fanout-via-EH pattern
   and replaces it with a simpler poll-from-Cosmos pattern.

2. **Remove the `raw-posts` Cosmos container** from `infra/modules/cosmos.bicep`.
   Blob Storage remains the single durable store for raw posts. If raw-post lookups
   are needed in the future, they will be served from Blob, not Cosmos.

## Consequences

- **Positive**: Namespace quota freed. No more dead resources to explain to new
  contributors. Bicep deploys cleanly with no orphan resources.
- **Positive**: Aggregator design is simpler — one data source (Cosmos `signals`)
  instead of needing to bridge EH + Cosmos.
- **Negative**: If a future phase needs a fanout bus (e.g., multiple consumers on
  raw signal stream), a new hub must be re-added. Assessed as low-risk: phases 3–6
  are sequential batch jobs, not competing stream consumers.
- **Infra note**: The live `ticker-events` hub and `raw-posts` container in Azure
  were not explicitly deleted (Bicep removes them on next `az deployment group create
  --mode Complete`, or they can be manually deleted). The containers are empty and
  pose no data-loss risk.

## Alternatives Considered

- **Keep both resources as stubs.** Rejected — dead infra erodes trust in the Bicep
  as the authoritative source of truth and wastes Basic SKU quota.
- **Upgrade to Standard SKU and keep `ticker-events`** for future fan-out.
  Rejected — ~$34/mo incremental cost against a $150/mo total budget with no
  concrete consumer planned before Phase 6.
