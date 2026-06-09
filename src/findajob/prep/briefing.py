"""Canonical briefing resolver for a prep folder.

Single source of truth for "where is the briefing in this prep folder", routed
through by every briefing reader (podcast, study/flashcards, interview-prep, and
the Phase-B resume tailorer). Consolidating these mirrors the ``company_match()``
discipline: before this helper, three readers used a case-sensitive
``re.search(r"Briefing.*\\.md$")`` that could not see the bare ``briefing.md``
that speculative jobs copy in, and the fourth (canonical) reader globbed both
names but selected ``[0]`` with no newest-wins ordering.

Resolution order:
1. The prep-generated Title-Cased ``{Prefix} Briefing - ....md`` — newest by
   mtime when several drafts exist (e.g. a regenerated job).
2. Fall back to the bare ``briefing.md`` that ``speculative.storage`` writes and
   prep copies into the folder.
3. ``None`` / ``""`` when neither is present.
"""

from __future__ import annotations

import os
from pathlib import Path


def find_briefing(prep_folder: str | os.PathLike[str]) -> Path | None:
    """Return the path of the briefing markdown in ``prep_folder``, or None.

    Prefers the newest Title-Cased ``*Briefing*.md``; falls back to the bare
    ``briefing.md``.
    """
    folder = Path(prep_folder)
    if not folder.is_dir():
        return None
    title_cased = sorted(
        (p for p in folder.glob("*Briefing*.md") if p.is_file()),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if title_cased:
        return title_cased[0]
    bare = folder / "briefing.md"
    if bare.is_file():
        return bare
    return None


def read_briefing(prep_folder: str | os.PathLike[str]) -> str:
    """Return the text of the resolved briefing, or ``""`` when none is found.

    The empty-string sentinel matches the ``if not briefing`` checks every reader
    already guards with.
    """
    path = find_briefing(prep_folder)
    if path is None:
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""
