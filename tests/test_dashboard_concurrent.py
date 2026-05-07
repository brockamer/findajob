"""Regression test for #486 — dashboard 5xx under concurrent load.

Pre-fix, the per-request SQLite connection produced by `get_db()` in
`findajob.web.app` was created without `check_same_thread=False`. The
`BasicAuthMiddleware` is a `BaseHTTPMiddleware` subclass which wraps the
inner app in a separate anyio task; under concurrent load (e.g., HTMX
polling), Depends resolution and the route handler can land on different
threadpool workers. The connection was created in thread A and used in
thread B, raising
`sqlite3.ProgrammingError: SQLite objects created in a thread can only be
used in that same thread.` ~85% of the time on the operator's stack.

The TestClient-based "concurrent burst" approach does not reliably
reproduce this because TestClient routes every call through a single
anyio portal that pins to one threadpool worker. Instead, this test
reconstructs the exact scenario the bug requires:

  1. Build the real app via `create_app` (so the registered get_db
     override is the one we ship to production)
  2. Open the per-request connection in the test's main thread
  3. Use it from a different thread
  4. Assert the call succeeds

Pre-fix this raises `sqlite3.ProgrammingError`. Post-fix it passes.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from threading import Thread

import pytest

from findajob.onboarding import mark_complete
from findajob.web.app import create_app
from findajob.web.routes import materials as _materials_routes


@pytest.fixture
def app_with_db(tmp_path: Path):
    db = tmp_path / "pipeline.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE jobs (id TEXT, fingerprint TEXT, stage TEXT)")
    conn.execute("INSERT INTO jobs (fingerprint, stage) VALUES ('fp1', 'scored')")
    conn.commit()
    conn.close()
    companies = tmp_path / "companies"
    companies.mkdir()
    mark_complete(tmp_path)
    return create_app(companies_root=companies, db_path=db, base_root=tmp_path)


def test_get_db_connection_is_usable_from_a_different_thread(app_with_db) -> None:
    """The connection produced by get_db must survive a thread handoff.

    This is the exact invariant `check_same_thread=False` establishes; it
    is what BaseHTTPMiddleware's task-spawning forces under concurrent
    load on the dashboard route.
    """
    get_db = app_with_db.dependency_overrides[_materials_routes.get_db]
    gen = get_db()
    conn = next(gen)
    try:
        result: list[object] = []

        def use_conn_from_other_thread() -> None:
            try:
                row = conn.execute("SELECT fingerprint FROM jobs").fetchone()
                result.append(row[0])
            except sqlite3.ProgrammingError as exc:
                result.append(("CROSS_THREAD_ERROR", str(exc)))
            except Exception as exc:  # noqa: BLE001
                result.append(("UNEXPECTED", type(exc).__name__, str(exc)))

        t = Thread(target=use_conn_from_other_thread)
        t.start()
        t.join(timeout=5)
        assert not t.is_alive(), "thread hung"
        assert result == ["fp1"], f"connection was not usable across threads: {result}"
    finally:
        try:
            next(gen)
        except StopIteration:
            pass
