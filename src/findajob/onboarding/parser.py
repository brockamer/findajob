"""Onboarding emission parser (#148).

Scans a pasted blob for ``<<<FILE: name>>> ... <<<END FILE: name>>>`` blocks,
validates each filename against :data:`ALLOWED_FILENAMES`, and returns a
:class:`ParsedEmission` describing what was found, what's missing, and what
had an unrecognized filename.

Pure module: imports only ``re`` and ``dataclasses``. No filesystem access,
no FastAPI import.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

ALLOWED_FILENAMES: tuple[str, ...] = (
    "profile.md",
    "master_resume.md",
    "target_companies.md",
    "business_sector_employers_reference.md",
    "jsearch_queries.txt",
    "prefilter_rules.yaml",
    "in_domain_patterns.yaml",
    "display_name.txt",
    "timezone.txt",
    "ntfy_topic.txt",
)

# Recognized but not required. If present in an emission, the injector
# processes them; if absent, no error and no entry in ParsedEmission.missing.
OPTIONAL_FILENAMES: tuple[str, ...] = ("voice-samples.md",)

_KNOWN_FILENAMES: frozenset[str] = frozenset(ALLOWED_FILENAMES) | frozenset(OPTIONAL_FILENAMES)


_BLOCK_RE = re.compile(
    r"<<<FILE:\s*(?P<name>[^>\s]+)\s*>>>\r?\n(?P<body>.*?)\r?\n<<<END FILE:\s*(?P=name)\s*>>>",
    re.DOTALL,
)

_FENCE_OPEN_RE = re.compile(r"\A```[^\n]*\r?\n")
_FENCE_CLOSE_RE = re.compile(r"(?<=\n)```[ \t]*\r?\n?\Z")


@dataclass(frozen=True)
class ParsedEmission:
    """Result of parsing an emission blob.

    ``found`` maps allowlisted filenames to their raw body content.
    ``missing`` is the subset of :data:`ALLOWED_FILENAMES` that were not
    present. ``unknown`` holds any filenames that appeared in delimiters
    but are not on the allowlist.
    """

    found: dict[str, str] = field(default_factory=dict)
    missing: list[str] = field(default_factory=list)
    unknown: list[str] = field(default_factory=list)


def _strip_code_fences(body: str) -> str:
    body = _FENCE_OPEN_RE.sub("", body, count=1)
    body = _FENCE_CLOSE_RE.sub("", body, count=1)
    return body


def parse_emission(blob: str) -> ParsedEmission:
    """Parse ``blob`` and return a :class:`ParsedEmission`.

    Tolerant of blocks embedded in a larger chat transcript. Last occurrence
    of any given filename wins (the interview may emit a ``redo`` sequence).
    """
    found: dict[str, str] = {}
    unknown: list[str] = []
    for match in _BLOCK_RE.finditer(blob):
        name = match.group("name").strip()
        body = _strip_code_fences(match.group("body"))
        if name in _KNOWN_FILENAMES:
            found[name] = body
        else:
            if name not in unknown:
                unknown.append(name)
    missing = [n for n in ALLOWED_FILENAMES if n not in found]
    return ParsedEmission(found=found, missing=missing, unknown=unknown)
