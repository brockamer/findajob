"""Tests for findajob.speculative.storage."""

from __future__ import annotations

from findajob.speculative.storage import speculative_folder_name, write_briefing


def test_folder_name_includes_company_date_time():
    name = speculative_folder_name("PSIQuantum", when_iso="2026-04-28T14:30:00")
    assert name == "PSIQuantum_SPECULATIVE_2026-04-28_143000"


def test_folder_name_strips_company_unsafe_chars():
    # Slashes, colons, etc. must not appear in folder names.
    name = speculative_folder_name("ai/&:Co", when_iso="2026-04-28T09:00:00")
    assert "/" not in name
    assert ":" not in name
    assert name.startswith("ai_Co_SPECULATIVE_") or name.startswith("ai__Co_SPECULATIVE_")


def test_write_briefing_creates_folder_and_md(tmp_path):
    folder = write_briefing(
        base_dir=tmp_path,
        company="PSIQuantum",
        briefing_md="# briefing\n\nbody\n",
        when_iso="2026-04-28T14:30:00",
    )
    assert folder.exists()
    assert folder.is_dir()
    md = folder / "briefing.md"
    assert md.read_text() == "# briefing\n\nbody\n"
    assert folder.name == "PSIQuantum_SPECULATIVE_2026-04-28_143000"
