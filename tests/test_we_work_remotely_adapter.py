"""Unit tests for WeWorkRemotelyAdapter (#853 Phase 2).

Includes a recorded-envelope regression test against a real WWR RSS
captured 2026-05-23.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import requests as req

from findajob.fetchers.adapters.we_work_remotely import WeWorkRemotelyAdapter

_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "wwr_envelope.xml"


# ───────────────────── is_configured ─────────────────────


def test_is_configured_always_true() -> None:
    assert WeWorkRemotelyAdapter().is_configured() is True


# ───────────────────── fetch() against recorded envelope ─────────────────────


def test_fetch_parses_recorded_envelope() -> None:
    """End-to-end parse of a real captured WWR RSS response."""
    xml_text = _FIXTURE_PATH.read_text()
    fake_response = MagicMock(status_code=200, text=xml_text)
    with patch("findajob.fetchers.adapters.we_work_remotely.requests.get", return_value=fake_response):
        rows = WeWorkRemotelyAdapter().fetch([])
    assert len(rows) == 5
    for row in rows:
        assert row["source"] == "wwr_rss"
        assert row["title"]
        assert row["company"]
        assert row["url"].startswith("https://weworkremotely.com/")
        assert "location" in row
        assert "description" in row


def test_fetch_splits_company_from_title() -> None:
    """WWR title format: 'Company: Title' — adapter must split on first ': '."""
    xml = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <item>
      <title>Acme Corp: Senior Engineer, Platform Team</title>
      <link>https://weworkremotely.com/remote-jobs/acme-senior-engineer</link>
      <description>desc</description>
      <region>Worldwide</region>
    </item>
  </channel>
</rss>"""
    fake_response = MagicMock(status_code=200, text=xml)
    with patch("findajob.fetchers.adapters.we_work_remotely.requests.get", return_value=fake_response):
        rows = WeWorkRemotelyAdapter().fetch([])
    assert rows[0]["company"] == "Acme Corp"
    assert rows[0]["title"] == "Senior Engineer, Platform Team"


def test_fetch_falls_back_to_wwr_company_when_no_delimiter() -> None:
    """Items without 'Company: Title' format fall back to 'WWR' as company."""
    xml = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <item>
      <title>Standalone Title No Colon</title>
      <link>https://weworkremotely.com/x</link>
      <description>desc</description>
    </item>
  </channel>
</rss>"""
    fake_response = MagicMock(status_code=200, text=xml)
    with patch("findajob.fetchers.adapters.we_work_remotely.requests.get", return_value=fake_response):
        rows = WeWorkRemotelyAdapter().fetch([])
    assert rows[0]["company"] == "WWR"
    assert rows[0]["title"] == "Standalone Title No Colon"


def test_fetch_joins_region_state_country_for_location() -> None:
    xml = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <item>
      <title>X: Y</title>
      <link>https://weworkremotely.com/x</link>
      <description>d</description>
      <region>Anywhere in the World</region>
      <state>California</state>
      <country>USA</country>
    </item>
  </channel>
</rss>"""
    fake_response = MagicMock(status_code=200, text=xml)
    with patch("findajob.fetchers.adapters.we_work_remotely.requests.get", return_value=fake_response):
        rows = WeWorkRemotelyAdapter().fetch([])
    assert rows[0]["location"] == "Anywhere in the World, California, USA"


def test_fetch_falls_back_to_guid_when_link_missing() -> None:
    xml = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <item>
      <title>X: Y</title>
      <guid>https://weworkremotely.com/jobs/abc</guid>
      <description>d</description>
    </item>
  </channel>
</rss>"""
    fake_response = MagicMock(status_code=200, text=xml)
    with patch("findajob.fetchers.adapters.we_work_remotely.requests.get", return_value=fake_response):
        rows = WeWorkRemotelyAdapter().fetch([])
    assert rows[0]["url"] == "https://weworkremotely.com/jobs/abc"


# ───────────────────── fetch() failure modes ─────────────────────


def test_fetch_returns_empty_on_non_200() -> None:
    fake_response = MagicMock(status_code=503)
    with patch("findajob.fetchers.adapters.we_work_remotely.requests.get", return_value=fake_response):
        rows = WeWorkRemotelyAdapter().fetch([])
    assert rows == []


def test_fetch_returns_empty_on_malformed_xml() -> None:
    fake_response = MagicMock(status_code=200, text="<rss<<><<not valid")
    with patch("findajob.fetchers.adapters.we_work_remotely.requests.get", return_value=fake_response):
        rows = WeWorkRemotelyAdapter().fetch([])
    assert rows == []


def test_fetch_returns_empty_on_missing_channel() -> None:
    """If WWR ever changes the RSS envelope, the adapter returns [] not crashes."""
    fake_response = MagicMock(status_code=200, text='<?xml version="1.0"?><rss version="2.0"></rss>')
    with patch("findajob.fetchers.adapters.we_work_remotely.requests.get", return_value=fake_response):
        rows = WeWorkRemotelyAdapter().fetch([])
    assert rows == []


def test_fetch_returns_empty_on_network_failure() -> None:
    with patch(
        "findajob.fetchers.adapters.we_work_remotely.requests.get",
        side_effect=req.RequestException("dns fail"),
    ):
        rows = WeWorkRemotelyAdapter().fetch([])
    assert rows == []


def test_fetch_retries_after_429() -> None:
    rate_limited = MagicMock(status_code=429, headers={"Retry-After": "1"})
    xml = '<?xml version="1.0"?><rss version="2.0"><channel><item><title>X: Y</title><link>https://x</link></item></channel></rss>'
    ok_response = MagicMock(status_code=200, text=xml)
    with (
        patch(
            "findajob.fetchers.adapters.we_work_remotely.requests.get",
            side_effect=[rate_limited, ok_response],
        ),
        patch("findajob.fetchers.adapters.we_work_remotely.time.sleep"),
    ):
        rows = WeWorkRemotelyAdapter().fetch([])
    assert len(rows) == 1


# ───────────────────── live_test() buckets ─────────────────────


def test_live_test_success_bucket() -> None:
    xml = (
        '<?xml version="1.0"?><rss version="2.0"><channel>'
        "<item><title>A: B</title><link>x</link></item>"
        "<item><title>C: D</title><link>y</link></item>"
        "</channel></rss>"
    )
    fake_response = MagicMock(status_code=200, text=xml)
    with patch("findajob.fetchers.adapters.we_work_remotely.requests.get", return_value=fake_response):
        result = WeWorkRemotelyAdapter().live_test([])
    assert result.ok is True
    assert result.bucket == "success"
    assert result.per_query[0].count == 2


def test_live_test_zero_rows_bucket() -> None:
    fake_response = MagicMock(status_code=200, text='<?xml version="1.0"?><rss version="2.0"><channel></channel></rss>')
    with patch("findajob.fetchers.adapters.we_work_remotely.requests.get", return_value=fake_response):
        result = WeWorkRemotelyAdapter().live_test([])
    assert result.ok is True
    assert result.bucket == "zero_rows"


def test_live_test_rate_limit_bucket() -> None:
    fake_response = MagicMock(status_code=429)
    with patch("findajob.fetchers.adapters.we_work_remotely.requests.get", return_value=fake_response):
        result = WeWorkRemotelyAdapter().live_test([])
    assert result.ok is False
    assert result.bucket == "rate_limit"


def test_live_test_server_bucket_on_5xx() -> None:
    fake_response = MagicMock(status_code=503)
    with patch("findajob.fetchers.adapters.we_work_remotely.requests.get", return_value=fake_response):
        result = WeWorkRemotelyAdapter().live_test([])
    assert result.ok is False
    assert result.bucket == "server"


def test_live_test_network_bucket() -> None:
    with patch(
        "findajob.fetchers.adapters.we_work_remotely.requests.get",
        side_effect=req.RequestException("conn refused"),
    ):
        result = WeWorkRemotelyAdapter().live_test([])
    assert result.ok is False
    assert result.bucket == "network"
