"""Stats throughput route — /stats/throughput SQL correctness + render (#196).

All-time per-ISO-week count of stage transitions into applied/interview/offer,
sourced from `audit_log` rows where `field_changed='stage'` and `new_value` is
one of the three throughput stages. Stacked-bar with one tick per audit event,
so re-applies (e.g., reactivated rows) count separately — the page measures
event throughput, not currently-applied job count.
"""

import json
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from findajob.onboarding import mark_complete
from findajob.web.app import create_app


def _seed_event(
    conn: sqlite3.Connection,
    *,
    job_id: str,
    field_changed: str,
    new_value: str,
    changed_at: str,
) -> None:
    conn.execute(
        "INSERT INTO audit_log (job_id, field_changed, old_value, new_value, changed_at) VALUES (?, ?, NULL, ?, ?)",
        (job_id, field_changed, new_value, changed_at),
    )


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    db = tmp_path / "pipeline.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE jobs ("
        "  id TEXT PRIMARY KEY, fingerprint TEXT, title TEXT, company TEXT, stage TEXT, "
        "  reject_reason TEXT"
        ")"
    )
    conn.execute(
        "CREATE TABLE audit_log (id INTEGER PRIMARY KEY, job_id TEXT, field_changed TEXT, "
        "old_value TEXT, new_value TEXT, changed_at TEXT, changed_by TEXT)"
    )
    conn.execute(
        "CREATE TABLE feedback_log ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL, title TEXT NOT NULL, "
        "company TEXT NOT NULL, relevance_score INTEGER, reject_reason TEXT NOT NULL, "
        "jd_excerpt TEXT DEFAULT '', created_at TEXT DEFAULT (datetime('now')))"
    )

    # Week 2026-W18 (Mon 2026-05-04 .. Sun 2026-05-10): 3 applied, 1 interview
    _seed_event(conn, job_id="j1", field_changed="stage", new_value="applied", changed_at="2026-05-04 10:00:00")
    _seed_event(conn, job_id="j2", field_changed="stage", new_value="applied", changed_at="2026-05-05 12:00:00")
    _seed_event(conn, job_id="j3", field_changed="stage", new_value="applied", changed_at="2026-05-10 18:00:00")
    _seed_event(conn, job_id="j4", field_changed="stage", new_value="interview", changed_at="2026-05-07 09:30:00")

    # Week 2026-W19 (Mon 2026-05-11 .. Sun 2026-05-17): 1 applied, 2 interview, 1 offer
    _seed_event(conn, job_id="j5", field_changed="stage", new_value="applied", changed_at="2026-05-12 11:00:00")
    _seed_event(conn, job_id="j6", field_changed="stage", new_value="interview", changed_at="2026-05-13 14:00:00")
    _seed_event(conn, job_id="j7", field_changed="stage", new_value="interview", changed_at="2026-05-15 10:00:00")
    _seed_event(conn, job_id="j8", field_changed="stage", new_value="offer", changed_at="2026-05-16 16:00:00")

    # Out-of-scope rows that MUST NOT leak into the payload — negative assertions
    # below check these don't appear (per feedback_negative_test_assertions).
    # 1) field_changed != 'stage' but new_value matches a throughput stage
    _seed_event(conn, job_id="j9", field_changed="user_notes", new_value="applied", changed_at="2026-05-05 10:00:00")
    # 2) field_changed = 'stage' but new_value is a non-throughput stage
    _seed_event(conn, job_id="j10", field_changed="stage", new_value="scored", changed_at="2026-05-05 10:00:00")
    _seed_event(conn, job_id="j11", field_changed="stage", new_value="rejected", changed_at="2026-05-05 10:00:00")
    _seed_event(conn, job_id="j12", field_changed="stage", new_value="manual_review", changed_at="2026-05-05 10:00:00")

    conn.commit()
    conn.close()
    companies = tmp_path / "companies"
    companies.mkdir()
    mark_complete(tmp_path)
    return TestClient(create_app(companies_root=companies, db_path=db, base_root=tmp_path))


def test_throughput_renders_200(client: TestClient) -> None:
    r = client.get("/stats/throughput")
    assert r.status_code == 200


def test_throughput_totals_match_seed(client: TestClient) -> None:
    r = client.get("/stats/throughput")
    body = r.text
    # Totals come from the seed: 4 applied (3 in W18 + 1 in W19), 3 interview
    # (1 in W18 + 2 in W19), 1 offer.
    # Assert each total card renders with its expected value adjacent to its
    # stage label — substring containment is sufficient given the template
    # shape (`<div>...applied</div><div>...4</div>`), but we also assert grand
    # total is right.
    assert 'Total: <span class="font-semibold">8</span> transition' in body
    # Per-stage totals appear in the totals grid cards.
    for stage, expected in (("applied", 4), ("interview", 3), ("offer", 1)):
        assert stage in body
        # The grid card layout: <div>label</div><div>value</div>. Look for the
        # label and the value text both — coarse-grained but catches drops.
        assert f">{expected}<" in body, f"expected total {expected} for {stage}"


def test_throughput_table_has_both_weeks(client: TestClient) -> None:
    r = client.get("/stats/throughput")
    body = r.text
    # W18 starts Monday 2026-05-04 → SQLite strftime('%Y-W%W', ...) → "2026-W18"
    # W19 starts Monday 2026-05-11 → "2026-W19"
    assert "2026-W18" in body
    assert "2026-W19" in body


def test_throughput_table_is_sorted_descending(client: TestClient) -> None:
    """Data table renders newest-week-first (operator reads recent activity at the top)."""
    r = client.get("/stats/throughput")
    body = r.text
    w18_idx = body.index("2026-W18")
    w19_idx = body.index("2026-W19")
    assert w19_idx < w18_idx, "expected W19 (newer) to appear before W18 (older) in the table"


def test_throughput_chart_payload_shape(client: TestClient) -> None:
    """The embedded JSON payload is what Chart.js consumes — assert its structure
    directly instead of inferring from rendered HTML."""
    r = client.get("/stats/throughput")
    body = r.text
    # Extract the embedded data block.
    needle = '<script id="throughput-data" type="application/json">'
    start = body.index(needle) + len(needle)
    end = body.index("</script>", start)
    payload = json.loads(body[start:end])

    assert payload["labels"] == ["2026-W18", "2026-W19"]
    series = {ds["label"]: ds["data"] for ds in payload["datasets"]}
    assert series == {
        "applied": [3, 1],
        "interview": [1, 2],
        "offer": [0, 1],
    }


def test_throughput_excludes_out_of_scope_rows(client: TestClient) -> None:
    """Negative assertions — the seed includes deliberately-poisoning rows that
    MUST NOT leak into the dashboard. If the WHERE clause loses its
    `field_changed='stage'` filter or its stage-set restriction, this catches
    it (per feedback_negative_test_assertions).
    """
    r = client.get("/stats/throughput")
    body = r.text
    needle = '<script id="throughput-data" type="application/json">'
    start = body.index(needle) + len(needle)
    end = body.index("</script>", start)
    payload = json.loads(body[start:end])

    # No third week should appear (we only seeded W18 and W19; out-of-scope rows
    # sit inside those weeks but in different field_changed / new_value combos).
    assert len(payload["labels"]) == 2

    # Per-stage totals stay at the in-scope counts. If field_changed='user_notes'
    # leaked, applied would be 5 not 4. If new_value='scored' or 'rejected'
    # leaked, a non-throughput dataset would appear in the payload.
    series = {ds["label"]: ds["data"] for ds in payload["datasets"]}
    assert set(series) == {"applied", "interview", "offer"}, "only throughput stages should appear as series"
    assert sum(series["applied"]) == 4, "applied total leaked an out-of-scope row"
    # Defensive negative-asserts against the specific poison values.
    assert "scored" not in series
    assert "rejected" not in series
    assert "manual_review" not in series


def test_throughput_empty_state(tmp_path: Path) -> None:
    """No audit_log rows → page renders 200 with empty-state copy, no 500."""
    db = tmp_path / "pipeline.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE jobs (id TEXT PRIMARY KEY, fingerprint TEXT, title TEXT, "
        "company TEXT, stage TEXT, reject_reason TEXT)"
    )
    conn.execute(
        "CREATE TABLE audit_log (id INTEGER PRIMARY KEY, job_id TEXT, field_changed TEXT, "
        "old_value TEXT, new_value TEXT, changed_at TEXT, changed_by TEXT)"
    )
    conn.execute(
        "CREATE TABLE feedback_log ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL, title TEXT NOT NULL, "
        "company TEXT NOT NULL, relevance_score INTEGER, reject_reason TEXT NOT NULL, "
        "jd_excerpt TEXT DEFAULT '', created_at TEXT DEFAULT (datetime('now')))"
    )
    conn.commit()
    conn.close()
    companies = tmp_path / "companies"
    companies.mkdir()
    mark_complete(tmp_path)
    client = TestClient(create_app(companies_root=companies, db_path=db, base_root=tmp_path))

    r = client.get("/stats/throughput")
    assert r.status_code == 200
    body = r.text
    assert "No stage transitions" in body
    # Total is 0 — the singular form should render ("0 transitions" via the
    # template's '' if grand_total == 1 else 's' guard).
    assert 'Total: <span class="font-semibold">0</span> transitions' in body
    # Chart payload should still be present and well-formed even when empty,
    # but no <canvas> element renders (empty-state branch).
    assert 'id="throughput-chart"' not in body


def test_throughput_ignores_null_changed_at(tmp_path: Path) -> None:
    """audit_log allows changed_at default of datetime('now'), but pre-existing
    rows or malformed inserts could carry NULL. The route filters them out so
    strftime doesn't emit NULL labels."""
    db = tmp_path / "pipeline.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE jobs (id TEXT PRIMARY KEY, fingerprint TEXT, title TEXT, "
        "company TEXT, stage TEXT, reject_reason TEXT)"
    )
    conn.execute(
        "CREATE TABLE audit_log (id INTEGER PRIMARY KEY, job_id TEXT, field_changed TEXT, "
        "old_value TEXT, new_value TEXT, changed_at TEXT, changed_by TEXT)"
    )
    conn.execute(
        "CREATE TABLE feedback_log ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL, title TEXT NOT NULL, "
        "company TEXT NOT NULL, relevance_score INTEGER, reject_reason TEXT NOT NULL, "
        "jd_excerpt TEXT DEFAULT '', created_at TEXT DEFAULT (datetime('now')))"
    )
    conn.execute(
        "INSERT INTO audit_log (job_id, field_changed, new_value, changed_at) VALUES ('jX', 'stage', 'applied', NULL)"
    )
    conn.execute(
        "INSERT INTO audit_log (job_id, field_changed, new_value, changed_at) "
        "VALUES ('jY', 'stage', 'applied', '2026-05-04 10:00:00')"
    )
    conn.commit()
    conn.close()
    companies = tmp_path / "companies"
    companies.mkdir()
    mark_complete(tmp_path)
    client = TestClient(create_app(companies_root=companies, db_path=db, base_root=tmp_path))

    r = client.get("/stats/throughput")
    assert r.status_code == 200
    body = r.text
    needle = '<script id="throughput-data" type="application/json">'
    start = body.index(needle) + len(needle)
    end = body.index("</script>", start)
    payload = json.loads(body[start:end])

    # Exactly one week (the non-NULL row), with applied=1.
    assert payload["labels"] == ["2026-W18"]
    series = {ds["label"]: ds["data"] for ds in payload["datasets"]}
    assert series["applied"] == [1]
