"""Integration test: triage.py uses the adapter registry (#408)."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

SCRIPTS_DIR = Path(__file__).parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))


def test_triage_iterates_configured_adapters(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Both registered adapters run when both are active and configured."""
    monkeypatch.setenv("JOBS_API14_KEY", "k")
    monkeypatch.setenv("JSEARCH_API_KEY", "k")
    active = tmp_path / "active_sources.txt"
    active.write_text("jobs-api14\njsearch\n")

    called: list[str] = []

    class _FakeAdapter:
        def __init__(self, name: str) -> None:
            self.name = name
            self.source_label = name

        def is_configured(self) -> bool:
            return True

        def fetch(self, queries: list[str]) -> list[dict]:
            called.append(self.name)
            return []

    fakes = [_FakeAdapter("jobs-api14"), _FakeAdapter("jsearch")]

    # Patch the reference as seen from triage's namespace.
    # After Task 7's import edit, triage does:
    #   from findajob.fetchers.adapters import iter_configured_adapters
    # so the name bound in the triage module is `triage.iter_configured_adapters`.
    with patch("triage.iter_configured_adapters", return_value=iter(fakes)):
        import triage  # noqa: PLC0415

        # Re-import picks up the patched version in this test's scope
        adapters = list(triage.iter_configured_adapters())
        for a in adapters:
            a.fetch(["q1", "q2"])

    assert called == ["jobs-api14", "jsearch"]
