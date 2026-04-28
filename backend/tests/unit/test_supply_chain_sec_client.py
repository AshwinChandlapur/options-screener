"""Unit tests for ``services.supply_chain.sec_client.SecDataClient``.

All HTTP traffic is intercepted with ``httpx.MockTransport`` — these
tests never touch the network. The client is constructed with an
injected ``httpx.Client`` whose lifecycle the test owns, so we can
assert that ``close()`` does not close caller-owned clients.
"""
from __future__ import annotations

import httpx
import pytest

from services.supply_chain.sec_client import SecDataClient
from services.supply_chain.types import EightKFetchResult

# -------------------------------------------------------------- helpers
_TICKER_PAYLOAD = {
    "0": {"ticker": "MSFT", "cik_str": 789019},
    "1": {"ticker": "KO", "cik_str": 21344},
}

_SUBMISSIONS_PAYLOAD = {
    "name": "Microsoft Corp",
    "filings": {
        "recent": {
            "form": ["10-K", "8-K", "8-K", "10-Q"],
            "accessionNumber": [
                "0000789019-24-000001",
                "0000789019-24-000002",
                "0000789019-24-000003",
                "0000789019-24-000004",
            ],
            "filingDate": ["2024-07-30", "2024-09-15", "2024-08-10", "2024-10-25"],
            "primaryDocument": ["msft-10k.htm", "8k-1.htm", "8k-2.htm", "10q.htm"],
        }
    },
}


def _make_transport(routes: dict[str, httpx.Response]) -> httpx.MockTransport:
    """Return a transport that maps URL → canned ``httpx.Response``."""

    def _handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url in routes:
            return routes[url]
        return httpx.Response(404, text=f"unmatched: {url}")

    return httpx.MockTransport(_handler)


def _make_client(transport: httpx.MockTransport, *, headers: dict | None = None) -> httpx.Client:
    return httpx.Client(transport=transport, headers=headers or {})


# ---------------------------------------------------------- ticker map tests
def test_get_company_tickers_parses_and_zero_pads_cik():
    # Arrange
    transport = _make_transport({
        "https://www.sec.gov/files/company_tickers.json": httpx.Response(200, json=_TICKER_PAYLOAD),
    })
    sec = SecDataClient(http_client=_make_client(transport))

    # Act
    mapping = sec.get_company_tickers()

    # Assert
    assert mapping["MSFT"] == "0000789019"
    assert mapping["KO"] == "0000021344"


def test_get_company_tickers_caches_after_first_call():
    # Arrange
    call_count = {"n": 0}

    def _handler(request: httpx.Request) -> httpx.Response:
        call_count["n"] += 1
        return httpx.Response(200, json=_TICKER_PAYLOAD)

    sec = SecDataClient(http_client=httpx.Client(transport=httpx.MockTransport(_handler)))

    # Act
    sec.get_company_tickers()
    sec.get_company_tickers()
    sec.get_company_tickers()

    # Assert
    assert call_count["n"] == 1


def test_resolve_cik_returns_none_for_unknown_ticker():
    # Arrange
    transport = _make_transport({
        "https://www.sec.gov/files/company_tickers.json": httpx.Response(200, json=_TICKER_PAYLOAD),
    })
    sec = SecDataClient(http_client=_make_client(transport))

    # Act
    result = sec.resolve_cik("NOPE")

    # Assert
    assert result is None


def test_resolve_cik_is_case_insensitive():
    # Arrange
    transport = _make_transport({
        "https://www.sec.gov/files/company_tickers.json": httpx.Response(200, json=_TICKER_PAYLOAD),
    })
    sec = SecDataClient(http_client=_make_client(transport))

    # Act
    result = sec.resolve_cik("msft")

    # Assert
    assert result == "0000789019"


# ------------------------------------------------------- filings index tests
def test_get_filings_index_builds_primary_doc_url():
    # Arrange
    transport = _make_transport({
        "https://data.sec.gov/submissions/CIK0000789019.json": httpx.Response(200, json=_SUBMISSIONS_PAYLOAD),
    })
    sec = SecDataClient(http_client=_make_client(transport))

    # Act
    name, items = sec.get_filings_index("0000789019")

    # Assert
    assert name == "Microsoft Corp"
    assert len(items) == 4
    assert items[0]["form"] == "10-K"
    assert items[0]["primary_doc_url"] == (
        "https://www.sec.gov/Archives/edgar/data/789019/"
        "000078901924000001/msft-10k.htm"
    )


def test_get_latest_10k_returns_first_10k_with_company_name():
    # Arrange
    transport = _make_transport({
        "https://data.sec.gov/submissions/CIK0000789019.json": httpx.Response(200, json=_SUBMISSIONS_PAYLOAD),
    })
    sec = SecDataClient(http_client=_make_client(transport))

    # Act
    filing = sec.get_latest_10k("0000789019")

    # Assert
    assert filing is not None
    assert filing["form"] == "10-K"
    assert filing["company_name"] == "Microsoft Corp"
    assert filing["accession"] == "0000789019-24-000001"


def test_get_latest_10k_returns_none_when_no_10k():
    # Arrange — submissions without any 10-K
    payload = {
        "name": "Test Co",
        "filings": {
            "recent": {
                "form": ["10-Q"],
                "accessionNumber": ["0000000000-24-000001"],
                "filingDate": ["2024-01-01"],
                "primaryDocument": ["10q.htm"],
            }
        },
    }
    transport = _make_transport({
        "https://data.sec.gov/submissions/CIK0000789019.json": httpx.Response(200, json=payload),
    })
    sec = SecDataClient(http_client=_make_client(transport))

    # Act
    filing = sec.get_latest_10k("0000789019")

    # Assert
    assert filing is None


def test_get_recent_8ks_filters_by_date_and_max_count():
    # Arrange
    transport = _make_transport({
        "https://data.sec.gov/submissions/CIK0000789019.json": httpx.Response(200, json=_SUBMISSIONS_PAYLOAD),
    })
    sec = SecDataClient(http_client=_make_client(transport))

    # Act
    eight_ks = sec.get_recent_8ks("0000789019", since_date="2024-09-01", max_count=10)

    # Assert — only the 2024-09-15 filing passes the cutoff
    assert len(eight_ks) == 1
    assert eight_ks[0]["filing_date"] == "2024-09-15"


def test_get_recent_8ks_respects_max_count():
    # Arrange
    transport = _make_transport({
        "https://data.sec.gov/submissions/CIK0000789019.json": httpx.Response(200, json=_SUBMISSIONS_PAYLOAD),
    })
    sec = SecDataClient(http_client=_make_client(transport))

    # Act — both 8-Ks are >= cutoff but cap at 1
    eight_ks = sec.get_recent_8ks("0000789019", since_date="2024-01-01", max_count=1)

    # Assert
    assert len(eight_ks) == 1


# ---------------------------------------------------------- filing text tests
_FILING_HTML = (
    "<html><body>"
    "<h2>Item 1. Business</h2><p>" + ("supplier text " * 1_000) + "</p>"
    "<h2>Item 2. Properties</h2></body></html>"
)


def test_fetch_filing_text_runs_extractor_on_response():
    # Arrange
    transport = _make_transport({
        "https://example.com/10k.htm": httpx.Response(200, text=_FILING_HTML),
    })
    sec = SecDataClient(http_client=_make_client(transport))

    # Act
    text = sec.fetch_filing_text("https://example.com/10k.htm")

    # Assert
    assert "supplier text" in text


def test_fetch_8k_text_strips_html():
    # Arrange
    transport = _make_transport({
        "https://example.com/8k.htm": httpx.Response(
            200, text="<html><body><p>material event disclosed</p></body></html>"
        ),
    })
    sec = SecDataClient(http_client=_make_client(transport))

    # Act
    text = sec.fetch_8k_text("https://example.com/8k.htm")

    # Assert
    assert "material event disclosed" in text
    assert "<p>" not in text


# ----------------------------------------------------- parallel 8-K fetch tests
def test_fetch_8ks_parallel_returns_empty_result_for_empty_input():
    # Arrange
    sec = SecDataClient(http_client=_make_client(_make_transport({})))

    # Act
    result = sec.fetch_8ks_parallel([])

    # Assert
    assert isinstance(result, EightKFetchResult)
    assert result.successful == []
    assert result.failed_count == 0


def test_fetch_8ks_parallel_preserves_order_and_counts_failures():
    # Arrange — 3 items, middle one returns 500
    routes = {
        "https://example.com/a.htm": httpx.Response(200, text="<p>A</p>"),
        "https://example.com/b.htm": httpx.Response(500, text="server error"),
        "https://example.com/c.htm": httpx.Response(200, text="<p>C</p>"),
    }
    sec = SecDataClient(http_client=_make_client(_make_transport(routes)))
    items = [
        {"primary_doc_url": "https://example.com/a.htm", "accession": "A"},
        {"primary_doc_url": "https://example.com/b.htm", "accession": "B"},
        {"primary_doc_url": "https://example.com/c.htm", "accession": "C"},
    ]

    # Act
    result = sec.fetch_8ks_parallel(items, max_workers=2)

    # Assert
    assert result.failed_count == 1
    assert [meta["accession"] for meta, _ in result.successful] == ["A", "C"]


# --------------------------------------------------------------- header tests
def test_user_agent_header_is_sent_on_requests():
    # Arrange
    captured: dict[str, str] = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        captured["ua"] = request.headers.get("user-agent", "")
        return httpx.Response(200, json=_TICKER_PAYLOAD)

    sec = SecDataClient(user_agent="Test Agent test@example.com")
    # Replace the owned client with one wired to the mock transport but
    # carrying the SecDataClient's headers.
    sec.close()
    sec._http = httpx.Client(  # type: ignore[attr-defined]
        transport=httpx.MockTransport(_handler),
        headers={"User-Agent": "Test Agent test@example.com"},
    )
    sec._owns_client = True  # type: ignore[attr-defined]

    # Act
    sec.get_company_tickers()

    # Assert
    assert captured["ua"] == "Test Agent test@example.com"
    sec.close()


# ----------------------------------------------------------- lifecycle tests
def test_close_does_not_close_caller_owned_client():
    # Arrange
    transport = _make_transport({})
    caller_client = _make_client(transport)
    sec = SecDataClient(http_client=caller_client)

    # Act
    sec.close()

    # Assert — caller's client is still usable
    assert not caller_client.is_closed
    caller_client.close()


def test_context_manager_closes_owned_client():
    # Arrange / Act
    with SecDataClient() as sec:
        owned = sec._http  # type: ignore[attr-defined]

    # Assert
    assert owned.is_closed


@pytest.mark.parametrize(
    ("status_code", "expect_raise"),
    [(200, False), (404, True), (500, True)],
)
def test_fetch_filing_text_raises_on_http_error(status_code: int, expect_raise: bool):
    # Arrange
    transport = _make_transport({
        "https://example.com/10k.htm": httpx.Response(status_code, text=_FILING_HTML),
    })
    sec = SecDataClient(http_client=_make_client(transport))

    # Act / Assert
    if expect_raise:
        with pytest.raises(httpx.HTTPStatusError):
            sec.fetch_filing_text("https://example.com/10k.htm")
    else:
        sec.fetch_filing_text("https://example.com/10k.htm")
