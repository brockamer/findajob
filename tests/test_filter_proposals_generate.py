import json
import sqlite3
import pytest
from findajob.db.migrate import apply_pending
from findajob import analyze_feedback, filter_proposals


@pytest.fixture
def conn(tmp_path, monkeypatch):
    c = sqlite3.connect(tmp_path / "p.db")
    c.row_factory = sqlite3.Row
    apply_pending(c)
    c.commit()
    # Title-signal reasons that drive the miner.
    monkeypatch.setattr(
        analyze_feedback, "load_reject_reasons",
        lambda: (("Skills Mismatch",), frozenset({"Skills Mismatch"})),
    )
    yield c
    c.close()


def _seed_rejection(c, title, reason="Skills Mismatch", score=9):
    c.execute(
        "INSERT INTO feedback_log (job_id, title, company, relevance_score, reject_reason) "
        "VALUES (?, ?, ?, ?, ?)",
        (f"id-{title}", title, "Acme", score, reason),
    )


def test_generate_inserts_pending_proposals(conn):
    for t in ("Fleet Readiness Manager", "Fleet Readiness Lead", "SFBA Fleet Readiness"):
        _seed_rejection(conn, t)
    conn.commit()

    n = filter_proposals.generate_proposals(conn)
    assert n >= 1
    rows = conn.execute("SELECT pattern, pattern_norm, status FROM filter_proposals").fetchall()
    assert any(r["pattern_norm"] == r["pattern"] for r in rows)
    assert all(r["status"] == "pending" for r in rows)
    assert any("fleet" in r["pattern"] for r in rows)


def test_generate_is_idempotent(conn):
    for t in ("Fleet Readiness Manager", "Fleet Readiness Lead", "SFBA Fleet Readiness"):
        _seed_rejection(conn, t)
    conn.commit()
    filter_proposals.generate_proposals(conn)
    before = conn.execute("SELECT COUNT(*) FROM filter_proposals").fetchone()[0]
    filter_proposals.generate_proposals(conn)
    after = conn.execute("SELECT COUNT(*) FROM filter_proposals").fetchone()[0]
    assert before == after  # INSERT OR IGNORE on pattern_norm


def test_preview_counts_active_matches_and_danger(conn):
    for t in ("Fleet Readiness Manager", "Fleet Readiness Lead", "SFBA Fleet Readiness"):
        _seed_rejection(conn, t)
    # An active, unactioned, high-score job the rule WOULD reject → danger.
    conn.execute(
        "INSERT INTO jobs (id, fingerprint, url, source, title, company, relevance_score, stage) "
        "VALUES ('j1', 'fp-j1', 'https://example.com/j1', 'test', "
        "'Senior Fleet Readiness Director', 'Beta', 8, 'scored')"
    )
    conn.commit()
    filter_proposals.generate_proposals(conn)
    row = conn.execute(
        "SELECT preview_count, preview_danger_count FROM filter_proposals "
        "WHERE pattern LIKE '%fleet%readiness%'"
    ).fetchone()
    assert row["preview_count"] >= 1
    assert row["preview_danger_count"] >= 1
