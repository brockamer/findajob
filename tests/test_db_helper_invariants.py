"""#548 — Transparency invariants for ``findajob.db.connect`` adoption.

Replaces ``tests/test_admin_sqlite_uri_invariants.py`` (the #515 admin URI
test). After M4.E1.I2 swept all 32 call sites to use the helper,
``sqlite3.connect`` only exists inside the ``findajob.db`` package —
the old test's "every admin connect uses cross-stack URI" shape no
longer has admin connect calls to inspect. M5 expanded the package
(adding ``findajob.db.migrate``); the global-ban check allows
``sqlite3.connect`` anywhere inside the package directory.

**Global ban** — no ``sqlite3.connect(...)`` call anywhere in
``src/findajob/`` or ``scripts/`` outside of the
``src/findajob/db/`` package. This is the load-bearing assertion:
if it ever fires, a regression has reintroduced a direct
``sqlite3.connect`` somewhere the helper was supposed to mediate.

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
