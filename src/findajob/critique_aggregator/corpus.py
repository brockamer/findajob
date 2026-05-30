"""Corpus scan + flag assembly for the critique aggregator (#265).

Locates ``* Critique - *.md`` artifacts across the live and archived company
buckets, de-duplicates apply-time ``.applied-*`` snapshots, derives the company
from the filename, loads master/profile source lines, and assembles the flat
``FlaggedItem`` list the clusterer consumes.
"""

from __future__ import annotations

from pathlib import Path

from findajob.critique_aggregator.anchor import SourceLine, anchor_quote
from findajob.critique_aggregator.cluster import FlaggedItem
from findajob.critique_aggregator.parse import (
    extract_quotes,
    parse_critique,
    sentence_for_quote,
)

# Matches the human-readable artifact name (``<Name> Critique - <Co> - ...md``).
# Globbing ``.md`` excludes the ``.docx`` renders for free.
_CRITIQUE_GLOB = "* Critique - *.md"
_SECTIONS = ("generic", "weak", "missing")


def iter_critique_files(companies_root: Path) -> list[Path]:
    """All critique markdown files under ``companies_root`` (recursively),
    excluding apply-time ``.applied-*`` snapshot duplicates."""
    return [f for f in sorted(companies_root.rglob(_CRITIQUE_GLOB)) if ".applied-" not in f.name]


def company_from_critique_path(path: Path) -> str:
    """Derive the company from a critique filename.

    Company is the field after ``" Critique - "`` and before the next ``" - "``;
    parsed at that stable delimiter rather than the ambiguous folder name. The
    role itself may contain ``" - "`` — only the first segment is the company.
    """
    after = path.name.split(" Critique - ", 1)
    if len(after) < 2:
        return ""
    return after[1].split(" - ", 1)[0].strip()


def load_source_lines(path: Path, file_label: str) -> list[SourceLine]:
    """Load non-blank lines of a source file as anchor targets (1-based)."""
    out: list[SourceLine] = []
    for line_no, raw in enumerate(path.read_text().splitlines(), start=1):
        text = raw.strip()
        if text:
            out.append(SourceLine(file=file_label, line_no=line_no, text=text))
    return out


def build_flagged_items(critique_files: list[Path], source_lines: list[SourceLine]) -> list[FlaggedItem]:
    """Parse every critique into per-quote flags, anchoring each to a source
    line where one matches."""
    items: list[FlaggedItem] = []
    for path in critique_files:
        company = company_from_critique_path(path)
        sections = parse_critique(path.read_text())
        for section in _SECTIONS:
            section_text = getattr(sections, section)
            for quote in extract_quotes(section_text):
                items.append(
                    FlaggedItem(
                        company=company,
                        section=section,
                        quote=quote,
                        recruiter_sentence=sentence_for_quote(section_text, quote),
                        anchor=anchor_quote(quote, source_lines),
                    )
                )
    return items
