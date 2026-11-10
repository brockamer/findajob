"""Prefilter zero-cost logging through the full triage path (#1145).

A pre-filter-rejected job never calls the LLM — ``score_job`` returns
``completion=None``. Before #1145, ``orchestrator.main()``'s log_call site
passed ``cost_usd_override=None`` on that path, and ``log_call`` silently
estimated a non-zero cost from the chars/4 heuristic against the
reconstructed scoring prompt. The fix hands log_call an explicit 0.0/0
instead, so the written row always reflects the real (zero) spend.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from tests.conftest import init_test_db


class _FakeAdapter:
    name = "fake"

    def fetch(self, queries: list[str]) -> list[dict]:
        return [
            {
                "title": "Software Engineer",  # matches the fixture hard-reject regex
                "company": "Acme",
                "location": "Remote",
                "url": "https://example.com/job1",
                "source": "manual",
            }
        ]


def test_prefilter_zero_cost_triage(tmp_path: Path, monkeypatch) -> None:
    """Scoring a prefilter-hard-rejected job via orchestrator.main() writes a
    cost_log row with cost_usd=0.0, input_tokens=0, output_tokens=0."""
    from findajob.triage import orchestrator

    db = tmp_path / "pipeline.db"
    init_test_db(db)

    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    (tmp_path / "data" / ".onboarding-complete").write_text("2026-01-01T00:00:00Z\n")
    profile = tmp_path / "profile.md"
    profile.write_text("Test candidate profile.\n")

    monkeypatch.setattr(orchestrator, "BASE", str(tmp_path))
    monkeypatch.setattr(orchestrator, "DB_PATH", str(db))
    monkeypatch.setattr(orchestrator, "PROFILE_PATH", str(profile))
    monkeypatch.setattr(orchestrator, "iter_configured_adapters", lambda: iter([_FakeAdapter()]))
    monkeypatch.setattr(orchestrator, "fetch_jd", lambda job: "A usable job description. " * 10)
    monkeypatch.setattr(orchestrator, "find_contacts", lambda company: [])

    orchestrator.main()

    conn = sqlite3.connect(db)
    row = conn.execute("SELECT cost_usd, input_tokens, output_tokens FROM cost_log WHERE operation='score'").fetchone()
    conn.close()

    assert row is not None, "no cost_log row written for the prefilter-rejected job"
    assert row == (0.0, 0, 0)


class _NonRejectedAdapter:
    name = "fake"

    def fetch(self, queries: list[str]) -> list[dict]:
        return [
            {
                "title": "Product Manager",  # doesn't match any fixture hard-reject regex
                "company": "Acme",
                "location": "Remote",
                "url": "https://example.com/job1",
                "source": "manual",
            }
        ]


def _run_triage_with_scorer_error(tmp_path: Path, monkeypatch, raise_error) -> sqlite3.Row:
    """Run orchestrator.main() against one non-prefiltered job whose LLM call
    raises ``raise_error``. Returns the cost_log row for the score operation."""
    from findajob.triage import orchestrator

    db = tmp_path / "pipeline.db"
    init_test_db(db)

    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    (tmp_path / "data" / ".onboarding-complete").write_text("2026-01-01T00:00:00Z\n")
    profile = tmp_path / "profile.md"
    profile.write_text("Test candidate profile.\n")

    monkeypatch.setattr(orchestrator, "BASE", str(tmp_path))
    monkeypatch.setattr(orchestrator, "DB_PATH", str(db))
    monkeypatch.setattr(orchestrator, "PROFILE_PATH", str(profile))
    monkeypatch.setattr(orchestrator, "iter_configured_adapters", lambda: iter([_NonRejectedAdapter()]))
    monkeypatch.setattr(orchestrator, "fetch_jd", lambda job: "A usable job description. " * 10)
    monkeypatch.setattr(orchestrator, "find_contacts", lambda company: [])
    monkeypatch.setattr("findajob.scoring.complete", raise_error)

    orchestrator.main()

    conn = sqlite3.connect(db)
    row = conn.execute("SELECT cost_usd, input_tokens, output_tokens FROM cost_log WHERE operation='score'").fetchone()
    conn.close()

    assert row is not None, "no cost_log row written for the scorer-error job"
    return row


def test_triage_billed_scorer_failure_cost(tmp_path: Path, monkeypatch) -> None:
    """A scorer failure that OpenRouter billed for (null content on a
    finish_reason=length truncation) must log the real cost and token
    counts, not a 0.0/0/0 undercount (#1282)."""
    from findajob.llm.openrouter import OpenRouterError

    def _raise_billed(**kw):
        raise OpenRouterError(
            "null content, finish_reason=length",
            kind="malformed",
            finish_reason="length",
            prompt_tokens=111,
            completion_tokens=222,
            cost_usd=0.0456,
        )

    row = _run_triage_with_scorer_error(tmp_path, monkeypatch, _raise_billed)
    assert row == (0.0456, 111, 222)


def test_unbilled_scorer_error_zero_cost(tmp_path: Path, monkeypatch) -> None:
    """A pre-response scorer error (network/auth/config) never billed, so the
    cost_log row must still carry an explicit 0.0/0/0 — mirrors the
    prefilter zero-cost behavior above (#1282)."""
    from findajob.llm.openrouter import OpenRouterError

    def _raise_unbilled(**kw):
        raise OpenRouterError("connection refused", kind="network")

    row = _run_triage_with_scorer_error(tmp_path, monkeypatch, _raise_unbilled)
    assert row == (0.0, 0, 0)
