"""#691 — Tests for the briefing-first gate split of ``_run_prep``.

Three contract tests:

1. ``_run_prep_phase_a`` runs only stages 1-3 (company_researcher,
   briefing_writer, fit_analyst) and transitions stage to
   ``briefing_ready`` with ``fit_score``/``probability_score`` written
   to the DB; writes ``briefing.md`` to the prep folder.
2. ``_run_prep_phase_b`` re-reads the briefing from disk + scores from
   DB on entry, runs stages 4-7 (resume_tailor through outreach), and
   transitions to ``materials_drafted``.
3. ``_run_prep_phase_b`` on subprocess failure resets stage to
   ``briefing_ready`` (NOT ``scored``), preserving the briefing folder
   so the operator can retry Phase B without re-paying Phase A.

The refactor of ``_run_prep`` itself takes args explicitly rather than
reading ``sys.argv`` — without that change these tests would have to
shell out, which makes mocking impractical.
"""

from __future__ import annotations

import sqlite3
import subprocess
from pathlib import Path

import pytest

SCHEMA = """
CREATE TABLE jobs (
    id TEXT PRIMARY KEY,
    fingerprint TEXT UNIQUE NOT NULL,
    url TEXT NOT NULL,
    title TEXT NOT NULL,
    company TEXT NOT NULL,
    location TEXT DEFAULT '',
    source TEXT NOT NULL DEFAULT 'test',
    raw_jd_text TEXT,
    relevance_score INTEGER,
    stage TEXT DEFAULT 'discovered',
    stage_updated TEXT,
    apply_flag INTEGER DEFAULT 0,
    reject_reason TEXT DEFAULT '',
    prep_folder_path TEXT,
    fit_score REAL,
    probability_score REAL,
    updated_at TEXT DEFAULT (datetime('now')),
    synthetic INTEGER NOT NULL DEFAULT 0,
    speculative_briefing_folder TEXT
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

JOB_ID = "phase-test-1"
FP = "fp-phase-test-1"
COMPANY = "Acme Lab Services"
TITLE = "Director of Lab Services"
URL = "https://example.com/job/1"

FAKE_JD = "We are looking for a Director with 10+ years experience..."
FAKE_PROFILE = "# Profile\n\n20 years operations.\n\nFile prefix: TST\n"
FAKE_MASTER_RESUME = "# Resume\n\nHistory here.\n"

# fit_analyst fake output must satisfy _fit_analysis_is_complete:
#   - contains "Fit Matrix"
#   - splittable on "## 🎯 Probability Assessment"
#   - has at least one ":NN%" on each side
FAKE_FIT_ANALYSIS = "## Fit Matrix\n- Skills: 80%\n- Domain: 70%\n\n## 🎯 Probability Assessment\n- Interview: 60%\n"

# Briefing must end with an "Overall Recommendation" heading per rec_re.
FAKE_BRIEFING = "# Briefing\n\nBody content.\n\n## Overall Recommendation: Apply with Reservations\n"


def _fake_run_role(role: str, _prompt: str, **_kwargs) -> str:
    """Stand-in for ``findajob.llm.role_runner.run_role`` — returns
    role-appropriate content so the orchestrator's parsing succeeds."""
    return {
        "company_researcher": "Raw research text.",
        "briefing_writer": FAKE_BRIEFING,
        "fit_analyst": FAKE_FIT_ANALYSIS,
        "resume_tailor": "# Resume\n\n" + "x" * 600,  # > MIN_BYTES (500)
        "resume_change_reviewer": "## Changes\n- swapped headline",
        "cover_letter_writer": "Dear Hiring Manager,\n\n" + "x" * 600,
        "recruiter_critic": "## Critique\n- Strong opening.",
        "outreach_drafter": "Hi {name},\n\nReaching out.",
    }.get(role, f"Fake output for {role}")


@pytest.fixture()
def isolated_base(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Stand up a self-contained BASE directory tree with profile.md +
    master_resume.md + an empty companies/ dir. Repoint the orchestrator's
    module-level BASE / DB_PATH / PROFILE_PATH / MASTER_RESUME_PATH at it."""
    candidate = tmp_path / "candidate_context"
    candidate.mkdir()
    (candidate / "profile.md").write_text(FAKE_PROFILE)
    (candidate / "master_resume.md").write_text(FAKE_MASTER_RESUME)
    (tmp_path / "companies").mkdir()
    (tmp_path / "data").mkdir()
    db_path = tmp_path / "data" / "pipeline.db"

    # Build the DB before pointing orchestrator at it
    conn = sqlite3.connect(str(db_path))
    conn.executescript(SCHEMA)
    conn.close()

    import findajob.prep.orchestrator as orch

    monkeypatch.setattr(orch, "BASE", str(tmp_path))
    monkeypatch.setattr(orch, "DB_PATH", str(db_path))
    monkeypatch.setattr(orch, "PROFILE_PATH", str(candidate / "profile.md"))
    monkeypatch.setattr(orch, "MASTER_RESUME_PATH", str(candidate / "master_resume.md"))
    return tmp_path


@pytest.fixture()
def mocked_orchestrator(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub out the I/O surfaces inside the orchestrator: LLM calls,
    docx rendering, ntfy, voice samples, find_contacts subprocess, and
    the scripts/diag/validate_resume.py informational call."""
    import findajob.prep.orchestrator as orch

    monkeypatch.setattr(orch, "run_role", _fake_run_role)
    monkeypatch.setattr(orch, "render_md_to_docx", lambda *args, **kwargs: None)
    monkeypatch.setattr(orch, "_add_cover_letter_spacing", lambda *args, **kwargs: None)
    monkeypatch.setattr(orch, "_linkify_contact_info", lambda s: s)
    monkeypatch.setattr(orch, "ntfy_send", lambda *args, **kwargs: None)
    monkeypatch.setattr(orch, "load_voice_samples", lambda: "")
    monkeypatch.setattr(orch, "read_file_prefix", lambda: "TST")
    monkeypatch.setattr(orch, "quarantine_stale_prep_folders", lambda *args, **kwargs: None)

    # Short-circuit any subprocess.run inside the orchestrator (find_contacts,
    # validate_resume informational check). Return a stand-in CompletedProcess.
    real_subprocess_run = subprocess.run

    def _fake_subprocess_run(cmd, *args, **kwargs):
        # Only short-circuit calls FROM the orchestrator's own subprocess.run.
        # find_contacts.py is the one with check=True.
        if isinstance(cmd, list) and len(cmd) > 1 and "find_contacts.py" in str(cmd[1]):
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout=b"", stderr=b"")
        if isinstance(cmd, list) and len(cmd) > 1 and "validate_resume.py" in str(cmd[1]):
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout=b"", stderr=b"")
        # Fall through to real for anything else (shouldn't be reached during the test)
        return real_subprocess_run(cmd, *args, **kwargs)

    monkeypatch.setattr(orch.subprocess, "run", _fake_subprocess_run)


@pytest.fixture()
def isolate_event_log(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    log_path = tmp_path / "pipeline.jsonl"
    monkeypatch.setattr("findajob.audit.LOG_PATH", str(log_path))
    return log_path


def _seed_job(db_path: str, stage: str = "prep_in_progress") -> None:
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO jobs (id, fingerprint, url, title, company, source, stage, raw_jd_text) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (JOB_ID, FP, URL, TITLE, COMPANY, "test", stage, FAKE_JD),
    )
    conn.commit()
    conn.close()


def _read_job(db_path: str) -> sqlite3.Row:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT stage, prep_folder_path, fit_score, probability_score FROM jobs WHERE id=?",
        (JOB_ID,),
    ).fetchone()
    conn.close()
    return row


# ═══════════════════════════════════════════════════════════════════════════
# Phase A — stops at briefing_ready, writes fit/prob scores + briefing.md
# ═══════════════════════════════════════════════════════════════════════════


def test_phase_a_transitions_to_briefing_ready(isolated_base, mocked_orchestrator, isolate_event_log) -> None:
    """Phase A runs stages 1-3 and exits with stage=briefing_ready."""
    from findajob.prep.orchestrator import _run_prep_phase_a

    _seed_job(str(isolated_base / "data" / "pipeline.db"))

    _run_prep_phase_a(COMPANY, TITLE, URL, JOB_ID)

    row = _read_job(str(isolated_base / "data" / "pipeline.db"))
    assert row["stage"] == "briefing_ready", f"Phase A must transition to briefing_ready, got stage={row['stage']!r}"
    assert row["prep_folder_path"], "Phase A must write prep_folder_path"
    assert row["fit_score"] is not None, "Phase A must write fit_score"
    assert row["probability_score"] is not None, "Phase A must write probability_score"


def test_phase_a_writes_briefing_md_to_disk(isolated_base, mocked_orchestrator, isolate_event_log) -> None:
    """Phase A writes a briefing.md (or {Prefix} Briefing - ... .md) to
    the prep folder — Phase B reads from that file on entry."""
    from findajob.prep.orchestrator import _run_prep_phase_a

    _seed_job(str(isolated_base / "data" / "pipeline.db"))

    _run_prep_phase_a(COMPANY, TITLE, URL, JOB_ID)

    row = _read_job(str(isolated_base / "data" / "pipeline.db"))
    folder = Path(row["prep_folder_path"])
    assert folder.exists(), f"prep folder must exist: {folder}"
    briefing_files = list(folder.glob("*Briefing*.md")) + list(folder.glob("briefing.md"))
    assert briefing_files, f"no briefing markdown found in {folder}; contents: {list(folder.iterdir())}"
    assert "Overall Recommendation" in briefing_files[0].read_text(), (
        "briefing must contain the Overall Recommendation heading"
    )


# ═══════════════════════════════════════════════════════════════════════════
# Phase B — reads from disk + DB, transitions to materials_drafted
# ═══════════════════════════════════════════════════════════════════════════


def test_phase_b_transitions_to_materials_drafted(isolated_base, mocked_orchestrator, isolate_event_log) -> None:
    """Phase B re-reads briefing from disk and DB-stored scores, runs
    stages 4-7, and exits with stage=materials_drafted."""
    from findajob.prep.orchestrator import _run_prep_phase_a, _run_prep_phase_b

    db_path = str(isolated_base / "data" / "pipeline.db")
    _seed_job(db_path)

    # Phase A produces the briefing folder + DB state Phase B needs
    _run_prep_phase_a(COMPANY, TITLE, URL, JOB_ID)
    assert _read_job(db_path)["stage"] == "briefing_ready"

    # Phase B picks up from briefing_ready
    _run_prep_phase_b(COMPANY, TITLE, URL, JOB_ID)

    row = _read_job(db_path)
    assert row["stage"] == "materials_drafted", (
        f"Phase B must transition to materials_drafted, got stage={row['stage']!r}"
    )

    # Phase B should have produced resume.md and cover.md alongside the briefing
    folder = Path(row["prep_folder_path"])
    md_files = {p.name for p in folder.glob("*.md")}
    assert any("Resume" in f for f in md_files), f"no Resume*.md in {md_files}"
    assert any("Cover" in f or "cover" in f for f in md_files), f"no cover letter in {md_files}"


# ═══════════════════════════════════════════════════════════════════════════
# Phase B failure path — resets to briefing_ready, NOT scored
# ═══════════════════════════════════════════════════════════════════════════


def test_phase_b_failure_resets_to_briefing_ready(
    isolated_base, mocked_orchestrator, isolate_event_log, monkeypatch
) -> None:
    """When a Phase B subprocess raises CalledProcessError, the
    orchestrator must reset stage to ``briefing_ready`` (NOT ``scored``)
    so the operator can retry Phase B without re-paying Phase A.

    Critical contract: the existing ``_handle_prep_subprocess_failure``
    resets to ``scored``; Phase B must take a different recovery path.
    """
    import findajob.prep.orchestrator as orch
    from findajob.prep.orchestrator import _run_prep_phase_a, _run_prep_phase_b

    db_path = str(isolated_base / "data" / "pipeline.db")
    _seed_job(db_path)

    _run_prep_phase_a(COMPANY, TITLE, URL, JOB_ID)
    assert _read_job(db_path)["stage"] == "briefing_ready"

    # Make the find_contacts subprocess fail to simulate a Phase B subprocess crash
    real_subprocess_run = orch.subprocess.run

    def _failing_subprocess_run(cmd, *args, **kwargs):
        if isinstance(cmd, list) and len(cmd) > 1 and "find_contacts.py" in str(cmd[1]):
            raise subprocess.CalledProcessError(returncode=1, cmd=cmd, stderr=b"simulated find_contacts crash")
        return real_subprocess_run(cmd, *args, **kwargs)

    monkeypatch.setattr(orch.subprocess, "run", _failing_subprocess_run)

    with pytest.raises(SystemExit):
        _run_prep_phase_b(COMPANY, TITLE, URL, JOB_ID)

    row = _read_job(db_path)
    assert row["stage"] == "briefing_ready", (
        f"Phase B failure must reset to briefing_ready (not scored), got {row['stage']!r}. "
        "The folder contains the briefing; operator should be able to retry without re-paying Phase A."
    )
    # prep_folder_path must remain set so the operator's retry surfaces the same briefing
    assert row["prep_folder_path"], "prep_folder_path must NOT be nulled on Phase B failure"
    assert Path(row["prep_folder_path"]).exists(), "briefing folder must remain on disk after Phase B failure"


# ═══════════════════════════════════════════════════════════════════════════
# CLI --phase flag dispatch — main() routes to the right phase helper
# ═══════════════════════════════════════════════════════════════════════════


def test_main_phase_a_dispatches_only_phase_a(monkeypatch, isolated_base) -> None:
    """``prep_application.py <args> --phase=a`` calls only ``_run_prep_phase_a``."""
    import findajob.prep.orchestrator as orch

    calls: list[str] = []
    monkeypatch.setattr(orch, "_run_prep_phase_a", lambda *a, **kw: calls.append("a"))
    monkeypatch.setattr(orch, "_run_prep_phase_b", lambda *a, **kw: calls.append("b"))
    monkeypatch.setattr(orch, "load_env", lambda: None)
    monkeypatch.setattr(orch, "writeback_subprocess", lambda *a, **kw: _NullContext())
    monkeypatch.setattr(
        "sys.argv",
        ["prep_application.py", COMPANY, TITLE, URL, JOB_ID, "--phase=a"],
    )

    orch.main()
    assert calls == ["a"], f"expected only Phase A to run, got {calls}"


def test_main_phase_b_dispatches_only_phase_b(monkeypatch, isolated_base) -> None:
    """``prep_application.py <args> --phase=b`` calls only ``_run_prep_phase_b``."""
    import findajob.prep.orchestrator as orch

    calls: list[str] = []
    monkeypatch.setattr(orch, "_run_prep_phase_a", lambda *a, **kw: calls.append("a"))
    monkeypatch.setattr(orch, "_run_prep_phase_b", lambda *a, **kw: calls.append("b"))
    monkeypatch.setattr(orch, "load_env", lambda: None)
    monkeypatch.setattr(orch, "writeback_subprocess", lambda *a, **kw: _NullContext())
    monkeypatch.setattr(
        "sys.argv",
        ["prep_application.py", COMPANY, TITLE, URL, JOB_ID, "--phase=b"],
    )

    orch.main()
    assert calls == ["b"], f"expected only Phase B to run, got {calls}"


def test_main_default_runs_phase_a_then_phase_b(monkeypatch, isolated_base) -> None:
    """No ``--phase`` flag (legacy callers like cron) runs the full chain."""
    import findajob.prep.orchestrator as orch

    calls: list[str] = []
    monkeypatch.setattr(orch, "_run_prep_phase_a", lambda *a, **kw: calls.append("a"))
    monkeypatch.setattr(orch, "_run_prep_phase_b", lambda *a, **kw: calls.append("b"))
    monkeypatch.setattr(orch, "load_env", lambda: None)
    monkeypatch.setattr(orch, "writeback_subprocess", lambda *a, **kw: _NullContext())
    monkeypatch.setattr(
        "sys.argv",
        ["prep_application.py", COMPANY, TITLE, URL, JOB_ID],
    )

    orch.main()
    assert calls == ["a", "b"], f"default must run Phase A then B, got {calls}"


def test_main_phase_all_explicit_runs_full_chain(monkeypatch, isolated_base) -> None:
    """``--phase=all`` is explicit alias for the default."""
    import findajob.prep.orchestrator as orch

    calls: list[str] = []
    monkeypatch.setattr(orch, "_run_prep_phase_a", lambda *a, **kw: calls.append("a"))
    monkeypatch.setattr(orch, "_run_prep_phase_b", lambda *a, **kw: calls.append("b"))
    monkeypatch.setattr(orch, "load_env", lambda: None)
    monkeypatch.setattr(orch, "writeback_subprocess", lambda *a, **kw: _NullContext())
    monkeypatch.setattr(
        "sys.argv",
        ["prep_application.py", COMPANY, TITLE, URL, JOB_ID, "--phase=all"],
    )

    orch.main()
    assert calls == ["a", "b"], f"--phase=all must run Phase A then B, got {calls}"


class _NullContext:
    """Minimal context-manager stand-in for ``writeback_subprocess``."""

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def test_phase_b_validation_failure_resets_to_briefing_ready(
    isolated_base, mocked_orchestrator, isolate_event_log, monkeypatch
) -> None:
    """When Phase B's Step-7 validation fails (resume_md or cover_md
    under MIN_BYTES=500), the orchestrator must take the same recovery
    path as a subprocess crash: reset stage to ``briefing_ready`` (NOT
    ``scored``) and preserve the briefing folder.

    Without this routing, a partial-LLM-output prep would silently roll
    back to ``scored`` and the operator would re-pay Phase A on retry.
    """
    import findajob.prep.orchestrator as orch
    from findajob.prep.orchestrator import _run_prep_phase_a, _run_prep_phase_b

    db_path = str(isolated_base / "data" / "pipeline.db")
    _seed_job(db_path)

    _run_prep_phase_a(COMPANY, TITLE, URL, JOB_ID)
    assert _read_job(db_path)["stage"] == "briefing_ready"

    # Stub run_role to return a too-short resume — validation will fail
    # because the file size < MIN_BYTES (500).
    def _short_resume_run_role(role: str, prompt: str, **kwargs) -> str:
        if role == "resume_tailor":
            return "# Resume\n\nTiny."  # < 500 bytes
        return _fake_run_role(role, prompt, **kwargs)

    monkeypatch.setattr(orch, "run_role", _short_resume_run_role)

    # Validation failure path returns rather than raising SystemExit.
    _run_prep_phase_b(COMPANY, TITLE, URL, JOB_ID)

    row = _read_job(db_path)
    assert row["stage"] == "briefing_ready", f"validation failure must reset to briefing_ready, got {row['stage']!r}"
    assert row["prep_folder_path"], "prep_folder_path must be preserved on validation failure"
    assert Path(row["prep_folder_path"]).exists(), "briefing folder must remain on disk"
