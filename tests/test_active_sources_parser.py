"""Tests for config/active_sources.txt parsing (#408 + #410.5 default expansion)."""

from __future__ import annotations

from pathlib import Path

from findajob.fetchers.adapters.registry import _DEFAULT_ACTIVE_SOURCES, _read_active_sources


def test_default_when_missing(tmp_path: Path) -> None:
    """Missing file → expanded default that includes every registered adapter (#410.5).

    Pre-#410.5 the default was ['jobs-api14'] only — the orchestrator fired
    the four fetch_*_jobs wrappers (greenhouse/ashby/lever/gmail)
    unconditionally regardless of active_sources.txt. After #410.5 those
    adapters are registry-gated, so the default has to expand to preserve
    the effective pre-cutover surface.
    """
    result = _read_active_sources(tmp_path / "missing.txt")
    assert result == _DEFAULT_ACTIVE_SOURCES
    # Spot-check: the four formerly-unconditional sources must be in the default.
    for required in ("greenhouse", "ashby", "lever", "gmail"):
        assert required in result, f"#410.5 regression: '{required}' missing from default"


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
    assert _read_active_sources(f) == _DEFAULT_ACTIVE_SOURCES
