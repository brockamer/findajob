"""Global test fixtures.

Redirects findajob.config_loader to read from tests/fixtures/config/
instead of the production config directory, and resets its cache before
each test so a test's config edits don't leak into the next test.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from findajob import config_loader

FIXTURES = Path(__file__).parent / "fixtures" / "config"


def ensure_view_prefs_table(conn: sqlite3.Connection) -> None:
    """Create migration 0005's view_prefs table on a hand-rolled test schema.

    Existing board-route tests build their own minimal schema directly
    (jobs, audit_log) rather than running ``apply_pending``. The #277
    persistence layer auto-saves on every page + /rows GET, so any test
    that hits a board route now needs this table to exist or the route
    raises ``sqlite3.OperationalError: no such table: view_prefs``.

    Production fixes this at container start via ``scripts/init_db.py``.
    Tests fix it by calling this helper after their hand-built CREATEs.
    Keep the DDL in sync with ``src/findajob/migrations/0005_view_prefs.sql``.
    """
    conn.execute(
        """CREATE TABLE IF NOT EXISTS view_prefs (
            tab TEXT PRIMARY KEY CHECK (tab IN (
                'dashboard','applied','review','waitlist',
                'rejected','not_selected','archive'
            )),
            query_string TEXT NOT NULL,
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        )"""
    )


@pytest.fixture(autouse=True)
def _use_fixture_configs(monkeypatch):
    monkeypatch.setattr(config_loader, "_RULES_PATH", FIXTURES / "prefilter_rules.yaml")
    monkeypatch.setattr(config_loader, "_IN_DOMAIN_PATH", FIXTURES / "in_domain_patterns.yaml")
    monkeypatch.setattr(config_loader, "_TARGET_COMPANIES_PATH", FIXTURES / "target_companies.md")
    monkeypatch.setattr(config_loader, "_EXCLUDED_EMPLOYERS_PATH", FIXTURES / "excluded_employers.yaml")
    monkeypatch.setattr(config_loader, "_SPEND_CEILING_PATH", FIXTURES / "spend_ceiling.txt")
    config_loader._reset_cache()
    yield
    config_loader._reset_cache()
