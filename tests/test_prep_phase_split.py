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
# #1222 — Phase A resumes an existing briefing instead of re-running
# ═══════════════════════════════════════════════════════════════════════════

_PHASE_A_ROLES = ("company_researcher", "briefing_writer", "fit_analyst")


def _seed_job_with_briefing(base: Path, *, briefing: bool = True) -> Path:
    """A claimed (prep_in_progress) job whose prep folder already holds a
    briefing — e.g. reactivate-and-prep of a job waitlisted after Phase A."""
    folder = base / "companies" / "Acme_Lab_Services_Dir_Lab_2026-10-01_120000"
    folder.mkdir()
    if briefing:
        (folder / "TST Briefing - Acme Lab Services - Director - 20261001-120000.md").write_text(FAKE_BRIEFING)
    db_path = str(base / "data" / "pipeline.db")
    _seed_job(db_path)
    conn = sqlite3.connect(db_path)
    conn.execute(
        "UPDATE jobs SET prep_folder_path=?, fit_score=72, probability_score=60 WHERE id=?",
        (str(folder), JOB_ID),
    )
    conn.commit()
    conn.close()
    return folder


def _no_phase_a_llm(role: str, prompt: str, **kwargs) -> str:
    if role in _PHASE_A_ROLES:
        raise AssertionError(f"Phase A must not re-run {role} when a briefing exists")
    return _fake_run_role(role, prompt, **kwargs)


def _events(log_path: Path) -> list[dict]:
    import json

    if not log_path.exists():
        return []
    return [json.loads(line) for line in log_path.read_text().splitlines() if line.strip()]


def test_phase_a_resumes_existing_briefing_without_rerunning(
    isolated_base, mocked_orchestrator, isolate_event_log, monkeypatch
) -> None:
    import findajob.prep.orchestrator as orch

    monkeypatch.setattr(orch, "run_role", _no_phase_a_llm)
    sent: list[dict] = []
    monkeypatch.setattr(orch, "ntfy_send", lambda *a, **kw: sent.append(kw))
    folder = _seed_job_with_briefing(isolated_base)
    db_path = str(isolated_base / "data" / "pipeline.db")

    orch._run_prep_phase_a(COMPANY, TITLE, URL, JOB_ID)

    row = _read_job(db_path)
    assert row["stage"] == "briefing_ready"
    assert row["prep_folder_path"] == str(folder)
    assert row["fit_score"] == 72, "the stored Phase A scores must survive"
    assert [p.name for p in (isolated_base / "companies").iterdir()] == [folder.name], "no new prep folder"
    names = [e["event"] for e in _events(isolate_event_log)]
    assert "briefing_resurfaced" in names
    assert "prep_started" not in names
    assert "prep_cost_projection" not in names
    conn = sqlite3.connect(db_path)
    audit = conn.execute("SELECT old_value, new_value FROM audit_log WHERE job_id=?", (JOB_ID,)).fetchall()
    conn.close()
    assert audit == [("prep_in_progress", "briefing_ready")]
    assert [kw.get("kind") for kw in sent] == ["prep_briefing_ready"]


def test_phase_a_without_briefing_in_folder_runs_normally(
    isolated_base, mocked_orchestrator, isolate_event_log
) -> None:
    """A tracked folder with no briefing (e.g. a partial run) is not a resume point."""
    from findajob.prep.orchestrator import _run_prep_phase_a

    old = _seed_job_with_briefing(isolated_base, briefing=False)

    _run_prep_phase_a(COMPANY, TITLE, URL, JOB_ID)

    row = _read_job(str(isolated_base / "data" / "pipeline.db"))
    assert row["stage"] == "briefing_ready"
    assert row["prep_folder_path"] != str(old), "Phase A ran and wrote its own folder"
    assert "prep_started" in [e["event"] for e in _events(isolate_event_log)]


def test_full_chain_after_resume_drafts_materials_without_phase_a(
    isolated_base, mocked_orchestrator, isolate_event_log, monkeypatch
) -> None:
    """reactivate-and-prep launches --phase=all; with a briefing on disk the
    chain skips Phase A and Phase B still drafts the materials."""
    import findajob.prep.orchestrator as orch

    monkeypatch.setattr(orch, "run_role", _no_phase_a_llm)
    folder = _seed_job_with_briefing(isolated_base)
    db_path = str(isolated_base / "data" / "pipeline.db")
    monkeypatch.setattr(orch.sys, "argv", ["prep_application.py", "--", COMPANY, TITLE, URL, JOB_ID])

    orch._run_prep()

    row = _read_job(db_path)
    assert row["stage"] == "materials_drafted"
    assert row["prep_folder_path"] == str(folder)
    assert any("Resume" in p.name for p in folder.glob("*.md"))


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


# ═══════════════════════════════════════════════════════════════════════════
# #956 — spend-ceiling / DB-error mid-prep: reset + notify, never strand
# ═══════════════════════════════════════════════════════════════════════════


def test_phase_a_spend_ceiling_resets_to_scored_and_notifies(
    isolated_base, mocked_orchestrator, isolate_event_log, monkeypatch
) -> None:
    """#956: a spend-ceiling breach mid-Phase-A must roll stage back to
    ``scored`` immediately and fire a prep_failure ntfy — not leave the job
    stranded in ``prep_in_progress`` for up to 60 minutes until the watchdog.

    The Phase A handler caught only ``CalledProcessError``;
    ``LLMSpendCeilingExceeded`` (correctly NOT an ``OpenRouterError``, so
    run_role re-raises it) fell through uncaught."""
    import findajob.prep.orchestrator as orch
    from findajob.llm.openrouter import LLMSpendCeilingExceeded
    from findajob.prep.orchestrator import _run_prep_phase_a

    db_path = str(isolated_base / "data" / "pipeline.db")
    _seed_job(db_path)  # stage=prep_in_progress

    ntfy_calls: list[dict] = []
    monkeypatch.setattr(orch, "ntfy_send", lambda *a, **kw: ntfy_calls.append(kw))

    def _ceiling_run_role(role: str, prompt: str, **kwargs) -> str:
        raise LLMSpendCeilingExceeded(ceiling_usd=50.0, current_sum_usd=55.0)

    monkeypatch.setattr(orch, "run_role", _ceiling_run_role)

    with pytest.raises(SystemExit):
        _run_prep_phase_a(COMPANY, TITLE, URL, JOB_ID)

    row = _read_job(db_path)
    assert row["stage"] == "scored", (
        f"ceiling mid-Phase-A must reset to scored, got {row['stage']!r} "
        "(stranded in prep_in_progress means the 60-min watchdog is the only recovery)"
    )
    assert ntfy_calls, "a prep_failure ntfy must fire on a ceiling breach"
    assert any(kw.get("kind") == "prep_failure" for kw in ntfy_calls)


def test_phase_b_spend_ceiling_resets_to_briefing_ready_and_notifies(
    isolated_base, mocked_orchestrator, isolate_event_log, monkeypatch
) -> None:
    """#956: a spend-ceiling breach mid-Phase-B must roll stage back to
    ``briefing_ready`` (preserving the briefing folder for retry) and fire a
    prep_failure ntfy — never strand the job in ``prep_in_progress``.

    Phase B is entered here at ``prep_in_progress`` to mirror the
    /continue-prep route, which claims the slot before spawning Phase B."""
    import sqlite3 as _sqlite3

    import findajob.prep.orchestrator as orch
    from findajob.llm.openrouter import LLMSpendCeilingExceeded
    from findajob.prep.orchestrator import _run_prep_phase_a, _run_prep_phase_b

    db_path = str(isolated_base / "data" / "pipeline.db")
    _seed_job(db_path)

    # Phase A produces the briefing folder + DB scores Phase B reads on entry.
    _run_prep_phase_a(COMPANY, TITLE, URL, JOB_ID)
    assert _read_job(db_path)["stage"] == "briefing_ready"

    # Simulate /continue-prep claiming the slot before Phase B runs.
    claim = _sqlite3.connect(db_path)
    claim.execute("UPDATE jobs SET stage='prep_in_progress' WHERE id=?", (JOB_ID,))
    claim.commit()
    claim.close()

    ntfy_calls: list[dict] = []
    monkeypatch.setattr(orch, "ntfy_send", lambda *a, **kw: ntfy_calls.append(kw))

    def _ceiling_on_resume(role: str, prompt: str, **kwargs) -> str:
        if role == "resume_tailor":
            raise LLMSpendCeilingExceeded(ceiling_usd=50.0, current_sum_usd=60.0)
        return _fake_run_role(role, prompt, **kwargs)

    monkeypatch.setattr(orch, "run_role", _ceiling_on_resume)

    with pytest.raises(SystemExit):
        _run_prep_phase_b(COMPANY, TITLE, URL, JOB_ID)

    row = _read_job(db_path)
    assert row["stage"] == "briefing_ready", (
        f"ceiling mid-Phase-B must reset to briefing_ready, got {row['stage']!r} "
        "(never stranded in prep_in_progress; briefing preserved for retry without re-paying Phase A)"
    )
    assert row["prep_folder_path"], "briefing folder must be preserved on a ceiling breach"
    assert Path(row["prep_folder_path"]).exists(), "briefing folder must remain on disk"
    assert any(kw.get("kind") == "prep_failure" for kw in ntfy_calls)


def test_phase_a_db_error_resets_to_scored_and_notifies(
    isolated_base, mocked_orchestrator, isolate_event_log, monkeypatch
) -> None:
    """#956 (defensive): a raw ``sqlite3.OperationalError`` surfacing from the
    spend-ceiling gate (pipeline.db missing/half-initialized) mid-prep must be
    caught and recovered like a ceiling breach — reset to ``scored`` + notify —
    not crash uncaught and strand the job. The gate is fail-closed by design
    (DB errors propagate); the orchestrator is the layer that recovers."""
    import sqlite3 as _sqlite3

    import findajob.prep.orchestrator as orch
    from findajob.prep.orchestrator import _run_prep_phase_a

    db_path = str(isolated_base / "data" / "pipeline.db")
    _seed_job(db_path)

    ntfy_calls: list[dict] = []
    monkeypatch.setattr(orch, "ntfy_send", lambda *a, **kw: ntfy_calls.append(kw))

    def _db_error_run_role(role: str, prompt: str, **kwargs) -> str:
        raise _sqlite3.OperationalError("unable to open database file")

    monkeypatch.setattr(orch, "run_role", _db_error_run_role)

    with pytest.raises(SystemExit):
        _run_prep_phase_a(COMPANY, TITLE, URL, JOB_ID)

    row = _read_job(db_path)
    assert row["stage"] == "scored", f"DB error mid-Phase-A must reset to scored, got {row['stage']!r}"
    assert any(kw.get("kind") == "prep_failure" for kw in ntfy_calls)


def test_phase_a_post_success_db_error_does_not_revert_or_false_notify(
    isolated_base, mocked_orchestrator, isolate_event_log, monkeypatch
) -> None:
    """#956 regression: the new `except sqlite3.OperationalError` wraps the
    whole Phase A body, including the post-commit write_audit. If a transient
    OperationalError fires AFTER stage was committed to briefing_ready, the job
    must NOT be mislabeled as a failure — stage stays briefing_ready (the reset
    is guarded on prep_in_progress) and NO prep_failure ntfy fires (prep
    actually succeeded)."""
    import sqlite3 as _sqlite3

    import findajob.prep.orchestrator as orch
    from findajob.prep.orchestrator import _run_prep_phase_a

    db_path = str(isolated_base / "data" / "pipeline.db")
    _seed_job(db_path)

    ntfy_calls: list[dict] = []
    monkeypatch.setattr(orch, "ntfy_send", lambda *a, **kw: ntfy_calls.append(kw))

    # Raise OperationalError on the post-commit briefing_ready audit write only.
    real_write_audit = orch.write_audit

    def _failing_write_audit(conn, job_id, field, old, new, *a, **kw):
        if new == "briefing_ready":
            raise _sqlite3.OperationalError("database is locked")
        return real_write_audit(conn, job_id, field, old, new, *a, **kw)

    monkeypatch.setattr(orch, "write_audit", _failing_write_audit)

    with pytest.raises(SystemExit):
        _run_prep_phase_a(COMPANY, TITLE, URL, JOB_ID)

    row = _read_job(db_path)
    assert row["stage"] == "briefing_ready", (
        f"a post-success OperationalError must not revert the committed stage, got {row['stage']!r}"
    )
    assert not any(kw.get("kind") == "prep_failure" for kw in ntfy_calls), (
        "prep actually succeeded — no prep_failure ntfy should fire on a post-commit hiccup"
    )


def test_phase_b_post_success_db_error_does_not_revert_or_false_notify(
    isolated_base, mocked_orchestrator, isolate_event_log, monkeypatch
) -> None:
    """#956 regression: the strongest case — Phase B commits materials_drafted
    BEFORE its post-commit write_audit. A transient OperationalError on that
    audit write must NOT revert the completed application to briefing_ready
    (the unguarded reset would have) and must NOT fire a misleading
    prep_failure ntfy."""
    import sqlite3 as _sqlite3

    import findajob.prep.orchestrator as orch
    from findajob.prep.orchestrator import _run_prep_phase_a, _run_prep_phase_b

    db_path = str(isolated_base / "data" / "pipeline.db")
    _seed_job(db_path)

    _run_prep_phase_a(COMPANY, TITLE, URL, JOB_ID)
    assert _read_job(db_path)["stage"] == "briefing_ready"

    ntfy_calls: list[dict] = []
    monkeypatch.setattr(orch, "ntfy_send", lambda *a, **kw: ntfy_calls.append(kw))

    real_write_audit = orch.write_audit

    def _failing_write_audit(conn, job_id, field, old, new, *a, **kw):
        if new == "materials_drafted":
            raise _sqlite3.OperationalError("database is locked")
        return real_write_audit(conn, job_id, field, old, new, *a, **kw)

    monkeypatch.setattr(orch, "write_audit", _failing_write_audit)

    with pytest.raises(SystemExit):
        _run_prep_phase_b(COMPANY, TITLE, URL, JOB_ID)

    row = _read_job(db_path)
    assert row["stage"] == "materials_drafted", (
        f"a post-success OperationalError must not revert materials_drafted, got {row['stage']!r}"
    )
    assert not any(kw.get("kind") == "prep_failure" for kw in ntfy_calls), (
        "prep actually succeeded — no prep_failure ntfy should fire on a post-commit hiccup"
    )


# ═══════════════════════════════════════════════════════════════════════════
# #1098 — marked master-resume sections
# ═══════════════════════════════════════════════════════════════════════════

MARKED_QUOTE = "Turned a struggling night shift into the steadiest team in the building."
MARKED_MASTER = FAKE_MASTER_RESUME + f'\n## Selected Quotes\n*(not for submitted resumes)*\n- "{MARKED_QUOTE}"\n'


def _run_both_phases_recording(
    base: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    master: str | None = None,
    tailor_output: str | None = None,
) -> list[tuple[str, str, dict]]:
    """Run Phase A then Phase B with a run_role stand-in that records every
    call as ``(role, prompt, kwargs)``."""
    import findajob.prep.orchestrator as orch

    if master is not None:
        (base / "candidate_context" / "master_resume.md").write_text(master)
    calls: list[tuple[str, str, dict]] = []

    def _recording(role: str, prompt: str, **kwargs) -> str:
        calls.append((role, prompt, kwargs))
        if role == "resume_tailor" and tailor_output is not None:
            return tailor_output
        return _fake_run_role(role, prompt, **kwargs)

    monkeypatch.setattr(orch, "run_role", _recording)
    _seed_job(str(base / "data" / "pipeline.db"))
    orch._run_prep_phase_a(COMPANY, TITLE, URL, JOB_ID)
    orch._run_prep_phase_b(COMPANY, TITLE, URL, JOB_ID)
    return calls


def _call(calls: list[tuple[str, str, dict]], role: str) -> tuple[str, str, dict]:
    return next(c for c in calls if c[0] == role)


def _checklist_text(base: Path) -> str:
    folder = Path(_read_job(str(base / "data" / "pipeline.db"))["prep_folder_path"])
    return next(folder.glob("Review Checklist - *.md")).read_text()


def test_tailor_prefix_excludes_marked_section(
    isolated_base, mocked_orchestrator, isolate_event_log, monkeypatch
) -> None:
    calls = _run_both_phases_recording(isolated_base, monkeypatch, master=MARKED_MASTER)

    tailor_prefix = _call(calls, "resume_tailor")[2]["cached_prefix"]
    assert "History here." in tailor_prefix
    assert "steadiest team" not in tailor_prefix


def test_cover_letter_prefix_extends_tailor_prefix_and_has_marked_text(
    isolated_base, mocked_orchestrator, isolate_event_log, monkeypatch
) -> None:
    calls = _run_both_phases_recording(isolated_base, monkeypatch, master=MARKED_MASTER)

    tailor_prefix = _call(calls, "resume_tailor")[2]["cached_prefix"]
    cover_prefix = _call(calls, "cover_letter_writer")[2]["cached_prefix"]
    assert cover_prefix.startswith(tailor_prefix), "the cover letter must keep the tailor's cache prefix"
    assert "steadiest team" in cover_prefix


def test_briefing_writer_still_gets_marked_section(
    isolated_base, mocked_orchestrator, isolate_event_log, monkeypatch
) -> None:
    calls = _run_both_phases_recording(isolated_base, monkeypatch, master=MARKED_MASTER)

    assert "steadiest team" in _call(calls, "briefing_writer")[2]["cached_prefix"]


def test_no_marker_keeps_phase_b_prefixes_unchanged(
    isolated_base, mocked_orchestrator, isolate_event_log, monkeypatch
) -> None:
    calls = _run_both_phases_recording(isolated_base, monkeypatch)

    expected = (
        f"CANDIDATE PROFILE:\n{FAKE_PROFILE}\n\nMASTER RESUME:\n{FAKE_MASTER_RESUME}\n\n"
        f"Company: {COMPANY}\nTitle: {TITLE}\n\nJD:\n{FAKE_JD}\n\n---\n\n"
    )
    assert _call(calls, "resume_tailor")[2]["cached_prefix"] == expected
    assert _call(calls, "cover_letter_writer")[2]["cached_prefix"] == expected + "---\n\n"


def test_copied_marked_text_is_logged_high_and_in_checklist(
    isolated_base, mocked_orchestrator, isolate_event_log, monkeypatch
) -> None:
    leaked = "# Resume\n\n## Summary\nTurned a struggling night shift into the steadiest team.\n" + "x" * 600
    _run_both_phases_recording(isolated_base, monkeypatch, master=MARKED_MASTER, tailor_output=leaked)

    events = [e for e in _events(isolate_event_log) if e.get("event") == "resume_marked_content"]
    assert len(events) == 1, events
    assert events[0]["severity"] == "HIGH"
    assert events[0]["count"] == 1
    checklist = _checklist_text(isolated_base)
    assert "Fix before sending" in checklist
    assert "steadiest team" in checklist


def test_clean_resume_logs_no_marked_content_event(
    isolated_base, mocked_orchestrator, isolate_event_log, monkeypatch
) -> None:
    _run_both_phases_recording(isolated_base, monkeypatch, master=MARKED_MASTER)

    assert not [e for e in _events(isolate_event_log) if e.get("event") == "resume_marked_content"]
    assert "Fix before sending" not in _checklist_text(isolated_base)


def test_unrecognized_marker_is_logged(isolated_base, mocked_orchestrator, isolate_event_log, monkeypatch) -> None:
    master = "# Resume\n\n## Experience\n- Ran the night shift.\n- This line is not for submitted resumes.\n"
    _run_both_phases_recording(isolated_base, monkeypatch, master=master)

    events = [e for e in _events(isolate_event_log) if e.get("event") == "master_resume_marker_unrecognized"]
    assert len(events) == 1, events
