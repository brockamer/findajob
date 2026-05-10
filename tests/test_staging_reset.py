"""Staging reset unit tests (#565)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

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


def test_reset_succeeds_when_subdirs_cannot_be_removed(tmp_path: Path) -> None:
    """Regression for #610 — reset must work when target/data and other target
    subdirs cannot be removed (e.g., they are Docker bind-mount roots).

    Simulated by monkeypatching ``shutil.rmtree`` to raise ``PermissionError``
    when called on directories that exist as non-empty children of the target,
    matching what happens inside a container when target/data is a bind mount.
    The fix is for ``reset_to_persona`` to wipe contents (iterdir + per-child
    removal) instead of removing the directory itself.
    """
    import shutil

    fixture = tmp_path / "fixture"
    target = tmp_path / "stack"

    # Build fixture with the canonical persona shape (config/, data/, candidate_context/)
    (fixture / "config").mkdir(parents=True)
    (fixture / "data").mkdir(parents=True)
    (fixture / "candidate_context").mkdir(parents=True)
    (fixture / "config" / "active_sources.txt").write_text("jobs-api14\n")
    (fixture / "data" / ".onboarding-complete").write_text("")
    (fixture / "candidate_context" / "profile.md").write_text("# Persona")

    # Pre-create target subdirs and put stale junk in data/ (simulating a
    # populated bind mount). The bind-mount nature is captured by the rmtree
    # protection below — _wipe_contents uses iterdir+remove-children, which
    # never tries to rmtree the protected directories themselves.
    (target / "data").mkdir(parents=True)
    (target / "config").mkdir(parents=True)
    (target / "candidate_context").mkdir(parents=True)
    (target / "data" / "stale_pipeline.db").write_text("STALE")
    (target / "config" / "stale_active_sources.txt").write_text("STALE")

    protected_dirs = {target / "data", target / "config", target / "candidate_context"}
    real_rmtree = shutil.rmtree

    def guarded_rmtree(path: Any, *args: Any, **kwargs: Any) -> None:
        if Path(path) in protected_dirs:
            raise PermissionError(f"[Errno 13] Permission denied (simulated bind mount): {path}")
        real_rmtree(path, *args, **kwargs)

    monkey = pytest.MonkeyPatch()
    monkey.setattr(reset.shutil, "rmtree", guarded_rmtree)
    try:
        reset.reset_to_persona(fixture=fixture, target=target)
    finally:
        monkey.undo()

    # All bind-mount-target dirs survived (we never rmtree'd them)
    assert (target / "data").is_dir()
    assert (target / "config").is_dir()
    assert (target / "candidate_context").is_dir()
    # Stale contents wiped
    assert not (target / "data" / "stale_pipeline.db").exists()
    assert not (target / "config" / "stale_active_sources.txt").exists()
    # Persona contents installed
    assert (target / "data" / ".onboarding-complete").exists()
    assert (target / "config" / "active_sources.txt").read_text() == "jobs-api14\n"
    assert (target / "candidate_context" / "profile.md").read_text() == "# Persona"
