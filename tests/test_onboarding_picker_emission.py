"""Tests for picker emission and active_sources.txt write (#408)."""

from __future__ import annotations

from pathlib import Path

import pytest

from findajob.onboarding.injector import inject
from findajob.onboarding.parser import parse_emission


def _emission_with_picker(adapter_name: str) -> str:
    return f"""<<<FILE: profile.md>>>
# Profile
<<<END FILE: profile.md>>>

<<<FILE: master_resume.md>>>
# Master resume
<<<END FILE: master_resume.md>>>

<<<FILE: target_companies.md>>>
## Target companies
<<<END FILE: target_companies.md>>>

<<<FILE: business_sector_employers_reference.md>>>
## Reference
<<<END FILE: business_sector_employers_reference.md>>>

<<<FILE: prefilter_rules.yaml>>>
patterns: []
<<<END FILE: prefilter_rules.yaml>>>

<<<FILE: in_domain_patterns.yaml>>>
patterns: []
<<<END FILE: in_domain_patterns.yaml>>>

<<<FILE: display_name.txt>>>
Test Candidate
<<<END FILE: display_name.txt>>>

<<<FILE: timezone.txt>>>
America/Los_Angeles
<<<END FILE: timezone.txt>>>

<<<FILE: ntfy_topic.txt>>>
test-topic
<<<END FILE: ntfy_topic.txt>>>

<<<FILE: rapidapi_feed.txt>>>
{adapter_name}
<<<END FILE: rapidapi_feed.txt>>>
"""


def test_picker_emission_writes_active_sources_txt(tmp_path: Path) -> None:
    (tmp_path / "config").mkdir()
    (tmp_path / "data").mkdir()
    (tmp_path / "candidate_context").mkdir()

    parsed = parse_emission(_emission_with_picker("jsearch"))
    inject(tmp_path, parsed.found, skip_smoke_check=True)

    active_sources = tmp_path / "config" / "active_sources.txt"
    assert active_sources.exists()
    assert active_sources.read_text().strip() == "jsearch"


def test_picker_emission_jobs_api14(tmp_path: Path) -> None:
    (tmp_path / "config").mkdir()
    (tmp_path / "data").mkdir()
    (tmp_path / "candidate_context").mkdir()

    parsed = parse_emission(_emission_with_picker("jobs-api14"))
    inject(tmp_path, parsed.found, skip_smoke_check=True)

    assert (tmp_path / "config" / "active_sources.txt").read_text().strip() == "jobs-api14"


def test_no_picker_emission_no_active_sources_file(tmp_path: Path) -> None:
    """If the candidate didn't pick 'a' in 3g, no rapidapi_feed.txt is emitted, no active_sources.txt is written."""
    (tmp_path / "config").mkdir()
    (tmp_path / "data").mkdir()
    (tmp_path / "candidate_context").mkdir()

    # emission without rapidapi_feed.txt (drop the trailing block)
    no_picker = _emission_with_picker("jobs-api14").rsplit("<<<FILE: rapidapi_feed.txt>>>", 1)[0]
    parsed = parse_emission(no_picker)
    inject(tmp_path, parsed.found, skip_smoke_check=True)

    assert not (tmp_path / "config" / "active_sources.txt").exists()


def test_inject_skips_sentinel_when_active_adapter_unconfigured(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If active adapter's env var is blank, sentinel is NOT written; gate to feed-config."""
    monkeypatch.delenv("JSEARCH_API_KEY", raising=False)
    (tmp_path / "config").mkdir()
    (tmp_path / "data").mkdir()
    (tmp_path / "candidate_context").mkdir()

    parsed = parse_emission(_emission_with_picker("jsearch"))
    result = inject(tmp_path, parsed.found, skip_smoke_check=True)

    sentinel = tmp_path / "data" / ".onboarding-complete"
    assert not sentinel.exists()
    assert result.decision.gate_to_feed_config is True
    assert result.decision.pending_adapter == "jsearch"


def test_inject_deletes_sentinel_when_gating(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When gate fires (re-run with new adapter, key blank), existing sentinel is deleted
    so the gate is enforcing, not advisory."""
    monkeypatch.delenv("JSEARCH_API_KEY", raising=False)
    (tmp_path / "config").mkdir()
    (tmp_path / "data").mkdir()
    (tmp_path / "candidate_context").mkdir()

    # Simulate a previous onboarding having written the sentinel.
    sentinel = tmp_path / "data" / ".onboarding-complete"
    sentinel.touch()
    assert sentinel.exists()

    parsed = parse_emission(_emission_with_picker("jsearch"))
    result = inject(tmp_path, parsed.found, skip_smoke_check=True)

    assert result.decision.gate_to_feed_config is True
    assert result.decision.pending_adapter == "jsearch"
    assert not sentinel.exists(), "sentinel must be deleted when gate fires so /board/ redirect is enforcing"


def test_inject_writes_sentinel_when_active_adapter_configured(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If active adapter's env var is set, sentinel is written immediately."""
    monkeypatch.setenv("JOBS_API14_KEY", "existing-key")
    (tmp_path / "config").mkdir()
    (tmp_path / "data").mkdir()
    (tmp_path / "candidate_context").mkdir()

    parsed = parse_emission(_emission_with_picker("jobs-api14"))
    result = inject(tmp_path, parsed.found, skip_smoke_check=True)

    sentinel = tmp_path / "data" / ".onboarding-complete"
    assert sentinel.exists()
    assert result.decision.gate_to_feed_config is False
