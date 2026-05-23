"""Per-stage LLM callers for the staged ETV pipeline.

Each ``run(...)`` returns a ``StageResult`` containing the LLM payload, the
:class:`numeric_guard.GuardReport`, and a small telemetry record suitable
for the orchestrator's ``pipeline_log``.
"""
from __future__ import annotations

from .s1_audit import run as run_s1
from .s2_intrinsic import run as run_s2
from .s3_overlays import run as run_s3
from .s4_decision import run as run_s4
from .s5_critic import format_feedback as critic_feedback
from .s5_critic import run as run_s5

__all__ = ["run_s1", "run_s2", "run_s3", "run_s4", "run_s5",
           "critic_feedback"]
