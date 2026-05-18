"""Strategy dispatch for the screener precomputation worker (ADR-0024).

Imports backend services directly (PYTHONPATH=/app/backend points to the backend
package root, so `from services.X import Y` works exactly as it does in the
FastAPI app). Imports are deferred to inside run_strategy() so that the module
can be imported in test environments where the backend is not on sys.path.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)

_CONCURRENCY = 10  # matches the scan semaphore in routers

# DTE windows used for precomputation — must cover all UI-selectable ranges.
_CSP_MIN_DTE = 14
_CSP_MAX_DTE = 90
_CC_MIN_DTE = 14
_CC_MAX_DTE = 90
_DITM_MIN_DTE = 90
_DITM_MAX_DTE = 730


def run_strategy(
    strategy: str,
) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    """Run the full universe scan for *strategy*.

    Returns:
        results: ticker → serialised result dict (may be empty list for no-strikes)
        errors:  ticker → error reason string
    """
    return asyncio.run(_run_async(strategy))


async def _run_async(
    strategy: str,
) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    # Defer backend imports so this module is importable in test environments
    # where the backend package is not on sys.path.
    from services.data_service import get_risk_free_rate  # noqa: PLC0415
    from services.universe import MOMENTUM_UNIVERSE  # noqa: PLC0415

    tickers = list(MOMENTUM_UNIVERSE)
    rf_rate = await asyncio.to_thread(get_risk_free_rate)
    macro_ctx: dict[str, Any] = {}
    if strategy == "ditm":
        from services.ditm_service import get_macro_context  # noqa: PLC0415
        macro_ctx = await asyncio.to_thread(get_macro_context)
        logger.info(
            "DITM macro context: macro_pass=%s vix_level=%s",
            macro_ctx.get("macro_pass"),
            macro_ctx.get("vix_level"),
        )

    sem = asyncio.Semaphore(_CONCURRENCY)

    async def _one(ticker: str) -> tuple[str, list | None, str | None]:
        async with sem:
            return await asyncio.to_thread(
                _process_one, strategy, ticker, rf_rate, macro_ctx
            )

    pairs = await asyncio.gather(*[_one(t) for t in tickers], return_exceptions=True)

    results: dict[str, dict[str, Any]] = {}
    errors: dict[str, str] = {}

    for item in pairs:
        if isinstance(item, BaseException):
            logger.error("Unhandled exception in gather: %s", item)
            continue
        ticker, result_list, error_reason = item
        if error_reason:
            errors[ticker] = error_reason
        elif result_list:
            results[ticker] = {
                "rows": [_result_to_dict(r) for r in result_list],
                "macro": macro_ctx if strategy == "ditm" else {},
            }

    logger.info(
        "Strategy=%s scan complete: %d results, %d errors",
        strategy, len(results), len(errors),
    )
    return results, errors


def _process_one(
    strategy: str,
    ticker: str,
    rf_rate: float,
    macro_ctx: dict[str, Any],
) -> tuple[str, list | None, str | None]:
    """Call the appropriate process_symbol and return (ticker, results, error)."""
    try:
        if strategy == "csp":
            from services.csp_service import process_symbol as csp_process  # noqa: PLC0415
            result_list, error = csp_process(
                ticker,
                min_dte=_CSP_MIN_DTE,
                max_dte=_CSP_MAX_DTE,
                rf_rate=rf_rate,
                max_capital=None,
            )
        elif strategy == "cc":
            from services.cc_service import process_cc_symbol as cc_process  # noqa: PLC0415
            result_list, error = cc_process(
                ticker,
                _CC_MIN_DTE,
                _CC_MAX_DTE,
                rf_rate,
            )
        elif strategy == "ditm":
            from services.ditm_service import process_symbol as ditm_process  # noqa: PLC0415
            result_list, error = ditm_process(
                ticker,
                _DITM_MIN_DTE,
                _DITM_MAX_DTE,
                rf_rate,
                macro_ctx,
            )
        else:
            return ticker, None, f"Unknown strategy: {strategy}"

        if error is not None:
            return ticker, None, error.reason
        return ticker, result_list, None
    except Exception as exc:
        logger.exception("process_symbol failed for %s (%s)", ticker, strategy)
        return ticker, None, str(exc)


def _result_to_dict(result: Any) -> dict[str, Any]:
    """Convert a result dataclass to a JSON-serialisable dict."""
    import dataclasses
    return dataclasses.asdict(result)
