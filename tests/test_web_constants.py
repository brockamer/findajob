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
    import scripts.sync_sheet as s  # noqa: WPS433

    assert set(s._FOLDER_STAGES) == set(FOLDER_STAGES)
