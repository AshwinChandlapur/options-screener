"""Unit tests for backend/services/narrative/cosmos_client.py.

The aggregator writes one ``ticker_timeline`` document per ``(ticker, bucket_date)``
pair, so a cross-partition query over the container returns multiple rows per
ticker (one per retained day). The Top-N read paths must collapse those to the
newest snapshot per ticker before sorting, otherwise the same ticker shows up
N times in the UI (regression observed 2026-05).

These tests cover ``_latest_per_ticker`` directly and assert that
``query_top_acs`` / ``query_emerging`` use it via a mocked Cosmos container.
"""
from __future__ import annotations

from unittest.mock import patch

from services.narrative import cosmos_client


# ---------------------------------------------------------------------------
# _latest_per_ticker — pure helper
# ---------------------------------------------------------------------------


class TestLatestPerTicker:
    def test_empty_input_returns_empty_list(self) -> None:
        assert cosmos_client._latest_per_ticker([]) == []

    def test_single_doc_passthrough(self) -> None:
        doc = {"ticker": "NVDA", "bucket_date": "2026-05-16", "acs": 70.0}
        assert cosmos_client._latest_per_ticker([doc]) == [doc]

    def test_keeps_newest_bucket_date_per_ticker(self) -> None:
        old = {"ticker": "MSFT", "bucket_date": "2026-05-15", "acs": 72.3}
        new = {"ticker": "MSFT", "bucket_date": "2026-05-16", "acs": 73.0}
        # Order in input must not matter.
        result = cosmos_client._latest_per_ticker([old, new])
        assert result == [new]
        result_rev = cosmos_client._latest_per_ticker([new, old])
        assert result_rev == [new]

    def test_computed_at_breaks_same_day_ties(self) -> None:
        earlier = {
            "ticker": "AAPL",
            "bucket_date": "2026-05-16",
            "computed_at": "2026-05-16T08:00:00Z",
            "acs": 60.0,
        }
        later = {
            "ticker": "AAPL",
            "bucket_date": "2026-05-16",
            "computed_at": "2026-05-16T16:00:00Z",
            "acs": 62.0,
        }
        result = cosmos_client._latest_per_ticker([earlier, later])
        assert result == [later]

    def test_dedups_across_multiple_tickers(self) -> None:
        docs = [
            {"ticker": "NVDA", "bucket_date": "2026-05-15", "acs": 70.0},
            {"ticker": "NVDA", "bucket_date": "2026-05-16", "acs": 71.5},
            {"ticker": "MSFT", "bucket_date": "2026-05-16", "acs": 73.0},
            {"ticker": "MSFT", "bucket_date": "2026-05-15", "acs": 72.3},
            {"ticker": "TSLA", "bucket_date": "2026-05-16", "acs": 55.0},
        ]
        result = cosmos_client._latest_per_ticker(docs)
        by_ticker = {d["ticker"]: d for d in result}
        assert len(result) == 3
        assert by_ticker["NVDA"]["acs"] == 71.5
        assert by_ticker["MSFT"]["acs"] == 73.0
        assert by_ticker["TSLA"]["acs"] == 55.0

    def test_skips_docs_without_ticker(self) -> None:
        docs = [
            {"ticker": "", "bucket_date": "2026-05-16", "acs": 99.0},  # malformed
            {"bucket_date": "2026-05-16", "acs": 99.0},                # malformed
            {"ticker": "NVDA", "bucket_date": "2026-05-16", "acs": 70.0},
        ]
        result = cosmos_client._latest_per_ticker(docs)
        assert len(result) == 1
        assert result[0]["ticker"] == "NVDA"

    def test_ticker_case_normalized(self) -> None:
        docs = [
            {"ticker": "msft", "bucket_date": "2026-05-15", "acs": 72.3},
            {"ticker": "MSFT", "bucket_date": "2026-05-16", "acs": 73.0},
        ]
        result = cosmos_client._latest_per_ticker(docs)
        assert len(result) == 1
        assert result[0]["acs"] == 73.0


# ---------------------------------------------------------------------------
# query_top_acs — dedup + sort + limit
# ---------------------------------------------------------------------------


class _FakeContainer:
    """Minimal stand-in for a Cosmos ContainerProxy."""

    def __init__(self, docs: list[dict]) -> None:
        self._docs = docs

    def query_items(self, query: str, **kwargs) -> list[dict]:
        # Backend code passes enable_cross_partition_query=True; we ignore it
        # and the query string, returning everything we were seeded with.
        return list(self._docs)


class TestQueryTopAcs:
    def test_collapses_multi_day_snapshots_to_one_row_per_ticker(self) -> None:
        # MSFT shows up twice (yesterday + today), NVDA once. The bug being
        # regressed: prior to ADR-0020 follow-up, both MSFT rows would appear.
        docs = [
            {"ticker": "MSFT", "bucket_date": "2026-05-15", "acs": 72.3},
            {"ticker": "MSFT", "bucket_date": "2026-05-16", "acs": 73.0},
            {"ticker": "NVDA", "bucket_date": "2026-05-16", "acs": 71.5},
        ]
        with patch.object(cosmos_client, "_get_timeline", return_value=_FakeContainer(docs)):
            result = cosmos_client.query_top_acs(limit=10)

        tickers = [d["ticker"] for d in result]
        assert tickers == ["MSFT", "NVDA"]  # sorted by acs desc, deduped
        assert result[0]["acs"] == 73.0      # newest snapshot kept

    def test_respects_limit_after_dedup(self) -> None:
        docs = [
            {"ticker": "A", "bucket_date": "2026-05-16", "acs": 90.0},
            {"ticker": "B", "bucket_date": "2026-05-16", "acs": 80.0},
            {"ticker": "C", "bucket_date": "2026-05-16", "acs": 70.0},
        ]
        with patch.object(cosmos_client, "_get_timeline", return_value=_FakeContainer(docs)):
            result = cosmos_client.query_top_acs(limit=2)
        assert [d["ticker"] for d in result] == ["A", "B"]


class TestQueryEmerging:
    def test_collapses_multi_day_snapshots(self) -> None:
        docs = [
            {"ticker": "MSFT", "bucket_date": "2026-05-15", "acs": 72.3, "lifecycle_stage": 3},
            {"ticker": "MSFT", "bucket_date": "2026-05-16", "acs": 73.0, "lifecycle_stage": 3},
        ]
        with patch.object(cosmos_client, "_get_timeline", return_value=_FakeContainer(docs)):
            result = cosmos_client.query_emerging(limit=10)
        assert len(result) == 1
        assert result[0]["acs"] == 73.0
