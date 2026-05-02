"""Tests for config/active_sources.txt parsing (#408)."""

from __future__ import annotations

from pathlib import Path

from findajob.fetchers.adapters.registry import _read_active_sources


def test_default_when_missing(tmp_path: Path) -> None:
    """Missing file → backwards-compat default ['jobs-api14']."""
    assert _read_active_sources(tmp_path / "missing.txt") == ["jobs-api14"]


def test_single_entry(tmp_path: Path) -> None:
    f = tmp_path / "active.txt"
    f.write_text("jsearch\n")
    assert _read_active_sources(f) == ["jsearch"]


def test_multiple_entries(tmp_path: Path) -> None:
    f = tmp_path / "active.txt"
    f.write_text("jobs-api14\njsearch\n")
    assert _read_active_sources(f) == ["jobs-api14", "jsearch"]


def test_comments_stripped(tmp_path: Path) -> None:
    f = tmp_path / "active.txt"
    f.write_text("# comment line\njobs-api14\n# another\njsearch\n")
    assert _read_active_sources(f) == ["jobs-api14", "jsearch"]


def test_blank_lines_stripped(tmp_path: Path) -> None:
    f = tmp_path / "active.txt"
    f.write_text("\njobs-api14\n\n\njsearch\n")
    assert _read_active_sources(f) == ["jobs-api14", "jsearch"]


def test_whitespace_trimmed(tmp_path: Path) -> None:
    f = tmp_path / "active.txt"
    f.write_text("  jobs-api14  \n\tjsearch\n")
    assert _read_active_sources(f) == ["jobs-api14", "jsearch"]


def test_empty_file_falls_back_to_default(tmp_path: Path) -> None:
    """Empty file (only comments / blank) is treated like missing — default applies."""
    f = tmp_path / "active.txt"
    f.write_text("# nothing\n\n# nothing\n")
    assert _read_active_sources(f) == ["jobs-api14"]
