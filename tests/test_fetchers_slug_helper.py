"""Unit tests for the shared `_parse_feed_slugs` helper (#410.2 extraction)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from findajob.fetchers.adapters._slugs import _parse_feed_slugs

_ASHBY_RE = re.compile(r"ashbyhq\.com/([A-Za-z0-9_.-]+)")
_LEVER_RE = re.compile(r"lever\.co/([A-Za-z0-9_.-]+)")


@pytest.fixture
def feed_urls(tmp_path: Path):
    def _write(lines: list[str]) -> str:
        p = tmp_path / "feed_urls.txt"
        p.write_text("\n".join(lines) + "\n")
        return str(p)

    return _write


def test_missing_file_returns_empty(tmp_path: Path) -> None:
    assert _parse_feed_slugs(str(tmp_path / "nope.txt"), _ASHBY_RE) == []


def test_dedup_keeps_first_occurrence(feed_urls) -> None:
    """Two URLs resolve to the same slug; first occurrence wins, including its
    display name. The second is silently dropped."""
    path = feed_urls(
        [
            "https://jobs.ashbyhq.com/openai  # First Mention",
            "https://jobs.ashbyhq.com/openai  # Second Mention",
        ]
    )
    assert _parse_feed_slugs(path, _ASHBY_RE) == [("openai", "First Mention")]


def test_inline_comment_used_as_display_name(feed_urls) -> None:
    path = feed_urls(["https://jobs.lever.co/zoox  # Zoox Inc."])
    assert _parse_feed_slugs(path, _LEVER_RE) == [("zoox", "Zoox Inc.")]


def test_default_display_name_titlecases_slug(feed_urls) -> None:
    """No inline comment → display name defaults to titlecased slug."""
    path = feed_urls(["https://jobs.lever.co/zoox"])
    assert _parse_feed_slugs(path, _LEVER_RE) == [("zoox", "Zoox")]


def test_non_matching_lines_ignored(feed_urls) -> None:
    """Lines that don't match the regex are silently skipped — does not raise."""
    path = feed_urls(
        [
            "# top-level comment",
            "",
            "https://boards.greenhouse.io/anthropic  # Greenhouse, not Ashby",
            "https://jobs.ashbyhq.com/openai  # OpenAI",
            "not a url at all",
        ]
    )
    assert _parse_feed_slugs(path, _ASHBY_RE) == [("openai", "OpenAI")]


def test_lever_regex_extracts_lever_only(feed_urls) -> None:
    """Same fixture parsed against the Lever regex returns only Lever rows."""
    path = feed_urls(
        [
            "https://jobs.lever.co/zoox  # Zoox",
            "https://jobs.ashbyhq.com/openai  # OpenAI",
            "https://jobs.lever.co/cerebras  # Cerebras",
        ]
    )
    assert _parse_feed_slugs(path, _LEVER_RE) == [
        ("zoox", "Zoox"),
        ("cerebras", "Cerebras"),
    ]


def test_empty_comment_falls_back_to_titlecase(feed_urls) -> None:
    """`url  # ` (comment with no text) → titlecase default, not empty string."""
    path = feed_urls(["https://jobs.ashbyhq.com/openai  # "])
    assert _parse_feed_slugs(path, _ASHBY_RE) == [("openai", "Openai")]
