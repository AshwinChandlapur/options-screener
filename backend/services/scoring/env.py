"""
Environment scorer — v3 lean model (see ADR-0007).

`compute_env_score` is direction-aware via the `direction` arg ('csp' | 'cc');
it shapes the Trend (52W) and RSI curves accordingly.

v3 reduced ENV from 7 factors to 4 (IV/HV 35 + Trend 25 + RSI 20 + Chain
OI 20 = 100). Dropped: HV Rank (correlated with IV/HV), SMA Alignment
(collapsed into Trend), DTE Sweet Spot (now a hard filter via min/max DTE).
The legacy parameters `iv_rank`, `price_above_sma50`, `sma50_above_sma200`,
and `dte` are kept in the signature for back-compat but ignored — call
sites do not need to change. They will be removed in a future cleanup
once all call sites are updated.

DITM environment scoring is intentionally *not* in this module yet — the
live implementation lives inline in `services.ditm_service.py`.
"""
from __future__ import annotations

import math

from .config import EARNINGS_PENALTY

__all__ = ["compute_env_score"]


def compute_env_score(
    *,
    iv_rank: float | None,             # IGNORED in v3 (HV Rank dropped) — kept for back-compat
    iv_hv_ratio: float | None,
    price_above_sma50: bool,           # IGNORED in v3 (SMA dropped) — kept for back-compat
    sma50_above_sma200: bool,          # IGNORED in v3 (SMA dropped) — kept for back-compat
    dist_from_52w_high_pct: float,
    rsi: float,
    chain_median_oi: float,
    earnings_within_dte: bool,
    direction: str = 'csp',            # 'csp' or 'cc' — affects Trend and RSI curves
    dte: int | None = None,            # IGNORED in v3 (DTE Sweet Spot dropped) — kept for back-compat
    iv_stale: bool = False,            # If True, IV/HV pts forced to 0
) -> tuple[float, str]:
    """
    Environment Score 0–100 (+earnings penalty up to −15).
    Direction-aware: CSP rewards strength near 52W high; CC rewards
    consolidation 5–15% below the high.

    Weights (v3): IV/HV 35 + Trend 25 + RSI 20 + Chain OI 20 = 100
    Penalty: Earnings within DTE = −15

    Note: `iv_rank`, `price_above_sma50`, `sma50_above_sma200`, and `dte`
    are accepted for call-site back-compat but unused in v3 scoring.
    """
    _ = iv_rank, price_above_sma50, sma50_above_sma200, dte  # explicitly unused
    direction = direction.lower()
    score = 0.0
    bk: dict[str, float] = {}

    # --- IV / HV Ratio (35 pts) — v3 rescale of v2 0.8/1.0/1.1/1.2/1.3 curve ---
    # Stale-IV: when iv_stale=True (IV NaN or ≤0.01), award 0 pts and let UI flag the row.
    p = 0.0
    if not iv_stale and iv_hv_ratio is not None and not math.isnan(iv_hv_ratio):
        if iv_hv_ratio >= 1.3:
            p = 35.0
        elif iv_hv_ratio >= 1.2:
            p = 22.5 + (iv_hv_ratio - 1.2) / 0.1 * 12.5    # 22.5 → 35.0
        elif iv_hv_ratio >= 1.1:
            p = 12.5 + (iv_hv_ratio - 1.1) / 0.1 * 10.0    # 12.5 → 22.5
        elif iv_hv_ratio >= 1.0:
            p = 5.0 + (iv_hv_ratio - 1.0) / 0.1 * 7.5      # 5.0 → 12.5
        elif iv_hv_ratio >= 0.8:
            p = (iv_hv_ratio - 0.8) / 0.2 * 5.0            # 0 → 5.0
    score += p; bk['IH'] = p

    # --- Trend / 52W High Distance (25 pts) — direction-aware ---
    # Replaces the v2 SMA Alignment (15) + 52W (10) into a single direction-aware
    # factor. SMA was redundant signal under the lean model; 52W direction-aware
    # captures the same trend information with a smooth curve.
    p = 0.0
    if not math.isnan(dist_from_52w_high_pct):
        pct_below = abs(min(dist_from_52w_high_pct, 0.0))
        if direction == 'cc':
            # CC: PENALIZE near-high (assignment risk — fix #5).
            # Sweet spot at 5–15% consolidation; smooth ramps both sides.
            if pct_below <= 5:
                p = 0.0  # was 4.0 in v2 — finding #5: full penalty for assignment risk
            elif pct_below <= 15:
                p = (pct_below - 5.0) / 10.0 * 25.0           # 0 → 25
            elif pct_below <= 25:
                p = 25.0 - (pct_below - 15.0) / 10.0 * 15.0   # 25 → 10
            elif pct_below <= 35:
                p = 10.0 - (pct_below - 25.0) / 10.0 * 10.0   # 10 → 0
        else:
            # CSP: reward strength near the 52W high (uptrend).
            # Smooth continuous decay from 25 at ≤5% to 0 at 30%.
            if pct_below <= 5:
                p = 25.0
            elif pct_below <= 10:
                p = 25.0 - (pct_below - 5.0) / 5.0 * 6.667        # 25.0 → 18.333
            elif pct_below <= 20:
                p = 18.333 - (pct_below - 10.0) / 10.0 * 6.667    # 18.333 → 11.667
            elif pct_below <= 30:
                p = 11.667 - (pct_below - 20.0) / 10.0 * 11.667   # 11.667 → 0
    score += p; bk['Tr'] = p

    # --- RSI(14) (20 pts) — direction-aware, cliff-fixed (#2, #8) ---
    p = 0.0
    if not math.isnan(rsi):
        if direction == 'cc':
            # CC sweet spot 38–58; smooth ramps both sides.
            # Ceiling extended from 70 to 75 (fix #8) so AAPL/MSFT in normal trends
            # (RSI 62–68) earn meaningful pts.
            if 38 <= rsi <= 58:
                p = 20.0
            elif 30 <= rsi < 38:
                p = (rsi - 30.0) / 8.0 * 20.0      # 0 → 20 (continuous)
            elif 58 < rsi <= 75:
                p = (75.0 - rsi) / 17.0 * 20.0     # 20 → 0
        else:
            # CSP sweet spot 42–62; smooth ramps both sides.
            # Cliff fix #2: removed the 30–35 floor of 2 pts that created a
            # 4-pt jump at RSI=35. Now: <35 = 0, 35–42 ramps continuously.
            if 42 <= rsi <= 62:
                p = 20.0
            elif 35 <= rsi < 42:
                p = (rsi - 35.0) / 7.0 * 20.0      # 0 → 20 (continuous)
            elif 62 < rsi <= 75:
                p = (75.0 - rsi) / 13.0 * 20.0     # 20 → 0
    score += p; bk['RSI'] = p

    # --- Chain Median OI (20 pts) — circuit breaker, scaled from 8 ---
    p = 0.0
    if not math.isnan(chain_median_oi) and chain_median_oi > 0:
        p = min(math.log10(chain_median_oi) / math.log10(5000), 1.0) * 20.0
    score += p; bk['OI'] = p

    # --- Earnings penalty (applied on top) ---
    earn_p = 0.0
    if earnings_within_dte:
        earn_p = EARNINGS_PENALTY
        score += earn_p

    detail = ' '.join(f"{k}:{round(v)}" for k, v in bk.items())
    if earn_p != 0:
        detail += f' Ear:{round(earn_p)}'
    return round(score, 1), detail
