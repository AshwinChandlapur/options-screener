"""Unit tests for ``services.supply_chain.pipeline.get_supply_chain``.

Both adapters are injected as in-memory fakes via the keyword-only
``sec_client`` / ``llm`` parameters. No network or LLM calls. These
tests target the orchestration logic directly (resolution, dedup,
enrichment fall-back, ``eight_k_failed_count`` plumbing); the integration
fixtures in ``test_supply_chain_baseline.py`` cover the full graph
shapes for real tickers.
"""
from __future__ import annotations

from typing import Any

import pytest

from services.supply_chain.pipeline import (
    _node_key,
    _to_company_node,
    get_supply_chain,
)
from services.supply_chain.types import (
    EightKFetchResult,
    LlmCompanyEntry,
    LlmFilingResult,
    LlmIndustryResult,
    LlmVerifierResult,
    SupplyChainGraph,
)


# ----------------------------------------------------------------- fakes
class _FakeSec:
    """Configurable ``SecDataClient`` stand-in."""

    _NO_FILING = object()  # sentinel: distinguish None from "use default"

    def __init__(
        self,
        *,
        cik: str | None = "0000789019",
        latest_10k: dict | None | object = _NO_FILING,
        eight_ks: list[dict] | None = None,
        eight_k_fail_urls: set[str] | None = None,
    ) -> None:
        self._cik = cik
        if latest_10k is _FakeSec._NO_FILING:
            self._latest_10k: dict | None = {
                "accession": "0000789019-24-000001",
                "filing_date": "2024-07-30",
                "primary_doc_url": "https://example.com/10k.htm",
                "company_name": "Microsoft Corp",
                "form": "10-K",
            }
        else:
            self._latest_10k = latest_10k  # type: ignore[assignment]
        self._eight_ks = eight_ks if eight_ks is not None else []
        self._fail_urls = eight_k_fail_urls or set()

    def resolve_cik(self, _ticker: str) -> str | None:
        return self._cik

    def get_latest_10k(self, _cik: str) -> dict | None:
        return self._latest_10k

    def fetch_filing_text(self, _url: str) -> str:
        return "filing text"

    def get_recent_8ks(
        self, _cik: str, since_date: str, max_count: int = 8  # noqa: ARG002
    ) -> list[dict]:
        return list(self._eight_ks)

    def fetch_8ks_parallel(
        self, items: list[dict], max_workers: int = 4  # noqa: ARG002
    ) -> EightKFetchResult:
        successful: list[tuple[dict, str]] = []
        failed = 0
        for meta in items:
            if meta["primary_doc_url"] in self._fail_urls:
                failed += 1
            else:
                successful.append((meta, f"text-{meta['accession']}"))
        return EightKFetchResult(successful=successful, failed_count=failed)


class _FakeLlm:
    """Configurable extractor stand-in."""

    def __init__(
        self,
        *,
        filing: LlmFilingResult,
        industry: LlmIndustryResult | None = None,
        verifier: LlmVerifierResult | None = None,
        industry_raises: Exception | None = None,
        verifier_raises: Exception | None = None,
    ) -> None:
        self._filing = filing
        self._industry = industry or LlmIndustryResult()
        self._verifier = verifier or LlmVerifierResult()
        self._industry_raises = industry_raises
        self._verifier_raises = verifier_raises
        self.calls: dict[str, int] = {"filing": 0, "industry": 0, "verifier": 0}

    def extract_filing(self, **_kwargs: Any) -> LlmFilingResult:
        self.calls["filing"] += 1
        return self._filing

    def enrich_industry(self, **_kwargs: Any) -> LlmIndustryResult:
        self.calls["industry"] += 1
        if self._industry_raises is not None:
            raise self._industry_raises
        return self._industry

    def verify(self, **_kwargs: Any) -> LlmVerifierResult:
        self.calls["verifier"] += 1
        if self._verifier_raises is not None:
            raise self._verifier_raises
        return self._verifier


# ----------------------------------------------------------- pure helper tests
def test_node_key_prefers_ticker_over_name():
    # Arrange / Act
    key = _node_key("Some Company", "abc")

    # Assert
    assert key == "T:ABC"


def test_node_key_uses_name_when_ticker_missing():
    # Arrange / Act
    key = _node_key("  Some Company  ", None)

    # Assert
    assert key == "N:some company"


def test_node_key_handles_none_name():
    # Arrange / Act
    key = _node_key(None, None)

    # Assert
    assert key == "N:"


def test_to_company_node_uses_default_source_when_entry_source_blank():
    # Arrange
    entry = LlmCompanyEntry(name="X", source=None)

    # Act
    node = _to_company_node(entry, default_source="industry")

    # Assert
    assert node.source == "industry"


def test_to_company_node_keeps_explicit_source():
    # Arrange
    entry = LlmCompanyEntry(name="X", source="8-K")

    # Act
    node = _to_company_node(entry, default_source="industry")

    # Assert
    assert node.source == "8-K"


# ----------------------------------------------------------------- happy path
def _filing_result_with_one_supplier() -> LlmFilingResult:
    return LlmFilingResult(
        segments=["Cloud"],
        concentration_note="No customer >10%",
        suppliers=[LlmCompanyEntry(name="TSMC", ticker="TSM", relationship="Foundry")],
        customers=[],
        competitors=[],
        summary="hyperscaler",
    )


def test_get_supply_chain_happy_path_with_enrichment():
    # Arrange
    filing = _filing_result_with_one_supplier()
    industry = LlmIndustryResult(
        suppliers=[LlmCompanyEntry(name="NVIDIA", ticker="NVDA", confidence=0.9)],
    )
    verifier = LlmVerifierResult(
        suppliers=[LlmCompanyEntry(name="NVIDIA", ticker="NVDA", confidence=0.9)],
        audit_summary="ok",
    )
    sec = _FakeSec()
    llm = _FakeLlm(filing=filing, industry=industry, verifier=verifier)

    # Act
    graph = get_supply_chain("msft", sec_client=sec, llm=llm)  # type: ignore[arg-type]

    # Assert
    assert isinstance(graph, SupplyChainGraph)
    assert graph.ticker == "MSFT"
    assert graph.company_name == "Microsoft Corp"
    assert graph.enrichment_used == ["filing", "verified", "industry"]
    assert [s.ticker for s in graph.suppliers] == ["TSM", "NVDA"]
    assert graph.suppliers[0].source == "10-K"
    assert graph.suppliers[1].source == "industry"
    assert graph.segments == ["Cloud"]
    assert graph.concentration_note == "No customer >10%"


def test_get_supply_chain_skips_enrichment_when_disabled():
    # Arrange
    sec = _FakeSec()
    llm = _FakeLlm(filing=_filing_result_with_one_supplier())

    # Act
    graph = get_supply_chain("MSFT", enrich_industry=False, sec_client=sec, llm=llm)  # type: ignore[arg-type]

    # Assert
    assert graph.enrichment_used == ["filing"]
    assert llm.calls == {"filing": 1, "industry": 0, "verifier": 0}
    assert len(graph.suppliers) == 1


# ------------------------------------------------------------ resolution tests
def test_get_supply_chain_unknown_ticker_raises_value_error():
    # Arrange
    sec = _FakeSec(cik=None)
    llm = _FakeLlm(filing=LlmFilingResult())

    # Act / Assert
    with pytest.raises(ValueError, match="not found in SEC database"):
        get_supply_chain("NOPE", sec_client=sec, llm=llm)  # type: ignore[arg-type]


def test_get_supply_chain_no_10k_raises_value_error():
    # Arrange
    sec = _FakeSec(latest_10k=None)
    llm = _FakeLlm(filing=LlmFilingResult())

    # Act / Assert
    with pytest.raises(ValueError, match="No 10-K filing found"):
        get_supply_chain("MSFT", sec_client=sec, llm=llm)  # type: ignore[arg-type]


# ----------------------------------------------------------- dedup / merge tests
def test_get_supply_chain_industry_pass_does_not_duplicate_filing_entries():
    # Arrange — industry pass proposes the same TSMC that filing already has
    filing = _filing_result_with_one_supplier()
    industry = LlmIndustryResult(
        suppliers=[
            LlmCompanyEntry(name="Taiwan Semi", ticker="TSM"),  # dup by ticker
            LlmCompanyEntry(name="NVIDIA", ticker="NVDA"),
        ],
    )
    verifier = LlmVerifierResult(
        suppliers=list(industry.suppliers),
    )
    sec = _FakeSec()
    llm = _FakeLlm(filing=filing, industry=industry, verifier=verifier)

    # Act
    graph = get_supply_chain("MSFT", sec_client=sec, llm=llm)  # type: ignore[arg-type]

    # Assert — only one TSM (the filing one), plus NVDA
    tickers = [s.ticker for s in graph.suppliers]
    assert tickers == ["TSM", "NVDA"]
    assert graph.suppliers[0].name == "TSMC"  # original filing name preserved


# ------------------------------------------------------ failure-mode tests
def test_get_supply_chain_industry_pass_failure_falls_back_to_filing_only():
    # Arrange
    sec = _FakeSec()
    llm = _FakeLlm(
        filing=_filing_result_with_one_supplier(),
        industry_raises=RuntimeError("simulated industry failure"),
    )

    # Act
    graph = get_supply_chain("MSFT", sec_client=sec, llm=llm)  # type: ignore[arg-type]

    # Assert
    assert graph.enrichment_used == ["filing"]
    assert llm.calls["industry"] == 1
    assert llm.calls["verifier"] == 0
    assert len(graph.suppliers) == 1


def test_get_supply_chain_verifier_failure_uses_raw_industry_pool():
    # Arrange
    industry = LlmIndustryResult(
        suppliers=[LlmCompanyEntry(name="NVIDIA", ticker="NVDA")],
    )
    sec = _FakeSec()
    llm = _FakeLlm(
        filing=_filing_result_with_one_supplier(),
        industry=industry,
        verifier_raises=RuntimeError("simulated verifier failure"),
    )

    # Act
    graph = get_supply_chain("MSFT", sec_client=sec, llm=llm)  # type: ignore[arg-type]

    # Assert
    assert graph.enrichment_used == ["filing", "industry"]  # no "verified"
    tickers = [s.ticker for s in graph.suppliers]
    assert "NVDA" in tickers


# ------------------------------------------------- 8-K corpus / failure plumbing
def test_get_supply_chain_eight_k_fields_populated():
    # Arrange
    eight_ks = [
        {
            "accession": "8K-1",
            "filing_date": "2024-08-10",
            "primary_doc_url": "https://example.com/a.htm",
            "form": "8-K",
        },
        {
            "accession": "8K-2",
            "filing_date": "2024-09-15",
            "primary_doc_url": "https://example.com/b.htm",
            "form": "8-K",
        },
    ]
    sec = _FakeSec(eight_ks=eight_ks)
    llm = _FakeLlm(filing=_filing_result_with_one_supplier())

    # Act
    graph = get_supply_chain("MSFT", sec_client=sec, llm=llm)  # type: ignore[arg-type]

    # Assert
    assert graph.eight_k_count == 2
    assert graph.eight_k_dates == ["2024-08-10", "2024-09-15"]
    assert graph.eight_k_failed_count == 0


def test_get_supply_chain_surfaces_eight_k_failed_count():
    # Arrange
    eight_ks = [
        {
            "accession": "8K-1",
            "filing_date": "2024-08-10",
            "primary_doc_url": "https://example.com/a.htm",
            "form": "8-K",
        },
        {
            "accession": "8K-2",
            "filing_date": "2024-09-15",
            "primary_doc_url": "https://example.com/fail.htm",
            "form": "8-K",
        },
    ]
    sec = _FakeSec(eight_ks=eight_ks, eight_k_fail_urls={"https://example.com/fail.htm"})
    llm = _FakeLlm(filing=_filing_result_with_one_supplier())

    # Act
    graph = get_supply_chain("MSFT", sec_client=sec, llm=llm)  # type: ignore[arg-type]

    # Assert
    assert graph.eight_k_count == 2  # metadata count, not text fetch count
    assert graph.eight_k_failed_count == 1


# ------------------------------------------------------------- segments filter
def test_get_supply_chain_drops_blank_and_non_string_segments():
    # Arrange — segments list with whitespace-only and empty strings
    filing = LlmFilingResult(
        segments=["Cloud", "", "  ", "Productivity"],
        suppliers=[],
        customers=[],
        competitors=[],
    )
    sec = _FakeSec()
    llm = _FakeLlm(filing=filing)

    # Act
    graph = get_supply_chain("MSFT", enrich_industry=False, sec_client=sec, llm=llm)  # type: ignore[arg-type]

    # Assert
    assert graph.segments == ["Cloud", "Productivity"]
