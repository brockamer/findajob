"""Parser tests — RFC 822 + bs4 HTML fallback + whitespace normalization."""

from __future__ import annotations

from pathlib import Path

from findajob.rejection_detector.parser import parse

FIXTURES = Path(__file__).parent / "fixtures" / "rejection_emails"


def test_parses_sender_and_domain() -> None:
    parsed = parse((FIXTURES / "greenhouse" / "rejection.eml").read_bytes())
    assert parsed.sender == "no-reply@us.greenhouse-mail.io"
    assert parsed.sender_domain == "us.greenhouse-mail.io"


def test_parses_subject_and_received_at() -> None:
    parsed = parse((FIXTURES / "ashby" / "rejection.eml").read_bytes())
    assert parsed.subject == "Thank you for your interest in ExampleCo"
    assert parsed.received_at.startswith("2026-04-30")


def test_extracts_plaintext_body() -> None:
    parsed = parse((FIXTURES / "lever" / "rejection.eml").read_bytes())
    assert "decided to pursue other candidates" in parsed.plaintext_body_lower
    assert parsed.plaintext_body  # non-empty


def test_html_only_falls_back_to_bs4() -> None:
    """Microsoft Careers fixture has only a text/html part — bs4 must extract."""
    parsed = parse((FIXTURES / "html_only" / "microsoft_careers.eml").read_bytes())
    assert parsed.plaintext_body  # non-empty after bs4 strip
    assert "Principal Hardware Program Manager" in parsed.plaintext_body
    assert "<p>" not in parsed.plaintext_body  # tags stripped
    assert "<strong>" not in parsed.plaintext_body


def test_whitespace_normalization_collapses_line_wraps() -> None:
    """Marker phrases broken across line wraps must still match.

    The greenhouse fixture's body wraps "unfortunately, we are not moving" /
    "forward" across two lines — the parser collapses to single spaces so
    the marker phrase remains contiguous.
    """
    parsed = parse((FIXTURES / "greenhouse" / "rejection.eml").read_bytes())
    assert "unfortunately, we are not moving forward" in parsed.plaintext_body_lower
    assert "\n" not in parsed.plaintext_body  # all newlines collapsed


def test_message_id_preserved() -> None:
    parsed = parse((FIXTURES / "greenhouse" / "rejection.eml").read_bytes())
    assert parsed.message_id == "<gh-rej-001@us.greenhouse-mail.io>"


def test_missing_headers_do_not_crash() -> None:
    raw = b"Subject: Bare email\nContent-Type: text/plain; charset=utf-8\n\nNo other headers."
    parsed = parse(raw)
    assert parsed.subject == "Bare email"
    assert parsed.sender == ""
    assert parsed.sender_domain == ""
    assert parsed.received_at == ""
    assert "no other headers" in parsed.plaintext_body_lower
