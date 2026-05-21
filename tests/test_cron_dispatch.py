"""Unit tests for dispatch_cron — shared cron launch path (#650)."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

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
    with patch("subprocess.Popen") as popen:
        dispatch_cron("notify-stats", db, base_root)
    argv = popen.call_args.args[0]
    # argv[0] = sys.executable, argv[1] ends with 'scripts/notify.py', argv[2] = 'daily-stats'
    assert argv[1].endswith("scripts/notify.py")
    assert argv[2] == "daily-stats"
