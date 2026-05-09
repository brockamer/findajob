"""Matcher tests — alias resolution, seniority gate, and the
``_POST_APPLICATION_STAGES`` lockstep regression."""

from __future__ import annotations

import inspect
import sqlite3

import pytest

from findajob.rejection_detector import matcher
from findajob.rejection_detector.matcher import MatchResult, match_job, resolve_aliases


@pytest.fixture
def conn() -> sqlite3.Connection:
    """In-memory DB with a minimal `jobs` shape covering the matcher's SELECT."""
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute(
        """
        CREATE TABLE jobs (
            id TEXT PRIMARY KEY,
            company TEXT NOT NULL,
            title TEXT NOT NULL,
            stage TEXT NOT NULL,
            synthetic INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    return c


def _insert(
    conn: sqlite3.Connection,
    *,
    id: str,
    company: str,
    title: str,
    stage: str = "applied",
    synthetic: int = 0,
) -> None:
    conn.execute(
        "INSERT INTO jobs(id, company, title, stage, synthetic) VALUES(?, ?, ?, ?, ?)",
        (id, company, title, stage, synthetic),
    )


def test_no_extracted_company_returns_unmatched(conn: sqlite3.Connection) -> None:
    result = match_job(conn, extracted_company=None, extracted_role=None, received_at="")
    assert result == MatchResult(job_id=None, status="unmatched")


def test_single_match_returns_job_id(conn: sqlite3.Connection) -> None:
    _insert(conn, id="j1", company="ExampleCo", title="Datacenter Manager")
    result = match_job(conn, extracted_company="ExampleCo", extracted_role=None, received_at="")
    assert result == MatchResult(job_id="j1", status="matched")


def test_zero_match_returns_unmatched(conn: sqlite3.Connection) -> None:
    _insert(conn, id="j1", company="OtherCo", title="X")
    result = match_job(conn, extracted_company="ExampleCo", extracted_role=None, received_at="")
    assert result == MatchResult(job_id=None, status="unmatched")


def test_synthetic_jobs_excluded(conn: sqlite3.Connection) -> None:
    _insert(conn, id="j1", company="ExampleCo", title="X", synthetic=1)
    result = match_job(conn, extracted_company="ExampleCo", extracted_role=None, received_at="")
    assert result.status == "unmatched"


def test_non_post_application_stage_excluded(conn: sqlite3.Connection) -> None:
    _insert(conn, id="j1", company="ExampleCo", title="X", stage="scored")
    _insert(conn, id="j2", company="ExampleCo", title="X", stage="materials_drafted")
    result = match_job(conn, extracted_company="ExampleCo", extracted_role=None, received_at="")
    assert result.status == "unmatched"


def test_role_narrows_when_company_ambiguous(conn: sqlite3.Connection) -> None:
    _insert(conn, id="j1", company="ExampleCo", title="Datacenter Manager")
    _insert(conn, id="j2", company="ExampleCo", title="Software Engineer")
    result = match_job(
        conn,
        extracted_company="ExampleCo",
        extracted_role="Datacenter Manager",
        received_at="",
    )
    assert result == MatchResult(job_id="j1", status="matched")


def test_seniority_gate_disambiguates_senior_vs_staff(conn: sqlite3.Connection) -> None:
    """Spec §4.2.2: when narrowing within a company, seniority tokens must agree.

    Concrete corpus case: operator applied to "Senior Engineer, Datacenter
    Server Lifecycle" — the rejection email was for the "Staff Engineer"
    auto-discovered posting. Without the seniority gate, token-set ratio
    would mis-attribute the Staff rejection to the Senior application.
    """
    _insert(conn, id="senior", company="ExampleCo", title="Senior Engineer, Datacenter Server Lifecycle")
    _insert(conn, id="staff", company="ExampleCo", title="Staff Engineer, Datacenter Server Lifecycle")
    result = match_job(
        conn,
        extracted_company="ExampleCo",
        extracted_role="Staff Engineer, Datacenter Server Lifecycle",
        received_at="",
    )
    assert result == MatchResult(job_id="staff", status="matched")


def test_ambiguous_when_role_does_not_disambiguate(conn: sqlite3.Connection) -> None:
    _insert(conn, id="j1", company="ExampleCo", title="Datacenter Manager")
    _insert(conn, id="j2", company="ExampleCo", title="Datacenter Operations Manager")
    result = match_job(
        conn,
        extracted_company="ExampleCo",
        extracted_role=None,  # No role provided → can't narrow
        received_at="",
    )
    assert result.status == "ambiguous"


def test_resolve_aliases_canonical_to_alias() -> None:
    """resolve_aliases is symmetric: feed canonical, get alias as a candidate."""
    aliases = {"cobot": "collaborative robotics"}
    candidates = resolve_aliases("Collaborative Robotics", aliases)
    assert "cobot" in candidates
    assert "collaborative robotics" in candidates


def test_resolve_aliases_alias_to_canonical() -> None:
    aliases = {"cobot": "collaborative robotics"}
    candidates = resolve_aliases("Cobot Hiring Team", aliases)
    assert "collaborative robotics" in candidates


def test_resolve_aliases_word_boundary() -> None:
    """'cobotics inc' must NOT resolve to 'collaborative robotics' on naive substring match."""
    aliases = {"cobot": "collaborative robotics"}
    candidates = resolve_aliases("Cobotics Inc", aliases)
    assert "collaborative robotics" not in candidates


def test_matcher_source_stages_lockstep_with_route_handler() -> None:
    """The matcher's source-stage filter must equal _POST_APPLICATION_STAGES.

    Divergence produces suggestions the operator clicks but cannot confirm
    (the not-selected route 409s any non-_POST_APPLICATION_STAGES source).
    Brittle on purpose — that's the point.
    """
    from findajob.web.routes.board_actions import _POST_APPLICATION_STAGES

    src = inspect.getsource(matcher.match_job)
    expected_clause = "stage IN (" + ", ".join(f"'{s}'" for s in _POST_APPLICATION_STAGES) + ")"
    assert expected_clause in src, (
        f"matcher.py SQL clause has drifted from _POST_APPLICATION_STAGES (expected substring: {expected_clause!r})"
    )
