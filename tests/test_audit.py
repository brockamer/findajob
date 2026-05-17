"""Unit tests for findajob.audit.write_audit.

write_audit is the only audit-log writer; every action helper in
findajob.actions calls it. The commit=False kwarg landed in #707 so callers
that need to compose multiple writes into one transaction (e.g.,
reattribute_from_archive) can run write_audit + UPDATE in the same atomic
block.
"""

import sqlite3

import pytest

from findajob.audit import write_audit

SCHEMA = """
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


@pytest.fixture()
def db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    yield conn
    conn.close()


class TestWriteAudit:
    def test_commit_true_default_persists_row(self, db):
        write_audit(db, "job_abc", "stage", "scored", "applied")

        # A rollback after the call must NOT undo the row — the default commit landed it.
        db.rollback()
        rows = db.execute("SELECT * FROM audit_log WHERE job_id='job_abc'").fetchall()
        assert len(rows) == 1
        assert rows[0]["field_changed"] == "stage"
        assert rows[0]["old_value"] == "scored"
        assert rows[0]["new_value"] == "applied"

    def test_commit_false_leaves_row_in_open_transaction(self, db):
        """commit=False writes the INSERT to the connection but skips conn.commit().
        The caller is then free to compose more writes and commit (or rollback) atomically."""
        write_audit(db, "job_abc", "stage", "scored", "applied", commit=False)

        # Same connection sees the row (open transaction)
        rows = db.execute("SELECT * FROM audit_log WHERE job_id='job_abc'").fetchall()
        assert len(rows) == 1

        # Caller-issued rollback discards it
        db.rollback()
        rows = db.execute("SELECT * FROM audit_log WHERE job_id='job_abc'").fetchall()
        assert len(rows) == 0

    def test_commit_false_with_changed_by(self, db):
        """commit=False composes with the changed_by kwarg (both paths through the if/else)."""
        write_audit(db, "job_abc", "stage", "applied", "rejected", changed_by="user", commit=False)
        db.rollback()
        rows = db.execute("SELECT * FROM audit_log WHERE job_id='job_abc'").fetchall()
        assert len(rows) == 0
