"""Expected Tradable Value (ETV) router."""
import logging
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request

from limiter import limiter
from services.etv_service import get_etv

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/etv", tags=["etv"])


@router.get("")
@limiter.limit("5/minute")
def fetch(
    request: Request,
    ticker: str = Query(..., min_length=1, max_length=10, pattern=r"^[A-Za-z\.\-]+$"),
    horizon: Literal["short", "medium", "long"] = Query("medium"),
    risk_tolerance: Literal["conservative", "moderate", "aggressive"] = Query("moderate"),
    refresh: bool = Query(False, description="Skip cache and re-run analysis"),
) -> dict:
    try:
        return get_etv(
            ticker,
            horizon=horizon,
            risk_tolerance=risk_tolerance,
            refresh=refresh,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        logger.exception("ETV failed for %s", ticker)
        raise HTTPException(status_code=500, detail=str(e))
