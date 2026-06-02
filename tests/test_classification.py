"""Tests for the pure classifier predicates in findajob.classification.

Covers all six public functions: jd_is_usable, is_aggregator_company,
is_valid_company, is_ingest_noise_title, is_synthetic_job, and
strip_jd_boilerplate. (Issue #884 said "seven" and listed five; the
module exposes six — is_valid_company is the wrapper it omitted.)
"""

from __future__ import annotations

import sqlite3

import pytest

from findajob.classification import (
    is_aggregator_company,
    is_ingest_noise_title,
    is_synthetic_job,
    is_valid_company,
    jd_is_usable,
    strip_jd_boilerplate,
)

# ── jd_is_usable ────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "jd_text,expected",
    [
        (None, False),
        ("", False),
        ("   ", False),
        ("too short", False),  # < 30 chars after strip
        ("This is a perfectly usable job description with detail.", True),
        # Wall signals (case-insensitive) flip a long-enough string to unusable.
        ("Please sign in to view this posting, it is quite long now.", False),
        ("ACCESS DENIED — you do not have permission to view this job.", False),
        ("You need to enable JavaScript to run this app on our site here.", False),
    ],
)
def test_jd_is_usable(jd_text: str | None, expected: bool) -> None:
    assert jd_is_usable(jd_text) is expected


# ── is_aggregator_company ───────────────────────────────────────────


@pytest.mark.parametrize(
    "company,expected",
    [
        (None, False),
        ("", False),
        ("Acme Corp", False),
        ("Jobs via LinkedIn", True),
        ("job via Indeed", True),
        ("Posted via Workday", True),
        ("Adecco Staffing", True),
        ("ADECCO", True),  # case-insensitive
        ("  Randstad", True),  # leading whitespace stripped
        ("Robert Half", True),
    ],
)
def test_is_aggregator_company(company: str | None, expected: bool) -> None:
    assert is_aggregator_company(company) is expected


# ── is_valid_company ────────────────────────────────────────────────


@pytest.mark.parametrize(
    "company,expected",
    [
        (None, False),
        ("", False),
        ("   ", False),  # blank-after-strip is invalid
        ("Acme Corp", True),
        ("Adecco", False),  # aggregator is not valid
    ],
)
def test_is_valid_company(company: str | None, expected: bool) -> None:
    assert is_valid_company(company) is expected


# ── is_ingest_noise_title ───────────────────────────────────────────


@pytest.mark.parametrize(
    "title,expected",
    [
        (None, False),
        ("", False),
        ("Senior Software Engineer", False),
        ("Jobs similar to Data Center Technician", True),
        ("JOBS SIMILAR to anything", True),  # case-insensitive prefix
        ("Job similar to", True),  # exact-match variant
        ("  jobs similar to X  ", True),  # whitespace stripped
    ],
)
def test_is_ingest_noise_title(title: str | None, expected: bool) -> None:
    assert is_ingest_noise_title(title) is expected


# ── is_synthetic_job ────────────────────────────────────────────────


def _sqlite_row(**cols: object) -> sqlite3.Row:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    keys = ", ".join(f"? AS {k}" for k in cols)
    return conn.execute(f"SELECT {keys}", tuple(cols.values())).fetchone()


@pytest.mark.parametrize(
    "job,expected",
    [
        (None, False),
        ({}, False),
        ({"synthetic": 1}, True),
        ({"synthetic": 0}, False),
        ({"synthetic": "1"}, True),  # truthy string
        ({"synthetic": "0"}, False),
        ({"synthetic": None}, False),
        ({"other": 1}, False),  # missing key -> not synthetic
    ],
)
def test_is_synthetic_job_dict(job: object, expected: bool) -> None:
    assert is_synthetic_job(job) is expected


def test_is_synthetic_job_sqlite_row_truthy() -> None:
    # sqlite3.Row supports __getitem__ but NOT .get() — exercises the
    # fallback branch that exists specifically for this row type.
    assert is_synthetic_job(_sqlite_row(synthetic=1)) is True


def test_is_synthetic_job_sqlite_row_falsey() -> None:
    assert is_synthetic_job(_sqlite_row(synthetic=0)) is False


def test_is_synthetic_job_sqlite_row_missing_column() -> None:
    # No 'synthetic' column -> row[...] raises IndexError -> not synthetic.
    assert is_synthetic_job(_sqlite_row(other=1)) is False


# ── strip_jd_boilerplate ────────────────────────────────────────────

_REAL = (
    "We are hiring a data center infrastructure engineer to own server "
    "and accelerator bring-up across our fleet. You will partner with "
    "hardware, software, and operations teams to validate new platforms "
    "from first power-on through production release. Strong NPI and lab "
    "experience is highly valued in this role."
)
_EEO = "We are an equal opportunity employer and do not discriminate."


def test_strip_none_returns_empty_string() -> None:
    assert strip_jd_boilerplate(None) == ""


def test_strip_short_text_unchanged() -> None:
    short = "Short JD under two hundred chars."
    assert strip_jd_boilerplate(short) == short


def test_strip_single_block_unchanged() -> None:
    # No double-newline -> single paragraph -> never risk stripping it.
    single = _REAL + " " + _EEO
    assert strip_jd_boilerplate(single) == single


def test_strip_trailing_boilerplate_removed() -> None:
    text = _REAL + "\n\n" + _EEO
    result = strip_jd_boilerplate(text)
    assert "equal opportunity employer" not in result
    assert result == _REAL


def test_strip_stops_at_real_content() -> None:
    middle = "The team works on-site three days a week in Los Angeles."
    text = _REAL + "\n\n" + middle + "\n\n" + _EEO
    result = strip_jd_boilerplate(text)
    # EEO trimmed, but the non-boilerplate middle paragraph is retained.
    assert "equal opportunity employer" not in result
    assert middle in result


def test_strip_respects_minimum_retain() -> None:
    # Trimming the huge trailing boilerplate would drop > 40% of the text,
    # so the safety bound returns the original untouched.
    real_short = "We are hiring an infrastructure engineer for fleet bring-up work here." * 3
    huge_boilerplate = "equal opportunity employer affirmative action " * 30
    text = real_short + "\n\n" + huge_boilerplate
    result = strip_jd_boilerplate(text)
    assert result == text
    assert "equal opportunity employer" in result
