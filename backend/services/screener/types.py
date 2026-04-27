"""
Protocol types for the unified screener.

This module defines the type surface that `ScreenerConfig` and the (future)
`runner.run(...)` will consume. **No logic.** Concrete screeners (CSP / CC /
DITM) supply the callables; the runner stays agnostic.

Design notes (see plan-screener-refactor.md, Phase 2):
- `Indicators` and `StrikeContext` are **union bundles** — they contain every
  field any of the three live scorers reads. Each scorer ignores the fields
  it doesn't use. This is the price of one runner over three; the alternative
  was a per-screener bundle, which would push branching back into the runner.
- All three concrete env/strike scorers diverge in arity (CSP/CC vs DITM in
  particular). The `EnvScorer` / `StrikeScorer` callable types take the union
  bundle so the runner has a single, stable call site.
- Fields default to `None` / sentinel where a screener doesn't populate them.
  Scorers that *require* a field must validate it themselves and raise — the
  runner does not police bundle completeness.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Literal, Optional

# --- Literals --------------------------------------------------------------

Direction = Literal["short_put", "short_call", "long_call"]
"""High-level screener orientation. Maps 1:1 to (csp, cc, ditm)."""


# --- Indicator + strike context bundles ------------------------------------

@dataclass(frozen=True)
class Indicators:
    """
    Per-symbol environment inputs.

    Union of every indicator field consumed by the three env scorers
    (`compute_env_score` for CSP/CC and the inline DITM env scorer). Optional
    fields default to None so a CSP config can leave `weekly_rsi` unset and a
    DITM config can leave `iv_rank`/`iv_hv_ratio` unset.
    """

    # Common (all three screeners)
    price: float
    sma50: float
    sma200: float
    price_above_sma50: bool
    sma50_above_sma200: bool
    dist_from_52w_high_pct: float
    chain_median_oi: float
    earnings_within_dte: bool
    days_to_earnings: Optional[int]
    dte: int

    # CSP / CC only
    iv_hv_ratio: Optional[float] = None
    iv_stale: bool = False
    rsi: Optional[float] = None            # RSI(14) daily

    # Shared by all three (CSP/CC env scorer historically called the param
    # `iv_rank`, but the value is HV-derived; we standardise on `hv_rank` at
    # this layer and Phase 3 will adapt scorer call sites).
    hv_rank: Optional[float] = None

    # DITM only
    weekly_rsi: Optional[float] = None
    ret_200d_frac: Optional[float] = None  # 200-day median-anchored return as fraction
    trend_pts: Optional[float] = None      # legacy trend strength (used by DITM hard gate)
    macro_hold: bool = False               # macro-context flag (DITM only)


@dataclass(frozen=True)
class StrikeContext:
    """
    Per-strike inputs to the strike scorer.

    Union of every field the three strike scorers consume. Optional fields
    default to None — a CSP scorer leaves DITM-only fields unset and vice
    versa.
    """

    # Common
    delta: float
    strike: float
    current_price: float
    bid_ask_spread_pct: Optional[float]
    open_interest: int
    volume: int
    market_open: bool
    iv_used: float
    dte: int

    # CSP / CC pricing
    credit: Optional[float] = None              # premium (mid)

    # CSP supports / CC resistances (3 levels each, screener picks one)
    vol_support_1: Optional[float] = None
    vol_support_2: Optional[float] = None
    vol_support_3: Optional[float] = None
    vol_resistance_1: Optional[float] = None
    vol_resistance_2: Optional[float] = None
    vol_resistance_3: Optional[float] = None

    # DITM-only (long-call mechanics)
    mid: Optional[float] = None
    extrinsic_pct_of_strike_frac: Optional[float] = None
    theta_annualized_pct: Optional[float] = None
    iv_percentile: Optional[float] = None


# --- Hard gate result ------------------------------------------------------

@dataclass(frozen=True)
class GateResult:
    """Outcome of a hard gate. `passed=False` short-circuits the env score
    to 0 with the supplied `reason` recorded in the env detail string."""

    passed: bool
    reason: str = ""


# --- Generic result base classes -------------------------------------------

@dataclass
class BaseStrikeResult:
    """Minimal fields every strike result has. Concrete dataclasses
    (`CspStrikeResult`, `CcStrikeResult`, `DitmStrikeResult`) inherit and add
    screener-specific fields; the runner only touches these common fields.

    Note: `is_best` is the only field the runner mutates after construction
    (post-sort). The class is intentionally NOT frozen for that reason."""

    strike: float
    delta: float
    env_score: float
    strike_score: float
    final_score: float
    env_detail: str = ""
    strike_detail: str = ""
    is_best: bool = False


@dataclass
class BaseScreenerResult:
    """Minimal fields every per-symbol result has."""

    symbol: str
    price: float
    dte: int
    expiration: str
    best_score: float = 0.0


# --- Callable type aliases -------------------------------------------------

# (spot, strike, T_years, sigma, rate) -> delta in [-1, 1]
DeltaFn = Callable[[float, float, float, float, float], float]

# (symbol, dte_min, dte_max) -> list of expiration chains; the actual return
# shape is `list[dict]` matching options_service. Kept loose to avoid a
# premature contract here.
ChainFetcher = Callable[[str, int, int], list[dict]]

# (current_price, strike) -> True if strike passes screener-specific filter
# (e.g. OTM puts: strike < price * 1.02; ITM calls: strike < price).
StrikeFilter = Callable[[float, float], bool]

# (Indicators) -> (env_score 0-100, detail string). Receives the union
# bundle; concrete scorer extracts only the fields it needs.
EnvScorer = Callable[[Indicators], tuple[float, str]]

# (StrikeContext) -> (strike_score 0-100, detail string, raw_metrics dict).
# `raw_metrics` carries dist_pct / em_buffer_pct / lq_count / roc_annualized
# etc. so the result_factory can stash them on the concrete result.
StrikeScorer = Callable[[StrikeContext], tuple[float, str, dict[str, Any]]]

# (StrikeContext) -> capital denominator in dollars per contract.
# CSP: strike * 100 ; CC: current_price * 100 ; DITM: mid * 100.
CapitalBasisFn = Callable[[StrikeContext], float]

# (Indicators) -> GateResult. DITM uses these for trend / hv_rank / earnings
# short-circuits. CSP / CC pass `()` (no gates).
HardGate = Callable[[Indicators], GateResult]

# (symbol, raw_ohlc_df, indicators_in_progress) -> Indicators (mutated copy).
# DITM uses these for macro_context, weekly_rsi, ret_200d enrichment.
PreProcessor = Callable[[str, Any, Indicators], Indicators]

# (BaseStrikeResult) -> sort key used to pick the best strike.
# CSP / CC: roc_annualized (descending). DITM: -|delta - ideal_delta|.
TieBreakKey = Callable[[BaseStrikeResult], float]

# Builds the concrete strike-result dataclass from runner-side bundle.
ResultFactory = Callable[..., Any]


__all__ = [
    "BaseScreenerResult",
    "BaseStrikeResult",
    "CapitalBasisFn",
    "ChainFetcher",
    "DeltaFn",
    "Direction",
    "EnvScorer",
    "GateResult",
    "HardGate",
    "Indicators",
    "PreProcessor",
    "ResultFactory",
    "StrikeContext",
    "StrikeFilter",
    "StrikeScorer",
    "TieBreakKey",
]
