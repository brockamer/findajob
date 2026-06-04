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


def init_test_db(db_path: Path) -> None:
    """Create a fresh ``pipeline.db`` at ``db_path`` with the production
    migration chain applied (mirrors ``scripts/init_db.py`` → ``apply_pending``).

    Test fixtures that need a SQLite pipeline DB call this helper instead
    of hand-rolling schema. Hand-rolled CREATE-TABLE statements were a
    documented anti-pattern (#721): every migration that introduced a new
    table (e.g. 0005's ``view_prefs``) silently broke every fixture that
    hit a route touching the new table, requiring a whack-a-mole
    ``ensure_<table>_table()`` helper. Routing schema setup through the
    real migration runner eliminates that fragility class — fixtures
    inherit every future table automatically.

    Opens and closes its own connection; the caller opens a fresh
    connection afterward for INSERTs / assertions. Idempotent — calling
    twice against the same path is harmless (``apply_pending`` short-
    circuits when the DB is already at the head version).
    """
    from findajob.db.migrate import apply_pending

    conn = sqlite3.connect(db_path)
    try:
        apply_pending(conn)
    finally:
        conn.close()


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


@pytest.fixture(autouse=True)
def _no_live_update_check():
    """Stop the #1016 update-availability check from firing a live GitHub call
    when tests hit ``/board/dashboard``. That route schedules a ``BackgroundTasks``
    refresh when the update cache is stale, and Starlette's ``TestClient`` runs
    background tasks synchronously — so a cold module cache would make ~every
    dashboard test reach ``api.github.com`` (green only because the check is
    fail-open) and leak a fetched ``latest`` across tests. Fresh-stamp the cache
    (not stale) with no known latest, so no refresh is scheduled and no banner
    shows by default. Tests that exercise the banner set ``update_check._cache``
    explicitly after this fixture runs.
    """
    from findajob.web import update_check

    update_check._cache["checked_at"] = update_check._now()
    update_check._cache["latest"] = None
    yield
