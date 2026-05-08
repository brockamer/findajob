"""#546 — Unit tests for ``findajob.db.connect`` helper.

Locks the four-axis API contract that M4.E1.I2's call-site sweep will
adopt across all 32 ``sqlite3.connect`` sites:

- Plain-write returns a writable connection.
- ``ro=True`` opens read-only (write attempts raise OperationalError).
- ``cross_stack=True`` constructs ``mode=ro&immutable=1`` URI with
  ``uri=True``. Required for foreign-uid reads of tester DBs from the
  operator-mode admin dashboard — see
  ``feedback_immutable_for_cross_stack_sqlite``.
- ``cross_stack=True`` without ``ro=True`` raises ``ValueError`` —
  codifies the operator-dashboard read-only invariant from CLAUDE.md.
- ``timeout`` and ``check_same_thread`` are plumbed through.

The URI-shape assertions monkeypatch ``sqlite3.connect`` to capture the
arguments the helper passes — the only portable way to verify URI
construction since SQLite exposes no introspection for the open-flags.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from findajob.db import connect


def _make_db(path: Path) -> None:
    """Create a SQLite DB file with a single table for read/write tests."""
    conn = sqlite3.connect(str(path))
    try:
        conn.execute("CREATE TABLE t (n INTEGER)")
        conn.execute("INSERT INTO t (n) VALUES (1)")
        conn.commit()
    finally:
        conn.close()


def test_plain_connect_is_writable(tmp_path: Path) -> None:
    db = tmp_path / "test.db"
    _make_db(db)

    conn = connect(db)
    try:
        conn.execute("INSERT INTO t (n) VALUES (2)")
        conn.commit()
        rows = conn.execute("SELECT n FROM t ORDER BY n").fetchall()
    finally:
        conn.close()

    assert rows == [(1,), (2,)]


def test_ro_connect_blocks_writes(tmp_path: Path) -> None:
    db = tmp_path / "test.db"
    _make_db(db)

    conn = connect(db, ro=True)
    try:
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            conn.execute("INSERT INTO t (n) VALUES (99)")
    finally:
        conn.close()


def test_cross_stack_uri_carries_immutable_flag(tmp_path: Path) -> None:
    """``cross_stack=True`` must produce a URI containing both
    ``mode=ro`` AND ``immutable=1``, with ``uri=True`` in kwargs.

    This is the load-bearing assertion for cross-stack reads — without
    ``immutable=1``, SQLite tries to open the WAL/shm sidecars for
    journal coordination, and a foreign uid (operator container vs
    tester container) can't read those sidecars. The helper is the
    single enforcement point per M4 acceptance criterion #3.
    """
    db = tmp_path / "test.db"
    _make_db(db)

    captured: dict = {}
    real_connect = sqlite3.connect

    def fake_connect(*args: object, **kwargs: object) -> sqlite3.Connection:
        captured["args"] = args
        captured["kwargs"] = kwargs
        return real_connect(*args, **kwargs)  # type: ignore[arg-type]

    with patch("findajob.db.sqlite3.connect", side_effect=fake_connect):
        conn = connect(db, ro=True, cross_stack=True)
        conn.close()

    uri = captured["args"][0]
    assert isinstance(uri, str)
    assert uri.startswith("file:")
    assert "mode=ro" in uri
    assert "immutable=1" in uri
    assert captured["kwargs"]["uri"] is True


def test_ro_without_cross_stack_omits_immutable(tmp_path: Path) -> None:
    """Same-stack read-only opens with ``mode=ro`` but NOT
    ``immutable=1`` — same-stack readers benefit from WAL coherence."""
    db = tmp_path / "test.db"
    _make_db(db)

    captured: dict = {}
    real_connect = sqlite3.connect

    def fake_connect(*args: object, **kwargs: object) -> sqlite3.Connection:
        captured["args"] = args
        captured["kwargs"] = kwargs
        return real_connect(*args, **kwargs)  # type: ignore[arg-type]

    with patch("findajob.db.sqlite3.connect", side_effect=fake_connect):
        conn = connect(db, ro=True)
        conn.close()

    uri = captured["args"][0]
    assert "mode=ro" in uri
    assert "immutable=1" not in uri
    assert captured["kwargs"]["uri"] is True


def test_cross_stack_without_ro_raises_value_error(tmp_path: Path) -> None:
    """The guard that codifies CLAUDE.md's operator-dashboard
    read-only invariant. Cross-stack writes are forbidden by topology
    (foreign uid + producer's WAL sidecar permissions) AND by policy
    (admin dashboard is read-only, no POST handlers)."""
    db = tmp_path / "test.db"
    _make_db(db)

    with pytest.raises(ValueError, match="cross_stack=True requires ro=True"):
        connect(db, cross_stack=True)


def test_kwargs_passthrough(tmp_path: Path) -> None:
    """``timeout`` and ``check_same_thread`` reach the underlying
    ``sqlite3.connect`` unchanged for every code path
    (plain-write / ro / cross_stack)."""
    db = tmp_path / "test.db"
    _make_db(db)

    captured: dict = {}
    real_connect = sqlite3.connect

    def fake_connect(*args: object, **kwargs: object) -> sqlite3.Connection:
        captured.clear()
        captured["kwargs"] = kwargs
        return real_connect(*args, **kwargs)  # type: ignore[arg-type]

    with patch("findajob.db.sqlite3.connect", side_effect=fake_connect):
        connect(db, timeout=5.0, check_same_thread=False).close()
        assert captured["kwargs"]["timeout"] == 5.0
        assert captured["kwargs"]["check_same_thread"] is False

        connect(db, ro=True, timeout=7.0, check_same_thread=False).close()
        assert captured["kwargs"]["timeout"] == 7.0
        assert captured["kwargs"]["check_same_thread"] is False

        connect(db, ro=True, cross_stack=True, timeout=3.0).close()
        assert captured["kwargs"]["timeout"] == 3.0
        assert captured["kwargs"]["check_same_thread"] is True


def test_path_object_accepted(tmp_path: Path) -> None:
    """The helper's ``path`` parameter is typed ``str | Path``;
    callers pass either form. Verify both produce identical URIs for
    the cross-stack path."""
    db = tmp_path / "test.db"
    _make_db(db)

    captured: list[str] = []
    real_connect = sqlite3.connect

    def fake_connect(*args: object, **kwargs: object) -> sqlite3.Connection:
        captured.append(args[0])  # type: ignore[arg-type]
        return real_connect(*args, **kwargs)  # type: ignore[arg-type]

    with patch("findajob.db.sqlite3.connect", side_effect=fake_connect):
        connect(db, ro=True, cross_stack=True).close()
        connect(str(db), ro=True, cross_stack=True).close()

    assert captured[0] == captured[1]
