"""Tests for findajob.speculative.runner — orchestrates briefing + role-synth.

aichat-ng subprocess is mocked. We assert the runner:
1. Reads the speculative_requests row and candidate context files
2. Calls the briefing role, then the synth role with the briefing as input
3. Writes briefing.md to a freshly-created folder
4. Updates the request row to status='ready_for_review' with briefing_md +
   role_cards_json + briefing_folder + research_completed_at populated
5. On any failure, sets status='failed' + error_message
"""

from __future__ import annotations

import json
import sqlite3
from unittest.mock import patch

from findajob.speculative.runner import run_research

SCHEMA = """
CREATE TABLE speculative_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company TEXT NOT NULL,
    hint TEXT,
    personal_notes TEXT,
    status TEXT NOT NULL DEFAULT 'researching',
    error_message TEXT,
    briefing_md TEXT,
    role_cards_json TEXT,
    briefing_folder TEXT,
    submitted_at TEXT NOT NULL DEFAULT (datetime('now')),
    research_completed_at TEXT,
    approved_at TEXT,
    approved_role_count INTEGER,
    briefing_prompt_version TEXT,
    synth_prompt_version TEXT
);
"""


def _seed(conn: sqlite3.Connection) -> int:
    cur = conn.execute(
        "INSERT INTO speculative_requests (company, hint, personal_notes, status) VALUES (?, ?, ?, 'researching')",
        ("PSIQuantum", "advanced computing infrastructure", None),
    )
    conn.commit()
    return cur.lastrowid


def _ok_briefing() -> str:
    return "# briefing\n\n## 🏢 Company Snapshot\nbody\n"


def _ok_role_cards() -> str:
    return json.dumps(
        [
            {
                "title": "Critical Infrastructure Engineer",
                "description": "Own GPU cluster bring-up.",
                "why_this_fits_candidate": "FTW Lab analog.",
                "likely_team_or_org": "Site Operations",
                "suggested_contact_type": "hiring_manager",
            }
        ]
    )


def test_run_research_happy_path(tmp_path):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    req_id = _seed(conn)

    profile = tmp_path / "profile.md"
    profile.write_text("candidate profile body")
    resume = tmp_path / "master_resume.md"
    resume.write_text("master resume body")

    with patch("findajob.speculative.runner._call_aichat") as mock_call:
        mock_call.side_effect = [_ok_briefing(), _ok_role_cards()]
        run_research(
            conn=conn,
            request_id=req_id,
            profile_path=profile,
            master_resume_path=resume,
            companies_dir=tmp_path / "companies",
        )

    assert mock_call.call_count == 2
    # First call is candidate_led_briefing, second is speculative_roles_synth
    first_role = mock_call.call_args_list[0][0][0]
    second_role = mock_call.call_args_list[1][0][0]
    assert first_role == "candidate_led_briefing"
    assert second_role == "speculative_roles_synth"

    # Row updated
    row = conn.execute("SELECT * FROM speculative_requests WHERE id=?", (req_id,)).fetchone()
    assert row["status"] == "ready_for_review"
    assert row["briefing_md"] == _ok_briefing()
    assert row["role_cards_json"] == _ok_role_cards()
    assert row["briefing_folder"] is not None
    assert row["research_completed_at"] is not None

    # Folder + briefing.md exist on disk
    folder = tmp_path / "companies" / row["briefing_folder"]
    assert folder.exists()
    assert (folder / "briefing.md").read_text() == _ok_briefing()


def test_run_research_briefing_failure_sets_status_failed(tmp_path):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    req_id = _seed(conn)

    profile = tmp_path / "profile.md"
    profile.write_text("p")
    resume = tmp_path / "master_resume.md"
    resume.write_text("r")

    with patch("findajob.speculative.runner._call_aichat") as mock_call:
        mock_call.side_effect = RuntimeError("aichat-ng exit 1: rate limited")
        run_research(
            conn=conn,
            request_id=req_id,
            profile_path=profile,
            master_resume_path=resume,
            companies_dir=tmp_path / "companies",
        )

    row = conn.execute("SELECT * FROM speculative_requests WHERE id=?", (req_id,)).fetchone()
    assert row["status"] == "failed"
    assert "rate limited" in (row["error_message"] or "")
    assert row["briefing_md"] is None
    assert row["role_cards_json"] is None


def test_run_research_synth_failure_preserves_briefing(tmp_path):
    """If briefing succeeds but role-synth fails, briefing_md is preserved
    in the row so a retry only re-runs the synth step."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    req_id = _seed(conn)

    profile = tmp_path / "profile.md"
    profile.write_text("p")
    resume = tmp_path / "master_resume.md"
    resume.write_text("r")

    with patch("findajob.speculative.runner._call_aichat") as mock_call:
        mock_call.side_effect = [_ok_briefing(), RuntimeError("synth failed: invalid JSON")]
        run_research(
            conn=conn,
            request_id=req_id,
            profile_path=profile,
            master_resume_path=resume,
            companies_dir=tmp_path / "companies",
        )

    row = conn.execute("SELECT * FROM speculative_requests WHERE id=?", (req_id,)).fetchone()
    assert row["status"] == "failed"
    assert row["briefing_md"] == _ok_briefing()
    assert row["role_cards_json"] is None
    assert "synth failed" in (row["error_message"] or "")
