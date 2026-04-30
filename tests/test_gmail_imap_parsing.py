"""Tier 3 — round-trip parsing of captured Gmail alerts via the IMAP-native parser."""

from __future__ import annotations

import email
from pathlib import Path

from findajob.fetchers import parse_jobs_from_email_imap

FIXTURES = Path(__file__).parent / "fixtures" / "gmail"


def test_linkedin_alert_extracts_at_least_one_job():
    raw = (FIXTURES / "linkedin_alert.eml").read_bytes()
    msg = email.message_from_bytes(raw)
    jobs = parse_jobs_from_email_imap(msg)
    assert len(jobs) >= 1
    for job in jobs:
        assert "title" in job
        assert "company" in job
        assert "url" in job
        assert job["url"].startswith("http")


def test_linkedin_alert_skips_navigation_labels():
    """The SKIP_LABELS set should filter out 'View Job', 'Apply Now', etc."""
    raw = (FIXTURES / "linkedin_alert.eml").read_bytes()
    msg = email.message_from_bytes(raw)
    jobs = parse_jobs_from_email_imap(msg)
    bad_labels = {"View Job", "Apply Now", "Unsubscribe", "View All Jobs"}
    for job in jobs:
        assert job["title"] not in bad_labels


def test_parse_handles_plain_text_only_message():
    """A plain-text message with no HTML should return an empty list, not crash."""
    msg = email.message_from_string(
        "From: jobalerts-noreply@linkedin.com\r\n"
        "To: tester@example.com\r\n"
        "Subject: test\r\n"
        "Content-Type: text/plain\r\n\r\n"
        "no html here"
    )
    jobs = parse_jobs_from_email_imap(msg)
    assert jobs == []
