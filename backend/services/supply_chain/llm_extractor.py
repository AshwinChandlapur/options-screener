"""Azure OpenAI adapter for supply-chain extraction passes.

Wraps the three LLM calls (filing-pass, industry-pass, verifier-pass)
behind one class with a single ``AzureOpenAI`` client. Each method
validates the JSON response against a Pydantic model
(:class:`~services.supply_chain.types.LlmFilingResult` and friends).

The legacy module-level functions in ``services.supply_chain_service``
(``_call_llm`` / ``_call_industry_llm`` / ``_call_verifier_llm``)
delegate here and dump results back to ``dict`` for the existing
orchestrator. The new ``pipeline.py`` (Phase 1d) consumes the Pydantic
results directly.
"""
from __future__ import annotations

import json
import os
from typing import Optional

from openai import AzureOpenAI
from pydantic import ValidationError

from .types import LlmFilingResult, LlmIndustryResult, LlmVerifierResult

# ============================================================ system prompts
# Prompts are part of the LLM contract; the Pydantic models in
# :mod:`.types` mirror the JSON schema documented here. Keep them in
# lockstep.

_FILING_SYSTEM_PROMPT = """You are a financial analyst extracting supply chain relationships from SEC filings.

You will receive the focal company's latest 10-K plus any recent 8-K filings (material event disclosures).
Merge information from BOTH sources into a single consolidated graph.
- Use 10-K as the foundation (suppliers, customers, competitors)
- Use 8-K filings to add NEW relationships announced since the 10-K (new contracts, customer wins, supplier changes)
- If 8-K info contradicts the 10-K, prefer the more recent 8-K
- Do NOT duplicate the same relationship across sources

Return a JSON object with this exact schema:
{
  "segments": ["<reportable business segment names from the filing, e.g. 'Intelligent Cloud', 'Productivity & Business Processes'>"],
  "concentration_note": "<verbatim or near-verbatim sentence describing customer/supplier concentration, e.g. 'No single customer accounted for more than 10% of net sales in fiscal 2024.' Empty string if not disclosed.>",
  "suppliers": [
    {
      "name": "<company name>",
      "ticker": "<stock ticker if publicly traded, else null>",
      "relationship": "<what they supply, e.g. 'Foundry/chip fab', 'Memory chips'>",
      "cost_pct": <% of focal company COGS/spending if disclosed, else null>,
      "segment": "<which segment this supplier serves, or null>",
      "source": "<'10-K' or '8-K'>",
      "notes": "<contract terms / 8-K filing date if applicable / qualitative info>"
    }
  ],
  "customers": [
    {
      "name": "...",
      "ticker": "...",
      "relationship": "<what they buy>",
      "revenue_pct": <% of focal company revenue if disclosed, else null>,
      "segment": "<which segment this customer buys from, or null>",
      "source": "<'10-K' or '8-K'>",
      "notes": "..."
    }
  ],
  "competitors": [
    {
      "name": "...",
      "ticker": "...",
      "relationship": "<segment/market they compete in>",
      "segment": "<which segment of focal company they compete with, or null>",
      "source": "<'10-K' or '8-K'>",
      "notes": "..."
    }
  ],
  "summary": "<2-3 sentences on the focal company's supply chain, mentioning notable shifts from recent 8-Ks>"
}

Rules:
- Only include companies clearly named in the filings
- Use the company's common name (e.g. "Taiwan Semiconductor Manufacturing" not "TSMC Holdings")
- If a ticker isn't standard (e.g. foreign-listed), still include it (e.g. "TSM", "005930.KS")
- Prefer publicly traded companies but include major private suppliers (e.g. "Foxconn")
- If a percentage is mentioned, extract it as a number (e.g. "represents 22% of revenue" -> 22.0)
- For `segment`: only fill if the filing explicitly attributes the relationship to a reportable segment; otherwise null
- For `source`: "10-K" unless the relationship is announced/disclosed only in an 8-K (then "8-K" with the date in `notes`)
- For `concentration_note`: capture customer-concentration disclosures (e.g. "top 5 customers = 41% of net sales", "no customer >10%"). This explains gaps in the customer list.
- Limit to top 15 suppliers, 15 customers, 10 competitors
- Return ONLY valid JSON, no markdown fences"""


_INDUSTRY_SYSTEM_PROMPT = """You are a financial analyst augmenting a supply-chain graph for a public company.

You will receive:
- The focal company name + ticker
- The reportable business segments
- The list of suppliers/customers/competitors already extracted from the company's SEC filings

Your task: ADD additional supplier/customer/competitor relationships that are PUBLICLY KNOWN but NOT mentioned in the filing-derived list. Use only widely reported, credible relationships from your training knowledge:
- Major announced partnerships, multi-year contracts covered in trade press
- Well-known customer relationships discussed in earnings calls or industry analysis
- Standard sector-typical suppliers (e.g. for a hyperscaler: NVIDIA for GPUs, Cisco/Arista for networking, Vertiv for power)
- Established competitors widely recognized in the industry

Return a JSON object with this exact schema:
{
  "suppliers": [{"name": "...", "ticker": "...", "relationship": "...", "cost_pct": null, "segment": "<segment served, or null>", "confidence": <0.0-1.0>, "notes": "<basis: e.g. 'Widely reported partnership announced 2023', 'Standard hyperscaler GPU supplier'>"}],
  "customers": [{"name": "...", "ticker": "...", "relationship": "...", "revenue_pct": null, "segment": "...", "confidence": <0.0-1.0>, "notes": "..."}],
  "competitors": [{"name": "...", "ticker": "...", "relationship": "...", "segment": "...", "confidence": <0.0-1.0>, "notes": "..."}]
}

CRITICAL rules:
- DO NOT duplicate any relationship that is already in the filing-derived list (match on name OR ticker, case-insensitive)
- DO NOT fabricate. If you are unsure or cannot recall a credible basis, OMIT the entry. Empty arrays are fine.
- `confidence`: 0.9+ for textbook/uncontested relationships (e.g. TSMC supplies NVIDIA), 0.7-0.9 for widely reported, 0.5-0.7 for likely but not certain. Below 0.5 = omit.
- Hard caps: at most 15 suppliers, 15 customers, 5 competitors
- For diversified companies, distribute additions across segments
- `notes` MUST cite the basis (e.g. 'Reported partnership 2023', 'Standard sector supplier', 'Discussed in Q3 2024 earnings call')
- Return ONLY valid JSON, no markdown fences"""


_VERIFIER_SYSTEM_PROMPT = """You are an audit reviewer for a supply-chain analyst.

You will receive a list of CANDIDATE supplier/customer/competitor relationships that another analyst proposed for a focal public company, based on industry knowledge (NOT from the company's SEC filings). Your job is to AUDIT this list and return a filtered, calibrated version.

For EACH candidate, evaluate:
1. Is the relationship publicly known and credibly reported (press releases, earnings calls, mainstream tech/business press, partnership announcements)?
2. Is the proposed `confidence` score appropriate? Apply this calibration:
   - 0.9+ : Textbook / uncontested / officially announced multi-year relationship
   - 0.7-0.89 : Widely reported, multiple credible sources
   - 0.5-0.69 : Likely / sector-typical but not specifically confirmed
   - <0.5 : Unsupported / speculation
3. Is the basis citation in `notes` specific enough? (Vague notes like "industry standard" are weak; "Announced 2023 partnership" or "Disclosed in Q3 2024 earnings call" are strong.)

ACTIONS to take:
- DROP any candidate where you cannot recall a credible public basis. Be strict — when in doubt, DROP.
- DROP any candidate whose final confidence falls below 0.6.
- ADJUST `confidence` downward if the original was overstated.
- IMPROVE `notes` to cite a specific basis where possible (e.g., year of announcement, type of source). Never invent a citation; if you can only say "widely reported", that's fine.
- DO NOT add new candidates. DO NOT change `name`, `ticker`, `relationship`, `revenue_pct`, `cost_pct`, or `segment`.

Return JSON in this exact shape (same as input minus dropped entries):
{
  "suppliers": [ ... ],
  "customers": [ ... ],
  "competitors": [ ... ],
  "audit_summary": "<1 sentence: how many dropped, common reason>"
}

Return ONLY valid JSON, no markdown fences."""


# ================================================================== extractor
class LlmSupplyChainExtractor:
    """Three-pass LLM extractor over a single AzureOpenAI client."""

    def __init__(
        self,
        *,
        api_key: Optional[str] = None,
        endpoint: Optional[str] = None,
        deployment: Optional[str] = None,
        api_version: Optional[str] = None,
        client: Optional[AzureOpenAI] = None,
    ) -> None:
        self._api_key = api_key if api_key is not None else os.getenv("AZURE_OPENAI_KEY", "")
        self._endpoint = endpoint if endpoint is not None else os.getenv("AZURE_OPENAI_ENDPOINT", "")
        self._deployment = deployment or os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4.1")
        self._api_version = api_version or os.getenv(
            "AZURE_OPENAI_API_VERSION", "2025-01-01-preview"
        )
        self._client_override = client

    # --------------------------------------------------------------- internals
    def _client(self) -> AzureOpenAI:
        if self._client_override is not None:
            return self._client_override
        if not self._api_key or not self._endpoint:
            raise RuntimeError(
                "Azure OpenAI not configured. Set AZURE_OPENAI_KEY and "
                "AZURE_OPENAI_ENDPOINT in backend/.env"
            )
        # Construct a fresh client per call to avoid sharing state across
        # background threads in Phase 2's parallel 8-K worker pool.
        return AzureOpenAI(
            api_key=self._api_key,
            azure_endpoint=self._endpoint,
            api_version=self._api_version,
        )

    def _chat(self, *, system: str, user: str, temperature: float) -> dict:
        resp = self._client().chat.completions.create(
            model=self._deployment,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=temperature,
            response_format={"type": "json_object"},
        )
        raw = resp.choices[0].message.content or "{}"
        return json.loads(raw)

    # ------------------------------------------------------------- pass 1: 10-K
    def extract_filing(
        self,
        *,
        ticker: str,
        company_name: str,
        filing_text: str,
        recent_8k_text: str = "",
    ) -> LlmFilingResult:
        user_parts = [
            f"Focal company: {company_name} ({ticker})",
            "",
            "=== 10-K excerpt (Item 1 Business + Risk Factors) ===",
            filing_text,
        ]
        if recent_8k_text:
            user_parts += [
                "",
                "=== Recent 8-K filings (material events since 10-K) ===",
                recent_8k_text,
            ]
        raw = self._chat(
            system=_FILING_SYSTEM_PROMPT,
            user="\n".join(user_parts),
            temperature=0.1,
        )
        try:
            return LlmFilingResult.model_validate(raw)
        except ValidationError as e:
            raise RuntimeError(f"LLM filing-pass response failed validation: {e}") from e

    # -------------------------------------------------------- pass 2: industry
    def enrich_industry(
        self,
        *,
        ticker: str,
        company_name: str,
        segments: list[str],
        existing: LlmFilingResult,
    ) -> LlmIndustryResult:
        def _compact(items: list) -> list[dict]:
            return [{"name": x.name, "ticker": x.ticker} for x in items]

        payload = {
            "focal_company": company_name,
            "focal_ticker": ticker,
            "segments": segments,
            "existing_suppliers": _compact(existing.suppliers),
            "existing_customers": _compact(existing.customers),
            "existing_competitors": _compact(existing.competitors),
        }
        raw = self._chat(
            system=_INDUSTRY_SYSTEM_PROMPT,
            user=json.dumps(payload, indent=2),
            temperature=0.2,
        )
        try:
            return LlmIndustryResult.model_validate(raw)
        except ValidationError as e:
            raise RuntimeError(f"LLM industry-pass response failed validation: {e}") from e

    # --------------------------------------------------------- pass 3: verifier
    def verify(
        self,
        *,
        ticker: str,
        company_name: str,
        candidates: LlmIndustryResult,
    ) -> LlmVerifierResult:
        # Skip the API call when there's nothing to audit. Returning a
        # well-typed result with the original candidates keeps the
        # pipeline call site uniform (no None branching).
        total = (
            len(candidates.suppliers)
            + len(candidates.customers)
            + len(candidates.competitors)
        )
        if total == 0:
            return LlmVerifierResult(
                suppliers=list(candidates.suppliers),
                customers=list(candidates.customers),
                competitors=list(candidates.competitors),
                audit_summary="",
            )

        payload = {
            "focal_company": company_name,
            "focal_ticker": ticker,
            "candidates": {
                "suppliers": [s.model_dump(exclude_none=True) for s in candidates.suppliers],
                "customers": [c.model_dump(exclude_none=True) for c in candidates.customers],
                "competitors": [c.model_dump(exclude_none=True) for c in candidates.competitors],
            },
        }
        raw = self._chat(
            system=_VERIFIER_SYSTEM_PROMPT,
            user=json.dumps(payload, indent=2),
            temperature=0.0,  # audit step: deterministic
        )
        try:
            return LlmVerifierResult.model_validate(raw)
        except ValidationError as e:
            raise RuntimeError(f"LLM verifier-pass response failed validation: {e}") from e


# ----------------------------------------------------------------- singleton
_DEFAULT_EXTRACTOR: Optional[LlmSupplyChainExtractor] = None


def get_default_extractor() -> LlmSupplyChainExtractor:
    """Return a process-wide ``LlmSupplyChainExtractor`` for legacy callers."""
    global _DEFAULT_EXTRACTOR
    if _DEFAULT_EXTRACTOR is None:
        _DEFAULT_EXTRACTOR = LlmSupplyChainExtractor()
    return _DEFAULT_EXTRACTOR
