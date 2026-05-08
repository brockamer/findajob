"""Tests for read_file_prefix() — the materials-filename prefix derivation.

Resolution order verified explicitly: display_name.txt > File Prefix line >
Name line > "Candidate" fallback. The display_name.txt source is the #328
structured field; legacy paths preserved for backward compatibility with
profiles that predate #328.
"""

from __future__ import annotations

from pathlib import Path

from findajob.profile import read_file_prefix


def _write_profile(tmp_path: Path, body: str) -> Path:
    """Write profile.md under tmp_path/candidate_context/ and return its path."""
    cc = tmp_path / "candidate_context"
    cc.mkdir(parents=True, exist_ok=True)
    profile = cc / "profile.md"
    profile.write_text(body, encoding="utf-8")
    return profile


def _write_display_name(tmp_path: Path, body: str) -> None:
    """Write display_name.txt as a sibling of profile.md."""
    cc = tmp_path / "candidate_context"
    cc.mkdir(parents=True, exist_ok=True)
    (cc / "display_name.txt").write_text(body, encoding="utf-8")


# ── 1. display_name.txt is the structured source — wins over everything ────


def test_display_name_takes_precedence_over_file_prefix_line(tmp_path: Path) -> None:
    """If display_name.txt and a File Prefix line both exist, display_name wins."""
    profile = _write_profile(
        tmp_path,
        "# Profile\n\nName: Old Narrative\nFile Prefix: LegacyPrefix\n",
    )
    _write_display_name(tmp_path, "Avery Westbrook")
    assert read_file_prefix(str(profile)) == "Westbrook"


def test_display_name_takes_precedence_over_name_line(tmp_path: Path) -> None:
    """display_name.txt wins over Name line even when no File Prefix line is present."""
    profile = _write_profile(tmp_path, "# Profile\n\nName: Different Narrative\n")
    _write_display_name(tmp_path, "Jordan Rivers")
    assert read_file_prefix(str(profile)) == "Rivers"


def test_display_name_single_word_returned_as_is(tmp_path: Path) -> None:
    """A single-word display name (e.g. 'Cher') becomes the prefix verbatim."""
    profile = _write_profile(tmp_path, "# Profile\n")
    _write_display_name(tmp_path, "Cher")
    assert read_file_prefix(str(profile)) == "Cher"


def test_display_name_strips_whitespace_and_uses_last_word(tmp_path: Path) -> None:
    """Trailing newlines and middle names are handled."""
    profile = _write_profile(tmp_path, "# Profile\n")
    _write_display_name(tmp_path, "  Sam Joseph Riverstone  \n")
    assert read_file_prefix(str(profile)) == "Riverstone"


def test_empty_display_name_falls_through_to_legacy(tmp_path: Path) -> None:
    """An empty display_name.txt does not silently produce '' — fall through to profile.md."""
    profile = _write_profile(tmp_path, "# Profile\n\nName: Operator\n")
    _write_display_name(tmp_path, "  \n  \n")
    assert read_file_prefix(str(profile)) == "Operator"


def test_whitespace_only_display_name_falls_through(tmp_path: Path) -> None:
    profile = _write_profile(tmp_path, "# Profile\n\nFile Prefix: LegacyVal\n")
    _write_display_name(tmp_path, "\t\n\n")
    assert read_file_prefix(str(profile)) == "LegacyVal"


# ── 2. Legacy paths preserved for profiles predating #328 ─────────────────


def test_no_display_name_uses_file_prefix_line(tmp_path: Path) -> None:
    """Pre-#328 profile with a File Prefix line continues to work."""
    profile = _write_profile(tmp_path, "# Profile\n\nFile Prefix: AlphaCo\n")
    assert read_file_prefix(str(profile)) == "AlphaCo"


def test_no_display_name_no_file_prefix_uses_name_last_word(tmp_path: Path) -> None:
    """Pre-#328 profile with only a Name line falls through to the last-word path."""
    profile = _write_profile(tmp_path, "# Profile\n\nName: Sample Operator\n")
    assert read_file_prefix(str(profile)) == "Operator"


def test_no_inputs_returns_candidate(tmp_path: Path) -> None:
    """Nothing matches → 'Candidate' fallback (so the pipeline doesn't crash)."""
    profile = _write_profile(tmp_path, "# Profile\nNo identifying field here.\n")
    assert read_file_prefix(str(profile)) == "Candidate"


def test_missing_profile_returns_candidate(tmp_path: Path) -> None:
    """If profile.md doesn't exist and display_name.txt doesn't exist, return 'Candidate'."""
    nonexistent = tmp_path / "candidate_context" / "profile.md"
    # Don't create candidate_context/ at all
    assert read_file_prefix(str(nonexistent)) == "Candidate"


def test_display_name_present_but_profile_missing(tmp_path: Path) -> None:
    """display_name.txt is consulted even if profile.md doesn't exist."""
    nonexistent = tmp_path / "candidate_context" / "profile.md"
    _write_display_name(tmp_path, "Sole Display")
    assert read_file_prefix(str(nonexistent)) == "Display"


# ── 3. Path resolution: display_name.txt is sibling of profile.md ─────────


def test_display_name_resolved_relative_to_profile_path(tmp_path: Path) -> None:
    """The display_name.txt path is derived from the profile_path's directory.

    Important: tests pass a tmp profile_path; the function MUST look for
    display_name.txt in that same directory, not under BASE.
    """
    other_dir = tmp_path / "alternate" / "context"
    other_dir.mkdir(parents=True)
    profile = other_dir / "profile.md"
    profile.write_text("# Profile\n", encoding="utf-8")
    (other_dir / "display_name.txt").write_text("Alt Path Person", encoding="utf-8")
    assert read_file_prefix(str(profile)) == "Person"
