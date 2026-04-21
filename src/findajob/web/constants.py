"""Shared constants for the web app and call sites that need to mirror it."""

FOLDER_STAGES: tuple[str, ...] = (
    "materials_drafted",
    "prep_in_progress",
    "applied",
    "interview",
    "offer",
    "waitlisted",
    "rejected",
    "not_selected",
)
"""Stages for which a job has a prep folder on disk.

Used by:
  - scripts/sync_sheet.py::materials_company_cell — decides hyperlink vs plain text
  - src/findajob/web/templates/_job_row.html — same decision, rendered server-side

Keep these call sites in lockstep by importing from here, never hard-coding.
"""
