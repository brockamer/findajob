"""Tests for #1263: tracked-folder matching by name, not exact path.

A prep folder stays live on disk under ``companies/<name>`` even when the
``jobs.prep_folder_path`` stored for it carries a base prefix that no longer
matches reality (e.g. an install-directory move changed everything before
``companies/``). Before this fix, the nightly orphan sweep (watchdog), the
prep-start quarantine, and the health check's orphan-folder count all
compared the stored path byte-for-byte against the folder's real path, so a
stale prefix made a live folder look orphaned.

The fix: all three now go through
:func:`findajob.prep.quarantine.tracked_top_level_folder_names`, which
matches by folder *name* at the top level of ``companies/`` — the basename
of any non-empty ``prep_folder_path`` whose parent directory is literally
named ``companies``. A row tracking a stage subfolder (``.../_applied/<name>``)
has a parent named ``_applied``, not ``companies``, so it does not protect a
top-level folder of the same name (test 4 below).
"""

from __future__ import annotations

import os
import sqlite3
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from findajob import audit

SCRIPTS = Path(__file__).parent.parent / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import watchdog  # noqa: E402

from findajob.prep.quarantine import (  # noqa: E402
    quarantine_stale_prep_folders,
    tracked_top_level_folder_names,
)


@pytest.fixture(autouse=True)
def _redirect_log(tmp_path, monkeypatch):
    monkeypatch.setattr(audit, "LOG_PATH", str(tmp_path / "events.jsonl"))


@pytest.fixture()
def db():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE jobs (id INTEGER PRIMARY KEY, prep_folder_path TEXT)")
    yield conn
    conn.close()


def _old_mtime() -> float:
    return (datetime.now(UTC) - timedelta(hours=3)).timestamp()


def test_sweep_keeps_folder_tracked_under_stale_base_prefix(db, tmp_path, monkeypatch):
    """A row whose saved path has a different base prefix still protects the
    live top-level folder of the same name from the nightly orphan sweep."""
    monkeypatch.setattr(watchdog, "BASE", str(tmp_path))
    companies = tmp_path / "companies"
    companies.mkdir()
    folder = companies / "Acme_Ops_Manager_2026-04-23_120000"
    folder.mkdir()
    old_ts = _old_mtime()
    os.utime(folder, (old_ts, old_ts))

    db.execute(
        "INSERT INTO jobs (prep_folder_path) VALUES (?)",
        ("/old/install/base/companies/Acme_Ops_Manager_2026-04-23_120000",),
    )
    db.commit()

    count = watchdog.sweep_orphan_folders(db)

    assert count == 0
    assert folder.is_dir()
    assert not (companies / ".stale").exists()


def test_quarantine_keeps_folder_tracked_under_stale_base_prefix(db, tmp_path):
    """Same stale-base-prefix scenario, but for the prep-start quarantine."""
    companies = tmp_path / "companies"
    companies.mkdir()
    folder = companies / "Acme_Ops_Manager_2026-04-22_110000"
    folder.mkdir()

    db.execute(
        "INSERT INTO jobs (prep_folder_path) VALUES (?)",
        ("/old/install/base/companies/Acme_Ops_Manager_2026-04-22_110000",),
    )
    db.commit()

    moved = quarantine_stale_prep_folders(
        db,
        str(companies),
        folder_prefix="Acme_Ops_Manager_",
        current_folder_name="Acme_Ops_Manager_2026-04-22_120000",
    )

    assert moved == []
    assert folder.is_dir()


def test_health_check_orphan_count_ignores_stale_base_prefix(tmp_path):
    """The health check's orphan-folder count mirrors health_check.py's logic:
    list top-level companies/ dirs, subtract names returned by the shared
    tracked-folder helper. A stale base prefix must not inflate the count."""
    companies = tmp_path / "companies"
    companies.mkdir()
    (companies / "Acme_Ops_Manager_2026-04-23_120000").mkdir()

    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE jobs (id INTEGER PRIMARY KEY, prep_folder_path TEXT)")
    conn.execute(
        "INSERT INTO jobs (prep_folder_path) VALUES (?)",
        ("/old/install/base/companies/Acme_Ops_Manager_2026-04-23_120000",),
    )
    conn.commit()

    folder_names = [
        d for d in os.listdir(companies) if not d.startswith(("_", ".")) and os.path.isdir(os.path.join(companies, d))
    ]
    tracked_names = tracked_top_level_folder_names(conn)
    orphan_folders = [name for name in folder_names if name not in tracked_names]

    assert orphan_folders == []


def test_sweep_moves_top_level_twin_of_stage_folder_stale_base_prefix(db, tmp_path, monkeypatch):
    """A row tracking a stage-folder path (parent dir named ``_applied``, not
    ``companies``) does not protect a top-level folder of the same name —
    the sweep still moves it. Already passes on main; guards against the
    name-based match becoming too permissive."""
    monkeypatch.setattr(watchdog, "BASE", str(tmp_path))
    companies = tmp_path / "companies"
    companies.mkdir()
    name = "Acme_Ops_Manager_2026-04-23_120000"
    top_level_twin = companies / name
    top_level_twin.mkdir()
    old_ts = _old_mtime()
    os.utime(top_level_twin, (old_ts, old_ts))

    db.execute(
        "INSERT INTO jobs (prep_folder_path) VALUES (?)",
        (f"/old/install/base/companies/_applied/{name}",),
    )
    db.commit()

    count = watchdog.sweep_orphan_folders(db)

    assert count == 1
    assert not top_level_twin.exists()
    assert (companies / ".stale" / name).is_dir()
