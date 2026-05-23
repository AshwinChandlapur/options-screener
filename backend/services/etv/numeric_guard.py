"""Closed-world numeric guard for ETV stage outputs.

Every numeric leaf in a stage's JSON output must be justifiable as one of:

* **grounded**   — within ±tolerance of a value present in ``EtvGrounding``,
                   optionally after scaling by {1, 1/100, 100, 1e6, 1e9} to
                   bridge fraction↔percent and unit conventions.
* **declared**   — equals (within tolerance) a value declared in
                   ``missing_inputs`` as ``ASSUMPTION:{name}={value}``.
* **derived**    — appears as the right-hand side of any ``derivation`` line
                   (``"foo = grounding.x * 1.1 = 220.5"``); the LLM has shown
                   its work and the critic stage will spot-check the algebra.
* **unjustified** — none of the above → reported for caller to action
                   (typically: route back to the offending stage with the
                   guard report appended to the prompt for one retry).

Whitelisted passthroughs (probabilities, current_price echoes, small
integers in 1-100 range used as counts/percentages/years) are exempt.

This module is *pure* and side-effect-free: callers decide what to do with
the report.
"""
from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass, field, is_dataclass
from typing import Any, Iterable, Iterator

# ----------------------------------------------------------- Constants ---

_TOLERANCE_FRAC = 0.005  # 0.5%
_TOLERANCE_ABS = 0.01    # for near-zero values

# JSON keys whose numeric values are *echoes* of inputs or schema-driven
# scalars — never treated as "model-generated numbers".
_PASSTHROUGH_KEYS: frozenset[str] = frozenset({
    "probability_pct",
    "current_price",
    "as_of",
    # Probability-weighted aggregates are validated by the deterministic
    # post-validator, not the numeric guard.
    "probability_weighted_etv",
    "weighted_decomposition_sum",
    "expected_return_pct",
    "central_estimate",
    "low_range",
    "high_range",
    # Asymmetry block is computed deterministically by validator.py.
    "upside_pct_weighted",
    "downside_pct_weighted",
    "ratio",
    # Cap & rubric scalars hard-coded in prompts.
    "confidence_pct",
})

# Path-suffix patterns (substring match) to treat as passthrough — covers
# nested fields like `validation.warnings_count`.
_PASSTHROUGH_PATH_HINTS: tuple[str, ...] = (
    "validation.",
    "cache_age_sec",
)

# Leaf-name suffixes that imply a non-valuation scalar (count, year, months,
# percent-as-integer). When the leaf key ends with one of these, the value
# is exempt from the guard regardless of magnitude.
_PASSTHROUGH_KEY_SUFFIXES: tuple[str, ...] = (
    "_count",
    "_year",
    "_months",
    "_days",
    "_years",
    "_n",
)

# Scaling factors to try when matching against grounding values.
# Covers: identity, fraction↔percent, raw↔millions, raw↔billions.
_SCALE_FACTORS: tuple[float, ...] = (1.0, 100.0, 0.01, 1e6, 1e-6, 1e9, 1e-9)

# Regex for declared assumptions inside missing_inputs strings.
# Example: "wacc: ASSUMPTION used = 0.09 (sector median)"
#       or "rev_growth: ASSUMPTION=0.12 (consensus)"
_ASSUMPTION_RE = re.compile(
    r"ASSUMPTION[^=]*=\s*([+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)",
)

# Regex for derivation lines: capture the *final* number after the last `=`.
# Example: "fcf_2026 = rev_2026 * margin * (1 - tax) = 39.2"
_DERIVATION_FINAL_RE = re.compile(
    r"=\s*([+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)\s*$",
)


# ----------------------------------------------------------- Dataclasses ---

@dataclass(frozen=True)
class Unjustified:
    """A numeric leaf the guard could not classify."""
    path: str
    value: float
    nearest_grounded_field: str | None = None
    nearest_grounded_value: float | None = None
    nearest_distance_pct: float | None = None


@dataclass
class GuardReport:
    """Outcome of one guard pass over a stage's JSON output."""
    unjustified: list[Unjustified] = field(default_factory=list)
    grounded_count: int = 0
    declared_count: int = 0
    derived_count: int = 0
    passthrough_count: int = 0
    total_numbers: int = 0

    @property
    def passed(self) -> bool:
        return not self.unjustified

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "total_numbers": self.total_numbers,
            "grounded_count": self.grounded_count,
            "declared_count": self.declared_count,
            "derived_count": self.derived_count,
            "passthrough_count": self.passthrough_count,
            "unjustified": [asdict(u) for u in self.unjustified],
        }


# ----------------------------------------------------------- Extractors ---

def _to_dict(obj: Any) -> dict[str, Any]:
    """Normalise grounding to dict; accept dataclass or mapping."""
    if obj is None:
        return {}
    if is_dataclass(obj):
        return asdict(obj)
    if isinstance(obj, dict):
        return obj
    raise TypeError(f"Unsupported grounding type: {type(obj).__name__}")


def _grounded_values(grounding: Any) -> dict[str, float]:
    """Map grounding field-name → numeric value (skip non-numeric)."""
    out: dict[str, float] = {}
    for k, v in _to_dict(grounding).items():
        if isinstance(v, bool):
            continue
        if isinstance(v, (int, float)) and not (isinstance(v, float) and math.isnan(v)):
            out[k] = float(v)
    return out


def _declared_assumptions(missing_inputs: Iterable[Any] | None) -> list[float]:
    """Extract numeric values from `ASSUMPTION:...=<value>` strings."""
    if not missing_inputs:
        return []
    vals: list[float] = []
    for entry in missing_inputs:
        if not isinstance(entry, str):
            continue
        for m in _ASSUMPTION_RE.finditer(entry):
            try:
                vals.append(float(m.group(1)))
            except ValueError:
                continue
    return vals


def _derived_values(node: Any) -> list[float]:
    """Walk node; for any list under key 'derivation', extract trailing
    `= <number>` from each string entry."""
    vals: list[float] = []

    def _walk(n: Any) -> None:
        if isinstance(n, dict):
            for k, v in n.items():
                if k == "derivation" and isinstance(v, list):
                    for line in v:
                        if isinstance(line, str):
                            m = _DERIVATION_FINAL_RE.search(line)
                            if m:
                                try:
                                    vals.append(float(m.group(1)))
                                except ValueError:
                                    pass
                else:
                    _walk(v)
        elif isinstance(n, list):
            for item in n:
                _walk(item)

    _walk(node)
    return vals


def _iter_numbers(node: Any, path: str = "") -> Iterator[tuple[str, float]]:
    """Yield (json-path, numeric value) for every numeric leaf."""
    if isinstance(node, bool):
        return
    if isinstance(node, (int, float)):
        if not (isinstance(node, float) and math.isnan(node)):
            yield path, float(node)
        return
    if isinstance(node, dict):
        for k, v in node.items():
            sub = f"{path}.{k}" if path else k
            yield from _iter_numbers(v, sub)
    elif isinstance(node, list):
        for i, item in enumerate(node):
            sub = f"{path}[{i}]"
            yield from _iter_numbers(item, sub)


# ------------------------------------------------------- Classification ---

def _is_passthrough(path: str) -> bool:
    leaf = path.rsplit(".", 1)[-1] if "." in path else path
    leaf = leaf.split("[", 1)[0]
    if leaf in _PASSTHROUGH_KEYS:
        return True
    if any(leaf.endswith(sfx) for sfx in _PASSTHROUGH_KEY_SUFFIXES):
        return True
    return any(hint in path for hint in _PASSTHROUGH_PATH_HINTS)


def _matches(value: float, candidate: float, tolerance: float) -> bool:
    if math.isclose(value, candidate, abs_tol=_TOLERANCE_ABS):
        return True
    if candidate == 0:
        return abs(value) < _TOLERANCE_ABS
    return abs(value - candidate) / abs(candidate) <= tolerance


def _nearest_grounded(
    value: float, grounded: dict[str, float]
) -> tuple[str | None, float | None, float | None]:
    """Return (field, scaled_value, distance%) for nearest grounded match
    under any allowed scaling."""
    best: tuple[str | None, float | None, float | None] = (None, None, None)
    best_dist = math.inf
    for name, raw in grounded.items():
        for factor in _SCALE_FACTORS:
            candidate = raw * factor
            if candidate == 0:
                continue
            d = abs(value - candidate) / abs(candidate)
            if d < best_dist:
                best_dist = d
                best = (name, candidate, d * 100.0)
    return best


# ----------------------------------------------------------- Public API ---

def guard(
    stage_output: dict[str, Any],
    grounding: Any,
    *,
    tolerance: float = _TOLERANCE_FRAC,
    extra_passthroughs: Iterable[str] = (),
) -> GuardReport:
    """Classify every numeric leaf in ``stage_output``.

    Parameters
    ----------
    stage_output
        The JSON dict returned by an LLM stage.
    grounding
        Either an ``EtvGrounding`` dataclass or a plain dict of grounding
        fields. Numeric values are matched (with scaling) against these.
    tolerance
        Fractional tolerance for grounded / declared matches (default 0.5%).
    extra_passthroughs
        Additional leaf-key names to exempt from checks (e.g. a stage's
        own deterministic scalar fields).
    """
    extra = frozenset(extra_passthroughs)
    grounded = _grounded_values(grounding)
    declared = _declared_assumptions(stage_output.get("missing_inputs"))
    derived = _derived_values(stage_output)

    report = GuardReport()

    for path, value in _iter_numbers(stage_output):
        report.total_numbers += 1
        leaf = path.rsplit(".", 1)[-1].split("[", 1)[0]
        if _is_passthrough(path) or leaf in extra:
            report.passthrough_count += 1
            continue

        # Grounded (with scaling) ?
        is_grounded = False
        for raw in grounded.values():
            for factor in _SCALE_FACTORS:
                if _matches(value, raw * factor, tolerance):
                    is_grounded = True
                    break
            if is_grounded:
                break
        if is_grounded:
            report.grounded_count += 1
            continue

        # Declared assumption ?
        if any(_matches(value, a, tolerance) for a in declared):
            report.declared_count += 1
            continue

        # Derived (LLM-claimed; spot-checked by critic stage later) ?
        if any(_matches(value, d, tolerance) for d in derived):
            report.derived_count += 1
            continue

        field_name, near_val, near_dist = _nearest_grounded(value, grounded)
        report.unjustified.append(
            Unjustified(
                path=path,
                value=value,
                nearest_grounded_field=field_name,
                nearest_grounded_value=near_val,
                nearest_distance_pct=near_dist,
            )
        )

    return report


def format_report_for_prompt(report: GuardReport, *, max_items: int = 8) -> str:
    """Render guard findings as a compact string to append to a retry prompt."""
    if report.passed:
        return "NUMERIC GUARD: PASSED (all numbers justified)."
    lines = [
        f"NUMERIC GUARD FAILED — {len(report.unjustified)} unjustified number(s):",
    ]
    for u in report.unjustified[:max_items]:
        suffix = ""
        if u.nearest_grounded_field and u.nearest_distance_pct is not None:
            suffix = (
                f"  (nearest grounded: {u.nearest_grounded_field}"
                f"={u.nearest_grounded_value:.4g}, off by"
                f" {u.nearest_distance_pct:.1f}%)"
            )
        lines.append(f"  - {u.path} = {u.value:g}{suffix}")
    if len(report.unjustified) > max_items:
        lines.append(f"  ... and {len(report.unjustified) - max_items} more")
    lines.append(
        "Each number must be (a) a grounding value, (b) declared as "
        "ASSUMPTION:name=value in missing_inputs, or (c) derived in a "
        "derivation[] line. Revise to comply."
    )
    return "\n".join(lines)
