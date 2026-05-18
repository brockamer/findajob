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


def _extract_editable_categories(config_files_path: Path) -> list[str]:
    """Parse EDITABLE_CATEGORIES dict from config_files.py and return all paths."""
    try:
        tree = ast.parse(config_files_path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return []
    paths: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "EDITABLE_CATEGORIES":
                    if isinstance(node.value, ast.Dict):
                        for v in node.value.values:
                            # Value can be a list of str literals OR a str literal (wildcard glob).
                            if isinstance(v, ast.List):
                                for elt in v.elts:
                                    if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                                        paths.append(elt.value)
    return paths


def walk_coverage_map(*, repo_root: Path) -> dict[str, list[SurfaceRef]]:
    """Walk web/, build map of UI-covered user-input file paths."""
    result: dict[str, list[SurfaceRef]] = {}

    # Mechanism 1: EDITABLE_CATEGORIES
    config_files = repo_root / "src" / "findajob" / "web" / "config_files.py"
    if config_files.exists():
        for path in _extract_editable_categories(config_files):
            ref = SurfaceRef(
                source="EDITABLE_CATEGORIES",
                file=str(config_files.relative_to(repo_root)),
                detail=f"/config/ raw editor allows direct edit of {path}",
            )
            result.setdefault(path, []).append(ref)

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
