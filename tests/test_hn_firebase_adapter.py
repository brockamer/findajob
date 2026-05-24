"""Unit tests for HNFirebaseAdapter (#853 Phase 3).

Includes recorded-envelope regression tests against a captured HN Firebase
API response shape. Per feedback_test_real_codepath_when_extracting: the
recorded envelope catches real-world HTML entity / pipe-format quirks
that synthetic fixtures miss.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import requests as req

from findajob.fetchers.adapters.hn_firebase import (
    HNFirebaseAdapter,
    _looks_like_job,
    _parse_comment,
    _strip_html,
)

_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "hn_firebase_thread_envelope.json"


def _load_fixture() -> dict:
    return json.loads(_FIXTURE_PATH.read_text())


def _mock_get_for_envelope(fixture: dict):
    """Build a side_effect function that returns the right fixture data per URL."""

    def _side_effect(url: str, **kwargs):
        resp = MagicMock(status_code=200)
        if "/user/whoishiring.json" in url:
            resp.json.return_value = fixture["user"]
        elif f"/item/{fixture['thread']['id']}.json" in url:
            resp.json.return_value = fixture["thread"]
        elif "/item/98500.json" in url:
            resp.json.return_value = fixture["non_hiring_story"]
        else:
            for cid_str, comment in fixture["comments"].items():
                if f"/item/{cid_str}.json" in url:
                    resp.json.return_value = comment
                    return resp
            for item_id in fixture["user"]["submitted"]:
                if f"/item/{item_id}.json" in url:
                    resp.status_code = 404
                    return resp
            resp.status_code = 404
        return resp

    return _side_effect


# ───────────────────── _strip_html ─────────────────────


def test_strip_html_converts_br_and_p() -> None:
    assert "line1\nline2" in _strip_html("line1<br>line2")
    assert "para1\npara2" in _strip_html("para1<p>para2")


def test_strip_html_decodes_entities() -> None:
    assert "A & B" in _strip_html("A &amp; B")
    assert '"quoted"' in _strip_html("&quot;quoted&quot;")


def test_strip_html_removes_tags() -> None:
    result = _strip_html('<a href="https://example.com">link text</a>')
    assert "link text" in result
    assert "<a" not in result


# ───────────────────── _parse_comment ─────────────────────


def test_parse_pipe_delimited_comment() -> None:
    html = "Acme Corp | Senior Engineer | San Francisco | Remote OK<p>We are hiring."
    result = _parse_comment(html, "https://hn/thread", 12345)
    assert result is not None
    assert result["company"] == "Acme Corp"
    assert result["title"] == "Senior Engineer"
    assert result["location"] == "San Francisco"
    assert result["source"] == "hn_whoishiring"
    assert result["url"] == "https://news.ycombinator.com/item?id=12345"
    assert "We are hiring" in result["description"]


def test_parse_two_pipe_segments() -> None:
    html = "StartupXYZ | ML Engineer<p>Apply at startup.example.com"
    result = _parse_comment(html, "https://hn/thread", 99)
    assert result is not None
    assert result["company"] == "StartupXYZ"
    assert "ML Engineer" in result["title"]


def test_parse_no_pipes_with_hiring_keywords() -> None:
    html = "We are hiring a remote engineer. Salary competitive. Apply now."
    result = _parse_comment(html, "https://hn/thread", 55)
    assert result is not None
    assert result["source"] == "hn_whoishiring"


def test_parse_no_pipes_no_keywords_returns_none() -> None:
    html = "This is just a random comment about the weather."
    result = _parse_comment(html, "https://hn/thread", 66)
    assert result is None


def test_parse_empty_text_returns_none() -> None:
    result = _parse_comment("", "https://hn/thread", 77)
    assert result is None


# ───────────────────── _looks_like_job ─────────────────────


def test_looks_like_job_positive() -> None:
    assert _looks_like_job("We are hiring a remote engineer") is True
    assert _looks_like_job("Looking for a senior position, apply now") is True


def test_looks_like_job_negative() -> None:
    assert _looks_like_job("Nice weather today in Portland") is False
    assert _looks_like_job("I love cats") is False


# ───────────────────── is_configured ─────────────────────


def test_is_configured_always_true() -> None:
    assert HNFirebaseAdapter().is_configured() is True


# ───────────────────── fetch() against recorded envelope ─────────────────────


def test_fetch_parses_recorded_envelope() -> None:
    fixture = _load_fixture()
    adapter = HNFirebaseAdapter()
    with (
        patch("findajob.fetchers.adapters.hn_firebase.requests.get", side_effect=_mock_get_for_envelope(fixture)),
        patch("findajob.fetchers.adapters.hn_firebase.time.sleep"),
    ):
        rows = adapter.fetch([])
    # 5 comments: 3 pipe-delimited jobs + 1 deleted (skipped) + 1 non-pipe but has keywords
    assert len(rows) >= 3
    for row in rows:
        assert row["source"] == "hn_whoishiring"
        assert row["url"].startswith("https://news.ycombinator.com/item?id=")
        assert "title" in row
        assert "company" in row


def test_fetch_skips_deleted_comments() -> None:
    fixture = _load_fixture()
    adapter = HNFirebaseAdapter()
    with (
        patch("findajob.fetchers.adapters.hn_firebase.requests.get", side_effect=_mock_get_for_envelope(fixture)),
        patch("findajob.fetchers.adapters.hn_firebase.time.sleep"),
    ):
        rows = adapter.fetch([])
    comment_ids = [int(r["url"].split("id=")[1]) for r in rows]
    assert 100004 not in comment_ids


def test_fetch_extracts_correct_company_from_pipe_format() -> None:
    fixture = _load_fixture()
    adapter = HNFirebaseAdapter()
    with (
        patch("findajob.fetchers.adapters.hn_firebase.requests.get", side_effect=_mock_get_for_envelope(fixture)),
        patch("findajob.fetchers.adapters.hn_firebase.time.sleep"),
    ):
        rows = adapter.fetch([])
    companies = [r["company"] for r in rows]
    assert "Acme Corp" in companies
    assert "NovaTech" in companies


# ───────────────────── fetch() failure modes ─────────────────────


def test_fetch_returns_empty_on_user_fetch_failure() -> None:
    adapter = HNFirebaseAdapter()
    with patch(
        "findajob.fetchers.adapters.hn_firebase.requests.get",
        side_effect=req.RequestException("network down"),
    ):
        rows = adapter.fetch([])
    assert rows == []


def test_fetch_returns_empty_when_no_threads_found() -> None:
    fake_user = MagicMock(status_code=200)
    fake_user.json.return_value = {"id": "whoishiring", "submitted": []}
    adapter = HNFirebaseAdapter()
    with patch("findajob.fetchers.adapters.hn_firebase.requests.get", return_value=fake_user):
        rows = adapter.fetch([])
    assert rows == []


# ───────────────────── live_test() buckets ─────────────────────


def test_live_test_success() -> None:
    fixture = _load_fixture()
    fake_response = MagicMock(status_code=200)
    fake_response.json.return_value = fixture["user"]
    with patch("findajob.fetchers.adapters.hn_firebase.requests.get", return_value=fake_response):
        result = HNFirebaseAdapter().live_test([])
    assert result.ok is True
    assert result.bucket == "success"


def test_live_test_network_error() -> None:
    with patch(
        "findajob.fetchers.adapters.hn_firebase.requests.get",
        side_effect=req.RequestException("dns fail"),
    ):
        result = HNFirebaseAdapter().live_test([])
    assert result.ok is False
    assert result.bucket == "network"


def test_live_test_server_error() -> None:
    fake_response = MagicMock(status_code=503)
    with patch("findajob.fetchers.adapters.hn_firebase.requests.get", return_value=fake_response):
        result = HNFirebaseAdapter().live_test([])
    assert result.ok is False
    assert result.bucket == "server"


def test_live_test_invalid_json() -> None:
    fake_response = MagicMock(status_code=200)
    fake_response.json.side_effect = ValueError("bad json")
    with patch("findajob.fetchers.adapters.hn_firebase.requests.get", return_value=fake_response):
        result = HNFirebaseAdapter().live_test([])
    assert result.ok is False
    assert result.bucket == "server"
