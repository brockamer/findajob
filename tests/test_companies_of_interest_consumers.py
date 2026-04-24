"""Regression guard: notify must consume companies_of_interest via the
config_loader, not via the now-deleted _is_tier1 / TIER1 from the
prefilter module. (sync_sheet no longer needs company-of-interest
filtering after Sheet1 retirement in #136.)
"""

from __future__ import annotations

import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]


def _read(relative: str) -> str:
    return (REPO_ROOT / relative).read_text()


def test_sync_sheet_does_not_use_deleted_tier1_pattern():
    src = _read("scripts/sync_sheet.py")
    assert "_is_tier1" not in src
    assert "from findajob.scorer_prefilter import TIER1" not in src


def test_notify_uses_is_company_of_interest():
    src = _read("scripts/notify.py")
    assert "from findajob.config_loader import is_company_of_interest" in src
    assert "is_company_of_interest(" in src
    assert "from findajob.scorer_prefilter import TIER1" not in src
    assert "_is_tier1" not in src
