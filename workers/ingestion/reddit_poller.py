"""Reddit polling via Arctic Shift API (no auth required).

Arctic Shift (https://arctic-shift.photon-reddit.com) is a public Reddit
archive API that works from any IP including Azure datacenters.

Advantages over RSS:
- Full post selftext (body) — not just the title
- Top-level comments with body text
- Author field present (used for pseudonymization)
- No credentials, no OAuth, no app registration required

Trade-offs vs Reddit OAuth API:
- Scores for posts < 36h old are always 1 (archival lag); real scores appear
  after ~36h. Score-based filtering is deferred to Phase 3 aggregation.
- Rate limit: soft limit tracked via X-RateLimit-Remaining header; we stay
  well under by capping at 30 req/min client-side.

If Arctic Shift ever becomes unavailable, revert this file to the RSS
implementation from git history. The RawEvent schema is unchanged.
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

_ARCTIC_SHIFT_BASE = "https://arctic-shift.photon-reddit.com/api"
# Fields we actually need — reduces response size.
_POST_FIELDS = "id,title,selftext,author,score,created_utc,num_comments,link_flair_text"
_COMMENT_FIELDS = "id,body,author,score,created_utc,link_id,parent_id"


class RedditPoller:
    """Polls Reddit subreddits via Arctic Shift — no credentials required.

    Fetches recent posts and their top-level comments per subreddit.
    A single httpx.Client is reused across calls for connection pooling.

    Dedup: _last_seen_utc tracks the highest created_utc returned per
    subreddit. Subsequent calls pass `after=<utc>` so Arctic Shift only
    returns posts newer than the last seen one. This is in-memory — on
    worker restart a single catch-up batch is ingested; the EH consumer
    checkpoint prevents that batch from being extracted twice.
    """

    def __init__(
        self,
        user_agent: str,
        author_salt: str,
        post_limit_per_subreddit: int = 100,
        comment_limit_per_post: int = 50,
    ) -> None:
        self._salt = author_salt
        self._post_limit = min(post_limit_per_subreddit, 100)
        self._comment_limit = min(comment_limit_per_post, 100)
        self._client = httpx.Client(
            headers={"User-Agent": user_agent},
            timeout=20.0,
            follow_redirects=True,
        )
        # subreddit → highest created_utc seen; used as `after` cursor.
        self._last_seen_utc: dict[str, int] = {}

    @retry(
        retry=retry_if_exception_type((httpx.TransportError, httpx.TimeoutException)),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    def _fetch_posts(self, subreddit_name: str) -> list[dict]:
        params: dict[str, object] = {
            "subreddit": subreddit_name,
            "limit": self._post_limit,
            "sort": "desc",
            "fields": _POST_FIELDS,
        }
        if subreddit_name in self._last_seen_utc:
            params["after"] = self._last_seen_utc[subreddit_name]
        resp = self._client.get(
            f"{_ARCTIC_SHIFT_BASE}/posts/search",
            params=params,
        )
        resp.raise_for_status()
        return resp.json().get("data", [])

    @retry(
        retry=retry_if_exception_type((httpx.TransportError, httpx.TimeoutException)),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    def _fetch_comments(self, post_id: str) -> list[dict]:
        resp = self._client.get(
            f"{_ARCTIC_SHIFT_BASE}/comments/search",
            params={
                "link_id": post_id,
                "limit": self._comment_limit,
                "sort": "desc",
                "fields": _COMMENT_FIELDS,
            },
        )
        resp.raise_for_status()
        return resp.json().get("data", [])

    def poll_subreddit(self, subreddit_name: str) -> Iterator[RawEvent]:
        """Yield RawEvents for posts newer than the last seen one + their top comments."""
        try:
            posts = self._fetch_posts(subreddit_name)
        except Exception:
            logger.exception("Failed to fetch posts for r/%s", subreddit_name)
            return

        if not posts:
            return

        # Advance the cursor to the newest post in this batch so the next
        # call only fetches posts created after this point.
        max_utc = max(int(p.get("created_utc", 0)) for p in posts)
        self._last_seen_utc[subreddit_name] = max_utc
        logger.debug("r/%s: %d new posts (cursor → %d)", subreddit_name, len(posts), max_utc)

        for post in posts:
            post_id: str = post.get("id", "")
            yield self._post_to_event(post, subreddit_name)

            # Fetch top comments for posts that have discussion
            if post.get("num_comments", 0) > 0 and post_id:
                try:
                    comments = self._fetch_comments(post_id)
                    for comment in comments:
                        yield self._comment_to_event(comment, subreddit_name, post_id)
                except Exception:
                    logger.warning(
                        "Failed to fetch comments for post %s in r/%s",
                        post_id, subreddit_name,
                    )

    def _post_to_event(self, post: dict, subreddit_name: str) -> RawEvent:
        title: str = post.get("title", "")
        selftext: str = post.get("selftext", "") or ""
        # Combine title + body; for link posts selftext is empty or "[removed]"
        if selftext in ("[removed]", "[deleted]", ""):
            body = title
        else:
            body = f"{title}\n\n{selftext}"

        return RawEvent(
            event_id=RawEvent.new_event_id(),
            source="arctic_shift",
            subreddit=subreddit_name,
            post_id=post.get("id", ""),
            parent_id=None,
            author_hash=hash_author(post.get("author"), self._salt),
            created_utc=int(post.get("created_utc", 0)),
            body=body[:8000],
            score=int(post.get("score", 0)),
            awards=0,
            flair=post.get("link_flair_text"),
            ingested_at=RawEvent.now_iso(),
            kind="post",
            metadata={"num_comments": post.get("num_comments", 0)},
        )

    def _comment_to_event(
        self, comment: dict, subreddit_name: str, post_id: str
    ) -> RawEvent:
        body: str = comment.get("body", "") or ""
        if body in ("[removed]", "[deleted]"):
            body = ""

        return RawEvent(
            event_id=RawEvent.new_event_id(),
            source="arctic_shift",
            subreddit=subreddit_name,
            post_id=post_id,
            parent_id=comment.get("parent_id"),
            author_hash=hash_author(comment.get("author"), self._salt),
            created_utc=int(comment.get("created_utc", 0)),
            body=body[:8000],
            score=int(comment.get("score", 0)),
            awards=0,
            flair=None,
            ingested_at=RawEvent.now_iso(),
            kind="comment",
            metadata={"comment_id": comment.get("id", "")},
        )

    def close(self) -> None:
        self._client.close()


class RateBudget:
    """Coarse client-side throttle to stay under N requests/minute.

    Arctic Shift is a free service — be considerate. We enforce 30 req/min
    by default. Each poll_subreddit() call costs 1 (posts) + up to N comment
    fetches; budget.consume() is called once per subreddit in main.py so
    posts + comments share the same token.
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

