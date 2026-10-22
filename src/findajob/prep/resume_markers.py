"""Master-resume sections marked "not for submitted resumes" (#1098).

A candidate can keep material in ``master_resume.md`` that the cover letter,
outreach and interview prep may use but a submitted resume must not — peer
quotes, for example. A section is marked when its heading line, or the first
non-blank line under its heading, contains "not for submitted resumes". The
section runs to the next heading of the same or higher level, so its
sub-sections are marked too.

``split_marked_sections`` removes marked sections before the master resume
reaches ``resume_tailor``. ``find_marked_text`` is the post-check: the
briefing can still carry marked text to the tailor, so a tailored resume is
searched for copied word runs. A paraphrase is not detected.
"""

from __future__ import annotations

import re
import unicodedata

MARKER_RE: re.Pattern[str] = re.compile(r"not\s+for\s+submitted\s+resumes", re.IGNORECASE)

# A marked line is flagged when this many consecutive words from it appear in
# the resume and nowhere in the kept master. Short attribution lines can never
# match, and a fact the master states elsewhere does not count as a copy.
MIN_RUN_WORDS = 6

_HEADING_RE = re.compile(r"^(#{1,6})\s")
_FENCE_RE = re.compile(r"^\s*```")
_WORD_RE = re.compile(r"[a-z0-9]+(?:'[a-z0-9]+)*")
_CHAR_MAP = str.maketrans(
    {
        "‘": "'",
        "’": "'",
        "“": '"',
        "”": '"',
        "–": "-",
        "—": "-",
        "−": "-",
        "*": None,
        "_": None,
        "`": None,
    }
)


def normalize_for_match(text: str) -> str:
    """Casefold and flatten text so formatting differences do not hide a match:
    curly quotes and dashes become plain, Markdown emphasis is dropped, and
    every whitespace run (newlines included) becomes one space."""
    text = unicodedata.normalize("NFKC", text).translate(_CHAR_MAP).casefold()
    return " ".join(text.split())


def _heading_levels(lines: list[str]) -> list[int]:
    """Heading level per line, 0 for non-headings. Lines inside a ``` fence
    are never headings."""
    levels: list[int] = []
    in_fence = False
    for line in lines:
        bare = line.rstrip("\r\n")
        if _FENCE_RE.match(bare):
            in_fence = not in_fence
            levels.append(0)
            continue
        m = None if in_fence else _HEADING_RE.match(bare)
        levels.append(len(m.group(1)) if m else 0)
    return levels


def _has_marker(line: str) -> bool:
    return bool(MARKER_RE.search(line.replace("*", "").replace("_", "")))


def split_marked_sections(master_text: str) -> tuple[str, str]:
    """Return ``(kept, marked)``. ``marked`` holds every marked section in
    document order; ``kept`` holds the rest with line endings unchanged. With
    no marked section, ``kept`` is ``master_text`` itself and ``marked`` is
    empty, so prompts built from it are byte-identical to before."""
    lines = master_text.splitlines(keepends=True)
    levels = _heading_levels(lines)
    is_marked = [False] * len(lines)

    for i, level in enumerate(levels):
        if not level:
            continue
        first_body = next((j for j in range(i + 1, len(lines)) if lines[j].strip()), None)
        marker_lines = [lines[i]]
        if first_body is not None and not levels[first_body]:
            marker_lines.append(lines[first_body])
        if not any(_has_marker(line) for line in marker_lines):
            continue
        end = next((j for j in range(i + 1, len(lines)) if levels[j] and levels[j] <= level), len(lines))
        for j in range(i, end):
            is_marked[j] = True

    if not any(is_marked):
        return master_text, ""
    kept = "".join(line for line, m in zip(lines, is_marked, strict=True) if not m)
    if not kept.strip():
        # A marker under the title (a legend line, say) would mark the whole
        # file. Honour nothing instead: the marker stays in the kept text, so
        # Phase B logs it as unrecognised and the tailor still gets a resume.
        return master_text, ""
    marked = "".join(line for line, m in zip(lines, is_marked, strict=True) if m)
    return kept, marked


def _word_runs(text: str) -> set[tuple[str, ...]]:
    words = _WORD_RE.findall(normalize_for_match(text))
    return {tuple(words[i : i + MIN_RUN_WORDS]) for i in range(len(words) - MIN_RUN_WORDS + 1)}


def find_marked_text(resume_text: str, marked_text: str, kept_text: str) -> list[str]:
    """Return the marked lines whose text reached the resume: lines sharing a
    run of ``MIN_RUN_WORDS`` consecutive words with ``resume_text`` that the
    kept master does not also contain. Stripped, in document order, no
    duplicates. Headings and marker lines are never returned."""
    if not marked_text:
        return []
    resume_runs = _word_runs(resume_text)
    kept_runs = _word_runs(kept_text)
    lines = marked_text.splitlines()
    levels = _heading_levels(lines)
    found: list[str] = []
    for line, level in zip(lines, levels, strict=True):
        stripped = line.strip()
        if not stripped or level or _has_marker(line) or stripped in found:
            continue
        if any(run in resume_runs and run not in kept_runs for run in _word_runs(line)):
            found.append(stripped)
    return found
