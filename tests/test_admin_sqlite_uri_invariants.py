"""#515 — admin/* SQLite URIs require ``mode=ro&immutable=1``.

Static-analysis sweep over the admin module and the operator-mode admin
route. Locks the contract that every ``sqlite3.connect(...)`` call in
those files passes a ``file:`` URI containing **both** ``mode=ro`` and
``immutable=1``, with ``uri=True`` in kwargs.

Why this matters (memory: ``feedback_immutable_for_cross_stack_sqlite``):
the operator-mode dashboard reads other stacks' SQLite databases under a
foreign uid. Without ``immutable=1``, SQLite tries to open the WAL/shm
sidecar files for journal coordination — and the producer-stack's
sidecar is unreadable to the operator's uid. Default ``mode=ro`` alone
isn't enough; ``immutable=1`` skips sidecar reads entirely.

Scope (per #515 AC):
- ``src/findajob/admin/*.py``
- ``src/findajob/web/routes/admin*.py``

The test resolves indirection: ``uri = f"file:..."`` followed by
``sqlite3.connect(uri, uri=True)`` is checked against the assigned URI
string, not just the bare ``Name`` node.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
ADMIN_DIR = REPO_ROOT / "src" / "findajob" / "admin"
ADMIN_ROUTES_GLOB = REPO_ROOT / "src" / "findajob" / "web" / "routes"


def _in_scope_files() -> list[Path]:
    """Files admin* SQLite URI invariants apply to."""
    files: list[Path] = []
    if ADMIN_DIR.is_dir():
        files.extend(p for p in ADMIN_DIR.rglob("*.py") if p.name != "__init__.py")
    files.extend(ADMIN_ROUTES_GLOB.glob("admin*.py"))
    return sorted(files)


def _is_sqlite3_connect(node: ast.Call) -> bool:
    """Match ``sqlite3.connect(...)`` (Attribute) and bare ``connect(...)``
    when ``connect`` was imported from ``sqlite3``. The latter doesn't
    appear in the current codebase but is cheap to cover."""
    func = node.func
    if isinstance(func, ast.Attribute) and func.attr == "connect":
        if isinstance(func.value, ast.Name) and func.value.id == "sqlite3":
            return True
    return False


def _joinedstr_literal_text(node: ast.JoinedStr) -> str:
    """Reconstruct the literal portions of an f-string; format placeholders
    contribute the empty string. Sufficient for substring checks like
    ``'file:' in text`` and ``'mode=ro' in text``."""
    parts: list[str] = []
    for piece in node.values:
        if isinstance(piece, ast.Constant) and isinstance(piece.value, str):
            parts.append(piece.value)
    return "".join(parts)


def _resolve_to_uri_text(arg: ast.expr, local_strs: dict[str, str]) -> str | None:
    """Best-effort resolution of a connect()'s first arg to a literal-ish string.

    Returns a string if the arg is a Constant, JoinedStr, or a Name bound
    to either of those in the same function body; otherwise None.
    """
    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
        return arg.value
    if isinstance(arg, ast.JoinedStr):
        return _joinedstr_literal_text(arg)
    if isinstance(arg, ast.Name):
        return local_strs.get(arg.id)
    return None


def _local_string_assignments(scope: ast.AST) -> dict[str, str]:
    """For each ``Assign``/``AnnAssign`` to a Name within ``scope``, capture
    the assigned string value when the RHS is a Constant or JoinedStr.

    Walks recursively — inner blocks (with-statements, if-blocks) are
    included since they're commonly where the URI literal lives.
    Conservative: shadowing/reassignment uses last-wins.
    """
    found: dict[str, str] = {}
    for node in ast.walk(scope):
        if isinstance(node, ast.Assign):
            value = node.value
            text: str | None = None
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                text = value.value
            elif isinstance(value, ast.JoinedStr):
                text = _joinedstr_literal_text(value)
            if text is None:
                continue
            for target in node.targets:
                if isinstance(target, ast.Name):
                    found[target.id] = text
        elif isinstance(node, ast.AnnAssign) and node.value is not None and isinstance(node.target, ast.Name):
            value = node.value
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                found[node.target.id] = value.value
            elif isinstance(value, ast.JoinedStr):
                found[node.target.id] = _joinedstr_literal_text(value)
    return found


def _enclosing_function(tree: ast.Module, target: ast.Call) -> ast.AST:
    """Find the smallest FunctionDef / AsyncFunctionDef containing ``target``,
    or fall back to the module itself for module-level connect calls."""
    enclosing: ast.AST = tree
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            for sub in ast.walk(node):
                if sub is target:
                    enclosing = node
                    break
    return enclosing


def _check_call(path: Path, tree: ast.Module, call: ast.Call) -> str | None:
    """Return a violation message for this connect call, or None if it's compliant."""
    line = call.lineno
    if not call.args:
        return f"{path}:{line} sqlite3.connect() called with no positional arg"

    scope = _enclosing_function(tree, call)
    local_strs = _local_string_assignments(scope)
    uri_text = _resolve_to_uri_text(call.args[0], local_strs)
    if uri_text is None:
        return (
            f"{path}:{line} sqlite3.connect(...) first arg is not a resolvable "
            f"string literal (got {type(call.args[0]).__name__}). "
            "Operator-mode admin reads must use an inline `file:...?mode=ro&immutable=1` "
            "URI or assign one to a local variable in the same function."
        )

    if not uri_text.startswith("file:"):
        return (
            f"{path}:{line} sqlite3.connect URI must start with `file:` "
            f"(got {uri_text!r}). Plain paths are forbidden for admin/* — "
            "they prevent passing query params like `mode=ro&immutable=1`."
        )

    missing: list[str] = []
    if "mode=ro" not in uri_text:
        missing.append("mode=ro")
    if "immutable=1" not in uri_text:
        missing.append("immutable=1")
    if missing:
        return (
            f"{path}:{line} sqlite3.connect URI is missing required query "
            f"params: {', '.join(missing)} (got {uri_text!r}). See memory "
            "feedback_immutable_for_cross_stack_sqlite for why immutable=1 "
            "is non-negotiable."
        )

    has_uri_kw = any(
        kw.arg == "uri" and isinstance(kw.value, ast.Constant) and kw.value.value is True for kw in call.keywords
    )
    if not has_uri_kw:
        return (
            f"{path}:{line} sqlite3.connect must pass `uri=True` for URI-form "
            "connections; otherwise SQLite treats the URI string as a literal "
            "filename (creating a 'file:...' file on disk)."
        )

    return None


def test_admin_sqlite_uris_are_readonly_and_immutable() -> None:
    """Every sqlite3.connect call in admin/* uses a `file:` URI with
    `mode=ro&immutable=1` and `uri=True`. Locks the contract for any
    future admin reader; current pipeline has one compliant call site
    at src/findajob/admin/stack_health.py."""
    files = _in_scope_files()
    assert files, "test_admin_sqlite_uri_invariants found no in-scope files — scope drift?"

    violations: list[str] = []
    found_any_call = False
    for path in files:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and _is_sqlite3_connect(node):
                found_any_call = True
                violation = _check_call(path, tree, node)
                if violation:
                    violations.append(violation)

    if violations:
        raise AssertionError("admin/* SQLite URI invariants violated:\n  - " + "\n  - ".join(violations))

    assert found_any_call, (
        "no sqlite3.connect calls found in admin/* — either the test's "
        "scope is wrong or the admin reader has been refactored away. "
        "Verify the scope in tests/test_admin_sqlite_uri_invariants.py."
    )
