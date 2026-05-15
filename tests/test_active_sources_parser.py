"""Tests for config/active_sources.txt parsing (#408 + #410.5 default expansion + #681 5-cell)."""

from __future__ import annotations

from pathlib import Path

from findajob.fetchers.adapters.registry import _DEFAULT_ACTIVE_SOURCES, _read_active_sources


def test_default_when_missing(tmp_path: Path) -> None:
    """Missing file + missing sentinel → expanded default that includes every registered adapter (#410.5).

    Pre-#410.5 the default was ['jobs-api14'] only — the orchestrator fired
    the four fetch_*_jobs wrappers (greenhouse/ashby/lever/gmail)
    unconditionally regardless of active_sources.txt. After #410.5 those
    adapters are registry-gated, so the default has to expand to preserve
    the effective pre-cutover surface.
    """
    result = _read_active_sources(
        tmp_path / "missing.txt",
        onboarding_complete_path=tmp_path / "no-sentinel",
    )
    assert result == _DEFAULT_ACTIVE_SOURCES
    # Spot-check: the four formerly-unconditional sources must be in the default.
    for required in ("greenhouse", "ashby", "lever", "gmail"):
        assert required in result, f"#410.5 regression: '{required}' missing from default"


def test_single_entry(tmp_path: Path) -> None:
    f = tmp_path / "active.txt"
    f.write_text("jsearch\n")
    assert _read_active_sources(f, onboarding_complete_path=tmp_path / "no-sentinel") == ["jsearch"]


def test_multiple_entries(tmp_path: Path) -> None:
    f = tmp_path / "active.txt"
    f.write_text("jobs-api14\njsearch\n")
    assert _read_active_sources(f, onboarding_complete_path=tmp_path / "no-sentinel") == ["jobs-api14", "jsearch"]


def test_comments_stripped(tmp_path: Path) -> None:
    f = tmp_path / "active.txt"
    f.write_text("# comment line\njobs-api14\n# another\njsearch\n")
    assert _read_active_sources(f, onboarding_complete_path=tmp_path / "no-sentinel") == ["jobs-api14", "jsearch"]


def test_blank_lines_stripped(tmp_path: Path) -> None:
    f = tmp_path / "active.txt"
    f.write_text("\njobs-api14\n\n\njsearch\n")
    assert _read_active_sources(f, onboarding_complete_path=tmp_path / "no-sentinel") == ["jobs-api14", "jsearch"]


def test_whitespace_trimmed(tmp_path: Path) -> None:
    f = tmp_path / "active.txt"
    f.write_text("  jobs-api14  \n\tjsearch\n")
    assert _read_active_sources(f, onboarding_complete_path=tmp_path / "no-sentinel") == ["jobs-api14", "jsearch"]


def test_empty_file_falls_back_to_default(tmp_path: Path) -> None:
    """Empty file (only comments / blank) is treated like missing — default applies."""
    f = tmp_path / "active.txt"
    f.write_text("# nothing\n\n# nothing\n")
    assert _read_active_sources(f, onboarding_complete_path=tmp_path / "no-sentinel") == _DEFAULT_ACTIVE_SOURCES


# #681 5-cell coverage — explicit assertions for each (.onboarding-complete, active_sources.txt) cell.


def test_cell_legacy_absent_no_sentinel_returns_default(tmp_path: Path) -> None:
    """Cell 1 (#681): file absent + sentinel absent → `_DEFAULT_ACTIVE_SOURCES`.

    Represents legacy stacks pre-dating `/settings/active-sources/` (#603) that
    have never written `active_sources.txt`. They get the 7-adapter default
    so triage continues to fire greenhouse/ashby/lever/gmail/etc.
    """
    result = _read_active_sources(
        tmp_path / "missing.txt",
        onboarding_complete_path=tmp_path / "no-sentinel",
    )
    assert result == _DEFAULT_ACTIVE_SOURCES


def test_cell_legacy_with_file_no_sentinel_parses_file(tmp_path: Path) -> None:
    """Cell 2 (#681): file present + sentinel absent → parse file.

    Legacy stack that touched `/settings/active-sources/` after #603 but
    before completing onboarding. File contents are honored.
    """
    f = tmp_path / "active.txt"
    f.write_text("greenhouse\nashby\n")
    result = _read_active_sources(f, onboarding_complete_path=tmp_path / "no-sentinel")
    assert result == ["greenhouse", "ashby"]


def test_cell_onboarded_none_returns_empty(tmp_path: Path) -> None:
    """Cell 3 (#681 — the bug fix): file absent + sentinel present → `[]`.

    Post-#680 onboarding writes no `active_sources.txt` when the user picks
    "none — Manual only". With the sentinel present, the absence is now
    interpreted as "user explicitly opted out" rather than "fall back to
    default". Net effect: zero adapters fire, zero RapidAPI calls.
    """
    sentinel = tmp_path / "onboarding-complete"
    sentinel.write_text("ok")
    result = _read_active_sources(tmp_path / "missing.txt", onboarding_complete_path=sentinel)
    assert result == []


def test_cell_onboarded_uncheck_to_default(tmp_path: Path) -> None:
    """Cell 4 (#681): file present but header-only + sentinel present → `_DEFAULT_ACTIVE_SOURCES`.

    Preserves the `/settings/active-sources/` "uncheck everything → revert
    to default" UX documented at `_write_active_sources`. The settings UI
    writes a header-comment-only file when the operator unchecks every
    adapter; that must continue to mean "revert to default", not "[]".
    """
    sentinel = tmp_path / "onboarding-complete"
    sentinel.write_text("ok")
    f = tmp_path / "active.txt"
    f.write_text("# Managed by /settings/active-sources/. Edit there to keep this file in sync.\n")
    result = _read_active_sources(f, onboarding_complete_path=sentinel)
    assert result == _DEFAULT_ACTIVE_SOURCES


def test_cell_onboarded_with_explicit_list(tmp_path: Path) -> None:
    """Cell 5 (#681): file with names + sentinel present → parse names.

    Operator explicitly selected adapters via `/settings/active-sources/`
    after completing onboarding. File contents are honored verbatim.
    """
    sentinel = tmp_path / "onboarding-complete"
    sentinel.write_text("ok")
    f = tmp_path / "active.txt"
    f.write_text("jobs-api14\njsearch\n")
    result = _read_active_sources(f, onboarding_complete_path=sentinel)
    assert result == ["jobs-api14", "jsearch"]
