"""Tests for the spend-ceiling launch gate wired into the 5 LLM-spawning routes (#671).

One representative test per gated route. Each test inserts a cost_log row that
puts the current-month sum at or above the configured ceiling, then POSTs to the
route and asserts 402 with the current/ceiling figures in the detail string.

The speculative routes use their own DB connection (_conn()), so we patch
findajob.web.routes.speculative.DB_PATH to point at the test DB.
The board_action routes use the FastAPI dependency (get_db), wired by create_app.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from findajob import audit, config_loader
from findajob.onboarding import mark_complete
from findajob.web.app import create_app

# ── DB helpers ────────────────────────────────────────────────────────────────


def _build_db(db_path: Path) -> None:
    from findajob.db.migrate import apply_pending

    conn = sqlite3.connect(str(db_path))
    try:
        apply_pending(conn)
    finally:
        conn.close()


def _insert_job(
    conn: sqlite3.Connection,
    *,
    fingerprint: str,
    stage: str,
    job_id: str | None = None,
) -> None:
    job_id = job_id or fingerprint.replace("fp_", "id_")
    conn.execute(
        "INSERT INTO jobs (id, fingerprint, url, title, company, source, stage, relevance_score) "
        "VALUES (?, ?, ?, ?, ?, 'test', ?, 8)",
        (job_id, fingerprint, "https://example.com/job", "Senior Ops", "Acme Corp", stage),
    )


def _insert_speculative(conn: sqlite3.Connection, *, request_id: int, status: str = "ready_for_review") -> None:
    conn.execute(
        "INSERT INTO speculative_requests (id, company, status) VALUES (?, ?, ?)",
        (request_id, "Acme Corp", status),
    )


def _insert_cost_at_ceiling(conn: sqlite3.Connection, *, ceiling: float) -> None:
    """Insert a cost_log row with cost_usd == ceiling, logged this calendar month (UTC)."""
    now_str = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")
    conn.execute(
        "INSERT INTO cost_log (operation, model, cost_usd, logged_at) VALUES (?, ?, ?, ?)",
        ("test_op", "test-model", ceiling, now_str),
    )


# ── fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture()
def _ceiling_file(tmp_path: Path, monkeypatch) -> Path:
    """Write spend_ceiling.txt = 50.00 and point config_loader at it."""
    p = tmp_path / "spend_ceiling.txt"
    p.write_text("50.00")
    monkeypatch.setattr(config_loader, "_SPEND_CEILING_PATH", p)
    return p


@pytest.fixture()
def board_client(tmp_path: Path, monkeypatch, _ceiling_file) -> TestClient:
    """TestClient backed by a real DB with sum >= ceiling and subprocess mocked out."""
    from findajob.web.routes import board_actions

    # Suppress subprocess.Popen (won't be reached — gate fires first)
    class _FakePopen:
        pid = 99999

        def __init__(self, *a, **kw):
            pass

    monkeypatch.setattr(board_actions.subprocess, "Popen", _FakePopen)
    monkeypatch.setattr(audit, "LOG_PATH", str(tmp_path / "events.jsonl"))
    monkeypatch.setattr(board_actions, "BASE", str(tmp_path))

    db_path = tmp_path / "pipeline.db"
    _build_db(db_path)

    conn = sqlite3.connect(str(db_path))
    _insert_job(conn, fingerprint="fp_scored", stage="scored")
    _insert_job(conn, fingerprint="fp_applied", stage="applied")
    _insert_cost_at_ceiling(conn, ceiling=50.0)
    conn.commit()
    conn.close()

    companies = tmp_path / "companies"
    companies.mkdir()
    mark_complete(tmp_path)
    app = create_app(companies_root=companies, db_path=db_path, base_root=tmp_path)
    return TestClient(app)


@pytest.fixture()
def speculative_client(tmp_path: Path, monkeypatch, _ceiling_file) -> TestClient:
    """TestClient for speculative routes with DB_PATH patched + sum >= ceiling."""
    import findajob.web.routes.speculative as spec_mod
    from findajob.web.routes import board_actions

    class _FakePopen:
        pid = 99999

        def __init__(self, *a, **kw):
            pass

    monkeypatch.setattr(spec_mod.subprocess, "Popen", _FakePopen)
    monkeypatch.setattr(board_actions.subprocess, "Popen", _FakePopen)
    monkeypatch.setattr(audit, "LOG_PATH", str(tmp_path / "events.jsonl"))
    monkeypatch.setattr(board_actions, "BASE", str(tmp_path))

    db_path = tmp_path / "pipeline.db"
    _build_db(db_path)

    conn = sqlite3.connect(str(db_path))
    _insert_speculative(conn, request_id=1, status="ready_for_review")
    _insert_cost_at_ceiling(conn, ceiling=50.0)
    conn.commit()
    conn.close()

    # Point the speculative module's module-level DB_PATH at our test DB
    monkeypatch.setattr(spec_mod, "DB_PATH", db_path)

    companies = tmp_path / "companies"
    companies.mkdir()
    mark_complete(tmp_path)
    app = create_app(companies_root=companies, db_path=db_path, base_root=tmp_path)
    return TestClient(app)


# ── route tests ───────────────────────────────────────────────────────────────


class TestPrepLaunchGate:
    def test_returns_402_when_ceiling_exceeded(self, board_client: TestClient):
        resp = board_client.post("/board/jobs/fp_scored/prep")
        assert resp.status_code == 402
        detail = resp.json()["detail"]
        assert "50.00" in detail
        assert "ceiling" in detail.lower()


class TestRegenerateLaunchGate:
    def test_returns_402_when_ceiling_exceeded(self, board_client: TestClient):
        # fp_scored has stage=scored; regenerate is gated before stage check
        resp = board_client.post("/board/jobs/fp_scored/regenerate")
        assert resp.status_code == 402
        detail = resp.json()["detail"]
        assert "50.00" in detail
        assert "ceiling" in detail.lower()


class TestInterviewLaunchGate:
    def test_returns_402_when_ceiling_exceeded(self, board_client: TestClient):
        resp = board_client.post("/board/jobs/fp_applied/interview")
        assert resp.status_code == 402
        detail = resp.json()["detail"]
        assert "50.00" in detail
        assert "ceiling" in detail.lower()


class TestIngestSpeculativeLaunchGate:
    def test_returns_402_when_ceiling_exceeded(self, speculative_client: TestClient):
        resp = speculative_client.post("/ingest/speculative", data={"company": "Acme Corp"})
        assert resp.status_code == 402
        detail = resp.json()["detail"]
        assert "50.00" in detail
        assert "ceiling" in detail.lower()


class TestSpeculativeRegenerateLaunchGate:
    def test_returns_402_when_ceiling_exceeded(self, speculative_client: TestClient):
        resp = speculative_client.post("/speculative/regenerate/1")
        assert resp.status_code == 402
        detail = resp.json()["detail"]
        assert "50.00" in detail
        assert "ceiling" in detail.lower()
