"""Filesystem layout for speculative submissions:

    {BASE}/companies/{Company}_SPECULATIVE_{YYYY-MM-DD}_{HHMMSS}/briefing.md

The folder name is referenced by `speculative_requests.briefing_folder` so
the approver and prep paths can locate the briefing on read.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path


def speculative_folder_name(company: str, when_iso: str | None = None) -> str:
    """Return the canonical folder name (no parent path) for a speculative briefing."""
    safe_company = re.sub(r"[^A-Za-z0-9_]+", "_", company).strip("_") or "Unknown"
    when = datetime.fromisoformat(when_iso) if when_iso else datetime.now()
    stamp = when.strftime("%Y-%m-%d_%H%M%S")
    return f"{safe_company}_SPECULATIVE_{stamp}"


def write_briefing(
    base_dir: Path,
    company: str,
    briefing_md: str,
    when_iso: str | None = None,
) -> Path:
    """Create the speculative folder under base_dir and write briefing.md."""
    folder = Path(base_dir) / speculative_folder_name(company, when_iso=when_iso)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "briefing.md").write_text(briefing_md)
    return folder
