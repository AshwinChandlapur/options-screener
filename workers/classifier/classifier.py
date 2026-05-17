"""GPT-4o-mini conviction classifier (§3 of NARRATIVE_METHODOLOGY.md).

Classifies each signal along four independent axes (per ADR-0020):
  - direction:  bull | bear
  - substance:  researched | emotional
  - driver:     earnings | product | macro | flows | valuation | other
  - position:   entering | holding | exiting | unstated
Plus a single ``confidence`` scalar.

ADR-0021 retired the back-compat legacy 10-state ``conviction_state``; the
classifier now writes only the axis fields.

Structured output via OpenAI JSON schema response_format — no parsing heuristics.

Prompt injection defence: instructions live in the `system` message; the
untrusted Reddit post body is sent as a separate `user` message. The model's
alignment ensures system instructions take precedence over user content.

Phase 5 addition: EmbeddingGenerator batches rationale text through the
Azure OpenAI embeddings deployment (text-embedding-ada-002, 1 536-dim) via
the `openai` SDK. Called alongside classification in main.py; errors are
soft-failed so conviction state is never blocked by an embedding failure.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Sequence

from openai import AzureOpenAI

logger = logging.getLogger(__name__)

# --- Axis enums (ADR-0020; sole conviction vocabulary per ADR-0021) -------
DIRECTIONS: list[str] = ["bull", "bear"]
SUBSTANCES: list[str] = ["researched", "emotional"]
DRIVERS:    list[str] = ["earnings", "product", "macro", "flows", "valuation", "other"]
POSITIONS:  list[str] = ["entering", "holding", "exiting", "unstated"]


@dataclass(frozen=True)
class ConvictionAxes:
    """Structured conviction output along 4 independent axes."""
    direction: str       # bull | bear
    substance: str       # researched | emotional
    driver: str          # earnings | product | macro | flows | valuation | other
    position: str        # entering | holding | exiting | unstated
    confidence: float    # 0.0-1.0


# Default system prompt — overridden by Key Vault secret `conviction-prompt-v1`.
# Template variables: {ticker}, {sentiment} only.
# The Reddit post body is sent as a SEPARATE user message (never interpolated here)
# to prevent prompt injection from adversarial post content.
DEFAULT_SYSTEM_PROMPT = """\
Classify the Reddit post (provided by the user) about ticker {ticker} along
four independent axes. Extractor sentiment hint: {sentiment} — treat as a hint
only; the post text is authoritative.

Axes (return ALL four):

1. direction:
   - "bull"  = author expects the stock to rise / is long
   - "bear"  = author expects the stock to fall / is short

2. substance:
   - "researched" = author cites ≥1 specific number, named filing/source,
                    product detail, or financial metric
   - "emotional"  = hype, FUD, memes, slogans, or vague conviction without
                    cited evidence

3. driver — the primary thing the thesis is ABOUT:
   - "earnings"   = tied to a specific past or upcoming earnings event
   - "product"    = a specific product, launch, technology, or roadmap item
   - "macro"      = industry-wide tailwind, sector rotation, rates/inflation
   - "flows"      = analyst PT/upgrade/downgrade, 13F flows, options flow
   - "valuation"  = multiple, DCF, comparables, intrinsic-value arguments
   - "other"      = none of the above clearly dominates

4. position — what the AUTHOR is doing or planning:
   - "entering"   = opening a position, adding, "loaded up", "started a position"
   - "holding"    = maintaining an existing position, "diamond hands", reiterating
   - "exiting"    = profit-taking, covering, stopping out, "sold today"
   - "unstated"   = author shares opinion but does not declare an action

If the post is off-topic, spam, or contains no opinion on {ticker}, default to
direction="bull", substance="emotional", driver="other", position="unstated",
confidence ≤ 0.2.

Respond with JSON only.\
"""

_RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name": "conviction_axes",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "direction":  {"type": "string", "enum": DIRECTIONS},
                "substance":  {"type": "string", "enum": SUBSTANCES},
                "driver":     {"type": "string", "enum": DRIVERS},
                "position":   {"type": "string", "enum": POSITIONS},
                "confidence": {"type": "number"},
            },
            "required": ["direction", "substance", "driver", "position", "confidence"],
            "additionalProperties": False,
        },
    },
}


class ConvictionClassifier:
    def __init__(
        self,
        api_key: str,
        endpoint: str,
        deployment: str,
        prompt_template: str,
    ) -> None:
        self._client = AzureOpenAI(
            api_key=api_key,
            azure_endpoint=endpoint,
            api_version="2024-08-01-preview",
        )
        self._deployment = deployment
        self._prompt_template = prompt_template

    def classify(
        self,
        ticker: str,
        sentiment: str,
        rationale: str,
    ) -> ConvictionAxes:
        """Return a ConvictionAxes for one signal.

        Raises on OpenAI API error — caller decides retry/skip policy.

        Prompt injection defence: instructions are in the system message;
        the untrusted post body is the user message only.
        """
        system_msg = self._prompt_template.format(
            ticker=ticker,
            sentiment=sentiment,
        )
        response = self._client.chat.completions.create(
            model=self._deployment,
            messages=[
                {"role": "system", "content": system_msg},
                {"role": "user",   "content": rationale or "(no content)"},
            ],
            response_format=_RESPONSE_FORMAT,  # type: ignore[arg-type]
            max_tokens=96,
            temperature=0.0,
        )
        content = response.choices[0].message.content or "{}"
        result = json.loads(content)
        # Strict JSON schema guarantees enum membership; defaults below only
        # protect against a future schema drift or a non-strict KV prompt override.
        direction = result.get("direction")  if result.get("direction")  in DIRECTIONS else "bull"
        substance = result.get("substance")  if result.get("substance")  in SUBSTANCES else "emotional"
        driver    = result.get("driver")     if result.get("driver")     in DRIVERS    else "other"
        position  = result.get("position")   if result.get("position")   in POSITIONS  else "unstated"
        confidence = float(result.get("confidence", 0.5))
        confidence = max(0.0, min(1.0, confidence))  # clamp to [0, 1]
        return ConvictionAxes(
            direction=direction,
            substance=substance,
            driver=driver,
            position=position,
            confidence=confidence,
        )


# ---------------------------------------------------------------------------
# Phase 5 — embedding generator
# ---------------------------------------------------------------------------

# Default embedding model name — overridden by KV secret embed-deployment.
# text-embedding-ada-002 is 1536-dim; text-embedding-3-large defaults to 3072-dim.
_EMBEDDING_MODEL = "text-embedding-ada-002"
_EMBEDDING_DIMS = 1536
# Azure OpenAI embeddings API hard limit per request.
_EMBED_BATCH_LIMIT = 100
# Stable embeddings API version (GA). Distinct from the chat api_version,
# which tracks newer previews for structured outputs.
_EMBED_API_VERSION = "2024-02-01"


class EmbeddingGenerator:
    """Batch embedding generator for rationale text via Azure OpenAI.

    Returns a list of float vectors (1536-dim for ada-002), one per input text.
    Inputs that exceed the token limit are truncated server-side; no client-
    side truncation needed.

    Error handling: the caller (main.py) wraps calls in a try/except so that
    embedding failures never block conviction-state writes.
    """

    def __init__(self, api_key: str, endpoint: str, deployment: str) -> None:
        self._client = AzureOpenAI(
            api_key=api_key,
            azure_endpoint=endpoint,
            api_version=_EMBED_API_VERSION,
        )
        self._deployment = deployment

    def embed_batch(self, texts: Sequence[str]) -> list[list[float]]:
        """Return embeddings for each text. Raises on API error.

        Splits into sub-batches of at most ``_EMBED_BATCH_LIMIT`` items.
        Empty strings are replaced with a single space to avoid API rejection.
        """
        # Azure OpenAI rejects empty strings — substitute a space placeholder.
        safe_texts = [t if t.strip() else " " for t in texts]

        results: list[list[float]] = []
        for i in range(0, len(safe_texts), _EMBED_BATCH_LIMIT):
            chunk = safe_texts[i : i + _EMBED_BATCH_LIMIT]
            resp = self._client.embeddings.create(
                model=self._deployment,
                input=list(chunk),
            )
            # SDK returns items in input order, but sort defensively by index.
            chunk_vecs = [
                item.embedding
                for item in sorted(resp.data, key=lambda x: x.index)
            ]
            results.extend(chunk_vecs)
        return results
