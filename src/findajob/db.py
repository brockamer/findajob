"""Centralized SQLite connection helper.

One function — :func:`connect` — replaces the 32 ``sqlite3.connect(...)``
call sites that had been splayed across ``src/findajob/`` and
``scripts/``. Three connection patterns existed in the wild:

1. **Plain write** (most common) — ``sqlite3.connect(path, timeout=30)``.
2. **Cross-stack read-only** — ``sqlite3.connect(uri, uri=True)`` with
   ``f"file:{path}?mode=ro&immutable=1"``. Used by the operator-mode
   admin dashboard reading other stacks' DBs under a foreign uid; the
   ``immutable=1`` flag skips the WAL/shm sidecars (which the foreign
   uid can't read). Without it, opens fail with "unable to open
   database file" — see ``feedback_immutable_for_cross_stack_sqlite``
   in operator memory.
3. **FastAPI per-request** — ``sqlite3.connect(path,
   check_same_thread=False)``. Required because BaseHTTPMiddleware
   wraps the inner app in a separate anyio task; FastAPI's ``Depends``
   resolution and the route handler can land on different threadpool
   workers under concurrent load (#486).

The helper exposes all four axes (``ro``, ``cross_stack``, ``timeout``,
``check_same_thread``) explicitly rather than inferring them. Identical
SQL behavior is the M4 acceptance criterion — the helper is a
*replacement*, not a re-design.

What this helper deliberately does NOT do:

- No ``row_factory`` defaulting. Some callers want ``sqlite3.Row``,
  others use tuple-style access. Setting one quietly breaks the other.
- No ``PRAGMA journal_mode=WAL``. Currently called explicitly only in
  ``triage/orchestrator.py``; preserving call-site behavior keeps SQL
  semantics identical.
- No connection-context-manager wrapping. Callers choose ``with`` or
  ``try/finally``.

Future helpers for transactions, cursor management, or pooling can be
added here, but cross-cutting policy belongs in ``findajob.actions``
or domain modules — this module is *only* the connection door.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path


def connect(
    path: str | Path,
    *,
    ro: bool = False,
    cross_stack: bool = False,
    timeout: float = 30.0,
    check_same_thread: bool = True,
) -> sqlite3.Connection:
    """Open a SQLite connection with the appropriate URI/flags for the use case.

    Args:
        path: Filesystem path to the database file.
        ro: Open read-only (``mode=ro``). Write attempts raise
            ``sqlite3.OperationalError``.
        cross_stack: Open with ``mode=ro&immutable=1`` for foreign-uid
            reads (operator-mode admin dashboard). Implies ``ro=True``;
            passing ``cross_stack=True`` without ``ro=True`` raises
            ``ValueError``. The ``immutable=1`` flag tells SQLite the
            DB is a fixed snapshot — no WAL/shm sidecar reads, no
            locking. Tradeoff: in-flight WAL writes from the producer
            stack are invisible until the next checkpoint.
        timeout: Busy-handler timeout in seconds. Default 30 matches
            the dominant pattern; web nav-chip queries pass
            ``timeout=5``.
        check_same_thread: Default ``True``. Pass ``False`` for the
            FastAPI per-request connection where Depends + handler may
            land on different threadpool workers (#486).

    Returns:
        An open ``sqlite3.Connection``.

    Raises:
        ValueError: If ``cross_stack=True`` without ``ro=True``. Codifies
            the operator-dashboard read-only invariant from CLAUDE.md.
    """
    if cross_stack and not ro:
        raise ValueError("cross_stack=True requires ro=True")
    if cross_stack:
        uri = f"file:{path}?mode=ro&immutable=1"
        return sqlite3.connect(uri, uri=True, timeout=timeout, check_same_thread=check_same_thread)
    if ro:
        uri = f"file:{path}?mode=ro"
        return sqlite3.connect(uri, uri=True, timeout=timeout, check_same_thread=check_same_thread)
    return sqlite3.connect(str(path), timeout=timeout, check_same_thread=check_same_thread)
