"""Stats rejections route — /stats/rejections SQL correctness + render (#195).

All-time view sourcing from `jobs.stage IN ('rejected','not_selected')`. Two
cuts: global per-reason bar (mirrors /stats/feedback shape) and per-company
top-5 stacked bar (the novel axis vs /stats/feedback's 28-day reason trend).
Captures company-side NOT_SELECTED events that never reach feedback_log —
that inclusion is the central reason this page exists alongside /stats/feedback.
"""

import json
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from findajob.onboarding import mark_complete
from findajob.web.app import create_app


def _seed_job(
    conn: sqlite3.Connection,
    job_id: str,
    *,
    stage: str,
    company: str,
    reject_reason: str,
    title: str = "Some role",
) -> None:
    conn.execute(
        "INSERT INTO jobs (id, fingerprint, title, company, stage, reject_reason) VALUES (?, ?, ?, ?, ?, ?)",
        (job_id, f"fp_{job_id}", title, company, stage, reject_reason),
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

    # Acme: 4 rejections — 2 wrong_level, 1 location, 1 company_not_selected
    _seed_job(conn, "j1", stage="rejected", company="Acme", reject_reason="wrong_level")
    _seed_job(conn, "j2", stage="rejected", company="Acme", reject_reason="wrong_level")
    _seed_job(conn, "j3", stage="rejected", company="Acme", reject_reason="location")
    _seed_job(conn, "j4", stage="not_selected", company="Acme", reject_reason="company_not_selected")
    # BetaCo: 3 rejections — 2 wrong_level, 1 not_selected
    _seed_job(conn, "j5", stage="rejected", company="BetaCo", reject_reason="wrong_level")
    _seed_job(conn, "j6", stage="rejected", company="BetaCo", reject_reason="wrong_level")
    _seed_job(conn, "j7", stage="not_selected", company="BetaCo", reject_reason="company_not_selected")
    # CDelta: 2 rejections — 1 location, 1 blank
    _seed_job(conn, "j8", stage="rejected", company="CDelta", reject_reason="location")
    _seed_job(conn, "j9", stage="rejected", company="CDelta", reject_reason="")
    # DZulu: 2 rejections — both location
    _seed_job(conn, "j10", stage="rejected", company="DZulu", reject_reason="location")
    _seed_job(conn, "j11", stage="rejected", company="DZulu", reject_reason="location")
    # Epsilon: 1 rejection (tiebreaker pool with Foxtrot/Goose for top-5 cutoff)
    _seed_job(conn, "j12", stage="rejected", company="Epsilon", reject_reason="location")
    # Foxtrot: 1 rejection
    _seed_job(conn, "j13", stage="rejected", company="Foxtrot", reject_reason="wrong_level")
    # Goose: 1 rejection — should NOT make top-5 (alphabetical tiebreak with Epsilon/Foxtrot)
    _seed_job(conn, "j14", stage="rejected", company="Goose", reject_reason="wrong_level")
    # Blank company — must be excluded from top-5
    _seed_job(conn, "j_blank", stage="rejected", company="", reject_reason="wrong_level")
    # Applied (not a rejection) — must be excluded entirely
    _seed_job(conn, "j_applied", stage="applied", company="HotelCo", reject_reason="")
    # Scored (not a rejection)
    _seed_job(conn, "j_scored", stage="scored", company="IndigoCo", reject_reason="")

    conn.commit()
    conn.close()
    companies = tmp_path / "companies"
    companies.mkdir()
    mark_complete(tmp_path)
    return TestClient(create_app(companies_root=companies, db_path=db, base_root=tmp_path))


def _global_data(text: str) -> dict[str, int]:
    """Pull the global chart's labels/data and zip into a reason→count map."""
    start = text.index('id="rejections-global-data"')
    block_start = text.index(">", start) + 1
    block_end = text.index("</script>", block_start)
    payload = json.loads(text[block_start:block_end])
    return dict(zip(payload["labels"], payload["data"], strict=True))


def _company_data(text: str) -> dict:
    start = text.index('id="rejections-company-data"')
    block_start = text.index(">", start) + 1
    block_end = text.index("</script>", block_start)
    return json.loads(text[block_start:block_end])


def test_rejections_renders_both_charts(client: TestClient) -> None:
    """GET /stats/rejections returns 200 with both chart canvases."""
    r = client.get("/stats/rejections")
    assert r.status_code == 200
    assert 'id="rejections-global-chart"' in r.text
    assert 'id="rejections-company-chart"' in r.text
    assert 'id="rejections-global-data"' in r.text
    assert 'id="rejections-company-data"' in r.text


def test_global_counts_aggregate_both_stages(client: TestClient) -> None:
    """Global per-reason counts include BOTH `rejected` AND `not_selected` rows.

    This is the central distinction from /stats/feedback — company NOT_SELECTED
    never reaches feedback_log, but it MUST appear here.
    """
    r = client.get("/stats/rejections")
    counts = _global_data(r.text)
    # 7× wrong_level — j_blank excluded only from top-5 companies, not from global totals
    # (j1 Acme, j2 Acme, j5 BetaCo, j6 BetaCo, j13 Foxtrot, j14 Goose, j_blank blank-co)
    assert counts["wrong_level"] == 7
    # 5× location (j3 Acme, j8 CDelta, j10 DZulu, j11 DZulu, j12 Epsilon)
    assert counts["location"] == 5
    # 2× company_not_selected (j4, j7) — proves not_selected stage is counted
    assert counts["company_not_selected"] == 2


def test_not_selected_stage_included(client: TestClient) -> None:
    """Rows with stage='not_selected' contribute to the page totals."""
    r = client.get("/stats/rejections")
    # Total = 7 wrong_level + 5 location + 2 company_not_selected + 1 blank-reason = 15
    assert 'Total: <span class="font-semibold">15</span>' in r.text


def test_applied_and_scored_stages_excluded(client: TestClient) -> None:
    """Jobs in non-rejection stages do not contribute."""
    r = client.get("/stats/rejections")
    # HotelCo is at stage='applied'; must not show up as a top-5 company
    assert "HotelCo" not in r.text
    # IndigoCo is at stage='scored'; must not show up
    assert "IndigoCo" not in r.text


def test_blank_reason_renders_as_label(client: TestClient) -> None:
    """An empty reject_reason renders under the '(blank)' label."""
    r = client.get("/stats/rejections")
    counts = _global_data(r.text)
    # j9 has reject_reason='' — coalesces to '(blank)'
    assert counts.get("(blank)") == 1


def test_top_companies_ordering_and_limit(client: TestClient) -> None:
    """Top-5 companies ordered by total DESC, then company ASC for tiebreak.

    Fixture totals: Acme=4, BetaCo=3, CDelta=2, DZulu=2, Epsilon=1, Foxtrot=1, Goose=1.
    Top-5 must be Acme, BetaCo, CDelta, DZulu, Epsilon (alphabetical tiebreak
    among the three 1-count companies).
    """
    r = client.get("/stats/rejections")
    payload = _company_data(r.text)
    assert payload["labels"] == ["Acme", "BetaCo", "CDelta", "DZulu", "Epsilon"]
    # Goose has 1 rejection but loses the alphabetical tiebreak to Epsilon/Foxtrot
    assert "Goose" not in payload["labels"]
    # Foxtrot also lost (E < F) — confirm exact 5
    assert "Foxtrot" not in payload["labels"]


def test_blank_company_excluded_from_top_companies(client: TestClient) -> None:
    """Rows with blank company are excluded from the per-company breakdown."""
    r = client.get("/stats/rejections")
    payload = _company_data(r.text)
    assert "" not in payload["labels"]
    # But j_blank is still counted in the global wrong_level total — already
    # verified in test_global_counts_aggregate_both_stages (count was 6, not 5).


def test_per_company_reason_breakdown_correct(client: TestClient) -> None:
    """Each top-5 company's reason counts in chart datasets match seeded rows."""
    r = client.get("/stats/rejections")
    payload = _company_data(r.text)
    # Build a (reason, company) -> count lookup from the dataset list.
    company_idx = {co: i for i, co in enumerate(payload["labels"])}
    by_reason = {ds["label"]: ds["data"] for ds in payload["datasets"]}
    # Acme: 2 wrong_level, 1 location, 1 company_not_selected
    assert by_reason["wrong_level"][company_idx["Acme"]] == 2
    assert by_reason["location"][company_idx["Acme"]] == 1
    assert by_reason["company_not_selected"][company_idx["Acme"]] == 1
    # BetaCo: 2 wrong_level, 1 company_not_selected, 0 location
    assert by_reason["wrong_level"][company_idx["BetaCo"]] == 2
    assert by_reason["company_not_selected"][company_idx["BetaCo"]] == 1
    assert by_reason["location"][company_idx["BetaCo"]] == 0
    # CDelta: 1 location, 1 blank
    assert by_reason["location"][company_idx["CDelta"]] == 1
    assert by_reason["(blank)"][company_idx["CDelta"]] == 1


def test_empty_state_renders_without_crash(tmp_path: Path) -> None:
    """Zero rejections in the DB → 200 with the zero-state message."""
    db = tmp_path / "pipeline.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE jobs ("
        "  id TEXT PRIMARY KEY, fingerprint TEXT, title TEXT, company TEXT, stage TEXT, "
        "  reject_reason TEXT)"
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
    r = client.get("/stats/rejections")
    assert r.status_code == 200
    assert "No rejections recorded yet." in r.text
    assert "No company-attributed rejections yet." in r.text


def test_canonical_reasons_appear_even_with_zero_counts(client: TestClient) -> None:
    """All 11 canonical reject reasons from config render in the global table.

    Reasons not seen in the seeded data should still appear with count=0, so
    the data table is the canonical taxonomy reference.
    """
    r = client.get("/stats/rejections")
    counts = _global_data(r.text)
    # The 4 reasons our fixture seeds — present with the expected counts.
    seeded_reasons = {"wrong_level", "location", "company_not_selected", "(blank)"}
    # The remaining canonical reasons must also render (with count=0).
    # We don't import load_reject_reasons here to avoid coupling to its exact
    # output; instead we assert at least 7 ADDITIONAL keys exist beyond the seeded ones.
    extra = set(counts) - seeded_reasons
    assert len(extra) >= 7, f"Expected ≥7 zero-count canonical reasons; got {extra}"
    for reason in extra:
        assert counts[reason] == 0
