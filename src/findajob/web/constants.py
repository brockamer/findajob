"""Shared constants for the web app and call sites that need to mirror it."""

import os

BUILD_SHA: str = os.environ.get("FINDAJOB_BUILD_SHA", "main")
"""Git SHA of the deployed image, baked in at build time.

Defaults to ``"main"`` when running outside the container (dev VM, CI). The
disclosure banner uses this to link audit URLs to the exact commit running,
not the moving ``main`` branch — so users can verify what code is actually
processing their mail.
"""

BUILD_SHA_SHORT: str = BUILD_SHA[:7] if BUILD_SHA != "main" else "main"


def github_blob_url(path: str) -> str:
    """Build a GitHub URL pinned to :data:`BUILD_SHA` for the given repo path."""
    return f"https://github.com/brockamer/findajob/blob/{BUILD_SHA}/{path}"


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
