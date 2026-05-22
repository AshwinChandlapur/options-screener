"""IC snapshot writer — Phase 2 validation infrastructure.

One snapshot document is written to the ``ic_snapshots`` Cosmos container each
time the scorer runs for a given (ticker, today) pair.  After ``forward_days``
have elapsed the scorer fills in ``forward_return_pct`` and marks the snapshot
``is_complete = True``.

Container schema
----------------
    id              "{ticker}_{snapshot_date}"  (unique per ticker per day)
    ticker          partition key
    snapshot_date   "YYYY-MM-DD" — date the ACS was recorded
    acs             float — ACS on snapshot_date
    forward_days    int   — prediction horizon (default 30)
    px_at_snapshot  float | None — closing price on snapshot_date
    forward_return_pct float | None — (px_at_T+30 / px_at_T - 1) * 100
    is_complete     bool  — True once forward return has been filled

No backfill of the ``forward_return_pct`` is done here; that happens in the
scorer's main loop via ``fill_pending_returns()``.

Design contract
---------------
This module is FROZEN for 90 days from first deployment.  Do not change the
snapshot schema, the IC formula, or the forward_days parameter until the live
IC test completes.  The whole point is to measure what the system produces
without touching it.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timezone

logger = logging.getLogger(__name__)

# Prediction horizon.  Frozen — do not change during live IC window.
FORWARD_DAYS: int = 30
# Minimum cohort size required to report a weekly IC value.
MIN_COHORT_FOR_IC: int = 10


def make_snapshot_id(ticker: str, snapshot_date: str) -> str:
    return f"{ticker}_{snapshot_date}"


def build_snapshot_doc(
    ticker: str,
    snapshot_date: str,
    acs: float,
    px_at_snapshot: float | None,
    factors: dict | None = None,
) -> dict:
    """Build a new (incomplete) IC snapshot document.

    ``factors`` is an optional dict of additional predictors to store alongside
    the primary ACS value.  All keys are stored at top level so Cosmos queries
    can filter on any of them.  Supported keys:

        acs_raw, acs_multiplier, decay_acs, acs_ci_lower, acs_ci_upper
        comp_a, comp_b, comp_c, comp_d
        dwd_14d, unique_authors_14d, mentions_14d, gini_14d
        lifecycle_stage, stage_confidence, s_br, s_Br
        market_cap, dominant_signal, flags
        stage_streak_days, acs_slope_14d
    """
    doc: dict = {
        "id": make_snapshot_id(ticker, snapshot_date),
        "ticker": ticker,
        "snapshot_date": snapshot_date,
        "acs": round(acs, 4),
        "forward_days": FORWARD_DAYS,
        "px_at_snapshot": px_at_snapshot,
        "forward_return_pct": None,
        "is_complete": False,
        "created_at": datetime.now(tz=timezone.utc).isoformat(),
    }
    if factors:
        for k, v in factors.items():
            if v is None:
                continue  # omit None — Cosmos reads missing as None
            if isinstance(v, float):
                doc[k] = round(v, 6)
            else:
                doc[k] = v
    return doc


def should_fill_return(doc: dict, today: date) -> bool:
    """Return True if this incomplete snapshot is old enough to have a return."""
    if doc.get("is_complete"):
        return False
    if doc.get("px_at_snapshot") is None:
        return False          # can't compute return without entry price
    try:
        snap_date = date.fromisoformat(doc["snapshot_date"])
    except (KeyError, ValueError):
        return False
    return (today - snap_date).days >= FORWARD_DAYS


def fetch_closing_price(ticker: str, on_date: date) -> float | None:
    """Fetch the adjusted closing price for ticker on or near on_date.

    Tries the exact date first, then looks back up to 5 days for weekends/
    holidays.  Returns None if yfinance is unavailable or returns no data.
    """
    try:
        import yfinance as yf  # noqa: PLC0415
        from datetime import timedelta

        end = on_date + timedelta(days=1)
        start = on_date - timedelta(days=5)
        hist = yf.download(
            ticker,
            start=start.isoformat(),
            end=end.isoformat(),
            auto_adjust=True,
            progress=False,
            actions=False,
        )
        if hist.empty:
            return None
        # Use the last available close on or before on_date.
        hist = hist[hist.index.date <= on_date]  # type: ignore[attr-defined]
        if hist.empty:
            return None
        return float(hist["Close"].iloc[-1])
    except Exception:
        logger.debug("fetch_closing_price(%s, %s) failed", ticker, on_date, exc_info=True)
        return None


def compute_forward_return(px_entry: float, px_exit: float) -> float:
    """Return (px_exit / px_entry - 1) * 100, i.e. percentage return."""
    if px_entry <= 0:
        return 0.0
    return (px_exit / px_entry - 1.0) * 100.0
