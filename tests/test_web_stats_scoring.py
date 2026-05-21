"""Stats scoring route — /stats/scoring rendering + SQL correctness (#194).

Four score columns: relevance_score + interview_likelihood (1-10 int, scorer
populated) and fit_score + probability_score (0-100 float, prep Phase B
populated). "Scored jobs in last 30 days" is sourced from audit_log on
field_changed='stage' AND new_value='scored' — created_at is ingest time,
not score time, so it cannot be used (the AC named it but the semantics
were wrong; see #194 Session 2026-05-21).
"""

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from findajob.onboarding import mark_complete
from findajob.web.app import create_app


def _ts(days_ago: int, hour: int = 12) -> str:
    dt = datetime.now(UTC) - timedelta(days=days_ago)
    dt = dt.replace(hour=hour, minute=0, second=0, microsecond=0)
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _seed_job(
    conn: sqlite3.Connection,
    job_id: str,
    *,
    relevance: int | None,
    interview: int | None,
    fit: float | None,
    probability: float | None,
    scored_days_ago: int = 5,
) -> None:
    conn.execute(
        "INSERT INTO jobs (id, fingerprint, title, company, stage, "
        "relevance_score, interview_likelihood, fit_score, probability_score) "
        "VALUES (?, ?, ?, ?, 'scored', ?, ?, ?, ?)",
        (job_id, f"fp_{job_id}", f"Title {job_id}", f"Co {job_id}", relevance, interview, fit, probability),
    )
    conn.execute(
        "INSERT INTO audit_log (job_id, field_changed, old_value, new_value, changed_at, changed_by) "
        "VALUES (?, 'stage', 'manual_review', 'scored', ?, 'system')",
        (job_id, _ts(scored_days_ago)),
    )


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    db = tmp_path / "pipeline.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE jobs ("
        "  id TEXT PRIMARY KEY, fingerprint TEXT, title TEXT, company TEXT, stage TEXT, "
        "  relevance_score INTEGER, interview_likelihood INTEGER, "
        "  fit_score REAL, probability_score REAL"
        ")"
    )
    conn.execute(
        "CREATE TABLE audit_log (id INTEGER PRIMARY KEY, job_id TEXT, field_changed TEXT, "
        "old_value TEXT, new_value TEXT, changed_at TEXT, changed_by TEXT)"
    )
    # Seed a spread of jobs scored within the window with varied scores.
    # relevance/interview integers spanning 1-10; fit/probability floats spanning 0-100.
    _seed_job(conn, "j1", relevance=8, interview=7, fit=72.0, probability=55.0)
    _seed_job(conn, "j2", relevance=8, interview=6, fit=68.5, probability=42.0)
    _seed_job(conn, "j3", relevance=5, interview=4, fit=None, probability=None)
    _seed_job(conn, "j4", relevance=3, interview=2, fit=None, probability=None)
    _seed_job(conn, "j5", relevance=10, interview=9, fit=88.0, probability=80.0)
    # Outside the 30-day window — must NOT be counted.
    _seed_job(conn, "j_old", relevance=8, interview=7, fit=70.0, probability=50.0, scored_days_ago=45)
    conn.commit()
    conn.close()
    companies = tmp_path / "companies"
    companies.mkdir()
    mark_complete(tmp_path)
    return TestClient(create_app(companies_root=companies, db_path=db, base_root=tmp_path))


def test_scoring_renders_with_four_histograms(client: TestClient) -> None:
    """GET /stats/scoring returns 200 and renders four score-distribution charts."""
    r = client.get("/stats/scoring")
    assert r.status_code == 200
    # One canvas per score type — four histograms total.
    assert 'id="scoring-relevance-chart"' in r.text
    assert 'id="scoring-interview-chart"' in r.text
    assert 'id="scoring-fit-chart"' in r.text
    assert 'id="scoring-probability-chart"' in r.text
    # Chart data payload anchor exists.
    assert 'id="scoring-chart-data"' in r.text


def test_int_score_bucketing(client: TestClient) -> None:
    """relevance_score bucketing: each integer 1..10 gets its own bucket.

    Fixture seeds relevance values: 8, 8, 5, 3, 10 within the window.
    Bucket 8 should show count=2, buckets 3/5/10 count=1, others zero.
    """
    import json

    r = client.get("/stats/scoring")
    assert r.status_code == 200
    # Pull the JSON payload to assert the actual histogram shape.
    start = r.text.index('id="scoring-chart-data"')
    block_start = r.text.index(">", start) + 1
    block_end = r.text.index("</script>", block_start)
    payload = json.loads(r.text[block_start:block_end])
    relevance = {b["label"]: b["count"] for b in payload["relevance"]}
    assert relevance["8"] == 2
    assert relevance["5"] == 1
    assert relevance["3"] == 1
    assert relevance["10"] == 1
    assert relevance["1"] == 0
    assert relevance["7"] == 0


def test_float_score_bucketing(client: TestClient) -> None:
    """fit_score bucketing: decile buckets, 100 falls in last bucket.

    Fixture seeds fit values: 72.0, 68.5, 88.0 (j3/j4 are NULL, must be excluded).
    Buckets: 70-79 → 1 (72.0), 60-69 → 1 (68.5), 80-89 → 1 (88.0), others zero.
    """
    import json

    r = client.get("/stats/scoring")
    start = r.text.index('id="scoring-chart-data"')
    block_start = r.text.index(">", start) + 1
    block_end = r.text.index("</script>", block_start)
    payload = json.loads(r.text[block_start:block_end])
    fit = {b["label"]: b["count"] for b in payload["fit"]}
    assert fit["70-79"] == 1  # 72.0
    assert fit["60-69"] == 1  # 68.5
    assert fit["80-89"] == 1  # 88.0
    assert fit["0-9"] == 0
    assert fit["90-100"] == 0


def test_null_scores_excluded_from_histogram(client: TestClient) -> None:
    """NULL fit_score / probability_score values don't add to any bucket."""
    import json

    r = client.get("/stats/scoring")
    start = r.text.index('id="scoring-chart-data"')
    block_start = r.text.index(">", start) + 1
    block_end = r.text.index("</script>", block_start)
    payload = json.loads(r.text[block_start:block_end])
    # Fixture has 5 in-window jobs total, of which 3 have a fit_score.
    fit_total = sum(b["count"] for b in payload["fit"])
    assert fit_total == 3
    # Coverage subtitle reflects the 3-of-5 reality.
    assert "3 of 5 scored jobs have a value" in r.text


def test_out_of_window_jobs_excluded(client: TestClient) -> None:
    """Job scored 45 days ago does not contribute to any histogram or total."""
    import json

    r = client.get("/stats/scoring")
    start = r.text.index('id="scoring-chart-data"')
    block_start = r.text.index(">", start) + 1
    block_end = r.text.index("</script>", block_start)
    payload = json.loads(r.text[block_start:block_end])
    # Total relevance values in-window: j1+j2+j3+j4+j5 = 5 (j_old excluded).
    relevance_total = sum(b["count"] for b in payload["relevance"])
    assert relevance_total == 5


def test_empty_window_renders_zero_state(tmp_path: Path) -> None:
    """No scored transitions in window → 200 with zero-state message, no crash."""
    db = tmp_path / "pipeline.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE jobs ("
        "  id TEXT PRIMARY KEY, fingerprint TEXT, title TEXT, company TEXT, stage TEXT, "
        "  relevance_score INTEGER, interview_likelihood INTEGER, "
        "  fit_score REAL, probability_score REAL"
        ")"
    )
    conn.execute(
        "CREATE TABLE audit_log (id INTEGER PRIMARY KEY, job_id TEXT, field_changed TEXT, "
        "old_value TEXT, new_value TEXT, changed_at TEXT, changed_by TEXT)"
    )
    conn.commit()
    conn.close()
    companies = tmp_path / "companies"
    companies.mkdir()
    mark_complete(tmp_path)
    client = TestClient(create_app(companies_root=companies, db_path=db, base_root=tmp_path))
    r = client.get("/stats/scoring")
    assert r.status_code == 200
    assert "No jobs transitioned to" in r.text
