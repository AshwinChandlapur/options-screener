"""
Supply Chain extraction service.

Pipeline:
1. Resolve ticker -> SEC CIK
2. Fetch latest 10-K filing index
3. Download the primary document, extract text
4. Send relevant sections to Azure OpenAI (gpt-4.1) for structured extraction
"""
from __future__ import annotations

import json  # noqa: F401  # retained for back-compat (re-exported by some callers)
import logging
import os
from typing import Optional

from services.supply_chain.llm_extractor import (
    LlmSupplyChainExtractor,
    get_default_extractor,
)
from services.supply_chain.sec_client import SecDataClient, get_default_client
from services.supply_chain.types import (
    CompanyNode,
    LlmFilingResult,
    LlmIndustryResult,
    LlmVerifierResult,
    SourceTag,
    SupplyChainGraph,
)

__all__ = [
    "CompanyNode",
    "LlmFilingResult",
    "LlmIndustryResult",
    "LlmSupplyChainExtractor",
    "LlmVerifierResult",
    "SecDataClient",
    "SourceTag",
    "SupplyChainGraph",
    "get_supply_chain",
    "resolve_cik",
]

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------- Config -----
# Retained for back-compat: a handful of tests / callers still read these
# constants. SEC HTTP access is now owned by ``SecDataClient``.
SEC_USER_AGENT = os.getenv("SEC_USER_AGENT", "Options Screener app@example.com")
SEC_HEADERS = {"User-Agent": SEC_USER_AGENT, "Accept-Encoding": "gzip, deflate"}

AZURE_OPENAI_KEY = os.getenv("AZURE_OPENAI_KEY", "")
AZURE_OPENAI_ENDPOINT = os.getenv("AZURE_OPENAI_ENDPOINT", "")
AZURE_OPENAI_DEPLOYMENT = os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4.1")
AZURE_OPENAI_API_VERSION = os.getenv("AZURE_OPENAI_API_VERSION", "2025-01-01-preview")


# ------------------------------------------------------------ SEC delegates --
# Thin wrappers around the singleton ``SecDataClient`` so the public
# module-level surface (and the Phase 0 monkeypatch points in
# ``backend/tests/fixtures/supply_chain/_mocks.py``) stays unchanged.
def resolve_cik(ticker: str) -> Optional[str]:
    return get_default_client().resolve_cik(ticker)


def _fetch_filings_index(cik: str) -> tuple[str, list[dict]]:
    return get_default_client().get_filings_index(cik)


def _fetch_latest_10k(cik: str) -> Optional[dict]:
    return get_default_client().get_latest_10k(cik)


def _fetch_recent_8ks(cik: str, since_date: str, max_count: int = 8) -> list[dict]:
    return get_default_client().get_recent_8ks(cik, since_date, max_count=max_count)


def _fetch_filing_text(url: str) -> str:
    return get_default_client().fetch_filing_text(url)


def _fetch_8k_text(url: str, max_chars: int = 30_000) -> str:
    return get_default_client().fetch_8k_text(url, max_chars=max_chars)


# -------------------------------------------------- LLM delegates -----
# The prompts and structured-output validation live in
# ``services.supply_chain.llm_extractor``. The functions below preserve
# the legacy module-level surface (and the Phase 0 monkeypatch points)
# while routing all real API traffic through ``LlmSupplyChainExtractor``.
def _call_llm(
    filing_text: str, ticker: str, company_name: str, recent_8k_text: str = ""
) -> dict:
    result = get_default_extractor().extract_filing(
        ticker=ticker,
        company_name=company_name,
        filing_text=filing_text,
        recent_8k_text=recent_8k_text,
    )
    return result.model_dump(exclude_none=False)


def _call_industry_llm(
    ticker: str,
    company_name: str,
    segments: list[str],
    existing: dict,
) -> dict:
    """Second-pass call: ask the LLM to add publicly-known relationships not in the filing."""
    existing_model = LlmFilingResult.model_validate(existing)
    result = get_default_extractor().enrich_industry(
        ticker=ticker,
        company_name=company_name,
        segments=segments,
        existing=existing_model,
    )
    return result.model_dump(exclude_none=False)


def _call_verifier_llm(
    ticker: str,
    company_name: str,
    candidates: dict,
) -> dict:
    """Audit the industry-pass output: drop unsupportable entries, calibrate confidence."""
    candidates_model = LlmIndustryResult.model_validate(candidates)
    result = get_default_extractor().verify(
        ticker=ticker,
        company_name=company_name,
        candidates=candidates_model,
    )
    return result.model_dump(exclude_none=False)


# ----------------------------------------------------- Merge / dedupe utils ---
def _node_key(name: str | None, ticker: str | None) -> str:
    if ticker:
        return f"T:{ticker.upper().strip()}"
    return f"N:{(name or '').lower().strip()}"


def _coerce_company_node(raw: dict, default_source: SourceTag) -> CompanyNode:
    """Build a CompanyNode from an LLM dict, accepting only known fields."""
    allowed = set(CompanyNode.__dataclass_fields__.keys())
    clean: dict = {k: v for k, v in raw.items() if k in allowed}
    clean.setdefault("source", default_source)
    return CompanyNode(**clean)


def _merge_industry(
    base: list[CompanyNode], additions: list[dict], cap: int
) -> list[CompanyNode]:
    """Append non-duplicate industry-pass entries to the filing-grounded list."""
    seen = {_node_key(n.name, n.ticker) for n in base}
    out = list(base)
    for raw in additions:
        key = _node_key(raw.get("name"), raw.get("ticker"))
        if key in seen:
            continue
        node = _coerce_company_node(raw, default_source="industry")
        out.append(node)
        seen.add(key)
        if len(out) >= cap + len(base):
            break
    return out


# --------------------------------------------------------------- Public ----
def get_supply_chain(
    ticker: str,
    force_refresh: bool = False,
    enrich_industry: bool = True,
) -> SupplyChainGraph:
    ticker = ticker.upper()
    cik = resolve_cik(ticker)
    if not cik:
        raise ValueError(f"Ticker {ticker} not found in SEC database")

    filing = _fetch_latest_10k(cik)
    if not filing:
        raise ValueError(f"No 10-K filing found for {ticker}")

    accession = filing["accession"]
    company_name = filing["company_name"]

    # Fetch + extract
    filing_text = _fetch_filing_text(filing["primary_doc_url"])
    logger.info("10-K text extracted: %d chars for %s", len(filing_text), ticker)

    # Fetch recent 8-Ks filed since the 10-K to capture material events
    eight_ks = _fetch_recent_8ks(cik, since_date=filing["filing_date"], max_count=8)
    eight_k_text_parts: list[str] = []
    for ek in eight_ks:
        try:
            t = _fetch_8k_text(ek["primary_doc_url"])
            eight_k_text_parts.append(f"--- 8-K filed {ek['filing_date']} ---\n{t}")
        except Exception as e:
            logger.warning("Failed to fetch 8-K %s for %s: %s", ek["accession"], ticker, e)
    eight_k_text = "\n\n".join(eight_k_text_parts)
    logger.info("Loaded %d 8-Ks (%d chars total) for %s", len(eight_ks), len(eight_k_text), ticker)

    extracted = _call_llm(filing_text, ticker, company_name, recent_8k_text=eight_k_text)

    suppliers = [
        _coerce_company_node(c, default_source=c.get("source") or "10-K")
        for c in extracted.get("suppliers", [])
    ]
    customers = [
        _coerce_company_node(c, default_source=c.get("source") or "10-K")
        for c in extracted.get("customers", [])
    ]
    competitors = [
        _coerce_company_node(c, default_source=c.get("source") or "10-K")
        for c in extracted.get("competitors", [])
    ]
    segments = [s for s in extracted.get("segments", []) if isinstance(s, str) and s.strip()]
    enrichment_used = ["filing"]

    # Phase 3: industry-knowledge enrichment pass
    if enrich_industry:
        try:
            industry = _call_industry_llm(
                ticker, company_name, segments, extracted
            )
            raw_counts = (
                len(industry.get("suppliers", [])),
                len(industry.get("customers", [])),
                len(industry.get("competitors", [])),
            )

            # Verifier pass: audit the industry-pass output, drop unsupportable
            # entries and calibrate confidence scores.
            try:
                verified = _call_verifier_llm(ticker, company_name, industry)
                ver_counts = (
                    len(verified.get("suppliers", [])),
                    len(verified.get("customers", [])),
                    len(verified.get("competitors", [])),
                )
                logger.info(
                    "Verifier pass for %s: suppliers %d->%d, customers %d->%d, competitors %d->%d. %s",
                    ticker,
                    raw_counts[0], ver_counts[0],
                    raw_counts[1], ver_counts[1],
                    raw_counts[2], ver_counts[2],
                    verified.get("audit_summary", ""),
                )
                industry = verified
                enrichment_used.append("verified")
            except Exception as e:
                logger.warning("Verifier pass failed for %s (using raw industry output): %s", ticker, e)

            suppliers = _merge_industry(suppliers, industry.get("suppliers", []), cap=15)
            customers = _merge_industry(customers, industry.get("customers", []), cap=15)
            competitors = _merge_industry(competitors, industry.get("competitors", []), cap=5)
            enrichment_used.append("industry")
            logger.info(
                "Industry pass added %d suppliers, %d customers, %d competitors for %s",
                len(industry.get("suppliers", [])),
                len(industry.get("customers", [])),
                len(industry.get("competitors", [])),
                ticker,
            )
        except Exception as e:
            logger.warning("Industry enrichment pass failed for %s: %s", ticker, e)

    graph = SupplyChainGraph(
        ticker=ticker,
        company_name=company_name,
        filing_date=filing["filing_date"],
        accession=accession,
        suppliers=suppliers,
        customers=customers,
        competitors=competitors,
        summary=extracted.get("summary", ""),
        cached=False,
        eight_k_count=len(eight_ks),
        eight_k_dates=[ek["filing_date"] for ek in eight_ks],
        segments=segments,
        concentration_note=extracted.get("concentration_note", "") or "",
        enrichment_used=enrichment_used,
    )

    return graph
