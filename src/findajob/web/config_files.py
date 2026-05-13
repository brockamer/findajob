"""Allowlist for the /config/ editor (#149).

The editor may read and write only the files named in :data:`EDITABLE_CATEGORIES`
plus any ``.md`` file directly under a wildcard glob directory (currently
``config/roles/`` and ``config/tool_prompts/``). Path-traversal guards reject
any relpath with a dot component, a leading slash, or a resolved absolute path
outside ``base_root``.

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
        "candidate_context/discovered_companies.md",
    ],
    "Search config": [
        "config/target_companies.md",
        "config/business_sector_employers_reference.md",
        "config/prefilter_rules.yaml",
        "config/in_domain_patterns.yaml",
        "config/jsearch_queries.txt",
        "config/feed_urls.txt",
        # #362 — operator-curated company-name aliases for the rejection-detection
        # matcher. Edited live via /config/; hot-reloaded on the next detection
        # cycle (matcher reads the file on every match_job call).
        "config/company_aliases.yaml",
    ],
    "Role prompts": "config/roles/*.md",
    # #150 — operator-editable prompts powering /tools/ tiles. Same
    # overwrite-on-restart semantics as role prompts: tracked files in
    # bundled-config get re-seeded by ops/entrypoint.sh on every container
    # start, so in-place edits persist only until the next docker compose pull.
    "Tool prompts": "config/tool_prompts/*.md",
}

_FLAT_ALLOWLIST: frozenset[str] = frozenset(
    p for value in EDITABLE_CATEGORIES.values() if isinstance(value, list) for p in value
)

# Directories whose direct ``.md`` children are wildcard-editable. Derived
# from EDITABLE_CATEGORIES values of shape ``<dir>/*.md`` so that adding a
# new wildcard category is a one-line registry edit.
_GLOB_DIRS: tuple[str, ...] = tuple(
    value.rsplit("/", 1)[0] for value in EDITABLE_CATEGORIES.values() if isinstance(value, str)
)


def _is_md_under_dir(relpath: str, parent_dir: str) -> bool:
    """True iff ``relpath`` is a direct ``.md`` child of ``parent_dir``.

    Direct child only — subdirectories are not allowed.
    """
    p = PurePosixPath(relpath)
    if p.suffix != ".md":
        return False
    if str(p.parent) != parent_dir:
        return False
    if any(part in ("", ".", "..") for part in p.parts):
        return False
    return True


def is_editable(relpath: str) -> bool:
    """True iff ``relpath`` is on the editable allowlist.

    Rejects: absolute paths, empty string, paths with dot or parent-ref
    components, paths not in the flat allowlist and not a direct ``.md``
    child of any wildcard glob directory.
    """
    if not relpath or relpath.startswith("/"):
        return False
    parts = relpath.split("/")
    if any(part in ("", ".", "..") for part in parts):
        return False
    if relpath in _FLAT_ALLOWLIST:
        return True
    return any(_is_md_under_dir(relpath, d) for d in _GLOB_DIRS)


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
    Each file dict is ``{"relpath": str, "exists": bool}``. Wildcard
    categories (role prompts, tool prompts) are discovered from the
    filesystem (glob) so new files appear automatically; flat-list
    categories use the fixed allowlist.
    """
    categories: list[dict] = []

    for name, value in EDITABLE_CATEGORIES.items():
        if isinstance(value, list):
            files = [{"relpath": p, "exists": (base_root / p).is_file()} for p in sorted(value)]
        else:
            glob_dir = value.rsplit("/", 1)[0]
            dir_path = base_root / glob_dir
            glob_files: list[dict] = []
            if dir_path.is_dir():
                for child in sorted(dir_path.iterdir()):
                    if child.is_file() and child.suffix == ".md":
                        glob_files.append(
                            {
                                "relpath": f"{glob_dir}/{child.name}",
                                "exists": True,
                            }
                        )
            files = glob_files
        categories.append({"name": name, "files": files})

    return categories
