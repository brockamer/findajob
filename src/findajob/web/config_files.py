"""Allowlist for the /config/ editor (#149).

The editor may read and write only the files named in :data:`EDITABLE_CATEGORIES`
plus any ``config/roles/*.md`` file. Path-traversal guards reject any relpath
with a dot component, a leading slash, or a resolved absolute path outside
``base_root``.

The module has no FastAPI import — it is a pure function-level API so the
allowlist can be unit-tested and reused from other surfaces (CLI tools,
future materials editor).
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath

EDITABLE_CATEGORIES: dict[str, list[str] | str] = {
    "Candidate context": [
        "candidate_context/profile.md",
        "candidate_context/master_resume.md",
    ],
    "Search config": [
        "config/prefilter_rules.yaml",
        "config/in_domain_patterns.yaml",
        "config/jsearch_queries.txt",
        "config/feed_urls.txt",
    ],
    "Role prompts": "config/roles/*.md",
}

_FLAT_ALLOWLIST: frozenset[str] = frozenset(
    p for value in EDITABLE_CATEGORIES.values() if isinstance(value, list) for p in value
)

_ROLES_DIR = "config/roles"


def _is_role_file(relpath: str) -> bool:
    """True iff ``relpath`` is a direct ``.md`` child of ``config/roles/``.

    Direct child only — subdirectories are not allowed.
    """
    p = PurePosixPath(relpath)
    if p.suffix != ".md":
        return False
    if str(p.parent) != _ROLES_DIR:
        return False
    if any(part in ("", ".", "..") for part in p.parts):
        return False
    return True


def is_editable(relpath: str) -> bool:
    """True iff ``relpath`` is on the editable allowlist.

    Rejects: absolute paths, empty string, paths with dot or parent-ref
    components, paths not in the flat allowlist and not a role file.
    """
    if not relpath or relpath.startswith("/"):
        return False
    parts = relpath.split("/")
    if any(part in ("", ".", "..") for part in parts):
        return False
    return relpath in _FLAT_ALLOWLIST or _is_role_file(relpath)


def resolve_editable(relpath: str, base_root: Path) -> Path | None:
    """Return the absolute :class:`Path` for ``relpath`` or ``None`` if rejected.

    Runs :func:`is_editable` first, then resolves symlinks and verifies the
    final absolute path is still under ``base_root``. Returns the path even
    if the file does not yet exist — callers handle the missing case.
    """
    if not is_editable(relpath):
        return None

    base_resolved = base_root.resolve()
    candidate = (base_root / relpath).resolve()

    try:
        candidate.relative_to(base_resolved)
    except ValueError:
        return None

    return candidate


def list_editable(base_root: Path) -> list[dict]:
    """Enumerate the allowlist for the index page.

    Returns a list of category dicts, each ``{"name": str, "files": [...]}``.
    Each file dict is ``{"relpath": str, "exists": bool}``. Role-prompt files
    are discovered from the filesystem (glob) so new role files appear
    automatically; the other two categories use the fixed allowlist.
    """
    categories: list[dict] = []

    for name, value in EDITABLE_CATEGORIES.items():
        if isinstance(value, list):
            files = [{"relpath": p, "exists": (base_root / p).is_file()} for p in sorted(value)]
        else:
            roles_dir = base_root / _ROLES_DIR
            role_files: list[dict] = []
            if roles_dir.is_dir():
                for child in sorted(roles_dir.iterdir()):
                    if child.is_file() and child.suffix == ".md":
                        role_files.append(
                            {
                                "relpath": f"{_ROLES_DIR}/{child.name}",
                                "exists": True,
                            }
                        )
            files = role_files
        categories.append({"name": name, "files": files})

    return categories
