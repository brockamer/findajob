"""Staging reset unit tests (#565)."""

from __future__ import annotations

from pathlib import Path

import pytest

from findajob.staging import reset


def test_reset_copies_persona(tmp_path: Path) -> None:
    """Reset wipes target data/ and copies fixture into target."""
    fixture = tmp_path / "fixture"
    target_base = tmp_path / "stack"
    (fixture / "config").mkdir(parents=True)
    (fixture / "data").mkdir(parents=True)
    (fixture / "candidate_context").mkdir(parents=True)
    (fixture / "config" / "active_sources.txt").write_text("jobs-api14\n")
    (fixture / "data" / ".onboarding-complete").write_text("")
    (fixture / "candidate_context" / "role_archetypes.md").write_text("# roles")
    (fixture / "profile.md").write_text("# Persona")
    (fixture / "master_resume.md").write_text("# Resume")

    # Pre-existing junk that must be wiped
    (target_base / "data").mkdir(parents=True)
    (target_base / "data" / "pipeline.db").write_text("STALE")
    (target_base / "data" / "stale_sentinel").write_text("STALE")

    reset.reset_to_persona(fixture=fixture, target=target_base)

    assert (target_base / "config" / "active_sources.txt").read_text() == "jobs-api14\n"
    assert (target_base / "data" / ".onboarding-complete").exists()
    assert (target_base / "candidate_context" / "role_archetypes.md").read_text() == "# roles"
    assert (target_base / "profile.md").read_text() == "# Persona"
    # Stale data wiped
    assert not (target_base / "data" / "pipeline.db").exists()
    assert not (target_base / "data" / "stale_sentinel").exists()


def test_reset_refuses_missing_fixture(tmp_path: Path) -> None:
    fixture = tmp_path / "missing"
    target = tmp_path / "stack"
    target.mkdir()
    with pytest.raises(FileNotFoundError):
        reset.reset_to_persona(fixture=fixture, target=target)


def test_reset_refuses_target_is_file(tmp_path: Path) -> None:
    fixture = tmp_path / "fixture"
    fixture.mkdir()
    target = tmp_path / "stack"
    target.write_text("not a dir")
    with pytest.raises(NotADirectoryError):
        reset.reset_to_persona(fixture=fixture, target=target)
