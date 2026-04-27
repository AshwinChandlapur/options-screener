"""
Unit tests for the CSP and CC strike scorers in `services.scoring.strike`.

Probes:
- Delta bell-curve elbows (sweet spot, shoulders, far OTM/ITM).
- Distance-vs-support / distance-vs-resistance scoring.
- Expected-Move buffer at zero / negative buffer.
- OTM percentage tiering.
- Bid-Ask spread tiering.
- Liquidity (OI / volume + market_open switch).
- ROC factor.
- CSP vs CC direction divergence (puts use negative deltas, calls positive).

Same philosophy as `test_env_score.py`: probe shapes at boundaries, don't pin
exact end-to-end outputs (characterization tests cover that).
"""
from __future__ import annotations

import pytest

from services.scoring.strike import (
    compute_cc_final_score,
    compute_cc_strike_score,
    compute_csp_final_score,
    compute_csp_strike_score,
)


def _csp_neutral_kwargs() -> dict:
    """CSP inputs that produce a near-zero score on every factor.

    Note: with iv_used=NaN the EM factor zeros; with no supports given, support
    awards 0; with delta NaN the Δ factor zeros; spread None → 0; volume/OI 0
    → 0; credit None → ROC 0. OTM at strike == price → 0.
    """
    return {
        "delta": float("nan"),
        "current_price": 100.0,
        "strike": 100.0,
        "iv_used": float("nan"),
        "dte": 30,
        "vol_support_1": None,
        "vol_support_2": None,
        "vol_support_3": None,
        "bid_ask_spread_pct": None,
        "open_interest": 0,
        "market_open": False,
        "volume": 0,
        "credit": None,
    }


def _cc_neutral_kwargs() -> dict:
    return {
        "delta": float("nan"),
        "current_price": 100.0,
        "strike": 100.0,
        "iv_used": float("nan"),
        "dte": 30,
        "vol_resistance_1": None,
        "vol_resistance_2": None,
        "vol_resistance_3": None,
        "bid_ask_spread_pct": None,
        "open_interest": 0,
        "market_open": False,
        "volume": 0,
        "credit": None,
    }


# === CSP =====================================================================

# --- Delta bell-curve (15 pts) ---------------------------------------------

@pytest.mark.parametrize(
    "delta, expected_pts",
    [
        (-0.22, 15.0),    # sweet spot
        (-0.20, 15.0),    # boundary
        (-0.25, 15.0),    # boundary
        (-0.18, 10.0),    # shoulder
        (-0.28, 10.0),    # shoulder
        (-0.12, 5.0),     # outer
        (-0.35, 5.833),   # far ITM tail
        (-0.05, 0.0),     # too close to ATM
        (0.10, 0.0),      # wrong sign
    ],
)
def test_csp_delta_factor_at_elbows(delta: float, expected_pts: float):
    kw = _csp_neutral_kwargs()
    kw["delta"] = delta
    score, _, _ = compute_csp_strike_score(**kw)
    assert score == pytest.approx(expected_pts, abs=0.1)


# --- Support distance (18 pts) ---------------------------------------------

def test_csp_support_at_or_below_strike_full_credit():
    kw = _csp_neutral_kwargs()
    kw["strike"] = 90.0
    kw["vol_support_1"] = 89.0  # 1.1% below strike
    score, _, raw = compute_csp_strike_score(**kw)
    # OTM factor also kicks in: spot 100, strike 90 → 10% OTM → 6.75 pts.
    # Sup factor at gap_pct=1.1 → 18 - 1.1/5 * 8 ≈ 16.24
    assert raw["dist_pct"] == pytest.approx(1.11, abs=0.05)
    assert score > 18.0  # Sup + OTM contributions


def test_csp_no_support_below_with_supports_present_awards_seven():
    kw = _csp_neutral_kwargs()
    kw["strike"] = 90.0
    kw["vol_support_1"] = 95.0  # above strike, ignored for "below strike" set
    score, _, _ = compute_csp_strike_score(**kw)
    # Sup factor: 7 pts (supports exist but none below). OTM 10% → 6.75.
    assert score == pytest.approx(7.0 + 6.75, abs=0.2)


# --- Expected Move buffer (20 pts) -----------------------------------------

def test_csp_em_buffer_zero_sigmas_below_awards_thirteen():
    """sigmas_outside == 0 → exactly at the EM lower bound → 13 pts."""
    kw = _csp_neutral_kwargs()
    kw["current_price"] = 100.0
    kw["iv_used"] = 0.30
    kw["dte"] = 30
    # em = 100 * 0.30 * sqrt(30/365) ≈ 8.6; em_lower ≈ 91.4
    kw["strike"] = 91.4
    score, _, raw = compute_csp_strike_score(**kw)
    assert raw["em_buffer_pct"] == pytest.approx(0.0, abs=2.0)
    # OTM factor (8.6%) ≈ 4.5 + (8.6-5)/5 * 2.25 ≈ 6.12 ; total ≈ 13 + 6.12
    assert score >= 12.0


# --- Bid-Ask spread (23 pts) -----------------------------------------------

@pytest.mark.parametrize(
    "spread, expected_min",
    [
        (0.5, 23.0),
        (1.0, 23.0),
        (3.0, 15.0),
        (5.0, 8.0),
        (8.0, 2.0),
        (12.0, 0.0),
    ],
)
def test_csp_bid_ask_factor_at_elbows(spread: float, expected_min: float):
    kw = _csp_neutral_kwargs()
    kw["bid_ask_spread_pct"] = spread
    score, _, _ = compute_csp_strike_score(**kw)
    assert score >= expected_min - 0.5


# --- Liquidity (5 pts) -----------------------------------------------------

def test_csp_liquidity_uses_oi_when_market_closed():
    kw = _csp_neutral_kwargs()
    kw["open_interest"] = 1500
    kw["volume"] = 0
    kw["market_open"] = False
    score, _, raw = compute_csp_strike_score(**kw)
    assert raw["lq_count"] == 1500
    assert score == pytest.approx(5.0, abs=0.1)


def test_csp_liquidity_uses_volume_when_market_open():
    kw = _csp_neutral_kwargs()
    kw["open_interest"] = 50
    kw["volume"] = 1500
    kw["market_open"] = True
    score, _, raw = compute_csp_strike_score(**kw)
    assert raw["lq_count"] == 1500
    assert score == pytest.approx(5.0, abs=0.1)


# --- ROC factor (10 pts) ---------------------------------------------------

def test_csp_roc_factor_strong_premium():
    kw = _csp_neutral_kwargs()
    kw["strike"] = 100.0
    kw["dte"] = 30
    kw["credit"] = 3.0  # capital = 97; roc = 3/97 * 365/30 * 100 ≈ 37.6 → cap 10
    score, _, raw = compute_csp_strike_score(**kw)
    assert raw["roc_annualized"] >= 30.0
    assert score >= 10.0


# --- Final-blend helpers ---------------------------------------------------

def test_csp_final_score_blend():
    assert compute_csp_final_score(env_score=50.0, strike_score=100.0) == pytest.approx(80.0, abs=0.1)
    assert compute_csp_final_score(env_score=100.0, strike_score=50.0) == pytest.approx(70.0, abs=0.1)


# === CC ======================================================================

# --- Delta bell-curve (15 pts) — positive deltas for calls -----------------

@pytest.mark.parametrize(
    "delta, expected_pts",
    [
        (0.22, 15.0),
        (0.20, 15.0),
        (0.25, 15.0),
        (0.18, 10.0),
        (0.28, 10.0),
        (0.12, 5.0),
        (0.35, 5.833),
        (0.05, 0.0),
        (-0.10, 0.0),  # wrong sign
    ],
)
def test_cc_delta_factor_at_elbows(delta: float, expected_pts: float):
    kw = _cc_neutral_kwargs()
    kw["delta"] = delta
    score, _, _ = compute_cc_strike_score(**kw)
    assert score == pytest.approx(expected_pts, abs=0.1)


# --- CC vs CSP delta divergence --------------------------------------------

def test_cc_and_csp_delta_factor_mirror_signs():
    """Both screeners should award full Δ credit at their respective sweet
    spots: CSP at -0.22, CC at +0.22. Each should give zero on the opposite
    sign."""
    csp_kw = _csp_neutral_kwargs()
    csp_kw["delta"] = -0.22
    csp_score, _, _ = compute_csp_strike_score(**csp_kw)
    assert csp_score == pytest.approx(15.0, abs=0.1)

    csp_kw["delta"] = 0.22
    csp_wrong_sign, _, _ = compute_csp_strike_score(**csp_kw)
    assert csp_wrong_sign == 0.0

    cc_kw = _cc_neutral_kwargs()
    cc_kw["delta"] = 0.22
    cc_score, _, _ = compute_cc_strike_score(**cc_kw)
    assert cc_score == pytest.approx(15.0, abs=0.1)

    cc_kw["delta"] = -0.22
    cc_wrong_sign, _, _ = compute_cc_strike_score(**cc_kw)
    assert cc_wrong_sign == 0.0


# --- CC resistance distance (18 pts) ---------------------------------------

def test_cc_resistance_above_strike_full_credit_with_bonus():
    """When all resistances sit at or below the strike, scorer adds a +5 bonus
    on top of the 18-pt full-credit gap_pct<=0 branch."""
    kw = _cc_neutral_kwargs()
    kw["current_price"] = 100.0
    kw["strike"] = 110.0
    # Resistance is above price (so it counts) but at/below strike
    kw["vol_resistance_1"] = 105.0
    score, _, raw = compute_cc_strike_score(**kw)
    # OTM factor: 10% → 6.75 pts. Res factor: 18 + 5 (all-below-strike bonus) = 23.
    assert raw["dist_pct"] is not None
    assert score >= 23.0 + 6.75 - 0.5


# --- CC OTM factor (calls go OTM upward) -----------------------------------

def test_cc_otm_factor_calls_above_spot():
    kw = _cc_neutral_kwargs()
    kw["current_price"] = 100.0
    kw["strike"] = 115.0  # 15% OTM upward
    score, _, raw = compute_cc_strike_score(**kw)
    assert raw["otm_pct"] == pytest.approx(15.0, abs=0.1)
    # OTM at >=15% → full 9 pts.
    assert score >= 9.0


# --- CC final-blend helper -------------------------------------------------

def test_cc_final_score_blend():
    assert compute_cc_final_score(env_score=50.0, strike_score=100.0) == pytest.approx(80.0, abs=0.1)
    assert compute_cc_final_score(env_score=0.0, strike_score=0.0) == 0.0
