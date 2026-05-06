"""Tests for the OpenRouter credits poll job."""

from __future__ import annotations

import sqlite3
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture
def db(tmp_path: Path) -> sqlite3.Connection:
    db_path = tmp_path / "pipeline.db"
    subprocess.run(
        [sys.executable, "scripts/init_db.py", str(db_path)],
        check=True,
        cwd=Path(__file__).resolve().parent.parent,
    )
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    yield conn
    conn.close()


def _seed_onboarding_total(conn: sqlite3.Connection, total: float) -> None:
    conn.execute(
        """INSERT INTO onboarding_sessions
           (id, history_json, captured_blocks_json, started_at, last_turn_at, cumulative_cost_usd)
           VALUES ('s1', '[]', '{}', datetime('now'), datetime('now'), ?)""",
        (total,),
    )
    conn.commit()


def _seed_heuristic(conn: sqlite3.Connection, total: float) -> None:
    conn.execute(
        """INSERT INTO cost_log
           (job_id, operation, model, cost_usd, success, logged_at)
           VALUES (NULL, 'briefing', 'm', ?, 1, datetime('now'))""",
        (total,),
    )
    conn.commit()


def test_poll_inserts_calibration_row_with_subtraction(
    db: sqlite3.Connection, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scripts import poll_openrouter_credits

    _seed_onboarding_total(db, 4.50)
    _seed_heuristic(db, 21.26)
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")

    with patch.object(
        poll_openrouter_credits,
        "_fetch_credits",
        return_value={"total_credits": 100.0, "total_usage": 32.14},
    ):
        poll_openrouter_credits.poll_once(db)

    row = db.execute("SELECT * FROM cost_calibration ORDER BY id DESC LIMIT 1").fetchone()
    assert row["poll_status"] == "ok"
    assert row["credits_remaining_usd"] == pytest.approx(67.86, rel=1e-3)
    assert row["onboarding_total_usd"] == pytest.approx(4.50, rel=1e-3)
    # pipeline_actual = 32.14 - 4.50 = 27.64
    assert row["pipeline_actual_usd"] == pytest.approx(27.64, rel=1e-3)
    # multiplier = 27.64 / 21.26 ≈ 1.3
    assert row["multiplier"] == pytest.approx(1.3, abs=0.05)
    assert row["multiplier_clamped"] == 0


def test_poll_clamps_extreme_multiplier(db: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch) -> None:
    from scripts import poll_openrouter_credits

    _seed_heuristic(db, 1.00)  # tiny heuristic → big multiplier
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")

    with patch.object(
        poll_openrouter_credits,
        "_fetch_credits",
        return_value={"total_credits": 100.0, "total_usage": 50.0},
    ):
        poll_openrouter_credits.poll_once(db)

    row = db.execute("SELECT * FROM cost_calibration ORDER BY id DESC LIMIT 1").fetchone()
    # Raw multiplier = 50, clamped to 3.0
    assert row["multiplier"] == pytest.approx(3.0, rel=1e-3)
    assert row["multiplier_clamped"] == 1


def test_poll_records_http_error(db: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch) -> None:
    from scripts import poll_openrouter_credits

    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")

    with patch.object(
        poll_openrouter_credits,
        "_fetch_credits",
        side_effect=poll_openrouter_credits.OpenRouterHTTPError("500 Internal Server Error"),
    ):
        poll_openrouter_credits.poll_once(db)

    row = db.execute("SELECT * FROM cost_calibration ORDER BY id DESC LIMIT 1").fetchone()
    assert row["poll_status"] == "http_error"
    assert "500" in row["error_message"]


def test_poll_no_ops_on_missing_key(db: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch) -> None:
    from scripts import poll_openrouter_credits

    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    poll_openrouter_credits.poll_once(db)

    rows = db.execute("SELECT poll_status FROM cost_calibration").fetchall()
    # We DO record the missing-key state so /admin/ surfaces can show "no key configured".
    assert len(rows) == 1
    assert rows[0]["poll_status"] == "missing_key"


def test_main_resolves_db_path_against_BASE_str(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression for #87: findajob.paths.BASE is a str (not a Path), so
    main() must wrap in Path() before path-joining. The original main()
    used `BASE / "data" / ...` which raised TypeError every cron tick in
    production, leaving cost_calibration empty post-merge."""
    db_path = tmp_path / "data" / "pipeline.db"
    db_path.parent.mkdir()
    subprocess.run(
        [sys.executable, "scripts/init_db.py", str(db_path)],
        check=True,
        cwd=Path(__file__).resolve().parent.parent,
    )

    from scripts import poll_openrouter_credits

    monkeypatch.setattr(poll_openrouter_credits, "BASE", str(tmp_path))
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    rc = poll_openrouter_credits.main()
    assert rc == 0

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT poll_status FROM cost_calibration").fetchall()
    conn.close()
    assert len(rows) == 1
    assert rows[0]["poll_status"] == "missing_key"
