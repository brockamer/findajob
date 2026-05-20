"""#738 — Phase B progress signals: per-step ntfy + sidecar file.

The briefing-first gate (#691) split prep into Phase A (briefing + scores)
and Phase B (resume → cover → critique → outreach). The split shipped
correctly, but Phase B emitted exactly one ntfy notification (terminal
"Drafts ready: ..."), leaving the operator with Phase A's stale "Briefing
ready" notif on their phone for ~2-5 minutes while Phase B ran silently.
The operator-reported symptom was "it only generated the briefing again"
— a UX bug, not a code regression.

These tests pin two contracts:

1. ``_run_prep_phase_b`` emits five ``Phase B:``-prefixed progress
   notifications in order (resume, changes, cover, critique, outreach),
   followed by the terminal ``Drafts ready: ...`` notif.

2. ``_run_prep_phase_b`` writes a ``.phase_b_step`` sidecar file in the
   prep folder at each step (``"{n}/5 {label}\\n"``). The materials page
   reads this sidecar to render "Phase B in progress — step N of 5: ..."
   instead of the generic "⟳ Regenerating…" label.

Fixtures here are local copies of the ones in ``test_prep_phase_split.py``
to keep #738 tests self-contained (ruff F811 trips on cross-file fixture
imports + the upstream file is not a conftest).
"""

from __future__ import annotations

import sqlite3
import subprocess
from pathlib import Path

import pytest

from tests.test_prep_phase_split import (
    COMPANY,
    FAKE_JD,
    FAKE_MASTER_RESUME,
    FAKE_PROFILE,
    FP,
    JOB_ID,
    SCHEMA,
    TITLE,
    URL,
    _fake_run_role,
)


@pytest.fixture()
def isolated_base(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Self-contained BASE with profile + master resume + empty
    companies/ + an empty DB."""
    candidate = tmp_path / "candidate_context"
    candidate.mkdir()
    (candidate / "profile.md").write_text(FAKE_PROFILE)
    (candidate / "master_resume.md").write_text(FAKE_MASTER_RESUME)
    (tmp_path / "companies").mkdir()
    (tmp_path / "data").mkdir()
    db_path = tmp_path / "data" / "pipeline.db"

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
    """Stub out LLM, docx, ntfy, voice, find_contacts, validate_resume."""
    import findajob.prep.orchestrator as orch

    monkeypatch.setattr(orch, "run_role", _fake_run_role)
    monkeypatch.setattr(orch, "render_md_to_docx", lambda *args, **kwargs: None)
    monkeypatch.setattr(orch, "_add_cover_letter_spacing", lambda *args, **kwargs: None)
    monkeypatch.setattr(orch, "_linkify_contact_info", lambda s: s)
    monkeypatch.setattr(orch, "quick_notify", lambda *args, **kwargs: None)
    monkeypatch.setattr(orch, "load_voice_samples", lambda: "")
    monkeypatch.setattr(orch, "read_file_prefix", lambda: "TST")
    monkeypatch.setattr(orch, "quarantine_stale_prep_folders", lambda *args, **kwargs: None)

    real_subprocess_run = subprocess.run

    def _fake_subprocess_run(cmd, *args, **kwargs):
        if isinstance(cmd, list) and len(cmd) > 1 and "find_contacts.py" in str(cmd[1]):
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout=b"", stderr=b"")
        if isinstance(cmd, list) and len(cmd) > 1 and "validate_resume.py" in str(cmd[1]):
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout=b"", stderr=b"")
        return real_subprocess_run(cmd, *args, **kwargs)

    monkeypatch.setattr(orch.subprocess, "run", _fake_subprocess_run)


@pytest.fixture()
def isolate_event_log(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    log_path = tmp_path / "pipeline.jsonl"
    monkeypatch.setattr("findajob.audit.LOG_PATH", str(log_path))
    return log_path


def _seed_briefing_ready_job(db_path: str) -> None:
    """Seed a job at stage=prep_in_progress (Phase B's entry stage when
    dispatched from /materials/{fp}/continue-prep)."""
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO jobs (id, fingerprint, url, title, company, source, stage, raw_jd_text) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (JOB_ID, FP, URL, TITLE, COMPANY, "test", "prep_in_progress", FAKE_JD),
    )
    conn.commit()
    conn.close()


def _stand_up_briefing_folder(base: Path, job_id: str) -> Path:
    """Create a prep folder with a valid briefing.md that Phase B will
    re-read on entry. Mirrors what Phase A would have produced."""
    folder = base / "companies" / "Acme" / f"prep-{job_id}"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "briefing.md").write_text("# Briefing\n\nContext.\n\n## Overall Recommendation\n\nApply.\n")

    db_path = base / "data" / "pipeline.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "UPDATE jobs SET prep_folder_path=?, fit_score=80, probability_score=60 WHERE id=?",
        (str(folder), job_id),
    )
    conn.commit()
    conn.close()
    return folder


@pytest.fixture()
def notify_spy(monkeypatch: pytest.MonkeyPatch, isolated_base: Path) -> list[tuple[str, str]]:
    """Record each (message, sidecar_contents) pair at quick_notify call
    time. The sidecar snapshot proves writes happen *before* the notify,
    so a reader (materials page) querying mid-flight sees the current
    step, not the previous one."""
    import findajob.prep.orchestrator as orch

    spy: list[tuple[str, str]] = []

    def _spy(msg: str) -> None:
        conn = sqlite3.connect(str(isolated_base / "data" / "pipeline.db"))
        row = conn.execute("SELECT prep_folder_path FROM jobs WHERE id=?", (JOB_ID,)).fetchone()
        conn.close()
        sidecar_path = Path(row[0]) / ".phase_b_step" if row and row[0] else None
        sidecar = sidecar_path.read_text().strip() if sidecar_path and sidecar_path.exists() else ""
        spy.append((msg, sidecar))

    monkeypatch.setattr(orch, "quick_notify", _spy)
    return spy


def test_phase_b_emits_five_progress_notifs_then_terminal(
    isolated_base, mocked_orchestrator, isolate_event_log, notify_spy
) -> None:
    """Phase B fires one ``Phase B:`` progress notif per step (5 total),
    in order: resume → changes → cover → critique → outreach. After all
    five, the terminal ``Drafts ready: ...`` notif fires once."""
    from findajob.prep.orchestrator import _run_prep_phase_b

    db_path = str(isolated_base / "data" / "pipeline.db")
    _seed_briefing_ready_job(db_path)
    _stand_up_briefing_folder(isolated_base, JOB_ID)

    _run_prep_phase_b(COMPANY, TITLE, URL, JOB_ID)

    messages = [m for m, _ in notify_spy]

    phase_b_msgs = [m for m in messages if m.startswith("Phase B:")]
    assert len(phase_b_msgs) == 5, (
        f"expected 5 'Phase B:' progress notifications, got {len(phase_b_msgs)}: {phase_b_msgs}"
    )

    expected_keywords = ["resume", "change", "cover", "critique", "outreach"]
    for i, keyword in enumerate(expected_keywords):
        assert keyword.lower() in phase_b_msgs[i].lower(), (
            f"Phase B notif #{i + 1} should mention '{keyword}'; got {phase_b_msgs[i]!r}"
        )

    drafts_ready = [m for m in messages if m.startswith("Drafts ready:")]
    assert len(drafts_ready) == 1, f"expected exactly one 'Drafts ready:' notif, got {drafts_ready}"

    last_phase_b_idx = max(i for i, m in enumerate(messages) if m.startswith("Phase B:"))
    drafts_ready_idx = messages.index(drafts_ready[0])
    assert drafts_ready_idx > last_phase_b_idx, (
        f"'Drafts ready' must fire AFTER all Phase B progress notifs; "
        f"got drafts at {drafts_ready_idx}, last Phase B at {last_phase_b_idx}"
    )


def test_phase_b_sidecar_advances_with_each_step(
    isolated_base, mocked_orchestrator, isolate_event_log, notify_spy
) -> None:
    """The ``.phase_b_step`` sidecar updates BEFORE each notify, so a
    reader (materials page) sees the current step in flight, not the
    previous one."""
    from findajob.prep.orchestrator import _run_prep_phase_b

    db_path = str(isolated_base / "data" / "pipeline.db")
    _seed_briefing_ready_job(db_path)
    _stand_up_briefing_folder(isolated_base, JOB_ID)

    _run_prep_phase_b(COMPANY, TITLE, URL, JOB_ID)

    progress_snaps = [s for m, s in notify_spy if m.startswith("Phase B:")]
    assert len(progress_snaps) == 5

    for i, snap in enumerate(progress_snaps, start=1):
        assert snap.startswith(f"{i}/5"), f"sidecar snapshot at notif #{i} should start with '{i}/5'; got {snap!r}"


def test_phase_b_clears_stale_sidecar_on_entry(
    isolated_base, mocked_orchestrator, isolate_event_log, notify_spy
) -> None:
    """Pre-existing sidecar from a prior failed run must NOT bleed into a
    retry. Phase B clears the sidecar after the outdir-validity check and
    before any work, so the materials page can't show "step 3/5" from a
    failed run during the 2–5 sec subprocess startup before Stage 4 fires.

    Advisor catch (pre-PR): walk-through showed Phase B retries would
    briefly display the stale label as "wait, did I go backward?" to the
    operator. Pin the clear at the orchestrator layer (closest to the
    contract that owns the sidecar)."""
    from findajob.prep.orchestrator import _run_prep_phase_b

    db_path = str(isolated_base / "data" / "pipeline.db")
    _seed_briefing_ready_job(db_path)
    folder = _stand_up_briefing_folder(isolated_base, JOB_ID)

    # Simulate stale state: write the sidecar BEFORE Phase B runs.
    (folder / ".phase_b_step").write_text("3/5 cover\n")

    _run_prep_phase_b(COMPANY, TITLE, URL, JOB_ID)

    # First Phase B notif's sidecar snapshot must be the fresh "1/5 resume",
    # NOT the stale "3/5 cover". The clear-on-entry guarantees this.
    first_phase_b = next((s for m, s in notify_spy if m.startswith("Phase B:")), None)
    assert first_phase_b is not None, "Phase B notif should have fired"
    assert first_phase_b.startswith("1/5"), (
        f"first sidecar snapshot must be '1/5 resume' after clear-on-entry; "
        f"got {first_phase_b!r} (stale sidecar bled through)"
    )


def test_phase_b_failure_resets_stage_to_briefing_ready(
    isolated_base, mocked_orchestrator, isolate_event_log, monkeypatch
) -> None:
    """When a Phase B subprocess fails, stage rolls back to briefing_ready
    so the materials page renders the briefing-first gate (not the Phase
    B progress label) on the next load."""
    import findajob.prep.orchestrator as orch
    from findajob.prep.orchestrator import _run_prep_phase_b

    db_path = str(isolated_base / "data" / "pipeline.db")
    _seed_briefing_ready_job(db_path)
    _stand_up_briefing_folder(isolated_base, JOB_ID)

    def _failing_subprocess_run(cmd, *args, **kwargs):
        if isinstance(cmd, list) and len(cmd) > 1 and "find_contacts.py" in str(cmd[1]):
            raise subprocess.CalledProcessError(1, cmd)
        if isinstance(cmd, list) and len(cmd) > 1 and "validate_resume.py" in str(cmd[1]):
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout=b"", stderr=b"")
        raise RuntimeError(f"unexpected subprocess call: {cmd}")

    monkeypatch.setattr(orch.subprocess, "run", _failing_subprocess_run)

    with pytest.raises(SystemExit):
        _run_prep_phase_b(COMPANY, TITLE, URL, JOB_ID)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT stage FROM jobs WHERE id=?", (JOB_ID,)).fetchone()
    conn.close()
    assert row["stage"] == "briefing_ready", f"failure must reset stage to briefing_ready; got {row['stage']!r}"


def test_phase_b_failure_fires_single_notification_not_duplicate(
    isolated_base, mocked_orchestrator, isolate_event_log, monkeypatch, notify_spy
) -> None:
    """Pre-#738 the failure path called ``quick_notify`` on two adjacent
    lines, double-pinging the operator. Pin single-fire."""
    import findajob.prep.orchestrator as orch
    from findajob.prep.orchestrator import _run_prep_phase_b

    db_path = str(isolated_base / "data" / "pipeline.db")
    _seed_briefing_ready_job(db_path)
    _stand_up_briefing_folder(isolated_base, JOB_ID)

    def _failing_subprocess_run(cmd, *args, **kwargs):
        if isinstance(cmd, list) and len(cmd) > 1 and "find_contacts.py" in str(cmd[1]):
            raise subprocess.CalledProcessError(1, cmd)
        if isinstance(cmd, list) and len(cmd) > 1 and "validate_resume.py" in str(cmd[1]):
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout=b"", stderr=b"")
        raise RuntimeError(f"unexpected subprocess call: {cmd}")

    monkeypatch.setattr(orch.subprocess, "run", _failing_subprocess_run)

    with pytest.raises(SystemExit):
        _run_prep_phase_b(COMPANY, TITLE, URL, JOB_ID)

    failure_notifs = [m for m, _ in notify_spy if "Phase B failed" in m]
    assert len(failure_notifs) == 1, (
        f"failure must fire exactly one 'Phase B failed' notif; got {len(failure_notifs)}: {failure_notifs}"
    )
