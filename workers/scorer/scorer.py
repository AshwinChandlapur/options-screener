"""ACS computation — pure functions (Phase 6).

Implements NARRATIVE_METHODOLOGY.md §5.

Components:
    A  Attention persistence  — decay_weighted_density_14d * A_max
    B  Contributor quality   — unique_authors / log(mentions) * (1-G) * B_max
    C  Narrative strength    — stage_map[stage] * stage_confidence * (C_max / 20)
    D  Thesis quality        — (0.6*r_rb + 0.2*r_rB + 0.2*dd_norm) * D_max
    E  Market confirmation   — 0 (deferred to Phase 6.1)

Adjustments (multipliers, in order):
    G > 0.65              → × 0.6
    acceleration_7d < 0   → × 0.8  (proxy for 3-day negative streak)
    lifecycle_stage > 3   → × 0.5

CI bands: acs ± 15% (heuristic; bootstrap resampling deferred to Phase 6.1).
Time decay: ACS(t) = ACS_raw * e^{-0.07 * t} where t = days since acs_scored_at.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone

# stage_map per NARRATIVE_METHODOLOGY.md §5.1 — stages 2 and 3 are target window.
_STAGE_MAP: dict[int, float] = {1: 10, 2: 18, 3: 20, 4: 10, 5: 5, 6: 2}
_DECAY_RATE: float = 0.07  # half-life ≈ 10 days per §5.4


@dataclass
class AcsResult:
    ticker: str
    acs: float
    acs_ci_lower: float
    acs_ci_upper: float
    decay_acs: float
    components: dict[str, float]   # {A, B, C, D, E}
    dominant_signal: str
    flags: list[str] = field(default_factory=list)


def compute_acs(doc: dict, weights: dict[str, float]) -> AcsResult:
    """Compute ACS for a single ticker_timeline Cosmos document.

    Args:
        doc:     ticker_timeline document (may have partial Phase 3–5 fields).
        weights: component max weights from Key Vault (or defaults).

    Returns:
        AcsResult with all fields populated.
    """
    ticker: str = doc.get("ticker", "")
    a_max: float = weights.get("A_max", 25.0)
    b_max: float = weights.get("B_max", 20.0)
    c_max: float = weights.get("C_max", 20.0)
    d_max: float = weights.get("D_max", 20.0)

    # --- Component A: attention persistence ---
    dwd_14d: float = doc.get("decay_weighted_density_14d") or 0.0
    comp_a = min(dwd_14d, 1.0) * a_max

    # --- Component B: contributor quality ---
    unique_authors: int = doc.get("unique_authors_14d") or 0
    mentions_14d: int = doc.get("mentions_14d") or 0
    gini: float = doc.get("gini_14d") or 0.0
    if mentions_14d > 1 and unique_authors > 0:
        comp_b = (unique_authors / math.log(mentions_14d)) * (1.0 - gini) * b_max
        comp_b = min(comp_b, b_max)
    else:
        comp_b = 0.0

    # --- Component C: narrative strength (lifecycle) ---
    stage: int = doc.get("lifecycle_stage") or 0
    stage_conf: float = doc.get("stage_confidence") or 0.0
    if stage in _STAGE_MAP:
        comp_c = (_STAGE_MAP[stage] / 20.0) * stage_conf * c_max
    else:
        comp_c = 0.0

    # --- Component D: thesis quality ---
    r_rb: float = doc.get("conviction_researched_bull_ratio") or 0.0
    r_rB: float = doc.get("conviction_researched_bear_ratio") or 0.0
    dd_norm: float = doc.get("conviction_dd_norm") or 0.0
    thesis_score = (0.6 * r_rb) + (0.2 * r_rB) + (0.2 * dd_norm)
    comp_d = min(thesis_score, 1.0) * d_max

    # --- Component E: deferred ---
    comp_e = 0.0

    acs_raw = comp_a + comp_b + comp_c + comp_d + comp_e

    # --- Adjustments ---
    multiplier = 1.0
    flags: list[str] = []

    if gini > 0.65:
        multiplier *= 0.6
        flags.append("gini_high")

    acceleration: float = doc.get("acceleration_7d") or 0.0
    if acceleration < 0:
        multiplier *= 0.8
        flags.append("decelerating")

    if stage > 3:
        multiplier *= 0.5
        flags.append("late_stage")

    acs = min(100.0, max(0.0, acs_raw * multiplier))

    # --- CI bands (heuristic ±15%) ---
    acs_ci_lower = max(0.0, acs * 0.85)
    acs_ci_upper = min(100.0, acs * 1.15)

    # --- Time decay ---
    computed_at_str: str = doc.get("computed_at") or doc.get("acs_scored_at") or ""
    days_stale = _days_since(computed_at_str)
    decay_acs = acs * math.exp(-_DECAY_RATE * days_stale) if days_stale > 0 else acs

    # --- Dominant signal ---
    dominant_signal = _dominant_signal(doc)

    return AcsResult(
        ticker=ticker,
        acs=round(acs, 4),
        acs_ci_lower=round(acs_ci_lower, 4),
        acs_ci_upper=round(acs_ci_upper, 4),
        decay_acs=round(decay_acs, 4),
        components={
            "A": round(comp_a, 4),
            "B": round(comp_b, 4),
            "C": round(comp_c, 4),
            "D": round(comp_d, 4),
            "E": 0.0,
        },
        dominant_signal=dominant_signal,
        flags=flags,
    )


def _days_since(iso_str: str) -> float:
    """Return fractional days between iso_str and now. 0.0 if unparseable."""
    if not iso_str:
        return 0.0
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        delta = datetime.now(tz=timezone.utc) - dt
        return max(0.0, delta.total_seconds() / 86400)
    except ValueError:
        return 0.0


def _dominant_signal(doc: dict) -> str:
    """Return the conviction state label with the highest ratio, or 'unknown'."""
    candidates = {
        "researched_bull": doc.get("conviction_researched_bull_ratio") or 0.0,
        "researched_bear": doc.get("conviction_researched_bear_ratio") or 0.0,
        "emotional_bull":  doc.get("conviction_emotional_bull_ratio") or 0.0,
    }
    # Also consider raw sentiment if conviction hasn't run yet.
    if all(v == 0.0 for v in candidates.values()):
        bullish: float = doc.get("bullish_ratio") or 0.0
        bearish: float = doc.get("bearish_ratio") or 0.0
        if bullish > 0 or bearish > 0:
            return "bullish" if bullish >= bearish else "bearish"
        return "unknown"
    return max(candidates, key=lambda k: candidates[k])
