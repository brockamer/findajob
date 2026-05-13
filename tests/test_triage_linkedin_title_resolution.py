"""Tests for `_resolve_degenerate_linkedin_title` (#656).

Acceptance criterion from the issue body:
    *"feed a gmail_linkedin job with title=<URL> and a mocked
    fetch_linkedin_job_data response that includes a real title;
    assert the **stored** title is the real one."*

The unit tests in `test_fetchers_linkedin_title.py` cover the in-memory
cache (`job["_linkedin_title"]`). These tests exercise the orchestrator
helper that actually writes the title to `jobs.title` in SQLite.
"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import UTC, datetime

import pytest

from findajob.cleaning import fingerprint, loose_fingerprint
from findajob.triage.orchestrator import _resolve_degenerate_linkedin_title


@pytest.fixture()
def conn() -> sqlite3.Connection:
    """In-memory SQLite with the columns the helper writes to.

    Doesn't replicate the full schema — only what's needed for the SELECT
    in the dedupe check, the UPDATE in the autofix path, and the
    audit_log row written via write_audit().
    """
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(
        """
        CREATE TABLE jobs (
            id TEXT PRIMARY KEY,
            fingerprint TEXT,
            loose_fingerprint TEXT,
            url TEXT,
            title TEXT,
            company TEXT,
            location TEXT,
            source TEXT,
            stage TEXT,
            stage_updated TEXT,
            updated_at TEXT,
            dupe_of TEXT DEFAULT ''
        );
        CREATE TABLE audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id TEXT NOT NULL,
            field_changed TEXT NOT NULL,
            old_value TEXT,
            new_value TEXT,
            changed_at TEXT DEFAULT (datetime('now')),
            changed_by TEXT DEFAULT 'system'
        );
        """
    )
    return c


def _insert(conn: sqlite3.Connection, **cols) -> str:
    """Insert a jobs row and return its id."""
    job_id = cols.setdefault("id", str(uuid.uuid4()))
    cols.setdefault("source", "gmail_linkedin")
    cols.setdefault("stage", "discovered")
    cols.setdefault("location", "")
    cols["fingerprint"] = fingerprint(cols["title"], cols.get("company", ""), cols.get("location", ""))
    cols["loose_fingerprint"] = loose_fingerprint(cols["title"], cols.get("company", ""))
    keys = ", ".join(cols.keys())
    placeholders = ", ".join(["?"] * len(cols))
    conn.execute(f"INSERT INTO jobs ({keys}) VALUES ({placeholders})", tuple(cols.values()))
    conn.commit()
    return job_id


def test_degenerate_url_title_swapped_in_db(conn: sqlite3.Connection) -> None:
    """URL-as-title row gets the real title written to jobs.title."""
    url = "https://www.linkedin.com/jobs/view/4341101773/"
    job_id = _insert(conn, title=url, company="Lambda", url=url)
    job = {
        "source": "gmail_linkedin",
        "title": url,
        "company": "Lambda",
        "url": url,
        "location": "",
        "_linkedin_title": "ML Compiler Engineer",
    }

    was_dupe = _resolve_degenerate_linkedin_title(conn, job, job_id, datetime.now(UTC).isoformat())

    assert was_dupe is False
    stored = conn.execute("SELECT title, fingerprint, loose_fingerprint FROM jobs WHERE id=?", (job_id,)).fetchone()
    # Positive: stored title is the real title
    assert stored["title"] == "ML Compiler Engineer"
    # Negative: URL leakage is gone (pair-positive-with-negative regression guard)
    assert "linkedin.com" not in stored["title"]
    assert "4341101773" not in stored["title"]
    # Fingerprint was recomputed against the new title (sanity: not the old fp)
    assert stored["fingerprint"] == fingerprint("ML Compiler Engineer", "Lambda", "")
    assert stored["loose_fingerprint"] == loose_fingerprint("ML Compiler Engineer", "Lambda")
    # In-memory dict also reflects the swap (helper mutates job)
    assert job["title"] == "ML Compiler Engineer"


def test_real_incoming_title_left_untouched(conn: sqlite3.Connection) -> None:
    """When the stored title is not degenerate, the helper is a no-op even if
    _linkedin_title is populated."""
    job_id = _insert(conn, title="Hardware Reliability Engineer", company="Lambda", url="https://x/y")
    job = {
        "source": "gmail_linkedin",
        "title": "Hardware Reliability Engineer",
        "company": "Lambda",
        "url": "https://x/y",
        "location": "",
        "_linkedin_title": "Something Else",  # cache is set but degeneracy check fails
    }

    was_dupe = _resolve_degenerate_linkedin_title(conn, job, job_id, datetime.now(UTC).isoformat())

    assert was_dupe is False
    stored = conn.execute("SELECT title FROM jobs WHERE id=?", (job_id,)).fetchone()
    assert stored["title"] == "Hardware Reliability Engineer"
    # In-memory dict also untouched
    assert job["title"] == "Hardware Reliability Engineer"


def test_no_cached_title_graceful_fallback(conn: sqlite3.Connection) -> None:
    """Degenerate title but _linkedin_title=None → row stays as-is, no crash."""
    url = "https://www.linkedin.com/jobs/view/4341101773/"
    job_id = _insert(conn, title=url, company="Lambda", url=url)
    job = {
        "source": "gmail_linkedin",
        "title": url,
        "company": "Lambda",
        "url": url,
        "location": "",
        # _linkedin_title intentionally absent (API returned no title)
    }

    was_dupe = _resolve_degenerate_linkedin_title(conn, job, job_id, datetime.now(UTC).isoformat())

    assert was_dupe is False
    stored = conn.execute("SELECT title FROM jobs WHERE id=?", (job_id,)).fetchone()
    # Title is unchanged — degenerate is no worse than crashing
    assert stored["title"] == url


def test_non_gmail_linkedin_source_skipped(conn: sqlite3.Connection) -> None:
    """jobsapi_linkedin (and other sources) bypass the helper entirely."""
    job_id = _insert(conn, title="Engineer", company="Lambda", url="https://x/y", source="jobsapi_linkedin")
    job = {
        "source": "jobsapi_linkedin",
        "title": "Engineer",  # would be degenerate by length (< 6 chars + lower)
        "company": "Lambda",
        "url": "https://x/y",
        "location": "",
        "_linkedin_title": "ML Compiler Engineer",
    }

    was_dupe = _resolve_degenerate_linkedin_title(conn, job, job_id, datetime.now(UTC).isoformat())

    assert was_dupe is False
    stored = conn.execute("SELECT title FROM jobs WHERE id=?", (job_id,)).fetchone()
    # jobsapi_linkedin source short-circuits before degeneracy check
    assert stored["title"] == "Engineer"


def test_collision_with_real_title_row_marks_dupe(conn: sqlite3.Connection) -> None:
    """If a row with the real title already exists, this one is marked dupe."""
    # Pre-existing row that already carries the real title — different URL,
    # so the original ingest didn't dedupe against it (different fingerprint).
    existing_id = _insert(
        conn,
        title="ML Compiler Engineer",
        company="Lambda",
        url="https://example.com/lambda-mle",
    )
    # Incoming degenerate-title row
    url = "https://www.linkedin.com/jobs/view/4341101773/"
    new_id = _insert(conn, title=url, company="Lambda", url=url)
    job = {
        "source": "gmail_linkedin",
        "title": url,
        "company": "Lambda",
        "url": url,
        "location": "",
        "_linkedin_title": "ML Compiler Engineer",
    }

    was_dupe = _resolve_degenerate_linkedin_title(conn, job, new_id, datetime.now(UTC).isoformat())

    assert was_dupe is True
    # The new row was marked as dupe of the existing one
    stored = conn.execute("SELECT title, stage, dupe_of FROM jobs WHERE id=?", (new_id,)).fetchone()
    assert stored["dupe_of"] == existing_id
    assert stored["stage"] == "rejected"
    # The pre-existing row is untouched
    pre = conn.execute("SELECT title, stage FROM jobs WHERE id=?", (existing_id,)).fetchone()
    assert pre["title"] == "ML Compiler Engineer"
    assert pre["stage"] == "discovered"
