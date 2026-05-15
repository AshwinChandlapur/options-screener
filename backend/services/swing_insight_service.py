"""
Swing-trade commentary — single batched LLM call covering the top N candidates.

The LLM receives setup-typed signals (NOT swing_score) and produces a brief
narrative + risk note per ticker. Blinded to numerical screener scores.

Called from the router AFTER scan results are computed and ranked.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass

from openai import AzureOpenAI

logger = logging.getLogger(__name__)

_AZURE_KEY = os.getenv("AZURE_OPENAI_KEY", "")
_AZURE_ENDPOINT = os.getenv("AZURE_OPENAI_ENDPOINT", "")
_AZURE_DEPLOYMENT = os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4.1")
_AZURE_API_VERSION = os.getenv("AZURE_OPENAI_API_VERSION", "2025-01-01-preview")


@dataclass(frozen=True)
class SwingCommentary:
    symbol: str
    narrative: str        # 1–2 sentences on the setup
    risk_note: str        # one sentence — what would invalidate it


class SwingInsightError(Exception):
    """Raised when commentary cannot be generated."""


_SYSTEM_PROMPT = """\
You are a discretionary swing trader reviewing a shortlist of technical setups.

For each ticker in the batch, write a brief narrative + risk note:

  narrative : 1-2 sentences describing the setup geometry in plain English.
              Reference the setup_type (breakout/momentum/reversion/retest) and
              the strongest driver(s). Do NOT cite numerical scores -- you have
              not been told them.
  risk_note : one sentence describing the single observable signal that would
              invalidate the trade thesis (e.g. "close below $X", "loss of
              200 EMA", "MACD reversal").

Reason only from the data provided. Stay disciplined and concise. Do not
recommend ENTER/SKIP -- that's the trader's call.
"""

_RESPONSE_SCHEMA = {
    "name": "swing_batch_commentary",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["items"],
        "properties": {
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["symbol", "narrative", "risk_note"],
                    "properties": {
                        "symbol": {"type": "string"},
                        "narrative": {"type": "string"},
                        "risk_note": {"type": "string"},
                    },
                },
            },
        },
    },
}


def _build_payload(candidates: list[dict]) -> str:
    """Strip screener scores from payload before sending."""
    blinded = []
    for c in candidates:
        blinded.append({
            "symbol": c["symbol"],
            "setup_type": c["setup_type"],
            "drivers": c["drivers"],
            "price": c["price"],
            "entry": c["entry"],
            "stop": c["stop"],
            "target": c["target"],
            "rr": c["rr"],
            "hold_days": f"{c['hold_min_days']}-{c['hold_max_days']}",
            "rsi": c.get("rsi"),
            "adx": c.get("adx"),
            "ema_alignment_score": c.get("ema_alignment_score"),
            "rs_vs_spy": c.get("rs_vs_spy"),
            "earnings_warning": c.get("earnings_warning", False),
        })
    return json.dumps({"candidates": blinded}, indent=2)


def get_batch_commentary(candidates: list[dict]) -> list[SwingCommentary]:
    """
    Generate commentary for a list of swing candidates in one LLM call.

    Returns empty list if Azure is not configured or the call fails — callers
    must tolerate missing commentary (the screener still works without it).
    """
    if not candidates:
        return []
    if not _AZURE_KEY or not _AZURE_ENDPOINT:
        logger.info("Azure OpenAI not configured — skipping swing commentary")
        return []

    client = AzureOpenAI(
        api_key=_AZURE_KEY,
        azure_endpoint=_AZURE_ENDPOINT,
        api_version=_AZURE_API_VERSION,
    )

    try:
        response = client.chat.completions.create(
            model=_AZURE_DEPLOYMENT,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": _build_payload(candidates)},
            ],
            response_format={"type": "json_schema", "json_schema": _RESPONSE_SCHEMA},
            temperature=0.3,
            max_tokens=900,
        )
    except Exception as exc:
        logger.warning("Swing batch commentary call failed: %s", exc)
        return []

    raw = response.choices[0].message.content or ""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.warning("Swing commentary JSON parse failed: %s", exc)
        return []

    out: list[SwingCommentary] = []
    for item in data.get("items", []):
        out.append(SwingCommentary(
            symbol=str(item.get("symbol", "")).upper(),
            narrative=str(item.get("narrative", "")),
            risk_note=str(item.get("risk_note", "")),
        ))
    return out
