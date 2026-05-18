"""Walk source tree for code consuming user-input files (#572 Phase 1).

Reads every .py under src/findajob/ and scripts/, extracts string literals
matching user-input path patterns, returns dict[path -> [CallSite]].

The walker is deterministic: no LLM, no network. Detection is grep-style
regex over the source text (cheap and good enough for the tracked path
patterns we care about: config/*, candidate_context/*, data/*).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

# Path literals we treat as "user-input file" consumption. The leading
# quote is optional so that paths mentioned in docstrings (e.g. triple-quoted
# strings, inline text) are also captured. Spec bias: false positives are
# operator-correctable via audit_exclusions.yaml; false negatives are not.
_PATH_PATTERNS = (
    re.compile(r'["\'"]?(config/[^"\'>\s]+\.(?:yaml|yml|txt|md|csv))'),
    re.compile(r'["\'"]?(candidate_context/[^"\'>\s]+\.(?:md|yaml|yml|csv))'),
    re.compile(r'["\'"]?(data/[^"\'>\s]+\.(?:db|env|sqlite))'),
)


@dataclass(frozen=True)
class CallSite:
    """A reference to a user-input file path in source code."""

    file: str  # repo-relative path of the consumer
    line: int  # 1-indexed line number of the reference
    snippet: str  # the matched source line, stripped


def walk_surface_map(*, repo_root: Path) -> dict[str, list[CallSite]]:
    """Walk every .py under src/findajob/ and scripts/. Return path -> consumers."""
    result: dict[str, list[CallSite]] = {}
    walk_roots = [repo_root / "src" / "findajob", repo_root / "scripts"]
    for root in walk_roots:
        if not root.exists():
            continue
        for py in sorted(root.rglob("*.py")):
            try:
                text = py.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            rel_consumer = str(py.relative_to(repo_root))
            for lineno, line in enumerate(text.splitlines(), start=1):
                for pat in _PATH_PATTERNS:
                    for match in pat.finditer(line):
                        path = match.group(1)
                        site = CallSite(file=rel_consumer, line=lineno, snippet=line.strip())
                        result.setdefault(path, []).append(site)
    return result
