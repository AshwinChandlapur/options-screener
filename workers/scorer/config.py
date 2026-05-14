"""Configuration for the ACS scorer worker (Phase 6).

Env contract (set by Container Apps Job):
    KEYVAULT_URI        e.g. https://kv-narrative-tinkerhub.vault.azure.net/
    COSMOS_ENDPOINT     e.g. https://cosmos-nr-tinkerhub.documents.azure.com:443/
    COSMOS_DB           narrative  (default)
    LOG_LEVEL           INFO / DEBUG  (default INFO)
    TICKERS_PER_RUN     max tickers scored per execution (default 500)
"""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class ScorerConfig:
    keyvault_uri: str
    cosmos_endpoint: str
    cosmos_db: str = "narrative"
    log_level: str = "INFO"
    tickers_per_run: int = 500


def load_from_env() -> ScorerConfig:
    return ScorerConfig(
        keyvault_uri=_required("KEYVAULT_URI"),
        cosmos_endpoint=_required("COSMOS_ENDPOINT"),
        cosmos_db=os.getenv("COSMOS_DB", "narrative"),
        log_level=os.getenv("LOG_LEVEL", "INFO"),
        tickers_per_run=int(os.getenv("TICKERS_PER_RUN", "500")),
    )


def _required(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Required env var {name!r} is unset")
    return value
