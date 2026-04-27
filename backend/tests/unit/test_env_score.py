"""
Unit tests for `services.scoring.env.compute_env_score`.

Probes:
- Each factor's bell-curve elbows (HV Rank, IV/HV ratio, SMA alignment, 52W,
  RSI, OI, DTE).
- Direction-aware divergence: 'csp' vs 'cc' produce different 52W and RSI scores
  for the same indicator inputs.
- Earnings penalty.
- Stale-IV gate (iv_stale=True forces the IV/HV factor to 0 regardless of value).

These tests do NOT pin the exact 0–100 outputs at every input — that's the
characterization tests' job. They probe the *shape* of each factor at its
documented boundaries so that calibration drift is caught at the unit level.
"""
from __future__ import annotations

import pytest

from services.scoring.env import compute_env_score


# Default inputs that produce a "neutral" environment (zero on every factor).
# Individual tests override one field at a time.
def _neutral_kwargs() -> dict:
    return {
        "iv_rank": 0.0,
        "iv_hv_ratio": 0.0,
        "price_above_sma50": False,
        "sma50_above_sma200": False,
        "dist_from_52w_high_pct": -50.0,
        "rsi": 50.0,            # CSP: 42–62 sweet spot → 10 pts
        "chain_median_oi": 0.0,
        "earnings_within_dte": False,
        "direction": "csp",
        "dte": 0,
        "iv_stale": False,
    }


# --- HV Rank factor (22 pts) -----------------------------------------------

@pytest.mark.parametrize(
    "iv_rank, expected_min",
    [
        (80.0, 22.0),   # plateau
        (95.0, 22.0),   # plateau
        (60.0, 13.0),   # ramp start
        (40.0, 6.5),    # ramp middle
        (20.0, 0.0),    # ramp floor
        (10.0, 0.0),    # below threshold → 0
    ],
)
def test_env_hv_rank_factor_at_elbows(iv_rank: float, expected_min: float):
    kw = _neutral_kwargs()
    kw["iv_rank"] = iv_rank
    score, _ = compute_env_score(**kw)
    # 50 RSI also contributes 10 pts; subtract that to isolate HV.
    isolated = score - 10.0
    assert isolated >= expected_min - 0.1
    assert isolated <= expected_min + 22.0  # upper sanity


# --- IV/HV ratio factor (28 pts) -------------------------------------------

@pytest.mark.parametrize(
    "ratio, expected_min",
    [
        (1.7, 28.0),
        (2.5, 28.0),    # plateau
        (1.4, 14.0),
        (1.1, 6.7),
        (0.9, 2.8),
        (0.5, 0.0),
    ],
)
def test_env_iv_hv_ratio_factor_at_elbows(ratio: float, expected_min: float):
    kw = _neutral_kwargs()
    kw["iv_hv_ratio"] = ratio
    score, _ = compute_env_score(**kw)
    isolated = score - 10.0  # subtract the 50-RSI plateau
    assert isolated >= expected_min - 0.1


def test_env_iv_stale_zeros_iv_hv_factor():
    """When iv_stale=True, a strong IV/HV ratio that would normally award 28 pts
    must contribute zero."""
    kw = _neutral_kwargs()
    kw["iv_hv_ratio"] = 2.0
    kw["iv_stale"] = True
    score, _ = compute_env_score(**kw)
    # Only the 50-RSI sweet spot contributes (10 pts).
    assert score == pytest.approx(10.0, abs=0.1)


# --- SMA alignment factor (15 pts, categorical) ----------------------------

def test_env_sma_full_alignment_awards_15():
    kw = _neutral_kwargs()
    kw["price_above_sma50"] = True
    kw["sma50_above_sma200"] = True
    score, _ = compute_env_score(**kw)
    assert score == pytest.approx(10.0 + 15.0, abs=0.1)


def test_env_sma_price_only_awards_9():
    kw = _neutral_kwargs()
    kw["price_above_sma50"] = True
    score, _ = compute_env_score(**kw)
    assert score == pytest.approx(10.0 + 9.0, abs=0.1)


def test_env_sma_50_above_200_only_awards_5():
    kw = _neutral_kwargs()
    kw["sma50_above_sma200"] = True
    score, _ = compute_env_score(**kw)
    assert score == pytest.approx(10.0 + 5.0, abs=0.1)


# --- Direction-aware divergence: 52W and RSI -------------------------------

def test_env_direction_diverges_at_52w_proximity():
    """At 0% below the 52W high, CSP rewards strength (10 pts) while CC
    penalizes the lack of consolidation (4 pts)."""
    kw = _neutral_kwargs()
    kw["dist_from_52w_high_pct"] = 0.0
    csp_score, _ = compute_env_score(**kw)

    kw["direction"] = "cc"
    cc_score, _ = compute_env_score(**kw)

    # CSP: full 10 pts on 52W. CC: only 4 pts.
    assert csp_score - cc_score == pytest.approx(6.0, abs=0.2)


def test_env_direction_diverges_at_rsi_60():
    """RSI 60: in the CSP sweet-spot (42–62 → 10 pts) but on the CC ceiling
    decay (58 < rsi <= 70 → 10 - (60-58)/12 * 10 ≈ 8.33)."""
    kw = _neutral_kwargs()
    kw["rsi"] = 60.0
    csp_score, _ = compute_env_score(**kw)

    kw["direction"] = "cc"
    cc_score, _ = compute_env_score(**kw)

    # CSP: 10 pts on RSI. CC: ~8.33 pts.
    assert csp_score > cc_score


# --- Chain OI factor (8 pts, log scale) ------------------------------------

def test_env_chain_oi_log_scale_caps_at_5000():
    kw = _neutral_kwargs()
    kw["chain_median_oi"] = 5000.0
    score_at_cap, _ = compute_env_score(**kw)

    kw["chain_median_oi"] = 50000.0
    score_above_cap, _ = compute_env_score(**kw)

    # Both should award the full 8 pts (log10 fraction is clamped to 1.0).
    assert score_at_cap == pytest.approx(score_above_cap, abs=0.1)


def test_env_chain_oi_zero_awards_zero():
    kw = _neutral_kwargs()
    kw["chain_median_oi"] = 0.0
    score, _ = compute_env_score(**kw)
    # No OI contribution; only the 50-RSI plateau.
    assert score == pytest.approx(10.0, abs=0.1)


# --- DTE factor (7 pts) ----------------------------------------------------

@pytest.mark.parametrize(
    "dte, expected_pts",
    [
        (35, 7.0),     # sweet spot center
        (25, 4.2),     # mid tier
        (50, 4.2),     # mid tier (other side)
        (16, 2.1),     # outer tier
        (70, 2.1),     # outer tier (other side)
        (10, 0.0),     # below threshold
        (90, 0.0),     # above threshold
    ],
)
def test_env_dte_sweet_spot(dte: int, expected_pts: float):
    kw = _neutral_kwargs()
    kw["dte"] = dte
    score, _ = compute_env_score(**kw)
    # Subtract 50-RSI plateau (10 pts) to isolate DTE.
    assert score - 10.0 == pytest.approx(expected_pts, abs=0.1)


# --- Earnings penalty ------------------------------------------------------

def test_env_earnings_penalty_applied():
    kw = _neutral_kwargs()
    score_no_earnings, _ = compute_env_score(**kw)

    kw["earnings_within_dte"] = True
    score_with_earnings, detail = compute_env_score(**kw)

    assert score_no_earnings - score_with_earnings == pytest.approx(15.0, abs=0.1)
    assert "Ear:-15" in detail


# --- Smoke: full-score CSP environment -------------------------------------

def test_env_full_score_csp_top_environment():
    """Maxed-out inputs in every factor → score should be ≥99 (allowing
    for small rounding in the rescaled curves)."""
    kw = _neutral_kwargs()
    kw["iv_rank"] = 95.0           # 22
    kw["iv_hv_ratio"] = 2.0        # 28
    kw["price_above_sma50"] = True
    kw["sma50_above_sma200"] = True  # 15
    kw["dist_from_52w_high_pct"] = 0.0  # 10 (CSP)
    kw["rsi"] = 50.0               # 10
    kw["chain_median_oi"] = 10000.0  # 8
    kw["dte"] = 35                 # 7
    score, _ = compute_env_score(**kw)
    assert score >= 99.0
    assert score <= 100.0
