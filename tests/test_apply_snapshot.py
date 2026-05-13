"""Tests for snapshot_applied_md_files — capture as-sent state on apply (#210)."""

from __future__ import annotations

import os
import shutil
import sqlite3
from datetime import UTC, datetime

import pytest

from findajob.actions import snapshot_applied_md_files

# ─── helper unit tests ───────────────────────────────────────────────────────


def test_copies_every_md(tmp_path):
    (tmp_path / "Resume.md").write_text("R")
    (tmp_path / "Cover.md").write_text("C")
    (tmp_path / "Briefing.md").write_text("B")

    created = snapshot_applied_md_files(tmp_path, date="2026-05-13")

    assert sorted(os.path.basename(p) for p in created) == [
        "Briefing.applied-2026-05-13.md",
        "Cover.applied-2026-05-13.md",
        "Resume.applied-2026-05-13.md",
    ]
    assert (tmp_path / "Resume.applied-2026-05-13.md").read_text() == "R"
    assert (tmp_path / "Cover.applied-2026-05-13.md").read_text() == "C"
    assert (tmp_path / "Briefing.applied-2026-05-13.md").read_text() == "B"


def test_skips_non_md_files(tmp_path):
    (tmp_path / "Resume.md").write_text("R")
    (tmp_path / "Resume.docx").write_bytes(b"binary")
    (tmp_path / "JD.txt").write_text("JD")
    (tmp_path / "Outreach to Friend.txt").write_text("hi")

    created = snapshot_applied_md_files(tmp_path, date="2026-05-13")

    assert [os.path.basename(p) for p in created] == ["Resume.applied-2026-05-13.md"]
    assert not list(tmp_path.glob("*.docx.applied-*"))
    assert not list(tmp_path.glob("*.txt.applied-*"))


def test_idempotent_same_day(tmp_path):
    (tmp_path / "Resume.md").write_text("R")

    first = snapshot_applied_md_files(tmp_path, date="2026-05-13")
    assert len(first) == 1

    # Mutate the original — second call must NOT overwrite the snapshot.
    (tmp_path / "Resume.md").write_text("R-mutated")

    second = snapshot_applied_md_files(tmp_path, date="2026-05-13")
    assert second == []
    assert (tmp_path / "Resume.applied-2026-05-13.md").read_text() == "R"


def test_does_not_snapshot_snapshots(tmp_path):
    (tmp_path / "Resume.md").write_text("R")
    # Pre-existing snapshot from a prior apply
    (tmp_path / "Resume.applied-2026-05-10.md").write_text("R-prior")

    created = snapshot_applied_md_files(tmp_path, date="2026-05-13")

    assert [os.path.basename(p) for p in created] == ["Resume.applied-2026-05-13.md"]
    # The pre-existing snapshot must not be doubly-snapshotted.
    assert not (tmp_path / "Resume.applied-2026-05-10.applied-2026-05-13.md").exists()


def test_missing_folder_returns_empty(tmp_path):
    nonexistent = tmp_path / "neverexisted"
    assert snapshot_applied_md_files(nonexistent, date="2026-05-13") == []


def test_default_date_is_today_utc(tmp_path):
    (tmp_path / "Resume.md").write_text("R")
    created = snapshot_applied_md_files(tmp_path)
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    expected = tmp_path / f"Resume.applied-{today}.md"
    assert str(expected) in created
    assert expected.exists()


# ─── integration: _move_folder_to_applied triggers the snapshot ───────────────


SCHEMA = """
CREATE TABLE jobs (
    id TEXT PRIMARY KEY,
    fingerprint TEXT UNIQUE NOT NULL,
    url TEXT NOT NULL,
    title TEXT NOT NULL,
    company TEXT NOT NULL,
    stage TEXT DEFAULT 'discovered',
    prep_folder_path TEXT
);
"""


@pytest.fixture()
def db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    yield conn
    conn.close()


def test_move_folder_to_applied_snapshots_md_files(db, tmp_path, monkeypatch):
    """End-to-end: moving a prep folder to _applied snapshots its *.md files."""
    # Lay out a realistic prep folder under a fake BASE/companies/.
    fake_base = tmp_path
    monkeypatch.setattr("findajob.web.routes.board_actions.BASE", str(fake_base))
    monkeypatch.setattr("findajob.actions.BASE", str(fake_base))

    src_folder = fake_base / "companies" / "Acme_Eng_2026-05-13_120000"
    src_folder.mkdir(parents=True)
    (src_folder / "Brock Resume.md").write_text("resume")
    (src_folder / "Brock Cover.md").write_text("cover")
    (src_folder / "Brock Resume.docx").write_bytes(b"\x50\x4b\x03\x04")  # binary placeholder

    db.execute(
        "INSERT INTO jobs (id, fingerprint, url, title, company, stage, prep_folder_path) "
        "VALUES ('j1', 'fp1', 'https://x.test', 'Engineer', 'Acme', 'materials_drafted', ?)",
        (str(src_folder),),
    )
    db.commit()

    from findajob.web.routes.board_actions import _move_folder_to_applied

    # Stub out log_event since it touches a file path that doesn't matter here.
    monkeypatch.setattr("findajob.web.routes.board_actions.log_event", lambda *a, **k: None)

    job = db.execute("SELECT id, company FROM jobs WHERE id='j1'").fetchone()
    moved = _move_folder_to_applied(db, job)
    assert moved is True

    # Folder is now under _applied/. Its *.md files have .applied-{date}.md siblings.
    dest = db.execute("SELECT prep_folder_path FROM jobs WHERE id='j1'").fetchone()["prep_folder_path"]
    assert dest.startswith(str(fake_base / "companies" / "_applied"))

    today = datetime.now(UTC).strftime("%Y-%m-%d")
    applied_dir = tmp_path / "companies" / "_applied" / src_folder.name
    assert (applied_dir / f"Brock Resume.applied-{today}.md").read_text() == "resume"
    assert (applied_dir / f"Brock Cover.applied-{today}.md").read_text() == "cover"
    # .docx never snapshotted
    assert not list(applied_dir.glob("*.docx.applied-*"))

    # Cleanup so other tests don't see this fake _applied tree.
    shutil.rmtree(tmp_path / "companies", ignore_errors=True)
