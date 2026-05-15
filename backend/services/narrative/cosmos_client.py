"""Cosmos DB read client for the narrative read_service (Phase 6).

Reads ACS scores and ticker_timeline docs from Cosmos for the FastAPI routes.
No writes — scorer worker owns all writes to ticker_timeline.
"""
from __future__ import annotations

import logging
import os

from azure.cosmos import CosmosClient
from azure.identity import DefaultAzureCredential

logger = logging.getLogger(__name__)

# Accept either env-var name for the Cosmos endpoint:
#   NARRATIVE_COSMOS_ENDPOINT — backend convention (set on the App Service,
#     used by services/narrative_db.py for the conviction / signals path).
#   COSMOS_ENDPOINT           — worker convention (set by Bicep on every
#     Container Apps Job in infra/modules/containerapps.bicep).
# Same for the database name. Whichever is set wins; NARRATIVE_* takes
# precedence so a single-name override doesn't shadow the backend's default.
_COSMOS_ENDPOINT = os.getenv("NARRATIVE_COSMOS_ENDPOINT") or os.getenv("COSMOS_ENDPOINT", "")
_COSMOS_DB = os.getenv("NARRATIVE_COSMOS_DB") or os.getenv("COSMOS_DB", "narrative")

# Module-level client — initialised lazily on first call, reused across requests.
_client: CosmosClient | None = None
_timeline_container = None  # type: ignore[assignment]


def _get_timeline():  # type: ignore[return]
    global _client, _timeline_container
    if _timeline_container is None:
        if not _COSMOS_ENDPOINT:
            raise RuntimeError(
                "Cosmos endpoint not set: configure NARRATIVE_COSMOS_ENDPOINT "
                "(backend convention) or COSMOS_ENDPOINT (worker convention) "
                "on this process."
            )
        _client = CosmosClient(_COSMOS_ENDPOINT, credential=DefaultAzureCredential())
        _timeline_container = (
            _client.get_database_client(_COSMOS_DB)
            .get_container_client("ticker_timeline")
        )
    return _timeline_container


def query_top_acs(limit: int) -> list[dict]:
    """Return up to limit ticker_timeline docs ordered by acs descending."""
    container = _get_timeline()
    query = (
        "SELECT * FROM c "
        "WHERE IS_DEFINED(c.acs) "
        "ORDER BY c.acs DESC "
        "OFFSET 0 LIMIT @limit"
    )
    return list(
        container.query_items(
            query=query,
            parameters=[{"name": "@limit", "value": limit}],
            enable_cross_partition_query=True,
        )
    )


def query_emerging(limit: int) -> list[dict]:
    """Return stage 1–3 tickers with acs > 0, ordered by acs descending."""
    container = _get_timeline()
    query = (
        "SELECT * FROM c "
        "WHERE IS_DEFINED(c.acs) AND c.acs > 0 "
        "AND IS_DEFINED(c.lifecycle_stage) "
        "AND c.lifecycle_stage >= 1 AND c.lifecycle_stage <= 3 "
        "ORDER BY c.acs DESC "
        "OFFSET 0 LIMIT @limit"
    )
    return list(
        container.query_items(
            query=query,
            parameters=[{"name": "@limit", "value": limit}],
            enable_cross_partition_query=True,
        )
    )


def query_ticker(ticker: str) -> dict | None:
    """Return the most recent ticker_timeline doc for a ticker, or None."""
    container = _get_timeline()
    query = (
        "SELECT * FROM c "
        "WHERE c.ticker = @ticker "
        "ORDER BY c.computed_at DESC "
        "OFFSET 0 LIMIT 1"
    )
    results = list(
        container.query_items(
            query=query,
            parameters=[{"name": "@ticker", "value": ticker.upper()}],
        )
    )
    return results[0] if results else None
