"""Cost-logging regression test for the full triage path (#1145).

findajob.triage.orchestrator.main() scores newly-ingested jobs via
findajob.scoring.score_job. A prefilter hard reject never calls the LLM —
score_job returns completion=None. The cost_log row main() writes for that
job must read cost_usd=0.0, input_tokens=0, output_tokens=0, not a chars/4
heuristic guess.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from tests.conftest import init_test_db


class _FakeAdapter:
    """Minimal JobSourceAdapter double — yields one fixed job, ignores queries."""

    name = "manual_test"

    def __init__(self, jobs: list[dict]) -> None:
        self._jobs = jobs

    def fetch(self, queries: list[str]) -> list[dict]:
        del queries
        return self._jobs


def test_prefilter_zero_cost_triage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A job whose title hits the Stage-1 hard-reject prefilter (tests/fixtures/config
    matches "Office Manager") is scored with no LLM call. The resulting cost_log row
    must be a real zero, not a heuristic estimate from the reconstructed prompt text."""
    import findajob.triage.orchestrator as orchestrator

    db = tmp_path / "pipeline.db"
    init_test_db(db)
    profile = tmp_path / "profile.md"
    profile.write_text("Senior Software Engineer candidate profile.\n")

    job = {
        "title": "Office Manager",
        "company": "Acme Corp",
        "location": "Remote",
        "url": "https://example.com/jobs/office-manager",
        "source": "manual_test",
    }

    monkeypatch.setattr(orchestrator, "DB_PATH", str(db))
    monkeypatch.setattr(orchestrator, "PROFILE_PATH", str(profile))
    monkeypatch.setattr(orchestrator, "_onboarding_is_complete", lambda base: True)
    monkeypatch.setattr(orchestrator, "iter_configured_adapters", lambda: [_FakeAdapter([job])])
    monkeypatch.setattr(orchestrator, "fetch_jd", lambda j: "A usable job description. " * 20)
    monkeypatch.setattr(orchestrator, "find_contacts", lambda company: [])

    orchestrator.main()

    conn = sqlite3.connect(db)
    job_row = conn.execute("SELECT id, scored_by, relevance_score FROM jobs WHERE title='Office Manager'").fetchone()
    assert job_row is not None, "job was not ingested/scored"
    job_id, scored_by, relevance_score = job_row
    assert scored_by == "prefilter_stage1", "expected the Stage-1 hard-reject prefilter to fire, not the LLM"
    assert relevance_score == 1

    cost_row = conn.execute(
        "SELECT cost_usd, input_tokens, output_tokens FROM cost_log WHERE job_id=? AND operation='score'",
        (job_id,),
    ).fetchone()
    assert cost_row is not None, "no cost_log row written for the prefiltered job"
    assert cost_row == (0.0, 0, 0)
