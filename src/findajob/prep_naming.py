"""Filename construction for prep folders and outreach drafts.

Three pure functions — no I/O, no DB, no env reads. Build the per-file
filenames inside a ``{Company}_{Title}_{Date}_{HHMMSS}`` prep folder
following the convention in CLAUDE.md ("Output Folder Format").

Extracted from ``utils.py`` in M4.E2.I2 (#550). No logic changes.
"""

from __future__ import annotations

import re

_UNSAFE_FNAME_CHARS: re.Pattern[str] = re.compile(r"[^\w\s\-&.,]")


def abbrev_title(title: str, max_words: int = 3) -> str:
    """Return a folder-safe abbreviated title: first N significant words joined with underscores."""
    title = re.sub(r"\s*\(.*?\)", "", title)  # strip parentheticals
    title = re.sub(r"[^\w\s-]", "", title)  # remove punctuation
    words = [w for w in title.split() if w][:max_words]
    return "_".join(words) if words else "Job"


def safe_filename_part(s: str | None, max_len: int = 80) -> str:
    """Sanitize a string for use as a filename component.

    Keeps word characters, spaces, hyphens, ampersands, periods, and commas.
    Collapses whitespace. Truncates to max_len. Strips trailing punctuation
    that would look odd at a word boundary.
    """
    s = _UNSAFE_FNAME_CHARS.sub("", s or "")
    s = re.sub(r"\s+", " ", s).strip()
    if len(s) > max_len:
        s = s[:max_len].rstrip()
    return s.rstrip(" .-,")


def build_prep_filenames(company: str, title: str, timestamp_fn: str, file_prefix: str) -> dict[str, str]:
    """Return a dict of {logical_name: filename} for a prep folder.

    Naming convention:
      {Prefix} Resume - {Company} - {Title} - {YYYYMMDD-HHMMSS}.{md,docx}
      {Prefix} Cover - {Company} - {Title} - {YYYYMMDD-HHMMSS}.{md,docx}
      {Prefix} Briefing - {Company} - {Title} - {YYYYMMDD-HHMMSS}.{md,docx}
      {Prefix} Resume Changes - {Company} - {Title} - {YYYYMMDD-HHMMSS}.md
      {Prefix} Critique - {Company} - {Title} - {YYYYMMDD-HHMMSS}.md
      JD - {Company} - {Title}.txt
      Review Checklist - {Company} - {Title}.md

    Outreach filenames are generated separately by find_contacts.py.
    """
    co = safe_filename_part(company, 40)
    t = safe_filename_part(title, 60)
    # Core user-facing docs: full pattern with timestamp
    resume_base = f"{file_prefix} Resume - {co} - {t} - {timestamp_fn}"
    cover_base = f"{file_prefix} Cover - {co} - {t} - {timestamp_fn}"
    briefing_base = f"{file_prefix} Briefing - {co} - {t} - {timestamp_fn}"
    changes_base = f"{file_prefix} Resume Changes - {co} - {t} - {timestamp_fn}"
    critique_base = f"{file_prefix} Critique - {co} - {t} - {timestamp_fn}"
    # Internal reference docs: short form, no prefix or timestamp
    jd_base = f"JD - {co} - {t}"
    checklist_base = f"Review Checklist - {co} - {t}"
    return {
        "resume_md": f"{resume_base}.md",
        "resume_docx": f"{resume_base}.docx",
        "cover_md": f"{cover_base}.md",
        "cover_docx": f"{cover_base}.docx",
        "briefing_md": f"{briefing_base}.md",
        "briefing_docx": f"{briefing_base}.docx",
        "changes_md": f"{changes_base}.md",
        "critique_md": f"{critique_base}.md",
        "jd_txt": f"{jd_base}.txt",
        "checklist_md": f"{checklist_base}.md",
    }


def build_outreach_filename(contact_name: str, company: str, timestamp_fn: str, file_prefix: str) -> str:
    """Return filename for an outreach draft.

    Pattern: {Prefix} Outreach to {Contact Name} - {Company} - {YYYYMMDD-HHMMSS}.txt
    """
    co = safe_filename_part(company, 40)
    ct = safe_filename_part(contact_name, 40)
    return f"{file_prefix} Outreach to {ct} - {co} - {timestamp_fn}.txt"
