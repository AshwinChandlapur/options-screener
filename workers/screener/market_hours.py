"""US equity market hours helper (Eastern Time).

Used by the screener worker to choose the staleness threshold.
"""
from __future__ import annotations

from datetime import datetime, time

import pytz

_ET = pytz.timezone("America/New_York")
_MARKET_OPEN = time(9, 30)
_MARKET_CLOSE = time(16, 0)


def is_market_open(now_utc: datetime) -> bool:
    """Return True if *now_utc* falls within US market hours (Mon–Fri 09:30–16:00 ET)."""
    et = now_utc.astimezone(_ET)
    if et.weekday() >= 5:  # Saturday=5, Sunday=6
        return False
    return _MARKET_OPEN <= et.time() < _MARKET_CLOSE
