"""Quarantine stale prep folders before a new prep run.

Each prep run mints a fresh ``{company}_{title}_{date}_{HHMMSS}`` folder
but only the latest is written to ``jobs.prep_folder_path``. Regenerate
clicks, concurrent prep races, or failed runs that never promoted to
``materials_drafted`` leave older folders orphaned on disk (#174).

This module is called at prep start, before the new folder is created.
Quarantined folders move to ``companies/.stale/`` rather than being
``rmtree``-d, so a concurrent prep that's still writing won't lose data.

Extracted from ``utils.py`` in M4.E2.I2 (#550). No logic changes.
Lives in ``findajob.prep`` (the package M3 created for prep
orchestration) rather than at the top level — folder maintenance is
prep-domain, not a cross-cutting utility.
"""

from __future__ import annotations

import os
import shutil
import sqlite3

from findajob.audit import log_event


def quarantine_stale_prep_folders(
    conn: sqlite3.Connection,
    companies_dir: str,
    folder_prefix: str,
    current_folder_name: str,
) -> list[str]:
    """Move abandoned prep folders matching ``folder_prefix`` into ``companies_dir/.stale/``.

    Called at prep start, before the new folder is created. Only folders whose
    name starts with ``folder_prefix`` are considered. A folder is **kept** if:
      * its basename equals ``current_folder_name`` (this run's folder), or
      * its absolute path appears as ``prep_folder_path`` on any jobs row, or
      * its name starts with ``_`` (stage holding directories:
        ``_applied``, ``_rejected``, ``_waitlisted``), or
      * its name equals ``.stale``, or
      * it is a regular file, not a directory.

    Everything else is moved into ``companies_dir/.stale/``. Rather than
    ``rmtree`` — which would destroy data if a concurrent prep is still writing
    — quarantining is reversible. Name collisions inside ``.stale/`` are
    disambiguated with a short random suffix. Returns the list of moved
    basenames (empty if nothing matched).
    """
    try:
        entries = os.listdir(companies_dir)
    except FileNotFoundError:
        return []

    tracked: set[str] = {
        row[0]
        for row in conn.execute(
            "SELECT prep_folder_path FROM jobs WHERE prep_folder_path IS NOT NULL AND prep_folder_path != ''"
        ).fetchall()
    }

    stale_dir = os.path.join(companies_dir, ".stale")
    moved: list[str] = []
    for entry in entries:
        if entry == current_folder_name or entry == ".stale" or entry.startswith("_"):
            continue
        if not entry.startswith(folder_prefix):
            continue
        src = os.path.join(companies_dir, entry)
        if not os.path.isdir(src):
            continue
        if src in tracked:
            continue
        os.makedirs(stale_dir, exist_ok=True)
        dest = os.path.join(stale_dir, entry)
        if os.path.exists(dest):
            dest = os.path.join(stale_dir, f"{entry}_{os.urandom(2).hex()}")
        shutil.move(src, dest)
        moved.append(entry)

    if moved:
        log_event("stale_prep_folders_quarantined", folders=moved, kept=current_folder_name)
    return moved
