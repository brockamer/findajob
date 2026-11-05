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
