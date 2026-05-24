"""Unit tests for AlgoraBountiesAdapter (#853 Phase 3).

Includes a recorded-envelope regression test against a real Algora API
response shape. Per feedback_test_real_codepath_when_extracting: adapters
need tests against recorded envelopes, not only synthetic fixtures.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import requests as req

from findajob.fetchers.adapters.algora_bounties import AlgoraBountiesAdapter

_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "algora_bounties_envelope.json"


# ───────────────────── is_configured ─────────────────────


def test_is_configured_false_when_no_config(tmp_path: Path) -> None:
    adapter = AlgoraBountiesAdapter()
    with patch.object(adapter, "_config_path", return_value=tmp_path / "algora_orgs.txt"):
        assert adapter.is_configured() is False


def test_is_configured_false_when_empty_file(tmp_path: Path) -> None:
    cfg = tmp_path / "algora_orgs.txt"
    cfg.write_text("# just a comment\n\n")
    adapter = AlgoraBountiesAdapter()
    with patch.object(adapter, "_config_path", return_value=cfg):
        assert adapter.is_configured() is False


def test_is_configured_true_with_orgs(tmp_path: Path) -> None:
    cfg = tmp_path / "algora_orgs.txt"
    cfg.write_text("cal-com\ndocumenso\n")
    adapter = AlgoraBountiesAdapter()
    with patch.object(adapter, "_config_path", return_value=cfg):
        assert adapter.is_configured() is True


# ───────────────────── fetch() against recorded envelope ─────────────────────


def test_fetch_parses_recorded_envelope(tmp_path: Path) -> None:
    """End-to-end parse of a real captured Algora API response."""
    cfg = tmp_path / "algora_orgs.txt"
    cfg.write_text("cal-com\n")
    raw = json.loads(_FIXTURE_PATH.read_text())
    fake_response = MagicMock(status_code=200)
    fake_response.json.return_value = raw
    adapter = AlgoraBountiesAdapter()
    with (
        patch.object(adapter, "_config_path", return_value=cfg),
        patch("findajob.fetchers.adapters.algora_bounties.requests.get", return_value=fake_response),
    ):
        rows = adapter.fetch([])
    assert len(rows) == 3
    for row in rows:
        assert row["source"] == "algora_bounties"
        assert row["title"]
        assert row["company"]
        assert row["url"].startswith("http")
        assert "location" in row
        assert "description" in row


def test_fetch_includes_reward_in_title(tmp_path: Path) -> None:
    cfg = tmp_path / "algora_orgs.txt"
    cfg.write_text("cal-com\n")
    raw = json.loads(_FIXTURE_PATH.read_text())
    fake_response = MagicMock(status_code=200)
    fake_response.json.return_value = raw
    adapter = AlgoraBountiesAdapter()
    with (
        patch.object(adapter, "_config_path", return_value=cfg),
        patch("findajob.fetchers.adapters.algora_bounties.requests.get", return_value=fake_response),
    ):
        rows = adapter.fetch([])
    assert "[$500]" in rows[0]["title"]


# ───────────────────── fetch() synthetic happy-path ─────────────────────


def test_fetch_normalizes_org_slug_to_company(tmp_path: Path) -> None:
    cfg = tmp_path / "algora_orgs.txt"
    cfg.write_text("trigger-dot-dev\n")
    fake_response = MagicMock(status_code=200)
    fake_response.json.return_value = [
        {"title": "Fix CI pipeline", "url": "https://console.algora.io/org/trigger-dot-dev/bounties/1"}
    ]
    adapter = AlgoraBountiesAdapter()
    with (
        patch.object(adapter, "_config_path", return_value=cfg),
        patch("findajob.fetchers.adapters.algora_bounties.requests.get", return_value=fake_response),
    ):
        rows = adapter.fetch([])
    assert len(rows) == 1
    assert rows[0]["company"] == "Trigger Dot Dev"


def test_fetch_handles_nested_bounties_key(tmp_path: Path) -> None:
    """Some Algora responses wrap bounties in a top-level object."""
    cfg = tmp_path / "algora_orgs.txt"
    cfg.write_text("some-org\n")
    fake_response = MagicMock(status_code=200)
    fake_response.json.return_value = {"bounties": [{"title": "Add tests", "url": "https://console.algora.io/x"}]}
    adapter = AlgoraBountiesAdapter()
    with (
        patch.object(adapter, "_config_path", return_value=cfg),
        patch("findajob.fetchers.adapters.algora_bounties.requests.get", return_value=fake_response),
    ):
        rows = adapter.fetch([])
    assert len(rows) == 1


def test_fetch_skips_non_dict_entries(tmp_path: Path) -> None:
    cfg = tmp_path / "algora_orgs.txt"
    cfg.write_text("org1\n")
    fake_response = MagicMock(status_code=200)
    fake_response.json.return_value = [None, "bad", {"title": "Good", "url": "https://x"}]
    adapter = AlgoraBountiesAdapter()
    with (
        patch.object(adapter, "_config_path", return_value=cfg),
        patch("findajob.fetchers.adapters.algora_bounties.requests.get", return_value=fake_response),
    ):
        rows = adapter.fetch([])
    assert len(rows) == 1


# ───────────────────── fetch() failure modes ────────────────────��


def test_fetch_returns_empty_when_no_config(tmp_path: Path) -> None:
    adapter = AlgoraBountiesAdapter()
    with patch.object(adapter, "_config_path", return_value=tmp_path / "nope.txt"):
        rows = adapter.fetch([])
    assert rows == []


def test_fetch_skips_org_on_non_200(tmp_path: Path) -> None:
    cfg = tmp_path / "algora_orgs.txt"
    cfg.write_text("bad-org\ngood-org\n")
    bad_resp = MagicMock(status_code=404)
    good_resp = MagicMock(status_code=200)
    good_resp.json.return_value = [{"title": "Task", "url": "https://x"}]
    adapter = AlgoraBountiesAdapter()
    with (
        patch.object(adapter, "_config_path", return_value=cfg),
        patch(
            "findajob.fetchers.adapters.algora_bounties.requests.get",
            side_effect=[bad_resp, good_resp],
        ),
    ):
        rows = adapter.fetch([])
    assert len(rows) == 1


def test_fetch_skips_org_on_network_error(tmp_path: Path) -> None:
    cfg = tmp_path / "algora_orgs.txt"
    cfg.write_text("org1\n")
    adapter = AlgoraBountiesAdapter()
    with (
        patch.object(adapter, "_config_path", return_value=cfg),
        patch(
            "findajob.fetchers.adapters.algora_bounties.requests.get",
            side_effect=req.RequestException("timeout"),
        ),
    ):
        rows = adapter.fetch([])
    assert rows == []


# ───────────────────── live_test() buckets ─────────────────────


def test_live_test_not_configured(tmp_path: Path) -> None:
    adapter = AlgoraBountiesAdapter()
    with patch.object(adapter, "_config_path", return_value=tmp_path / "nope.txt"):
        result = adapter.live_test([])
    assert result.ok is False
    assert result.bucket == "auth"


def test_live_test_success(tmp_path: Path) -> None:
    cfg = tmp_path / "algora_orgs.txt"
    cfg.write_text("cal-com\n")
    fake_response = MagicMock(status_code=200)
    fake_response.json.return_value = [{"title": "a"}, {"title": "b"}]
    adapter = AlgoraBountiesAdapter()
    with (
        patch.object(adapter, "_config_path", return_value=cfg),
        patch("findajob.fetchers.adapters.algora_bounties.requests.get", return_value=fake_response),
    ):
        result = adapter.live_test([])
    assert result.ok is True
    assert result.bucket == "success"
    assert result.per_query[0].count == 2


def test_live_test_network_error(tmp_path: Path) -> None:
    cfg = tmp_path / "algora_orgs.txt"
    cfg.write_text("cal-com\n")
    adapter = AlgoraBountiesAdapter()
    with (
        patch.object(adapter, "_config_path", return_value=cfg),
        patch(
            "findajob.fetchers.adapters.algora_bounties.requests.get",
            side_effect=req.RequestException("dns fail"),
        ),
    ):
        result = adapter.live_test([])
    assert result.ok is False
    assert result.bucket == "network"
