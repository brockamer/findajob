"""FOLDER_STAGES is the single source of truth for which stages have prep folders."""

from findajob.web.constants import FOLDER_STAGES


def test_folder_stages_is_frozen_tuple() -> None:
    assert isinstance(FOLDER_STAGES, tuple)
    expected = {
        "materials_drafted",
        "prep_in_progress",
        "applied",
        "interview",
        "offer",
        "waitlisted",
        "rejected",
        "not_selected",
    }
    assert set(FOLDER_STAGES) == expected


def test_sync_sheet_uses_shared_constant() -> None:
    """scripts/sync_sheet.py imports FOLDER_STAGES from findajob.web.constants
    rather than hard-coding its own tuple, so the two call sites stay in lockstep.
    (Source-level check — sync_sheet has side-effects at import time that make
    a runtime import impractical in CI.)
    """
    from pathlib import Path

    src = Path(__file__).parent.parent / "scripts" / "sync_sheet.py"
    text = src.read_text()
    assert "from findajob.web.constants import FOLDER_STAGES" in text, (
        "sync_sheet.py must import FOLDER_STAGES from findajob.web.constants"
    )
