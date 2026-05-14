"""Conviction-state classifier entry point (Phase 4).

Container Apps Job — runs on a 30-minute cron schedule.

What it does:
1. Fetches up to MAX_SIGNALS_PER_RUN unclassified signals from Cosmos `signals`.
2. For each signal, calls GPT-4o-mini (structured output) to classify into one
   of the 10 conviction states defined in docs/NARRATIVE_METHODOLOGY.md §3.
3. Writes conviction_state + conviction_confidence back to the signal document.

The Phase 3 aggregator (job-aggregator, 15-min cron) reads conviction_state
on its next run and computes conviction ratios for ticker_timeline.

Idempotent: already-classified signals are skipped by the Cosmos query.

Env contract:
    KEYVAULT_URI           https://kv-narrative-<suffix>.vault.azure.net/
    COSMOS_ENDPOINT        https://cosmos-nr-<suffix>.documents.azure.com:443/
    COSMOS_DB              narrative  (default)
    LOG_LEVEL              INFO / DEBUG  (default INFO)
    BATCH_SIZE             signals fetched per run (default 50)
    MAX_SIGNALS_PER_RUN    hard cap per job execution (default 200)
"""
from __future__ import annotations

import logging
import sys

from classifier import ConvictionClassifier
from config import load_from_env
from cosmos_client import CosmosClassifierClient
from kv_secrets import fetch_secrets

logger = logging.getLogger(__name__)


def _setup_logging(level: str) -> None:
    logging.basicConfig(
        level=level.upper(),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        stream=sys.stdout,
    )


def main() -> None:
    config = load_from_env()
    _setup_logging(config.log_level)
    logger.info("Starting conviction classifier (Phase 4)")

    secrets = fetch_secrets(config.keyvault_uri)
    client = CosmosClassifierClient(
        endpoint=config.cosmos_endpoint,
        database=config.cosmos_db,
    )
    clf = ConvictionClassifier(
        api_key=secrets.openai_api_key,
        endpoint=secrets.openai_endpoint,
        deployment=secrets.openai_deployment,
        prompt_template=secrets.prompt_template,
    )

    classified = 0
    skipped = 0
    remaining = config.max_signals_per_run
    _skipped_ids: set[str] = set()  # prevents re-processing write-failed docs in same run

    while remaining > 0:
        batch_size = min(config.batch_size, remaining)
        signals = client.fetch_unclassified(batch_size, skip_ids=_skipped_ids)
        if not signals:
            logger.info("No unclassified signals remaining")
            break

        for doc in signals:
            ticker = doc.get("ticker", "")
            sentiment = doc.get("sentiment", "neutral")
            rationale = doc.get("rationale", "")

            try:
                state, confidence = clf.classify(ticker, sentiment, rationale)
                client.write_conviction(doc, state, confidence)
                classified += 1
                logger.debug(
                    "  %s [%s] → %s (%.2f)",
                    ticker, doc.get("id", "")[:8], state, confidence,
                )
            except Exception:
                logger.exception(
                    "Failed to classify signal %s for ticker %s",
                    doc.get("id", "?"), ticker,
                )
                _skipped_ids.add(doc.get("id", ""))
                skipped += 1

        remaining -= len(signals)
        # If we got fewer than requested, we've drained the queue.
        if len(signals) < batch_size:
            break

    logger.info(
        "Classifier complete — classified=%d skipped=%d",
        classified, skipped,
    )

    # Exit non-zero if every attempted signal failed — surfaces as job failure
    # so Container Apps retries and the on-call alert fires.
    if classified == 0 and skipped > 0:
        logger.error("All %d signals failed classification — exiting non-zero", skipped)
        sys.exit(1)


if __name__ == "__main__":
    main()
