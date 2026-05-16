"""Tests for the spend-ceiling gate inside openrouter.complete() (#671).

Uses a real in-process SQLite DB (via the schema migration runner) and
mocks urllib.request.urlopen at the module level — no real HTTP calls.
The conftest autouse fixture redirects _SPEND_CEILING_PATH so prod
config doesn't interfere.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from findajob import config_loader
from findajob.llm.openrouter import LLMSpendCeilingExceeded, complete

# ── helpers ──────────────────────────────────────────────────────────────────


class _FakeResp:
    def __init__(self, body: bytes) -> None:
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self):
        return self._body


def _ok_body(text: str = "ok") -> dict:
    return {
        "id": "gen-test",
        "choices": [{"message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
        "usage": {
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "prompt_tokens_details": {"cached_tokens": 0},
            "cost": 0.001,
        },
    }


def _build_db(db_path: Path) -> sqlite3.Connection:
    """Create a schema-correct pipeline.db at db_path and return an open connection."""
    from findajob.db.migrate import apply_pending

    conn = sqlite3.connect(str(db_path))
    apply_pending(conn)
    return conn


def _insert_cost_row(conn: sqlite3.Connection, *, cost_usd: float, logged_at: str) -> None:
    """Insert a minimal cost_log row with the given cost and timestamp."""
    conn.execute(
        "INSERT INTO cost_log (operation, model, cost_usd, logged_at) VALUES (?, ?, ?, ?)",
        ("test_op", "test-model", cost_usd, logged_at),
    )
    conn.commit()


def _this_month_ts() -> str:
    """UTC timestamp string in the production format: 'YYYY-MM-DD HH:MM:SS'."""
    now = datetime.now(UTC)
    return now.strftime("%Y-%m-%d %H:%M:%S")


def _last_month_ts() -> str:
    """UTC timestamp string from 32 days ago (safely in the prior calendar month)."""
    from datetime import timedelta

    ago = datetime.now(UTC) - timedelta(days=32)
    return ago.strftime("%Y-%m-%d %H:%M:%S")


@pytest.fixture()
def roles_dir(tmp_path: Path) -> Path:
    d = tmp_path / "roles"
    d.mkdir()
    (d / "test_role.md").write_text("---\nmodel: openrouter:anthropic/claude-sonnet-4-6\n---\nSYSTEM\n")
    return d


# ── tests ────────────────────────────────────────────────────────────────────


class TestCallGateDisabled:
    def test_ceiling_absent_proceeds(self, tmp_path, roles_dir, monkeypatch):
        """When spend_ceiling.txt is missing, complete() proceeds normally."""
        # conftest already pointed _SPEND_CEILING_PATH at a non-existent fixture file;
        # nothing to set up — gate is a no-op.
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
        with patch(
            "findajob.llm.openrouter.urllib.request.urlopen",
            return_value=_FakeResp(json.dumps(_ok_body()).encode()),
        ):
            result = complete("test_role", "hello", roles_dir=roles_dir)
        assert result.text == "ok"

    def test_ceiling_present_sum_below_proceeds(self, tmp_path, roles_dir, monkeypatch):
        """When sum < ceiling, complete() proceeds normally."""
        # Configure ceiling at 50.00
        ceiling_path = tmp_path / "spend_ceiling.txt"
        ceiling_path.write_text("50.00")
        monkeypatch.setattr(config_loader, "_SPEND_CEILING_PATH", ceiling_path)

        # Build a real DB with sum=10.00
        db_path = tmp_path / "pipeline.db"
        conn = _build_db(db_path)
        _insert_cost_row(conn, cost_usd=10.00, logged_at=_this_month_ts())
        conn.close()
        monkeypatch.setattr("findajob.spend_ceiling._DB_PATH", db_path)

        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
        with patch(
            "findajob.llm.openrouter.urllib.request.urlopen",
            return_value=_FakeResp(json.dumps(_ok_body()).encode()),
        ):
            result = complete("test_role", "hello", roles_dir=roles_dir)
        assert result.text == "ok"


class TestCallGateBlocks:
    def test_ceiling_exceeded_raises_lsce(self, tmp_path, roles_dir, monkeypatch):
        """When sum >= ceiling, complete() raises LLMSpendCeilingExceeded."""
        ceiling_path = tmp_path / "spend_ceiling.txt"
        ceiling_path.write_text("50.00")
        monkeypatch.setattr(config_loader, "_SPEND_CEILING_PATH", ceiling_path)

        db_path = tmp_path / "pipeline.db"
        conn = _build_db(db_path)
        # Insert exactly at ceiling
        _insert_cost_row(conn, cost_usd=50.00, logged_at=_this_month_ts())
        conn.close()
        monkeypatch.setattr("findajob.spend_ceiling._DB_PATH", db_path)

        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
        with pytest.raises(LLMSpendCeilingExceeded) as exc_info:
            complete("test_role", "hello", roles_dir=roles_dir)

        exc = exc_info.value
        assert exc.ceiling_usd == 50.0
        assert exc.current_sum_usd == pytest.approx(50.0)
        assert "50.00" in str(exc)

    def test_ceiling_exceeded_over_limit(self, tmp_path, roles_dir, monkeypatch):
        """When sum > ceiling, also raises LSCE."""
        ceiling_path = tmp_path / "spend_ceiling.txt"
        ceiling_path.write_text("10.00")
        monkeypatch.setattr(config_loader, "_SPEND_CEILING_PATH", ceiling_path)

        db_path = tmp_path / "pipeline.db"
        conn = _build_db(db_path)
        _insert_cost_row(conn, cost_usd=15.00, logged_at=_this_month_ts())
        conn.close()
        monkeypatch.setattr("findajob.spend_ceiling._DB_PATH", db_path)

        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
        with pytest.raises(LLMSpendCeilingExceeded) as exc_info:
            complete("test_role", "hello", roles_dir=roles_dir)

        exc = exc_info.value
        assert exc.ceiling_usd == 10.0
        assert exc.current_sum_usd == pytest.approx(15.0)


class TestCallGateCrossMonth:
    def test_only_current_month_counts(self, tmp_path, roles_dir, monkeypatch):
        """Last-month rows don't count toward the ceiling; only current-month rows do."""
        ceiling_path = tmp_path / "spend_ceiling.txt"
        ceiling_path.write_text("50.00")
        monkeypatch.setattr(config_loader, "_SPEND_CEILING_PATH", ceiling_path)

        db_path = tmp_path / "pipeline.db"
        conn = _build_db(db_path)
        # Last month: 45.00 — would exceed 50.00 if summed across months
        _insert_cost_row(conn, cost_usd=45.00, logged_at=_last_month_ts())
        # This month: 10.00 — under ceiling
        _insert_cost_row(conn, cost_usd=10.00, logged_at=_this_month_ts())
        conn.close()
        monkeypatch.setattr("findajob.spend_ceiling._DB_PATH", db_path)

        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
        # Should proceed — only the 10.00 row counts
        with patch(
            "findajob.llm.openrouter.urllib.request.urlopen",
            return_value=_FakeResp(json.dumps(_ok_body()).encode()),
        ):
            result = complete("test_role", "hello", roles_dir=roles_dir)
        assert result.text == "ok"

    def test_current_month_exceeds_when_last_excluded(self, tmp_path, roles_dir, monkeypatch):
        """Verify the boundary: last-month rows excluded, this-month rows included."""
        ceiling_path = tmp_path / "spend_ceiling.txt"
        ceiling_path.write_text("50.00")
        monkeypatch.setattr(config_loader, "_SPEND_CEILING_PATH", ceiling_path)

        db_path = tmp_path / "pipeline.db"
        conn = _build_db(db_path)
        # Last month below ceiling (would be fine if included)
        _insert_cost_row(conn, cost_usd=5.00, logged_at=_last_month_ts())
        # This month at ceiling — should block
        _insert_cost_row(conn, cost_usd=50.00, logged_at=_this_month_ts())
        conn.close()
        monkeypatch.setattr("findajob.spend_ceiling._DB_PATH", db_path)

        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
        with pytest.raises(LLMSpendCeilingExceeded) as exc_info:
            complete("test_role", "hello", roles_dir=roles_dir)

        assert exc_info.value.current_sum_usd == pytest.approx(50.0)
