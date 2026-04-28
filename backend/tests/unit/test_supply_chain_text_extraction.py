"""Unit tests for the pure text-extraction helpers.

These tests use small synthetic HTML strings — no real 10-K bodies — so
they run in milliseconds and isolate the regex/slice logic from any
network or file I/O.
"""
from __future__ import annotations

from services.supply_chain.text_extraction import (
    extract_8k_text,
    extract_10k_relevant_text,
)


def _wrap_html(body: str) -> str:
    return f"<html><body>{body}</body></html>"


def _section(label: str, length: int) -> str:
    """Return a section body of approximately ``length`` characters."""
    filler = " ".join(["lorem"] * (length // 6 + 1))
    return f"<p>{label} {filler[:length]}</p>"


def test_extract_10k_relevant_text_with_both_sections_returns_both_slices():
    # Arrange
    biz = _section("supplier disclosures", 6_000)
    mda = _section("management discussion", 4_000)
    html = _wrap_html(
        "<h1>cover</h1>"
        "<h2>Item 1. Business</h2>" + biz +
        "<h2>Item 2. Properties</h2><p>property notes</p>"
        "<h2>Item 7. Management's Discussion</h2>" + mda +
        "<h2>Item 8. Financial Statements</h2><p>accountant notes</p>"
    )

    # Act
    result = extract_10k_relevant_text(html)

    # Assert
    assert "supplier disclosures" in result
    assert "management discussion" in result
    assert "accountant notes" not in result
    assert "cover" not in result


def test_extract_10k_relevant_text_short_business_slice_falls_back():
    # Arrange — Item 1 slice well under the 5_000-char floor
    html = _wrap_html(
        "<h2>Item 1. Business</h2><p>tiny</p>"
        "<h2>Item 2. Properties</h2>"
    )

    # Act
    result = extract_10k_relevant_text(html)

    # Assert — falls back to full text rather than returning the short slice
    assert "tiny" in result


def test_extract_10k_relevant_text_no_markers_returns_full_text():
    # Arrange
    html = _wrap_html("<p>no item markers anywhere here</p>")

    # Act
    result = extract_10k_relevant_text(html)

    # Assert
    assert "no item markers anywhere here" in result


def test_extract_10k_relevant_text_trims_to_max_chars_from_end():
    # Arrange — assemble valid sections then force a max_chars trim. Place a
    # distinctive marker near the END of the MD&A so the tail-keep slice can
    # be checked without depending on filler position.
    biz = _section("supplierMARK", 6_000)
    mda_filler = _section("mdaFiller", 3_500)
    mda_tail = "<p>MDA_TAIL_MARKER" + (" extra" * 50) + "</p>"
    html = _wrap_html(
        "<h2>Item 1. Business</h2>" + biz +
        "<h2>Item 2. Properties</h2>"
        "<h2>Item 7. Management's Discussion</h2>" + mda_filler + mda_tail +
        "<h2>Item 8. Financial Statements</h2>"
    )
    max_chars = 2_000

    # Act
    result = extract_10k_relevant_text(html, max_chars=max_chars)

    # Assert — keeps the tail (end of MD&A), drops the head
    assert len(result) <= max_chars
    assert "MDA_TAIL_MARKER" in result
    assert "supplierMARK" not in result


def test_extract_10k_relevant_text_strips_script_and_style_tags():
    # Arrange
    biz = _section("real business text", 6_000)
    html = _wrap_html(
        "<script>var x = 'malicious';</script>"
        "<style>body{color:red}</style>"
        "<h2>Item 1. Business</h2>" + biz +
        "<h2>Item 2. Properties</h2>"
    )

    # Act
    result = extract_10k_relevant_text(html)

    # Assert
    assert "malicious" not in result
    assert "color:red" not in result
    assert "real business text" in result


def test_extract_8k_text_strips_html_returns_plain_text():
    # Arrange
    html = _wrap_html(
        "<h1>8-K Item 1.01</h1><p>Material agreement signed with Acme Corp.</p>"
    )

    # Act
    result = extract_8k_text(html)

    # Assert
    assert "Material agreement signed with Acme Corp." in result
    assert "<p>" not in result


def test_extract_8k_text_truncates_to_max_chars():
    # Arrange
    body = "abcdefghij" * 5_000  # 50_000 chars
    html = _wrap_html(f"<p>{body}</p>")

    # Act
    result = extract_8k_text(html, max_chars=1_000)

    # Assert
    assert len(result) == 1_000
