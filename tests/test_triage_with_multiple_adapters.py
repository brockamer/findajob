"""Integration test: triage uses the adapter registry (#408).

Imports from `findajob.triage.orchestrator` after the M3 extraction
(#537). Pre-extraction the test inserted SCRIPTS_DIR into `sys.path`
and imported `triage` as a top-level module; that hack is no longer
needed because the orchestrator is now a regular library module.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest


def test_triage_iterates_configured_adapters(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
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

    # Patch the binding inside the orchestrator namespace — that's where
    # `iter_configured_adapters` is resolved when main() runs.
    with patch("findajob.triage.orchestrator.iter_configured_adapters", return_value=iter(fakes)):
        from findajob.triage import orchestrator  # noqa: PLC0415

        adapters = list(orchestrator.iter_configured_adapters())
        for a in adapters:
            a.fetch(["q1", "q2"])

    assert called == ["jobs-api14", "jsearch"]
