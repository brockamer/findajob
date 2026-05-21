"""Unit tests for dispatch_cron — shared cron launch path (#650)."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from findajob import audit
from findajob.web.cron_dispatch import dispatch_cron


@pytest.fixture()
def base_root(tmp_path: Path) -> Path:
    (tmp_path / "logs").mkdir()
    return tmp_path


@pytest.fixture()
def db(tmp_path: Path) -> sqlite3.Connection:
    """Open a writable DB at tmp_path with the schema seeded."""
    from tests.conftest import init_test_db

    db_path = tmp_path / "pipeline.db"
    init_test_db(db_path)
    return sqlite3.connect(db_path)


def test_dispatch_unknown_slug_404(db: sqlite3.Connection, base_root: Path) -> None:
    with pytest.raises(HTTPException) as exc:
        dispatch_cron("nonexistent", db, base_root)
    assert exc.value.status_code == 404


def test_dispatch_disabled_slug_409(db: sqlite3.Connection, base_root: Path) -> None:
    with pytest.raises(HTTPException) as exc:
        dispatch_cron("notify-scoreboard", db, base_root)
    assert exc.value.status_code == 409


def test_dispatch_already_running_409(db: sqlite3.Connection, base_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("findajob.web.cron_dispatch.is_currently_running", lambda slug, root: True)
    with pytest.raises(HTTPException) as exc:
        dispatch_cron("triage", db, base_root)
    assert exc.value.status_code == 409


def test_dispatch_spend_ceiling_402(db: sqlite3.Connection, base_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_refusal = MagicMock(current_sum_usd=50.0, ceiling_usd=40.0)
    monkeypatch.setattr("findajob.web.cron_dispatch.check_launch_gate", lambda conn: fake_refusal)
    monkeypatch.setattr("findajob.web.cron_dispatch.is_currently_running", lambda slug, root: False)
    with pytest.raises(HTTPException) as exc:
        dispatch_cron("triage", db, base_root)
    assert exc.value.status_code == 402


def test_dispatch_non_gated_cron_skips_spend_check(
    db: sqlite3.Connection, base_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """watchdog isn't spend-gated; check_launch_gate must not be called."""
    monkeypatch.setattr("findajob.web.cron_dispatch.is_currently_running", lambda slug, root: False)
    monkeypatch.setattr(audit, "LOG_PATH", str(base_root / "logs" / "pipeline.jsonl"))
    sentinel = MagicMock()
    monkeypatch.setattr("findajob.web.cron_dispatch.check_launch_gate", sentinel)
    with patch("subprocess.Popen") as popen:
        dispatch_cron("watchdog", db, base_root)
    sentinel.assert_not_called()
    popen.assert_called_once()


def test_dispatch_happy_path_spawns_and_redirects(
    db: sqlite3.Connection, base_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("findajob.web.cron_dispatch.is_currently_running", lambda slug, root: False)
    monkeypatch.setattr("findajob.web.cron_dispatch.check_launch_gate", lambda conn: None)
    monkeypatch.setattr(audit, "LOG_PATH", str(base_root / "logs" / "pipeline.jsonl"))
    with patch("subprocess.Popen") as popen:
        resp = dispatch_cron("notify-stats", db, base_root)
    popen.assert_called_once()
    assert resp.status_code == 303
    assert resp.headers["location"] == "/tools/?triggered=notify-stats"


def test_dispatch_honors_redirect_url_override(
    db: sqlite3.Connection, base_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The banner route at /board/trigger-triage passes a custom redirect_url."""
    monkeypatch.setattr("findajob.web.cron_dispatch.is_currently_running", lambda slug, root: False)
    monkeypatch.setattr("findajob.web.cron_dispatch.check_launch_gate", lambda conn: None)
    monkeypatch.setattr(audit, "LOG_PATH", str(base_root / "logs" / "pipeline.jsonl"))
    with patch("subprocess.Popen"):
        resp = dispatch_cron(
            "triage",
            db,
            base_root,
            source="dashboard_banner",
            redirect_url="/board/dashboard?triage_launched=1",
        )
    assert resp.headers["location"] == "/board/dashboard?triage_launched=1"


def test_dispatch_appends_tile_args_to_argv(
    db: sqlite3.Connection, base_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """notify-stats has script_path='scripts/notify.py' + args=('daily-stats',) — argv splat."""
    monkeypatch.setattr("findajob.web.cron_dispatch.is_currently_running", lambda slug, root: False)
    monkeypatch.setattr("findajob.web.cron_dispatch.check_launch_gate", lambda conn: None)
    monkeypatch.setattr(audit, "LOG_PATH", str(base_root / "logs" / "pipeline.jsonl"))
    with patch("subprocess.Popen") as popen:
        dispatch_cron("notify-stats", db, base_root)
    argv = popen.call_args.args[0]
    # argv[0] = sys.executable, argv[1] ends with 'scripts/notify.py', argv[2] = 'daily-stats'
    assert argv[1].endswith("scripts/notify.py")
    assert argv[2] == "daily-stats"


def test_dispatch_pre_emits_cron_started_to_close_race(
    db: sqlite3.Connection, base_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The dispatcher emits cron_started itself BEFORE Popen so is_currently_running
    sees the run during the ~100ms interpreter-startup window.
    Race-close fix from #650 T5 reviewer follow-up."""
    monkeypatch.setattr("findajob.web.cron_dispatch.is_currently_running", lambda slug, root: False)
    monkeypatch.setattr("findajob.web.cron_dispatch.check_launch_gate", lambda conn: None)

    log_path = base_root / "logs" / "pipeline.jsonl"
    monkeypatch.setattr(audit, "LOG_PATH", str(log_path))

    with patch("subprocess.Popen"):
        dispatch_cron("notify-stats", db, base_root)

    assert log_path.exists()
    lines = log_path.read_text().splitlines()
    events = [line for line in lines if '"event": "cron_started"' in line]
    assert len(events) == 1
    assert '"cron": "notify-stats"' in events[0]
    assert '"source": "tools_panel"' in events[0]


def test_dispatch_subprocess_failure_500_and_emits_failed_event(
    db: sqlite3.Connection, base_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Popen raising → 500 + web_cron_dispatch_failed event."""
    monkeypatch.setattr("findajob.web.cron_dispatch.is_currently_running", lambda slug, root: False)
    monkeypatch.setattr("findajob.web.cron_dispatch.check_launch_gate", lambda conn: None)

    log_path = base_root / "logs" / "pipeline.jsonl"
    monkeypatch.setattr(audit, "LOG_PATH", str(log_path))

    def _raise(*_args: object, **_kwargs: object) -> None:
        raise OSError("simulated spawn failure")

    monkeypatch.setattr("subprocess.Popen", _raise)
    with pytest.raises(HTTPException) as exc:
        dispatch_cron("notify-stats", db, base_root)
    assert exc.value.status_code == 500
    assert "simulated spawn failure" in exc.value.detail

    failed_lines = [line for line in log_path.read_text().splitlines() if '"event": "web_cron_dispatch_failed"' in line]
    assert len(failed_lines) == 1
    assert '"cron": "notify-stats"' in failed_lines[0]


def test_dispatch_subprocess_failure_releases_slug_via_cron_finished(
    db: sqlite3.Connection, base_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Popen failure must emit cron_finished status=failed so the slug
    is not bricked until max_runtime_minutes elapses.

    Cross-task seam bug caught in the #650 whole-feature review: the
    race-close pre-emit writes cron_started BEFORE Popen, so a Popen
    failure without a paired cron_finished leaves is_currently_running
    returning True for up to 120 min (triage's ceiling).
    """
    monkeypatch.setattr("findajob.web.cron_dispatch.is_currently_running", lambda slug, root: False)
    monkeypatch.setattr("findajob.web.cron_dispatch.check_launch_gate", lambda conn: None)

    from findajob.web.cron_registry import is_currently_running as real_is_running

    log_path = base_root / "logs" / "pipeline.jsonl"
    monkeypatch.setattr(audit, "LOG_PATH", str(log_path))

    def _raise(*_args: object, **_kwargs: object) -> None:
        raise OSError("simulated spawn failure")

    monkeypatch.setattr("subprocess.Popen", _raise)
    with pytest.raises(HTTPException) as exc:
        dispatch_cron("notify-stats", db, base_root)
    assert exc.value.status_code == 500

    # The real (un-mocked) is_currently_running must see the slug as released —
    # the dangling cron_started from the pre-emit was paired with cron_finished
    # status=failed in the except branch.
    assert real_is_running("notify-stats", base_root) is False

    # And the log shows both events for traceability.
    log_text = log_path.read_text()
    finished_lines = [line for line in log_text.splitlines() if '"event": "cron_finished"' in line]
    assert len(finished_lines) == 1
    assert '"cron": "notify-stats"' in finished_lines[0]
    assert '"status": "failed"' in finished_lines[0]
    failed_lines = [line for line in log_text.splitlines() if '"event": "web_cron_dispatch_failed"' in line]
    assert len(failed_lines) == 1
