from __future__ import annotations

from dataclasses import FrozenInstanceError
from unittest.mock import MagicMock, patch

import pytest
import requests

from findajob.fetchers import feed_probe
from findajob.fetchers.feed_probe import FeedRow, ProbeResult, parse_feed_rows, probe_feed_url


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


def _gh_row() -> FeedRow:
    return parse_feed_rows("https://boards.greenhouse.io/acme\n")[0]


def test_probe_live_on_200_and_does_not_download_body() -> None:
    resp = MagicMock(status_code=200)
    with patch("findajob.fetchers.feed_probe.requests.get", return_value=resp) as g:
        res = probe_feed_url(_gh_row())
    assert res.status == "live"
    assert res.http_code == 200
    assert res.hint == ""
    assert g.call_args.kwargs.get("stream") is True  # body never downloaded
    resp.close.assert_called_once()  # connection released


def test_probe_dead_on_404_with_slug_hint() -> None:
    resp = MagicMock(status_code=404)
    with patch("findajob.fetchers.feed_probe.requests.get", return_value=resp):
        res = probe_feed_url(_gh_row())
    assert res.status == "dead"
    assert res.http_code == 404
    assert "404" in res.hint
    assert "live" not in res.hint.lower()  # negative: not a success message


def test_probe_other_status_dead_with_code_in_hint() -> None:
    resp = MagicMock(status_code=500)
    with patch("findajob.fetchers.feed_probe.requests.get", return_value=resp):
        res = probe_feed_url(_gh_row())
    assert res.status == "dead"
    assert res.http_code == 500
    assert "500" in res.hint


def test_probe_unreachable_on_request_exception() -> None:
    with patch(
        "findajob.fetchers.feed_probe.requests.get",
        side_effect=requests.ConnectionError("boom"),
    ):
        res = probe_feed_url(_gh_row())
    assert res.status == "unreachable"
    assert res.http_code is None
    assert "offline" in res.hint.lower()


def test_probe_unsupported_skips_network() -> None:
    row = FeedRow(url="https://x.workday.com", ats=None, slug=None, company="https://x.workday.com")
    with patch("findajob.fetchers.feed_probe.requests.get") as g:
        res = probe_feed_url(row)
    assert res.status == "unsupported"
    assert res.http_code is None
    g.assert_not_called()


def test_probe_all_preserves_order_and_isolates_failures() -> None:
    rows = parse_feed_rows("https://boards.greenhouse.io/liveco\nhttps://jobs.lever.co/deadco\n")

    def fake_get(url: str, **kwargs: object) -> MagicMock:
        return MagicMock(status_code=200 if "liveco" in url else 404)

    with patch("findajob.fetchers.feed_probe.requests.get", side_effect=fake_get):
        results = feed_probe.probe_all(rows)
    assert [r.status for r in results] == ["live", "dead"]


def test_probe_all_isolates_unexpected_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    """A probe that raises an *unexpected* (non-RequestException) error must
    degrade to a single 'unreachable' row without breaking the batch or order."""
    rows = parse_feed_rows("https://boards.greenhouse.io/okco\nhttps://jobs.lever.co/boomco\n")

    def flaky(row: FeedRow, timeout: float = feed_probe.DEFAULT_TIMEOUT) -> ProbeResult:
        if row.slug == "boomco":
            raise ValueError("unexpected internal error")
        return ProbeResult(row=row, status="live", http_code=200, hint="")

    monkeypatch.setattr(feed_probe, "probe_feed_url", flaky)
    results = feed_probe.probe_all(rows)
    assert [r.status for r in results] == ["live", "unreachable"]  # failure isolated, order kept
    assert results[1].row.slug == "boomco"


def test_probe_all_empty_returns_empty_without_pool() -> None:
    assert feed_probe.probe_all([]) == []
