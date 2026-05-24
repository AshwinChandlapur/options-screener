"""S0 — narrative scaffold.

Cheap LLM call that produces the report fields the staged numeric pipeline
(S1..S4) does NOT own: company summary plus the alternative-archetype /
supporting-model / excluded-model metadata.  Replaces the full monolithic
call we used to make inside the staged path purely to seed these fields.

No numbers, no valuation math, no numeric guard.
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict

from ..grounding import EtvGrounding
from ..llm import call_json
from ..prompts import S0_SYSTEM
from ..schemas import S0_SCAFFOLD_SCHEMA
from ._base import StageResult


def _build_user(g: EtvGrounding) -> str:
    return json.dumps({"grounding": asdict(g)}, default=str)


def run(g: EtvGrounding) -> StageResult:
    """Run the S0 scaffold stage."""
    t0 = time.perf_counter()
    output = call_json(
        system=S0_SYSTEM,
        user=_build_user(g),
        schema=S0_SCAFFOLD_SCHEMA,
    )
    latency_ms = int((time.perf_counter() - t0) * 1000)
    return StageResult(
        stage="S0_scaffold",
        output=output,
        guard=None,
        latency_ms=latency_ms,
    )


__all__ = ["run"]
