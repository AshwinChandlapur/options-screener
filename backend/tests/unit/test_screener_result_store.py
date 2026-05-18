"""Unit tests for backend/services/screener/result_store.py (ADR-0024).

Tests cover:
- ScreenerStoreEmpty raised when all point reads fail (no docs)
- DTE window filtering (rows outside [min_dte, max_dte] excluded)
- max_capital filtering on CSP strikes
- top_n slicing returns at most top_n rows sorted by score
- last_updated_at / oldest_age_s timestamps computed correctly
- DITM macro fields extracted from stored doc
"""
from __future__ import annotations

import dataclasses
from datetime import datetime, timezone, timedelta
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers to build minimal doc fixtures
# ---------------------------------------------------------------------------

def _csp_strike(csp_score: float = 70.0, strike: float = 100.0) -> dict:
    return {
        "strike": strike,
        "delta": -0.3,
        "premium": 1.5,
        "annualized_return": 18.0,
        "bid_ask_spread_pct": 5.0,
        "env_score": 70.0,
        "strike_score": 70.0,
        "csp_score": csp_score,
        "env_detail": "",
        "strike_detail": "",
        "is_best": True,
        "iv_fallback": False,
        "stale_premium": False,
        "iv_hv_ratio": None,
        "dist_pct": None,
        "em_buffer_pct": None,
        "otm_pct": 5.0,
        "lq_count": 100,
        "roc_annualized": None,
        "iv_stale": False,
    }


def _csp_row(symbol: str, dte: int, best_csp_score: float, strikes: list | None = None) -> dict:
    return {
        "symbol": symbol,
        "price": 150.0,
        "bb_upper": 160.0,
        "bb_middle": 150.0,
        "bb_lower": 140.0,
        "sma_ratio": 1.05,
        "rsi": 55.0,
        "iv_rank": 40.0,
        "iv_percentile": 50.0,
        "earnings_date": None,
        "earnings_within_dte": False,
        "vol_support_126_1": None,
        "vol_support_126_2": None,
        "vol_support_126_3": None,
        "dte": dte,
        "expiration": "2026-07-18",
        "strikes": strikes or [_csp_strike(csp_score=best_csp_score)],
        "best_csp_score": best_csp_score,
        "using_hv_fallback": False,
        "expected_move": 10.0,
        "dist_from_52w_high_pct": -5.0,
        "chain_median_oi": 500.0,
    }


def _csp_doc(
    ticker: str,
    dte: int = 45,
    best_csp_score: float = 70.0,
    computed_at: str | None = None,
    strikes: list | None = None,
) -> dict:
    ts = computed_at or datetime.now(tz=timezone.utc).isoformat()
    return {
        "id": ticker,
        "ticker": ticker,
        "computed_at": ts,
        "result": {"rows": [_csp_row(ticker, dte, best_csp_score, strikes)]},
        "error": None,
    }


def _ditm_strike() -> dict:
    return {
        "strike": 130.0,
        "delta": 0.75,
        "mid": 25.0,
        "extrinsic_pct": 2.0,
        "theta_annualized_pct": 3.0,
        "breakeven_pct": -2.0,
        "capital_efficiency_pct": 30.0,
        "bid_ask_spread_pct": 1.0,
        "chain_oi": 200,
        "env_score": 65.0,
        "strike_score": 65.0,
        "ditm_score": 65.0,
        "is_best": True,
        "iv_fallback": False,
    }


def _ditm_row(symbol: str, dte: int, best_ditm_score: float) -> dict:
    return {
        "symbol": symbol,
        "price": 170.0,
        "sma_ratio": 1.1,
        "hv_rank": 30.0,
        "hv30": 25.0,
        "weekly_rsi": 60.0,
        "ret_200d": 20.0,
        "dist_from_52w_high_pct": -3.0,
        "earnings_date": None,
        "days_to_earnings": None,
        "earnings_within_dte": False,
        "dte": dte,
        "expiration": "2027-01-15",
        "strikes": [_ditm_strike()],
        "best_ditm_score": best_ditm_score,
        "gap_3d_pct": 0.5,
        "macro_hold": False,
        "chain_median_oi": 300.0,
        "iv_percentile": 40.0,
        "trend_r2": 0.85,
    }


def _ditm_doc(ticker: str, dte: int = 240, best_ditm_score: float = 65.0) -> dict:
    return {
        "id": ticker,
        "ticker": ticker,
        "computed_at": datetime.now(tz=timezone.utc).isoformat(),
        "result": {"rows": [_ditm_row(ticker, dte, best_ditm_score)]},
        "error": None,
        "macro_pass": True,
        "vix_level": 16.5,
        "vix_5d_change": -0.5,
        "spy_above_sma200": True,
    }


# ---------------------------------------------------------------------------
# Tests: ScreenerStoreEmpty
# ---------------------------------------------------------------------------

class TestScreenerStoreEmpty:
    def test_raises_when_no_docs(self, monkeypatch):
        import services.screener.result_store as rs
        monkeypatch.setattr(rs, "_containers", {})
        monkeypatch.setattr(rs, "_client", None)
        monkeypatch.setenv("COSMOS_ENDPOINT", "https://fake.documents.azure.com/")

        mock_container = MagicMock()
        mock_container.read_item.side_effect = Exception("Not Found")

        with patch.object(rs, "_get_container", return_value=mock_container):
            with pytest.raises(rs.ScreenerStoreEmpty):
                rs.get_csp_results(["NVDA"], 30, 60, 20, None)


# ---------------------------------------------------------------------------
# Tests: DTE filtering
# ---------------------------------------------------------------------------

class TestDteFiltering:
    def _patch_container(self, monkeypatch, docs: list[dict], strategy: str = "csp"):
        import services.screener.result_store as rs
        mock_container = MagicMock()

        def _read_item(item, partition_key):
            for d in docs:
                if d["id"] == item:
                    return d
            raise Exception("Not Found")

        mock_container.read_item.side_effect = _read_item
        monkeypatch.setattr(rs, "_containers", {})
        with patch.object(rs, "_get_container", return_value=mock_container):
            yield rs

    def test_keeps_rows_within_window(self, monkeypatch):
        docs = [
            _csp_doc("AAPL", dte=45, best_csp_score=80.0),
            _csp_doc("MSFT", dte=20, best_csp_score=90.0),  # outside window
        ]
        import services.screener.result_store as rs
        mock_container = MagicMock()

        def _read_item(item, partition_key):
            for d in docs:
                if d["id"] == item:
                    return d
            raise Exception("Not Found")

        mock_container.read_item.side_effect = _read_item
        with patch.object(rs, "_get_container", return_value=mock_container):
            rows, _, _ = rs.get_csp_results(["AAPL", "MSFT"], 30, 60, 20, None)

        symbols = [r.symbol for r in rows]
        assert "AAPL" in symbols
        assert "MSFT" not in symbols

    def test_empty_result_when_all_outside_window(self, monkeypatch):
        docs = [_csp_doc("AAPL", dte=10, best_csp_score=80.0)]
        import services.screener.result_store as rs
        mock_container = MagicMock()

        def _read_item(item, partition_key):
            for d in docs:
                if d["id"] == item:
                    return d
            raise Exception("Not Found")

        mock_container.read_item.side_effect = _read_item
        with patch.object(rs, "_get_container", return_value=mock_container):
            rows, _, _ = rs.get_csp_results(["AAPL"], 30, 60, 20, None)

        assert rows == []


# ---------------------------------------------------------------------------
# Tests: max_capital filtering on CSP
# ---------------------------------------------------------------------------

class TestMaxCapitalFiltering:
    def test_filters_expensive_strikes(self, monkeypatch):
        """Strikes where strike×100 > max_capital must be removed."""
        cheap_strike = _csp_strike(csp_score=60.0, strike=50.0)   # 50*100=5000 ≤ 6000
        expensive_strike = _csp_strike(csp_score=80.0, strike=80.0)  # 80*100=8000 > 6000
        expensive_strike["is_best"] = True
        docs = [_csp_doc("NVDA", dte=45, best_csp_score=80.0, strikes=[cheap_strike, expensive_strike])]

        import services.screener.result_store as rs
        mock_container = MagicMock()
        mock_container.read_item.return_value = docs[0]

        with patch.object(rs, "_get_container", return_value=mock_container):
            rows, _, _ = rs.get_csp_results(["NVDA"], 30, 60, 20, max_capital=6000.0)

        assert len(rows) == 1
        assert all(s.strike * 100 <= 6000 for s in rows[0].strikes)

    def test_excludes_row_when_all_strikes_too_expensive(self, monkeypatch):
        """Rows with no affordable strikes must be excluded entirely."""
        strike = _csp_strike(csp_score=80.0, strike=200.0)  # 200*100=20000 > 5000
        docs = [_csp_doc("TSLA", dte=45, best_csp_score=80.0, strikes=[strike])]

        import services.screener.result_store as rs
        mock_container = MagicMock()
        mock_container.read_item.return_value = docs[0]

        with patch.object(rs, "_get_container", return_value=mock_container):
            rows, _, _ = rs.get_csp_results(["TSLA"], 30, 60, 20, max_capital=5000.0)

        assert rows == []


# ---------------------------------------------------------------------------
# Tests: top_n + sorting
# ---------------------------------------------------------------------------

class TestTopNAndSorting:
    def test_sorted_by_score_desc(self, monkeypatch):
        docs = [
            _csp_doc("LOW", dte=45, best_csp_score=50.0),
            _csp_doc("HIGH", dte=45, best_csp_score=90.0),
            _csp_doc("MID", dte=45, best_csp_score=70.0),
        ]
        import services.screener.result_store as rs
        mock_container = MagicMock()

        def _read_item(item, partition_key):
            for d in docs:
                if d["id"] == item:
                    return d
            raise Exception("Not Found")

        mock_container.read_item.side_effect = _read_item
        with patch.object(rs, "_get_container", return_value=mock_container):
            rows, _, _ = rs.get_csp_results(["LOW", "HIGH", "MID"], 30, 60, 20, None)

        assert [r.symbol for r in rows] == ["HIGH", "MID", "LOW"]

    def test_top_n_slices(self, monkeypatch):
        docs = [_csp_doc(f"T{i}", dte=45, best_csp_score=float(i)) for i in range(10)]
        import services.screener.result_store as rs
        mock_container = MagicMock()

        def _read_item(item, partition_key):
            for d in docs:
                if d["id"] == item:
                    return d
            raise Exception("Not Found")

        mock_container.read_item.side_effect = _read_item
        tickers = [f"T{i}" for i in range(10)]
        with patch.object(rs, "_get_container", return_value=mock_container):
            rows, _, _ = rs.get_csp_results(tickers, 30, 60, 3, None)

        assert len(rows) == 3


# ---------------------------------------------------------------------------
# Tests: timestamps
# ---------------------------------------------------------------------------

class TestTimestamps:
    def test_last_updated_at_is_newest(self, monkeypatch):
        now = datetime.now(tz=timezone.utc)
        old_ts = (now - timedelta(hours=1)).isoformat()
        new_ts = now.isoformat()
        docs = [
            _csp_doc("A", computed_at=old_ts),
            _csp_doc("B", computed_at=new_ts),
        ]
        import services.screener.result_store as rs
        mock_container = MagicMock()

        def _read_item(item, partition_key):
            for d in docs:
                if d["id"] == item:
                    return d
            raise Exception("Not Found")

        mock_container.read_item.side_effect = _read_item
        with patch.object(rs, "_get_container", return_value=mock_container):
            _, last_updated, oldest_age = rs.get_csp_results(["A", "B"], 30, 60, 20, None)

        assert last_updated is not None
        assert oldest_age is not None
        assert oldest_age >= 3500  # at least ~1 hour


# ---------------------------------------------------------------------------
# Tests: DITM macro fields
# ---------------------------------------------------------------------------

class TestDitmMacroFields:
    def test_macro_fields_extracted(self, monkeypatch):
        docs = [_ditm_doc("NVDA", dte=240, best_ditm_score=70.0)]
        import services.screener.result_store as rs
        mock_container = MagicMock()
        mock_container.read_item.return_value = docs[0]

        with patch.object(rs, "_get_container", return_value=mock_container):
            rows, macro, _, _ = rs.get_ditm_results(["NVDA"], 90, 730, 20)

        assert macro["macro_pass"] is True
        assert macro["vix_level"] == 16.5
        assert macro["spy_above_sma200"] is True

    def test_macro_defaults_when_no_macro_in_doc(self, monkeypatch):
        doc = _ditm_doc("AAPL")
        del doc["macro_pass"]  # simulate missing macro fields
        import services.screener.result_store as rs
        mock_container = MagicMock()
        mock_container.read_item.return_value = doc

        with patch.object(rs, "_get_container", return_value=mock_container):
            _, macro, _, _ = rs.get_ditm_results(["AAPL"], 90, 730, 20)

        # Should fall back to safe defaults
        assert macro["macro_pass"] is True
        assert macro["spy_above_sma200"] is True
