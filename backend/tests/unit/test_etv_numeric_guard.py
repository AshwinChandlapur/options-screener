"""Unit tests for the ETV numeric guard.

The guard classifies every numeric leaf of a stage's JSON output as
grounded / declared / derived / passthrough / unjustified. These tests
exercise each path with synthetic stage outputs against a small
synthetic grounding so we do not depend on yfinance or the LLM.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import pytest

from services.etv.numeric_guard import (
    GuardReport,
    Unjustified,
    format_report_for_prompt,
    guard,
)


# --------------------------------------------------------- Synthetic grounding ---

@dataclass
class _Grounding:
    """Minimal grounding stand-in (matches the real EtvGrounding shape just
    enough for the guard's perspective)."""
    current_price: float = 420.0
    market_cap: float = 3_100_000_000_000.0
    shares_out: float = 7_400_000_000.0
    revenue_ttm: float = 245_000_000_000.0
    revenue_growth_yoy: float = 0.12          # = 12%
    operating_margin: float = 0.44            # = 44%
    free_cash_flow: float = 74_000_000_000.0
    forward_pe: float = 32.0
    implied_vol_30d: float = 0.22
    rsi_14: float = 58.0
    as_of: str = "2026-05-22"


# ============================================================== Arrange ==


@pytest.fixture()
def grounding() -> _Grounding:
    return _Grounding()


# ============================================================== Tests ===


class TestGrounded:
    """Numbers that mirror a grounding field are accepted."""

    def test_exact_match_passes(self, grounding: _Grounding) -> None:
        out = {"fundamental": 420.0}  # = current_price
        report = guard(out, grounding)
        assert report.passed
        assert report.grounded_count == 1

    def test_percent_scaling_passes(self, grounding: _Grounding) -> None:
        # grounding.operating_margin = 0.44 ; model emits 44.1 (~0.2% off as
        # percent, inside 0.5% tolerance).
        out = {"implied_margin_pct": 44.1}
        report = guard(out, grounding)
        assert report.passed
        assert report.grounded_count == 1

    def test_unit_scaling_millions_passes(self, grounding: _Grounding) -> None:
        # grounding.revenue_ttm = 245e9 ; model emits 245000 (in millions)
        out = {"revenue_millions": 245_000.0}
        report = guard(out, grounding)
        assert report.passed

    def test_tolerance_allows_rounding(self, grounding: _Grounding) -> None:
        # current_price = 420 ; emit 421 (≈0.24%, inside 0.5% tolerance)
        out = {"price": 421.0}
        assert guard(out, grounding).passed

    def test_outside_tolerance_fails(self, grounding: _Grounding) -> None:
        # 425 is +1.2% off current_price → not grounded
        out = {"fundamental": 425.0}
        report = guard(out, grounding)
        assert not report.passed
        assert len(report.unjustified) == 1
        u = report.unjustified[0]
        assert u.path == "fundamental"
        assert u.value == 425.0
        assert u.nearest_grounded_field == "current_price"


class TestDeclaredAssumption:
    """Numbers covered by ASSUMPTION:name=value entries pass."""

    def test_assumption_in_missing_inputs(self, grounding: _Grounding) -> None:
        out = {
            "missing_inputs": [
                "wacc: ASSUMPTION used = 0.09 (sector median, no debt cost in feed)",
            ],
            "model_inputs": {"wacc_used": 0.09},
        }
        report = guard(out, grounding)
        assert report.passed
        assert report.declared_count == 1

    def test_assumption_no_space_format(self, grounding: _Grounding) -> None:
        out = {
            "missing_inputs": ["terminal_growth: ASSUMPTION=0.03 (long-run GDP)"],
            "model_inputs": {"g_terminal": 0.03},
        }
        assert guard(out, grounding).passed

    def test_missing_assumption_fails(self, grounding: _Grounding) -> None:
        # Number appears but no ASSUMPTION entry covers it.
        out = {"model_inputs": {"wacc_used": 0.087}}
        report = guard(out, grounding)
        assert not report.passed
        assert report.unjustified[0].path == "model_inputs.wacc_used"


class TestDerivation:
    """Numbers appearing as the final value of a derivation line pass."""

    def test_derived_number_passes(self, grounding: _Grounding) -> None:
        out = {
            "economic_value": {
                "base": {
                    "fundamental": 487.5,
                    "derivation": [
                        "rev_2026 = grounding.revenue_ttm * 1.12 = 274.4",
                        "fcf_yield = grounding.free_cash_flow / market_cap = 0.024",
                        "pv_per_share = sum_pv / shares_out = 487.5",
                    ],
                },
            },
        }
        report = guard(out, grounding)
        assert report.passed, report.unjustified
        assert report.derived_count >= 1


class TestPassthroughs:
    """Whitelisted keys + small ints never trigger the guard."""

    def test_probability_pct_is_passthrough(self, grounding: _Grounding) -> None:
        out = {"bear": {"probability_pct": 25.0, "price": 420.0}}
        report = guard(out, grounding)
        assert report.passed
        assert report.passthrough_count >= 1

    def test_small_integers_are_passthrough(self, grounding: _Grounding) -> None:
        # Suffix-based passthroughs cover counts / years / months.
        out = {"horizon_months": 6, "analyst_count": 35, "fiscal_year": 2026}
        report = guard(out, grounding)
        assert report.passed
        assert report.passthrough_count == 3

    def test_validation_block_is_passthrough(self, grounding: _Grounding) -> None:
        # Anything under validation.* (from validator.py output) is exempt.
        out = {"validation": {"corrections_count": 7, "warnings_count": 0}}
        assert guard(out, grounding).passed


class TestHallucinationDetection:
    """End-to-end: a fabricated DCF number with no source should be flagged."""

    def test_invented_intrinsic_is_flagged(self, grounding: _Grounding) -> None:
        # 612 has no grounding match, no assumption, no derivation.
        out = {
            "economic_value": {
                "bear": {"fundamental": 612.0},
            },
        }
        report = guard(out, grounding)
        assert not report.passed
        bad = report.unjustified[0]
        assert bad.path == "economic_value.bear.fundamental"
        assert bad.value == 612.0
        # Nearest grounded should still be reported for the retry prompt.
        assert bad.nearest_grounded_field is not None

    def test_multiple_unjustified_collected(self, grounding: _Grounding) -> None:
        out = {
            "economic_value": {
                "bear": {"fundamental": 612.0},   # unjustified
                "base": {"fundamental": 420.0},   # grounded (== current_price)
                "bull": {"fundamental": 999.0},   # unjustified
            },
        }
        report = guard(out, grounding)
        assert len(report.unjustified) == 2
        paths = {u.path for u in report.unjustified}
        assert paths == {
            "economic_value.bear.fundamental",
            "economic_value.bull.fundamental",
        }


class TestPromptFormatter:
    """The textual report fed back to the LLM on a retry."""

    def test_pass_message(self, grounding: _Grounding) -> None:
        out = {"price": 420.0}
        msg = format_report_for_prompt(guard(out, grounding))
        assert "PASSED" in msg

    def test_failure_message_lists_paths_and_nearest(
        self, grounding: _Grounding
    ) -> None:
        out = {"deep": {"nested": {"value": 612.0}}}
        report = guard(out, grounding)
        msg = format_report_for_prompt(report)
        assert "deep.nested.value" in msg
        assert "612" in msg
        assert "nearest grounded" in msg
        assert "current_price" in msg

    def test_failure_message_truncates(self, grounding: _Grounding) -> None:
        out = {f"k{i}": 600.0 + i * 50 for i in range(20)}
        report = guard(out, grounding)
        msg = format_report_for_prompt(report, max_items=3)
        assert "and 17 more" in msg


class TestExtraPassthroughs:
    """Callers can exempt stage-specific fields."""

    def test_extra_passthrough_exempts_field(self, grounding: _Grounding) -> None:
        # 67.4 sits far from every grounding field at every scale factor,
        # so without exemption it must be flagged.
        out = {"selection_confidence_pct": 67.4}
        assert not guard(out, grounding).passed
        # With exemption — accepted.
        report = guard(
            out, grounding, extra_passthroughs={"selection_confidence_pct"}
        )
        assert report.passed


# ================================ Phase 2: tag-aware + structural checks ===


class TestProvenanceTagInDerivation:
    """v3-final RULE A: derivations may carry an optional `[tag]` after the
    final number. The trailing-number regex must still match."""

    def test_tagged_derived_number_matches_leaf(self, grounding: _Grounding) -> None:
        out = {
            "economic_value": {
                "base": {
                    "fundamental": 480.5,
                    "derivation": [
                        "fair_value = 245000 * 1.5 / 7400 = 480.5 [derived]",
                    ],
                }
            }
        }
        report = guard(out, grounding)
        # `fundamental` is in extra_passthroughs in s2_intrinsic, but here
        # we call guard() directly so the leaf still classifies as derived.
        assert report.passed, report.unjustified

    def test_untagged_derived_number_still_matches(self, grounding: _Grounding) -> None:
        out = {
            "economic_value": {
                "base": {
                    "fundamental": 480.5,
                    "derivation": [
                        "fair_value = 245000 * 1.5 / 7400 = 480.5",
                    ],
                }
            }
        }
        report = guard(out, grounding)
        assert report.passed, report.unjustified


class TestAssumedTagCounter:
    def test_zero_when_no_tags(self, grounding: _Grounding) -> None:
        out = {"x": {"derivation": ["a = 1 + 2 = 3"]}}
        report = guard(out, grounding)
        assert report.assumed_tag_count == 0
        assert report.assumption_heavy is False

    def test_counts_case_insensitive(self, grounding: _Grounding) -> None:
        out = {
            "x": {
                "derivation": [
                    "a = 1 [ASSUMED] + 2 [from grounding] = 3 [derived]",
                    "b = 4 [assumed] + 5 [Assumed] = 9 [derived]",
                ]
            }
        }
        report = guard(out, grounding)
        assert report.assumed_tag_count == 3
        assert report.assumption_heavy is False  # threshold is > 3

    def test_assumption_heavy_above_threshold(self, grounding: _Grounding) -> None:
        out = {
            "x": {
                "derivation": [
                    "a = 1 [ASSUMED] + 2 [ASSUMED] + 3 [ASSUMED] = 6 [derived]",
                    "b = 4 [ASSUMED] + 5 [ASSUMED] = 9 [derived]",
                ]
            }
        }
        report = guard(out, grounding)
        assert report.assumed_tag_count == 5
        assert report.assumption_heavy is True


class TestS2StructuralValidator:
    """Exercise `validate_s2_structure` via `guard(..., validate_structure=True)`."""

    def _ev_recipe_output(self) -> dict:
        return {
            "economic_value": {
                "base": {
                    "fundamental": 480.0,
                    "derivation": [
                        'sbc_treatment = "subtracted_from_fcf"',
                        "shares_out_diluted = basic_shares + tsm_dilution = 7500",
                        "enterprise_value = sum_pv_explicit + pv_terminal = 3600000",
                        "net_debt = 55000 [from grounding] + 0 [from grounding] - 75000 [from grounding] - 0 [from grounding] = -20000",
                        "equity_value = 3600000 [derived] - -20000 [derived] - 0 [from grounding] - 0 [from grounding] - 0 [ASSUMED] = 3620000",
                        "fundamental = equity_value / shares_out_diluted = 482.7",
                    ],
                }
            }
        }

    def test_ev_recipe_clean_has_no_warnings(self, grounding: _Grounding) -> None:
        out = self._ev_recipe_output()
        report = guard(out, grounding, validate_structure=True)
        assert report.structure_warnings == [], report.structure_warnings

    def test_ev_recipe_missing_net_debt_warns(self, grounding: _Grounding) -> None:
        out = self._ev_recipe_output()
        # Remove the net_debt line.
        deriv = out["economic_value"]["base"]["derivation"]
        out["economic_value"]["base"]["derivation"] = [
            ln for ln in deriv if not ln.lower().startswith("net_debt")
        ]
        report = guard(out, grounding, validate_structure=True)
        joined = "\n".join(report.structure_warnings)
        assert "net_debt" in joined and "RULE A" in joined

    def test_ev_recipe_short_net_debt_warns(self, grounding: _Grounding) -> None:
        out = self._ev_recipe_output()
        deriv = out["economic_value"]["base"]["derivation"]
        out["economic_value"]["base"]["derivation"] = [
            "net_debt = 55000 - 75000 = -20000" if ln.lower().startswith("net_debt") else ln
            for ln in deriv
        ]
        report = guard(out, grounding, validate_structure=True)
        joined = "\n".join(report.structure_warnings)
        assert "net_debt bridge has < 4 RHS terms" in joined

    def test_missing_sbc_treatment_warns(self, grounding: _Grounding) -> None:
        out = self._ev_recipe_output()
        deriv = out["economic_value"]["base"]["derivation"]
        out["economic_value"]["base"]["derivation"] = [
            ln for ln in deriv if "sbc_treatment" not in ln
        ]
        report = guard(out, grounding, validate_structure=True)
        joined = "\n".join(report.structure_warnings)
        assert "sbc_treatment" in joined and "RULE C" in joined

    def test_bare_number_fundamental_warns(self, grounding: _Grounding) -> None:
        out = self._ev_recipe_output()
        deriv = out["economic_value"]["base"]["derivation"]
        out["economic_value"]["base"]["derivation"] = [
            "fundamental = 482.7" if ln.lower().startswith("fundamental") else ln
            for ln in deriv
        ]
        report = guard(out, grounding, validate_structure=True)
        joined = "\n".join(report.structure_warnings)
        assert "RULE H" in joined

    def test_equity_only_model_skips_ev_bridge_checks(self, grounding: _Grounding) -> None:
        # P/E or DDM recipe: no enterprise_value mention -> RULE A is skipped.
        out = {
            "economic_value": {
                "base": {
                    "fundamental": 480.0,
                    "derivation": [
                        'sbc_treatment = "kept_in_earnings_with_dilution"',
                        "normalised_eps = 12 [from grounding] = 12",
                        "fundamental = normalised_eps * chosen_pe = 480",
                    ],
                }
            }
        }
        report = guard(out, grounding, validate_structure=True)
        # Should NOT complain about missing net_debt / equity_value.
        joined = "\n".join(report.structure_warnings)
        assert "net_debt" not in joined
        assert "equity_value" not in joined

    def test_structure_warnings_do_not_flip_passed(self, grounding: _Grounding) -> None:
        # Output with structure issues but no unjustified numbers.
        out = {
            "missing_inputs": [],
            "economic_value": {
                "base": {
                    "fundamental": 420.0,  # grounded == current_price
                    "derivation": [
                        # Missing sbc_treatment + missing bridge lines.
                        "fundamental = 420",
                    ],
                }
            }
        }
        report = guard(out, grounding, validate_structure=True)
        assert report.passed is True
        assert report.structure_warnings  # but warnings are present


class TestGuardReportToDict:
    def test_includes_phase2_fields(self, grounding: _Grounding) -> None:
        out = {
            "x": {
                "derivation": [
                    "a = 1 [ASSUMED] + 2 [ASSUMED] + 3 [ASSUMED] + 4 [ASSUMED] = 10 [derived]",
                ]
            }
        }
        report = guard(out, grounding, validate_structure=True)
        d = report.to_dict()
        assert "assumed_tag_count" in d and d["assumed_tag_count"] == 4
        assert "assumption_heavy" in d and d["assumption_heavy"] is True
        assert "structure_warnings" in d and isinstance(d["structure_warnings"], list)


class TestFormatReportForPromptPhase2:
    def test_includes_structure_warnings_section(self, grounding: _Grounding) -> None:
        out = {
            "economic_value": {
                "base": {
                    "fundamental": 420.0,
                    "derivation": ["fundamental = 420"],
                }
            }
        }
        report = guard(out, grounding, validate_structure=True)
        text = format_report_for_prompt(report)
        assert "STRUCTURE WARNINGS" in text

    def test_includes_assumption_heavy_section(self, grounding: _Grounding) -> None:
        out = {
            "x": {
                "derivation": [
                    "a = 1 [ASSUMED] + 2 [ASSUMED] + 3 [ASSUMED] + 4 [ASSUMED] = 10 [derived]",
                ]
            }
        }
        report = guard(out, grounding)
        text = format_report_for_prompt(report)
        assert "ASSUMPTION-HEAVY OUTPUT" in text
