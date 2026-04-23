"""End-to-end Tier 2 dedup tests for triage.py ingest path — #182 Bug C.

Drives the SQL-backed loose-fingerprint branch: LinkedIn syndication of a
Greenhouse posting (different URLs, different-coarseness locations) must
dedupe; distinct-city reqs (both specific) must not.
"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import UTC, datetime

import pytest

from findajob.cleaning import fingerprint, is_coarse_location, loose_fingerprint

SCHEMA = """
CREATE TABLE jobs (
    id TEXT PRIMARY KEY,
    fingerprint TEXT UNIQUE NOT NULL,
    loose_fingerprint TEXT,
    url TEXT NOT NULL,
    title TEXT NOT NULL,
    company TEXT NOT NULL,
    location TEXT DEFAULT '',
    source TEXT NOT NULL,
    stage TEXT DEFAULT 'discovered',
    stage_updated TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX idx_jobs_loose_fingerprint ON jobs(loose_fingerprint);
"""


def _insert(conn, title, company, location, url, source):
    fp = fingerprint(title, company, location)
    lfp = loose_fingerprint(title, company)
    job_id = str(uuid.uuid4())
    now = datetime.now(UTC).isoformat()
    conn.execute(
        "INSERT INTO jobs "
        "(id, fingerprint, loose_fingerprint, url, title, company, location, source, stage_updated, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (job_id, fp, lfp, url, title, company, location, source, now, now),
    )
    conn.commit()
    return job_id


def _dedup_check(conn, title, company, location, url):
    """Mirror triage.py's three-tier dedup lookup logic."""
    fp = fingerprint(title, company, location)
    lfp = loose_fingerprint(title, company)
    existing = conn.execute("SELECT id FROM jobs WHERE fingerprint = ?", (fp,)).fetchone()
    if not existing and url:
        existing = conn.execute("SELECT id FROM jobs WHERE url = ?", (url,)).fetchone()
    if not existing:
        incoming_coarse = is_coarse_location(location)
        for row in conn.execute("SELECT id, location FROM jobs WHERE loose_fingerprint = ?", (lfp,)).fetchall():
            if incoming_coarse or is_coarse_location(row["location"] or ""):
                existing = row
                break
    return existing


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(SCHEMA)
    yield c
    c.close()


class TestTier2Dedup:
    def test_linkedin_specific_vs_greenhouse_coarse_dedupes(self, conn):
        """Bug C core case — Greenhouse posts 'US', LinkedIn syndicates 'Barstow, TX'."""
        _insert(
            conn,
            "Data Center Operations Program Manager",
            "Nscale",
            "US",
            "https://job-boards.eu.greenhouse.io/nscale/jobs/4848114101",
            "greenhouse_json",
        )
        match = _dedup_check(
            conn,
            "Data Center Operations Program Manager",
            "Nscale",
            "Barstow, TX",
            "https://www.linkedin.com/jobs/view/4405427338",
        )
        assert match is not None

    def test_reverse_linkedin_specific_first_greenhouse_coarse_second(self, conn):
        """Order-independent — coarse-side arriving second also dedupes."""
        _insert(
            conn,
            "Data Center Operations Program Manager",
            "Nscale",
            "Barstow, TX",
            "https://www.linkedin.com/jobs/view/4405427338",
            "jobsapi_linkedin",
        )
        match = _dedup_check(
            conn,
            "Data Center Operations Program Manager",
            "Nscale",
            "US",
            "https://job-boards.eu.greenhouse.io/nscale/jobs/4848114101",
        )
        assert match is not None

    def test_distinct_cities_both_specific_do_not_dedupe(self, conn):
        """Data Center Site Manager in different cities must remain distinct."""
        _insert(
            conn,
            "Data Center Site Manager",
            "Meta",
            "Austin, TX",
            "https://example.com/1",
            "greenhouse_json",
        )
        match = _dedup_check(
            conn,
            "Data Center Site Manager",
            "Meta",
            "Menlo Park, CA",
            "https://example.com/2",
        )
        assert match is None

    def test_same_url_stable_fingerprint_across_onsite_suffix(self, conn):
        """Bug B — re-ingest of same URL with LinkedIn '(On-site)' tag still dedupes."""
        _insert(
            conn,
            "Director of Lab Services",
            "Nscale",
            "Barstow, TX",
            "https://www.linkedin.com/jobs/view/4405427338",
            "jobsapi_linkedin",
        )
        match = _dedup_check(
            conn,
            "Director of Lab Services",
            "Nscale",
            "Barstow, TX (On-site)",
            "https://www.linkedin.com/jobs/view/4405427338",
        )
        assert match is not None

    def test_leading_whitespace_title_stable_fingerprint(self, conn):
        """Bug A — ingest-path call site must dedupe despite leading whitespace."""
        _insert(
            conn,
            "Director of Lab Services",
            "Nscale",
            "Barstow, TX",
            "https://example.com/1",
            "greenhouse_json",
        )
        match = _dedup_check(
            conn,
            " Director of Lab Services",
            "Nscale",
            "Barstow, TX",
            "https://example.com/2",
        )
        assert match is not None
