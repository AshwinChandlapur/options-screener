"""Phase 3 of the staged ETV S2 rebuild: S2 reroute on model_inapplicable.

Covers
======
* ``orchestrator._resolve_s2_reroute`` — pure-function helper picking the
  next ``primary_model`` from S1's ``supporting_models`` list.
* The orchestrator's staged path — full ``get_etv`` flow with every stage
  mocked, asserting the reroute fires exactly once when S2 declares
  ``model_inapplicable=True`` and a fallback is available.
* The S2 strict schema — must declare the new ``model_inapplicable`` and
  ``inapplicability_reason`` fields as required + nullable.

LLM and yfinance are stubbed; this is pure orchestration logic.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import pytest

from services.etv import orchestrator
from services.etv.schemas import S2_INTRINSIC_SCHEMA
from services.etv.stages import (
    s0_scaffold, s1_audit, s2_intrinsic, s3_overlays, s4_decision, s5_critic,
)
from services.etv.stages._base import StageResult


# --------------------------------------------------------- Grounding stub ---

@dataclass
class _G:
    ticker: str = "MSFT"
    company_name: str = "Microsoft"
    sector: Optional[str] = "Technology"
    industry: Optional[str] = "Software"
    business_summary: Optional[str] = "n/a"
    current_price: float = 420.0
    market_cap: Optional[float] = 3_100_000_000_000.0
    enterprise_value: Optional[float] = 3_050_000_000_000.0
    shares_out: Optional[float] = 7_400_000_000.0
    week52_high: Optional[float] = 470.0
    week52_low: Optional[float] = 310.0
    avg_volume_10d: Optional[float] = 20_000_000.0
    implied_vol_30d: Optional[float] = 0.22
    short_pct_float: Optional[float] = 0.005
    trailing_pe: Optional[float] = 34.0
    forward_pe: Optional[float] = 32.0
    ev_ebitda: Optional[float] = 24.0
    ev_revenue: Optional[float] = 12.0
    price_to_fcf: Optional[float] = 38.0
    price_to_book: Optional[float] = 12.0
    revenue_ttm: Optional[float] = 245_000_000_000.0
    revenue_growth_yoy: Optional[float] = 0.12
    gross_margin: Optional[float] = 0.69
    ebitda: Optional[float] = 130_000_000_000.0
    ebitda_margin: Optional[float] = 0.53
    operating_income: Optional[float] = 108_000_000_000.0
    operating_margin: Optional[float] = 0.44
    net_income: Optional[float] = 88_000_000_000.0
    eps_ttm: Optional[float] = 11.9
    free_cash_flow: Optional[float] = 74_000_000_000.0
    total_debt: Optional[float] = 55_000_000_000.0
    net_debt: Optional[float] = -20_000_000_000.0
    cash: Optional[float] = 75_000_000_000.0
    capex: Optional[float] = 44_000_000_000.0
    roic: Optional[float] = 0.22
    forward_revenue: Optional[float] = 280_000_000_000.0
    forward_eps: Optional[float] = 13.5
    long_term_growth: Optional[float] = 0.11
    analyst_count: Optional[int] = 35
    analyst_recommendation: Optional[str] = "buy"
    analyst_target_mean: Optional[float] = 480.0
    analyst_target_high: Optional[float] = 600.0
    analyst_target_low: Optional[float] = 380.0
    sma_50: Optional[float] = 410.0
    sma_200: Optional[float] = 390.0
    rsi_14: Optional[float] = 58.0
    as_of: str = "2026-05-23"


@pytest.fixture
def g() -> _G:
    return _G()


# --------------------------------------------------------- _resolve_s2_reroute ---


class TestResolveS2Reroute:
    """Pure-function unit tests for ``orchestrator._resolve_s2_reroute``."""

    def test_returns_none_when_model_applicable(self):
        s2 = {"model_inapplicable": False}
        s1 = {"primary_model": "DCF", "supporting_models": ["EV/EBITDA multiple"]}
        assert orchestrator._resolve_s2_reroute(s2, s1) is None

    def test_returns_none_when_field_absent(self):
        s2 = {"economic_value": {}}
        s1 = {"primary_model": "DCF", "supporting_models": ["EV/EBITDA multiple"]}
        assert orchestrator._resolve_s2_reroute(s2, s1) is None

    def test_returns_none_when_field_null(self):
        s2 = {"model_inapplicable": None}
        s1 = {"primary_model": "DCF", "supporting_models": ["EV/EBITDA multiple"]}
        assert orchestrator._resolve_s2_reroute(s2, s1) is None

    def test_returns_first_supporting_model_when_inapplicable(self):
        s2 = {"model_inapplicable": True}
        s1 = {
            "primary_model": "DCF",
            "supporting_models": ["EV/EBITDA multiple", "DDM"],
        }
        assert orchestrator._resolve_s2_reroute(s2, s1) == "EV/EBITDA multiple"

    def test_skips_current_primary_in_supporting_list(self):
        s2 = {"model_inapplicable": True}
        s1 = {
            "primary_model": "DCF",
            "supporting_models": ["DCF", "DDM"],
        }
        assert orchestrator._resolve_s2_reroute(s2, s1) == "DDM"

    def test_returns_none_when_supporting_empty(self):
        s2 = {"model_inapplicable": True, "inapplicability_reason": "EBITDA<0"}
        s1 = {"primary_model": "DCF", "supporting_models": []}
        assert orchestrator._resolve_s2_reroute(s2, s1) is None

    def test_returns_none_when_only_current_in_supporting(self):
        s2 = {"model_inapplicable": True}
        s1 = {"primary_model": "DCF", "supporting_models": ["DCF"]}
        assert orchestrator._resolve_s2_reroute(s2, s1) is None

    def test_handles_missing_supporting_models_key(self):
        s2 = {"model_inapplicable": True}
        s1 = {"primary_model": "DCF"}
        assert orchestrator._resolve_s2_reroute(s2, s1) is None

    def test_handles_non_list_supporting_models(self):
        s2 = {"model_inapplicable": True}
        s1 = {"primary_model": "DCF", "supporting_models": "not a list"}
        assert orchestrator._resolve_s2_reroute(s2, s1) is None

    def test_handles_non_string_entries(self):
        s2 = {"model_inapplicable": True}
        s1 = {"primary_model": "DCF", "supporting_models": [None, 42, "", "DDM"]}
        assert orchestrator._resolve_s2_reroute(s2, s1) == "DDM"


# --------------------------------------------------------- Schema (Phase 3) ---


class TestS2SchemaPhase3:
    """S2 strict schema declares the new model_inapplicable + reason fields."""

    def test_schema_includes_inapplicability_fields(self):
        props = S2_INTRINSIC_SCHEMA["schema"]["properties"]
        assert "model_inapplicable" in props
        assert "inapplicability_reason" in props

    def test_inapplicability_fields_required_by_strict_mode(self):
        required = S2_INTRINSIC_SCHEMA["schema"]["required"]
        assert "model_inapplicable" in required
        assert "inapplicability_reason" in required

    def test_inapplicability_fields_are_nullable(self):
        props = S2_INTRINSIC_SCHEMA["schema"]["properties"]
        assert "null" in props["model_inapplicable"]["type"]
        assert "null" in props["inapplicability_reason"]["type"]


# --------------------------------------------------------- Integration: full flow ---


def _good_s0_output() -> dict:
    return {
        "company_summary": {
            "what_it_does": "x", "how_it_makes_money": "x",
            "scale_and_position": "x",
        },
        "candidate_archetypes": ["Growth", "Mature cash flow"],
        "supporting_models": ["EV/EBITDA multiple", "DDM"],
    }


def _good_s1_output() -> dict:
    return {
        "model_archetype": "Growth",
        "archetype_rationale": "rationale",
        "primary_model": "DCF",
        "model_rationale": "rationale",
        "required_inputs": [],
        "missing_inputs": [],
        "selection_confidence": "High",
        "supporting_models": ["EV/EBITDA multiple", "DDM"],
    }


def _s2_output(*, inapplicable: bool, reason: str | None = None) -> dict:
    """Minimal S2 output the orchestrator can splice."""
    scenario = {
        "probability_pct": 33.0,
        "price": 420.0,
        "fundamental": 420.0,
        "value_decomposition": {
            "fundamental": 420.0,
            "regime_adjustment": 0,
            "market_expectations_adjustment": 0,
            "optionality": 0,
            "behavioral_premium": 0,
        },
        "derivation": ["fundamental = 420"],
        "conditions": ["c"],
        "rationale": "r",
    }
    return {
        "missing_inputs": [],
        "model_inapplicable": inapplicable,
        "inapplicability_reason": reason,
        "economic_value": {
            "bear": dict(scenario, probability_pct=33.0),
            "base": dict(scenario, probability_pct=34.0),
            "bull": dict(scenario, probability_pct=33.0),
            "central_estimate": 420.0,
            "low_range": 400.0,
            "high_range": 440.0,
            "key_drivers": ["d"],
            "key_sensitivities": ["s"],
        },
    }


def _good_s3_output() -> dict:
    sc = {
        "probability_pct": 33.0, "fundamental": 420.0, "price": 420.0,
        "value_decomposition": {
            "fundamental": 420.0, "regime_adjustment": 0,
            "market_expectations_adjustment": 0, "optionality": 0,
            "behavioral_premium": 0,
        },
        "regime_multiplier": "1x", "behavior_impact": "n",
        "conditions": ["c"], "rationale": "r",
        "derivation": ["fundamental = 420"],
    }
    return {
        "regime": {"label": "neutral", "rationale": "r", "drivers": ["d"]},
        "optionality": {"sources": [], "magnitude": "low", "rationale": "r"},
        "market_implied": {
            "rationale": "r", "implied_growth": None,
            "implied_margin": None, "implied_multiple": None,
        },
        "market_behavior": {"label": "neutral", "rationale": "r"},
        "etv": {
            "bear": dict(sc, probability_pct=33.0),
            "base": dict(sc, probability_pct=34.0),
            "bull": dict(sc, probability_pct=33.0),
            "probability_weighted_etv": 420.0,
            "weighted_decomposition_sum": 420.0,
            "expected_return_pct": 0.0,
        },
        "missing_inputs": [],
    }


def _good_s4_output() -> dict:
    return {
        "risk": {"label": "moderate", "rationale": "r", "key_risks": ["r"]},
        "asymmetry": {
            "upside_pct_weighted": 5.0, "downside_pct_weighted": 3.0,
            "ratio": 1.67, "rationale": "r",
        },
        "decision": {
            "action": "BUY", "conviction": "Medium",
            "confidence_pct": 60, "rationale": "r",
        },
        "sizing": {"target_pct": 2.0, "rationale": "r"},
        "catalysts": [{"name": "n", "timing": "t", "impact": "i"}],
        "failure_conditions": ["f"],
        "core_thesis": "t",
        "advisor_challenges": ["c"],
        "missing_inputs": [],
    }


def _good_s5_pass() -> dict:
    return {
        "verdict": "PASS",
        "issues": [],
        "retry_stage": None,
        "retry_focus": None,
        "rationale": "ok",
    }


@pytest.fixture
def _mock_stages(monkeypatch, g):
    """Patch every stage + grounding in the orchestrator namespace."""

    def _fake_grounding(_ticker):
        return g

    def _fake_s0(_g):
        return StageResult(stage="S0_scaffold", output=_good_s0_output(),
                           guard=None, latency_ms=1)

    def _fake_s1(_g):
        return StageResult(stage="S1_audit", output=_good_s1_output(),
                           guard=None, latency_ms=1)

    def _fake_s3(*args, **kw):
        return StageResult(stage="S3_overlays", output=_good_s3_output(),
                           guard=None, latency_ms=1)

    def _fake_s4(*args, **kw):
        return StageResult(stage="S4_decision", output=_good_s4_output(),
                           guard=None, latency_ms=1)

    def _fake_s5(*args, **kw):
        return StageResult(stage="S5_critic", output=_good_s5_pass(),
                           guard=None, latency_ms=1)

    monkeypatch.setattr(orchestrator, "fetch_grounding", _fake_grounding)
    monkeypatch.setattr(orchestrator, "run_s0", _fake_s0)
    monkeypatch.setattr(orchestrator, "run_s1", _fake_s1)
    monkeypatch.setattr(orchestrator, "run_s3", _fake_s3)
    monkeypatch.setattr(orchestrator, "run_s4", _fake_s4)
    monkeypatch.setattr(orchestrator, "run_s5", _fake_s5)
    # The orchestrator caches results — bust between tests.
    orchestrator._CACHE.clear()
    yield
    orchestrator._CACHE.clear()


def _pipeline_log(report: dict) -> list[dict]:
    return list(report.get("pipeline_log") or [])


class TestOrchestratorRerouteIntegration:
    def test_reroutes_once_when_first_s2_inapplicable(self, monkeypatch, _mock_stages):
        calls: list[str | None] = []

        def _fake_s2(_g, s1_output, critic_feedback=None):
            calls.append(s1_output.get("primary_model"))
            inapplicable = len(calls) == 1
            reason = "EBITDA negative" if inapplicable else None
            return StageResult(
                stage="S2_intrinsic",
                output=_s2_output(inapplicable=inapplicable, reason=reason),
                guard=None, latency_ms=1,
            )

        monkeypatch.setattr(orchestrator, "run_s2", _fake_s2)
        report = orchestrator.get_etv("MSFT", refresh=True)

        assert calls == ["DCF", "EV/EBITDA multiple"]
        reroute = [e for e in _pipeline_log(report)
                   if e.get("stage") == "S2_reroute"]
        assert len(reroute) == 1
        assert reroute[0]["from_model"] == "DCF"
        assert reroute[0]["to_model"] == "EV/EBITDA multiple"
        assert reroute[0]["reason"] == "EBITDA negative"
        # The rerouted primary_model propagates into the spliced ETV report.
        assert (
            report["report"]["model_selection"]["primary_model"]
            == "EV/EBITDA multiple"
        )

    def test_no_reroute_when_first_s2_applicable(self, monkeypatch, _mock_stages):
        calls: list[str | None] = []

        def _fake_s2(_g, s1_output, critic_feedback=None):
            calls.append(s1_output.get("primary_model"))
            return StageResult(
                stage="S2_intrinsic",
                output=_s2_output(inapplicable=False),
                guard=None, latency_ms=1,
            )

        monkeypatch.setattr(orchestrator, "run_s2", _fake_s2)
        report = orchestrator.get_etv("MSFT", refresh=True)

        assert calls == ["DCF"]
        assert not any(e.get("stage") == "S2_reroute"
                       for e in _pipeline_log(report))

    def test_no_reroute_when_supporting_models_empty(self, monkeypatch, _mock_stages):
        # Patch S1 to return an empty supporting_models list.
        def _fake_s1_empty(_g):
            out = _good_s1_output()
            out["supporting_models"] = []
            return StageResult(stage="S1_audit", output=out,
                               guard=None, latency_ms=1)

        monkeypatch.setattr(orchestrator, "run_s1", _fake_s1_empty)

        calls: list[str | None] = []

        def _fake_s2(_g, s1_output, critic_feedback=None):
            calls.append(s1_output.get("primary_model"))
            return StageResult(
                stage="S2_intrinsic",
                output=_s2_output(inapplicable=True, reason="no path"),
                guard=None, latency_ms=1,
            )

        monkeypatch.setattr(orchestrator, "run_s2", _fake_s2)
        report = orchestrator.get_etv("MSFT", refresh=True)

        assert calls == ["DCF"]
        assert not any(e.get("stage") == "S2_reroute"
                       for e in _pipeline_log(report))

    def test_reroute_caps_at_one_when_second_s2_also_inapplicable(
        self, monkeypatch, _mock_stages,
    ):
        calls: list[str | None] = []

        def _fake_s2(_g, s1_output, critic_feedback=None):
            calls.append(s1_output.get("primary_model"))
            return StageResult(
                stage="S2_intrinsic",
                output=_s2_output(inapplicable=True, reason="still inapplicable"),
                guard=None, latency_ms=1,
            )

        monkeypatch.setattr(orchestrator, "run_s2", _fake_s2)
        report = orchestrator.get_etv("MSFT", refresh=True)

        # Exactly two S2 calls; no third attempt.
        assert calls == ["DCF", "EV/EBITDA multiple"]
        reroute = [e for e in _pipeline_log(report)
                   if e.get("stage") == "S2_reroute"]
        assert len(reroute) == 1
