"""Regression tests for #1263 — name-based "tracked" matching under a stale base prefix.

A ``jobs.prep_folder_path`` row can carry a base prefix that no longer matches
the live filesystem (e.g. after an install-directory move) while the folder
itself still sits, correctly named, directly under ``companies/``. The three
call sites that decide "is this folder tracked" — the watchdog's orphan
sweep, the prep-start quarantine, and the health check's orphan count — used
an exact-path comparison that fails in exactly that case. They now share
:func:`findajob.prep.quarantine.tracked_top_level_folder_names`, which matches
by folder name as long as the stored path's immediate parent segment is
literally ``companies`` — so a stage-subfolder path (``.../_applied/<name>``)
must NOT protect a top-level folder of the same name.
"""

from __future__ import annotations

import os
import sqlite3
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from findajob import audit
from findajob.db.migrate import apply_pending
from findajob.prep.quarantine import quarantine_stale_prep_folders, tracked_top_level_folder_names

# scripts/ isn't on sys.path by default; watchdog.py needs to be importable.
SCRIPTS = Path(__file__).parent.parent / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import watchdog  # noqa: E402


@pytest.fixture()
def db():
    conn = sqlite3.connect(":memory:")
    apply_pending(conn)
    conn.row_factory = sqlite3.Row
    yield conn
    conn.close()


@pytest.fixture(autouse=True)
def _patch_log(tmp_path, monkeypatch):
    monkeypatch.setattr(audit, "LOG_PATH", str(tmp_path / "events.jsonl"))


def _insert_job(conn, *, prep_folder_path: str) -> None:
    conn.execute(
        "INSERT INTO jobs (id, fingerprint, url, title, company, source, stage, prep_folder_path) "
        "VALUES ('job-1', 'fp1', 'https://example.com/job', 'Ops Manager', 'Acme', 'manual', "
        "'briefing_ready', ?)",
        (prep_folder_path,),
    )
    conn.commit()


def test_sweep_keeps_folder_tracked_under_stale_base_prefix(db, tmp_path, monkeypatch):
    """A row whose path carries a different base prefix but the same folder
    name, parented under a literal "companies" segment, still counts as
    tracked — the sweep must not quarantine the live folder."""
    monkeypatch.setattr(watchdog, "BASE", str(tmp_path))
    companies = tmp_path / "companies"
    companies.mkdir()
    folder = companies / "Acme_Ops_Manager_2026-04-23_120000"
    folder.mkdir()
    old_ts = (datetime.now(UTC) - timedelta(hours=3)).timestamp()
    os.utime(folder, (old_ts, old_ts))

    _insert_job(db, prep_folder_path="/old/base/companies/Acme_Ops_Manager_2026-04-23_120000")

    count = watchdog.sweep_orphan_folders(db)

    assert count == 0
    assert folder.is_dir()
    assert not (companies / ".stale").exists()


def test_quarantine_keeps_folder_tracked_under_stale_base_prefix(db, tmp_path):
    """Same stale-base-prefix scenario at prep-start quarantine time: the
    tracked sibling folder must stay in place, not move to .stale/."""
    companies = tmp_path / "companies"
    companies.mkdir()
    tracked_folder = companies / "Acme_Ops_Manager_2026-04-22_110000"
    tracked_folder.mkdir()
    current_folder = companies / "Acme_Ops_Manager_2026-04-22_120000"
    current_folder.mkdir()

    _insert_job(db, prep_folder_path="/old/base/companies/Acme_Ops_Manager_2026-04-22_110000")

    moved = quarantine_stale_prep_folders(
        db,
        str(companies),
        folder_prefix="Acme_Ops_Manager_",
        current_folder_name="Acme_Ops_Manager_2026-04-22_120000",
    )

    assert moved == []
    assert tracked_folder.is_dir()
    assert current_folder.is_dir()


def test_health_check_orphan_count_ignores_stale_base_prefix(db, tmp_path):
    """Mirrors the health check's orphan-folder count: a folder whose DB row
    carries a stale base prefix (same name, parent segment "companies") must
    not be counted as an orphan."""
    companies = tmp_path / "companies"
    companies.mkdir()
    (companies / "Acme_Ops_Manager_2026-04-23_120000").mkdir()

    _insert_job(db, prep_folder_path="/old/base/companies/Acme_Ops_Manager_2026-04-23_120000")

    folder_names = [d for d in os.listdir(companies) if not d.startswith(("_", ".")) and os.path.isdir(companies / d)]
    tracked = tracked_top_level_folder_names(db)
    orphan_folders = [name for name in folder_names if name not in tracked]

    assert orphan_folders == []


def test_sweep_moves_top_level_twin_of_stage_folder_stale_base_prefix(db, tmp_path, monkeypatch):
    """A row tracking a stage-folder path (parent segment "_applied", not
    "companies") must not protect a top-level folder of the same name — the
    sweep still moves it. Already passes on main; guards against the new
    name-based match over-matching."""
    monkeypatch.setattr(watchdog, "BASE", str(tmp_path))
    companies = tmp_path / "companies"
    companies.mkdir()
    folder = companies / "Acme_Ops_Manager_2026-04-23_120000"
    folder.mkdir()
    old_ts = (datetime.now(UTC) - timedelta(hours=3)).timestamp()
    os.utime(folder, (old_ts, old_ts))

    _insert_job(db, prep_folder_path="/old/base/companies/_applied/Acme_Ops_Manager_2026-04-23_120000")

    count = watchdog.sweep_orphan_folders(db)

    assert count == 1
    assert not folder.exists()
    assert (companies / ".stale" / folder.name).is_dir()
