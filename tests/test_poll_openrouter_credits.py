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


def _seed_calibration_baseline(
    conn: sqlite3.Connection,
    *,
    days_ago: int,
    credits_used_usd: float,
    onboarding_total_usd: float = 0.0,
    multiplier: float = 1.0,
) -> None:
    conn.execute(
        """INSERT INTO cost_calibration
           (polled_at, credits_total_usd, credits_used_usd, credits_remaining_usd,
            onboarding_total_usd, pipeline_actual_usd, heuristic_sum_usd,
            multiplier, multiplier_clamped, poll_status)
           VALUES (datetime('now', '-' || ? || ' days'),
                   100.0, ?, 100.0 - ?,
                   ?, ?, 0.0,
                   ?, 0, 'ok')""",
        (
            days_ago,
            credits_used_usd,
            credits_used_usd,
            onboarding_total_usd,
            credits_used_usd - onboarding_total_usd,
            multiplier,
        ),
    )
    conn.commit()


def test_poll_warming_up_when_no_baseline(
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
    assert row["poll_status"] == "warming_up"
    assert row["multiplier"] == pytest.approx(1.0, rel=1e-3)
    assert row["multiplier_clamped"] == 0
    assert row["credits_remaining_usd"] == pytest.approx(67.86, rel=1e-3)
    # pipeline_actual = 32.14 - 4.50 = 27.64 (lifetime, stored for next baseline)
    assert row["pipeline_actual_usd"] == pytest.approx(27.64, rel=1e-3)


def test_poll_clamps_extreme_multiplier(db: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch) -> None:
    from scripts import poll_openrouter_credits

    # baseline 8 days ago: credits_used = $0
    _seed_calibration_baseline(db, days_ago=8, credits_used_usd=0.0)
    # current credits_used = $50 → delta = $50
    # cost_log $1 in last 7 days → heuristic_window = $1 → raw = 50, clamped to 3.0
    _seed_heuristic(db, 1.00)
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")

    with patch.object(
        poll_openrouter_credits,
        "_fetch_credits",
        return_value={"total_credits": 100.0, "total_usage": 50.0},
    ):
        poll_openrouter_credits.poll_once(db)

    row = db.execute("SELECT * FROM cost_calibration WHERE poll_status='ok' ORDER BY id DESC LIMIT 1").fetchone()
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


def test_poll_uses_windowed_delta_when_baseline_exists(db: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch) -> None:
    """With a baseline row 8 days old, multiplier reflects 7-day deltas, not lifetime."""
    from scripts import poll_openrouter_credits

    # 8-day-old baseline: credits_used was $20 then.
    _seed_calibration_baseline(db, days_ago=8, credits_used_usd=20.0, multiplier=1.0)

    # Current state: credits_used = $30 → 7-day delta ≈ $10.
    # Heuristic in last 7 days: one $5 cost_log row.
    db.execute(
        """INSERT INTO cost_log (job_id, operation, model, cost_usd, success, logged_at)
           VALUES (NULL, 'briefing', 'm', 5.0, 1, datetime('now', '-1 day'))"""
    )
    db.commit()

    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    with patch.object(
        poll_openrouter_credits,
        "_fetch_credits",
        return_value={"total_credits": 100.0, "total_usage": 30.0},
    ):
        poll_openrouter_credits.poll_once(db)

    row = db.execute("SELECT * FROM cost_calibration WHERE poll_status='ok' ORDER BY id DESC LIMIT 1").fetchone()
    # delta_credits = 30 - 20 = 10; heuristic_window = 5 → multiplier = 2.0
    assert row["multiplier"] == pytest.approx(2.0, abs=0.05)
    assert row["multiplier_clamped"] == 0


def test_poll_inherits_last_good_multiplier_when_window_sparse(
    db: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When heuristic_window is 0 (no recent prep), multiplier inherits last good 'ok' value."""
    from scripts import poll_openrouter_credits

    # 8-day baseline + a recent 'ok' row with multiplier=1.5
    _seed_calibration_baseline(db, days_ago=8, credits_used_usd=10.0)
    _seed_calibration_baseline(db, days_ago=2, credits_used_usd=20.0, multiplier=1.5)
    # No cost_log rows → heuristic_window = 0

    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    with patch.object(
        poll_openrouter_credits,
        "_fetch_credits",
        return_value={"total_credits": 100.0, "total_usage": 25.0},
    ):
        poll_openrouter_credits.poll_once(db)

    row = db.execute(
        """SELECT * FROM cost_calibration
           WHERE poll_status = 'ok' ORDER BY id DESC LIMIT 1"""
    ).fetchone()
    assert row["multiplier"] == pytest.approx(1.5, abs=1e-6)
    assert row["multiplier_clamped"] == 0


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
