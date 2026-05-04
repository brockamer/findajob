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
