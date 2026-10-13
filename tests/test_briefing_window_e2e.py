"""#1222 end to end: flag → briefing → window passes → briefing still shown → re-flag.

Drives the real pieces in sequence — the /prep route, Phase A in
``prep/orchestrator.py``, and ``scripts/watchdog.py``'s ``main()`` — against one
on-disk database. Only the LLM (``run_role``), docx rendering, ntfy and the
subprocess launch are faked; the test calls Phase A itself where the route
would have spawned it.
"""

from __future__ import annotations

import os
import sqlite3
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from findajob import actions, audit
from findajob.db.migrate import apply_pending
from findajob.onboarding import mark_complete
from findajob.web.app import create_app

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import watchdog  # noqa: E402

JOB_ID = "id_e2e"
FP = "fp_e2e"
COMPANY = "Acme Lab Services"
TITLE = "Director of Lab Services"
URL = "https://example.com/job/e2e"
PHASE_A_ROLES = {"company_researcher", "briefing_writer", "fit_analyst"}

_FAKE_OUTPUT = {
    "company_researcher": "Raw research text.",
    "briefing_writer": "# Briefing\n\nBody.\n\n## Overall Recommendation: Apply\n",
    "fit_analyst": "## Fit Matrix\n- Skills: 80%\n\n## 🎯 Probability Assessment\n- Interview: 60%\n",
}


class _KeepOpen:
    """watchdog.main() closes its connection; keep the test's open."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def __getattr__(self, name: str):  # type: ignore[no-untyped-def]
        return getattr(self._conn, name)

    def close(self) -> None:
        pass


@pytest.fixture()
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import findajob.prep.orchestrator as orch
    from findajob.web.routes import board_actions

    candidate = tmp_path / "candidate_context"
    candidate.mkdir()
    (candidate / "profile.md").write_text("# Profile\n\nFile prefix: TST\n")
    (candidate / "master_resume.md").write_text("# Resume\n")
    companies = tmp_path / "companies"
    companies.mkdir()
    (tmp_path / "data").mkdir()
    db_path = tmp_path / "data" / "pipeline.db"
    conn = sqlite3.connect(db_path)
    apply_pending(conn)
    conn.execute(
        "INSERT INTO jobs (id, fingerprint, url, title, company, source, stage, relevance_score, raw_jd_text) "
        "VALUES (?, ?, ?, ?, ?, 'test', 'scored', 8, ?)",
        (JOB_ID, FP, URL, TITLE, COMPANY, "We need a director with ten years of lab operations experience."),
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(audit, "LOG_PATH", str(tmp_path / "pipeline.jsonl"))
    monkeypatch.setattr(actions, "BASE", str(tmp_path))
    monkeypatch.setattr(board_actions, "BASE", str(tmp_path))
    monkeypatch.setattr(watchdog, "BASE", str(tmp_path))
    monkeypatch.delenv(watchdog.BRIEFING_READY_WINDOW_ENV, raising=False)

    launches: list[list[str]] = []

    class _FakePopen:
        pid = 99999

        def __init__(self, args, **_kw):
            launches.append(args)

    monkeypatch.setattr(board_actions.subprocess, "Popen", _FakePopen)
    monkeypatch.setattr(actions.subprocess, "Popen", _FakePopen)

    llm_calls: list[str] = []

    def _run_role(role: str, _prompt: str, **_kw) -> str:
        llm_calls.append(role)
        return _FAKE_OUTPUT.get(role, f"Fake output for {role}")

    monkeypatch.setattr(orch, "BASE", str(tmp_path))
    monkeypatch.setattr(orch, "DB_PATH", str(db_path))
    monkeypatch.setattr(orch, "PROFILE_PATH", str(candidate / "profile.md"))
    monkeypatch.setattr(orch, "MASTER_RESUME_PATH", str(candidate / "master_resume.md"))
    monkeypatch.setattr(orch, "run_role", _run_role)
    monkeypatch.setattr(orch, "render_md_to_docx", lambda *a, **kw: None)
    monkeypatch.setattr(orch, "ntfy_send", lambda *a, **kw: None)
    monkeypatch.setattr(orch, "read_file_prefix", lambda: "TST")

    mark_complete(tmp_path)
    client = TestClient(
        create_app(companies_root=companies, db_path=db_path, base_root=tmp_path),
        headers={"HX-Request": "true"},
    )
    return {
        "client": client,
        "db_path": db_path,
        "companies": companies,
        "launches": launches,
        "llm_calls": llm_calls,
        "orch": orch,
    }


def _job(db_path: Path) -> sqlite3.Row:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT stage, prep_folder_path FROM jobs WHERE id=?", (JOB_ID,)).fetchone()
    conn.close()
    return row


def _prep_launches(launches: list[list[str]]) -> list[list[str]]:
    return [a for a in launches if "prep_application.py" in a[1]]


def test_briefing_survives_the_window_and_reflag_pays_phase_a_once(env, monkeypatch) -> None:
    client, db_path, companies = env["client"], env["db_path"], env["companies"]

    # 1. Flag for Prep: the route claims the row and launches Phase A.
    assert client.post(f"/board/jobs/{FP}/prep").status_code == 200
    assert _job(db_path)["stage"] == "prep_in_progress"
    assert len(_prep_launches(env["launches"])) == 1

    # 2. Phase A runs (the subprocess the route launched).
    env["orch"]._run_prep_phase_a(COMPANY, TITLE, URL, JOB_ID)
    after_a = _job(db_path)
    assert after_a["stage"] == "briefing_ready"
    folder = Path(after_a["prep_folder_path"])
    briefing = next(folder.glob("*Briefing*.md"))

    # 3. Fifteen days pass with no decision; the watchdog runs (reaper + orphan sweep).
    past = datetime.now(UTC) - timedelta(days=15)
    conn = sqlite3.connect(db_path)
    conn.execute("UPDATE jobs SET stage_updated=? WHERE id=?", (past.isoformat(), JOB_ID))
    conn.commit()
    for p in (briefing, folder):
        os.utime(p, (past.timestamp(), past.timestamp()))
    wd_conn = sqlite3.connect(db_path)
    wd_conn.row_factory = sqlite3.Row
    monkeypatch.setattr(watchdog, "connect", lambda *a, **kw: _KeepOpen(wd_conn))
    watchdog.main()
    wd_conn.close()

    after_window = _job(db_path)
    assert after_window["stage"] == "scored"
    assert after_window["prep_folder_path"] == str(folder)
    assert folder.is_dir(), "the orphan sweep must not move a tracked briefing folder"
    assert not (companies / ".stale").exists()

    # 4. The dashboard still shows the briefing, aged from the briefing file.
    page = client.get("/board/dashboard").text
    assert f'href="/materials/{FP}"' in page
    assert "Briefing ready (15 days)" in page

    # 5. Re-flag: the briefing comes back; no second Phase A launch or LLM call.
    assert client.post(f"/board/jobs/{FP}/prep").status_code == 200
    after_reflag = _job(db_path)
    assert after_reflag["stage"] == "briefing_ready"
    assert after_reflag["prep_folder_path"] == str(folder)
    assert len(_prep_launches(env["launches"])) == 1
    assert sorted(r for r in env["llm_calls"] if r in PHASE_A_ROLES) == sorted(PHASE_A_ROLES), (
        "each Phase A role runs once across the whole sequence"
    )
    assert [p.name for p in companies.iterdir()] == [folder.name]

    # 6. The audit trail tells the same story.
    rows = conn.execute(
        "SELECT old_value, new_value FROM audit_log WHERE job_id=? AND field_changed='stage' ORDER BY id",
        (JOB_ID,),
    ).fetchall()
    conn.close()
    assert rows == [
        ("scored", "prep_in_progress"),
        ("prep_in_progress", "briefing_ready"),
        ("briefing_ready", "scored"),
        ("scored", "briefing_ready"),
    ]
