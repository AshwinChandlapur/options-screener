"""Read-side service for the narrative tab (Phase 6).

Reads ACS scores directly from Cosmos ticker_timeline — no Redis in Phase 6.
Converts raw Cosmos documents into typed AcsScore domain objects.

Raises:
    TickerNotTracked  — ticker has no document in ticker_timeline
    NarrativeUnavailable — Cosmos endpoint not configured (NARRATIVE_COSMOS_ENDPOINT
                           or COSMOS_ENDPOINT) or Cosmos unreachable
"""
from __future__ import annotations

import logging
import math
from datetime import datetime, timezone
from uuid import UUID

from .cosmos_client import query_emerging, query_ticker, query_top_acs
from .errors import NarrativeUnavailable, NarrativeNotFound, TickerNotTracked
from .types import (
    AcsComponents,
    AcsScore,
    DailyBucketOut,
    NarrativeAlert,
    NarrativeCluster,
    TickerDetail,
)

logger = logging.getLogger(__name__)

# stage_map must match NARRATIVE_METHODOLOGY.md §5.1 and scorer.py.
_STAGE_MAP: dict[int, float] = {1: 10, 2: 18, 3: 20, 4: 10, 5: 5, 6: 2}


def _doc_to_acs(doc: dict) -> AcsScore:
    """Convert a ticker_timeline Cosmos document to an AcsScore domain object."""
    comps_raw: dict = doc.get("acs_components") or {}
    components = AcsComponents(
        a_attention_persistence=comps_raw.get("A", 0.0),
        b_contributor_quality=comps_raw.get("B", 0.0),
        c_narrative_strength=comps_raw.get("C", 0.0),
        d_thesis_quality=comps_raw.get("D", 0.0),
        e_market_confirmation=comps_raw.get("E", 0.0),
    )
    scored_at_str: str = doc.get("acs_scored_at") or doc.get("computed_at") or ""
    try:
        scored_at = datetime.fromisoformat(scored_at_str.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        scored_at = datetime.now(tz=timezone.utc)

    return AcsScore(
        ticker=doc.get("ticker", ""),
        scored_at=scored_at,
        acs=float(doc.get("acs", 0.0)),
        acs_ci_lower=float(doc.get("acs_ci_lower", 0.0)),
        acs_ci_upper=float(doc.get("acs_ci_upper", 0.0)),
        components=components,
        dominant_signal=doc.get("dominant_signal") or _dominant_from_doc(doc),
        decay_acs=float(doc.get("decay_acs", doc.get("acs", 0.0))),
        flags=list(doc.get("acs_flags") or []),
        lifecycle_stage=int(doc.get("lifecycle_stage") or 0),
        stage_confidence=float(doc.get("stage_confidence") or 0.0),
    )


def _dominant_from_doc(doc: dict) -> str:
    """Fallback dominant signal if scorer hasn't run yet."""
    candidates = {
        "researched_bull": doc.get("conviction_researched_bull_ratio") or 0.0,
        "researched_bear": doc.get("conviction_researched_bear_ratio") or 0.0,
        "emotional_bull":  doc.get("conviction_emotional_bull_ratio") or 0.0,
    }
    if all(v == 0.0 for v in candidates.values()):
        return "unknown"
    return max(candidates, key=lambda k: candidates[k])


async def get_acs_for_ticker(ticker: str) -> AcsScore:
    """Latest ACS for a ticker. Reads directly from Cosmos ticker_timeline."""
    try:
        doc = query_ticker(ticker)
    except Exception as exc:
        raise NarrativeUnavailable(f"Cosmos unavailable: {exc}") from exc
    if doc is None:
        raise TickerNotTracked(f"{ticker} has no narrative history")
    return _doc_to_acs(doc)


def _doc_to_detail(doc: dict) -> TickerDetail:
    """Convert a ticker_timeline doc to a TickerDetail (score + timeline shape)."""
    buckets_raw = doc.get("daily_buckets") or []
    buckets = [
        DailyBucketOut(
            day=str(b.get("day", "")),
            count=int(b.get("count", 0)),
            unique_authors=int(b.get("unique_authors", 0)),
        )
        for b in buckets_raw
    ]
    return TickerDetail(
        ticker=doc.get("ticker", ""),
        bucket_date=str(doc.get("bucket_date", "")),
        score=_doc_to_acs(doc),
        daily_buckets=buckets,
        tier1_pct=float(doc.get("tier1_pct") or 0.0),
        tier2_pct=float(doc.get("tier2_pct") or 0.0),
        tier3_pct=float(doc.get("tier3_pct") or 0.0),
        mentions_14d=int(doc.get("mentions_14d") or 0),
        unique_authors_14d=int(doc.get("unique_authors_14d") or 0),
        gini_14d=float(doc.get("gini_14d") or 0.0),
        contributor_count_growth_7d=float(doc.get("contributor_count_growth_7d") or 0.0),
        conviction_researched_bull_ratio=doc.get("conviction_researched_bull_ratio"),
        conviction_researched_bear_ratio=doc.get("conviction_researched_bear_ratio"),
        conviction_emotional_bull_ratio=doc.get("conviction_emotional_bull_ratio"),
        conviction_dd_norm=doc.get("conviction_dd_norm"),
        conviction_classified_14d=doc.get("conviction_classified_14d"),
    )


async def get_ticker_detail(ticker: str) -> TickerDetail:
    """Full ticker_timeline projection for the drilldown panel."""
    try:
        doc = query_ticker(ticker)
    except Exception as exc:
        raise NarrativeUnavailable(f"Cosmos unavailable: {exc}") from exc
    if doc is None:
        raise TickerNotTracked(f"{ticker} has no narrative history")
    return _doc_to_detail(doc)


async def get_top_tickers(limit: int = 100) -> list[AcsScore]:
    """Top-N tickers by current ACS. Reads directly from Cosmos ticker_timeline."""
    try:
        docs = query_top_acs(limit)
    except Exception as exc:
        raise NarrativeUnavailable(f"Cosmos unavailable: {exc}") from exc
    return [_doc_to_acs(d) for d in docs]


async def get_emerging_tickers(limit: int = 50) -> list[AcsScore]:
    """Stage 1–3 tickers with ACS > 0, ordered by ACS descending."""
    try:
        docs = query_emerging(limit)
    except Exception as exc:
        raise NarrativeUnavailable(f"Cosmos unavailable: {exc}") from exc
    return [_doc_to_acs(d) for d in docs]


async def get_narrative(narrative_id: UUID) -> NarrativeCluster:
    """Cluster detail — not yet implemented in Phase 6."""
    raise NarrativeUnavailable("Narrative cluster detail not yet provisioned (Phase 7)")


async def get_alerts(limit: int = 50) -> list[NarrativeAlert]:
    """Alerts — not yet implemented in Phase 6."""
    raise NarrativeUnavailable("Alert pipeline not yet provisioned (Phase 7)")

