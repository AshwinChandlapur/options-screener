"""Backwards-compatible shim for ``services.etv_service``.

The implementation now lives in :mod:`services.etv`. This module preserves
the old import path so existing callers (``from services.etv_service
import get_etv``) keep working while the staged pipeline is built out.
"""
from __future__ import annotations

from .etv import EtvGrounding, Horizon, RiskTolerance, fetch_grounding, get_etv

__all__ = [
    "EtvGrounding",
    "Horizon",
    "RiskTolerance",
    "fetch_grounding",
    "get_etv",
]
