from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from findajob.fetchers.feed_probe import FeedRow, parse_feed_rows


def test_parse_greenhouse_extracts_slug_and_ignores_trailing_comment() -> None:
    # greenhouse._SLUG_RE.search() stops at the space before '#', so the slug
    # is extracted; Greenhouse ignores comments for company name (API supplies it).
    rows = parse_feed_rows("https://boards.greenhouse.io/acme  # Acme Corp\n")
    assert len(rows) == 1
    assert rows[0].ats == "greenhouse"
    assert rows[0].slug == "acme"
    assert rows[0].company == "Acme"  # titlecased slug, NOT the comment
    assert rows[0].company != "Acme Corp"  # negative: comment not used for Greenhouse


def test_parse_lever_uses_inline_comment_as_company() -> None:
    rows = parse_feed_rows("https://jobs.lever.co/zoox  # Zoox Robotics\n")
    assert rows[0].ats == "lever"
    assert rows[0].slug == "zoox"
    assert rows[0].company == "Zoox Robotics"


def test_parse_ashby_no_comment_titlecases_slug() -> None:
    rows = parse_feed_rows("https://jobs.ashbyhq.com/openai\n")
    assert rows[0].ats == "ashby"
    assert rows[0].slug == "openai"
    assert rows[0].company == "Openai"


def test_parse_unsupported_url_marks_ats_none() -> None:
    rows = parse_feed_rows("https://acme.wd1.myworkdayjobs.com/careers\n")
    assert len(rows) == 1
    assert rows[0].ats is None
    assert rows[0].slug is None
    assert rows[0].company == "https://acme.wd1.myworkdayjobs.com/careers"


def test_parse_skips_blank_and_comment_lines() -> None:
    text = "\n# a fully commented-out line\n   \nhttps://jobs.lever.co/zoox\n"
    rows = parse_feed_rows(text)
    assert len(rows) == 1
    assert rows[0].slug == "zoox"


def test_parse_dedupes_by_ats_and_slug_first_wins() -> None:
    text = "https://jobs.lever.co/zoox  # First\nhttps://jobs.lever.co/zoox  # Second\n"
    rows = parse_feed_rows(text)
    assert len(rows) == 1
    assert rows[0].company == "First"


def test_feedrow_is_frozen_dataclass() -> None:
    row = FeedRow(url="u", ats=None, slug=None, company="u")
    assert row.url == "u"
    with pytest.raises(FrozenInstanceError):
        row.url = "x"  # type: ignore[misc]
