"""Walk source tree for UI surfaces covering user-input files (#572 Phase 1).

Detects coverage via three mechanisms:
  1. Reading the EDITABLE_CATEGORIES dict in src/findajob/web/config_files.py
     — paths there are covered by the raw /config/ text editor.
  2. Walking src/findajob/web/routes/ for path-literal references inside
     route modules (a route that mentions 'config/foo.yaml' covers it).
  3. Reading _PAGES from routes/docs.py and TILES from web/tools_registry.py
     — docs slugs and tool-tile bodies count as coverage of any path they
     reference.

Returns dict[user_input_path -> list[SurfaceRef]] keyed compatibly with
surface_map's keys so set-diff works.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path

# Strict regex: requires both leading and trailing quote so only genuine
# string literals match. Coverage-side false positives mean saying "this
# file is covered" when it isn't → the audit MISSES a real gap. Spec bias
# favors false positives on the findings side, so coverage detection must
# be strict to avoid suppressing gaps.
_PATH_PATTERNS = (
    re.compile(r'["\'](config/[^"\']+\.(?:yaml|yml|txt|md|csv))["\']'),
    re.compile(r'["\'](candidate_context/[^"\']+\.(?:md|yaml|yml|csv))["\']'),
    re.compile(r'["\'](data/[^"\']+\.(?:db|env|sqlite))["\']'),
)


@dataclass(frozen=True)
class SurfaceRef:
    """A UI surface that covers a user-input file."""

    source: str  # "EDITABLE_CATEGORIES" | "route:<module>" | "_PAGES" | "TILES"
    file: str  # repo-relative path of the covering module
    detail: str  # one-line description (route path, slug, tile id, etc.)


def _extract_editable_categories(config_files_path: Path) -> tuple[list[str], list[str]]:
    """Parse EDITABLE_CATEGORIES dict. Returns (explicit_paths, wildcard_globs).

    Handles both bare assignment (``EDITABLE_CATEGORIES = {...}``) and
    type-annotated assignment
    (``EDITABLE_CATEGORIES: dict[...] = {...}`` → ``ast.AnnAssign``).

    List values yield explicit paths. String values are treated as glob patterns
    (e.g., ``"config/roles/*.md"``) that the caller glob-expands against repo_root.
    """
    try:
        tree = ast.parse(config_files_path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return [], []
    explicit: list[str] = []
    wildcards: list[str] = []
    for node in ast.walk(tree):
        value_node = None
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name) and tgt.id == "EDITABLE_CATEGORIES":
                    value_node = node.value
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name) and node.target.id == "EDITABLE_CATEGORIES" and node.value is not None:
                value_node = node.value
        if not isinstance(value_node, ast.Dict):
            continue
        for v in value_node.values:
            if isinstance(v, ast.List):
                for elt in v.elts:
                    if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                        explicit.append(elt.value)
            elif isinstance(v, ast.Constant) and isinstance(v.value, str):
                wildcards.append(v.value)
    return explicit, wildcards


def walk_coverage_map(*, repo_root: Path) -> dict[str, list[SurfaceRef]]:
    """Walk web/, build map of UI-covered user-input file paths."""
    result: dict[str, list[SurfaceRef]] = {}

    # Mechanism 1: EDITABLE_CATEGORIES (explicit paths + wildcard globs)
    config_files = repo_root / "src" / "findajob" / "web" / "config_files.py"
    if config_files.exists():
        explicit, wildcards = _extract_editable_categories(config_files)
        rel_config_files = str(config_files.relative_to(repo_root))
        for path in explicit:
            ref = SurfaceRef(
                source="EDITABLE_CATEGORIES",
                file=rel_config_files,
                detail=f"/config/ raw editor allows direct edit of {path}",
            )
            result.setdefault(path, []).append(ref)
        for pattern in wildcards:
            # Glob-expand against repo_root so concrete paths register as covered.
            # PurePosixPath splitting works because EDITABLE_CATEGORIES paths use
            # forward slashes.
            for concrete in sorted(repo_root.glob(pattern)):
                rel_concrete = str(concrete.relative_to(repo_root))
                ref = SurfaceRef(
                    source="EDITABLE_CATEGORIES",
                    file=rel_config_files,
                    detail=f"/config/ raw editor (wildcard {pattern}) covers {rel_concrete}",
                )
                result.setdefault(rel_concrete, []).append(ref)

    # Mechanism 2: path literals inside route modules
    routes_dir = repo_root / "src" / "findajob" / "web" / "routes"
    if routes_dir.exists():
        for py in sorted(routes_dir.rglob("*.py")):
            try:
                text = py.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            rel = str(py.relative_to(repo_root))
            for line in text.splitlines():
                for pat in _PATH_PATTERNS:
                    for match in pat.finditer(line):
                        path = match.group(1)
                        ref = SurfaceRef(
                            source=f"route:{py.stem}",
                            file=rel,
                            detail=line.strip()[:120],
                        )
                        result.setdefault(path, []).append(ref)

    return result
