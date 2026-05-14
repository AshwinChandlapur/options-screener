"""GPT-4o-mini conviction-state classifier (§3 of NARRATIVE_METHODOLOGY.md).

Pure function: takes a signal's ticker, sentiment, and rationale; returns
one of the 10 conviction states plus a confidence score.

Structured output via OpenAI JSON schema response_format — no parsing heuristics.
"""
from __future__ import annotations

import logging

from openai import AzureOpenAI

logger = logging.getLogger(__name__)

# Exactly the 10 conviction states defined in §3.
CONVICTION_STATES: list[str] = [
    "researched_bull",
    "researched_bear",
    "emotional_bull",
    "emotional_bear",
    "uncertainty",
    "earnings_focused",
    "product_thesis",
    "ecosystem_thesis",
    "institutional_watch",
    "exit_signal",
]

_RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name": "conviction_classification",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "conviction_state": {
                    "type": "string",
                    "enum": CONVICTION_STATES,
                },
                "conviction_confidence": {
                    "type": "number",
                },
            },
            "required": ["conviction_state", "conviction_confidence"],
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
    ) -> tuple[str, float]:
        """Return (conviction_state, conviction_confidence).

        Raises on OpenAI API error — caller decides retry/skip policy.
        """
        prompt = self._prompt_template.format(
            ticker=ticker,
            sentiment=sentiment,
            rationale=rationale or "(no content)",
        )
        response = self._client.chat.completions.create(
            model=self._deployment,
            messages=[{"role": "user", "content": prompt}],
            response_format=_RESPONSE_FORMAT,  # type: ignore[arg-type]
            max_tokens=64,
            temperature=0.0,
        )
        import json  # local import — only needed here
        content = response.choices[0].message.content or "{}"
        result = json.loads(content)
        state = result.get("conviction_state", "uncertainty")
        confidence = float(result.get("conviction_confidence", 0.5))
        # Clamp confidence to [0, 1] — model may hallucinate out-of-range values.
        confidence = max(0.0, min(1.0, confidence))
        if state not in CONVICTION_STATES:
            logger.warning("Unexpected conviction_state %r — defaulting to uncertainty", state)
            state = "uncertainty"
        return state, confidence
