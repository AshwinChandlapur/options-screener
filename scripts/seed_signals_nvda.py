"""Seed synthetic NVDA signals into Cosmos `signals` for Phase 5 experimentation.

Writes N docs that look exactly like classifier-completed signals (have
``conviction_state`` AND ``embedding``) so ``job-narrative-detector`` will
pick them up on its next cron and write a lifecycle row into ``ticker_timeline``.

The embeddings are SYNTHETIC: two tight Gaussian clusters in 1536-d space.
This guarantees HDBSCAN finds ≥1 cluster (vs. real 4-post samples that go
all-noise) so we can validate the detector → timeline write path end-to-end.

Usage (PowerShell, from repo root):
    az login   # if not already
    $env:COSMOS_ENDPOINT = "https://cosmos-nr-tinkerhub-westus2.documents.azure.com:443/"
    .\backend\venv\Scripts\python.exe scripts\seed_signals_nvda.py --count 8

Cleanup:
    .\backend\venv\Scripts\python.exe scripts\seed_signals_nvda.py --delete

Notes:
- Doc IDs are prefixed ``seed_nvda_`` so they're easy to target for cleanup.
- ``createdUtc`` and ``_ts`` proxy → docs land inside the detector's 72h window.
- Cosmos ``_ts`` is server-assigned, so we can't backdate; that's fine for
  experimentation — the detector uses ``_ts`` for the cutoff.
"""
from __future__ import annotations

import argparse
import os
import random
import sys
from datetime import datetime, timezone
from hashlib import sha256

import numpy as np
from azure.cosmos import CosmosClient
from azure.identity import DefaultAzureCredential

EMBED_DIM = 1536
EMBED_MODEL = "text-embedding-ada-002"
TICKER = "NVDA"


def _make_doc(idx: int, conviction_state: str, embedding: list[float], rationale: str) -> dict:
    now = datetime.now(timezone.utc)
    post_id = f"seed_{TICKER.lower()}_{idx:03d}"
    # Match the extractor's enum (bullish/bearish/neutral), not its colloquial
    # variants — aggregator counts strict string matches for bullish_ratio.
    if "bull" in conviction_state:
        sentiment = "bullish"
    elif "bear" in conviction_state:
        sentiment = "bearish"
    else:
        sentiment = "neutral"
    return {
        "id": f"{post_id}_{TICKER}",
        "ticker": TICKER,
        "sentiment": sentiment,
        "confidence": 0.85,
        "rationale": rationale,
        "postId": post_id,
        "subreddit": "stocks" if conviction_state == "researched_bull" else "wallstreetbets",
        # Tag DD-flavored seeds via flair so dd_post_ratio fires for the
        # researched cluster — see workers/aggregator/attention.py _DD_TERMS.
        "flair": "DD" if conviction_state == "researched_bull" else None,
        "authorHash": sha256(f"seed_author_{idx}".encode()).hexdigest()[:16],
        "createdUtc": int(now.timestamp()),
        "source": "seed_script",
        "extractedAt": now.isoformat(),
        # Classifier-stamped fields:
        "conviction_state": conviction_state,
        "conviction_confidence": 0.80,
        "embedding": embedding,
        "embedding_model": EMBED_MODEL,
    }


def _two_cluster_embeddings(count: int, seed: int = 42) -> list[tuple[str, list[float]]]:
    """Build `count` embeddings split between two tight clusters.

    Cluster A: 'researched_bull' centroid (e.g. earnings/DD posts).
    Cluster B: 'emotional_bull' centroid (e.g. WSB hype).

    Within-cluster cosine similarity ~0.95; cross-cluster ~0.10. Easy for HDBSCAN.
    """
    rng = np.random.default_rng(seed)
    centroid_a = rng.normal(size=EMBED_DIM).astype(np.float32)
    centroid_b = rng.normal(size=EMBED_DIM).astype(np.float32)
    centroid_a /= np.linalg.norm(centroid_a)
    centroid_b /= np.linalg.norm(centroid_b)

    out: list[tuple[str, list[float]]] = []
    half = count // 2
    for i in range(count):
        if i < half:
            state = "researched_bull"
            base = centroid_a
        else:
            state = "emotional_bull"
            base = centroid_b
        # Tiny perturbation keeps cosine similarity within cluster ≥ ~0.95.
        noise = rng.normal(scale=0.05, size=EMBED_DIM).astype(np.float32)
        vec = base + noise
        vec /= np.linalg.norm(vec)
        out.append((state, vec.tolist()))
    return out


def _seed(container, count: int) -> None:
    rationales = [
        "Strong Q1 data center beat; FCF guide raised.",
        "Inference cycle just getting started — multiyear runway.",
        "Margin expansion despite Hopper→Blackwell transition.",
        "Cap-ex commentary from MAG7 implies durable demand.",
        "Going to $200 EOY easy, just buy and hold.",
        "Loaded calls again this morning, sky's the limit.",
        "Diamond hands on NVDA, this thing prints money.",
        "WSB favorite for a reason, momentum is unreal.",
    ]
    pairs = _two_cluster_embeddings(count)
    for i, (state, vec) in enumerate(pairs):
        rationale = rationales[i % len(rationales)]
        doc = _make_doc(i, state, vec, rationale)
        container.upsert_item(doc)
        print(f"  seeded {doc['id']:30s} state={state:18s} dim={len(vec)}")


def _delete_seeded(container) -> int:
    query = (
        "SELECT c.id, c.ticker FROM c "
        "WHERE STARTSWITH(c.id, 'seed_nvda_') AND c.ticker = @ticker"
    )
    items = list(
        container.query_items(
            query=query,
            parameters=[{"name": "@ticker", "value": TICKER}],
            enable_cross_partition_query=True,
        )
    )
    for item in items:
        container.delete_item(item=item["id"], partition_key=item["ticker"])
    return len(items)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, default=8,
                        help="Number of synthetic signals to seed (default 8 → two clusters of 4).")
    parser.add_argument("--delete", action="store_true",
                        help="Delete previously seeded NVDA docs and exit.")
    parser.add_argument("--db", default=os.getenv("COSMOS_DB", "narrative"))
    args = parser.parse_args()

    endpoint = os.getenv("COSMOS_ENDPOINT")
    if not endpoint:
        print("ERROR: set COSMOS_ENDPOINT env var", file=sys.stderr)
        return 2

    client = CosmosClient(endpoint, credential=DefaultAzureCredential())
    container = client.get_database_client(args.db).get_container_client("signals")

    if args.delete:
        n = _delete_seeded(container)
        print(f"Deleted {n} seeded NVDA docs.")
        return 0

    if args.count < 4:
        print("WARN: count < 4 — HDBSCAN may still return all-noise.", file=sys.stderr)

    print(f"Seeding {args.count} NVDA signals into {args.db}.signals ...")
    _seed(container, args.count)
    print("Done. Next job-narrative-detector cron should write a ticker_timeline lifecycle row for NVDA.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
