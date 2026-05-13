"""Reddit polling via the public JSON API (no OAuth required).

Hits /r/{sub}/new.json with a polite User-Agent. Unauthenticated access is
rate-limited by Reddit to roughly 1 req/2s; RateBudget enforces 30 req/min to
stay well clear. Comment fetching is deferred until OAuth approval lands — post
titles + selftext alone carry sufficient signal for Phase 1 extraction.

Migration note: when Reddit API access is approved, swap this module for the
PRAW-based poller in git history. No other files in this worker need to change.
"""
from __future__ import annotations

import logging
import time
from collections.abc import Iterator

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from author_privacy import hash_author
from schema import RawEvent

logger = logging.getLogger(__name__)

_BASE_URL = "https://www.reddit.com"


class RedditPoller:
    """Polls Reddit's public JSON endpoints without OAuth credentials.

    A single httpx.Client is reused across calls for connection pooling.
    Reddit blocks the default Python UA; the caller must supply a descriptive
    user_agent (e.g. "narrative-screener/1.0 by u/yourname").
    """

    def __init__(
        self,
        user_agent: str,
        author_salt: str,
        post_limit_per_subreddit: int = 100,
    ) -> None:
        self._salt = author_salt
        self._post_limit = post_limit_per_subreddit
        self._client = httpx.Client(
            headers={"User-Agent": user_agent},
            timeout=15.0,
            follow_redirects=True,
        )

    @retry(
        retry=retry_if_exception_type((httpx.TransportError, httpx.TimeoutException)),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    def _fetch_listing(self, subreddit_name: str) -> list[dict]:
        url = f"{_BASE_URL}/r/{subreddit_name}/new.json"
        resp = self._client.get(url, params={"limit": self._post_limit, "raw_json": "1"})
        resp.raise_for_status()
        return resp.json().get("data", {}).get("children", [])

    def poll_subreddit(self, subreddit_name: str) -> Iterator[RawEvent]:
        try:
            children = self._fetch_listing(subreddit_name)
        except Exception:
            logger.exception("Failed to fetch r/%s", subreddit_name)
            return

        for child in children:
            if child.get("kind") == "t3":
                yield self._post_to_event(child.get("data", {}), subreddit_name)

    def _post_to_event(self, post: dict, subreddit_name: str) -> RawEvent:
        body = (post.get("title") or "") + "\n\n" + (post.get("selftext") or "")
        return RawEvent(
            event_id=RawEvent.new_event_id(),
            source="reddit_json",
            subreddit=subreddit_name,
            post_id=post.get("id", ""),
            parent_id=None,
            author_hash=hash_author(post.get("author"), self._salt),
            created_utc=int(post.get("created_utc") or 0),
            body=body[:8000],
            score=int(post.get("score") or 0),
            awards=int(post.get("total_awards_received") or 0),
            flair=post.get("link_flair_text"),
            ingested_at=RawEvent.now_iso(),
            kind="post",
            metadata={
                "num_comments": int(post.get("num_comments") or 0),
                "permalink": post.get("permalink", ""),
            },
        )

    def close(self) -> None:
        self._client.close()


class RateBudget:
    """Coarse client-side throttle to stay under N requests/minute.

    Unauthenticated Reddit allows roughly 30 req/min; this enforces that cap
    so no single cycle can burn through the budget across all subreddits.
    """

    def __init__(self, requests_per_minute: int) -> None:
        self._budget = requests_per_minute
        self._window_start = time.monotonic()
        self._used = 0

    def consume(self, n: int = 1) -> None:
        now = time.monotonic()
        if now - self._window_start >= 60:
            self._window_start = now
            self._used = 0
        if self._used + n > self._budget:
            sleep_for = 60 - (now - self._window_start)
            if sleep_for > 0:
                logger.info("Rate budget exhausted; sleeping %.1fs", sleep_for)
                time.sleep(sleep_for)
            self._window_start = time.monotonic()
            self._used = 0
        self._used += n
