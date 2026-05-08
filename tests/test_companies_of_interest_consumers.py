"""Regression guard: notify must consume companies_of_interest via the
config_loader, not via the deleted _is_tier1 / TIER1 from the prefilter module.

Post-#537: the relevant import lives in
`src/findajob/notifications/health_check.py` (where `is_company_of_interest`
is actually called); the script shim no longer contains it.
"""

from __future__ import annotations

import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]


def _read(relative: str) -> str:
    return (REPO_ROOT / relative).read_text()


def test_notify_uses_is_company_of_interest():
    src = _read("src/findajob/notifications/health_check.py")
    assert "from findajob.config_loader import is_company_of_interest" in src
    assert "is_company_of_interest(" in src
    assert "from findajob.scorer_prefilter import TIER1" not in src
    assert "_is_tier1" not in src
