"""
Unit tests for swing event-risk handling and hybrid scoring multipliers.

Covers:
- `_days_to_earnings`: parsing, future / past / unknown.
- `earnings_factor` graduated multiplier table.
- `compute_swing_score`: multiplicative composition (regime × earnings × extended)
  and clamping into [0, 100].
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from services.scoring.swing import (
    EARNINGS_FACTOR_LE_3,
    EARNINGS_FACTOR_LE_7,
    EARNINGS_FACTOR_LE_14,
    EXTENDED_FACTOR,
    compute_swing_score,
    earnings_factor,
)
from services.swing_service import _days_to_earnings


# ---------------------------------------------------------------------------
# _days_to_earnings
# ---------------------------------------------------------------------------

class TestDaysToEarnings:
    def test_none_when_no_date(self):
        assert _days_to_earnings(None) is None
        assert _days_to_earnings("") is None

    def test_none_when_unparseable(self):
        assert _days_to_earnings("not-a-date") is None

    def test_none_when_past(self):
        past = (datetime.now(tz=timezone.utc) - timedelta(days=5)).strftime("%Y-%m-%d")
        assert _days_to_earnings(past) is None

    def test_future_returns_day_delta(self):
        future = (datetime.now(tz=timezone.utc) + timedelta(days=10)).strftime("%Y-%m-%d")
        # Allow for crossing-midnight off-by-one.
        assert _days_to_earnings(future) in (9, 10)


# ---------------------------------------------------------------------------
# earnings_factor table
# ---------------------------------------------------------------------------

class TestEarningsFactor:
    @pytest.mark.parametrize(
        "dte,expected",
        [
            (None, 1.0),
            (-1, 1.0),
            (0, EARNINGS_FACTOR_LE_3),
            (3, EARNINGS_FACTOR_LE_3),
            (4, EARNINGS_FACTOR_LE_7),
            (7, EARNINGS_FACTOR_LE_7),
            (8, EARNINGS_FACTOR_LE_14),
            (14, EARNINGS_FACTOR_LE_14),
            (15, 1.0),
            (60, 1.0),
        ],
    )
    def test_buckets(self, dte, expected):
        assert earnings_factor(dte) == expected

    def test_factors_are_monotonic_haircut(self):
        # Closer to earnings → smaller (more punitive) factor.
        assert EARNINGS_FACTOR_LE_3 < EARNINGS_FACTOR_LE_7 < EARNINGS_FACTOR_LE_14 < 1.0


# ---------------------------------------------------------------------------
# compute_swing_score — hybrid composition
# ---------------------------------------------------------------------------

def _base_kwargs():
    """Inputs that produce a healthy, fully-loaded raw score."""
    return dict(
        rr=4.0,
        setup_score=80.0,
        adx_value=28.0,
        ad_line_slope_pct=8.0,
        higher_lows=3,
        institutional_ownership_pct=70.0,
    )


class TestCompositeScoring:
    def test_no_multipliers_score_equals_raw(self):
        out = compute_swing_score(**_base_kwargs())
        assert out["score"] == out["raw_score"]
        assert out["multipliers"] == {"regime": 1.0, "earnings": 1.0, "extended": 1.0}

    def test_regime_multiplier_applied(self):
        out = compute_swing_score(**_base_kwargs(), regime_factor=0.7)
        expected = round(out["raw_score"] * 0.7, 2)
        assert out["score"] == expected
        assert out["multipliers"]["regime"] == 0.7

    def test_earnings_multiplier_applied(self):
        out = compute_swing_score(**_base_kwargs(), days_to_earnings=2)
        expected = round(out["raw_score"] * EARNINGS_FACTOR_LE_3, 2)
        assert out["score"] == expected
        assert out["multipliers"]["earnings"] == EARNINGS_FACTOR_LE_3

    def test_extended_multiplier_applied(self):
        out = compute_swing_score(**_base_kwargs(), extended=True)
        expected = round(out["raw_score"] * EXTENDED_FACTOR, 2)
        assert out["score"] == expected
        assert out["multipliers"]["extended"] == EXTENDED_FACTOR

    def test_all_multipliers_compose_multiplicatively(self):
        out = compute_swing_score(
            **_base_kwargs(),
            regime_factor=0.8,
            days_to_earnings=5,    # → EARNINGS_FACTOR_LE_7 = 0.75
            extended=True,         # → 0.7
        )
        raw = out["raw_score"]
        expected = round(raw * 0.8 * EARNINGS_FACTOR_LE_7 * EXTENDED_FACTOR, 2)
        assert out["score"] == expected
        assert out["multipliers"] == {
            "regime": 0.8,
            "earnings": EARNINGS_FACTOR_LE_7,
            "extended": EXTENDED_FACTOR,
        }

    def test_score_clamped_to_zero(self):
        out = compute_swing_score(
            rr=0.0, setup_score=0.0, adx_value=None,
            ad_line_slope_pct=None, higher_lows=None,
            institutional_ownership_pct=None,
            regime_factor=0.0,
        )
        assert out["score"] == 0.0
        assert 0.0 <= out["raw_score"] <= 100.0

    def test_score_clamped_to_100(self):
        # Even with regime_factor pushed > 1 (defensive), final ≤ 100.
        out = compute_swing_score(**_base_kwargs(), regime_factor=2.0)
        assert out["score"] <= 100.0

    def test_confidence_uses_post_multiplier_score(self):
        # Big haircut should drop a "high" raw into "speculative".
        out = compute_swing_score(**_base_kwargs(), regime_factor=0.5, days_to_earnings=1)
        assert out["confidence"] in {"speculative", "medium"}
