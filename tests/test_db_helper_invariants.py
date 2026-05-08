"""#548 — Transparency invariants for ``findajob.db.connect`` adoption.

Replaces ``tests/test_admin_sqlite_uri_invariants.py`` (the #515 admin URI
test). After M4.E1.I2 swept all 32 call sites to use the helper,
``sqlite3.connect`` only exists inside the ``findajob.db`` package —
the old test's "every admin connect uses cross-stack URI" shape no
longer has admin connect calls to inspect. M5 expanded the package
(adding ``findajob.db.migrate``); the global-ban check allows
``sqlite3.connect`` anywhere inside the package directory.

Two complementary invariants live here:

1. **Global ban** — no ``sqlite3.connect(...)`` call anywhere in
   ``src/findajob/`` or ``scripts/`` outside of the
   ``src/findajob/db/`` package. This is the load-bearing assertion:
   if it ever fires, a regression has reintroduced a direct
   ``sqlite3.connect`` somewhere the helper was supposed to mediate.

2. **Admin cross-stack invariant** — every ``connect(...)`` call inside
   ``src/findajob/admin/`` must pass ``cross_stack=True`` AND
   ``ro=True`` keyword arguments. Codifies CLAUDE.md's operator-mode
   admin invariant ("read-only, no POST handlers"). The helper itself
   raises ``ValueError`` for ``cross_stack=True`` without ``ro=True``,
   so this test is belt-and-suspenders — the helper enforces at
   runtime, the test enforces at static-analysis time.

The AST-walk approach matches the prior test's style: it resolves
function-local string assignments and checks call-site kwargs without
needing to import the modules.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = REPO_ROOT / "src" / "findajob"
SCRIPTS_DIR = REPO_ROOT / "scripts"
ADMIN_DIR = SRC_DIR / "admin"
DB_PKG = SRC_DIR / "db"


def _python_files(root: Path) -> list[Path]:
    """All .py files under ``root``, excluding __pycache__ + dotted dirs."""
    return sorted(p for p in root.rglob("*.py") if "__pycache__" not in p.parts)


def _is_sqlite3_connect(node: ast.Call) -> bool:
    """Match ``sqlite3.connect(...)``."""
    func = node.func
    return (
        isinstance(func, ast.Attribute)
        and func.attr == "connect"
        and isinstance(func.value, ast.Name)
        and func.value.id == "sqlite3"
    )


def _is_findajob_connect(node: ast.Call) -> bool:
    """Match ``connect(...)`` (Name) or ``db.connect(...)`` (Attribute on db).

    Requires the call site to have imported ``connect`` from
    ``findajob.db`` — assumed by the caller scope; we treat any bare
    ``connect()`` in admin/* as the helper since that file imports
    ``from findajob.db import connect``.
    """
    func = node.func
    if isinstance(func, ast.Name) and func.id == "connect":
        return True
    if (
        isinstance(func, ast.Attribute)
        and func.attr == "connect"
        and isinstance(func.value, ast.Name)
        and func.value.id == "db"
    ):
        return True
    return False


def test_no_sqlite3_connect_outside_helper() -> None:
    """The load-bearing M4 invariant: every DB connection in ``src/`` and
    ``scripts/`` flows through ``findajob.db.connect``.

    The helper itself is the one place ``sqlite3.connect`` may appear.
    A regression here means a future call site bypassed the helper —
    most concerning for cross-stack reads (silently reintroduces the
    foreign-uid WAL-sidecar bug from #333) but also for FastAPI routes
    that need ``check_same_thread=False`` (silently reintroduces the
    threadpool race from #486).
    """
    violations: list[str] = []
    db_pkg_resolved = DB_PKG.resolve()
    for path in _python_files(SRC_DIR) + _python_files(SCRIPTS_DIR):
        if path.resolve().is_relative_to(db_pkg_resolved):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError as e:
            violations.append(f"{path}: parse error: {e}")
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and _is_sqlite3_connect(node):
                rel = path.relative_to(REPO_ROOT)
                violations.append(f"{rel}:{node.lineno} bypasses findajob.db.connect")

    if violations:
        raise AssertionError(
            "sqlite3.connect found outside findajob/db.py — every connection "
            "must flow through findajob.db.connect (M4.E1.I2):\n  - " + "\n  - ".join(violations)
        )


def test_admin_connects_are_cross_stack_readonly() -> None:
    """Every ``findajob.db.connect`` call inside ``src/findajob/admin/``
    must pass ``cross_stack=True`` AND ``ro=True``.

    Codifies CLAUDE.md's operator-mode admin invariant ("read-only,
    no POST handlers"). The helper raises ``ValueError`` for
    ``cross_stack=True`` without ``ro=True`` (runtime), and this test
    locks both flags as required (static analysis).

    Scope is admin/* only — other modules legitimately use ``ro=False``
    (most pipeline writers) or ``ro=True`` without ``cross_stack`` (web
    nav-chip same-stack reads).
    """
    violations: list[str] = []
    found_any_call = False
    for path in _python_files(ADMIN_DIR):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError as e:
            violations.append(f"{path}: parse error: {e}")
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and _is_findajob_connect(node):
                found_any_call = True
                kwargs = {kw.arg: kw.value for kw in node.keywords if kw.arg}
                cross_stack_ok = (
                    "cross_stack" in kwargs
                    and isinstance(kwargs["cross_stack"], ast.Constant)
                    and kwargs["cross_stack"].value is True
                )
                ro_ok = "ro" in kwargs and isinstance(kwargs["ro"], ast.Constant) and kwargs["ro"].value is True
                if not (cross_stack_ok and ro_ok):
                    rel = path.relative_to(REPO_ROOT)
                    missing = []
                    if not cross_stack_ok:
                        missing.append("cross_stack=True")
                    if not ro_ok:
                        missing.append("ro=True")
                    violations.append(f"{rel}:{node.lineno} admin connect missing {', '.join(missing)}")

    if violations:
        raise AssertionError(
            "admin/* findajob.db.connect calls must pass cross_stack=True AND "
            "ro=True (operator-dashboard read-only invariant):\n  - " + "\n  - ".join(violations)
        )

    assert found_any_call, (
        "no findajob.db.connect calls found in src/findajob/admin/* — either "
        "scope drift in this test or the admin reader has been refactored "
        "away. Verify in tests/test_db_helper_invariants.py."
    )
