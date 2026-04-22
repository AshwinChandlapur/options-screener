"""
POST /api/screener/ditm
Deep In The Money Long Call screener.
"""
from __future__ import annotations

import asyncio
import logging
from typing import List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, field_validator

from services.data_service import get_risk_free_rate
from services.ditm_service import DitmError, DitmResult, process_ditm_symbol

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/screener", tags=["ditm"])

_MAX_SYMBOLS = 20
_CONCURRENCY = 5


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------

class DitmRequest(BaseModel):
    symbols: List[str]
    minDTE: int = 180
    maxDTE: int = 365
    minDelta: float = 0.80

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

    @field_validator("minDTE", "maxDTE")
    @classmethod
    def validate_dte(cls, v: int) -> int:
        if not (1 <= v <= 365):
            raise ValueError("DTE values must be between 1 and 365")
        return v

    @field_validator("maxDTE")
    @classmethod
    def validate_dte_range(cls, v: int, info) -> int:
        min_dte = info.data.get("minDTE")
        if min_dte is not None and v < min_dte:
            raise ValueError("maxDTE must be >= minDTE")
        return v

    @field_validator("minDelta")
    @classmethod
    def validate_min_delta(cls, v: float) -> float:
        if not (0.50 <= v <= 1.0):
            raise ValueError("minDelta must be between 0.50 and 1.0")
        return v


class DitmResultOut(BaseModel):
    symbol: str
    price: float
    sma_ratio: float
    rsi: float
    iv_rank: Optional[float]
    iv_percentile: Optional[float]
    earnings_date: Optional[str]
    earnings_within_dte: bool
    strike: float
    strike_is_fallback: bool
    expiration: str
    dte: int
    premium: float
    delta: float
    extrinsic_value: float
    extrinsic_pct: float
    moneyness_pct: float
    leverage_ratio: float
    breakeven_price: float
    breakeven_pct_above: float
    capital_at_risk: float
    vs_stock_cost_pct: float
    bid_ask_spread_pct: Optional[float]
    open_interest: Optional[int]


class DitmErrorOut(BaseModel):
    symbol: str
    reason: str


class DitmResponse(BaseModel):
    results: List[DitmResultOut]
    errors: List[DitmErrorOut]


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------

@router.post("/ditm", response_model=DitmResponse)
async def run_ditm_screener(request: DitmRequest) -> DitmResponse:
    """
    Runs the DITM Long Call screener for the provided symbols.
    Symbols that fail are returned in the errors list; others still appear in results.
    """
    if request.minDTE > request.maxDTE:
        raise HTTPException(status_code=422, detail="minDTE must be <= maxDTE")

    rf_rate = await asyncio.to_thread(get_risk_free_rate)
    logger.info(
        "Starting DITM screener for %d symbols, DTE %d–%d, minDelta=%.2f, rf=%.3f",
        len(request.symbols),
        request.minDTE,
        request.maxDTE,
        request.minDelta,
        rf_rate,
    )

    sem = asyncio.Semaphore(_CONCURRENCY)

    async def process_one(symbol: str):
        async with sem:
            return await asyncio.to_thread(
                process_ditm_symbol,
                symbol,
                request.minDTE,
                request.maxDTE,
                rf_rate,
                request.minDelta,
            )

    pairs = await asyncio.gather(*[process_one(s) for s in request.symbols])

    results: list[DitmResultOut] = []
    errors: list[DitmErrorOut] = []
    for result, error in pairs:
        if result is not None:
            results.append(_to_out(result))
        if error is not None:
            errors.append(DitmErrorOut(symbol=error.symbol, reason=error.reason))

    logger.info("DITM screener complete: %d results, %d errors", len(results), len(errors))
    return DitmResponse(results=results, errors=errors)


def _to_out(r: DitmResult) -> DitmResultOut:
    return DitmResultOut(
        symbol=r.symbol,
        price=r.price,
        sma_ratio=r.sma_ratio,
        rsi=r.rsi,
        iv_rank=r.iv_rank,
        iv_percentile=r.iv_percentile,
        earnings_date=r.earnings_date,
        earnings_within_dte=r.earnings_within_dte,
        strike=r.strike,
        strike_is_fallback=r.strike_is_fallback,
        expiration=r.expiration,
        dte=r.dte,
        premium=r.premium,
        delta=r.delta,
        extrinsic_value=r.extrinsic_value,
        extrinsic_pct=r.extrinsic_pct,
        moneyness_pct=r.moneyness_pct,
        leverage_ratio=r.leverage_ratio,
        breakeven_price=r.breakeven_price,
        breakeven_pct_above=r.breakeven_pct_above,
        capital_at_risk=r.capital_at_risk,
        vs_stock_cost_pct=r.vs_stock_cost_pct,
        bid_ask_spread_pct=r.bid_ask_spread_pct,
        open_interest=r.open_interest,
    )
