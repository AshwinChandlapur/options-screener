"""Shared dataclasses + helpers for ETV stages."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from ..numeric_guard import GuardReport


@dataclass
class StageResult:
    """Output of a single LLM stage."""
    stage: str                    # e.g. "S1_audit"
    output: dict                  # parsed JSON payload
    guard: GuardReport | None     # numeric_guard result (None if not applicable)
    latency_ms: int               # wall time for the LLM call
    retries: int = 0              # number of critic-triggered retries
    extra: dict[str, Any] = field(default_factory=dict)

    def to_log(self) -> dict:
        """Compact record for ``pipeline_log``."""
        rec: dict[str, Any] = {
            "stage": self.stage,
            "latency_ms": self.latency_ms,
            "retries": self.retries,
        }
        if self.guard is not None:
            rec["guard"] = self.guard.to_dict()
        if self.extra:
            rec["extra"] = self.extra
        return rec


__all__ = ["StageResult", "asdict"]
