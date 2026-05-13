"""
Download real Reddit posts from Arctic Shift and append them to the precision
evaluation fixture as unlabeled entries ready for human review.

Usage:
    cd backend
    .\\venv\\Scripts\\python.exe ..\\scripts\\fetch_labeling_candidates.py

What it does:
- Fetches recent posts (and optionally top comments) from the configured
  subreddits via Arctic Shift API (no auth required)
- Filters out posts too short for meaningful labeling (< 100 chars)
- Skips post IDs already present in labeled_mentions.jsonl
- Appends new entries with human_labels=[] and captured_output=null
- Prints a summary so you know how many new entries were added

After running:
1. Open backend/tests/fixtures/extractor/labeled_mentions.jsonl
2. For each entry with source="arctic_shift" and human_labels=[]:
   - Read the body
   - Add {"ticker": "NVDA", "sentiment": "bullish"} labels for each
     clear ticker opinion you see (leave [] if there is no clear opinion)
3. Run the capture script to fill captured_output:
      .\\venv\\Scripts\\python.exe ..\\scripts\\capture_extractor_fixtures.py

Subreddits sampled: a mix of investing-tier and WSB-tier per the methodology's
subreddit universe, weighted toward higher signal-to-noise.

Cost: $0 — this script makes no OpenAI calls.
"""
from __future__ import annotations

import json
import logging
import sys
import time
from pathlib import Path

import httpx

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

_ARCTIC_SHIFT_BASE = "https://arctic-shift.photon-reddit.com/api"

FIXTURE_PATH = (
    Path(__file__).resolve().parent.parent
    / "backend"
    / "tests"
    / "fixtures"
    / "extractor"
    / "labeled_mentions.jsonl"
)

# Subreddits to sample, with per-subreddit post limits.
# Tier 1 = higher quality / research-oriented; Tier 2 = WSB / high volume.
_SUBREDDIT_LIMITS: dict[str, int] = {
    # Tier 1 — research-grade
    "investing": 40,
    "stocks": 40,
    "SecurityAnalysis": 20,
    "ValueInvesting": 20,
    # Tier 2 — high signal volume
    "wallstreetbets": 60,
    "options": 40,
    "smallstreetbets": 20,
    # Tier 3 — sector-specific
    "artificial": 20,
    "SemiConductors": 20,
    "energy": 20,
}

# Minimum body length for a post to be worth labeling.
_MIN_BODY_LEN = 100

# Fields to request from Arctic Shift.
_POST_FIELDS = "id,title,selftext,author,score,created_utc,num_comments,link_flair_text,subreddit"


def _load_existing_ids() -> set[str]:
    """Return post IDs (from 'id' field) already in the fixture."""
    if not FIXTURE_PATH.exists():
        return set()
    ids = set()
    with FIXTURE_PATH.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                entry = json.loads(line)
                ids.add(entry.get("id", ""))
    return ids


def _fetch_posts(client: httpx.Client, subreddit: str, limit: int, before_utc: int | None = None) -> list[dict]:
    """Fetch recent posts from Arctic Shift for a subreddit.

    before_utc: if set, fetch posts created before this UTC timestamp (pagination).
    """
    params: dict[str, object] = {
        "subreddit": subreddit,
        "limit": min(limit, 100),
        "sort": "desc",
        "fields": _POST_FIELDS,
    }
    if before_utc is not None:
        params["before"] = before_utc
    try:
        resp = client.get(
            f"{_ARCTIC_SHIFT_BASE}/posts/search",
            params=params,
            timeout=20.0,
        )
        resp.raise_for_status()
        return resp.json().get("data", [])
    except Exception as exc:
        logger.warning("Failed to fetch r/%s: %s", subreddit, exc)
        return []


def _build_body(post: dict) -> str:
    """Combine title + selftext into a single body string."""
    title = post.get("title", "").strip()
    selftext = (post.get("selftext", "") or "").strip()
    if selftext in ("[removed]", "[deleted]", ""):
        return title
    return f"{title}\n\n{selftext}"


def main(target: int = 500) -> None:
    existing_ids = _load_existing_ids()
    logger.info("Existing fixture entries: %d", len(existing_ids))

    new_entries: list[dict] = []
    client = httpx.Client(
        headers={"User-Agent": "script:narrative-labeling:1.0 (by /u/AshwinChandlapur)"},
        follow_redirects=True,
    )

    for subreddit, limit in _SUBREDDIT_LIMITS.items():
        if len(new_entries) >= target:
            break
        logger.info("Fetching r/%s...", subreddit)
        added = 0
        before_utc: int | None = None
        # Paginate: each page fetches up to 100 posts, going backwards in time.
        while added < limit and len(new_entries) < target:
            posts = _fetch_posts(client, subreddit, 100, before_utc)
            if not posts:
                break
            for post in posts:
                post_id = post.get("id", "")
                if not post_id or post_id in existing_ids:
                    continue
                body = _build_body(post)
                if len(body) < _MIN_BODY_LEN:
                    continue
                entry = {
                    "id": post_id,
                    "source": "arctic_shift",
                    "subreddit": post.get("subreddit", subreddit),
                    "score": post.get("score", 0),
                    "created_utc": post.get("created_utc", 0),
                    "flair": post.get("link_flair_text"),
                    "body": body[:2000],
                    "human_labels": [],
                    "captured_output": None,
                }
                new_entries.append(entry)
                existing_ids.add(post_id)
                added += 1
            # Set cursor to oldest post in this batch for next page
            oldest_utc = min(int(p.get("created_utc", 0)) for p in posts)
            if oldest_utc == before_utc:
                break  # no progress — stop
            before_utc = oldest_utc
            time.sleep(0.4)  # polite pacing between pages
        logger.info("  r/%s: %d new entries added", subreddit, added)
        time.sleep(0.5)

    client.close()

    if not new_entries:
        logger.info("No new entries to add.")
        return

    # Append to fixture file
    with FIXTURE_PATH.open("a", encoding="utf-8") as fh:
        for entry in new_entries:
            fh.write(json.dumps(entry) + "\n")

    logger.info(
        "Added %d new entries to %s",
        len(new_entries),
        FIXTURE_PATH,
    )
    logger.info("")
    logger.info("Next steps:")
    logger.info("  1. Open: %s", FIXTURE_PATH)
    logger.info("     For each entry with source='arctic_shift' and human_labels=[]:")
    logger.info("       - Read the body")
    logger.info('       - Set human_labels to e.g. [{"ticker":"NVDA","sentiment":"bullish"}]')
    logger.info("       - Leave [] if there is no clear ticker opinion")
    logger.info("  2. Run capture script:")
    logger.info("       cd backend")
    logger.info(r"       .\venv\Scripts\python.exe ..\scripts\capture_extractor_fixtures.py")


if __name__ == "__main__":
    target = int(sys.argv[1]) if len(sys.argv) > 1 else 500
    main(target)
