"""Tests for spend-ceiling threshold alerts (#876).

Exercises :func:`_maybe_fire_threshold_alerts` via both gates
(``check_call_gate`` and ``check_launch_gate``), verifying:

- 80% warning fires, 100% reached fires
- Same-month dedup (in-process set + DB row)
- Ceiling disabled → no notifications
- Notification send failure doesn't break the gate
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from findajob import config_loader, spend_ceiling
from findajob.llm.openrouter import LLMSpendCeilingExceeded

# ── helpers ──────────────────────────────────────────────────────────────────


def _build_db(db_path: Path) -> sqlite3.Connection:
    from findajob.db.migrate import apply_pending

    conn = sqlite3.connect(str(db_path))
    apply_pending(conn)
    return conn


def _insert_cost_row(conn: sqlite3.Connection, *, cost_usd: float, logged_at: str) -> None:
    conn.execute(
        "INSERT INTO cost_log (operation, model, cost_usd, logged_at) VALUES (?, ?, ?, ?)",
        ("test_op", "test-model", cost_usd, logged_at),
    )
    conn.commit()


def _this_month_ts() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")


@pytest.fixture(autouse=True)
def _clear_alerts_cache():
    """Reset the module-level dedup set between tests."""
    spend_ceiling._alerts_fired.clear()
    yield
    spend_ceiling._alerts_fired.clear()


@pytest.fixture()
def ceiling_db(tmp_path, monkeypatch):
    """Provide a real DB + ceiling config wired into spend_ceiling."""
    ceiling_path = tmp_path / "spend_ceiling.txt"
    ceiling_path.write_text("100.00")
    monkeypatch.setattr(config_loader, "_SPEND_CEILING_PATH", ceiling_path)
    config_loader._reset_cache()

    db_path = tmp_path / "pipeline.db"
    conn = _build_db(db_path)
    monkeypatch.setattr("findajob.spend_ceiling._DB_PATH", db_path)

    yield conn, ceiling_path, db_path
    conn.close()
    config_loader._reset_cache()


# ── threshold alert tests (unit) ────────────────────────────────────────────


class TestWarningAt80Percent:
    def test_fires_at_80_percent(self, ceiling_db):
        conn, _, db_path = ceiling_db
        _insert_cost_row(conn, cost_usd=80.00, logged_at=_this_month_ts())

        alert_conn = sqlite3.connect(str(db_path))
        _build_db(db_path)  # ensure schema exists on this connection
        with patch("findajob.notifications.ntfy.send") as mock_send:
            spend_ceiling._maybe_fire_threshold_alerts(80.0, 100.0, alert_conn)

        mock_send.assert_called_once()
        call_kwargs = mock_send.call_args
        assert call_kwargs.kwargs["kind"] == "spend_ceiling_warning"
        assert "$80.00" in call_kwargs.kwargs["title"]
        assert "$100.00" in call_kwargs.kwargs["title"]
        alert_conn.close()

    def test_no_warning_below_80_percent(self, ceiling_db):
        conn, _, db_path = ceiling_db
        _insert_cost_row(conn, cost_usd=79.00, logged_at=_this_month_ts())

        alert_conn = sqlite3.connect(str(db_path))
        with patch("findajob.notifications.ntfy.send") as mock_send:
            spend_ceiling._maybe_fire_threshold_alerts(79.0, 100.0, alert_conn)

        mock_send.assert_not_called()
        alert_conn.close()


class TestReachedAt100Percent:
    def test_fires_at_100_percent(self, ceiling_db):
        conn, _, db_path = ceiling_db
        _insert_cost_row(conn, cost_usd=100.00, logged_at=_this_month_ts())

        alert_conn = sqlite3.connect(str(db_path))
        with patch("findajob.notifications.ntfy.send") as mock_send:
            spend_ceiling._maybe_fire_threshold_alerts(100.0, 100.0, alert_conn)

        kinds_sent = [c.kwargs["kind"] for c in mock_send.call_args_list]
        assert "spend_ceiling_reached" in kinds_sent
        assert "spend_ceiling_warning" in kinds_sent
        alert_conn.close()

    def test_reached_body_mentions_blocked(self, ceiling_db):
        conn, _, db_path = ceiling_db
        _insert_cost_row(conn, cost_usd=105.00, logged_at=_this_month_ts())

        alert_conn = sqlite3.connect(str(db_path))
        with patch("findajob.notifications.ntfy.send") as mock_send:
            spend_ceiling._maybe_fire_threshold_alerts(105.0, 100.0, alert_conn)

        reached_calls = [c for c in mock_send.call_args_list if c.kwargs["kind"] == "spend_ceiling_reached"]
        assert len(reached_calls) == 1
        assert "blocked" in reached_calls[0].kwargs["body"]
        alert_conn.close()


class TestSameMonthDedup:
    def test_in_process_dedup(self, ceiling_db):
        """Second call in same process doesn't re-send."""
        conn, _, db_path = ceiling_db
        _insert_cost_row(conn, cost_usd=85.00, logged_at=_this_month_ts())

        alert_conn = sqlite3.connect(str(db_path))
        with patch("findajob.notifications.ntfy.send") as mock_send:
            spend_ceiling._maybe_fire_threshold_alerts(85.0, 100.0, alert_conn)
            spend_ceiling._maybe_fire_threshold_alerts(85.0, 100.0, alert_conn)

        assert mock_send.call_count == 1
        alert_conn.close()

    def test_cross_process_dedup_via_db(self, ceiling_db):
        """If a notification row already exists in the DB, skip even with cleared cache."""
        conn, _, db_path = ceiling_db
        _insert_cost_row(conn, cost_usd=85.00, logged_at=_this_month_ts())

        conn.execute(
            "INSERT INTO notifications (kind, title, body, priority, delivery_status) VALUES (?, ?, ?, ?, ?)",
            ("spend_ceiling_warning", "test", "test", "high", "sent"),
        )
        conn.commit()

        alert_conn = sqlite3.connect(str(db_path))
        with patch("findajob.notifications.ntfy.send") as mock_send:
            spend_ceiling._maybe_fire_threshold_alerts(85.0, 100.0, alert_conn)

        mock_send.assert_not_called()
        alert_conn.close()


class TestCeilingDisabled:
    def test_no_alerts_when_ceiling_none(self, tmp_path, monkeypatch):
        """When ceiling is disabled, gates are no-op and no alerts fire."""
        monkeypatch.setattr(config_loader, "_SPEND_CEILING_PATH", tmp_path / "nonexistent.txt")
        config_loader._reset_cache()

        db_path = tmp_path / "pipeline.db"
        conn = _build_db(db_path)
        _insert_cost_row(conn, cost_usd=999.00, logged_at=_this_month_ts())
        monkeypatch.setattr("findajob.spend_ceiling._DB_PATH", db_path)

        with patch("findajob.notifications.ntfy.send") as mock_send:
            spend_ceiling.check_call_gate()

        mock_send.assert_not_called()
        conn.close()
        config_loader._reset_cache()


class TestNotificationFailureDoesNotBreakGate:
    def test_send_raises_gate_still_enforces(self, ceiling_db, monkeypatch):
        """If notification send() raises, the gate must still block."""
        conn, _, db_path = ceiling_db
        _insert_cost_row(conn, cost_usd=100.00, logged_at=_this_month_ts())
        monkeypatch.setattr("findajob.spend_ceiling._DB_PATH", db_path)

        with patch("findajob.notifications.ntfy.send", side_effect=RuntimeError("ntfy down")):
            with pytest.raises(LLMSpendCeilingExceeded):
                spend_ceiling.check_call_gate()

    def test_send_raises_launch_gate_still_refuses(self, ceiling_db):
        """If notification send() raises, launch gate still returns refusal."""
        conn, _, db_path = ceiling_db
        _insert_cost_row(conn, cost_usd=100.00, logged_at=_this_month_ts())

        with patch("findajob.notifications.ntfy.send", side_effect=RuntimeError("ntfy down")):
            result = spend_ceiling.check_launch_gate(conn)

        assert result is not None
        assert result.ceiling_usd == 100.0


# ── integration via check_call_gate / check_launch_gate ──────────────────────


class TestCallGateFiresAlerts:
    def test_warning_fires_through_call_gate(self, ceiling_db, monkeypatch):
        conn, _, db_path = ceiling_db
        _insert_cost_row(conn, cost_usd=85.00, logged_at=_this_month_ts())
        monkeypatch.setattr("findajob.spend_ceiling._DB_PATH", db_path)

        with patch("findajob.notifications.ntfy.send") as mock_send:
            spend_ceiling.check_call_gate()

        assert mock_send.call_count == 1
        assert mock_send.call_args.kwargs["kind"] == "spend_ceiling_warning"

    def test_reached_fires_then_raises(self, ceiling_db, monkeypatch):
        conn, _, db_path = ceiling_db
        _insert_cost_row(conn, cost_usd=100.00, logged_at=_this_month_ts())
        monkeypatch.setattr("findajob.spend_ceiling._DB_PATH", db_path)

        with patch("findajob.notifications.ntfy.send") as mock_send:
            with pytest.raises(LLMSpendCeilingExceeded):
                spend_ceiling.check_call_gate()

        kinds_sent = [c.kwargs["kind"] for c in mock_send.call_args_list]
        assert "spend_ceiling_reached" in kinds_sent


class TestLaunchGateFiresAlerts:
    def test_warning_fires_through_launch_gate(self, ceiling_db):
        conn, _, _ = ceiling_db
        _insert_cost_row(conn, cost_usd=85.00, logged_at=_this_month_ts())

        with patch("findajob.notifications.ntfy.send") as mock_send:
            result = spend_ceiling.check_launch_gate(conn)

        assert result is None
        assert mock_send.call_count == 1
        assert mock_send.call_args.kwargs["kind"] == "spend_ceiling_warning"

    def test_reached_fires_and_returns_refusal(self, ceiling_db):
        conn, _, _ = ceiling_db
        _insert_cost_row(conn, cost_usd=100.00, logged_at=_this_month_ts())

        with patch("findajob.notifications.ntfy.send") as mock_send:
            result = spend_ceiling.check_launch_gate(conn)

        assert result is not None
        assert result.ceiling_usd == 100.0
        kinds_sent = [c.kwargs["kind"] for c in mock_send.call_args_list]
        assert "spend_ceiling_reached" in kinds_sent
