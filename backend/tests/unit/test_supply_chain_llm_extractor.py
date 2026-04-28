"""Unit tests for ``services.supply_chain.llm_extractor``.

Mocks the ``AzureOpenAI`` client by injecting a stub via the
``client=`` constructor parameter. No network traffic, no Azure config
required.
"""
from __future__ import annotations

import json
from typing import Any

import pytest

from services.supply_chain.llm_extractor import LlmSupplyChainExtractor
from services.supply_chain.types import (
    LlmCompanyEntry,
    LlmFilingResult,
    LlmIndustryResult,
    LlmVerifierResult,
)


# ----------------------------------------------------------- mock infrastructure
class _StubMessage:
    def __init__(self, content: str) -> None:
        self.content = content


class _StubChoice:
    def __init__(self, content: str) -> None:
        self.message = _StubMessage(content)


class _StubResponse:
    def __init__(self, content: str) -> None:
        self.choices = [_StubChoice(content)]


class _StubCompletions:
    def __init__(self, content: str) -> None:
        self._content = content
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> _StubResponse:
        self.calls.append(kwargs)
        return _StubResponse(self._content)


class _StubChat:
    def __init__(self, completions: _StubCompletions) -> None:
        self.completions = completions


class _StubClient:
    """Drop-in replacement for ``AzureOpenAI`` with one canned response."""

    def __init__(self, content: str) -> None:
        self.completions = _StubCompletions(content)
        self.chat = _StubChat(self.completions)


def _make_extractor(canned_json: dict | str) -> tuple[LlmSupplyChainExtractor, _StubClient]:
    payload = canned_json if isinstance(canned_json, str) else json.dumps(canned_json)
    client = _StubClient(payload)
    extractor = LlmSupplyChainExtractor(client=client)  # type: ignore[arg-type]
    return extractor, client


# ----------------------------------------------------------- filing-pass tests
def test_extract_filing_returns_validated_result():
    # Arrange
    canned = {
        "segments": ["Cloud", "Productivity"],
        "concentration_note": "No customer over 10%.",
        "suppliers": [{"name": "TSMC", "ticker": "TSM", "relationship": "Foundry"}],
        "customers": [],
        "competitors": [],
        "summary": "Hyperscaler with diverse supply chain.",
    }
    extractor, _ = _make_extractor(canned)

    # Act
    result = extractor.extract_filing(
        ticker="MSFT",
        company_name="Microsoft",
        filing_text="business text",
    )

    # Assert
    assert isinstance(result, LlmFilingResult)
    assert result.segments == ["Cloud", "Productivity"]
    assert len(result.suppliers) == 1
    assert result.suppliers[0].ticker == "TSM"


def test_extract_filing_includes_8k_section_when_provided():
    # Arrange
    extractor, client = _make_extractor({"summary": "ok"})

    # Act
    extractor.extract_filing(
        ticker="MSFT",
        company_name="Microsoft",
        filing_text="10K body",
        recent_8k_text="8K body",
    )

    # Assert
    user_msg = client.completions.calls[0]["messages"][1]["content"]
    assert "10K body" in user_msg
    assert "Recent 8-K filings" in user_msg
    assert "8K body" in user_msg


def test_extract_filing_omits_8k_section_when_blank():
    # Arrange
    extractor, client = _make_extractor({"summary": "ok"})

    # Act
    extractor.extract_filing(
        ticker="MSFT",
        company_name="Microsoft",
        filing_text="10K body",
    )

    # Assert
    user_msg = client.completions.calls[0]["messages"][1]["content"]
    assert "Recent 8-K filings" not in user_msg


def test_extract_filing_invalid_json_raises_runtime_error():
    # Arrange — segments must be a list, not a string
    extractor, _ = _make_extractor({"segments": "not a list"})

    # Act / Assert
    with pytest.raises(RuntimeError, match="filing-pass response failed validation"):
        extractor.extract_filing(
            ticker="MSFT",
            company_name="Microsoft",
            filing_text="x",
        )


def test_extract_filing_uses_low_temperature():
    # Arrange
    extractor, client = _make_extractor({"summary": "ok"})

    # Act
    extractor.extract_filing(ticker="X", company_name="X Co", filing_text="x")

    # Assert
    assert client.completions.calls[0]["temperature"] == 0.1


# --------------------------------------------------------- industry-pass tests
def test_enrich_industry_returns_validated_result():
    # Arrange
    canned = {
        "suppliers": [{"name": "NVIDIA", "ticker": "NVDA", "confidence": 0.95}],
        "customers": [],
        "competitors": [],
    }
    extractor, _ = _make_extractor(canned)
    existing = LlmFilingResult()

    # Act
    result = extractor.enrich_industry(
        ticker="MSFT",
        company_name="Microsoft",
        segments=["Cloud"],
        existing=existing,
    )

    # Assert
    assert isinstance(result, LlmIndustryResult)
    assert result.suppliers[0].name == "NVIDIA"


def test_enrich_industry_invalid_json_raises_runtime_error():
    # Arrange
    extractor, _ = _make_extractor({"suppliers": "bad"})

    # Act / Assert
    with pytest.raises(RuntimeError, match="industry-pass response failed validation"):
        extractor.enrich_industry(
            ticker="MSFT",
            company_name="Microsoft",
            segments=[],
            existing=LlmFilingResult(),
        )


# ---------------------------------------------------------- verifier-pass tests
def test_verify_short_circuits_on_empty_candidates():
    # Arrange
    extractor, client = _make_extractor({"audit_summary": "should not be called"})
    candidates = LlmIndustryResult()  # all empty lists

    # Act
    result = extractor.verify(
        ticker="MSFT", company_name="Microsoft", candidates=candidates
    )

    # Assert
    assert isinstance(result, LlmVerifierResult)
    assert result.audit_summary == ""
    assert client.completions.calls == []  # API never called


def test_verify_returns_validated_result_when_candidates_present():
    # Arrange
    canned = {
        "suppliers": [{"name": "NVIDIA", "ticker": "NVDA"}],
        "customers": [],
        "competitors": [],
        "audit_summary": "Dropped 1 weak entry.",
    }
    extractor, client = _make_extractor(canned)
    candidates = LlmIndustryResult(
        suppliers=[
            LlmCompanyEntry(name="NVIDIA", ticker="NVDA"),
            LlmCompanyEntry(name="Weak", ticker=None),
        ],
    )

    # Act
    result = extractor.verify(
        ticker="MSFT", company_name="Microsoft", candidates=candidates
    )

    # Assert
    assert isinstance(result, LlmVerifierResult)
    assert result.audit_summary == "Dropped 1 weak entry."
    assert len(result.suppliers) == 1
    assert client.completions.calls[0]["temperature"] == 0.0


def test_verify_invalid_json_raises_runtime_error():
    # Arrange
    extractor, _ = _make_extractor({"audit_summary": 123, "suppliers": "bad"})
    candidates = LlmIndustryResult(suppliers=[LlmCompanyEntry(name="X")])

    # Act / Assert
    with pytest.raises(RuntimeError, match="verifier-pass response failed validation"):
        extractor.verify(
            ticker="MSFT", company_name="Microsoft", candidates=candidates
        )


def test_extractor_without_config_raises_when_used_without_client_override():
    # Arrange — no api_key, no endpoint, no injected client
    extractor = LlmSupplyChainExtractor(api_key="", endpoint="")

    # Act / Assert
    with pytest.raises(RuntimeError, match="Azure OpenAI not configured"):
        extractor.extract_filing(
            ticker="X", company_name="X", filing_text="x"
        )
