"""
Strike-quality scorers + final-blend helpers — v3 lean model (see ADR-0007).

Both CSP and CC scorers share the same factor structure (Δ, BA, LQ, ROC) with
direction-specific math: CSP uses negative deltas with capital basis = strike,
CC uses positive deltas with capital basis = current_price.

v3 reduced Strike from 7 factors to 4 (Δ 20 + BA 30 + LQ 15 + ROC 35 = 100).
Dropped factors:
- EM Buffer: deterministic at the configured ideal_delta — adds no signal
  beyond Δ position. Removing it fixes the 44%-redundancy stack identified
  in the quant audit (Δ + EM + %OTM all measured the same delta-position).
- %OTM from Spot: deterministic function of Δ + IV; redundant with Δ.
- S/R Distance: fragile swing-detection heuristic; high implementation cost
  for low signal value.

The fields `em_buffer_pct`, `dist_pct`, and `otm_pct` continue to be computed
and returned in the response payload so the frontend table columns remain
populated for diagnostic visibility — they simply contribute 0 to the score.

Direction-aware divergences (kept):
- Δ ideal: CSP −0.225, CC +0.225 (sign flip, symmetric bell)
- ROC capital basis: CSP = strike − credit, CC = current_price − credit

Legacy parameters `vol_support_*` (CSP) / `vol_resistance_*` (CC) and
`iv_used` are accepted in the signature for back-compat but no longer affect
the score. They will be removed in a future cleanup once all call sites are
updated.

DITM strike scoring is intentionally *not* in this module yet.
"""
from __future__ import annotations

import math

__all__ = [
    "compute_csp_strike_score",
    "compute_csp_final_score",
    "compute_cc_strike_score",
    "compute_cc_final_score",
]


# ---------------------------------------------------------------------------
# Shared scorer fragments (identical math for CSP and CC; only inputs differ)
# ---------------------------------------------------------------------------


def _score_bid_ask(spread_pct: float | None) -> float:
    """Bid-Ask Spread % — 30 pts. Lower spread = better execution."""
    if spread_pct is None or math.isnan(spread_pct):
        return 0.0
    if spread_pct <= 1.0:
        return 30.0
    if spread_pct <= 3.0:
        return 20.0 + (3.0 - spread_pct) / 2.0 * 10.0     # 30 → 20
    if spread_pct <= 5.0:
        return 11.0 + (5.0 - spread_pct) / 2.0 * 9.0      # 20 → 11
    if spread_pct <= 8.0:
        return 3.0 + (8.0 - spread_pct) / 3.0 * 8.0       # 11 → 3
    return 0.0


def _score_liquidity(market_open: bool, volume: int, open_interest: int) -> tuple[float, int]:
    """OI / Volume circuit-breaker — 15 pts. Returns (pts, liquidity_count_used)."""
    liquidity_count = volume if (market_open and volume > 0) else open_interest
    if liquidity_count >= 1000:
        p = 15.0
    elif liquidity_count >= 500:
        p = 10.5 + (liquidity_count - 500) / 500.0 * 4.5   # 10.5 → 15.0
    elif liquidity_count >= 200:
        p = 6.0 + (liquidity_count - 200) / 300.0 * 4.5    # 6.0 → 10.5
    elif liquidity_count >= 100:
        p = (liquidity_count - 100) / 100.0 * 6.0          # 0 → 6.0
    else:
        p = 0.0
    return p, liquidity_count


def _score_roc(roc: float) -> float:
    """Annualized ROC — 35 pts. Cliff-fixed (#6): adds 2–4% ramp."""
    if roc >= 20:
        return 35.0
    if roc >= 14:
        return 24.5 + (roc - 14) / 6.0 * 10.5    # 24.5 → 35.0
    if roc >= 8:
        return 14.0 + (roc - 8) / 6.0 * 10.5     # 14.0 → 24.5
    if roc >= 4:
        return 3.5 + (roc - 4) / 4.0 * 10.5      # 3.5 → 14.0
    if roc >= 2:
        return (roc - 2) / 2.0 * 3.5             # 0 → 3.5 (cliff fix)
    return 0.0


def _score_delta_symmetric(delta: float, ideal: float) -> float:
    """Δ symmetric bell — 20 pts. Fixes audit #7 asymmetry between wings.

    Sweet band ±0.025 around ideal = full credit; widens 13 → 7 → 0 by 0.05
    bands. The legacy gate (-0.35 to -0.10 for CSP, mirrored for CC) is enforced
    by the candidate filter upstream; this scorer awards 0 outside ±0.125.
    """
    if math.isnan(delta):
        return 0.0
    offset = abs(delta - ideal)
    if offset <= 0.025:
        return 20.0
    if offset <= 0.075:
        return 13.0
    if offset <= 0.125:
        return 7.0
    return 0.0


def _diag_em_buffer_pct(current_price: float, strike: float, iv_used: float, dte: int, *, side: str) -> float:
    """Diagnostic only — computes 0.5×EM-referenced sigmas_outside × 100.

    Returned in the response for visibility, but does NOT contribute to score
    in v3 (see ADR-0007). `side='csp'` uses lower boundary, `side='cc'` upper.
    """
    if math.isnan(iv_used) or iv_used <= 0 or dte <= 0:
        return float('nan')
    em = current_price * iv_used * math.sqrt(dte / 365.0)
    if side == 'cc':
        boundary = current_price + 0.5 * em
        sigmas_outside = (strike - boundary) / em
    else:
        boundary = current_price - 0.5 * em
        sigmas_outside = (boundary - strike) / em
    return round(sigmas_outside * 100, 2)


# ---------------------------------------------------------------------------
# CSP strike scorer
# ---------------------------------------------------------------------------


def compute_csp_strike_score(
    *,
    delta: float,
    current_price: float,
    strike: float,
    iv_used: float,
    dte: int,
    vol_support_1: float | None = None,   # IGNORED in v3 (S/R dropped)
    vol_support_2: float | None = None,   # IGNORED in v3
    vol_support_3: float | None = None,   # IGNORED in v3
    bid_ask_spread_pct: float | None,
    open_interest: int,
    market_open: bool,
    volume: int,
    credit: float | None = None,
) -> tuple[float, str, dict]:
    """
    CSP Strike Safety Score 0–100. Weights: Δ 20 + BA 30 + LQ 15 + ROC 35 = 100.
    """
    _ = vol_support_1, vol_support_2, vol_support_3  # explicitly unused in v3
    bk: dict[str, float] = {}

    p_delta = _score_delta_symmetric(delta, ideal=-0.225)
    bk['Δ'] = p_delta

    p_ba = _score_bid_ask(bid_ask_spread_pct)
    bk['BA'] = p_ba

    p_lq, liquidity_count = _score_liquidity(market_open, volume, open_interest)
    bk['LQ'] = p_lq

    p_roc = 0.0
    _roc_annualized: float = float('nan')
    if credit is not None and credit > 0 and dte > 0:
        capital_per_share = strike - credit
        if capital_per_share > 0:
            roc = (credit / capital_per_share) * (365.0 / dte) * 100.0
            _roc_annualized = round(roc, 2)
            p_roc = _score_roc(roc)
    bk['ROC'] = p_roc

    score = p_delta + p_ba + p_lq + p_roc

    # Diagnostic-only fields (kept in response for frontend column visibility)
    _em_buffer_pct = _diag_em_buffer_pct(current_price, strike, iv_used, dte, side='csp')
    otm_pct = (current_price - strike) / current_price * 100.0 if current_price > 0 else 0.0

    detail = ' '.join(f"{k}:{round(v)}" for k, v in bk.items())
    raw = {
        'dist_pct': None,                # S/R dropped — preserved as None for back-compat
        'em_buffer_pct': _em_buffer_pct, # diagnostic only
        'otm_pct': otm_pct,              # diagnostic only
        'lq_count': liquidity_count,
        'roc_annualized': _roc_annualized,
    }
    return round(max(0.0, min(100.0, score)), 1), detail, raw


def compute_csp_final_score(env_score: float, strike_score: float) -> float:
    """Final Score = 0.4 × Env Score + 0.6 × Strike Score."""
    return round(0.4 * env_score + 0.6 * strike_score, 1)


# ---------------------------------------------------------------------------
# CC strike scorer
# ---------------------------------------------------------------------------


def compute_cc_strike_score(
    *,
    delta: float,
    current_price: float,
    strike: float,
    iv_used: float,
    dte: int,
    vol_resistance_1: float | None = None,   # IGNORED in v3 (S/R dropped)
    vol_resistance_2: float | None = None,   # IGNORED in v3
    vol_resistance_3: float | None = None,   # IGNORED in v3
    bid_ask_spread_pct: float | None,
    open_interest: int,
    market_open: bool,
    volume: int,
    credit: float | None = None,
) -> tuple[float, str, dict]:
    """
    CC Strike Safety Score 0–100. Weights: Δ 20 + BA 30 + LQ 15 + ROC 35 = 100.

    ROC capital basis = current_price (the underlying held to write the call),
    not strike. This differs from CSP, which uses strike − credit (cash-secured).
    """
    _ = vol_resistance_1, vol_resistance_2, vol_resistance_3  # explicitly unused in v3
    bk: dict[str, float] = {}

    p_delta = _score_delta_symmetric(delta, ideal=+0.225)
    bk['Δ'] = p_delta

    p_ba = _score_bid_ask(bid_ask_spread_pct)
    bk['BA'] = p_ba

    p_lq, liquidity_count = _score_liquidity(market_open, volume, open_interest)
    bk['LQ'] = p_lq

    p_roc = 0.0
    _roc_annualized: float = float('nan')
    if credit is not None and credit > 0 and dte > 0 and current_price > 0:
        capital_per_share = current_price - credit
        if capital_per_share > 0:
            roc = (credit / capital_per_share) * (365.0 / dte) * 100.0
            _roc_annualized = round(roc, 2)
            p_roc = _score_roc(roc)
    bk['ROC'] = p_roc

    score = p_delta + p_ba + p_lq + p_roc

    # Diagnostic-only fields (kept in response for frontend column visibility)
    _em_buffer_pct = _diag_em_buffer_pct(current_price, strike, iv_used, dte, side='cc')
    otm_pct = (strike - current_price) / current_price * 100.0 if current_price > 0 else 0.0

    detail = ' '.join(f"{k}:{round(v)}" for k, v in bk.items())
    raw = {
        'dist_pct': None,                # S/R dropped — preserved as None for back-compat
        'em_buffer_pct': _em_buffer_pct, # diagnostic only
        'otm_pct': otm_pct,              # diagnostic only
        'lq_count': liquidity_count,
        'roc_annualized': _roc_annualized,
    }
    return round(max(0.0, min(100.0, score)), 1), detail, raw


def compute_cc_final_score(env_score: float, strike_score: float) -> float:
    """CC Final Score = 0.4 × Env Score + 0.6 × Strike Score."""
    return round(0.4 * env_score + 0.6 * strike_score, 1)
