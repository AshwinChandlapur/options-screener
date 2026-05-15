"""
GET  /api/screener/swing/scan   — universe scan, returns ranked SwingResults
POST /api/screener/swing        — custom symbol list (≤ 20)
"""
from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, field_validator

from services.scan_cache import regime_cache, swing_scan_cache
from services.scoring.swing import SWING_SCORER_VERSION
from services.swing.regime import RegimeState, compute_regime
from services.swing_insight_service import get_batch_commentary
from services.swing_service import process_symbol, run_scan
from services.universe import UNIVERSES, get_universe

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/screener", tags=["swing"])

_MAX_SYMBOLS = 20


class SwingRequest(BaseModel):
    symbols: list[str]

    @field_validator("symbols")
    @classmethod
    def validate_symbols(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("symbols list must not be empty")
        if len(v) > _MAX_SYMBOLS:
            raise ValueError(f"Maximum {_MAX_SYMBOLS} symbols per request")
        cleaned = [s.strip().upper() for s in v if s.strip()]
        if not cleaned:
            raise ValueError("symbols list contains no valid entries")
        for sym in cleaned:
            if len(sym) > 10 or not sym.isalnum():
                raise ValueError(f"Invalid symbol: '{sym}'")
        return cleaned


class SwingResultOut(BaseModel):
    symbol: str
    price: float
    setup_type: str
    setup_score: float
    swing_score: float
    confidence: str
    entry: float
    stop: float
    target: float
    risk_per_share: float
    reward_per_share: float
    rr: float
    hold_min_days: int
    hold_max_days: int
    trigger_kind: str = ""
    extended: bool = False
    drivers: list[str]
    earnings_date: str | None = None
    earnings_warning: bool = False
    rsi: float | None = None
    atr14: float | None = None
    adx: float | None = None
    rs_vs_spy: float | None = None
    ema_alignment_score: int | None = None
    ad_line_slope_pct: float | None = None
    institutional_ownership_pct: float | None = None
    bb_squeeze_pct: float | None = None
    consolidation_days: int | None = None
    consolidation_range_pct: float | None = None
    volume_surge_ratio: float | None = None
    higher_lows: int | None = None
    macd_inflection: bool = False
    rsi_divergence: bool = False
    fib_618_hold: bool = False
    structure_reclaimed: bool = False
    setup_scores: dict[str, float] = {}
    breakdown: dict[str, float] = {}
    multipliers: dict[str, float] = {}
    raw_score: float = 0.0
    days_to_earnings: int | None = None
    forced_short_hold: bool = False
    rr_gate: float = 0.0
    regime_label: str = ""
    narrative: str | None = None
    risk_note: str | None = None


class SwingResponse(BaseModel):
    results: list[SwingResultOut]
    scoring_version: str = SWING_SCORER_VERSION
    regime: RegimeOut | None = None


class RegimeOut(BaseModel):
    index_trend: str
    vol_regime: str
    breadth_pct: float
    risk_appetite: float
    risk_on_score: float
    regime_label: str
    rr_gate: float
    multiplier: float
    disable_setups: list[str]
    drivers: list[str]
    degraded: bool
    spy_close: float
    spy_ema21: float
    spy_ema50: float
    vix: float
    vix_percentile: float


def _regime_to_out(r: RegimeState) -> RegimeOut:
    return RegimeOut(
        index_trend=r.index_trend,
        vol_regime=r.vol_regime,
        breadth_pct=r.breadth_pct,
        risk_appetite=r.risk_appetite,
        risk_on_score=r.risk_on_score,
        regime_label=r.regime_label,
        rr_gate=r.rr_gate,
        multiplier=r.multiplier,
        disable_setups=r.disable_setups,
        drivers=r.drivers,
        degraded=r.degraded,
        spy_close=r.spy_close,
        spy_ema21=r.spy_ema21,
        spy_ema50=r.spy_ema50,
        vix=r.vix,
        vix_percentile=r.vix_percentile,
    )


def _get_cached_regime(spy_df=None, universe_ohlc=None) -> RegimeState:
    """Memoize the regime calc per scan (30-min TTL)."""
    cached = regime_cache.get("regime:global")
    if cached is not None:
        return cached
    state = compute_regime(spy_df=spy_df, universe_ohlc=universe_ohlc)
    regime_cache.set("regime:global", state)
    return state


@router.get("/swing/regime", response_model=RegimeOut)
async def get_swing_regime() -> RegimeOut:
    """Return the current global market regime used by the swing screener."""
    from services.data_service import get_ohlc as _get_ohlc

    def _build() -> RegimeState:
        try:
            spy_df = _get_ohlc("SPY", period="1y")
        except Exception as exc:  # noqa: BLE001
            logger.warning("regime endpoint: SPY fetch failed: %s", exc)
            spy_df = None
        # No universe OHLC available for the standalone endpoint — breadth degrades to neutral.
        return _get_cached_regime(spy_df=spy_df, universe_ohlc=None)

    state = await asyncio.to_thread(_build)
    return _regime_to_out(state)


@router.get("/swing/scan", response_model=SwingResponse)
async def run_swing_scan(
    top_n: int = Query(default=20, ge=1, le=50),
    universe: str = Query(
        default="swing_eligible",
        description=f"Universe key: one of {sorted(UNIVERSES)}",
    ),
) -> SwingResponse:
    """Scan a universe, return top_n by swing_score."""
    universe_key, symbols = get_universe(universe)
    cache_key = f"swing:{universe_key}:{top_n}"
    cached = swing_scan_cache.get(cache_key)
    if cached is not None:
        logger.info("Swing scan cache hit: %s", cache_key)
        return cached

    logger.info("Starting swing scan universe=%s (%d stocks)", universe_key, len(symbols))
    raw = await asyncio.to_thread(run_scan, symbols)
    results = [SwingResultOut(**r) for r in raw[:top_n]]

    # LLM commentary for top 3 (batched single call). Best-effort.
    if results:
        top3_raw = raw[: min(3, len(results))]
        commentary = await asyncio.to_thread(get_batch_commentary, top3_raw)
        by_sym = {c.symbol: c for c in commentary}
        for r in results[: min(3, len(results))]:
            c = by_sym.get(r.symbol)
            if c is not None:
                r.narrative = c.narrative
                r.risk_note = c.risk_note

    logger.info("Swing scan complete: %d qualified of %d", len(raw), len(symbols))

    regime_state = regime_cache.get("regime:global")
    response = SwingResponse(
        results=results,
        regime=_regime_to_out(regime_state) if regime_state is not None else None,
    )
    swing_scan_cache.set(cache_key, response)
    return response


@router.post("/swing", response_model=SwingResponse)
async def run_swing(req: SwingRequest) -> SwingResponse:
    """Run swing pipeline on a custom symbol list. Excluded symbols dropped."""
    if not req.symbols:
        raise HTTPException(status_code=422, detail="symbols list is empty")

    raw = await asyncio.to_thread(run_scan, req.symbols, 4)
    results = [SwingResultOut(**r) for r in raw]
    regime_state = regime_cache.get("regime:global")
    return SwingResponse(
        results=results,
        regime=_regime_to_out(regime_state) if regime_state is not None else None,
    )
