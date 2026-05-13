"""Reddit polling via OAuth client credentials (no user login required).

Uses the Reddit OAuth2 client_credentials grant — requires a Reddit app
(script type) registered at reddit.com/prefs/apps. Tokens are refreshed
automatically when they expire (typically 24h). Azure datacenter IPs are
blocked by Reddit for unauthenticated requests; OAuth bypasses this.
"""
from __future__ import annotations

import logging
import threading
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

_TOKEN_URL = "https://www.reddit.com/api/v1/access_token"
_API_BASE = "https://oauth.reddit.com"


class RedditPoller:
    """Polls Reddit subreddits using OAuth client credentials.

    Fetches a Bearer token on first use and refreshes it 60 s before expiry.
    Thread-safe token refresh is handled internally.
    """

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        user_agent: str,
        author_salt: str,
        post_limit_per_subreddit: int = 100,
    ) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._user_agent = user_agent
        self._salt = author_salt
        self._post_limit = post_limit_per_subreddit
        self._access_token: str | None = None
        self._token_expires_at: float = 0.0
        self._lock = threading.Lock()
        self._http = httpx.Client(timeout=15.0, follow_redirects=True)

    def _ensure_token(self) -> str:
        with self._lock:
            if self._access_token and time.monotonic() < self._token_expires_at - 60:
                return self._access_token
            resp = self._http.post(
                _TOKEN_URL,
                auth=(self._client_id, self._client_secret),
                data={"grant_type": "client_credentials"},
                headers={"User-Agent": self._user_agent},
            )
            resp.raise_for_status()
            data = resp.json()
            self._access_token = data["access_token"]
            self._token_expires_at = time.monotonic() + int(data.get("expires_in", 3600))
            logger.info("Reddit OAuth token refreshed, expires in %ds", data.get("expires_in", 3600))
            return self._access_token

    @retry(
        retry=retry_if_exception_type((httpx.TransportError, httpx.TimeoutException)),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    def _fetch_listing(self, subreddit_name: str) -> list[dict]:
        token = self._ensure_token()
        url = f"{_API_BASE}/r/{subreddit_name}/new"
        resp = self._http.get(
            url,
            params={"limit": self._post_limit, "raw_json": "1"},
            headers={"Authorization": f"Bearer {token}", "User-Agent": self._user_agent},
        )
        if resp.status_code == 401:
            # Token rejected — force refresh on next call
            with self._lock:
                self._access_token = None
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
            source="reddit_oauth",
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
        self._http.close()


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
