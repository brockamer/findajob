"""Candidate profile / voice-sample readers.

Reads from ``candidate_context/profile.md`` (and its sibling
``display_name.txt``, plus ``voice_samples/``) to surface fields that the
LLM-prompted roles need: candidate name, file prefix for generated
artifacts, and accumulated voice samples for tone calibration.

Profile parsing tolerates several markdown shapes (``Name: X``,
``**Name:** X``, etc.) because the onboarding LLM doesn't always emit a
canonical structure. Defaults degrade gracefully — a missing profile
yields ``"Candidate"`` rather than crashing the pipeline.

Extracted from ``utils.py`` in M4.E2.I2 (#550). No logic changes.
"""

from __future__ import annotations

import os
import re

from findajob.paths import BASE

_PROFILE_NAME_RE: re.Pattern[str] = re.compile(
    r"^\s*\*{0,2}\s*Name:\s*\*{0,2}\s*(.+?)\s*\*{0,2}\s*$",
    re.IGNORECASE,
)
_PROFILE_FILE_PREFIX_RE: re.Pattern[str] = re.compile(
    r"^\s*\*{0,2}\s*File\s*Prefix:\s*\*{0,2}\s*(.+?)\s*\*{0,2}\s*$",
    re.IGNORECASE,
)


def _clean_profile_field(raw: str | None) -> str:
    """Strip surrounding whitespace, asterisks, and backticks from a profile field value."""
    return (raw or "").strip().strip("*").strip("`").strip()


def read_candidate_name(profile_path: str | None = None) -> str:
    """Read the candidate's full name from profile.md.

    Prefers an explicit `Name: Xxx Yyy` line (from the Identity section).
    Tolerates `**Name:** Xxx Yyy` (bold markdown) and similar variants.
    Returns 'Candidate' if nothing matches.
    """
    if profile_path is None:
        profile_path = f"{BASE}/candidate_context/profile.md"
    try:
        with open(profile_path) as f:
            for line in f:
                m = _PROFILE_NAME_RE.match(line)
                if m:
                    value = _clean_profile_field(m.group(1))
                    if value:
                        return value
    except (FileNotFoundError, OSError):
        pass
    return "Candidate"


def read_file_prefix(profile_path: str | None = None) -> str:
    """Read the prefix used in generated filenames.

    Resolution order:
      1. ``display_name.txt`` sibling of profile.md (single line, written by
         the #328 onboarding injector). Last word of the line — deterministic,
         matches the AbbrevName-Last shape used historically.
      2. Explicit ``File Prefix:`` line in profile.md (legacy).
      3. ``Name:`` line in profile.md, last word (legacy fallback).
      4. ``Candidate`` if nothing else matches.

    The display_name.txt path is the structured source — preferred over
    profile.md narrative parsing, which can break when the LLM emits the
    "Name" / "Identity" section in a non-standard shape.
    """
    if profile_path is None:
        profile_path = f"{BASE}/candidate_context/profile.md"

    # 1. Structured source from #328 onboarding — sibling of profile.md
    display_name_path = os.path.join(os.path.dirname(profile_path), "display_name.txt")
    try:
        with open(display_name_path) as f:
            display_name = f.read().strip()
        if display_name:
            parts = display_name.split()
            return parts[-1] if parts else display_name
    except (FileNotFoundError, OSError):
        pass

    # 2. Legacy: explicit File Prefix line in profile.md
    try:
        with open(profile_path) as f:
            for line in f:
                m = _PROFILE_FILE_PREFIX_RE.match(line)
                if m:
                    value = _clean_profile_field(m.group(1))
                    if value:
                        return value
    except (FileNotFoundError, OSError):
        pass

    # 3. Legacy: last word of Name line in profile.md
    name = read_candidate_name(profile_path)
    parts = name.strip().split()
    return parts[-1] if parts else "Candidate"


def load_voice_samples(samples_dir: str | None = None, max_chars: int = 32000) -> str:
    """Concatenate candidate voice samples for style calibration.

    Reads .md and .txt files from candidate_context/voice_samples/ (excluding
    README*), joins with double-newline separators, caps at max_chars. Returns
    empty string when the directory is missing, contains no samples, or only
    contains README files.
    """
    if samples_dir is None:
        samples_dir = f"{BASE}/candidate_context/voice_samples"
    if not os.path.isdir(samples_dir):
        return ""

    parts: list[str] = []
    for name in sorted(os.listdir(samples_dir)):
        if name.lower().startswith("readme"):
            continue
        if not (name.endswith(".md") or name.endswith(".txt")):
            continue
        path = os.path.join(samples_dir, name)
        try:
            with open(path) as f:
                content = f.read().strip()
        except OSError:
            continue
        if content:
            parts.append(content)

    if not parts:
        return ""

    text = "\n\n".join(parts)
    if len(text) > max_chars:
        text = text[:max_chars]
    return text
