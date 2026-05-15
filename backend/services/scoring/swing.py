"""
Swing-trade composite scoring (v2.0.0).

Composite (raw) = R:R (40) + setup_score (30) + context (20) + institutional (10)

  R:R 40            : piecewise — 2.5→0, 3.0→25, 4.0→35, 5.0+→40
  setup 30          : best_setup score scaled to 0–30
  context 20        : RS vs SPY (10) + EMA alignment (10)
  institutional 10  : A/D line slope (5) + institutional ownership snapshot (5)

Cross-bucket multipliers (v2):

  final = raw × regime_factor × earnings_factor × extended_factor
        clamped to [0, 100]

  regime_factor    : 0.6–1.0, from services.swing.regime (composite-multiplier curve)
  earnings_factor  : 1.0 / 0.9 / 0.75 / 0.5 by days-to-earnings bucket
  extended_factor  : 0.7 if current price is >3% past structural trigger, else 1.0

The R:R *gate* (RR_HARD_GATE) is NOT here — it's set per-regime in
`services.swing.regime.RR_GATE_BY_REGIME` and enforced in the runner.

Hard gates handled by the runner BEFORE scoring:
  - R:R below the regime-specific gate
  - setup_score < 40
  - missing essentials (ATR, EMAs, ADV)
  - earnings within 1 day (any setup) or 7 days (reversion only)
  - reversion setup in risk_off regime
"""
from __future__ import annotations

SWING_SCORER_VERSION: str = "2.0.0"

SWING_WEIGHTS: dict[str, float] = {
    "RR": 40.0,
    "SETUP": 30.0,
    "CTX": 20.0,
    "INST": 10.0,
}
SWING_MAX: float = sum(SWING_WEIGHTS.values())  # 100.0


# --- Earnings multiplier bands (days to next earnings) -----------------------
EARNINGS_FACTOR_LE_3: float = 0.5
EARNINGS_FACTOR_LE_7: float = 0.75
EARNINGS_FACTOR_LE_14: float = 0.9

# --- Chasing penalty ---------------------------------------------------------
EXTENDED_FACTOR: float = 0.7


def _rr_points(rr: float) -> float:
    """Piecewise-linear R:R points."""
    if rr <= 2.5:
        return 0.0
    if rr >= 5.0:
        return 40.0
    if rr <= 3.0:
        # 2.5 → 0, 3.0 → 25
        return 25.0 * (rr - 2.5) / 0.5
    if rr <= 4.0:
        # 3.0 → 25, 4.0 → 35
        return 25.0 + 10.0 * (rr - 3.0)
    # 4.0 → 35, 5.0 → 40
    return 35.0 + 5.0 * (rr - 4.0)


def _setup_points(setup_score: float) -> float:
    """Setup score (0–100) → 0–30 points."""
    return max(0.0, min(30.0, setup_score * 0.30))


def _context_points(rs_vs_spy: float | None, ema_alignment_score: float | None) -> float:
    """RS (10) + EMA stack (10)."""
    rs_pts = 0.0
    if rs_vs_spy is not None and rs_vs_spy == rs_vs_spy:
        if rs_vs_spy >= 1.2:
            rs_pts = 10.0
        elif rs_vs_spy >= 1.0:
            rs_pts = 5.0 + 5.0 * (rs_vs_spy - 1.0) / 0.2
        elif rs_vs_spy >= 0.9:
            rs_pts = 5.0 * (rs_vs_spy - 0.9) / 0.1
    ema_pts = 0.0
    if ema_alignment_score is not None:
        # 0–9 scale → 0–10 (slight reward for max)
        ema_pts = max(0.0, min(10.0, ema_alignment_score * 10.0 / 9.0))
    return rs_pts + ema_pts


def _institutional_points(
    ad_line_slope_pct: float | None,
    institutional_ownership_pct: float | None,
) -> float:
    """A/D slope (5) + ownership snapshot (5).

    A/D: slope >= 5% → 5; 0–5% → linear; <0 → 0.
    Ownership: ≥70% → 5; 40–70% → linear; <40% → 0.
    """
    ad_pts = 0.0
    if ad_line_slope_pct is not None and ad_line_slope_pct == ad_line_slope_pct:
        if ad_line_slope_pct >= 5.0:
            ad_pts = 5.0
        elif ad_line_slope_pct > 0:
            ad_pts = 5.0 * ad_line_slope_pct / 5.0
    own_pts = 0.0
    if institutional_ownership_pct is not None and institutional_ownership_pct == institutional_ownership_pct:
        if institutional_ownership_pct >= 70:
            own_pts = 5.0
        elif institutional_ownership_pct >= 40:
            own_pts = 5.0 * (institutional_ownership_pct - 40) / 30.0
    return ad_pts + own_pts


def earnings_factor(days_to_earnings: int | None) -> float:
    """Multiplier based on proximity to next earnings report."""
    if days_to_earnings is None or days_to_earnings < 0:
        return 1.0
    if days_to_earnings <= 3:
        return EARNINGS_FACTOR_LE_3
    if days_to_earnings <= 7:
        return EARNINGS_FACTOR_LE_7
    if days_to_earnings <= 14:
        return EARNINGS_FACTOR_LE_14
    return 1.0


def compute_swing_score(
    rr: float,
    setup_score: float,
    rs_vs_spy: float | None,
    ema_alignment_score: float | None,
    ad_line_slope_pct: float | None,
    institutional_ownership_pct: float | None,
    *,
    regime_factor: float = 1.0,
    days_to_earnings: int | None = None,
    extended: bool = False,
) -> dict:
    """
    Returns:
      score        : float 0–100 (post-multipliers)
      raw_score    : float 0–100 (pre-multipliers)
      breakdown    : dict of factor → points (raw, pre-multipliers)
      multipliers  : dict regime/earnings/extended → factor used
      confidence   : "high" | "medium" | "speculative"

    Confidence tiers reference the POST-multiplier score and rr.
    """
    rr_pts = round(_rr_points(rr), 2)
    setup_pts = round(_setup_points(setup_score), 2)
    ctx_pts = round(_context_points(rs_vs_spy, ema_alignment_score), 2)
    inst_pts = round(_institutional_points(ad_line_slope_pct, institutional_ownership_pct), 2)
    raw = round(rr_pts + setup_pts + ctx_pts + inst_pts, 2)

    e_factor = earnings_factor(days_to_earnings)
    x_factor = EXTENDED_FACTOR if extended else 1.0
    final = raw * regime_factor * e_factor * x_factor
    final = round(max(0.0, min(100.0, final)), 2)

    if final >= 75 and rr >= 3.5 and setup_score >= 70:
        confidence = "high"
    elif final >= 55:
        confidence = "medium"
    else:
        confidence = "speculative"

    return {
        "score": final,
        "raw_score": raw,
        "breakdown": {
            "rr": rr_pts,
            "setup": setup_pts,
            "context": ctx_pts,
            "institutional": inst_pts,
        },
        "multipliers": {
            "regime": round(regime_factor, 3),
            "earnings": round(e_factor, 3),
            "extended": round(x_factor, 3),
        },
        "confidence": confidence,
    }
