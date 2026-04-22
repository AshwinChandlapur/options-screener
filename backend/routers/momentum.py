"""
POST /api/screener/momentum
Pre-breakout momentum screener: RVOL, RSI, ROC, SMA structure, MACD, 52w high proximity.
"""
from __future__ import annotations

import asyncio
import logging
import math
from typing import List, Optional

from fastapi import APIRouter, Query
from pydantic import BaseModel, field_validator

from services.momentum_service import MomentumError, MomentumResult, process_momentum_symbol
from services.universe import MOMENTUM_UNIVERSE, UNIVERSE_SIZE

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/screener", tags=["momentum"])

_MAX_SYMBOLS = 20
_CONCURRENCY = 5


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------

class MomentumRequest(BaseModel):
    symbols: List[str]

    @field_validator("symbols")
    @classmethod
    def validate_symbols(cls, v: List[str]) -> List[str]:
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


def _f(v: float) -> Optional[float]:
    """Convert NaN to None so Pydantic serialises as JSON null."""
    if math.isnan(v):
        return None
    return v


class MomentumResultOut(BaseModel):
    symbol: str
    price: float
    price_change_1d_pct: Optional[float]
    rvol: Optional[float]
    rsi: Optional[float]
    roc_21: Optional[float]
    sma_ratio: Optional[float]
    sma20_slope_pct: Optional[float]
    price_vs_sma20_pct: Optional[float]
    dist_from_52w_high_pct: Optional[float]
    dist_from_sma200_pct: Optional[float]
    macd_histogram: Optional[float]
    high_52w: float
    low_52w: float
    short_ratio: Optional[float]
    momentum_score: float


class MomentumErrorOut(BaseModel):
    symbol: str
    reason: str


class MomentumResponse(BaseModel):
    results: List[MomentumResultOut]
    errors: List[MomentumErrorOut]


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------

@router.post("/momentum", response_model=MomentumResponse)
async def run_momentum_screener(request: MomentumRequest) -> MomentumResponse:
    """
    Runs the Momentum screener for the provided symbols.
    No DTE/options required — pure price/volume/technical analysis.
    """
    logger.info("Starting Momentum screener for %d symbols", len(request.symbols))

    sem = asyncio.Semaphore(_CONCURRENCY)

    async def process_one(symbol: str):
        async with sem:
            return await asyncio.to_thread(process_momentum_symbol, symbol)

    pairs = await asyncio.gather(*[process_one(s) for s in request.symbols])

    results: list[MomentumResultOut] = []
    errors: list[MomentumErrorOut] = []
    for result, error in pairs:
        if result is not None:
            results.append(_to_out(result))
        if error is not None:
            errors.append(MomentumErrorOut(symbol=error.symbol, reason=error.reason))

    logger.info("Momentum screener complete: %d results, %d errors", len(results), len(errors))
    return MomentumResponse(results=results, errors=errors)


@router.get("/momentum/scan", response_model=MomentumResponse)
async def run_momentum_scan(
    top_n: int = Query(default=20, ge=1, le=50),
) -> MomentumResponse:
    """
    Scans the full curated universe (~75 stocks) and returns the
    top_n results ranked by composite momentum score descending.
    """
    logger.info("Starting Momentum universe scan (%d stocks), top_n=%d", UNIVERSE_SIZE, top_n)

    sem = asyncio.Semaphore(10)  # higher concurrency — no options chain needed

    async def process_one(symbol: str):
        async with sem:
            return await asyncio.to_thread(process_momentum_symbol, symbol)

    pairs = await asyncio.gather(*[process_one(s) for s in MOMENTUM_UNIVERSE])

    results: list[MomentumResultOut] = []
    errors: list[MomentumErrorOut] = []
    for result, error in pairs:
        if result is not None:
            results.append(_to_out(result))
        if error is not None:
            errors.append(MomentumErrorOut(symbol=error.symbol, reason=error.reason))

    results.sort(key=lambda r: r.momentum_score, reverse=True)
    top_results = results[:top_n]

    logger.info(
        "Momentum scan complete: returning top %d of %d (errors=%d)",
        len(top_results), UNIVERSE_SIZE, len(errors),
    )
    return MomentumResponse(results=top_results, errors=errors)


def _to_out(r: MomentumResult) -> MomentumResultOut:
    return MomentumResultOut(
        symbol=r.symbol,
        price=r.price,
        price_change_1d_pct=_f(r.price_change_1d_pct),
        rvol=_f(r.rvol),
        rsi=_f(r.rsi),
        roc_21=_f(r.roc_21),
        sma_ratio=_f(r.sma_ratio),
        sma20_slope_pct=_f(r.sma20_slope_pct),
        price_vs_sma20_pct=_f(r.price_vs_sma20_pct),
        dist_from_52w_high_pct=_f(r.dist_from_52w_high_pct),
        dist_from_sma200_pct=_f(r.dist_from_sma200_pct),
        macd_histogram=_f(r.macd_histogram),
        high_52w=r.high_52w,
        low_52w=r.low_52w,
        short_ratio=r.short_ratio,
        momentum_score=r.momentum_score,
    )
