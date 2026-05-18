"""Unit tests for the screener precomputation worker (ADR-0024).

Tests cover:
- config validation (STRATEGY, DTE sentinel values)
- market_hours.is_market_open edge cases
- cosmos_client.is_fresh staleness logic
- main._staleness_threshold selects market vs off-hours threshold
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# config
# ---------------------------------------------------------------------------

class TestConfig:
    def test_load_valid_strategy(self, monkeypatch):
        monkeypatch.setenv("STRATEGY", "csp")
        monkeypatch.setenv("COSMOS_ENDPOINT", "https://example.documents.azure.com/")
        from config import load_from_env
        cfg = load_from_env()
        assert cfg.strategy == "csp"

    def test_invalid_strategy_raises(self, monkeypatch):
        monkeypatch.setenv("STRATEGY", "unknown")
        monkeypatch.setenv("COSMOS_ENDPOINT", "https://example.documents.azure.com/")
        # Re-import after monkeypatching
        import importlib
        import config as cfg_mod
        importlib.reload(cfg_mod)
        with pytest.raises(RuntimeError, match="STRATEGY must be one of"):
            cfg_mod.load_from_env()

    def test_missing_cosmos_endpoint_raises(self, monkeypatch):
        monkeypatch.setenv("STRATEGY", "cc")
        monkeypatch.delenv("COSMOS_ENDPOINT", raising=False)
        import importlib
        import config as cfg_mod
        importlib.reload(cfg_mod)
        with pytest.raises(RuntimeError, match="COSMOS_ENDPOINT"):
            cfg_mod.load_from_env()

    def test_custom_refresh_thresholds(self, monkeypatch):
        monkeypatch.setenv("STRATEGY", "ditm")
        monkeypatch.setenv("COSMOS_ENDPOINT", "https://example.documents.azure.com/")
        monkeypatch.setenv("MIN_REFRESH_SECONDS_MARKET", "600")
        monkeypatch.setenv("MIN_REFRESH_SECONDS_OFF", "7200")
        import importlib
        import config as cfg_mod
        importlib.reload(cfg_mod)
        cfg = cfg_mod.load_from_env()
        assert cfg.min_refresh_seconds_market == 600
        assert cfg.min_refresh_seconds_off == 7200


# ---------------------------------------------------------------------------
# market_hours
# ---------------------------------------------------------------------------

class TestMarketHours:
    def _utc(self, isostr: str) -> datetime:
        return datetime.fromisoformat(isostr).replace(tzinfo=timezone.utc)

    def test_open_weekday_930(self):
        from market_hours import is_market_open
        # Monday 2026-05-18 13:35 UTC = 09:35 ET
        assert is_market_open(self._utc("2026-05-18T13:35:00")) is True

    def test_closed_before_930(self):
        from market_hours import is_market_open
        # Monday 2026-05-18 13:25 UTC = 09:25 ET
        assert is_market_open(self._utc("2026-05-18T13:25:00")) is False

    def test_closed_at_1600(self):
        from market_hours import is_market_open
        # Monday 2026-05-18 20:00 UTC = 16:00 ET — closed (boundary exclusive)
        assert is_market_open(self._utc("2026-05-18T20:00:00")) is False

    def test_closed_weekend(self):
        from market_hours import is_market_open
        # Saturday 2026-05-16 14:00 UTC
        assert is_market_open(self._utc("2026-05-16T14:00:00")) is False

    def test_open_friday_close_boundary(self):
        from market_hours import is_market_open
        # Friday 2026-05-15 19:59 UTC = 15:59 ET — still open
        assert is_market_open(self._utc("2026-05-15T19:59:00")) is True


# ---------------------------------------------------------------------------
# cosmos_client.is_fresh
# ---------------------------------------------------------------------------

class TestIsFresh:
    def _make_client(self, strategy: str = "csp") -> "ScreenerCosmosClient":  # noqa: F821
        with patch("cosmos_client.CosmosClient"), \
             patch("cosmos_client.DefaultAzureCredential"):
            from cosmos_client import ScreenerCosmosClient
            client = ScreenerCosmosClient(
                endpoint="https://fake.documents.azure.com/",
                database="narrative",
                strategy=strategy,
            )
        return client

    def test_fresh_when_young_doc(self):
        client = self._make_client()
        recent_ts = (datetime.now(tz=timezone.utc) - timedelta(seconds=60)).isoformat()
        client._container = MagicMock()
        client._container.query_items.return_value = [{"computed_at": recent_ts}]
        assert client.is_fresh(threshold_seconds=900) is True

    def test_stale_when_old_doc(self):
        client = self._make_client()
        old_ts = (datetime.now(tz=timezone.utc) - timedelta(seconds=1800)).isoformat()
        client._container = MagicMock()
        client._container.query_items.return_value = [{"computed_at": old_ts}]
        assert client.is_fresh(threshold_seconds=900) is False

    def test_stale_when_empty_container(self):
        client = self._make_client()
        client._container = MagicMock()
        client._container.query_items.return_value = []
        assert client.is_fresh(threshold_seconds=900) is False

    def test_stale_on_cosmos_exception(self):
        client = self._make_client()
        client._container = MagicMock()
        client._container.query_items.side_effect = RuntimeError("Cosmos down")
        assert client.is_fresh(threshold_seconds=900) is False


# ---------------------------------------------------------------------------
# main._staleness_threshold
# ---------------------------------------------------------------------------

class TestStalenessThreshold:
    def _cfg(self):
        from config import ScreenerConfig
        return ScreenerConfig(
            cosmos_endpoint="https://fake.documents.azure.com/",
            strategy="csp",
            min_refresh_seconds_market=900,
            min_refresh_seconds_off=14400,
        )

    def test_market_hours_returns_market_threshold(self):
        from main import _staleness_threshold
        # Monday market hours
        now = datetime(2026, 5, 18, 14, 0, 0, tzinfo=timezone.utc)  # 10:00 ET
        with patch("main.is_market_open", return_value=True):
            assert _staleness_threshold(now, self._cfg()) == 900

    def test_off_hours_returns_off_threshold(self):
        from main import _staleness_threshold
        # Saturday
        now = datetime(2026, 5, 16, 14, 0, 0, tzinfo=timezone.utc)
        with patch("main.is_market_open", return_value=False):
            assert _staleness_threshold(now, self._cfg()) == 14400
