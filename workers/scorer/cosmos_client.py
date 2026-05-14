"""Cosmos DB client for the ACS scorer worker (Phase 6).

Reads:  ticker_timeline — today's snapshot docs (aggregated by job-aggregator)
Writes: ticker_timeline — adds acs, acs_ci_lower, acs_ci_upper, acs_components,
        acs_flags, acs_scored_at, decay_acs to the same doc.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from azure.cosmos import CosmosClient
from azure.identity import DefaultAzureCredential
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)


class ScorerCosmosClient:
    def __init__(self, endpoint: str, database: str = "narrative") -> None:
        credential = DefaultAzureCredential()
        self._client = CosmosClient(endpoint, credential=credential)
        self._db = self._client.get_database_client(database)
        self._timeline = self._db.get_container_client("ticker_timeline")

    # ------------------------------------------------------------------
    # Read: today's ticker_timeline docs that have attention data but
    # have not been scored yet (or need a re-score this run).
    # ------------------------------------------------------------------

    @retry(
        retry=retry_if_exception_type(Exception),
        wait=wait_exponential(multiplier=1, min=1, max=15),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    def fetch_today_docs(self, bucket_date: str, limit: int) -> list[dict]:
        """Return ticker_timeline docs for bucket_date, up to limit.

        Returns all docs (scored and unscored) — scorer is idempotent;
        re-scoring is cheap and ensures ACS reflects the latest data.
        """
        query = (
            "SELECT * FROM c "
            "WHERE c.bucket_date = @bucket_date "
            "ORDER BY c._ts ASC "
            "OFFSET 0 LIMIT @limit"
        )
        params = [
            {"name": "@bucket_date", "value": bucket_date},
            {"name": "@limit", "value": limit},
        ]
        return list(
            self._timeline.query_items(
                query=query,
                parameters=params,
                enable_cross_partition_query=True,
            )
        )

    # ------------------------------------------------------------------
    # Write: ACS fields onto the ticker_timeline doc.
    # ------------------------------------------------------------------

    @retry(
        retry=retry_if_exception_type(Exception),
        wait=wait_exponential(multiplier=1, min=1, max=15),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    def write_acs(
        self,
        doc: dict,
        acs: float,
        acs_ci_lower: float,
        acs_ci_upper: float,
        decay_acs: float,
        components: dict[str, float],
        flags: list[str],
    ) -> None:
        """Upsert the ticker_timeline doc with ACS fields."""
        updated = {
            **doc,
            "acs": round(acs, 4),
            "acs_ci_lower": round(acs_ci_lower, 4),
            "acs_ci_upper": round(acs_ci_upper, 4),
            "decay_acs": round(decay_acs, 4),
            "acs_components": components,
            "acs_flags": flags,
            "acs_scored_at": datetime.now(tz=timezone.utc).isoformat(),
        }
        self._timeline.upsert_item(updated)
