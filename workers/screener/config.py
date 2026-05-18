"""Configuration for the screener precomputation worker (ADR-0024).

Env contract (set by Container Apps Job):
    COSMOS_ENDPOINT              e.g. https://cosmos-nr-tinkerhub.documents.azure.com:443/
    COSMOS_DB                    narrative  (default)
    STRATEGY                     csp | cc | ditm
    LOG_LEVEL                    INFO / DEBUG  (default INFO)
    MIN_REFRESH_SECONDS_MARKET   default 900  (15 min)
    MIN_REFRESH_SECONDS_OFF      default 14400 (4 h)
"""
from __future__ import annotations

import os
from dataclasses import dataclass

_VALID_STRATEGIES = frozenset({"csp", "cc", "ditm", "swing"})


@dataclass(frozen=True)
class ScreenerConfig:
    cosmos_endpoint: str
    strategy: str
    cosmos_db: str = "narrative"
    log_level: str = "INFO"
    min_refresh_seconds_market: int = 900
    min_refresh_seconds_off: int = 14400


def load_from_env() -> ScreenerConfig:
    strategy = _required("STRATEGY").lower()
    if strategy not in _VALID_STRATEGIES:
        raise RuntimeError(
            f"STRATEGY must be one of {sorted(_VALID_STRATEGIES)}, got {strategy!r}"
        )
    return ScreenerConfig(
        cosmos_endpoint=_required("COSMOS_ENDPOINT"),
        strategy=strategy,
        cosmos_db=os.getenv("COSMOS_DB", "narrative"),
        log_level=os.getenv("LOG_LEVEL", "INFO"),
        min_refresh_seconds_market=int(
            os.getenv("MIN_REFRESH_SECONDS_MARKET", "900")
        ),
        min_refresh_seconds_off=int(
            os.getenv("MIN_REFRESH_SECONDS_OFF", "14400")
        ),
    )


def _required(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Required env var {name!r} is unset")
    return value
