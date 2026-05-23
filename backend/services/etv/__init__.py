"""Expected Tradable Value (ETV) — multi-layer probabilistic valuation system.

Distinct from ``dcf_service``: DCF outputs a fair value. ETV outputs a
*trade/no-trade* decision with a confidence score, position sizing, and
horizon — grounded in explicit layers (model selection → regime →
asymmetry → sizing).

This package is the staging ground for the multi-stage pipeline (S1 audit →
S2 intrinsic → S3 overlays → S4 decision → S5 critic).  Step 1 only
extracts the existing monolithic implementation into discrete modules with
**no behavior change**; later steps split the single LLM call into stages.

Public API (preserved):
    - :func:`get_etv` — fetch / compute / cache a full report.
    - :func:`fetch_grounding` — pure yfinance grounding (no LLM).
    - :data:`Horizon`, :data:`RiskTolerance` — Literal type aliases.
    - :class:`EtvGrounding` — dataclass for grounding payload.
"""
from .grounding import EtvGrounding, fetch_grounding
from .orchestrator import Horizon, RiskTolerance, get_etv

__all__ = [
    "EtvGrounding",
    "Horizon",
    "RiskTolerance",
    "fetch_grounding",
    "get_etv",
]
