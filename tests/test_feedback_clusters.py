import sqlite3
import pytest
from findajob.db.migrate import apply_pending
from findajob.scoring import _feedback_clusters


@pytest.fixture
def conn(tmp_path):
    c = sqlite3.connect(tmp_path / "p.db")
    c.row_factory = sqlite3.Row
    apply_pending(c)
    c.commit()
    yield c
    c.close()


def test_clusters_group_by_reason_and_apply_exclusions(conn):
    conn.executemany(
        "INSERT INTO feedback_log (job_id, title, company, relevance_score, reject_reason) "
        "VALUES (?, ?, 'Acme', 8, ?)",
        [
            ("a", "Director Ops", "Too Senior"),
            ("b", "VP Ops", "Too Senior"),
            ("c", "Closed Role", "Stale/Closed"),   # housekeeping → excluded
        ],
    )
    conn.commit()
    clusters = _feedback_clusters(conn)
    assert clusters.get("Too Senior") == ["Director Ops", "VP Ops"]
    assert "Stale/Closed" not in clusters
