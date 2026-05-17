"""Cosmos DB client for the conviction classifier worker.

Reads unclassified signals from the `signals` container and writes the four
conviction axis fields + conviction_confidence back to each document
(ADR-0020 / ADR-0021).
"""
from __future__ import annotations

import logging

from azure.cosmos import CosmosClient
from azure.identity import DefaultAzureCredential
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)


class CosmosClassifierClient:
    def __init__(self, endpoint: str, database: str = "narrative") -> None:
        credential = DefaultAzureCredential()
        self._client = CosmosClient(endpoint, credential=credential)
        self._db = self._client.get_database_client(database)
        self._signals = self._db.get_container_client("signals")

    @retry(
        retry=retry_if_exception_type(Exception),
        wait=wait_exponential(multiplier=1, min=1, max=15),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    def fetch_unclassified(self, batch_size: int, skip_ids: set[str] | None = None) -> list[dict]:
        """Return up to batch_size signal documents without conviction_direction set.

        Gate is the axis field (ADR-0021). Pre-axis docs that only have the
        retired ``conviction_state`` field will be re-classified once on the
        next run, which is the intended one-time migration cost.

        ORDER BY c._ts ASC ensures deterministic ordering across consecutive calls
        so OFFSET 0 LIMIT N is stable within a job run. skip_ids excludes
        documents that failed to write in a previous batch iteration.
        """
        query = (
            "SELECT * FROM c WHERE NOT IS_DEFINED(c.conviction_direction) "
            "ORDER BY c._ts ASC OFFSET 0 LIMIT @batch_size"
        )
        params = [{"name": "@batch_size", "value": batch_size}]
        items = list(
            self._signals.query_items(
                query=query,
                parameters=params,
                enable_cross_partition_query=True,
            )
        )
        if skip_ids:
            items = [i for i in items if i.get("id") not in skip_ids]
        logger.debug("Fetched %d unclassified signals", len(items))
        return items

    @retry(
        retry=retry_if_exception_type(Exception),
        wait=wait_exponential(multiplier=1, min=1, max=15),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    def fetch_missing_embeddings(self, batch_size: int) -> list[dict]:
        """Return signal docs that are classified (axis-style) but lack embedding.

        Used to backfill embeddings for docs classified when the embedding
        API soft-failed on a prior run.
        """
        query = (
            "SELECT * FROM c "
            "WHERE IS_DEFINED(c.conviction_direction) "
            "AND NOT IS_DEFINED(c.embedding) "
            "ORDER BY c._ts ASC OFFSET 0 LIMIT @batch_size"
        )
        params = [{"name": "@batch_size", "value": batch_size}]
        items = list(
            self._signals.query_items(
                query=query,
                parameters=params,
                enable_cross_partition_query=True,
            )
        )
        logger.debug("Fetched %d convicted-but-unembedded signals", len(items))
        return items

    def write_embedding(
        self,
        doc: dict,
        embedding: list[float],
        embedding_model: str,
    ) -> None:
        """Patch an existing signal doc with embedding fields only."""
        updated = {
            **doc,
            "embedding": embedding,
            "embedding_model": embedding_model,
        }
        self._signals.upsert_item(updated)

    @retry(
        retry=retry_if_exception_type(Exception),
        wait=wait_exponential(multiplier=1, min=1, max=15),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    def write_conviction(
        self,
        doc: dict,
        axes: dict,
        conviction_confidence: float,
        embedding: list[float] | None = None,
        embedding_model: str | None = None,
    ) -> None:
        """Upsert the signal document with axis conviction + optional embedding.

        ``axes`` (ADR-0020 / ADR-0021) is a dict with keys
        ``direction``, ``substance``, ``driver``, ``position``; persisted as
        ``conviction_direction`` etc. The retired ``conviction_state`` field
        is no longer written (ADR-0021).

        embedding is stored under the key excluded from Cosmos range indexing
        (/embedding/?) per the Phase 2 Bicep indexing policy in cosmos.bicep.

        If ``embedding`` is provided, ``embedding_model`` MUST be provided too
        — callers always have it (it is the deployment name from KV) and a
        silent default would hide drift if the deployment name changes.
        """
        updated: dict = {
            **doc,
            "conviction_direction": axes["direction"],
            "conviction_substance": axes["substance"],
            "conviction_driver":    axes["driver"],
            "conviction_position":  axes["position"],
            "conviction_confidence": conviction_confidence,
        }
        if embedding is not None:
            if not embedding_model:
                raise ValueError("embedding_model is required when embedding is provided")
            updated["embedding"] = embedding
            updated["embedding_model"] = embedding_model
        self._signals.upsert_item(updated)
