"""
Scrapes IV Percentile for a batch of symbols from barchart.com.

Flow:
  1. GET barchart.com homepage to establish session cookies (incl. XSRF-TOKEN).
  2. POST to their internal core-api quotes endpoint with all symbols in one call.
  3. Parse ivPercentile from each result.

Uses curl_cffi (already installed via yfinance) to impersonate a browser
and avoid Cloudflare/bot-detection blocks.
"""
from __future__ import annotations

import logging
from urllib.parse import unquote

logger = logging.getLogger(__name__)

_BARCHART_HOME = "https://www.barchart.com/"
_BARCHART_API = "https://www.barchart.com/proxies/core-api/v1/quotes/get"

# Fields to request — barchart may return either name depending on instrument type
_FIELDS = "symbol,ivPercentile,historicalVolatilityPercentile"

_HEADERS_BASE = {
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


def fetch_iv_percentiles(symbols: list[str]) -> dict[str, float | None]:
    """
    Returns {SYMBOL: iv_percentile_float} for each symbol.
    Value is None if barchart did not return data for that symbol.
    Never raises — all errors are logged and an empty/partial dict is returned.
    """
    result: dict[str, float | None] = {sym.upper(): None for sym in symbols}

    try:
        from curl_cffi import requests as cffi_requests  # lazy import

        session = cffi_requests.Session(impersonate="chrome110")

        # ── Step 1: Establish session & grab XSRF token ────────────────────
        home_resp = session.get(
            _BARCHART_HOME,
            headers=_HEADERS_BASE,
            timeout=10,
        )
        home_resp.raise_for_status()

        xsrf_token = unquote(session.cookies.get("XSRF-TOKEN", ""))
        if not xsrf_token:
            logger.warning("Barchart: could not retrieve XSRF-TOKEN — skipping IV fetch")
            return result

        # ── Step 2: Batch fetch IV percentile for all symbols ──────────────
        api_resp = session.get(
            _BARCHART_API,
            params={
                "symbols": ",".join(sym.upper() for sym in symbols),
                "fields": _FIELDS,
                "raw": "1",
            },
            headers={
                **_HEADERS_BASE,
                "Accept": "application/json, text/plain, */*",
                "Referer": _BARCHART_HOME,
                "X-XSRF-TOKEN": xsrf_token,
            },
            timeout=15,
        )
        api_resp.raise_for_status()
        data = api_resp.json()

        for item in data.get("data", []):
            sym = (item.get("symbol") or "").upper()
            if sym not in result:
                continue
            raw = item.get("raw", {})
            # Try both field names barchart uses
            val = raw.get("ivPercentile")
            if val is None:
                val = raw.get("historicalVolatilityPercentile")
            if val is not None:
                try:
                    result[sym] = round(float(val), 2)
                except (TypeError, ValueError):
                    pass

        fetched = sum(1 for v in result.values() if v is not None)
        logger.info("Barchart IV: fetched %d/%d symbols", fetched, len(symbols))

    except Exception as exc:
        logger.warning("Barchart IV fetch failed: %s", exc)

    return result
