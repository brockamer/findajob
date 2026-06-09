"""Tests for the canonical briefing resolver (#1031).

All four briefing readers — podcast, study/flashcards, interview-prep, and the
Phase-B resume tailorer — route through ``find_briefing()`` / ``read_briefing()``
so the "where is the briefing" rule lives in exactly one place. This mirrors the
``company_match()`` single-source discipline: before this, three readers used a
case-sensitive ``re.search(r"Briefing.*\\.md$")`` that silently missed the bare
``briefing.md`` that speculative jobs write, while a fourth (canonical) reader
globbed both names but without a newest-wins sort.
"""

import os
from pathlib import Path

from findajob.prep.briefing import find_briefing, read_briefing


def _write(p: Path, text: str, mtime: float | None = None) -> Path:
    p.write_text(text, encoding="utf-8")
    if mtime is not None:
        os.utime(p, (mtime, mtime))
    return p


def test_find_briefing_resolves_bare_lowercase_only(tmp_path: Path) -> None:
    # Speculative-shaped folder: only the bare briefing.md the case-sensitive
    # readers used to miss. This is the regression the issue is filed against.
    bare = _write(tmp_path / "briefing.md", "spec briefing")
    assert find_briefing(tmp_path) == bare


def test_find_briefing_prefers_title_cased_over_bare(tmp_path: Path) -> None:
    # A fully-prepped folder has both; the Title-Cased prep briefing wins so the
    # readers keep returning the merged briefing+fit doc, not the raw spec copy.
    title = _write(tmp_path / "Candidate Briefing - Acme - 20260101-000000.md", "real")
    _write(tmp_path / "briefing.md", "spec copy")
    assert find_briefing(tmp_path) == title


def test_find_briefing_picks_newest_title_cased_draft(tmp_path: Path) -> None:
    # Defensive newest-wins contract. No current code path accumulates more than
    # one Title-Cased briefing per folder (Phase A mints a fresh outdir per run),
    # so this guards the invariant rather than fixing a live case — and makes the
    # helper deterministic where the canonical Phase-B reader's unsorted [0] was not.
    _write(tmp_path / "Candidate Briefing - Acme - 20260101-000000.md", "old", mtime=100)
    newest = _write(tmp_path / "Candidate Briefing - Acme - 20260102-000000.md", "new", mtime=200)
    assert find_briefing(tmp_path) == newest


def test_find_briefing_none_when_no_briefing(tmp_path: Path) -> None:
    _write(tmp_path / "Candidate Resume - Acme.md", "resume")
    assert find_briefing(tmp_path) is None


def test_find_briefing_none_for_missing_folder(tmp_path: Path) -> None:
    assert find_briefing(tmp_path / "does-not-exist") is None


def test_read_briefing_returns_bare_text(tmp_path: Path) -> None:
    _write(tmp_path / "briefing.md", "speculative briefing body")
    assert read_briefing(tmp_path) == "speculative briefing body"


def test_read_briefing_returns_newest_title_cased_text(tmp_path: Path) -> None:
    _write(tmp_path / "Candidate Briefing - Acme - 20260101-000000.md", "old body", mtime=100)
    _write(tmp_path / "Candidate Briefing - Acme - 20260102-000000.md", "new body", mtime=200)
    assert read_briefing(tmp_path) == "new body"


def test_read_briefing_empty_when_absent(tmp_path: Path) -> None:
    assert read_briefing(tmp_path) == ""
